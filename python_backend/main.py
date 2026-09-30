from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status

from app.mirror import MirrorError, SnapshotMirror
from app.models import CreateJobRequest, HealthResponse, JobAccepted, RetryResponse
from app.runner import WorkerPool
from app.session import ProfileContextError, ProfileSession, ProxyCheckError
from app.store import JobStore
from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.facebook_docids import (
    list_candidates,
    registry_view,
    upsert_candidate,
)

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
log=logging.getLogger('remask.python_api')

DATA_ROOT=os.getenv('REMASK_DATA_DIR') or os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '/var/lib/remask'
DB_PATH=os.getenv('REMASK_JOB_DB',os.path.join(DATA_ROOT,'python-worker','jobs.sqlite3'))
CONCURRENCY=int(os.getenv('REMASK_WORKER_CONCURRENCY','30'))
API_KEY=os.getenv('REMASK_WORKER_API_KEY')
STATE_URL=os.getenv('REMASK_STATE_URL')
INTERNAL_KEY=os.getenv('REMASK_INTERNAL_KEY')
store=JobStore(DB_PATH)
mirror=SnapshotMirror(STATE_URL,INTERNAL_KEY)
pool=WorkerPool(store,mirror,CONCURRENCY)
SMOKE_ON_START=str(os.getenv('REMASK_E2E_SMOKE_ON_START','0')).strip().lower() in {'1','true','yes','on'}
BM_CANARY_ON_START=str(os.getenv('REMASK_BM_CANARY_ON_START','0')).strip().lower() in {'1','true','yes','on'}

async def run_bm_browser_canary() -> None:
    """
    Safe Railway canary for the browser BM transport.

    It checks several usable profiles and stops at the first profile whose
    current Meta Business Suite exposes the create-portfolio surface. It never
    submits CREATE or Page-add.
    """
    await asyncio.sleep(3.0)
    if not BM_CANARY_ON_START:
        return

    try:
        profiles=await pool.resolver.list_profiles()
        candidates=[
            row for row in profiles
            if isinstance(row,dict)
            and str(row.get('profile_id') or '').strip()
        ]
        candidates.sort(
            key=lambda row: (
                0 if bool(row.get('proxy_configured')) else 1,
                str(row.get('profile_id') or ''),
            )
        )

        if not candidates:
            log.error('bm browser canary aborted: profile resolver returned no profiles')
            return

        try:
            max_profiles=max(
                1,
                int(os.getenv('REMASK_BM_CANARY_MAX_PROFILES','8')),
            )
        except (TypeError,ValueError):
            max_profiles=8

        attempted=0
        rejected: list[dict[str,str]]=[]

        try:
            canary_phase_timeout=max(
                20,
                min(
                    90,
                    int(os.getenv('REMASK_BM_CANARY_PHASE_TIMEOUT','55')),
                ),
            )
        except (TypeError,ValueError):
            canary_phase_timeout=55

        async def browser_phase(label: str, context, callback):
            async def execute():
                async with FacebookBusinessBrowser(context) as phase_browser:
                    return await callback(phase_browser)

            try:
                return await asyncio.wait_for(
                    execute(),
                    timeout=float(canary_phase_timeout),
                )
            except asyncio.TimeoutError as exc:
                raise BrowserBusinessError(
                    'BM_CANARY_TIMEOUT',
                    f'BM canary phase {label} exceeded {canary_phase_timeout}s',
                    retryable=True,
                    diagnostic={'phase':label},
                ) from exc

        for candidate in candidates:
            if attempted >= max_profiles:
                break

            profile_id=str(candidate.get('profile_id') or '').strip()
            try:
                context=await pool.resolver.resolve(profile_id)
            except ProfileContextError as exc:
                rejected.append({
                    'profile_id':profile_id,
                    'code':'PROFILE_CONTEXT_ERROR',
                    'detail':str(exc)[:300],
                })
                continue

            attempted+=1

            try:
                # Keep the safe canary memory-bounded. Meta Business Suite is a
                # heavy SPA and repeatedly opening several flows in one Chromium
                # context can approach Railway's 1 GB memory limit. Each phase
                # uses a short-lived context while production Add BM still uses
                # one profile-bound context for its actual transaction.
                business_snapshot=await browser_phase(
                    'snapshot_businesses',
                    context,
                    lambda browser: browser.snapshot_businesses(),
                )
                result=await browser_phase(
                    'create_surface',
                    context,
                    lambda browser: browser.preflight(),
                )
                form_result=await browser_phase(
                    'create_form',
                    context,
                    lambda browser: browser.preflight_create_form(),
                )

                try:
                    browser_pages=await browser_phase(
                        'discover_pages',
                        context,
                        lambda browser: browser.discover_managed_pages(),
                    )
                except BrowserBusinessError as page_discovery_exc:
                    browser_pages=[]
                    log.warning(
                        'bm browser page discovery failed profile=%s code=%s detail=%s',
                        profile_id,
                        page_discovery_exc.code,
                        str(page_discovery_exc),
                    )

                request_result=await browser_phase(
                    'capture_create_request',
                    context,
                    lambda browser: browser.preflight_capture_create_request(),
                )
                request_summary=request_result.get('request') or {}
                fill_result={
                    'name_present':bool(
                        request_summary.get('contains_canary_name')
                    ),
                    'email_present':bool(
                        request_summary.get('contains_canary_email')
                    ),
                    'filled_input_count':int(
                        form_result.get('field_count') or 0
                    ),
                }

                page_form_result={
                    'ready':False,
                    'skipped':True,
                    'reason':'no existing Business + free saved Page pair',
                }
                saved_pages=[
                    row for row in (context.pages or [])
                    if isinstance(row,dict)
                    and str(row.get('id') or '').strip().isdigit()
                ]
                known_page_ids={
                    str(row.get('id') or '').strip()
                    for row in saved_pages
                }
                for row in (browser_pages or []):
                    if not isinstance(row,dict):
                        continue
                    page_id=str(row.get('id') or '').strip()
                    if not page_id.isdigit() or page_id in known_page_ids:
                        continue
                    saved_pages.append(row)
                    known_page_ids.add(page_id)
                free_pages=[
                    row for row in saved_pages
                    if not str(row.get('business_id') or '').strip()
                ]
                free_page=free_pages[0] if free_pages else None
                existing_business_id=next(iter(sorted(business_snapshot)), '')
                if existing_business_id and free_page is not None:
                    page_id=str(free_page.get('id') or '').strip()
                    page_form_result=await browser_phase(
                        'page_add_form',
                        context,
                        lambda browser: browser.preflight_page_add_form(
                            business_id=existing_business_id,
                            page_id=page_id,
                        ),
                    )
                    page_form_result['skipped']=False

                log.info(
                    'bm browser canary SUCCESS profile=%s snapshot_businesses=%d '
                    'create_surface=%s form_ready=%s field_count=%s '
                    'dry_fill_name=%s dry_fill_email=%s filled_inputs=%s fields=%s '
                    'blocked_create=%s create_friendly=%s create_doc_id=%s '
                    'create_input_keys=%s blocked_posts=%s '
                    'saved_pages=%s free_pages=%s '
                    'page_form_ready=%s page_form_skipped=%s page_already_attached=%s '
                    'page_result_selected=%s page_final_actions=%s '
                    'url=%s attempted=%d',
                    profile_id,
                    len(business_snapshot),
                    result.create_surface_ready,
                    bool(form_result.get('ready')),
                    int(form_result.get('field_count') or 0),
                    bool(fill_result.get('name_present')),
                    bool(fill_result.get('email_present')),
                    int(fill_result.get('filled_input_count') or 0),
                    json.dumps(form_result.get('fields') or [],ensure_ascii=False)[:4000],
                    bool(request_result.get('blocked')),
                    str((request_result.get('request') or {}).get('friendly_name') or ''),
                    str((request_result.get('request') or {}).get('doc_id') or ''),
                    json.dumps(
                        (request_result.get('request') or {}).get('input_keys') or [],
                        ensure_ascii=False,
                    )[:4000],
                    int(request_result.get('blocked_post_count') or 0),
                    len(saved_pages),
                    len(free_pages),
                    bool(page_form_result.get('ready')),
                    bool(page_form_result.get('skipped')),
                    bool(page_form_result.get('already_attached')),
                    bool(page_form_result.get('result_selected')),
                    json.dumps(
                        page_form_result.get('final_actions') or [],
                        ensure_ascii=False,
                    )[:2000],
                    str(form_result.get('current_url') or result.current_url),
                    attempted,
                )

                if not bool(page_form_result.get('skipped')):
                    return

                rejected.append({
                    'profile_id':profile_id,
                    'code':'PAGE_CANARY_PAIR_UNAVAILABLE',
                    'detail':(
                        f"businesses={len(business_snapshot)} "
                        f"saved_pages={len(saved_pages)} "
                        f"free_pages={len(free_pages)}"
                    ),
                })
                log.info(
                    'bm browser page canary skipped profile=%s businesses=%d '
                    'saved_pages=%d free_pages=%d; trying next profile',
                    profile_id,
                    len(business_snapshot),
                    len(saved_pages),
                    len(free_pages),
                )
                continue

            except BrowserBusinessError as exc:
                rejected.append({
                    'profile_id':profile_id,
                    'code':exc.code,
                    'detail':str(exc)[:600],
                })
                log.warning(
                    'bm browser canary profile rejected profile=%s code=%s detail=%s diagnostic=%s',
                    profile_id,
                    exc.code,
                    str(exc),
                    json.dumps(exc.diagnostic,ensure_ascii=False)[:8000],
                )
            except Exception as exc:
                rejected.append({
                    'profile_id':profile_id,
                    'code':exc.__class__.__name__,
                    'detail':str(exc)[:600],
                })
                log.exception(
                    'bm browser canary profile error profile=%s: %s',
                    profile_id,
                    exc,
                )

        log.error(
            'bm browser canary found no create-capable profile attempted=%d rejected=%s',
            attempted,
            json.dumps(rejected[-max_profiles:],ensure_ascii=False)[:16000],
        )

    except Exception as exc:
        log.exception('bm browser canary ERROR: %s',exc)

