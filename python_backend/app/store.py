from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

def _now() -> int:
    return int(time.time())

class JobStore:
    def __init__(self, db_path: str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA foreign_keys=ON')
        return con

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    def _init_sync(self) -> None:
        with self._connect() as con:
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
                updated_at INTEGER NOT NULL,
                UNIQUE(job_id, profile_id)
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
            CREATE INDEX IF NOT EXISTS idx_items_status ON job_items(status);
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

    async def create_job(self, request: Any) -> tuple[str, bool]:
        return await asyncio.to_thread(self._create_job_sync, request)

    def _create_job_sync(self, request: Any) -> tuple[str, bool]:
        now = _now()
        with self._connect() as con:
            if request.idempotency_key:
                row = con.execute('SELECT id FROM jobs WHERE idempotency_key=?', (request.idempotency_key,)).fetchone()
                if row:
                    return str(row['id']), False
            job_id = uuid.uuid4().hex
            con.execute('INSERT INTO jobs(id,status,idempotency_key,created_at,updated_at) VALUES(?,?,?,?,?)',
                        (job_id,'QUEUED',request.idempotency_key,now,now))
            for profile in request.profiles:
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
                rows = con.execute("SELECT id FROM job_items WHERE job_id=? AND status='QUEUED' ORDER BY created_at", (job_id,)).fetchall()
            else:
                rows = con.execute("SELECT id FROM job_items WHERE status='QUEUED' ORDER BY created_at").fetchall()
            return [str(r['id']) for r in rows]

    async def recover(self) -> list[str]:
        return await asyncio.to_thread(self._recover_sync)

    def _recover_sync(self) -> list[str]:
        now = _now()
        with self._connect() as con:
            con.execute("UPDATE job_items SET status='QUEUED',updated_at=? WHERE status='RUNNING'", (now,))
            con.execute("UPDATE job_tasks SET status='QUEUED',updated_at=? WHERE status='RUNNING'", (now,))
            rows = con.execute("SELECT id FROM job_items WHERE status='QUEUED' ORDER BY created_at").fetchall()
            return [str(r['id']) for r in rows]

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

    async def set_item_running(self, item_id: str) -> None:
        await asyncio.to_thread(self._execute, "UPDATE job_items SET status='RUNNING',attempt=attempt+1,error_code=NULL,error_message=NULL,retryable=0,updated_at=? WHERE id=?", (_now(),item_id))

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

    def _job_view_sync(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            job=con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not job:
                return None
            items=[]
            for ir in con.execute('SELECT * FROM job_items WHERE job_id=? ORDER BY created_at', (job_id,)).fetchall():
                item=dict(ir)
                item['retryable']=bool(item.get('retryable'))
                tasks=[]
                for tr in con.execute('SELECT * FROM job_tasks WHERE item_id=? ORDER BY position', (item['id'],)).fetchall():
                    t=dict(tr)
                    t['retryable']=bool(t.get('retryable'))
                    t['payload']=json.loads(t.pop('payload_json'))
                    if t.get('result_json'):
                        t['result']=json.loads(t['result_json'])
                    t.pop('result_json',None)
                    tasks.append(t)
                item['tasks']=tasks
                provisioning_steps=[]
                for pr in con.execute(
                    'SELECT * FROM provisioning_steps WHERE item_id=? ORDER BY created_at,step',
                    (item['id'],),
                ).fetchall():
                    p=dict(pr)
                    if p.get('result_json'):
                        p['result']=json.loads(p['result_json'])
                    p.pop('result_json',None)
                    provisioning_steps.append(p)
                item['provisioning_steps']=provisioning_steps
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
                        """INSERT INTO job_items(id,job_id,profile_id,status,attempt,error_code,error_message,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                           job_id=excluded.job_id,profile_id=excluded.profile_id,status=excluded.status,attempt=excluded.attempt,
                           error_code=excluded.error_code,error_message=excluded.error_message,created_at=excluded.created_at,updated_at=excluded.updated_at""",
                        (item_id, job_id, profile_id, str(item.get('status') or 'QUEUED'), int(item.get('attempt') or 0),
                         item.get('error_code'), item.get('error_message'), icreated, iupdated),
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
                            """INSERT INTO job_tasks(id,item_id,position,action,payload_json,idempotency_key,status,attempt,result_json,error_code,error_message,created_at,updated_at)
                               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                               item_id=excluded.item_id,position=excluded.position,action=excluded.action,payload_json=excluded.payload_json,
                               idempotency_key=excluded.idempotency_key,status=excluded.status,attempt=excluded.attempt,result_json=excluded.result_json,
                               error_code=excluded.error_code,error_message=excluded.error_message,created_at=excluded.created_at,updated_at=excluded.updated_at""",
                            (task_id, item_id, int(task.get('position') or 0), str(task.get('action') or ''),
                             json.dumps(payload, separators=(',', ':')), task.get('idempotency_key'), str(task.get('status') or 'QUEUED'),
                             int(task.get('attempt') or 0), json.dumps(result, separators=(',', ':')) if result is not None else None,
                             task.get('error_code'), task.get('error_message'), tcreated, tupdated),
                        )
                    for pstep in item.get('provisioning_steps') or []:
                        if not isinstance(pstep, dict):
                            continue
                        step = str(pstep.get('step') or '').strip().upper()
                        if step not in {'PROXY_CHECK','BUSINESS','AD_ACCOUNT','FUNDING'}:
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
        return imported

    async def retry_failed(self, job_id: str) -> int:
        return await asyncio.to_thread(self._retry_failed_sync, job_id)

    def _retry_failed_sync(self, job_id: str) -> int:
        now=_now()
        with self._connect() as con:
            rows=con.execute(
                "SELECT id FROM job_items WHERE job_id=? AND status='FAILED' AND retryable=1",
                (job_id,),
            ).fetchall()
            ids=[str(r['id']) for r in rows]
            for item_id in ids:
                con.execute(
                    "UPDATE job_items SET status='QUEUED',error_code=NULL,error_message=NULL,retryable=0,updated_at=? WHERE id=?",
                    (now,item_id),
                )
                con.execute(
                    "UPDATE job_tasks SET status='QUEUED',error_code=NULL,error_message=NULL,retryable=0,updated_at=? "
                    "WHERE item_id=? AND status='FAILED' AND retryable=1",
                    (now,item_id),
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
