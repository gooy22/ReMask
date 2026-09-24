import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from app.store import JobStore
from app.provisioning.state import ProvisioningStateStore
from app.runner import _await_with_hard_watchdog
from app.provisioning.models import ProvisioningError


class JobStoreRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "jobs.sqlite3")
        self.store = JobStore(db_path)
        self.provisioning_state = ProvisioningStateStore(db_path)
        await self.store.init()
        await self.provisioning_state.init()

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def _seed(self, *, task_status: str) -> tuple[str, str]:
        now = int(time.time())
        job_id = "job-test-12345678"
        item_id = "item-test-12345678"
        task_id = "task-test-12345678"
        with self.store._connect() as con:
            con.execute(
                "INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)",
                (job_id, "RUNNING", "idem-test", now, now),
            )
            con.execute(
                "INSERT INTO job_items(id,job_id,profile_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (item_id, job_id, "4", "RUNNING", now, now),
            )
            con.execute(
                """INSERT INTO job_tasks(
                    id,item_id,position,action,payload_json,idempotency_key,status,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    item_id,
                    0,
                    "provisioning",
                    "{}",
                    "task-idem",
                    task_status,
                    now,
                    now,
                ),
            )
        return job_id, item_id

    async def test_finalize_does_not_promote_running_task_to_success(self):
        job_id, item_id = self._seed(task_status="RUNNING")

        await self.store.finalize_item(item_id)

        item = await self.store.item(item_id)
        job = await self.store.job_view(job_id)
        self.assertEqual(item["status"], "RUNNING")
        self.assertEqual(job["status"], "RUNNING")

    async def test_recover_requeues_interrupted_running_task(self):
        job_id, item_id = self._seed(task_status="RUNNING")

        await self.store.finalize_item(item_id)
        recovered = await self.store.recover()

        item = await self.store.item(item_id)
        tasks = await self.store.tasks(item_id)
        self.assertIn(item_id, recovered)
        self.assertEqual(item["status"], "QUEUED")
        self.assertEqual(tasks[0]["status"], "QUEUED")

    async def test_hard_watchdog_does_not_wait_for_slow_cancellation(self):
        async def cancellation_resistant():
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                # Simulate a Playwright coroutine that takes time to unwind
                # after cancellation. The watchdog must return before this
                # cleanup completes.
                await asyncio.sleep(0.20)
                return {"late": True}

        started = time.monotonic()
        with self.assertRaises(ProvisioningError) as ctx:
            await _await_with_hard_watchdog(
                cancellation_resistant(),
                timeout_seconds=0.02,
                code="TEST_HARD_TIMEOUT",
                message="test watchdog",
            )
        elapsed = time.monotonic() - started

        self.assertEqual(ctx.exception.code, "TEST_HARD_TIMEOUT")
        self.assertTrue(ctx.exception.retryable)
        self.assertLess(elapsed, 0.12)

        # Let the detached cancellation cleanup finish so the test loop exits
        # without leaving a pending task.
        await asyncio.sleep(0.25)

    async def test_finalize_marks_all_success_tasks_success(self):
        job_id, item_id = self._seed(task_status="SUCCESS")

        await self.store.finalize_item(item_id)

        item = await self.store.item(item_id)
        job = await self.store.job_view(job_id)
        self.assertEqual(item["status"], "SUCCESS")
        self.assertEqual(job["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