async def run_startup_smoke() -> None:
    await asyncio.sleep(2.0)
    if not SMOKE_ON_START:
        return
    try:
        profiles=await pool.resolver.list_profiles()
        if not profiles:
            log.error('e2e smoke aborted: profile resolver returned no profiles')
            return
        candidate=next((p for p in profiles if bool(p.get('proxy_configured'))),profiles[0])
        profile_id=str(candidate.get('profile_id') or '').strip()
        if not profile_id:
            log.error('e2e smoke aborted: selected profile has no profile_id')
            return
        key=f'e2e-v03-proxy-check-{profile_id}'
        request=CreateJobRequest.model_validate({
            'profiles':[{
                'profile_id':profile_id,
                'tasks':[{
                    'action':'proxy_check',
                    'payload':{},
                    'idempotency_key':key,
                }],
            }],
            'idempotency_key':key,
        })
        job_id,created=await store.create_job(request)
        if created:
            await pool.enqueue_job(job_id)
        log.info(
            'e2e smoke created locally job=%s profile=%s created=%s',
            job_id,
            profile_id,
            created,
        )

        terminal={'SUCCESS','FAILED','PARTIAL'}
        last_status=''
        for _ in range(90):
            job=await store.job_view(job_id)
            last_status=str((job or {}).get('status') or '')
            if last_status in terminal:
                items=(job or {}).get('items') or []
                item=items[0] if isinstance(items,list) and items else {}
                error_code=item.get('error_code') if isinstance(item,dict) else None
                log.info(
                    'e2e smoke terminal job=%s status=%s item_error=%s',
                    job_id,
                    last_status,
                    error_code or '',
                )
                return
            await asyncio.sleep(0.5)

        log.error('e2e smoke timeout job=%s last_status=%s',job_id,last_status)
    except Exception as exc:
        log.exception('e2e smoke failed: %s',exc)

async def require_key(x_remask_worker_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_remask_worker_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='invalid worker key')

@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    await pool.provisioning_state.init()
    if mirror.enabled:
        try:
            snapshots=await mirror.load_jobs()
            imported=await store.import_snapshots(snapshots)
            log.info('persistent job mirror restored snapshots=%d imported=%d',len(snapshots),imported)
        except MirrorError as exc:
            log.error('persistent job mirror restore failed: %s',exc)
    await pool.start()
    log.info(
        'bm browser runtime config canary=%s diagnostics=%s browser_concurrency=%s',
        BM_CANARY_ON_START,
        str(os.getenv('REMASK_BM_DIAGNOSTICS') or ''),
        str(os.getenv('REMASK_BM_BROWSER_CONCURRENCY') or ''),
    )
    smoke_task=asyncio.create_task(run_startup_smoke(),name='remask-e2e-smoke')
    bm_canary_task=asyncio.create_task(
        run_bm_browser_canary(),
        name='remask-bm-browser-canary',
    )
    yield
    smoke_task.cancel()
    bm_canary_task.cancel()
    await asyncio.gather(
        smoke_task,
        bm_canary_task,
        return_exceptions=True,
    )
    await pool.stop()

_FP_AUTH_GATE_CACHE: dict[str, dict[str, Any]] = {}
_FP_AUTH_GATE_TTL_SECONDS = 20.0

def _remember_fp_auth_gate(profile_id: str, preflight: dict[str, Any]) -> None:
    _FP_AUTH_GATE_CACHE[str(profile_id)] = {
        'at': time.monotonic(),
        'auth_blocked': bool(preflight.get('auth_blocked')),
        'auth_error_code': str(preflight.get('auth_error_code') or '').strip().upper(),
        'facebook_session_ready': bool(preflight.get('facebook_session_ready')),
    }

def _cached_fp_auth_gate(profile_id: str) -> dict[str, Any] | None:
    row = _FP_AUTH_GATE_CACHE.get(str(profile_id))
    if not isinstance(row, dict):
        return None
    if time.monotonic() - float(row.get('at') or 0.0) > _FP_AUTH_GATE_TTL_SECONDS:
        return None
    return row

