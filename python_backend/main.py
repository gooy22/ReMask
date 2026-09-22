from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status

from app.mirror import MirrorError, SnapshotMirror
from app.models import CreateJobRequest, HealthResponse, JobAccepted, RetryResponse
from app.runner import WorkerPool
from app.session import ProfileContextError, ProfileSession, ProxyCheckError
from app.store import JobStore
from app.facebook_business_create import candidate_requirements
from app.facebook_graph_api import GraphApiError
from app.facebook_page_discovery import (
    PageDiscoveryError,
    discover_pages_via_web,
)
from app.facebook_docids import (
    list_candidates,
    registry_view,
    upsert_candidate,
)
from fb_worker import AuthenticationError, RemoteRequestError

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
    smoke_task=asyncio.create_task(run_startup_smoke(),name='remask-e2e-smoke')
    yield
    smoke_task.cancel()
    await asyncio.gather(smoke_task,return_exceptions=True)
    await pool.stop()

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
            try:
                proxy_result=await profile_session.proxy_check()
            except ProxyCheckError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f'PROXY_DEAD: {exc}',
                ) from exc

            web_state={
                'ready':False,
                'actor_present':False,
                'fb_dtsg_present':False,
                'lsd_present':False,
                'jazoest_present':False,
                'error':'',
            }
            try:
                facebook=await profile_session.facebook_web()
                bootstrap=await facebook.bootstrap()
                web_state.update({
                    'ready':True,
                    'actor_present':bool(bootstrap.actor_id),
                    'fb_dtsg_present':bool(bootstrap.fb_dtsg),
                    'lsd_present':bool(bootstrap.lsd),
                    'jazoest_present':bool(bootstrap.jazoest),
                })
            except (AuthenticationError, RemoteRequestError) as exc:
                web_state['error']=str(exc)

            graph_state={
                'ready':False,
                'token_present':bool(str(context.access_token or '').strip()),
                'identity_ready':False,
                'pages_ready':False,
                'businesses_ready':False,
                'user_id':'',
                'name':'',
                'pages':[],
                'businesses':[],
                'error':'',
                'identity_error':'',
                'pages_error':'',
                'businesses_error':'',
                'error_code':None,
                'error_subcode':None,
            }

            if graph_state['token_present']:
                try:
                    graph=await profile_session.graph_api()
                    identity=await graph.identity()
                    graph_state.update({
                        'ready':True,
                        'identity_ready':True,
                        'user_id':identity.user_id,
                        'name':identity.name,
                    })
                except GraphApiError as exc:
                    graph_state.update({
                        'error':str(exc),
                        'identity_error':str(exc),
                        'error_code':exc.code,
                        'error_subcode':exc.subcode,
                    })

                if graph_state['identity_ready']:
                    try:
                        pages=await graph.list_pages()
                        graph_state.update({
                            'pages_ready':True,
                            'pages':pages,
                        })
                    except GraphApiError as exc:
                        graph_state['pages_error']=str(exc)
                        if graph_state['error_code'] is None:
                            graph_state['error_code']=exc.code
                            graph_state['error_subcode']=exc.subcode

                    try:
                        businesses=await graph.list_businesses()
                        graph_state.update({
                            'businesses_ready':True,
                            'businesses':businesses,
                        })
                    except GraphApiError as exc:
                        graph_state['businesses_error']=str(exc)

            private_pages_state={
                'ready':False,
                'pages':[],
                'source':'',
                'doc_id':'',
                'friendly_name':'',
                'error':'',
                'diagnostics':[],
            }

            if not graph_state['pages'] and web_state['ready']:
                try:
                    private_result=await asyncio.wait_for(
                        discover_pages_via_web(facebook),
                        timeout=20.0,
                    )
                    private_pages_state.update({
                        'ready':True,
                        'pages':private_result.pages,
                        'source':private_result.source,
                        'doc_id':(
                            private_result.candidate.doc_id
                            if private_result.candidate is not None
                            else ''
                        ),
                        'friendly_name':(
                            private_result.candidate.friendly_name
                            if private_result.candidate is not None
                            else ''
                        ),
                        'diagnostics':private_result.diagnostics[-8:],
                    })
                except (PageDiscoveryError, asyncio.TimeoutError) as exc:
                    private_pages_state['error']=str(exc)

            selected_pages=(
                graph_state['pages']
                if graph_state['pages']
                else private_pages_state['pages']
            )
            pages_source=(
                'official_graph_api'
                if graph_state['pages']
                else (
                    private_pages_state['source']
                    if private_pages_state['pages']
                    else ''
                )
            )


    bm_candidates=[]
    for candidate in list_candidates('CREATE_BM'):
        bm_candidates.append({
            'doc_id':candidate.doc_id,
            'friendly_name':candidate.friendly_name,
            'variables_mode':candidate.variables_mode,
            'source':candidate.source,
            'priority':candidate.priority,
            'requirements':candidate_requirements(candidate),
        })

    return {
        'ok':True,
        'profile_id':clean_profile,
        'profile_context':'ok',
        'proxy':'ok',
        'proxy_exit_ip':str(proxy_result.get('exit_ip') or ''),
        'proxy_latency_ms':int(proxy_result.get('latency_ms') or 0),
        'facebook_session':'ok' if web_state['ready'] else 'unavailable',
        'actor_present':web_state['actor_present'],
        'fb_dtsg_present':web_state['fb_dtsg_present'],
        'lsd_present':web_state['lsd_present'],
        'jazoest_present':web_state['jazoest_present'],
        'web_error':web_state['error'],
        'graph_api':graph_state,
        'private_pages':private_pages_state,
        'pages':selected_pages,
        'pages_count':len(selected_pages),
        'pages_source':pages_source,
        'businesses':graph_state['businesses'],
        'businesses_count':len(graph_state['businesses']),
        'email_present':bool(str(context.email or '').strip()),
        'first_name_present':bool(str(context.first_name or '').strip()),
        'last_name_present':bool(str(context.last_name or '').strip()),
        'display_name_present':bool(str(context.display_name or '').strip()),
        'create_bm_candidates':bm_candidates,
        'bm_route_ready':bool(
            selected_pages
            and (
                graph_state['identity_ready']
                or web_state['ready']
            )
        ),
    }

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
    if clean_operation != 'CREATE_BM':
        raise HTTPException(
            status_code=400,
            detail='only CREATE_BM registry updates are enabled',
        )

    try:
        candidate=upsert_candidate(
            clean_operation,
            doc_id=str(payload.get('doc_id') or '').strip(),
            friendly_name=str(payload.get('friendly_name') or '').strip(),
            endpoint_url=str(
                payload.get('endpoint_url')
                or 'https://business.facebook.com/api/graphql/'
            ).strip(),
            variables_mode=str(
                payload.get('variables_mode')
                or 'scope_selector_business_creation_v1'
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

@app.post('/api/v1/jobs',response_model=JobAccepted,dependencies=[Depends(require_key)])
async def create_job(request: CreateJobRequest) -> JobAccepted:
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
    if not await store.job_view(job_id):
        raise HTTPException(status_code=404,detail='job not found')
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
