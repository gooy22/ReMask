from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .mirror import MirrorError, SnapshotMirror
from .provisioning import ProvisioningError, ProvisioningService, ProvisioningStateStore
from .provisioning.timeouts import browser_provisioning_hard_timeout
from .router import RoutePolicyError, TransparentPostRouter
from .session import ProfileResolver, ProfileSession, ProfileContextError, ProxyCheckError
from .store import JobStore

log=logging.getLogger('remask.python_worker')
Handler=Callable[[ProfileSession,dict[str,Any]],Awaitable[dict[str,Any]]]

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
        self.provisioning=ProvisioningService(self.provisioning_state)
        self.router=TransparentPostRouter()
        self.registry.register('proxy_check',_proxy_check)
        self.registry.register('transparent_post',self.router.execute)
        self.resolver=ProfileResolver(os.getenv('REMASK_PROFILE_RESOLVER_URL'),os.getenv('REMASK_INTERNAL_KEY'))
        self._workers: list[asyncio.Task[None]]=[]

    async def start(self) -> None:
        await self.provisioning_state.init()
        recovered=await self.store.recover()
        for item_id in recovered:
            await self.queue.put(item_id)
        self._workers=[
            asyncio.create_task(self._worker(i),name=f'remask-worker-{i}')
            for i in range(self.concurrency)
        ]
        log.info('worker pool started concurrency=%d recovered=%d',self.concurrency,len(recovered))

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
                            if action=='provisioning':
                                payload=task['payload']
                                raw_steps=payload.get('steps') if isinstance(payload,dict) else None
                                normalized_steps=[
                                    str(value).strip().upper()
                                    for value in (raw_steps or [])
                                ] if isinstance(raw_steps,list) else []

                                # CREATE_BM and CREATE_AD_ACCOUNT both use
                                # profile-bound Chromium and need a hard wall
                                # clock watchdog independent of Playwright
                                # cooperative cancellation.
                                business_guarded='BUSINESS' in normalized_steps
                                ad_account_guarded='AD_ACCOUNT' in normalized_steps

                                if business_guarded or ad_account_guarded:
                                    browser_steps=[
                                        value
                                        for value in normalized_steps
                                        if value in {'BUSINESS','AD_ACCOUNT'}
                                    ]
                                    hard_timeout=browser_provisioning_hard_timeout(
                                        browser_steps
                                    )
                                    if business_guarded and ad_account_guarded:
                                        watchdog_code='BROWSER_PROVISIONING_HARD_TIMEOUT'
                                        watchdog_label='BUSINESS+AD_ACCOUNT'
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
                if tasks:
                    first=next((t for t in tasks if t['status']!='SUCCESS'),tasks[0])
                    await self.store.set_task_failed(
                        first['id'],
                        'PROFILE_CONTEXT_ERROR',
                        str(exc),
                        retryable=False,
                    )
            finally:
                await self.store.finalize_item(item_id)
                if self.mirror and self.mirror.enabled:
                    current = await self.store.item(item_id)
                    if current:
                        view = await self.store.job_view(str(current['job_id']))
                        if view:
                            try:
                                await self.mirror.save_job(view)
                            except MirrorError as exc:
                                log.error('job mirror save failed job=%s: %s', current['job_id'], exc)
