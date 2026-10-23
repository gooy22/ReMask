"""SQLite connection overhead regression for local read-only job polling."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.store import JobStore


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


if __name__ == "__main__":
    unittest.main()
