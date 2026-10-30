from __future__ import annotations

import asyncio
import os
import shutil
import json
import hashlib
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .facebook_ad_account_identity import details_candidate

def _now() -> int:
    return int(time.time())

def _interleave_profile_items(rows: list[Any]) -> list[str]:
    """Keep a bulk profile from filling every worker before other profiles."""
    groups: dict[str, deque[str]] = {}
    for row in rows:
        groups.setdefault(str(row['profile_id']), deque()).append(str(row['id']))
    ordered = []
    while groups:
        for profile_id, pending in list(groups.items()):
            ordered.append(pending.popleft())
            if not pending:
                del groups[profile_id]
    return ordered


def _profiles_serialized_bytes(profiles: list[Any]) -> int:
    # Use the same structural interface as create_job, including expanded auto-plan rows.
    total = 0
    for profile in profiles:
        tasks = [{'action':task.action, 'payload':task.payload, 'idempotency_key':task.idempotency_key,
            **task.model_dump(exclude={'action','payload','idempotency_key'}, exclude_none=True)} for task in profile.tasks]
        total += len(json.dumps({'profile_id':profile.profile_id,'tasks':tasks}, ensure_ascii=False).encode('utf-8'))
    return total

class JobStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        try:
            con.row_factory = sqlite3.Row
            # WAL is a persistent per-database setting. Repeating the pragma
            # on every short-lived connection adds unnecessary journal checks
            # to hot-path job polling and queue reads.
            con.execute("PRAGMA foreign_keys=ON")
            with con:
                yield con
        finally:
            con.close()

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
            # A single startup migration sets the durable journal mode before
            # concurrent worker and polling connections begin.
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript('''
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                idempotency_key TEXT UNIQUE,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_items (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                profile_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                error_message TEXT,
                retryable INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS job_tasks (
                id TEXT PRIMARY KEY,
                item_id TEXT NOT NULL REFERENCES job_items(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                action TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                idempotency_key TEXT,
                status TEXT NOT NULL,
                attempt INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error_code TEXT,
                error_message TEXT,
                retryable INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(item_id, position)
            );
            CREATE TABLE IF NOT EXISTS profile_terminal_statuses (
                profile_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                checked_at INTEGER NOT NULL,
                message TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_items_status ON job_items(status);
            CREATE INDEX IF NOT EXISTS idx_items_job_created ON job_items(job_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_item ON job_tasks(item_id, position);

            ''')

            # Forward-compatible migration for databases created before
            # retryability became first-class state.
            for table in ("job_items", "job_tasks"):
                columns = {
                    str(row["name"])
                    for row in con.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if "retryable" not in columns:
                    con.execute(
                        f"ALTER TABLE {table} "
                        "ADD COLUMN retryable INTEGER NOT NULL DEFAULT 0"
                    )

            # Older jobs allowed only one item per profile. Bulk units have
            # independent checkpoints but keep the real profile identity.
            unique_profile_index = any(
                row["unique"] and [col["name"] for col in con.execute(
                    "SELECT * FROM pragma_index_info(?)", (row["name"],)
                )] == ["job_id", "profile_id"]
                for row in con.execute("PRAGMA index_list(job_items)").fetchall()
            )
            if unique_profile_index:
                schema = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='job_items'").fetchone()["sql"]
                new_schema = re.sub(r",\s*UNIQUE\s*\(\s*job_id\s*,\s*profile_id\s*\)", "", schema, flags=re.I)
                new_schema = re.sub(r"^(CREATE TABLE(?: IF NOT EXISTS)?)\s+(?:\"job_items\"|job_items)", r"\1 job_items_bulk", new_schema, count=1, flags=re.I)
                if new_schema == schema or "UNIQUE(job_id, profile_id)" in new_schema:
                    raise RuntimeError("Could not migrate bulk job items schema")
                con.commit()
                con.execute("PRAGMA foreign_keys=OFF")
                try:
                    con.execute("BEGIN IMMEDIATE")
                    con.execute(new_schema)
                    con.execute("INSERT INTO job_items_bulk SELECT * FROM job_items")
                    con.execute("DROP TABLE job_items")
                    con.execute("ALTER TABLE job_items_bulk RENAME TO job_items")
                    con.execute("CREATE INDEX idx_items_status ON job_items(status)")
                    if con.execute("PRAGMA foreign_key_check").fetchall():
                        raise RuntimeError("Bulk job migration broke a foreign key")
                    con.commit()
                except BaseException:
                    con.rollback()
                    raise
                finally:
                    con.execute("PRAGMA foreign_keys=ON")
            # The legacy bulk-item migration rebuilds job_items. Install these
            # triggers only after that migration so SQLite never carries a
            # trigger bound to the table being renamed.
            con.executescript(
                """
            CREATE TRIGGER IF NOT EXISTS capture_item_checkpoint_insert
            AFTER INSERT ON job_items
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_item_checkpoint_update
            AFTER UPDATE OF error_code,error_message ON job_items
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_checkpoint_insert
            AFTER INSERT ON job_tasks
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_checkpoint_update
            AFTER UPDATE OF error_code,error_message ON job_tasks
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_item_checkpoint_status_insert
            AFTER INSERT ON job_items
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                status='CHECKPOINT',
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_item_checkpoint_status_update
            AFTER UPDATE OF error_code,error_message ON job_items
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                status='CHECKPOINT',
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_checkpoint_status_insert
            AFTER INSERT ON job_tasks
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                status='CHECKPOINT',
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_checkpoint_status_update
            AFTER UPDATE OF error_code,error_message ON job_tasks
            WHEN UPPER(COALESCE(NEW.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_required%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%checkpoint_redirect%'
              OR LOWER(COALESCE(NEW.error_message,'')) LIKE '%/checkpoint/%'
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'CHECKPOINT',NEW.updated_at,
                     'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                status='CHECKPOINT',
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=excluded.message;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_item_business_login_insert
            AFTER INSERT ON job_items
            WHEN INSTR(UPPER(COALESCE(NEW.error_code,'') || ' ' || COALESCE(NEW.error_message,'')),
                       'BUSINESS_LOGIN_GATE') > 0
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'BUSINESS_LOGIN_GATE',NEW.updated_at,
                     'Meta остановила доступ. Новые действия запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                status=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                            THEN 'CHECKPOINT' ELSE 'BUSINESS_LOGIN_GATE' END,
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                             THEN profile_terminal_statuses.message ELSE excluded.message END;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_item_business_login_update
            AFTER UPDATE OF error_code,error_message ON job_items
            WHEN INSTR(UPPER(COALESCE(NEW.error_code,'') || ' ' || COALESCE(NEW.error_message,'')),
                       'BUSINESS_LOGIN_GATE') > 0
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              VALUES(NEW.profile_id,'BUSINESS_LOGIN_GATE',NEW.updated_at,
                     'Meta остановила доступ. Новые действия запрещены.')
              ON CONFLICT(profile_id) DO UPDATE SET
                status=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                            THEN 'CHECKPOINT' ELSE 'BUSINESS_LOGIN_GATE' END,
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                             THEN profile_terminal_statuses.message ELSE excluded.message END;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_business_login_insert
            AFTER INSERT ON job_tasks
            WHEN INSTR(UPPER(COALESCE(NEW.error_code,'') || ' ' || COALESCE(NEW.error_message,'')),
                       'BUSINESS_LOGIN_GATE') > 0
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'BUSINESS_LOGIN_GATE',NEW.updated_at,
                     'Meta остановила доступ. Новые действия запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                status=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                            THEN 'CHECKPOINT' ELSE 'BUSINESS_LOGIN_GATE' END,
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                             THEN profile_terminal_statuses.message ELSE excluded.message END;
            END;

            CREATE TRIGGER IF NOT EXISTS capture_task_business_login_update
            AFTER UPDATE OF error_code,error_message ON job_tasks
            WHEN INSTR(UPPER(COALESCE(NEW.error_code,'') || ' ' || COALESCE(NEW.error_message,'')),
                       'BUSINESS_LOGIN_GATE') > 0
            BEGIN
              INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
              SELECT i.profile_id,'BUSINESS_LOGIN_GATE',NEW.updated_at,
                     'Meta остановила доступ. Новые действия запрещены.'
              FROM job_items i WHERE i.id=NEW.item_id
              ON CONFLICT(profile_id) DO UPDATE SET
                status=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                            THEN 'CHECKPOINT' ELSE 'BUSINESS_LOGIN_GATE' END,
                checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                message=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                             THEN profile_terminal_statuses.message ELSE excluded.message END;
            END;

            INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
            SELECT i.profile_id,'CHECKPOINT',MAX(i.updated_at),
                   'Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены.'
            FROM job_items i
            LEFT JOIN job_tasks t ON t.item_id=i.id AND UPPER(COALESCE(t.status,''))='FAILED'
            WHERE UPPER(COALESCE(i.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
               OR LOWER(COALESCE(i.error_message,'')) LIKE '%checkpoint_required%'
               OR LOWER(COALESCE(i.error_message,'')) LIKE '%checkpoint_redirect%'
               OR LOWER(COALESCE(i.error_message,'')) LIKE '%/checkpoint/%'
               OR UPPER(COALESCE(t.error_code,'')) LIKE '%CHECKPOINT_REQUIRED'
               OR LOWER(COALESCE(t.error_message,'')) LIKE '%checkpoint_required%'
               OR LOWER(COALESCE(t.error_message,'')) LIKE '%checkpoint_redirect%'
               OR LOWER(COALESCE(t.error_message,'')) LIKE '%/checkpoint/%'
            GROUP BY i.profile_id
            ON CONFLICT(profile_id) DO UPDATE SET
              status='CHECKPOINT',
              checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
              message=excluded.message;
            INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
            SELECT i.profile_id,'BUSINESS_LOGIN_GATE',MAX(i.updated_at),
                   'Meta остановила доступ. Новые действия запрещены.'
            FROM job_items i
            LEFT JOIN job_tasks t ON t.item_id=i.id AND UPPER(COALESCE(t.status,''))='FAILED'
            WHERE INSTR(UPPER(COALESCE(i.error_code,'') || ' ' || COALESCE(i.error_message,'')),
                        'BUSINESS_LOGIN_GATE') > 0
               OR INSTR(UPPER(COALESCE(t.error_code,'') || ' ' || COALESCE(t.error_message,'')),
                        'BUSINESS_LOGIN_GATE') > 0
            GROUP BY i.profile_id
            ON CONFLICT(profile_id) DO UPDATE SET
              status=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                          THEN 'CHECKPOINT' ELSE 'BUSINESS_LOGIN_GATE' END,
              checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
              message=CASE WHEN profile_terminal_statuses.status='CHECKPOINT'
                           THEN profile_terminal_statuses.message ELSE excluded.message END;
                """
            )
            self._recover_legacy_fp_page_crashes(con)
            self._recover_common_page_checkpoint_failure(con)
            self._repair_unstarted_prgssteam_rk_timezone(con)
            self._recover_premature_page_access(con)
            self._recover_common_page_picker_failure(con)
            self._recover_legacy_owner_page_approval_stub(con)
            self._recover_owner_page_actor_probe(con)
            self._recover_owner_active_partner_reconciliation(con)
            self._recover_owner_confirmed_operator_assignment(con)

    @staticmethod
    def _recover_legacy_owner_page_approval_stub(con: sqlite3.Connection) -> None:
        """Auto-resume only the pre-owner-approval stub shipped before bd030728.

        The old handler had already submitted the exact Page/BM access request,
        then deliberately stopped with PAGE_OWNER_APPROVAL_UI_UNAVAILABLE.
        Requeueing is safe only when durable BUSINESS and AD_ACCOUNT successes
        match the PAGE_ACCESS checkpoint. New owner-approval failures are left
        untouched so a deploy can never create an automatic retry loop.
        """
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'"
        ).fetchone():
            return

        rows=con.execute(
            """SELECT t.id AS task_id,t.item_id,i.job_id,t.error_message,
                      p.result_json AS page_result,
                      b.result_json AS business_result,
                      a.result_json AS ad_account_result
               FROM job_tasks t
               JOIN job_items i ON i.id=t.item_id
               JOIN provisioning_steps p
                 ON p.item_id=t.item_id AND p.step='PAGE_ACCESS' AND p.status='FAILED'
               JOIN provisioning_steps b
                 ON b.item_id=t.item_id AND b.step='BUSINESS' AND b.status='SUCCESS'
               JOIN provisioning_steps a
                 ON a.item_id=t.item_id AND a.step='AD_ACCOUNT' AND a.status='SUCCESS'
               WHERE t.status='FAILED'
                 AND i.status='FAILED'
                 AND t.action='provisioning'
                 AND t.error_code='PAGE_OWNER_APPROVAL_UI_UNAVAILABLE'
                 AND t.error_message LIKE ?
            """,
            ('%The existing request needs the Page owner approval surface%',),
        ).fetchall()

        now=_now()
        for row in rows:
            try:
                page=json.loads(row['page_result'] or '{}')
                business=json.loads(row['business_result'] or '{}')
                account=json.loads(row['ad_account_result'] or '{}')
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
            if not all(isinstance(value,dict) for value in (page,business,account)):
                continue

            # The old stub was reached only after the exact request was sent.
            # Do not auto-resume a pre-submit or uncertain checkpoint.
            if str(page.get('phase') or '')!='TARGET_PAGE_ACCESS_SUBMITTED':
                continue

            page_id=str(page.get('page_id') or '').strip()
            business_id=str(page.get('business_id') or '').strip()
            ad_account_id=str(page.get('ad_account_id') or '').strip()
            if ad_account_id.startswith('act_'):
                ad_account_id=ad_account_id[4:]

            bm_id=str(business.get('business_id') or '').strip()
            rk_business=str(account.get('business_id') or '').strip()
            rk_id=str(account.get('ad_account_id') or '').strip()
            if rk_id.startswith('act_'):
                rk_id=rk_id[4:]

            if not (
                page_id.isdigit()
                and business_id.isdigit()
                and ad_account_id.isdigit()
                and bm_id==business_id
                and rk_business==business_id
                and rk_id==ad_account_id
            ):
                continue

            # Requeue only the failed provisioning task/item. ProvisioningService
            # will skip the already-SUCCESS BUSINESS and AD_ACCOUNT steps, while
            # PAGE_ACCESS resumes from TARGET_PAGE_ACCESS_SUBMITTED and therefore
            # cannot send a second Page request.
            con.execute(
                """UPDATE job_tasks
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['task_id']),
            )
            con.execute(
                """UPDATE job_items
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['item_id']),
            )
            con.execute(
                "UPDATE jobs SET status='QUEUED',updated_at=? WHERE id=?",
                (now,row['job_id']),
            )

    @staticmethod
    def _recover_owner_page_actor_probe(con: sqlite3.Connection) -> None:
        """Retry one proven submitted owner-approval failure exactly once.

        This is a diagnostic continuation, not a new Page request. The durable
        marker survives another failure so restarts cannot create a retry loop.
        """
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'"
        ).fetchone():
            return

        rows=con.execute(
            """SELECT t.id AS task_id,t.item_id,i.job_id,
                      p.result_json AS page_result,
                      b.result_json AS business_result,
                      a.result_json AS ad_account_result
               FROM job_tasks t
               JOIN job_items i ON i.id=t.item_id
               JOIN provisioning_steps p
                 ON p.item_id=t.item_id AND p.step='PAGE_ACCESS' AND p.status='FAILED'
               JOIN provisioning_steps b
                 ON b.item_id=t.item_id AND b.step='BUSINESS' AND b.status='SUCCESS'
               JOIN provisioning_steps a
                 ON a.item_id=t.item_id AND a.step='AD_ACCOUNT' AND a.status='SUCCESS'
               WHERE t.status='FAILED'
                 AND i.status='FAILED'
                 AND t.action='provisioning'
                 AND t.error_code='PAGE_OWNER_APPROVAL_UI_UNAVAILABLE'
                 AND t.error_message LIKE ?
            """,
            ('%exact pending Page access request is not visible%',),
        ).fetchall()

        now=_now()
        for row in rows:
            try:
                page=json.loads(row['page_result'] or '{}')
                business=json.loads(row['business_result'] or '{}')
                account=json.loads(row['ad_account_result'] or '{}')
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
            if not all(isinstance(value,dict) for value in (page,business,account)):
                continue
            if page.get('owner_actor_live_discovery_retry') is True:
                continue
            if str(page.get('phase') or '')!='TARGET_PAGE_ACCESS_SUBMITTED':
                continue

            page_id=str(page.get('page_id') or '').strip()
            business_id=str(page.get('business_id') or '').strip()
            ad_account_id=str(page.get('ad_account_id') or '').strip().removeprefix('act_')
            bm_id=str(business.get('business_id') or '').strip()
            rk_business=str(account.get('business_id') or '').strip()
            rk_id=str(account.get('ad_account_id') or '').strip().removeprefix('act_')
            if not (
                page_id.isdigit()
                and business_id.isdigit()
                and ad_account_id.isdigit()
                and bm_id==business_id
                and rk_business==business_id
                and rk_id==ad_account_id
            ):
                continue

            if page.get('owner_actor_probe_retry') is True:
                page['owner_actor_live_discovery_retry']=True
                page['owner_actor_live_discovery_retry_at']=now
            else:
                page['owner_actor_probe_retry']=True
                page['owner_actor_probe_retry_at']=now
            con.execute(
                """UPDATE provisioning_steps
                   SET result_json=?,updated_at=?
                   WHERE item_id=? AND step='PAGE_ACCESS'""",
                (json.dumps(page,separators=(',',':'),ensure_ascii=False),
                 now,row['item_id']),
            )
            con.execute(
                """UPDATE job_tasks
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['task_id']),
            )
            con.execute(
                """UPDATE job_items
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['item_id']),
            )
            con.execute(
                "UPDATE jobs SET status='QUEUED',updated_at=? WHERE id=?",
                (now,row['job_id']),
            )

    @staticmethod
    def _recover_owner_active_partner_reconciliation(con: sqlite3.Connection) -> None:
        """Retry once after live owner discovery exposed an already-active Page partner.

        This continuation is allowed only from a durable
        TARGET_PAGE_ACCESS_SUBMITTED checkpoint whose BUSINESS and AD_ACCOUNT
        successes match the exact Page-access target. The request must never be
        re-sent: page_access_handler resumes reconciliation from the saved grant.
        """
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'"
        ).fetchone():
            return

        rows=con.execute(
            """SELECT t.id AS task_id,t.item_id,i.job_id,
                      p.result_json AS page_result,
                      b.result_json AS business_result,
                      a.result_json AS ad_account_result
               FROM job_tasks t
               JOIN job_items i ON i.id=t.item_id
               JOIN provisioning_steps p
                 ON p.item_id=t.item_id AND p.step='PAGE_ACCESS' AND p.status='FAILED'
               JOIN provisioning_steps b
                 ON b.item_id=t.item_id AND b.step='BUSINESS' AND b.status='SUCCESS'
               JOIN provisioning_steps a
                 ON a.item_id=t.item_id AND a.step='AD_ACCOUNT' AND a.status='SUCCESS'
               WHERE t.status='FAILED'
                 AND i.status='FAILED'
                 AND t.action='provisioning'
                 AND t.error_code='PAGE_OWNER_APPROVAL_UI_UNAVAILABLE'
                 AND t.error_message LIKE ?
            """,
            ('%exact pending Page access request is not visible%',),
        ).fetchall()

        now=_now()
        for row in rows:
            try:
                page=json.loads(row['page_result'] or '{}')
                business=json.loads(row['business_result'] or '{}')
                account=json.loads(row['ad_account_result'] or '{}')
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
            if not all(isinstance(value,dict) for value in (page,business,account)):
                continue
            if page.get('owner_actor_live_discovery_retry') is not True:
                continue
            if str(page.get('phase') or '')!='TARGET_PAGE_ACCESS_SUBMITTED':
                continue

            retry_marker=''
            if page.get('owner_active_partner_reconciliation_retry') is not True:
                retry_marker='owner_active_partner_reconciliation_retry'
            elif page.get('owner_access_surface_wait_retry') is not True:
                # One final continuation is safe after the active-partner pass:
                # the exact request is already durably SUBMITTED and the
                # BUSINESS/AD_ACCOUNT checkpoints below must match it. The
                # handler therefore reconciles the existing relation and can
                # never send a second Page request.
                retry_marker='owner_access_surface_wait_retry'
            else:
                continue

            page_id=str(page.get('page_id') or '').strip()
            business_id=str(page.get('business_id') or '').strip()
            ad_account_id=str(page.get('ad_account_id') or '').strip().removeprefix('act_')
            bm_id=str(business.get('business_id') or '').strip()
            rk_business=str(account.get('business_id') or '').strip()
            rk_id=str(account.get('ad_account_id') or '').strip().removeprefix('act_')
            if not (
                page_id.isdigit()
                and business_id.isdigit()
                and ad_account_id.isdigit()
                and bm_id==business_id
                and rk_business==business_id
                and rk_id==ad_account_id
            ):
                continue

            page[retry_marker]=True
            page[retry_marker+'_at']=now
            con.execute(
                """UPDATE provisioning_steps
                   SET result_json=?,updated_at=?
                   WHERE item_id=? AND step='PAGE_ACCESS'""",
                (json.dumps(page,separators=(',',':'),ensure_ascii=False),
                 now,row['item_id']),
            )
            con.execute(
                """UPDATE job_tasks
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['task_id']),
            )
            con.execute(
                """UPDATE job_items
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['item_id']),
            )
            con.execute(
                "UPDATE jobs SET status='QUEUED',updated_at=? WHERE id=?",
                (now,row['job_id']),
            )

    @staticmethod
    def _recover_owner_confirmed_operator_assignment(con: sqlite3.Connection) -> None:
        """Retry one operator-assignment UI failure after owner relation is proven.

        The Page request is already OWNER_CONFIRMED, and exact BUSINESS/RK
        success checkpoints must match. This retry can only continue assignment;
        it cannot submit another Page-access request.
        """
        if not con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'"
        ).fetchone():
            return

        rows=con.execute(
            """SELECT t.id AS task_id,t.item_id,i.job_id,t.error_code,
                      p.result_json AS page_result,
                      b.result_json AS business_result,
                      a.result_json AS ad_account_result
               FROM job_tasks t
               JOIN job_items i ON i.id=t.item_id
               JOIN provisioning_steps p
                 ON p.item_id=t.item_id AND p.step='PAGE_ACCESS' AND p.status='FAILED'
               JOIN provisioning_steps b
                 ON b.item_id=t.item_id AND b.step='BUSINESS' AND b.status='SUCCESS'
               JOIN provisioning_steps a
                 ON a.item_id=t.item_id AND a.step='AD_ACCOUNT' AND a.status='SUCCESS'
               WHERE t.status='FAILED'
                 AND i.status='FAILED'
                 AND t.action='provisioning'
                 AND t.error_code IN (
                   'PAGE_SHARE_UI_UNAVAILABLE',
                   'PAGE_OPERATOR_PAGE_SELECTION_UNAVAILABLE',
                   'PAGE_OPERATOR_ASSIGNMENT_REQUIRED',
                   'FACEBOOK_NAVIGATION_FAILED'
                 )
            """
        ).fetchall()

        now=_now()
        for row in rows:
            try:
                page=json.loads(row['page_result'] or '{}')
                business=json.loads(row['business_result'] or '{}')
                account=json.loads(row['ad_account_result'] or '{}')
            except (TypeError,ValueError,json.JSONDecodeError):
                continue
            if not all(isinstance(value,dict) for value in (page,business,account)):
                continue
            if str(page.get('phase') or '')!='TARGET_PAGE_ACCESS_OWNER_CONFIRMED':
                continue

            retry_marker=''
            if page.get('owner_confirmed_operator_assignment_retry') is not True:
                retry_marker='owner_confirmed_operator_assignment_retry'
            elif (
                str(row['error_code'] or '')=='FACEBOOK_NAVIGATION_FAILED'
                and page.get('operator_preconfirmed_navigation_retry') is not True
            ):
                # Legacy operator flow re-navigated verify_page_attached even
                # after OWNER_CONFIRMED and could fail before Page selection.
                retry_marker='operator_preconfirmed_navigation_retry'
            elif (
                str(row['error_code'] or '')=='FACEBOOK_NAVIGATION_FAILED'
                and page.get('operator_owner_confirmed_short_circuit_retry') is not True
            ):
                # A second legacy navigation remained in
                # _request_target_page_access before the OWNER_CONFIRMED phase
                # check. New code short-circuits the durable owner proof before
                # any relation navigation. Allow exactly one continuation.
                retry_marker='operator_owner_confirmed_short_circuit_retry'
            else:
                continue

            page_id=str(page.get('page_id') or '').strip()
            business_id=str(page.get('business_id') or '').strip()
            ad_account_id=str(page.get('ad_account_id') or '').strip().removeprefix('act_')
            bm_id=str(business.get('business_id') or '').strip()
            rk_business=str(account.get('business_id') or '').strip()
            rk_id=str(account.get('ad_account_id') or '').strip().removeprefix('act_')
            if not (
                page_id.isdigit()
                and business_id.isdigit()
                and ad_account_id.isdigit()
                and bm_id==business_id
                and rk_business==business_id
                and rk_id==ad_account_id
            ):
                continue

            page[retry_marker]=True
            page[retry_marker+'_at']=now
            con.execute(
                """UPDATE provisioning_steps
                   SET result_json=?,updated_at=?
                   WHERE item_id=? AND step='PAGE_ACCESS'""",
                (json.dumps(page,separators=(',',':'),ensure_ascii=False),
                 now,row['item_id']),
            )
            con.execute(
                """UPDATE job_tasks
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['task_id']),
            )
            con.execute(
                """UPDATE job_items
                   SET status='QUEUED',error_code=NULL,error_message=NULL,
                       retryable=0,updated_at=?
                   WHERE id=? AND status='FAILED'""",
                (now,row['item_id']),
            )
            con.execute(
                "UPDATE jobs SET status='QUEUED',updated_at=? WHERE id=?",
                (now,row['job_id']),
            )

    @staticmethod
    def _recover_common_page_picker_failure(con: sqlite3.Connection) -> None:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'").fetchone(): return
        rows=con.execute("SELECT t.id,t.item_id,t.payload_json,s.result_json FROM job_tasks t "
            "JOIN provisioning_steps s ON s.item_id=t.item_id AND s.step='PAGE_ACCESS' AND s.status='FAILED' "
            "WHERE t.status='FAILED' AND t.action='provisioning' AND t.error_code='PAGE_ADD_UI_CHANGED' "
            "AND t.error_message='Meta Page-add review/submit action was not found.'").fetchall()
        for row in rows:
            payload=json.loads(row['payload_json']); result=json.loads(row['result_json'] or '{}')
            diagnostic=result.get('diagnostic') or {}
            if (payload.get('parameters',{}).get('FAN_PAGES') or {}).get('common_page') is not True: continue
            if diagnostic.get('stage')!='page_add_submit_missing' or diagnostic.get('result_selected') is not False or result.get('phase') not in {None,'','PAGE_ADD_NOT_SUBMITTED'}: continue
            con.execute('UPDATE job_tasks SET retryable=1 WHERE id=?',(row['id'],))
            con.execute("UPDATE job_items SET retryable=1 WHERE id=? AND status='FAILED' AND error_code='PAGE_ADD_UI_CHANGED'",(row['item_id'],))

    @staticmethod
    def _recover_premature_page_access(con: sqlite3.Connection) -> None:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'").fetchone(): return
        rows=con.execute("SELECT t.id,t.item_id,t.payload_json FROM job_tasks t WHERE t.status='FAILED' "
            "AND t.action='provisioning' AND t.error_code='CREATED_BUSINESS_RK_REQUIRED' "
            "AND NOT EXISTS (SELECT 1 FROM provisioning_steps s WHERE s.item_id=t.item_id AND s.step='AD_ACCOUNT')").fetchall()
        for row in rows:
            payload=json.loads(row['payload_json']); params=payload.get('parameters') or {}
            steps=payload.get('steps') or []
            if 'AD_ACCOUNT' not in steps or (params.get('AD_ACCOUNT') or {}).get('use_common_page') is not True: continue
            # Only restore manual retry before RK creation ever started. Retain
            # every Page/BM checkpoint and never enqueue work at startup.
            con.execute("UPDATE job_tasks SET retryable=1 WHERE id=?",(row['id'],))
            con.execute("UPDATE job_items SET retryable=1 WHERE id=? AND status='FAILED' AND error_code='CREATED_BUSINESS_RK_REQUIRED'",(row['item_id'],))
        rows=con.execute("SELECT t.id,t.item_id,a.result_json AS rk,b.result_json AS bm FROM job_tasks t "
            "JOIN provisioning_steps a ON a.item_id=t.item_id AND a.step='AD_ACCOUNT' AND a.status='SUCCESS' "
            "JOIN provisioning_steps b ON b.item_id=t.item_id AND b.step='BUSINESS' AND b.status='SUCCESS' "
            "WHERE t.status='FAILED' AND t.action='provisioning' AND t.error_code='CREATED_BUSINESS_RK_REQUIRED'").fetchall()
        for row in rows:
            rk=json.loads(row['rk']); bm=json.loads(row['bm']); account=str(rk.get('ad_account_id') or '')
            if not account.startswith('act_') or not account[4:].isdigit(): continue
            if not str(bm.get('business_id') or '').isdigit() or rk.get('business_id')!=bm.get('business_id'): continue
            # The exact RK was already created by this item; repair only the
            # prefix mismatch and leave creation checkpoints untouched.
            con.execute('UPDATE job_tasks SET retryable=1 WHERE id=?',(row['id'],))
            con.execute("UPDATE job_items SET retryable=1 WHERE id=? AND status='FAILED' AND error_code='CREATED_BUSINESS_RK_REQUIRED'",(row['item_id'],))

    @staticmethod
    def _repair_unstarted_prgssteam_rk_timezone(con: sqlite3.Connection) -> None:
        # The saved trial used the old UI's timezone default. Change it only
        # while no RK step has ever started; preserve every submitted RK.
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisioning_steps'").fetchone(): return
        rows=con.execute("SELECT t.id,t.payload_json FROM job_tasks t JOIN job_items i ON i.id=t.item_id "
            "WHERE i.job_id=? AND t.status='FAILED' AND NOT EXISTS "
            "(SELECT 1 FROM provisioning_steps s WHERE s.item_id=i.id AND s.step='AD_ACCOUNT')",
            ('cbd636ff53bd48c89259b015f2775797',)).fetchall()
        for row in rows:
            payload=json.loads(row['payload_json']); params=payload.get('parameters') or {}
            rk=params.get('AD_ACCOUNT') or {}
            if (params.get('FAN_PAGES') or {}).get('common_page') is True and rk.get('timezone_id')==1 and rk.get('currency')=='USD':
                rk['timezone_id']=137
                con.execute('UPDATE job_tasks SET payload_json=? WHERE id=?',(json.dumps(payload),row['id']))

    @staticmethod
    def _recover_common_page_checkpoint_failure(con: sqlite3.Connection) -> None:
        # The shared Page's missing step row failed before the final CREATE
        # callback could return. Preserve the Job and restore manual retry only.
        rows=con.execute("SELECT id,item_id,payload_json,error_message FROM job_tasks "
            "WHERE status='FAILED' AND action='provisioning' AND error_code='TASK_FAILED' "
            "AND error_message='cannot checkpoint FAN_PAGES: step row does not exist for item workspace-common-page-9'").fetchall()
        for row in rows:
            try: payload=json.loads(row['payload_json'])
            except (ValueError,TypeError): continue
            if (payload.get('parameters',{}).get('FAN_PAGES') or {}).get('common_page') is not True: continue
            code='COMMON_PAGE_STATE_REPAIRED'
            con.execute("UPDATE job_tasks SET error_code=?,retryable=1 WHERE id=?",(code,row['id']))
            con.execute("UPDATE job_items SET error_code=?,retryable=1 WHERE id=? AND status='FAILED' AND error_code='TASK_FAILED'",
                (code,row['item_id']))

    @staticmethod
    def _recover_legacy_fp_page_crashes(con: sqlite3.Connection) -> None:
        """Restore manual retry for FP renderer failures mislabeled TASK_FAILED.

        Keep IDs, results and mutation checkpoints intact. Nothing is enqueued;
        the existing FP handler reconciles a saved submit intent before CREATE.
        """
        rows = con.execute(
            "SELECT id,item_id,payload_json,error_message FROM job_tasks "
            "WHERE status='FAILED' AND action='provisioning' "
            "AND error_code='TASK_FAILED' AND (lower(error_message) LIKE '%page crashed%' OR lower(error_message) LIKE '%target crashed%' "
            "OR (lower(error_message) LIKE '%writeunixtransport%' AND lower(error_message) LIKE '%handler is closed%') "
            "OR (error_message LIKE '%createTreeWalker%' AND error_message LIKE '%parameter 1 is not of type%'))"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row['payload_json'])
            except (ValueError, TypeError):
                continue
            steps = payload.get('steps') if isinstance(payload, dict) else None
            if not isinstance(steps, list) or 'FAN_PAGES' not in [str(step).upper() for step in steps]:
                continue
            message = str(row['error_message']).lower()
            code = ('BROWSER_PAGE_CRASHED' if any(marker in message for marker in ('page crashed', 'target crashed'))
                    else 'BROWSER_DIAGNOSTIC_FAILED' if 'createtreewalker' in message else 'BROWSER_CONNECTION_CLOSED')
            con.execute("UPDATE job_tasks SET error_code=?,retryable=1 WHERE id=?", (code, row['id']))
            con.execute(
                "UPDATE job_items SET retryable=1,error_code=CASE "
                "WHEN error_code='TASK_FAILED' AND error_message=? THEN ? ELSE error_code END "
                "WHERE id=? AND status='FAILED'",
                (row['error_message'], code, row['item_id']),
            )

    async def create_job(self, request: Any) -> tuple[str, bool]:
        return await asyncio.to_thread(self._create_job_sync, request)

    def _create_job_sync(self, request: Any) -> tuple[str, bool]:
        now = _now()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if request.idempotency_key:
                row = con.execute('SELECT id FROM jobs WHERE idempotency_key=?', (request.idempotency_key,)).fetchone()
                if row:
                    return str(row['id']), False
            reserve = max(1048576, int(os.getenv('REMASK_STORAGE_RESERVE_BYTES', '33554432')))
            requested_bytes = _profiles_serialized_bytes(request.profiles)
            if requested_bytes > 8 * 1024 * 1024:
                raise ValueError('JOB_REQUEST_TOO_LARGE')
            if shutil.disk_usage(self.path.parent).free < reserve + requested_bytes * 4:
                raise ValueError('STORAGE_LOW_SPACE')
            maximum = max(1, int(os.getenv('REMASK_MAX_PENDING_ITEMS', '10000')))
            pending = con.execute("SELECT count(*) FROM job_items WHERE status IN ('QUEUED','RUNNING')").fetchone()[0]
            job_id = uuid.uuid4().hex
            from .provisioning.auto_plan import expand_auto_profiles
            profiles = expand_auto_profiles(request.profiles, job_id)
            if pending + len(profiles) > maximum:
                raise ValueError('JOB_QUEUE_FULL')
            expanded_bytes = _profiles_serialized_bytes(profiles)
            if shutil.disk_usage(self.path.parent).free < reserve + expanded_bytes * 4:
                raise ValueError('STORAGE_LOW_SPACE')
            con.execute('INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)',
                        (job_id,'QUEUED',request.idempotency_key,now,now))
            for profile in profiles:
                item_id = uuid.uuid4().hex
                con.execute('INSERT INTO job_items(id,job_id,profile_id,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (item_id,job_id,profile.profile_id,'QUEUED',now,now))
                for pos, task in enumerate(profile.tasks):
                    execution_payload = dict(task.payload)
                    task_dump = task.model_dump(exclude={'action','payload','idempotency_key'}, exclude_none=True)
                    execution_payload.update(task_dump)
                    con.execute('''INSERT INTO job_tasks(id,item_id,position,action,payload_json,idempotency_key,status,created_at,updated_at)
                                   VALUES(?,?,?,?,?,?,?,?,?)''',
                                (uuid.uuid4().hex,item_id,pos,task.action,json.dumps(execution_payload,separators=(',',':')),
                                 task.idempotency_key,'QUEUED',now,now))
            return job_id, True

    async def queued_item_ids(self, job_id: str | None = None) -> list[str]:
        return await asyncio.to_thread(self._queued_item_ids_sync, job_id)

    def _queued_item_ids_sync(self, job_id: str | None) -> list[str]:
        with self._connect() as con:
            if job_id:
                rows = con.execute("SELECT id,profile_id FROM job_items WHERE job_id=? AND status='QUEUED' ORDER BY created_at", (job_id,)).fetchall()
            else:
                rows = con.execute("SELECT id,profile_id FROM job_items WHERE status='QUEUED' ORDER BY created_at").fetchall()
            return _interleave_profile_items(rows)

    async def recover(self) -> list[str]:
        return await asyncio.to_thread(self._recover_sync)

    def _recover_sync(self) -> list[str]:
        now = _now()
        with self._connect() as con:
            con.execute("UPDATE job_items SET status='QUEUED',updated_at=? WHERE status='RUNNING'", (now,))
            con.execute("UPDATE job_tasks SET status='QUEUED',updated_at=? WHERE status='RUNNING'", (now,))
            rows = con.execute("SELECT id,profile_id FROM job_items WHERE status='QUEUED' ORDER BY created_at").fetchall()
            return _interleave_profile_items(rows)

    async def item(self, item_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._item_sync, item_id)

    def _item_sync(self, item_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute('SELECT * FROM job_items WHERE id=?', (item_id,)).fetchone()
            return dict(row) if row else None

    async def tasks(self, item_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._tasks_sync, item_id)

    def _tasks_sync(self, item_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute('SELECT * FROM job_tasks WHERE item_id=? ORDER BY position', (item_id,)).fetchall()
            out=[]
            for row in rows:
                d=dict(row)
                d['payload']=json.loads(d.pop('payload_json'))
                if d.get('result_json'):
                    d['result']=json.loads(d['result_json'])
                out.append(d)
            return out

    async def fail_item_for_checkpoint(self, item_id: str) -> bool:
        return await asyncio.to_thread(self._fail_item_for_checkpoint_sync, item_id)

    def _fail_item_for_checkpoint_sync(self, item_id: str) -> bool:
        now = _now()
        message = "Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены."
        with self._connect() as con:
            row = con.execute(
                "SELECT job_id FROM job_items WHERE id=? AND status='QUEUED'",
                (item_id,),
            ).fetchone()
            if not row:
                return False
            con.execute(
                """UPDATE job_tasks
                   SET status='FAILED',error_code='CHECKPOINT_REQUIRED',
                       error_message=?,retryable=0,updated_at=?
                   WHERE item_id=? AND status<>'SUCCESS'""",
                (message,now,item_id),
            )
            con.execute(
                """UPDATE job_items
                   SET status='FAILED',error_code='CHECKPOINT_REQUIRED',
                       error_message=?,retryable=0,updated_at=?
                   WHERE id=? AND status='QUEUED'""",
                (message,now,item_id),
            )
            self._refresh_job_sync(con,str(row['job_id']))
            return True

    async def fail_item_for_terminal_access(self, item_id: str, status: str) -> bool:
        terminal = str(status or "").strip().upper()
        if terminal != "BUSINESS_LOGIN_GATE":
            return False
        return await asyncio.to_thread(self._fail_item_for_terminal_access_sync, item_id)

    def _fail_item_for_terminal_access_sync(self, item_id: str) -> bool:
        now = _now()
        message = "Meta остановила доступ. Новые действия запрещены."
        with self._connect() as con:
            row = con.execute(
                "SELECT job_id FROM job_items WHERE id=? AND status='QUEUED'",
                (item_id,),
            ).fetchone()
            if not row:
                return False
            con.execute(
                """UPDATE job_tasks
                   SET status='FAILED',error_code='BUSINESS_LOGIN_GATE',
                       error_message=?,retryable=0,updated_at=?
                   WHERE item_id=? AND status<>'SUCCESS'""",
                (message, now, item_id),
            )
            con.execute(
                """UPDATE job_items
                   SET status='FAILED',error_code='BUSINESS_LOGIN_GATE',
                       error_message=?,retryable=0,updated_at=?
                   WHERE id=? AND status='QUEUED'""",
                (message, now, item_id),
            )
            self._refresh_job_sync(con, str(row["job_id"]))
            return True

    async def set_item_running(self, item_id: str) -> bool:
        return await asyncio.to_thread(self._claim_item_sync, item_id)

    def _claim_item_sync(self, item_id: str) -> bool:
        with self._connect() as con:
            return con.execute("UPDATE job_items SET status='RUNNING',attempt=attempt+1,error_code=NULL,error_message=NULL,retryable=0,updated_at=? WHERE id=? AND status='QUEUED'", (_now(),item_id)).rowcount == 1

    async def queued_count(self, job_id: str) -> int:
        def count():
            with self._connect() as con:
                return int(con.execute("SELECT count(*) FROM job_items WHERE job_id=? AND status='QUEUED'", (job_id,)).fetchone()[0])
        return await asyncio.to_thread(count)

    async def next_queued_item(self, blocked_profiles: set[str], after_profile: str) -> dict | None:
        def select():
            blocked = sorted(blocked_profiles)
            exclusion = ' AND profile_id NOT IN (' + ','.join('?' for _ in blocked) + ')' if blocked else ''
            with self._connect() as con:
                row = con.execute("SELECT id,profile_id FROM job_items WHERE status='QUEUED'" + exclusion +
                    " ORDER BY CASE WHEN profile_id > ? THEN 0 ELSE 1 END, profile_id, created_at, rowid LIMIT 1", (*blocked,after_profile)).fetchone()
                return dict(row) if row else None
        return await asyncio.to_thread(select)


    async def set_task_running(self, task_id: str) -> None:
        await asyncio.to_thread(self._execute, "UPDATE job_tasks SET status='RUNNING',attempt=attempt+1,error_code=NULL,error_message=NULL,retryable=0,updated_at=? WHERE id=?", (_now(),task_id))

    async def set_task_success(self, task_id: str, result: dict[str, Any]) -> None:
        await asyncio.to_thread(self._execute, "UPDATE job_tasks SET status='SUCCESS',result_json=?,retryable=0,updated_at=? WHERE id=?",
                                (json.dumps(result,separators=(',',':')),_now(),task_id))

    async def set_task_failed(
        self,
        task_id: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        await asyncio.to_thread(
            self._execute,
            "UPDATE job_tasks SET status='FAILED',error_code=?,error_message=?,retryable=?,updated_at=? WHERE id=?",
            (code, message[:1000], 1 if retryable else 0, _now(), task_id),
        )

    async def finalize_item(self, item_id: str) -> None:
        await asyncio.to_thread(self._finalize_item_sync, item_id)

    def _finalize_item_sync(self, item_id: str) -> None:
        now=_now()
        with self._connect() as con:
            rows=con.execute(
                'SELECT status,error_code,error_message,retryable FROM job_tasks WHERE item_id=?',
                (item_id,),
            ).fetchall()
            failed=[r for r in rows if r['status']=='FAILED']
            statuses=[str(r['status']) for r in rows]

            if failed:
                status='FAILED'
            elif statuses and all(value=='SUCCESS' for value in statuses):
                status='SUCCESS'
            elif 'RUNNING' in statuses:
                status='RUNNING'
            else:
                # QUEUED (including a cancelled/recoverable worker task) must
                # never be promoted to SUCCESS merely because no failure row
                # exists yet.
                status='QUEUED'

            code=failed[0]['error_code'] if failed else None
            msg=failed[0]['error_message'] if failed else None
            retryable=1 if any(bool(r['retryable']) for r in failed) else 0
            con.execute(
                'UPDATE job_items SET status=?,error_code=?,error_message=?,retryable=?,updated_at=? WHERE id=?',
                (status,code,msg,retryable,now,item_id),
            )
            row=con.execute('SELECT job_id FROM job_items WHERE id=?', (item_id,)).fetchone()
            if row:
                self._refresh_job_sync(con, str(row['job_id']))

    def _refresh_job_sync(self, con: sqlite3.Connection, job_id: str) -> None:
        rows=con.execute('SELECT status FROM job_items WHERE job_id=?', (job_id,)).fetchall()
        statuses=[str(r['status']) for r in rows]
        if any(s in {'QUEUED','RUNNING'} for s in statuses):
            status='RUNNING' if 'RUNNING' in statuses else 'QUEUED'
        elif statuses and all(s=='SUCCESS' for s in statuses):
            status='SUCCESS'
        elif statuses and all(s=='FAILED' for s in statuses):
            status='FAILED'
        else:
            status='PARTIAL'
        con.execute('UPDATE jobs SET status=?,updated_at=? WHERE id=?', (status,_now(),job_id))

    async def job_view(self, job_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._job_view_sync, job_id)


    def _rk_details_recovery_available(self, con, item_id: str, profile_id: str) -> bool:
        row = con.execute(
            "SELECT result_json FROM provisioning_steps "
            "WHERE item_id=? AND profile_id=? AND step='AD_ACCOUNT'",
            (item_id, profile_id),
        ).fetchone()
        if not row or not row["result_json"]:
            return False
        try:
            result = json.loads(row["result_json"])
        except (ValueError, TypeError):
            return False
        if not isinstance(result, dict):
            return False
        return bool(details_candidate(result,
            business_id=str(result.get("business_id") or ""),
            account_name=str(result.get("account_name") or result.get("name") or "")))

    @staticmethod
    def _advertising_progress(con, item, tasks):
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_launch_steps'").fetchone():
            return []
        progress, seen = [], set()
        for task in tasks:
            if task.get('action') not in {'private_launch','private_delivery'} or not isinstance(task.get('payload'), dict):
                continue
            payload = task['payload']
            business = str(payload.get('business_id') or '').strip()
            account = str(payload.get('ad_account_id') or '').removeprefix('act_').strip()
            launch_key = str((payload.get('delivery_key') if task.get('action') == 'private_delivery' else payload.get('launch_key')) or task.get('idempotency_key')
                             or f"{item['profile_id']}:{business}:{account}").strip()
            if not business.isdigit() or not account.isdigit() or len(launch_key) > 200:
                continue
            delivery_key = ('ads-delivery:' + hashlib.sha256(launch_key.encode()).hexdigest()
                            if task.get('action') == 'private_launch' else '')
            rows = con.execute("""SELECT launch_key,step,status,entity_id,result_json,error_code,submitted,updated_at
                FROM private_launch_steps WHERE launch_key IN (?,?) AND profile_id=? AND business_id=? AND ad_account_id=?
                ORDER BY CASE WHEN step LIKE 'ADS_MANAGER:VIDEO_TRANSFER_%' THEN 1 ELSE 0 END,
                         created_at,step LIMIT 100""", (launch_key, delivery_key, item['profile_id'], business, account)).fetchall()
            for row in rows:
                identity = (row['launch_key'], row['step'])
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    result = json.loads(row['result_json'] or '{}')
                    evidence = result.get('evidence', {})
                    if not isinstance(evidence, dict):
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue
                outputs = evidence.get('outputs', {})
                verification = evidence.get('verification', {})
                publication = evidence.get('publication', {})
                if (row['step'] == 'ADS_MANAGER:PUBLISH' and isinstance(verification, dict)
                        and verification.get('verified') is True):
                    outputs = verification.get('entities', {})
                progress.append({'task_id': task['id'], 'step': row['step'], 'status': row['status'],
                    'business_id': business, 'ad_account_id': account, 'entity_id': row['entity_id'],
                    'submitted': None if row['submitted'] is None else bool(row['submitted']),
                    'updated_at': row['updated_at'], 'code': row['error_code'],
                    'phase_receipt': verification.get('phase_receipt') is True if isinstance(verification, dict) else False,
                    'verified': (verification.get('verified') is True and verification.get('phase_receipt') is not True)
                        if isinstance(verification, dict) else False,
                    'outputs': {key: value for key, value in outputs.items()
                        if key in {'entity_id','campaign_id','adset_id','creative_id','ad_id'}
                        and isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{5,128}', value)} if isinstance(outputs, dict) else {},
                    'publication': {key: publication[key] for key in ('operation_id','status','code','poll_count','last_polled_at')
                        if key in publication} if isinstance(publication, dict) else {}})
        return progress

    def _job_view_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            job=con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job:
                return None
            # Frequent UI polls must not run two child SELECTs per profile.
            # Join through durable job_items so one bound job_id parameter covers
            # arbitrarily large jobs without SQLite's IN-list variable limit.
            item_rows=con.execute(
                'SELECT * FROM job_items WHERE job_id=? ORDER BY created_at',(job_id,)
            ).fetchall()
            tasks_by_item={}
            for tr in con.execute(
                'SELECT t.* FROM job_tasks AS t '
                'JOIN job_items AS i ON i.id=t.item_id '
                'WHERE i.job_id=? ORDER BY i.created_at,t.position',(job_id,)
            ).fetchall():
                t=dict(tr)
                t['retryable']=bool(t.get('retryable'))
                t['payload']=json.loads(t.pop('payload_json'))
                if t.get('result_json'):
                    t['result']=json.loads(t['result_json'])
                t.pop('result_json',None)
                tasks_by_item.setdefault(t['item_id'],[]).append(t)
            steps_by_item={}
            for pr in con.execute(
                'SELECT p.* FROM provisioning_steps AS p '
                'JOIN job_items AS i ON i.id=p.item_id '
                'WHERE i.job_id=? ORDER BY i.created_at,p.created_at,p.step',(job_id,)
            ).fetchall():
                p=dict(pr)
                if p.get('result_json'):
                    p['result']=json.loads(p['result_json'])
                p.pop('result_json',None)
                steps_by_item.setdefault(p['item_id'],[]).append(p)
            items=[]
            for ir in item_rows:
                item=dict(ir)
                item['retryable']=bool(item.get('retryable'))
                tasks=tasks_by_item.get(item['id'],[])
                # Expose a manual verification retry for the legacy false-quota
                # failure. This read-only view never queues work or alters IDs.
                details_recovery = (
                    item.get('status') == 'FAILED'
                    and any(task.get('status') == 'FAILED' and
                        task.get('error_code') == 'META_AD_ACCOUNT_CREATE_UNAVAILABLE' for task in tasks)
                    and self._rk_details_recovery_available(con, item['id'], item['profile_id'])
                )
                if details_recovery:
                    item['retryable'] = True
                    item['recovery_mode'] = 'verify_created_ad_account'
                    for task in tasks:
                        if task.get('status') == 'FAILED' and task.get('error_code') == 'META_AD_ACCOUNT_CREATE_UNAVAILABLE':
                            task['retryable'] = True
                            task['recovery_mode'] = 'verify_created_ad_account'
                item['tasks']=tasks
                item['advertising_steps']=(self._advertising_progress(con,item,tasks)
                    if any(t.get('action') in {'private_launch','private_delivery'} for t in tasks)
                    else [])
                item['provisioning_steps']=steps_by_item.get(item['id'],[])
                items.append(item)
            data=dict(job)
            data['items']=items
            data['items_total']=len(items)
            data['items_succeeded']=sum(1 for x in items if x['status']=='SUCCESS')
            data['items_failed']=sum(1 for x in items if x['status']=='FAILED')
            return data

    async def import_snapshots(self, snapshots: list[dict[str, Any]]) -> int:
        return await asyncio.to_thread(self._import_snapshots_sync, snapshots)

    def _import_snapshots_sync(self, snapshots: list[dict[str, Any]]) -> int:
        imported = 0
        with self._connect() as con:
            for snapshot in snapshots:
                if not isinstance(snapshot, dict):
                    continue
                job_id = str(snapshot.get('id') or '').strip()
                if not job_id:
                    continue
                created_at = int(snapshot.get('created_at') or _now())
                updated_at = int(snapshot.get('updated_at') or created_at)
                con.execute(
                    """INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET status=excluded.status,idempotency_key=excluded.idempotency_key,
                       created_at=excluded.created_at,updated_at=excluded.updated_at""",
                    (job_id, str(snapshot.get('status') or 'QUEUED'), snapshot.get('idempotency_key'), created_at, updated_at),
                )
                for item in snapshot.get('items') or []:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get('id') or '').strip()
                    profile_id = str(item.get('profile_id') or '').strip()
                    if not item_id or not profile_id:
                        continue
                    icreated = int(item.get('created_at') or created_at)
                    iupdated = int(item.get('updated_at') or updated_at)
                    con.execute(
                        """INSERT INTO job_items(id,job_id,profile_id,status,attempt,error_code,error_message,created_at,updated_at,retryable)
                           VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                           job_id=excluded.job_id,profile_id=excluded.profile_id,status=excluded.status,attempt=excluded.attempt,
                           error_code=excluded.error_code,error_message=excluded.error_message,created_at=excluded.created_at,updated_at=excluded.updated_at,retryable=excluded.retryable""",
                        (item_id, job_id, profile_id, str(item.get('status') or 'QUEUED'), int(item.get('attempt') or 0),
                         item.get('error_code'), item.get('error_message'), icreated, iupdated, int(bool(item.get('retryable')))),
                    )
                    for task in item.get('tasks') or []:
                        if not isinstance(task, dict):
                            continue
                        task_id = str(task.get('id') or '').strip()
                        if not task_id:
                            continue
                        payload = task.get('payload') if isinstance(task.get('payload'), dict) else {}
                        result = task.get('result') if isinstance(task.get('result'), dict) else None
                        tcreated = int(task.get('created_at') or icreated)
                        tupdated = int(task.get('updated_at') or iupdated)
                        con.execute(
                            """INSERT INTO job_tasks(id,item_id,position,action,payload_json,idempotency_key,status,attempt,result_json,error_code,error_message,created_at,updated_at,retryable)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                               item_id=excluded.item_id,position=excluded.position,action=excluded.action,payload_json=excluded.payload_json,
                               idempotency_key=excluded.idempotency_key,status=excluded.status,attempt=excluded.attempt,result_json=excluded.result_json,
                               error_code=excluded.error_code,error_message=excluded.error_message,created_at=excluded.created_at,updated_at=excluded.updated_at,retryable=excluded.retryable""",
                            (task_id, item_id, int(task.get('position') or 0), str(task.get('action') or ''),
                             json.dumps(payload, separators=(',', ':')), task.get('idempotency_key'), str(task.get('status') or 'QUEUED'),
                             int(task.get('attempt') or 0), json.dumps(result, separators=(',', ':')) if result is not None else None,
                             task.get('error_code'), task.get('error_message'), tcreated, tupdated, int(bool(task.get('retryable')))),
                        )
                    for pstep in item.get('provisioning_steps') or []:
                        if not isinstance(pstep, dict):
                            continue
                        step = str(pstep.get('step') or '').strip().upper()
                        if step not in {'PROXY_CHECK','FAN_PAGES','BUSINESS','AD_ACCOUNT','PAGE_ACCESS','FUNDING'}:
                            continue
                        scope_key = str(pstep.get('scope_key') or 'default').strip() or 'default'
                        presult = pstep.get('result') if isinstance(pstep.get('result'), dict) else None
                        pcreated = int(pstep.get('created_at') or icreated)
                        pupdated = int(pstep.get('updated_at') or iupdated)
                        pstatus = str(pstep.get('status') or 'QUEUED')
                        con.execute(
                            """INSERT INTO provisioning_steps(
                                   item_id,profile_id,scope_key,step,status,attempt,result_json,
                                   error_code,error_message,created_at,updated_at
                               ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                               ON CONFLICT(item_id,step) DO UPDATE SET
                                   profile_id=excluded.profile_id,scope_key=excluded.scope_key,
                                   status=excluded.status,attempt=excluded.attempt,
                                   result_json=excluded.result_json,error_code=excluded.error_code,
                                   error_message=excluded.error_message,created_at=excluded.created_at,
                                   updated_at=excluded.updated_at""",
                            (
                                item_id, profile_id, scope_key, step, pstatus,
                                int(pstep.get('attempt') or 0),
                                json.dumps(presult, separators=(',', ':')) if presult is not None else None,
                                pstep.get('error_code'), pstep.get('error_message'),
                                pcreated, pupdated,
                            ),
                        )
                        result_key = {
                            'BUSINESS': 'business_id',
                            'AD_ACCOUNT': 'ad_account_id',
                            'FUNDING': 'funding_source_id',
                        }.get(step)
                        result_value = str((presult or {}).get(result_key) or '').strip() if result_key else ''
                        if pstatus == 'SUCCESS' and result_key and result_value:
                            con.execute(
                                """INSERT INTO provisioning_entities(
                                       profile_id,scope_key,business_id,ad_account_id,funding_source_id,
                                       created_at,updated_at
                                   ) VALUES(?,?,?,?,?,?,?)
                                   ON CONFLICT(profile_id,scope_key) DO NOTHING""",
                                (profile_id, scope_key, None, None, None, pcreated, pupdated),
                            )
                            column = {
                                'business_id': 'business_id',
                                'ad_account_id': 'ad_account_id',
                                'funding_source_id': 'funding_source_id',
                            }[result_key]
                            con.execute(
                                f"UPDATE provisioning_entities SET {column}=?,updated_at=? "
                                "WHERE profile_id=? AND scope_key=?",
                                (result_value, pupdated, profile_id, scope_key),
                            )
                imported += 1
            self._recover_legacy_fp_page_crashes(con)
            self._recover_common_page_checkpoint_failure(con)
            self._repair_unstarted_prgssteam_rk_timezone(con)
            self._recover_premature_page_access(con)
            self._recover_common_page_picker_failure(con)
            self._recover_legacy_owner_page_approval_stub(con)
            self._recover_owner_page_actor_probe(con)
            self._recover_owner_active_partner_reconciliation(con)
            self._recover_owner_confirmed_operator_assignment(con)
        return imported

    async def record_terminal_checkpoint(self, profile_id: str) -> bool:
        """Persist an explicitly observed Meta checkpoint as a terminal profile state."""
        return await self.record_terminal_profile_status(profile_id, "CHECKPOINT")

    async def record_terminal_profile_status(self, profile_id: str, status: str) -> bool:
        """Persist an explicitly observed terminal Meta access state."""
        clean = str(profile_id or "").strip()
        terminal = str(status or "").strip().upper()
        if not clean or terminal not in {"CHECKPOINT", "BUSINESS_LOGIN_GATE"}:
            return False
        try:
            return await asyncio.to_thread(self._record_terminal_profile_status_sync, clean, terminal)
        except sqlite3.OperationalError as exc:
            if "no such table: profile_terminal_statuses" not in str(exc):
                raise
            await self.init()
            return await asyncio.to_thread(self._record_terminal_profile_status_sync, clean, terminal)

    def _record_terminal_profile_status_sync(self, profile_id: str, status: str) -> bool:
        now = _now()
        checkpoint_message = "Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены."
        gate_message = "Meta остановила доступ. Новые действия запрещены."
        with self._connect() as con:
            con.execute(
                """INSERT INTO profile_terminal_statuses(profile_id,status,checked_at,message)
                   VALUES(?,?,?,?)
                   ON CONFLICT(profile_id) DO UPDATE SET
                     status=CASE
                       WHEN profile_terminal_statuses.status='CHECKPOINT'
                         OR excluded.status='CHECKPOINT' THEN 'CHECKPOINT'
                       ELSE 'BUSINESS_LOGIN_GATE' END,
                     checked_at=MAX(profile_terminal_statuses.checked_at,excluded.checked_at),
                     message=CASE
                       WHEN profile_terminal_statuses.status='CHECKPOINT'
                         OR excluded.status='CHECKPOINT' THEN ?
                       ELSE ? END""",
                (profile_id, status, now,
                 checkpoint_message if status == "CHECKPOINT" else gate_message,
                 checkpoint_message, gate_message),
            )
        return True

    def _record_terminal_checkpoint_sync(self, profile_id: str) -> bool:
        return self._record_terminal_profile_status_sync(profile_id, "CHECKPOINT")

    async def clear_terminal_business_login_gate(self, profile_id: str) -> bool:
        """Clear only a Business Login Gate after a successful live read-only revalidation."""
        clean = str(profile_id or "").strip()
        if not clean:
            return False
        try:
            return await asyncio.to_thread(
                self._clear_terminal_business_login_gate_sync, clean
            )
        except sqlite3.OperationalError as exc:
            if "no such table: profile_terminal_statuses" not in str(exc):
                raise
            await self.init()
            return await asyncio.to_thread(
                self._clear_terminal_business_login_gate_sync, clean
            )

    def _clear_terminal_business_login_gate_sync(self, profile_id: str) -> bool:
        with self._connect() as con:
            cursor = con.execute(
                "DELETE FROM profile_terminal_statuses "
                "WHERE profile_id=? AND status='BUSINESS_LOGIN_GATE'",
                (profile_id,),
            )
        return cursor.rowcount > 0

    async def terminal_profile_statuses(self, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
        clean = list(dict.fromkeys(
            str(value or "").strip() for value in profile_ids if str(value or "").strip()
        ))
        if not clean:
            return {}
        try:
            return await asyncio.to_thread(self._terminal_profile_statuses_sync, clean)
        except sqlite3.OperationalError as exc:
            if "no such table: profile_terminal_statuses" not in str(exc):
                raise
            await self.init()
            return await asyncio.to_thread(self._terminal_profile_statuses_sync, clean)

    def _terminal_profile_statuses_sync(self, profile_ids: list[str]) -> dict[str, dict[str, Any]]:
        placeholders = ",".join("?" for _ in profile_ids)
        with self._connect() as con:
            rows = con.execute(
                "SELECT profile_id,status,checked_at,message FROM profile_terminal_statuses "
                "WHERE status IN ('CHECKPOINT','BUSINESS_LOGIN_GATE') AND profile_id IN ("
                + placeholders + ")",
                tuple(profile_ids),
            ).fetchall()
        return {
            str(row["profile_id"]): {
                "status": str(row["status"]),
                "checked_at": int(row["checked_at"] or 0),
                "message": str(row["message"] or ""),
            }
            for row in rows
        }

    async def terminal_checkpoint_profiles(self, profile_ids: list[str]) -> list[str]:
        """Return profiles whose Meta checkpoint has been recorded as terminal."""
        clean = list(dict.fromkeys(
            str(value or '').strip() for value in profile_ids if str(value or '').strip()
        ))
        if not clean:
            return []
        try:
            return await asyncio.to_thread(self._terminal_checkpoint_profiles_sync, clean)
        except sqlite3.OperationalError as exc:
            if 'no such table: profile_terminal_statuses' not in str(exc):
                raise
            await self.init()
            return await asyncio.to_thread(self._terminal_checkpoint_profiles_sync, clean)

    def _terminal_checkpoint_profiles_sync(self, profile_ids: list[str]) -> list[str]:
        placeholders = ','.join('?' for _ in profile_ids)
        with self._connect() as con:
            rows = con.execute(
                "SELECT profile_id FROM profile_terminal_statuses "
                "WHERE status='CHECKPOINT' AND profile_id IN (" + placeholders + ")",
                tuple(profile_ids),
            ).fetchall()
        return [str(row['profile_id']) for row in rows]

    async def latest_profile_session_statuses(self) -> list[dict[str, Any]]:
        """Return concise per-profile status, with terminal checkpoints preserved."""
        return await asyncio.to_thread(self._latest_profile_session_statuses_sync)

    def _latest_profile_session_statuses_sync(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            terminal_rows = con.execute(
                "SELECT profile_id,status,checked_at,message FROM profile_terminal_statuses "
                "WHERE status IN ('CHECKPOINT','BUSINESS_LOGIN_GATE') "
                "ORDER BY checked_at DESC,profile_id"
            ).fetchall()
            terminal_by_profile = {
                str(row["profile_id"]): {
                    "status": str(row["status"]),
                    "checked_at": int(row["checked_at"] or 0),
                    "message": str(row["message"] or ""),
                }
                for row in terminal_rows
            }
            rows = con.execute(
                """
                SELECT i.id AS item_id,i.profile_id,i.status,i.error_code,i.error_message,i.updated_at,
                       j.created_at AS job_created_at,
                       COALESCE((
                           SELECT group_concat(
                               COALESCE(t.error_code,'') || ' ' || COALESCE(t.error_message,''),
                               ' '
                           )
                           FROM job_tasks t
                           WHERE t.item_id=i.id
                             AND upper(t.status)='FAILED'
                             AND (COALESCE(t.error_code,'')<>'' OR COALESCE(t.error_message,'')<>'')
                       ), '') AS task_errors
                FROM job_items i
                JOIN jobs j ON j.id=i.job_id
                ORDER BY i.updated_at DESC,j.created_at DESC,i.created_at DESC,i.job_id DESC
                """
            ).fetchall()

        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            profile_id = str(row["profile_id"] or "").strip()
            if not profile_id or profile_id in seen:
                continue
            seen.add(profile_id)

            if profile_id in terminal_by_profile:
                output.append({
                    "profile_id": profile_id,
                    **terminal_by_profile[profile_id],
                })
                continue

            item_status = str(row["status"] or "").strip().upper()
            error_code = str(row["error_code"] or "").strip().upper()
            error_message = str(row["error_message"] or "").strip()
            task_errors = str(row["task_errors"] or "").strip()
            search_text = (error_code + " " + error_message + " " + task_errors).casefold()

            if item_status in {"QUEUED", "RUNNING"}:
                state = "IN_PROGRESS"
                message = "Подготовка выполняется."
            elif item_status == "SUCCESS":
                state = "ACTIVE"
                message = "Meta ответила на последнюю задачу."
            elif (
                error_code == "CHECKPOINT_REQUIRED"
                or "checkpoint_required" in search_text
                or "checkpoint_redirect" in search_text
                or "/checkpoint/" in search_text
            ):
                state = "CHECKPOINT"
                message = "Чекпоинт Meta: аккаунт остановлен. Новые задачи запрещены."
            elif error_code == "BUSINESS_LOGIN_GATE" or "business_login_gate" in search_text:
                state = "BUSINESS_LOGIN_GATE"
                message = "Meta остановила доступ. Новые действия запрещены."
            elif error_code == "TWO_FACTOR_REQUIRED" or "two_factor_required" in search_text:
                state = "TWO_FACTOR"
                message = "Meta запросила двухфакторную проверку."
            elif (
                error_code == "SESSION_EXPIRED"
                or "session_expired" in search_text
                or "loginpage" in search_text
            ):
                state = "LOGIN_REQUIRED"
                message = "Meta не подтвердила вход."
            elif error_code == "FACEBOOK_TEMPORARILY_BLOCKED" or "facebook_temporarily_blocked" in search_text:
                state = "LIMITED"
                message = "Meta временно ограничила действия."
            elif re.search(
                r"1373107|2455031|field_exception|too many attempts",
                search_text,
                re.IGNORECASE,
            ):
                state = "ACTIVE"
                message = (
                    "Meta ограничила последнее действие."
                    if re.search(r"1373107|too many attempts", search_text, re.IGNORECASE)
                    else "Meta ответила, но последний шаг не выполнен."
                )
            else:
                state = "UNVERIFIED"
                message = "Последняя задача не подтвердила состояние сессии."

            output.append(
                {
                    "profile_id": profile_id,
                    "status": state,
                    "checked_at": int(row["updated_at"] or row["job_created_at"] or 0),
                    "message": message,
                }
            )

        for profile_id, terminal in terminal_by_profile.items():
            if profile_id not in seen:
                output.append({"profile_id": profile_id, **terminal})
        return output

    async def retry_failed(
        self,
        job_id: str,
        *,
        excluded_profile_ids: set[str] | None = None,
    ) -> int:
        excluded=tuple(sorted({
            str(value).strip()
            for value in (excluded_profile_ids or set())
            if str(value).strip()
        }))
        return await asyncio.to_thread(
            self._retry_failed_sync, job_id, excluded
        )

    def _retry_failed_sync(
        self,
        job_id: str,
        excluded_profile_ids: tuple[str, ...] = (),
    ) -> int:
        now=_now()
        excluded=set(excluded_profile_ids)
        with self._connect() as con:
            rows=con.execute(
                "SELECT id,profile_id,retryable,error_code FROM job_items "
                "WHERE job_id=? AND status='FAILED'", (job_id,),
            ).fetchall()
            excluded.update(
                str(row['profile_id'] or '')
                for row in con.execute(
                    "SELECT profile_id FROM profile_terminal_statuses "
                    "WHERE status IN ('CHECKPOINT','BUSINESS_LOGIN_GATE')"
                ).fetchall()
            )
            recovery_ids = {
                str(row['id']) for row in rows
                if row['error_code'] == 'META_AD_ACCOUNT_CREATE_UNAVAILABLE'
                and self._rk_details_recovery_available(con, row['id'], row['profile_id'])
            }
            ids=[
                str(row['id'])
                for row in rows
                if str(row['profile_id'] or '') not in excluded
                and (bool(row['retryable']) or str(row['id']) in recovery_ids or row['error_code'] in {
                    'CHECKPOINT_REQUIRED', 'SESSION_EXPIRED', 'TWO_FACTOR_REQUIRED', 'PAGE_ADD_UI_CHANGED',
                })
            ]
            for item_id in ids:
                con.execute(
                    "UPDATE job_items SET status='QUEUED',error_code=NULL,error_message=NULL,retryable=0,updated_at=? WHERE id=?",
                    (now,item_id),
                )
                con.execute(
                    """UPDATE job_tasks
                       SET status='QUEUED',error_code=NULL,error_message=NULL,retryable=0,updated_at=?
                       WHERE item_id=? AND status='FAILED'
                         AND (
                           retryable=1
                           OR error_code IN (
                             'CHECKPOINT_REQUIRED',
                             'SESSION_EXPIRED',
                             'TWO_FACTOR_REQUIRED',
                             'PAGE_ADD_UI_CHANGED'
                           )
                           OR (error_code='META_AD_ACCOUNT_CREATE_UNAVAILABLE' AND ?=1)
                         )""",
                    (now,item_id,1 if item_id in recovery_ids else 0),
                )
            if ids:
                con.execute(
                    "UPDATE jobs SET status='QUEUED',updated_at=? WHERE id=?",
                    (now,job_id),
                )
            return len(ids)

    async def queue_count(self) -> int:
        return await asyncio.to_thread(self._queue_count_sync)

    def _queue_count_sync(self) -> int:
        with self._connect() as con:
            return int(con.execute("SELECT COUNT(*) c FROM job_items WHERE status='QUEUED'").fetchone()['c'])

    def _execute(self, sql: str, args: tuple[Any, ...]) -> None:
        with self._connect() as con:
            con.execute(sql, args)

