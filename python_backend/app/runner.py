from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from urllib.parse import urlsplit
from pathlib import Path
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .mirror import MirrorError, SnapshotMirror
from .provisioning import PrepareService, ProvisioningError, ProvisioningService, ProvisioningStateStore
from .provisioning.models import ProvisioningStep
from .provisioning.timeouts import browser_provisioning_hard_timeout, prepare_hard_timeout
from .router import RoutePolicyError, TransparentPostRouter
from .private_launch import PrivateLaunchService
from .session import ProfileResolver, ProfileSession, ProfileContextError, ProxyCheckError
from .store import JobStore

log=logging.getLogger('remask.python_worker')
Handler=Callable[[ProfileSession,dict[str,Any]],Awaitable[dict[str,Any]]]


def _fan_page_error_summary(value: Any) -> str:
    """Keep the failure reason, never credentials or URL query values."""
    text = str(value or "")[:2500]
    def clean_url(match: Any) -> str:
        try:
            parsed = urlsplit(match.group(0))
            return f"{parsed.scheme}://{parsed.hostname or ''}{parsed.path}"
        except ValueError:
            return "[redacted-url]"
    text = re.sub(r"https?://[^\s<>\"']+", clean_url, text)
    text = re.sub(r"(?im)\b(?:cookie|authorization)\s*:\s*[^\r\n]+", "[redacted]", text)
    text = re.sub(
        r"(?i)\b(?:xs|fb_dtsg|jazoest|lsd|access_token|token)[\"']?\s*[:=]\s*[\"']?[^&\s,;\"'>]+",
        "[redacted]", text,
    )
    return text[:700]

class TaskRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str,Handler]={}

    def register(self, name: str, handler: Handler) -> None:
        self._handlers[name]=handler

    async def execute(self, name: str, session: ProfileSession, payload: dict[str,Any]) -> dict[str,Any]:
        handler=self._handlers.get(name)
        if not handler:
            raise RuntimeError(f'unsupported action: {name}')
        return await handler(session,payload)

async def _proxy_check(session: ProfileSession, payload: dict[str,Any]) -> dict[str,Any]:
    return await session.proxy_check()

