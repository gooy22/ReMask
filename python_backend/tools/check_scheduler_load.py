import asyncio,json,tempfile,time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from app.store import JobStore
from app.runner import WorkerPool
from app.models import CreateJobRequest
from app import runner

async def main():
 with tempfile.TemporaryDirectory() as tmp:
  store=JobStore(str(Path(tmp)/'jobs.sqlite3'));await store.init()
  pool=WorkerPool(store,concurrency=8);await pool.provisioning_state.init()
  calls={};active=set();peak=0;duplicate=False
  async def handler(session,payload):
   nonlocal peak,duplicate
   name=session.context.profile_id
   duplicate|=name in active
   active.add(name);peak=max(peak,len(active));calls[name]=calls.get(name,0)+1
   await asyncio.sleep(.001);active.remove(name)
   return {'ok':True}
  pool.registry.register('fixture',handler)
  pool.resolver.resolve=AsyncMock(side_effect=lambda name:SimpleNamespace(profile_id=name))
  class Session:
   def __init__(self,context):self.context=context
   async def __aenter__(self):return self
   async def __aexit__(self,*args):pass
  req=CreateJobRequest(profiles=[{'profile_id':str(i),'tasks':[{'action':'fixture'}]} for i in range(500)])
  started=time.monotonic();job,_=await store.create_job(req)
  with patch.object(runner,'ProfileSession',Session):
   await pool.enqueue_job(job);await pool.enqueue_job(job)
   pool._workers=[asyncio.create_task(pool._worker(i)) for i in range(pool.concurrency)]
   try:
    async with asyncio.timeout(90):
     while True:
      view=await store.job_view(job)
      if view['status']=='SUCCESS':break
      await asyncio.sleep(.1)
   finally:await pool.stop()
  assert len(calls)==500 and set(calls.values())=={1} and not duplicate
  print(json.dumps({'profiles':len(calls),'executions':sum(calls.values()),'duplicate_execution':duplicate,
    'status':view['status'],'concurrency':8,'elapsed_seconds':round(time.monotonic()-started,3),
    'network':'mocked; no Meta requests','purpose':'local scheduling regression; not a Meta throughput estimate'}))
asyncio.run(main())
