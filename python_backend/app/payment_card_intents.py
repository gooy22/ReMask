"""Durable metadata-only guard at the card Save boundary."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from pathlib import Path


_SCHEMA_LOCK = threading.Lock()


class CardIntentLedger:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        # SQLite schema initialization changes journal mode and creates indexes.
        # Concurrent first-use calls must serialize this stage; otherwise a
        # competing PRAGMA can fail with 'database is locked' before the
        # unique pending-intent guard gets a chance to reject a second Save.
        with _SCHEMA_LOCK:
            con = sqlite3.connect(self.path, timeout=15)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('''CREATE TABLE IF NOT EXISTS card_http_intents (
                attempt_id TEXT PRIMARY KEY, profile TEXT NOT NULL, account TEXT NOT NULL,
                card_id TEXT NOT NULL, phase TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}',
                updated_at INTEGER NOT NULL)''')
            con.execute('''CREATE UNIQUE INDEX IF NOT EXISTS card_http_pending_account
                ON card_http_intents(profile, account)
                WHERE phase IN ('SUBMITTED', 'SUBMITTED_UNVERIFIED', 'ACTION_REQUIRED', 'VERIFYING')''')
            con.execute('''CREATE TABLE IF NOT EXISTS card_bank_verification_intents (
                attempt_id TEXT PRIMARY KEY,
                profile TEXT NOT NULL, account TEXT NOT NULL,
                payment_account TEXT NOT NULL, credential_id TEXT NOT NULL,
                card_id TEXT NOT NULL, flow TEXT NOT NULL,
                stage TEXT NOT NULL, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(profile, account, payment_account, credential_id, flow))''')
            con.commit()
            return con

    async def pending(self, profile, account):
        return await asyncio.to_thread(self._pending, profile, account)

    def _pending(self, profile, account):
        con = self.connect()
        try:
            row = con.execute("""SELECT * FROM card_http_intents WHERE profile=? AND account=?
                AND phase IN ('SUBMITTED', 'SUBMITTED_UNVERIFIED', 'ACTION_REQUIRED', 'VERIFYING')""",
                (profile, account)).fetchone()
            return dict(row) if row else None
        finally:
            con.close()

    async def submit(self, profile, account, card_id, attempt_id, proof=None):
        await asyncio.to_thread(self._submit, profile, account, card_id, attempt_id, proof or {})

    def _submit(self, profile, account, card_id, attempt_id, proof):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            safe = {k:proof[k] for k in ('account_id','business_id','payment_account_id','payment_account_node_id',
                'account_scope_verified','last4','expected_card_type','preexisting_credential_ids') if k in proof}
            con.execute('INSERT INTO card_http_intents(attempt_id,profile,account,card_id,phase,result,updated_at) VALUES(?,?,?,?,?,?,?)',
                (attempt_id, profile, account, card_id, 'SUBMITTED', json.dumps(safe), int(time.time())))
            con.commit()
        except sqlite3.IntegrityError:
            con.rollback()
            raise ValueError('CARD_BINDING_RECONCILE_REQUIRED') from None
        finally:
            con.close()

    async def finish(self, attempt_id, result):
        # Display error messages have already removed card fields, URLs and tokens.
        # Never persist inputs, response payloads, PTT or bank parameters.
        safe = {k: result[k] for k in ('account_id', 'business_id', 'payment_account_id',
            'payment_account_node_id', 'status', 'code', 'submitted', 'retry_blocked',
            'account_scope_verified', 'save_response_stage', 'verification_stage',
            'meta_error_codes', 'meta_error_messages') if k in result}
        credential = result.get('credential')
        if result.get('card_confirmation_status') in {'REQUIRED', 'CLEAR', 'UNKNOWN'}:
            safe['card_confirmation_status'] = result['card_confirmation_status']
        if isinstance(result.get('verification_tasks'), list):
            safe['verification_tasks'] = sorted({task for task in result['verification_tasks']
                if isinstance(task, str) and task in {'statement_code', 'bank_app', 'three_ds', 'cvv', 'meta_action'}})
        if isinstance(credential, dict):
            safe['credential'] = {k: credential[k] for k in ('id', 'credential_id', 'type', 'last4') if k in credential}
        phase = 'LINKED' if result.get('status') == 'LINKED' else 'ACTION_REQUIRED' if result.get('status') == 'ACTION_REQUIRED' else 'SUBMITTED_UNVERIFIED'
        await asyncio.to_thread(self._finish, attempt_id, phase, safe)

    async def confirmed(self, profile, account, card_id):
        def read():
            con = self.connect()
            try:
                return con.execute("SELECT 1 FROM card_http_intents WHERE profile=? AND account=? AND card_id=? AND phase='LINKED' LIMIT 1",
                    (profile, account, card_id)).fetchone() is not None
            finally:
                con.close()
        return await asyncio.to_thread(read)

    async def confirmed_intent(self, profile, account, card_id):
        def read():
            con = self.connect()
            try:
                row = con.execute("SELECT * FROM card_http_intents WHERE profile=? AND account=? AND card_id=? AND phase='LINKED' ORDER BY updated_at DESC LIMIT 1",
                    (profile, account, card_id)).fetchone()
                return dict(row) if row else None
            finally:
                con.close()
        return await asyncio.to_thread(read)

    async def source_read_intent(self, profile, account):
        """Known payment scope for explicit read-only source maintenance.

        A fresh exact-RK/BM methods response is still required before using
        loader maps. This method cannot initiate a financial action.
        """
        def read():
            con = self.connect()
            try:
                row = con.execute("SELECT * FROM card_http_intents WHERE profile=? AND account=? AND phase IN ('LINKED','ACTION_REQUIRED') ORDER BY updated_at DESC LIMIT 1",
                    (profile, account)).fetchone()
                return dict(row) if row else None
            finally:
                con.close()
        return await asyncio.to_thread(read)

    async def review_empty(self, attempt_id, profile, account, card_id):
        """Retain a stale attempt's history after an explicit reviewed retry.

        The caller must freshly prove the exact unfiltered collection empty.
        No pending bank verification or competing attempt can be superseded.
        """
        def write():
            con = self.connect()
            try:
                con.execute('BEGIN IMMEDIATE')
                changed = con.execute("UPDATE card_http_intents SET phase='REVIEWED_EMPTY',updated_at=? WHERE attempt_id=? AND profile=? AND account=? AND card_id=? AND phase IN ('SUBMITTED','SUBMITTED_UNVERIFIED','VERIFYING') AND updated_at<=?",
                    (int(time.time()),attempt_id,profile,account,card_id,int(time.time())-180)).rowcount
                if changed != 1:
                    raise ValueError('CARD_BINDING_CHANGED')
                con.commit()
            except Exception:
                con.rollback()
                raise
            finally:
                con.close()
        await asyncio.to_thread(write)

    def _finish(self, attempt_id, phase, safe):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            existing = con.execute('SELECT result FROM card_http_intents WHERE attempt_id=?', (attempt_id,)).fetchone()
            if existing:
                safe = {**json.loads(existing['result']), **safe}
            con.execute('UPDATE card_http_intents SET phase=?,result=?,updated_at=? WHERE attempt_id=?',
                (phase, json.dumps(safe, separators=(',', ':')), int(time.time()), attempt_id))
            con.commit()
        finally:
            con.close()


class BankVerificationLedger(CardIntentLedger):
    """Persist a *single* authorization attempt across workers and restarts.

    A request with an ambiguous outcome remains reserved. Neither timeout nor
    a repeated button click can start another authorization for the same
    credential/RK/flow. No card data, CVC, bank URL or code is persisted.
    """

    @staticmethod
    def _validate(profile, account, payment_account, credential, card_id, flow):
        import re
        if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', profile):
            raise ValueError('VERIFY_INVALID_PROFILE')
        for item in (account, payment_account):
            if not isinstance(item, str) or not re.fullmatch(r'\d{5,30}', item):
                raise ValueError('VERIFY_INVALID_SCOPE')
        if not isinstance(credential, str) or not re.fullmatch(r'[A-Za-z0-9_:+-]{1,200}', credential):
            raise ValueError('VERIFY_INVALID_CREDENTIAL')
        if not isinstance(card_id, str) or not re.fullmatch(r'card_[a-f0-9]{24}', card_id):
            raise ValueError('VERIFY_INVALID_CARD')
        if flow not in ('SDC', 'THREEDS'):
            raise ValueError('VERIFY_INVALID_FLOW')

    async def reserve(self, *, profile, account, payment_account, credential,
                      card_id, flow):
        self._validate(profile, account, payment_account, credential, card_id, flow)
        return await asyncio.to_thread(self._reserve, profile, account,
            payment_account, credential, card_id, flow)

    def _reserve(self, profile, account, payment_account, credential, card_id, flow):
        import secrets
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            attempt_id = secrets.token_hex(12)
            now = int(time.time())
            inserted = con.execute('''INSERT OR IGNORE INTO card_bank_verification_intents
                (attempt_id,profile,account,payment_account,credential_id,card_id,flow,stage,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (attempt_id,profile,account,payment_account,credential,card_id,flow,
                 'RESERVED',now,now)).rowcount
            row = con.execute('''SELECT attempt_id,stage,card_id
                FROM card_bank_verification_intents
                WHERE profile=? AND account=? AND payment_account=?
                  AND credential_id=? AND flow=?''',
                (profile,account,payment_account,credential,flow)).fetchone()
            con.commit()
            if row is None:
                raise RuntimeError('VERIFY_RESERVATION_NOT_FOUND')
            return {'attempt_id': row['attempt_id'],
                    'stage': row['stage'], 'reserved': inserted == 1,
                    'same_card': row['card_id'] == card_id}
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    async def mark(self, attempt_id, stage):
        if not isinstance(attempt_id, str) or len(attempt_id) != 24:
            raise ValueError('VERIFY_ATTEMPT_INVALID')
        if stage not in ('REQUEST_SENT', 'RESULT_UNKNOWN',
                         'CHALLENGE_READY', 'SERVER_REJECTED', 'META_CONFIRMED'):
            raise ValueError('VERIFY_STAGE_INVALID')
        return await asyncio.to_thread(self._mark, attempt_id, stage)

    def _mark(self, attempt_id, stage):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            previous = ('RESERVED',) if stage == 'REQUEST_SENT' else (
                ('REQUEST_SENT',) if stage in ('RESULT_UNKNOWN', 'CHALLENGE_READY',
                                             'SERVER_REJECTED') else
                ('CHALLENGE_READY', 'RESULT_UNKNOWN'))
            changed = con.execute('''UPDATE card_bank_verification_intents
                SET stage=?,updated_at=?
                WHERE attempt_id=? AND stage IN (''' +
                ','.join('?' for _ in previous) + ')',
                (stage,int(time.time()),attempt_id,*previous)).rowcount
            con.commit()
            return changed == 1
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()
