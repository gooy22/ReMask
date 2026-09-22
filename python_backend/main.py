from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.bridge import JobBridgeClient, JobBridgeError
from app.mirror import MirrorError, SnapshotMirror
from app.models import CreateJobRequest, HealthResponse, JobAccepted, RetryResponse
from app.runner import WorkerPool
from app.session import ProfileContextError, ProfileSession, ProxyCheckError
from app.store import JobStore
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
bridge=JobBridgeClient(os.getenv('REMASK_JOB_BRIDGE_URL'),INTERNAL_KEY)
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
        key=f'e2e-v02-proxy-check-{profile_id}'
        job_id=await bridge.create_proxy_check_job(profile_id,key)
        log.info('e2e smoke created via PHP bridge job=%s profile=%s',job_id,profile_id)
        terminal={'SUCCESS','FAILED','PARTIAL'}
        last_status=''
        for _ in range(90):
            job=await bridge.get_job(job_id)
            last_status=str(job.get('status') or '')
            if last_status in terminal:
                items=job.get('items') or []
                item=items[0] if isinstance(items,list) and items else {}
                error_code=item.get('error_code') if isinstance(item,dict) else None
                log.info('e2e smoke terminal job=%s status=%s item_error=%s',job_id,last_status,error_code or '')
                return
            await asyncio.sleep(0.5)
        log.error('e2e smoke timeout job=%s last_status=%s',job_id,last_status)
    except (JobBridgeError, Exception) as exc:
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
        async with ProfileSession(context) as profile_session:
            proxy_result=await profile_session.proxy_check()
            facebook=await profile_session.facebook_web()
            bootstrap=await facebook.bootstrap()

        return {
            'ok':True,
            'profile_id':clean_profile,
            'profile_context':'ok',
            'proxy':'ok',
            'proxy_exit_ip':str(proxy_result.get('exit_ip') or ''),
            'proxy_latency_ms':int(proxy_result.get('latency_ms') or 0),
            'facebook_session':'ok',
            'actor_present':bool(bootstrap.actor_id),
            'fb_dtsg_present':bool(bootstrap.fb_dtsg),
            'lsd_present':bool(bootstrap.lsd),
            'jazoest_present':bool(bootstrap.jazoest),
        }
    except ProfileContextError as exc:
        raise HTTPException(status_code=422,detail=f'PROFILE_CONTEXT_ERROR: {exc}') from exc
    except ProxyCheckError as exc:
        raise HTTPException(status_code=422,detail=f'PROXY_DEAD: {exc}') from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=422,detail=f'SESSION_EXPIRED: {exc}') from exc
    except RemoteRequestError as exc:
        raise HTTPException(status_code=502,detail=f'FACEBOOK_WEB_ERROR: {exc}') from exc

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