def _request_fan_page_profile_ids(request: CreateJobRequest) -> list[str]:
    out: list[str] = []
    for profile in request.profiles:
        has_fp = False
        for task in profile.tasks:
            if str(task.action or '').strip().lower() != 'provisioning':
                continue
            payload = task.payload if isinstance(task.payload, dict) else {}
            steps = payload.get('steps') if isinstance(payload.get('steps'), list) else []
            if any(str(step or '').strip().upper() == 'FAN_PAGES' for step in steps):
                has_fp = True
                break
        if has_fp:
            profile_id = str(profile.profile_id or '').strip()
            if profile_id and profile_id not in out:
                out.append(profile_id)
    return out

def _view_fan_page_retry_profile_ids(view: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in view.get('items') or []:
        if not isinstance(item, dict):
            continue
        if str(item.get('status') or '').upper() != 'FAILED':
            continue
        has_fp = False
        for task in item.get('tasks') or []:
            if not isinstance(task, dict):
                continue
            payload = task.get('payload') if isinstance(task.get('payload'), dict) else {}
            steps = payload.get('steps') if isinstance(payload.get('steps'), list) else []
            if any(str(step or '').strip().upper() == 'FAN_PAGES' for step in steps):
                has_fp = True
                break
        if has_fp:
            profile_id = str(item.get('profile_id') or '').strip()
            if profile_id and profile_id not in out:
                out.append(profile_id)
    return out

async def _require_fp_auth_ready(profile_ids: list[str]) -> None:
    async def check(profile_id: str) -> tuple[str, dict[str, Any]]:
        cached = _cached_fp_auth_gate(profile_id)
        if cached is not None:
            return profile_id, cached
        preflight = await profile_preflight(profile_id)
        _remember_fp_auth_gate(profile_id, preflight)
        return profile_id, preflight

    if not profile_ids:
        return

    results = await asyncio.gather(
        *(check(profile_id) for profile_id in profile_ids)
    )
    blocked: list[str] = []
    not_ready: list[str] = []
    for profile_id, state in results:
        code = str(state.get('auth_error_code') or '').strip().upper()
        if bool(state.get('auth_blocked')):
            blocked.append(f'{profile_id}:{code or "FACEBOOK_AUTH_BLOCKED"}')
        elif state.get('facebook_session_ready') is not True:
            not_ready.append(profile_id)

    if blocked:
        raise HTTPException(
            status_code=409,
            detail='FP_AUTH_BLOCKED: ' + ', '.join(blocked),
        )
    if not_ready:
        raise HTTPException(
            status_code=409,
            detail='FP_SESSION_NOT_READY: ' + ', '.join(not_ready),
        )

app=FastAPI(title='ReMask Python Worker',version='0.4.0',lifespan=lifespan)

@app.get('/health',response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        service='remask-python-worker',
        queued_items=await store.queue_count(),
        worker_concurrency=CONCURRENCY,
    )

@app.get('/ready',dependencies=[Depends(require_key)])
async def ready():
    try:
        profiles=await pool.resolver.list_profiles()
    except Exception as exc:
        log.error('readiness failed: profile resolver unavailable: %s',exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'profile resolver unavailable: {exc}',
        ) from exc

    return {
        'ok':True,
        'service':'remask-python-worker',
        'ready':True,
        'queued_items':await store.queue_count(),
        'worker_concurrency':CONCURRENCY,
        'profiles_visible':len(profiles),
        'profile_resolver':'ok',
        'db_path':str(DB_PATH),
        'revision':str(
            os.getenv('RAILWAY_GIT_COMMIT_SHA')
            or os.getenv('REMASK_DEPLOY_REV')
            or ''
        )[:12],
        'create_bm_payload_version':'business_suite_ui_v1',
        'volume_mounted':bool(str(os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or '').strip()),
        'volume_path':str(os.getenv('RAILWAY_VOLUME_MOUNT_PATH') or ''),
    }

@app.post('/api/v1/profiles/{profile_id}/preflight',dependencies=[Depends(require_key)])
async def profile_preflight(profile_id: str):
    clean_profile=str(profile_id or '').strip()
    if not clean_profile:
        raise HTTPException(status_code=400,detail='profile_id is required')

    try:
        context=await pool.resolver.resolve(clean_profile)
    except ProfileContextError as exc:
        raise HTTPException(
            status_code=422,
            detail=f'PROFILE_CONTEXT_ERROR: {exc}',
        ) from exc

    try:
        async with ProfileSession(context) as profile_session:
            preflight_started=time.monotonic()
            proxy_started=time.monotonic()
            proxy_task=asyncio.create_task(profile_session.proxy_check())

            browser_state={
                'session_ready':False,
                'ready':False,
                'create_surface_ready':False,
                'current_url':'',
                'account_id':'',
                'diagnostics':[],
                'error':'',
                'error_code':'',
            }
            browser_started=time.monotonic()
            business_browser=None
            try:
                business_browser=await profile_session.facebook_business_browser()
                browser_preflight=await asyncio.wait_for(
                    business_browser.preflight(),
                    timeout=50.0,
                )
                browser_state.update({
                    'session_ready':True,
                    'ready':bool(browser_preflight.ready),
                    'create_surface_ready':bool(browser_preflight.create_surface_ready),
                    'current_url':browser_preflight.current_url,
                    'account_id':browser_preflight.account_id,
                    'diagnostics':list(browser_preflight.diagnostics),
                })
            except asyncio.TimeoutError:
                browser_state.update({
                    'error':'Business Suite browser preflight exceeded 50 seconds',
                    'error_code':'BUSINESS_PREFLIGHT_TIMEOUT',
                })
            except BrowserBusinessError as exc:
                diagnostic=exc.diagnostic if isinstance(exc.diagnostic,dict) else {}
                browser_state.update({
                    'error':str(exc),
                    'error_code':exc.code,
                    'diagnostic':diagnostic,
                    'current_url':str(diagnostic.get('url') or ''),
                    'auth_evidence':str(diagnostic.get('auth_evidence') or ''),
                })
                if exc.code == 'BUSINESS_CREATE_UI_UNAVAILABLE':
                    # Authentication/navigation already succeeded; only the
                    # Create-BM surface was not recognized. Keep session/Page
                    # synchronization independent from create-route readiness.
                    browser_state['session_ready']=True
            browser_ms=int((time.monotonic()-browser_started)*1000)

            try:
                proxy_result=await proxy_task
            except ProxyCheckError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f'PROXY_DEAD: {exc}',
                ) from exc
            proxy_ms=int((time.monotonic()-proxy_started)*1000)

            saved_pages=[
                {
                    'id':str(row.get('id') or '').strip(),
                    'name':str(row.get('name') or row.get('id') or '').strip(),
                    'category':str(row.get('category') or '').strip(),
                    'tasks':[
                        str(task)
                        for task in (row.get('tasks') or [])
                        if isinstance(task,(str,int))
                    ],
                    'business_id':str(row.get('business_id') or '').strip(),
                    'is_owned':row.get('is_owned'),
                }
                for row in (context.pages or [])
                if isinstance(row,dict)
                and str(row.get('id') or '').strip().isdigit()
            ]
            pages_source='saved_profile_pages' if saved_pages else ''

            pages_ms=0
            if not saved_pages and browser_state['session_ready'] and business_browser is not None:
                pages_started=time.monotonic()
                try:
                    discovered_pages=await asyncio.wait_for(
                        business_browser.discover_managed_pages(fast=True),
                        timeout=28.0,
                    )
                    saved_pages=[
                        {
                            'id':str(row.get('id') or '').strip(),
                            'name':str(row.get('name') or row.get('id') or '').strip(),
                            'category':str(row.get('category') or '').strip(),
                            'tasks':[
                                str(task)
                                for task in (row.get('tasks') or [])
                                if isinstance(task,(str,int))
                            ],
                            'business_id':str(row.get('business_id') or '').strip(),
                            'is_owned':row.get('is_owned'),
                        }
                        for row in discovered_pages
                        if isinstance(row,dict)
                        and str(row.get('id') or '').strip().isdigit()
                    ]
                    if saved_pages:
                        pages_source='facebook_business_browser'
                except asyncio.TimeoutError:
                    browser_state['page_discovery_error']='Fan Page discovery exceeded 28 seconds'
                    browser_state['page_discovery_error_code']='FAN_PAGES_DISCOVERY_TIMEOUT'
                except BrowserBusinessError as exc:
                    browser_state['page_discovery_error']=str(exc)
                    browser_state['page_discovery_error_code']=exc.code
                finally:
                    pages_ms=int((time.monotonic()-pages_started)*1000)

            total_ms=int((time.monotonic()-preflight_started)*1000)
            log.info(
                'bm preflight profile=%s total_ms=%d proxy_ms=%d browser_ms=%d pages_ms=%d '
                'browser_ready=%s create_ready=%s pages=%d page_source=%s browser_error=%s '
                'auth_evidence=%s current_url=%s pages_error=%s',
                clean_profile,
                total_ms,
                proxy_ms,
                browser_ms,
                pages_ms,
                bool(browser_state.get('ready')),
                bool(browser_state.get('create_surface_ready')),
                len(saved_pages),
                pages_source,
                str(browser_state.get('error_code') or ''),
                str(browser_state.get('auth_evidence') or ''),
                str(browser_state.get('current_url') or '')[:500],
                str(browser_state.get('page_discovery_error_code') or ''),
            )

            saved_pages.sort(
                key=lambda page: (
                    1 if str(page.get('business_id') or '').strip() else 0,
                    str(page.get('name') or '').casefold(),
                )
            )

            auth_codes={
                'CHECKPOINT_REQUIRED',
                'SESSION_EXPIRED',
                'TWO_FACTOR_REQUIRED',
            }
            auth_error_code=str(
                browser_state.get('error_code')
                or browser_state.get('page_discovery_error_code')
                or ''
            ).strip().upper()
            auth_blocked=auth_error_code in auth_codes
            facebook_session_ready=bool(
                browser_state.get('session_ready')
                and not auth_blocked
            )
            browser_ui_ready=bool(
                browser_state['ready']
                and browser_state['create_surface_ready']
                and facebook_session_ready
            )

    except HTTPException:
        raise
    except Exception as exc:
        log.exception('profile preflight failed profile=%s: %s', clean_profile, exc)
        raise HTTPException(
            status_code=502,
            detail=f'PROFILE_PREFLIGHT_FAILED: {exc}',
        ) from exc

    result = {
        'ok':True,
        'profile_id':clean_profile,
        'profile_context':'ok',
        'proxy':'ok',
        'proxy_exit_ip':str(proxy_result.get('exit_ip') or ''),
        'proxy_latency_ms':int(proxy_result.get('latency_ms') or 0),
        'facebook_session':'browser',
        'facebook_session_ready':facebook_session_ready,
        'auth_blocked':auth_blocked,
        'auth_error_code':auth_error_code,
        'browser_business':browser_state,
        'actor_present':False,
        'fb_dtsg_present':False,
        'lsd_present':False,
        'jazoest_present':False,
        'web_error':'',
        'graph_api':{
            'ready':False,
            'token_present':bool(str(context.access_token or '').strip()),
            'identity_ready':False,
            'permissions_ready':False,
            'pages_ready':False,
            'businesses_ready':False,
            'permissions':{},
            'pages':[],
            'businesses':[],
            'error':'not used by Add BM',
        },
        'private_pages':{
            'ready':False,
            'pages':[],
            'source':'',
            'error':'not used by Add BM',
        },
        'saved_pages_count':len(saved_pages),
        'pages':saved_pages,
        'pages_count':len(saved_pages),
        'pages_source':pages_source,
        'businesses':[],
        'businesses_count':0,
        'email_present':bool(str(context.email or '').strip()),
        'first_name_present':bool(str(context.first_name or '').strip()),
        'last_name_present':bool(str(context.last_name or '').strip()),
        'display_name_present':bool(str(context.display_name or '').strip()),
        'create_bm_candidates':[],
        'bm_routes':{
            'browser_ui':browser_ui_ready,
            'official_graph_api':False,
            'web_page_backed_candidate':False,
            'web_scope_selector_candidate':False,
            'web_dynamic_or_manual':False,
        },
        'bm_route_ready':browser_ui_ready,
    }
    _remember_fp_auth_gate(clean_profile, result)
    return result