def _consume_background_task(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except BaseException:
        # The task is intentionally detached only after a hard watchdog fires.
        # Its browser/session is closed by the owning ProfileSession context.
        pass


async def _await_with_hard_watchdog(
    awaitable: Awaitable[dict[str, Any]],
    *,
    timeout_seconds: float,
    code: str,
    message: str,
) -> dict[str, Any]:
    """
    Wall-clock watchdog that does not wait for cooperative cancellation.

    asyncio.wait_for() can exceed its nominal timeout because it waits until
    the wrapped coroutine acknowledges cancellation. Playwright/Chromium can
    wedge during that cancellation path. This watchdog marks the job failed
    immediately, requests cancellation, and lets ProfileSession.close() kill
    the browser independently.
    """
    task = asyncio.create_task(awaitable)
    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=max(0.01, float(timeout_seconds)),
            return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        task.cancel()
        task.add_done_callback(_consume_background_task)
        raise

    if task not in done:
        task.cancel()
        task.add_done_callback(_consume_background_task)
        raise ProvisioningError(
            code,
            message,
            retryable=True,
        )

    return task.result()


class WorkerPool:
    def __init__(self, store: JobStore, mirror: SnapshotMirror | None = None, concurrency: int = 30) -> None:
        self.store=store
        self.mirror=mirror
        self.concurrency=max(1,concurrency)
        self.queue: asyncio.Queue[str]=asyncio.Queue()
        self.profile_locks: defaultdict[str,asyncio.Lock]=defaultdict(asyncio.Lock)
        self.registry=TaskRegistry()
        self.provisioning_state=ProvisioningStateStore(str(store.path))
        self.router=TransparentPostRouter()
        self.registry.register('proxy_check',_proxy_check)
        self.registry.register('transparent_post',self.router.execute)
        self.resolver=ProfileResolver(os.getenv('REMASK_PROFILE_RESOLVER_URL'),os.getenv('REMASK_INTERNAL_KEY'))
        self.provisioning=ProvisioningService(self.provisioning_state,profile_resolver=self.resolver)
        self.prepare=PrepareService(self.provisioning_state,self.provisioning)
        self.private_launch=PrivateLaunchService(self.provisioning_state)
        self._workers: list[asyncio.Task[None]]=[]
        self._created_businesses_lock = asyncio.Lock()

    async def start(self) -> None:
        await self.provisioning_state.init()
        await self._restore_workspace_bindings()
        recovered=await self.store.recover()
        if str(os.getenv('REMASK_STARTUP_STATE_AUDIT','0')).strip().lower() in {'1','true','yes','on'}:
            await self._log_recent_page_access_state()
        if str(os.getenv('REMASK_FAN_PAGE_STATE_AUDIT','0')).strip().lower() in {'1','true','yes','on'}:
            await self._log_recent_fan_page_state()
        for item_id in recovered:
            await self.queue.put(item_id)
        self._workers=[
            asyncio.create_task(self._worker(i),name=f'remask-worker-{i}')
            for i in range(self.concurrency)
        ]
        log.info('worker pool started concurrency=%d recovered=%d',self.concurrency,len(recovered))

    async def _log_recent_fan_page_state(self) -> None:
        """Read existing pending CREATE diagnostics; do not contact Facebook."""
        from .facebook_fan_page_create import fan_page_pending_never_submitted
        def read_rows() -> list[dict[str, Any]]:
            with self.provisioning_state._connect() as con:
                rows = con.execute(
                    """SELECT item_id,profile_id,status,error_code,result_json,updated_at
                       FROM provisioning_steps WHERE step='FAN_PAGES'
                       ORDER BY updated_at DESC LIMIT 50"""
                ).fetchall()
                return [dict(row) for row in rows]
        try:
            rows = await asyncio.to_thread(read_rows)
        except Exception as exc:
            log.warning('FAN_PAGES state audit unavailable type=%s', exc.__class__.__name__)
            return
        for row in rows:
            try:
                result = json.loads(row.get('result_json') or '{}')
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict) or result.get('phase') not in {
                'PAGE_CREATE_CLICK_INTENT', 'PAGE_CREATE_RESULT_UNKNOWN',
            }:
                continue
            diag = result.get('browser_diagnostic')
            diag = diag if isinstance(diag, dict) else {}
            click = diag.get('click_meta')
            click = click if isinstance(click, dict) else {}
            checks = result.get('reconciliation') or []
            safe_checks = [
                {key: _fan_page_error_summary(check.get(key))
                 for key in ('source', 'code', 'message') if check.get(key)}
                for check in checks[-8:] if isinstance(check, dict)
            ] if isinstance(checks, list) else []
            log.info(
                'FAN_PAGES pending diagnostic profile=%s item=%s phase=%s '
                'click_attempted=%s click_clicked=%s click_error=%s '
                'stage=%s no_click_proven=%s reconciliation=%s updated_at=%s',
                row['profile_id'], row['item_id'], result['phase'],
                click.get('attempted'), click.get('clicked'),
                _fan_page_error_summary(click.get('error')),
                diag.get('stage', ''), fan_page_pending_never_submitted(result),
                json.dumps(safe_checks), row['updated_at'],
            )

    async def _log_recent_page_access_state(self) -> None:
        """Emit durable PAGE_ACCESS state so recovery outcomes are observable."""
        def load_rows() -> list[dict[str,Any]]:
            with self.store._connect() as con:
                rows=con.execute(
                    """SELECT i.job_id,i.id AS item_id,i.profile_id,
                              i.status AS item_status,i.error_code AS item_error_code,
                              i.error_message AS item_error_message,i.updated_at,
                              p.status AS page_status,p.error_code AS page_error_code,
                              p.error_message AS page_error_message,p.result_json
                       FROM job_items i
                       JOIN provisioning_steps p
                         ON p.item_id=i.id AND p.step='PAGE_ACCESS'
                       ORDER BY i.updated_at DESC
                       LIMIT 20"""
                ).fetchall()
                return [dict(row) for row in rows]

        try:
            rows=await asyncio.to_thread(load_rows)
        except Exception as exc:
            log.warning('PAGE_ACCESS startup state audit failed: %s',exc)
            return
        for row in rows:
            result={}
            try:
                decoded=json.loads(str(row.get('result_json') or '{}'))
                if isinstance(decoded,dict):
                    result=decoded
            except (TypeError,ValueError,json.JSONDecodeError):
                result={}
            diagnostic=(
                result.get('diagnostic')
                if isinstance(result.get('diagnostic'),dict)
                else {}
            )
            candidates=diagnostic.get('pending_request_candidates')
            candidate_count=len(candidates) if isinstance(candidates,list) else 0
            log.info(
                'PAGE_ACCESS durable state job=%s item=%s profile=%s '
                'item_status=%s item_error=%s item_message=%s '
                'page_status=%s page_error=%s page_message=%s '
                'phase=%s diagnostic_stage=%s diagnostic_url=%s '
                'pending_candidates=%s page=%s business=%s ad_account=%s updated_at=%s',
                str(row.get('job_id') or ''),
                str(row.get('item_id') or ''),
                str(row.get('profile_id') or ''),
                str(row.get('item_status') or ''),
                str(row.get('item_error_code') or ''),
                str(row.get('item_error_message') or '')[:500],
                str(row.get('page_status') or ''),
                str(row.get('page_error_code') or ''),
                str(row.get('page_error_message') or '')[:500],
                str(result.get('phase') or ''),
                str(diagnostic.get('stage') or ''),
                str(diagnostic.get('url') or '')[:700],
                candidate_count,
                str(result.get('page_id') or ''),
                str(result.get('business_id') or ''),
                str(result.get('ad_account_id') or ''),
                str(row.get('updated_at') or ''),
            )


        try:
            from .provisioning.advertising_page import AdvertisingPageStore
            profiles=sorted({str(row.get('profile_id') or '').strip() for row in rows
                if str(row.get('profile_id') or '').strip()})
            for profile_id in profiles:
                store=AdvertisingPageStore(self.provisioning_state,profile_id)
                facebook_uid=''
                page_context=[]
                try:
                    context=await asyncio.wait_for(self.resolver.resolve(profile_id),timeout=3)
                    facebook_uid=str((getattr(context,'cookies',{}) or {}).get('c_user') or '')
                    store=AdvertisingPageStore.for_context(self.provisioning_state,context,profile_id)
                    page_context=[
                        {
                            'id':str(row.get('id') or ''),
                            'profile_id':str(row.get('profile_id') or ''),
                            'business_id':str(row.get('business_id') or ''),
                            'is_owned':row.get('is_owned'),
                        }
                        for row in (getattr(context,'pages',None) or [])
                        if isinstance(row,dict)
                    ]
                except Exception:
                    pass
                page=await store.get()
                grants=page.get('grants') if isinstance(page.get('grants'),dict) else {}
                grant_phases={str(key):str((value or {}).get('phase') or '')
                    for key,value in grants.items() if isinstance(value,dict)}
                target_context=[row for row in page_context
                    if row.get('id')==str(page.get('page_id') or '')]
                log.info(
                    'ADVERTISING_PAGE durable state profile=%s facebook_uid=%s '
                    'page=%s name=%s owner_profile=%s owner_business=%s '
                    'ownership_phase=%s owner_business_confirmed=%s grants=%s page_context=%s',
                    profile_id,
                    facebook_uid,
                    str(page.get('page_id') or ''),
                    str(page.get('name') or ''),
                    str(page.get('owner_profile_id') or ''),
                    str(page.get('owner_business_id') or ''),
                    str(page.get('ownership_phase') or ''),
                    bool(page.get('owner_business_confirmed')),
                    json.dumps(grant_phases,separators=(',',':')),
                    json.dumps(target_context,separators=(',',':')),
                )
        except Exception as exc:
            log.warning('ADVERTISING_PAGE startup state audit failed: %s',exc)

    async def _persist_created_businesses(self) -> None:
        # Local display mirror; never contacts Meta and never marks live inventory.
        async with self._created_businesses_lock:
            groups = await self.provisioning_state.confirmed_business_binding_groups()
            root = os.getenv('REMASK_DATA_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '/var/lib/remask'
            path = Path(root) / 'workspace-created-businesses.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + '.worker.tmp')
            temp.write_text(json.dumps(groups, separators=(',', ':'), ensure_ascii=False), encoding='utf-8')
            os.replace(temp, path)
            log.info('workspace confirmed CREATE mirror refreshed profiles=%d businesses=%s',
                     len(groups), json.dumps({profile: list(group.get('businesses', {}))
                                              for profile, group in groups.items()}, separators=(',', ':')))

    async def _restore_workspace_bindings(self) -> None:
        """Rebuild multi-Business Workspace BM->RK bindings from durable history."""
        await self._persist_created_businesses()
        bindings=(
            await self.provisioning_state.confirmed_ad_account_binding_groups()
        )
        if not bindings:
            return

        root=(
            os.getenv('REMASK_DATA_DIR')
            or os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
            or '/var/lib/remask'
        )
        path=Path(root) / 'workspace-provisioning-bindings.json'
        path.parent.mkdir(parents=True,exist_ok=True)

        current: dict[str,Any]={}
        try:
            if path.exists():
                decoded=json.loads(path.read_text(encoding='utf-8'))
                if isinstance(decoded,dict):
                    current=decoded
        except (OSError,json.JSONDecodeError,ValueError):
            current={}

        for profile, group in bindings.items():
            if not isinstance(group,dict):
                continue
            incoming=group.get('ad_accounts')
            if not isinstance(incoming,dict):
                continue

            existing=current.get(profile)
            merged: dict[str,Any]={}

            if isinstance(existing,dict):
                existing_accounts=existing.get('ad_accounts')
                if isinstance(existing_accounts,dict):
                    merged.update(
                        {
                            str(key):value
                            for key,value in existing_accounts.items()
                            if isinstance(value,dict)
                        }
                    )
                else:
                    # V1 file shape: one pair directly under the profile.
                    business_id=str(existing.get('business_id') or '').strip()
                    ad_account_id=str(existing.get('ad_account_id') or '').strip()
                    if business_id.isdigit() and ad_account_id.isdigit():
                        merged[business_id]={
                            'business_id':business_id,
                            'ad_account_id':ad_account_id,
                            'account_name':str(
                                existing.get('account_name') or ''
                            ).strip(),
                            'updated_at':int(existing.get('updated_at') or 0),
                            'source':str(
                                existing.get('source') or 'legacy_binding_v1'
                            ),
                        }

            merged.update(
                {
                    str(key):value
                    for key,value in incoming.items()
                    if isinstance(value,dict)
                }
            )
            current[profile]={'ad_accounts':merged}

        temp=path.with_suffix(path.suffix + '.worker.tmp')
        temp.write_text(
            json.dumps(current,separators=(',',':'),ensure_ascii=False),
            encoding='utf-8',
        )
        os.replace(temp,path)
        total=sum(
            len(
                group.get('ad_accounts')
                if isinstance(group,dict)
                and isinstance(group.get('ad_accounts'),dict)
                else {}
            )
            for group in bindings.values()
        )
        log.info(
            'workspace BM/RK bindings restored profiles=%d relations=%d path=%s',
            len(bindings),
            total,
            path,
        )

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers,return_exceptions=True)
        self._workers=[]

    async def enqueue_job(self, job_id: str) -> int:
        ids=await self.store.queued_item_ids(job_id)
        for item_id in ids:
            await self.queue.put(item_id)
        return len(ids)

    async def _worker(self, index: int) -> None:
        while True:
            item_id=await self.queue.get()
            try:
                await self._run_item(item_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception('unhandled item crash item=%s worker=%d',item_id,index)
            finally:
                self.queue.task_done()

    async def _run_item(self, item_id: str) -> None:
        item=await self.store.item(item_id)
        if not item or item['status']!='QUEUED':
            return
        profile_id=str(item['profile_id'])
        async with self.profile_locks[profile_id]:
            # Another queue consumer may have completed this item while this
            # consumer waited for the profile lock.
            item = await self.store.item(item_id)
            if not item or item['status'] != 'QUEUED':
                return
            await self.store.set_item_running(item_id)
            tasks=await self.store.tasks(item_id)
            try:
                context=await self.resolver.resolve(profile_id)
                async with ProfileSession(context) as session:
                    for task in tasks:
                        if task['status']=='SUCCESS':
                            continue
                        await self.store.set_task_running(task['id'])
                        try:
                            action=str(task['action'])
                            if action=='private_launch':
                                payload=task['payload']
                                try:
                                    launch_timeout=float(
                                        os.getenv('REMASK_PRIVATE_LAUNCH_HARD_TIMEOUT_SECONDS')
                                        or '300'
                                    )
                                except (TypeError,ValueError):
                                    launch_timeout=300.0
                                launch_timeout=max(120.0,min(launch_timeout,900.0))
                                result=await _await_with_hard_watchdog(
                                    self.private_launch.run(
                                        item_id=item_id,
                                        profile_id=profile_id,
                                        context=context,
                                        session=session,
                                        payload=payload,
                                        task_idempotency_key=task.get('idempotency_key'),
                                    ),
                                    timeout_seconds=launch_timeout,
                                    code='PRIVATE_LAUNCH_HARD_TIMEOUT',
                                    message=(
                                        'Private Launch exact preflight/mutation pipeline '
                                        f'exceeded {int(launch_timeout)}s total runtime.'
                                    ),
                                )
                            elif action=='prepare':
                                payload=task['payload']
                                desired=(payload.get('desired') or {}) if isinstance(payload,dict) else {}
                                hard_timeout=prepare_hard_timeout(desired.get('ad_accounts',2))
                                result=await _await_with_hard_watchdog(
                                    self.prepare.run(
                                        item_id=item_id,
                                        profile_id=profile_id,
                                        context=context,
                                        session=session,
                                        payload=payload,
                                        task_idempotency_key=task.get('idempotency_key'),
                                    ),
                                    timeout_seconds=hard_timeout,
                                    code='PREPARE_HARD_TIMEOUT',
                                    message=(
                                        'Prepare desired-state pipeline exceeded '
                                        f'{int(hard_timeout)}s total runtime.'
                                    ),
                                )
                            elif action=='provisioning':
                                payload=task['payload']
                                raw_steps=payload.get('steps') if isinstance(payload,dict) else None
                                normalized_steps=[
                                    str(value).strip().upper()
                                    for value in (raw_steps or [])
                                ] if isinstance(raw_steps,list) else []

                                # FAN_PAGES, CREATE_BM and CREATE_AD_ACCOUNT
                                # all use profile-bound Chromium and need a hard
                                # wall-clock watchdog independent of Playwright.
                                fan_pages_guarded='FAN_PAGES' in normalized_steps
                                business_guarded='BUSINESS' in normalized_steps
                                ad_account_guarded='AD_ACCOUNT' in normalized_steps
                                access_guarded='PAGE_ACCESS' in normalized_steps
                                rk_params=(payload.get('parameters') or {}).get('AD_ACCOUNT',{})
                                if ad_account_guarded and isinstance(rk_params,dict) and rk_params.get('use_common_page') is True:
                                    access_guarded=True
                                    if 'PAGE_ACCESS' not in normalized_steps: normalized_steps.append('PAGE_ACCESS')

                                if fan_pages_guarded or business_guarded or ad_account_guarded or access_guarded:
                                    browser_steps=[
                                        value
                                        for value in normalized_steps
                                        if value in {'FAN_PAGES','BUSINESS','AD_ACCOUNT','PAGE_ACCESS'}
                                    ]
                                    hard_timeout=browser_provisioning_hard_timeout(
                                        browser_steps
                                    )
                                    guarded_count=sum(
                                        1 for enabled in (
                                            fan_pages_guarded,
                                            business_guarded,
                                            ad_account_guarded,
                                            access_guarded,
                                        ) if enabled
                                    )
                                    if guarded_count > 1:
                                        watchdog_code='BROWSER_PROVISIONING_HARD_TIMEOUT'
                                        watchdog_label='+'.join(
                                            value
                                            for value,enabled in (
                                                ('FAN_PAGES',fan_pages_guarded),
                                                ('BUSINESS',business_guarded),
                                                ('AD_ACCOUNT',ad_account_guarded),
                                                ('PAGE_ACCESS',access_guarded),
                                            )
                                            if enabled
                                        )
                                    elif access_guarded:
                                        watchdog_code='PAGE_ACCESS_HARD_TIMEOUT'
                                        watchdog_label='PAGE_ACCESS'
                                    elif fan_pages_guarded:
                                        watchdog_code='ADD_FP_HARD_TIMEOUT'
                                        watchdog_label='FAN_PAGES'
                                    elif business_guarded:
                                        watchdog_code='ADD_BM_HARD_TIMEOUT'
                                        watchdog_label='BUSINESS'
                                    else:
                                        watchdog_code='ADD_RK_HARD_TIMEOUT'
                                        watchdog_label='AD_ACCOUNT'

                                    result=await _await_with_hard_watchdog(
                                        self.provisioning.run(
                                            item_id=item_id,
                                            profile_id=profile_id,
                                            context=context,
                                            session=session,
                                            payload=payload,
                                            task_idempotency_key=task.get('idempotency_key'),
                                        ),
                                        timeout_seconds=hard_timeout,
                                        code=watchdog_code,
                                        message=(
                                            f'{watchdog_label} total queue/runtime '
                                            f'watchdog exceeded {int(hard_timeout)}s. '
                                            'Active Meta phases have separate shorter '
                                            'timeouts; this guard includes browser-slot '
                                            'queue time.'
                                        ),
                                    )
                                else:
                                    result=await self.provisioning.run(
                                        item_id=item_id,
                                        profile_id=profile_id,
                                        context=context,
                                        session=session,
                                        payload=payload,
                                        task_idempotency_key=task.get('idempotency_key'),
                                    )
                            else:
                                result=await self.registry.execute(action,session,task['payload'])
                            await self.store.set_task_success(task['id'],result)
                        except ProvisioningError as exc:
                            await self.store.set_task_failed(
                                task['id'],
                                exc.code,
                                str(exc),
                                retryable=bool(exc.retryable),
                            )
                            break
                        except ProxyCheckError as exc:
                            await self.store.set_task_failed(
                                task['id'],
                                'PROXY_DEAD',
                                str(exc),
                                retryable=True,
                            )
                            break
                        except RoutePolicyError as exc:
                            await self.store.set_task_failed(
                                task['id'],
                                'ROUTE_POLICY',
                                str(exc),
                                retryable=False,
                            )
                            break
                        except Exception as exc:
                            await self.store.set_task_failed(
                                task['id'],
                                'TASK_FAILED',
                                str(exc),
                                retryable=False,
                            )
                            break
            except ProfileContextError as exc:
                retryable=bool(getattr(exc,'retryable',False))
                category=str(getattr(exc,'category','profile_context') or 'profile_context')
                log.warning(
                    'profile context failure job=%s item=%s profile=%s category=%s retryable=%s detail=%s',
                    str(item.get('job_id') or ''),
                    item_id,
                    profile_id,
                    category,
                    retryable,
                    str(exc),
                )
                if tasks:
                    first=next((t for t in tasks if t['status']!='SUCCESS'),tasks[0])
                    await self.store.set_task_failed(
                        first['id'],
                        'PROFILE_CONTEXT_ERROR',
                        str(exc),
                        retryable=retryable,
                    )
            finally:
                await self.store.finalize_item(item_id)
                try:
                    current=await self.store.item(item_id)
                    page_state=await self.provisioning_state.step(
                        item_id,ProvisioningStep.PAGE_ACCESS
                    )
                    page_result=(page_state or {}).get('result') or {}
                    fan_state=await self.provisioning_state.step(
                        item_id,ProvisioningStep.FAN_PAGES
                    )
                    fan_result=(fan_state or {}).get('result') or {}
                    fan_diag=fan_result.get('browser_diagnostic') or {}
                    fan_controls=fan_diag.get('visible_controls') or []
                    log.info(
                        'worker item finalized job=%s item=%s profile=%s '
                        'status=%s error=%s page_status=%s page_error=%s phase=%s '
                        'fan_phase=%s fan_diag_stage=%s fan_diag_url=%s fan_controls=%s',
                        str((current or {}).get('job_id') or ''),
                        item_id,
                        profile_id,
                        str((current or {}).get('status') or ''),
                        str((current or {}).get('error_code') or ''),
                        str((page_state or {}).get('status') or ''),
                        str((page_state or {}).get('error_code') or ''),
                        str(page_result.get('phase') or ''),
                        str(fan_result.get('phase') or ''),
                        str(fan_diag.get('stage') or ''),
                        str(fan_diag.get('url') or '')[:500],
                        [
                            {
                                'tag': str(row.get('tag') or '')[:20],
                                'role': str(row.get('role') or '')[:40],
                                'text': str(row.get('text') or '')[:120],
                                'aria': str(row.get('aria') or '')[:120],
                            }
                            for row in fan_controls[:12]
                            if isinstance(row,dict)
                        ],
                    )
                except Exception as exc:
                    log.warning('worker item final state logging failed item=%s: %s',item_id,exc)
                try:
                    await self._restore_workspace_bindings()
                except Exception as exc:
                    # Remote CREATE/SQLite proof must not be turned into a
                    # failed operation by a local display-mirror write failure.
                    log.error('workspace display mirror refresh failed: %s', exc)
                if self.mirror and self.mirror.enabled:
                    current = await self.store.item(item_id)
                    if current:
                        view = await self.store.job_view(str(current['job_id']))
                        if view:
                            try:
                                await self.mirror.save_job(view)
                            except MirrorError as exc:
                                log.error('job mirror save failed job=%s: %s', current['job_id'], exc)
