"""Bounded-memory scheduling from durable SQLite; active profiles never consume waiting slots."""
from __future__ import annotations
import asyncio
import fcntl
from pathlib import Path

class WorkerInstanceLease:
    """Embedded SQLite worker is deliberately single-owner, including recovery."""
    def __init__(self, path):
        self.path = Path(str(path) + '.worker.lock')
        self.handle = None
    def acquire(self):
        if self.handle is not None:
            raise RuntimeError('WORKER_ALREADY_STARTED')
        handle = self.path.open('a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            handle.close()
            raise RuntimeError('WORKER_STORE_ALREADY_OWNED') from None
        self.handle = handle
    def release(self):
        if self.handle is not None:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None

class ProfileWorkQueue:
    def __init__(self, store):
        self.store = store
        self.condition = asyncio.Condition()
        self.active = {}  # at most one profile per active worker
        self.last_profile = ''
    async def put(self, item_id):
        # The item is already durable. No duplicate, unbounded in-memory ID list.
        async with self.condition:
            self.condition.notify_all()
    async def get(self):
        async with self.condition:
            while True:
                row = await self.store.next_queued_item(set(self.active.values()), self.last_profile)
                if row:
                    profile = str(row['profile_id'])
                    self.active[asyncio.current_task()] = profile
                    self.last_profile = profile
                    return str(row['id'])
                try:
                    await asyncio.wait_for(self.condition.wait(), timeout=0.5)
                except TimeoutError:
                    pass  # Also discover committed work if its HTTP caller was interrupted.
    def task_done(self):
        self.active.pop(asyncio.current_task(), None)
        # Every get polls the durable queue; wake peers immediately as well.
        async def notify():
            async with self.condition:
                self.condition.notify_all()
        asyncio.create_task(notify())