@app.get('/api/v1/facebook/docids',dependencies=[Depends(require_key)])
async def facebook_docids(operation: str | None = None):
    return {
        'ok':True,
        **registry_view(operation),
    }

@app.post('/api/v1/facebook/docids/{operation}',dependencies=[Depends(require_key)])
async def register_facebook_docid(
    operation: str,
    payload: dict = Body(...),
):
    clean_operation=str(operation or '').strip().upper()
    if clean_operation not in {'CREATE_BM','CREATE_AD_ACCOUNT','LIST_PAGES'}:
        raise HTTPException(
            status_code=400,
            detail=(
                'supported doc_id operations: CREATE_BM, '
                'CREATE_AD_ACCOUNT, LIST_PAGES'
            ),
        )

    default_endpoint=(
        'https://business.facebook.com/api/graphql/'
        if clean_operation in {'CREATE_BM','CREATE_AD_ACCOUNT'}
        else 'https://www.facebook.com/api/graphql/'
    )
    default_mode=(
        'scope_selector_business_creation_v1'
        if clean_operation == 'CREATE_BM'
        else (
            'business_ad_account_create_v1'
            if clean_operation == 'CREATE_AD_ACCOUNT'
            else 'account_quality_user_pages_v1'
        )
    )

    try:
        candidate=upsert_candidate(
            clean_operation,
            doc_id=str(payload.get('doc_id') or '').strip(),
            friendly_name=str(payload.get('friendly_name') or '').strip(),
            endpoint_url=str(
                payload.get('endpoint_url')
                or default_endpoint
            ).strip(),
            variables_mode=str(
                payload.get('variables_mode')
                or default_mode
            ).strip(),
            source=str(payload.get('source') or 'manual_capture').strip(),
            priority=int(payload.get('priority') or 7500),
            observed_at=str(payload.get('observed_at') or '').strip(),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400,detail=str(exc)) from exc

    log.info(
        'doc_id candidate registered operation=%s doc_id=%s friendly=%s mode=%s source=%s',
        clean_operation,
        candidate.doc_id,
        candidate.friendly_name or '-',
        candidate.variables_mode,
        candidate.source,
    )

    return {
        'ok':True,
        'candidate':candidate.as_dict(),
        'registry':registry_view(clean_operation),
    }

