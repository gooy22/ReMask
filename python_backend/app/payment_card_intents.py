"""Durable metadata-only guard at the card Save boundary."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path


class CardIntentLedger:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
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

    async def submit(self, profile, account, card_id, attempt_id):
        await asyncio.to_thread(self._submit, profile, account, card_id, attempt_id)

    def _submit(self, profile, account, card_id, attempt_id):
        con = self.connect()
        try:
            con.execute('BEGIN IMMEDIATE')
            con.execute('INSERT INTO card_http_intents(attempt_id,profile,account,card_id,phase,updated_at) VALUES(?,?,?,?,?,?)',
                (attempt_id, profile, account, card_id, 'SUBMITTED', int(time.time())))
            con.commit()
        except sqlite3.IntegrityError:
            con.rollback()
            raise ValueError('CARD_BINDING_RECONCILE_REQUIRED') from None
        finally:
            con.close()

    async def finish(self, attempt_id, result):
        # Never persist inputs, payloads, PTT, bank URLs or arbitrary error text.
        safe = {k: result[k] for k in ('account_id', 'business_id', 'payment_account_id',
            'payment_account_node_id', 'status', 'code', 'submitted', 'retry_blocked',
            'account_scope_verified') if k in result}
        credential = result.get('credential')
        if isinstance(credential, dict):
            safe['credential'] = {k: credential[k] for k in ('id', 'credential_id', 'type', 'last4') if k in credential}
        phase = 'LINKED' if result.get('status') == 'LINKED' else 'ACTION_REQUIRED' if result.get('status') == 'ACTION_REQUIRED' else 'SUBMITTED_UNVERIFIED'
        await asyncio.to_thread(self._finish, attempt_id, phase, safe)

    def _finish(self, attempt_id, phase, safe):
        con = self.connect()
        try:
            con.execute('UPDATE card_http_intents SET phase=?,result=?,updated_at=? WHERE attempt_id=?',
                (phase, json.dumps(safe, separators=(',', ':')), int(time.time()), attempt_id))
            con.commit()
        finally:
            con.close()
