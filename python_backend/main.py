from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.models import CreateJobRequest, HealthResponse, JobAccepted, RetryResponse
from app.runner import WorkerPool
from app.store import JobStore

logging.basicConfig(
    level=os.getenv('LOG_LEVEL','INFO'),
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
)

DB_PATH=os.getenv('REMASK_JOB_DB','/var/lib/remask-python/jobs.sqlite3')
CONCURRENCY=int(os.getenv('REMASK_WORKER_CONCURRENCY','30'))
API_KEY=os.getenv('REMASK_WORKER_API_KEY')

store=JobStore(DB_PATH)
pool=WorkerPool(store,CONCURRENCY)

async def require_key(x_remask_worker_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_remask_worker_key != API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,detail='invalid worker key')

@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.init()
    await pool.start()
    yield
    await pool.stop()

app=FastAPI(title='ReMask Python Worker',version='0.1.0',lifespan=lifespan)

@app.get('/health',response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        service='remask-python-worker',
        queued_items=await store.queue_count(),
        worker_concurrency=CONCURRENCY,
    )

@app.post('/api/v1/jobs',response_model=JobAccepted,dependencies=[Depends(require_key)])
async def create_job(request: CreateJobRequest) -> JobAccepted:
    job_id,created=await store.create_job(request)
    if created:
        await pool.enqueue_job(job_id)
    view=await store.job_view(job_id)
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
    if count:
        await pool.enqueue_job(job_id)
    return RetryResponse(job_id=job_id,requeued=count)