@app.get('/api/v1/profiles/{profile_id}/live-inventory',dependencies=[Depends(require_key)])
async def profile_live_inventory(
    profile_id: str,
    business_ids: str | None = None,
    ad_account_hints: str | None = None,
):
    clean_profile=str(profile_id or '').strip()
    if not clean_profile:
        raise HTTPException(status_code=400,detail='profile_id is required')

    started=time.monotonic()
    stage='resolver'
    try:
        context=await pool.resolver.resolve(clean_profile)
    except ProfileContextError as exc:
        raise HTTPException(
            status_code=422,
            detail=f'PROFILE_CONTEXT_ERROR: {exc}',
        ) from exc

    warnings: list[str] = []

    # REMASK_REQUESTED_BUSINESS_WORKER_SCOPE_V1
    # An explicit business_ids query is a target scope, not just another hint.
    # This keeps a one-BM Workspace sync from expanding back to every durable
    # BM/RK pair already stored for the profile.
    requested_business_ids={
        value.strip()
        for value in str(business_ids or '').split(',')
        if value.strip().isdigit()
    }

    # Prefer already-confirmed durable BM identities. For a profile that ReMask
    # itself provisioned, forcing a fresh Business Suite HOME discovery first
    # is both redundant and fragile: Meta HOME can take >18s to settle while
    # the exact Business Settings route is directly addressable.
    confirmed_bindings=await pool.provisioning_state.confirmed_ad_account_bindings_for_profile(
        clean_profile
    )
    latest_entities=await pool.provisioning_state.latest_profile_entities(
        clean_profile
    )

    binding_by_business={
        str(row.get('business_id') or '').strip():row
        for row in confirmed_bindings
        if isinstance(row,dict)
        and str(row.get('business_id') or '').strip().isdigit()
        and (
            not requested_business_ids
            or str(row.get('business_id') or '').strip()
                in requested_business_ids
        )
    }
    latest_business_id=str(
        (latest_entities or {}).get('business_id') or ''
    ).strip()
    known_business_ids=set(binding_by_business)
    if (
        latest_business_id.isdigit()
        and (
            not requested_business_ids
            or latest_business_id in requested_business_ids
        )
    ):
        known_business_ids.add(latest_business_id)

    # REMASK_DURABLE_BINDING_ACCOUNT_HINTS_V1
    # Provisioning state already persists worker-confirmed BM->RK pairs. Use
    # those IDs only as targets for a fresh browser revalidation. Do not make
    # fast sync depend on PHP re-sending the same relation in ad_account_hints.
    known_accounts_by_business: dict[str,set[str]] = {}
    for row in confirmed_bindings:
        if not isinstance(row,dict):
            continue
        hinted_business_id=str(row.get('business_id') or '').strip()
        hinted_account_id=str(row.get('ad_account_id') or '').strip()
        if hinted_account_id.startswith('act_'):
            hinted_account_id=hinted_account_id[4:]
        if (
            hinted_business_id.isdigit()
            and hinted_account_id.isdigit()
            and (
                not requested_business_ids
                or hinted_business_id in requested_business_ids
            )
        ):
            known_business_ids.add(hinted_business_id)
            known_accounts_by_business.setdefault(
                hinted_business_id,set()
            ).add(hinted_account_id)

    # REMASK_PRIVATE_SYNC_BUSINESS_HINTS_V1
    # Workspace may already know BM IDs even when Business Suite HOME fails to
    # render the portfolio selector. Treat them as navigation hints only; the
    # browser still has to prove each BM/RK through the live settings surface.
    for hinted_business_id in requested_business_ids:
        known_business_ids.add(hinted_business_id)

    # BM:RK pairs below come only from the last live-confirmed Workspace
    # snapshot. They are hints for surviving Meta selector/UI drift; they are
    # never accepted without a fresh live Ads Manager observation.
    for raw_pair in str(ad_account_hints or '').split(','):
        raw_pair=raw_pair.strip()
        if not raw_pair or ':' not in raw_pair:
            continue
        hinted_business_id,hinted_account_id=raw_pair.split(':',1)
        hinted_business_id=hinted_business_id.strip()
        hinted_account_id=hinted_account_id.strip()
        if (
            hinted_business_id.isdigit()
            and hinted_account_id.isdigit()
            and (
                not requested_business_ids
                or hinted_business_id in requested_business_ids
            )
        ):
            known_business_ids.add(hinted_business_id)
            known_accounts_by_business.setdefault(
                hinted_business_id,set()
            ).add(hinted_account_id)

    try:
        stage='profile_session'
        async with ProfileSession(context) as profile_session:
            stage='browser_open'
            browser_open_started=time.monotonic()
            try:
                browser=await asyncio.wait_for(
                    profile_session.facebook_business_browser(),
                    timeout=24.0,
                )
            except asyncio.TimeoutError as exc:
                log.warning(
                    'live inventory profile=%s browser_open timeout ms=%d',
                    clean_profile,
                    int((time.monotonic()-browser_open_started)*1000),
                )
                raise HTTPException(
                    status_code=504,
                    detail='LIVE_INVENTORY_BROWSER_OPEN_TIMEOUT',
                ) from exc
            log.info(
                'live inventory profile=%s browser_open ms=%d',
                clean_profile,
                int((time.monotonic()-browser_open_started)*1000),
            )
            log.info(
                'live inventory profile=%s durable_targets businesses=%d exact_pairs=%d requested_businesses=%s',
                clean_profile,
                len(known_business_ids),
                sum(len(ids) for ids in known_accounts_by_business.values()),
                ','.join(sorted(requested_business_ids)) or '-',
            )

            business_map: dict[str,str] = {}
            discovery_source=''
            prevalidated_inventory: dict[str,dict] = {}

            # REMASK_CONFIRMED_HINT_FAST_REVALIDATION_V1
            # When Workspace has a last-live-confirmed BM->RK pair, validate that
            # exact pair against the current authenticated Ads Manager first.
            # This avoids spending 8-20s on the flaky Business Suite selector
            # before probing the account Meta is already opening live.
            if known_accounts_by_business:
                stage='confirmed_hint_revalidation'
                fast_started=time.monotonic()
                for hinted_business_id,expected_ids in sorted(
                    known_accounts_by_business.items()
                )[:25]:
                    try:
                        ads_probe=await asyncio.wait_for(
                            browser.probe_ads_manager_inventory_context(
                                business_id=str(hinted_business_id),
                                timeout_seconds=7.0,
                                expected_account_ids=sorted(expected_ids),
                            ),
                            timeout=10.0,
                        )
                    except (asyncio.TimeoutError,BrowserBusinessError) as exc:
                        log.warning(
                            'live inventory profile=%s business=%s fast live revalidation failed=%s',
                            clean_profile,
                            hinted_business_id,
                            f'{exc.__class__.__name__}: {exc}',
                        )
                        continue

                    if not ads_probe.get('confirmed'):
                        continue
                    confirmed_accounts=[
                        account
                        for account in (
                            ads_probe.get('confirmed_accounts') or []
                        )
                        if isinstance(account,dict)
                    ]
                    confirmed_ids={
                        str(
                            account.get('id')
                            or account.get('account_id')
                            or ''
                        ).replace('act_','').strip()
                        for account in confirmed_accounts
                    }
                    expected_clean={
                        str(value).replace('act_','').strip()
                        for value in expected_ids
                    }
                    if not confirmed_accounts or not (
                        confirmed_ids & expected_clean
                    ):
                        continue

                    business_key=str(hinted_business_id)
                    business_map[business_key]=business_key
                    prevalidated_inventory[business_key]={
                        'business_id':business_key,
                        'ready':True,
                        'confirmed_empty':False,
                        'accounts':confirmed_accounts,
                        'accounts_count':len(confirmed_accounts),
                        'source':str(
                            ads_probe.get('confirmation_source')
                            or 'ads_manager_confirmed_snapshot_revalidation'
                        ),
                        'attempts':[{
                            'requested_url':str(
                                ads_probe.get('requested_url') or ''
                            ),
                            'landed_url':str(
                                ads_probe.get('final_url') or ''
                            ),
                            'result':'confirmed_snapshot_live_revalidated',
                        }],
                        'diagnostics':ads_probe.get('diagnostics') or [],
                        'section_diagnostic':{},
                        'ads_manager_diagnostic':ads_probe,
                    }
                    log.info(
                        'live inventory profile=%s business=%s fast live revalidation ready=True accounts=%d source=%s',
                        clean_profile,
                        business_key,
                        len(confirmed_accounts),
                        prevalidated_inventory[business_key]['source'],
                    )

                if business_map:
                    discovery_source='confirmed_snapshot_ads_manager_revalidation'
                    log.info(
                        'live inventory profile=%s fast revalidation ms=%d businesses=%d; Business Suite discovery skipped',
                        clean_profile,
                        int((time.monotonic()-fast_started)*1000),
                        len(business_map),
                    )

            discovery_revalidation=False
            if not business_map and known_accounts_by_business:
                # REMASK_EXACT_HINTS_SKIP_FULL_BM_DISCOVERY_V1
                # We already know exact BM->RK candidates. Full Business Suite
                # HOME/selector discovery can hang during Playwright cancellation
                # for >60s, so do not put it in front of targeted live proof.
                business_map={
                    business_id: business_id
                    for business_id in sorted(known_accounts_by_business)
                    if str(business_id).isdigit()
                }
                if business_map:
                    discovery_revalidation=True
                    discovery_source='confirmed_account_hint_targeted_revalidation'
                    log.info(
                        'live inventory profile=%s exact BM/RK hints present; '
                        'full Business Suite discovery skipped targets=%s',
                        clean_profile,
                        ','.join(sorted(business_map)),
                    )

            if not business_map:
                stage='business_discovery'
                discovery_started=time.monotonic()
                try:
                    try:
                        business_discovery_timeout=float(
                            os.getenv(
                                'REMASK_LIVE_INVENTORY_BUSINESS_DISCOVERY_TIMEOUT_SECONDS',
                                '28',
                            )
                        )
                    except (TypeError,ValueError):
                        business_discovery_timeout=28.0
                    business_discovery_timeout=max(
                        20.0,
                        min(business_discovery_timeout,40.0),
                    )
                    business_map=await asyncio.wait_for(
                        browser.snapshot_businesses(),
                        timeout=business_discovery_timeout,
                    )
                    business_diag=getattr(
                        browser,
                        '_last_business_inventory_diagnostic',
                        {},
                    )
                    discovery_source=str(
                        (business_diag or {}).get('source')
                        or 'business_suite_private_inventory'
                    )

                    hinted_only=sorted(
                        set(known_business_ids) - set(business_map)
                    )
                    if hinted_only:
                        log.info(
                            'live inventory profile=%s stale_or_unconfirmed_business_hints=%s',
                            clean_profile,
                            ','.join(hinted_only),
                        )

                    if not business_map:
                        warnings.append(
                            'Private Business Suite inventory returned no Business portfolios'
                        )
                        log.warning(
                            'live inventory profile=%s business_discovery diagnostic=%s',
                            clean_profile,
                            json.dumps(
                                business_diag,
                                ensure_ascii=False,
                                separators=(',', ':'),
                            )[:6000],
                        )
                except asyncio.TimeoutError:
                    warnings.append('Business discovery timed out')
                    business_map={}
                    discovery_source='business_suite_discovery_timeout'
                    business_diag=getattr(
                        browser,
                        '_last_business_inventory_diagnostic',
                        {},
                    )
                    log.warning(
                        'live inventory profile=%s business_discovery timeout=%.1fs diagnostic=%s',
                        clean_profile,
                        business_discovery_timeout,
                        json.dumps(
                            business_diag,
                            ensure_ascii=False,
                            separators=(',', ':'),
                        )[:6000],
                    )
                finally:
                    log.info(
                        'live inventory profile=%s business_discovery source=%s ms=%d count=%d',
                        clean_profile,
                        discovery_source,
                        int((time.monotonic()-discovery_started)*1000),
                        len(business_map),
                    )

            # If Meta's HOME/selector emitted no BM rows, do not stop here.
            # ReMask already has durable BM identities from previously confirmed
            # provisioning/live state. Re-open those exact BM candidates and only
            # accept them after the current browser proves their RK scope live.
            if not business_map and known_business_ids:
                business_map={
                    business_id: business_id
                    for business_id in sorted(known_business_ids)
                    if str(business_id).isdigit()
                }
                if business_map:
                    discovery_revalidation=True
                    discovery_source='confirmed_business_hint_live_revalidation'
                    log.info(
                        'live inventory profile=%s selector empty; revalidating known businesses=%s',
                        clean_profile,
                        ','.join(sorted(business_map)),
                    )

            businesses=[]
            live_business_ids:set[str]=set()

            async def load_business_inventory(business_id: str):
                nonlocal browser
                business_key=str(business_id)
                if business_key in prevalidated_inventory:
                    return dict(prevalidated_inventory[business_key])
                last_error=None
                for attempt in range(2):
                    try:
                        try:
                            ads_probe=await asyncio.wait_for(
                                browser.probe_ads_manager_inventory_context(
                                    business_id=str(business_id),
                                    timeout_seconds=10.0,
                                    expected_account_ids=sorted(
                                        known_accounts_by_business.get(
                                            str(business_id),
                                            set(),
                                        )
                                    ),
                                ),
                                timeout=12.0,
                            )
                        except asyncio.TimeoutError:
                            ads_probe={
                                'source':'ads_manager_read_only_probe',
                                'business_id':str(business_id),
                                'confirmed':False,
                                'confirmed_accounts':[],
                                'error':'ADS_MANAGER_SCOPE_TIMEOUT',
                                'timed_out':True,
                            }
                            log.warning(
                                'live inventory profile=%s business=%s '
                                'Ads Manager scope probe timed out; '
                                'continuing with Business Settings inventory',
                                clean_profile,
                                business_id,
                            )

                        if ads_probe.get('confirmed'):
                            confirmed_accounts=[
                                account
                                for account in (
                                    ads_probe.get('confirmed_accounts') or []
                                )
                                if isinstance(account,dict)
                            ]
                            if confirmed_accounts:
                                return {
                                    'business_id':str(business_id),
                                    'ready':True,
                                    'confirmed_empty':False,
                                    'accounts':confirmed_accounts,
                                    'accounts_count':len(confirmed_accounts),
                                    'source':str(
                                        ads_probe.get('confirmation_source')
                                        or 'ads_manager_business_scope_inventory'
                                    ),
                                    'attempts':[{
                                        'requested_url':str(
                                            ads_probe.get('requested_url') or ''
                                        ),
                                        'landed_url':str(
                                            ads_probe.get('final_url') or ''
                                        ),
                                        'result':'business_scope_confirmed',
                                    }],
                                    'diagnostics':(
                                        ads_probe.get('diagnostics') or []
                                    ),
                                    'section_diagnostic':{},
                                    'ads_manager_diagnostic':ads_probe,
                                }

                        settings_inventory=await asyncio.wait_for(
                            browser.snapshot_ad_accounts_for_business(
                                business_id=str(business_id),
                                timeout_seconds=8.0,
                            ),
                            timeout=10.0,
                        )
                        settings_inventory['ads_manager_diagnostic']=ads_probe
                        return settings_inventory
                    except BrowserBusinessError as exc:
                        last_error=exc
                        if (
                            attempt == 0
                            and exc.code in {
                                'CHECKPOINT_REQUIRED',
                                'SESSION_EXPIRED',
                                'TWO_FACTOR_REQUIRED',
                            }
                        ):
                            log.warning(
                                'live inventory profile=%s business=%s auth redirect=%s; reopening profile browser once',
                                clean_profile,
                                business_id,
                                exc.code,
                            )
                            try:
                                await browser.close()
                            except Exception:
                                pass
                            try:
                                profile_session._business_browser=None
                            except Exception:
                                pass
                            browser=await asyncio.wait_for(
                                profile_session.facebook_business_browser(),
                                timeout=24.0,
                            )
                            continue
                        raise
                if last_error is not None:
                    raise last_error
                raise RuntimeError('business inventory retry exhausted')

            stage='rk_inventory'
            for business_id,business_name in sorted(
                business_map.items(),
                key=lambda item: str(item[0]),
            )[:25]:
                row={
                    'id':str(business_id or '').strip(),
                    'name':str(business_name or business_id or '').strip(),
                    'ad_accounts':[],
                    'ad_accounts_count':0,
                    'ad_accounts_ready':False,
                }
                inventory_started=time.monotonic()
                try:
                    inventory=await load_business_inventory(
                        str(business_id)
                    )
                    row['ad_accounts']=[
                        account
                        for account in (inventory.get('accounts') or [])
                        if isinstance(account,dict)
                    ]
                    row['ad_accounts_count']=len(row['ad_accounts'])
                    row['ad_accounts_ready']=bool(inventory.get('ready'))
                    row['ad_accounts_source']=str(
                        inventory.get('source') or ''
                    )
                    row['attempts']=inventory.get('attempts') or []
                    row['diagnostics']=inventory.get('diagnostics') or []
                    row['section_diagnostic']=(
                        inventory.get('section_diagnostic') or {}
                    )
                    row['ads_manager_diagnostic']=(
                        inventory.get('ads_manager_diagnostic') or {}
                    )
                    if row['ad_accounts_ready']:
                        live_business_ids.add(str(business_id))
                    else:
                        warnings.append(
                            f'BM {business_id}: live RK inventory not confirmed'
                        )
                        log.warning(
                            'live inventory profile=%s business=%s rk inconclusive attempts=%s diagnostics=%s',
                            clean_profile,
                            business_id,
                            json.dumps(
                                row.get('attempts') or [],
                                ensure_ascii=False,
                                separators=(',', ':'),
                            )[:2500],
                            json.dumps(
                                row.get('diagnostics') or [],
                                ensure_ascii=False,
                                separators=(',', ':'),
                            )[:9000],
                        )
                except asyncio.TimeoutError:
                    row['ad_accounts_source']='business_settings_timeout'
                    row['diagnostics']=[
                        getattr(
                            browser,
                            '_last_ad_account_section_diagnostic',
                            {},
                        )
                    ]
                    warnings.append(
                        f'BM {business_id}: live RK inventory timed out'
                    )
                    log.warning(
                        'live inventory profile=%s business=%s rk timeout diagnostic=%s',
                        clean_profile,
                        business_id,
                        json.dumps(
                            getattr(
                                browser,
                                '_last_ad_account_section_diagnostic',
                                {},
                            ),
                            ensure_ascii=False,
                            separators=(',', ':'),
                        )[:6000],
                    )
                except BrowserBusinessError as exc:
                    row['ad_accounts_source']=(
                        'business_auth_blocked'
                        if exc.code in {
                            'CHECKPOINT_REQUIRED',
                            'SESSION_EXPIRED',
                            'TWO_FACTOR_REQUIRED',
                        }
                        else 'browser_error'
                    )
                    row['browser_error_code']=exc.code
                    row['browser_error']=str(exc)
                    if isinstance(getattr(exc, 'diagnostic', None), dict):
                        row['browser_error_diagnostic']=exc.diagnostic
                    row['auth_blocked']=exc.code in {
                        'CHECKPOINT_REQUIRED',
                        'SESSION_EXPIRED',
                        'TWO_FACTOR_REQUIRED',
                    }
                    warnings.append(
                        f'BM {business_id}: {exc.code}'
                    )
                finally:
                    log.info(
                        'live inventory profile=%s business=%s rk_ms=%d ready=%s accounts=%d source=%s error=%s',
                        clean_profile,
                        business_id,
                        int((time.monotonic()-inventory_started)*1000),
                        bool(row.get('ad_accounts_ready')),
                        len(row.get('ad_accounts') or []),
                        str(row.get('ad_accounts_source') or ''),
                        str(row.get('browser_error_code') or ''),
                    )
                businesses.append(row)

            # Preserve only already-confirmed bindings as a fallback annotation;
            # they never turn a Business into "live_ready".
            seen_businesses={
                str(row.get('id') or '').strip()
                for row in businesses
                if isinstance(row,dict)
            }
            for business_id,binding in binding_by_business.items():
                if business_id not in seen_businesses:
                    businesses.append({
                        'id':business_id,
                        'name':business_id,
                        'ad_accounts':[],
                        'ad_accounts_count':0,
                        'ad_accounts_ready':False,
                        'source':'worker_confirmed_fallback',
                    })

            by_business={
                str(row.get('id') or '').strip():row
                for row in businesses
                if isinstance(row,dict)
                and str(row.get('id') or '').strip()
            }
            for business_id,binding in binding_by_business.items():
                ad_account_id=str(
                    binding.get('ad_account_id') or ''
                ).strip().removeprefix('act_')
                if not ad_account_id.isdigit():
                    continue
                business_row=by_business.get(business_id)
                if business_row is None:
                    continue
                existing={
                    str(row.get('id') or row.get('account_id') or '').strip().removeprefix('act_')
                    for row in (business_row.get('ad_accounts') or [])
                    if isinstance(row,dict)
                }
                if ad_account_id not in existing:
                    business_row.setdefault('ad_accounts',[]).append({
                        'id':ad_account_id,
                        'account_id':ad_account_id,
                        'name':str(binding.get('account_name') or '').strip(),
                        'business_id':business_id,
                        '_source':'worker_confirmed_fallback',
                    })
                    business_row['ad_accounts_count']=len(
                        business_row['ad_accounts']
                    )

            businesses.sort(
                key=lambda row:(
                    str(row.get('name') or '').casefold(),
                    str(row.get('id') or ''),
                )
            )

            # REMASK_SYNC_SINGLE_SOURCE_PAGES_V1
            # Sync is the only inventory fetch. Reuse this browser session to
            # collect Pages once; downstream UI reads the persisted snapshot.
            stage='page_inventory'
            pages=[]
            pages_source=''
            pages_ready=False
            pages_started=time.monotonic()
            try:
                discovered_pages=await asyncio.wait_for(
                    browser.discover_managed_pages(fast=True),
                    timeout=14.0,
                )
                pages=[
                    {
                        'id':str(row.get('id') or '').strip(),
                        'name':str(row.get('name') or row.get('id') or '').strip(),
                        'category':str(row.get('category') or '').strip(),
                        'tasks':[str(task) for task in (row.get('tasks') or []) if isinstance(task,(str,int))],
                        'business_id':str(row.get('business_id') or '').strip(),
                        'is_owned':row.get('is_owned'),
                    }
                    for row in (discovered_pages or [])
                    if isinstance(row,dict)
                    and str(row.get('id') or '').strip().isdigit()
                ]
                pages.sort(
                    key=lambda page:(
                        1 if str(page.get('business_id') or '').strip() else 0,
                        str(page.get('name') or '').casefold(),
                        str(page.get('id') or ''),
                    )
                )
                pages_source='facebook_business_browser'
                pages_ready=True
            except asyncio.TimeoutError:
                pages_source='facebook_business_browser_timeout'
                warnings.append('Fan Page inventory timed out')
            except BrowserBusinessError as exc:
                pages_source='facebook_business_browser_error'
                warnings.append(f'Fan Page inventory: {exc.code}')
            finally:
                log.info(
                    'live inventory profile=%s pages_ms=%d ready=%s pages=%d source=%s',
                    clean_profile,
                    int((time.monotonic()-pages_started)*1000),
                    pages_ready,
                    len(pages),
                    pages_source,
                )

            live_ready=bool(live_business_ids)
            if live_ready and discovery_revalidation:
                warnings=[
                    warning
                    for warning in warnings
                    if warning != 'Private Business Suite inventory returned no Business portfolios'
                ]
                log.info(
                    'live inventory profile=%s known BM revalidation recovered live state businesses=%s',
                    clean_profile,
                    ','.join(sorted(live_business_ids)),
                )

            result={
                'ok':True,
                'profile_id':clean_profile,
                # REMASK_SYNC_SESSION_READY_V1
                'session_ready':True,
                'live_ready':live_ready,
                'businesses':businesses,
                'businesses_count':len(businesses),
                'live_businesses_count':len(live_business_ids),
                'known_businesses_count':len(known_business_ids),
                'auth_blocked_businesses':[
                    str(row.get('id') or '')
                    for row in businesses
                    if isinstance(row,dict) and row.get('auth_blocked')
                ],
                'discovery_source':discovery_source,
                'business_inventory_diagnostic':getattr(
                    browser,
                    '_last_business_inventory_diagnostic',
                    {},
                ),
                'source':'business_suite_private_inventory',
                'pages':pages,
                'pages_count':len(pages),
                'pages_ready':pages_ready,
                'pages_source':pages_source,
                'warnings':warnings,
            }
            log.info(
                'live inventory profile=%s complete ms=%d live_ready=%s live_businesses=%d businesses=%d pages=%d warnings=%d',
                clean_profile,
                int((time.monotonic()-started)*1000),
                live_ready,
                len(live_business_ids),
                len(businesses),
                len(pages),
                len(warnings),
            )
            return result

    except asyncio.TimeoutError as exc:
        log.warning(
            'live inventory profile=%s outer timeout stage=%s ms=%d',
            clean_profile,
            stage,
            int((time.monotonic()-started)*1000),
        )
        raise HTTPException(
            status_code=504,
            detail=f'LIVE_INVENTORY_TIMEOUT:{stage}',
        ) from exc
    except BrowserBusinessError as exc:
        log.warning(
            'live inventory profile=%s browser error=%s message=%s',
            clean_profile,
            exc.code,
            exc,
        )
        raise HTTPException(
            status_code=409 if exc.code in {
                'CHECKPOINT_REQUIRED',
                'SESSION_EXPIRED',
                'TWO_FACTOR_REQUIRED',
            } else 502,
            detail=f'{exc.code}: {exc}',
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        log.exception(
            'live inventory failed profile=%s: %s',
            clean_profile,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=f'LIVE_INVENTORY_FAILED: {exc}',
        ) from exc

@app.get('/api/v1/profiles/{profile_id}/provisioning-state',dependencies=[Depends(require_key)])
async def profile_provisioning_state(profile_id: str):
    clean_profile=str(profile_id or '').strip()
    if not clean_profile:
        raise HTTPException(status_code=400,detail='profile_id is required')
    entities=await pool.provisioning_state.latest_profile_entities(clean_profile)
    ad_account_bindings=await pool.provisioning_state.confirmed_ad_account_bindings_for_profile(
        clean_profile
    )
    fan_pages=await pool.provisioning_state.latest_profile_fan_pages(clean_profile)
    return {
        'ok':True,
        **entities,
        'ad_account_bindings':ad_account_bindings,
        'fan_pages':fan_pages,
    }

@app.post('/api/v1/jobs',response_model=JobAccepted,dependencies=[Depends(require_key)])
async def create_job(request: CreateJobRequest) -> JobAccepted:
    fp_profiles=_request_fan_page_profile_ids(request)
    if fp_profiles:
        await _require_fp_auth_ready(fp_profiles)
    job_id,created=await store.create_job(request)
    view=await store.job_view(job_id)
    if view and mirror.enabled:
        try:
            await mirror.save_job(view)
        except MirrorError as exc:
            log.error('persistent job mirror save failed job=%s: %s',job_id,exc)
    if created:
        enqueued=await pool.enqueue_job(job_id)
    else:
        enqueued=0

    log.info(
        'job accepted id=%s created=%s profiles=%d enqueued=%d',
        job_id,
        created,
        len(request.profiles),
        enqueued,
    )

    return JobAccepted(
        job_id=job_id,
        status=(view or {'status':'QUEUED'})['status'],
        items_total=(view or {'items_total':len(request.profiles)})['items_total'],
    )

@app.get('/api/v1/jobs/{job_id}',dependencies=[Depends(require_key)])
async def get_job(job_id: str):
    view=await store.job_view(job_id)
    if not view:
        raise HTTPException(status_code=404,detail='job not found')
    return view

@app.post('/api/v1/jobs/{job_id}/retry-failed',response_model=RetryResponse,dependencies=[Depends(require_key)])
async def retry_failed(job_id: str) -> RetryResponse:
    current_view=await store.job_view(job_id)
    if not current_view:
        raise HTTPException(status_code=404,detail='job not found')
    fp_profiles=_view_fan_page_retry_profile_ids(current_view)
    if fp_profiles:
        await _require_fp_auth_ready(fp_profiles)
    count=await store.retry_failed(job_id)
    view=await store.job_view(job_id)
    if view and mirror.enabled:
        try:
            await mirror.save_job(view)
        except MirrorError as exc:
            log.error('persistent job mirror retry save failed job=%s: %s',job_id,exc)
    if count:
        await pool.enqueue_job(job_id)
    return RetryResponse(job_id=job_id,requeued=count)
