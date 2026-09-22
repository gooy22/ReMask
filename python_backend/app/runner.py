from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .mirror import MirrorError, SnapshotMirror
from .provisioning import ProvisioningError, ProvisioningService, ProvisioningStateStore
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
                                result=await self.provisioning.run(
                                    item_id=item_id,
                                    profile_id=profile_id,
                                    context=context,
                                    session=session,
                                    payload=task['payload'],
                                    task_idempotency_key=task.get('idempotency_key'),
                                )
                            else:
                                result=await self.registry.execute(action,session,task['payload'])
                            await self.store.set_task_success(task['id'],result)
                        except ProvisioningError as exc:
                            await self.store.set_task_failed(task['id'],exc.code,str(exc))
                            break
                        except ProxyCheckError as exc:
                            await self.store.set_task_failed(task['id'],'PROXY_DEAD',str(exc))
                            break
                        except RoutePolicyError as exc:
                            await self.store.set_task_failed(task['id'],'ROUTE_POLICY',str(exc))
                            break
                        except Exception as exc:
                            await self.store.set_task_failed(task['id'],'TASK_FAILED',str(exc))
                            break
            except ProfileContextError as exc:
                if tasks:
                    first=next((t for t in tasks if t['status']!='SUCCESS'),tasks[0])
                    await self.store.set_task_failed(first['id'],'PROFILE_CONTEXT_ERROR',str(exc))
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
