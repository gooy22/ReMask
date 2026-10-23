"""SQLite connection overhead regression for local read-only job polling."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.store import JobStore
from app.provisioning.state import ProvisioningStateStore


class JobStoreWalConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_wal_configured_once_before_polling_and_foreign_keys_stay_enabled(self):
        journal_changes = []
        original_connect = sqlite3.connect

        class Counted(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if isinstance(sql, str) and sql.strip().upper().startswith("PRAGMA JOURNAL_MODE=WAL"):
                    journal_changes.append(sql)
                return super().execute(sql, *args, **kwargs)

        def counted_connect(*args, **kwargs):
            return original_connect(*args, factory=Counted, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            store = JobStore(str(Path(folder) / "jobs.db"))
            with patch("app.store.sqlite3.connect", side_effect=counted_connect):
                await store.init()
                self.assertEqual(len(journal_changes), 1)
                self.assertEqual(await store.queue_count(), 0)
                self.assertIsNone(await store.job_view("missing"))
                # Frequent polls open distinct connections but do not
                # renegotiate the journal mode on each hot-path read.
                results = await asyncio.gather(
                    *(store.queue_count() for _ in range(16)))
                self.assertEqual(results, [0] * 16)
                self.assertEqual(len(journal_changes), 1)
                # Foreign keys must still be configured per connection.
                with self.assertRaises(sqlite3.IntegrityError):
                    with store._connect() as connection:
                        connection.execute(
                            "INSERT INTO job_items "
                            "(id, job_id, profile_id, status, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            ("unlinked", "missing-parent", "fixture", "QUEUED", 1, 1),
                        )
            with original_connect(store.path) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal",
                )


    async def test_provisioning_state_avoids_repeated_wal_mode_on_shared_db(self):
        changes = []
        actual_connect = sqlite3.connect

        class Counted(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if isinstance(sql, str) and sql.strip().upper().startswith("PRAGMA JOURNAL_MODE=WAL"):
                    changes.append(sql)
                return super().execute(sql, *args, **kwargs)

        def instrumented(*args, **kwargs):
            return actual_connect(*args, factory=Counted, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "shared-worker.db")
            # Both stores coexist in production; they may each initialize
            # WAL once but must not do it on every 1-second UI state poll.
            job_store = JobStore(path)
            state_store = ProvisioningStateStore(path)
            with patch("app.provisioning.state.sqlite3.connect", side_effect=instrumented):
                await job_store.init()
                await state_store.init()
                self.assertEqual(len(changes), 2)
                async def read_state():
                    def query():
                        with state_store._connect() as connection:
                            self.assertEqual(connection.execute(
                                "PRAGMA foreign_keys").fetchone()[0], 1)
                            return connection.execute("SELECT 1").fetchone()[0]
                    return await asyncio.to_thread(query)
                self.assertEqual(await asyncio.gather(
                    *(read_state() for _ in range(12))), [1]*12)
                self.assertEqual(len(changes), 2)
            with actual_connect(path) as connection:
                self.assertEqual(
                    connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                    "wal")


if __name__ == "__main__":
    unittest.main()
