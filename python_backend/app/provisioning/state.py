from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import ENTITY_RESULT_KEYS, ProvisioningSnapshot, ProvisioningStep


def _now() -> int:
    return int(time.time())


class ProvisioningStateStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS provisioning_entities (
                    profile_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    business_id TEXT,
                    ad_account_id TEXT,
                    funding_source_id TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(profile_id, scope_key)
                );

                CREATE TABLE IF NOT EXISTS provisioning_steps (
                    item_id TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    step TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    result_json TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(item_id, step)
                );

                CREATE INDEX IF NOT EXISTS idx_provisioning_steps_profile
                    ON provisioning_steps(profile_id, scope_key, updated_at);
                """
            )
            con.execute(
                "UPDATE provisioning_steps SET status='QUEUED',updated_at=? WHERE status='RUNNING'",
                (_now(),),
            )

    async def snapshot(self, profile_id: str, scope_key: str) -> ProvisioningSnapshot:
        return await asyncio.to_thread(self._snapshot_sync, profile_id, scope_key)

    def _snapshot_sync(self, profile_id: str, scope_key: str) -> ProvisioningSnapshot:
        with self._connect() as con:
            row = con.execute(
                """
                SELECT profile_id,scope_key,business_id,ad_account_id,funding_source_id
                FROM provisioning_entities
                WHERE profile_id=? AND scope_key=?
                """,
                (profile_id, scope_key),
            ).fetchone()
            if not row:
                return ProvisioningSnapshot(profile_id=profile_id, scope_key=scope_key)
            return ProvisioningSnapshot(
                profile_id=str(row["profile_id"]),
                scope_key=str(row["scope_key"]),
                business_id=row["business_id"],
                ad_account_id=row["ad_account_id"],
                funding_source_id=row["funding_source_id"],
            )

    async def step(self, item_id: str, step: ProvisioningStep) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._step_sync, item_id, step)

    def _step_sync(self, item_id: str, step: ProvisioningStep) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM provisioning_steps WHERE item_id=? AND step=?",
                (item_id, step.value),
            ).fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("result_json"):
                data["result"] = json.loads(data["result_json"])
            data.pop("result_json", None)
            return data

    async def set_running(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
    ) -> None:
        await asyncio.to_thread(self._set_running_sync, item_id, profile_id, scope_key, step)

    def _set_running_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
    ) -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='RUNNING',
                    attempt=provisioning_steps.attempt+1,
                    error_code=NULL,
                    error_message=NULL,
                    updated_at=excluded.updated_at
                """,
                (item_id, profile_id, scope_key, step.value, "RUNNING", now, now),
            )

    async def checkpoint(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Atomically merge a partial step result without marking the step SUCCESS.

        This is used for resumable multi-phase operations such as BUSINESS:
        CREATE may have succeeded while ATTACH_PAGE is still pending. The
        checkpoint is persisted inside provisioning_steps.result_json only;
        provisioning_entities is deliberately untouched until complete().
        """
        if not isinstance(patch, dict):
            raise TypeError("checkpoint patch must be a dict")

        return await asyncio.to_thread(
            self._checkpoint_sync,
            item_id,
            profile_id,
            scope_key,
            step,
            patch,
        )

    def _checkpoint_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")

            row = con.execute(
                """
                SELECT profile_id,scope_key,status,result_json
                FROM provisioning_steps
                WHERE item_id=? AND step=?
                """,
                (item_id, step.value),
            ).fetchone()

            if row is None:
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: step row does not exist for item {item_id}"
                )

            stored_profile_id = str(row["profile_id"] or "")
            stored_scope_key = str(row["scope_key"] or "")

            if stored_profile_id != str(profile_id):
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: profile mismatch "
                    f"{stored_profile_id!r} != {profile_id!r}"
                )

            if stored_scope_key != str(scope_key):
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: scope mismatch "
                    f"{stored_scope_key!r} != {scope_key!r}"
                )

            current: dict[str, Any] = {}
            raw_result = row["result_json"]

            if raw_result:
                try:
                    decoded = json.loads(str(raw_result))
                except (json.JSONDecodeError, ValueError) as exc:
                    con.rollback()
                    raise RuntimeError(
                        f"cannot checkpoint {step.value}: stored result_json is invalid"
                    ) from exc

                if not isinstance(decoded, dict):
                    con.rollback()
                    raise RuntimeError(
                        f"cannot checkpoint {step.value}: stored result_json is not an object"
                    )

                current.update(decoded)

            current.update(patch)

            result_json = json.dumps(
                current,
                separators=(",", ":"),
                ensure_ascii=False,
            )

            cursor = con.execute(
                """
                UPDATE provisioning_steps
                SET result_json=?,updated_at=?
                WHERE item_id=? AND step=?
                """,
                (
                    result_json,
                    now,
                    item_id,
                    step.value,
                ),
            )

            if cursor.rowcount != 1:
                con.rollback()
                raise RuntimeError(
                    f"cannot checkpoint {step.value}: update affected {cursor.rowcount} rows"
                )

            con.commit()
            return current

    async def complete(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        result: dict[str, Any],
    ) -> None:
        await asyncio.to_thread(
            self._complete_sync, item_id, profile_id, scope_key, step, result
        )

    def _complete_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        result: dict[str, Any],
    ) -> None:
        now = _now()
        result_json = json.dumps(result, separators=(",", ":"), ensure_ascii=False)
        entity_key = ENTITY_RESULT_KEYS.get(step)
        entity_value = str(result.get(entity_key) or "").strip() if entity_key else ""

        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,result_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='SUCCESS',
                    result_json=excluded.result_json,
                    error_code=NULL,
                    error_message=NULL,
                    updated_at=excluded.updated_at
                """,
                (item_id, profile_id, scope_key, step.value, "SUCCESS", result_json, now, now),
            )

            if entity_key and entity_value:
                con.execute(
                    """
                    INSERT INTO provisioning_entities(
                        profile_id,scope_key,business_id,ad_account_id,funding_source_id,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(profile_id,scope_key) DO NOTHING
                    """,
                    (profile_id, scope_key, None, None, None, now, now),
                )
                column = {
                    "business_id": "business_id",
                    "ad_account_id": "ad_account_id",
                    "funding_source_id": "funding_source_id",
                }[entity_key]
                con.execute(
                    f"UPDATE provisioning_entities SET {column}=?,updated_at=? "
                    "WHERE profile_id=? AND scope_key=?",
                    (entity_value, now, profile_id, scope_key),
                )
            con.commit()

    async def fail(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        code: str,
        message: str,
    ) -> None:
        await asyncio.to_thread(
            self._fail_sync, item_id, profile_id, scope_key, step, code, message
        )

    def _fail_sync(
        self,
        item_id: str,
        profile_id: str,
        scope_key: str,
        step: ProvisioningStep,
        code: str,
        message: str,
    ) -> None:
        now = _now()
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO provisioning_steps(
                    item_id,profile_id,scope_key,step,status,attempt,error_code,error_message,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,1,?,?,?,?)
                ON CONFLICT(item_id,step) DO UPDATE SET
                    status='FAILED',
                    error_code=excluded.error_code,
                    error_message=excluded.error_message,
                    updated_at=excluded.updated_at
                """,
                (
                    item_id,
                    profile_id,
                    scope_key,
                    step.value,
                    "FAILED",
                    code,
                    message[:1000],
                    now,
                    now,
                ),
            )
