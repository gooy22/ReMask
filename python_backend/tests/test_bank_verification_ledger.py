"""Bank authorization reservations are durable and never blindly retried."""
import asyncio
import tempfile
import sqlite3
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import unittest

from app.payment_card_intents import BankVerificationLedger, CardIntentLedger


def reserve_in_worker(path, scope):
    return BankVerificationLedger(path)._reserve(
        scope['profile'], scope['account'], scope['payment_account'],
        scope['credential'], scope['card_id'], scope['flow'])


class BankVerificationLedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'state.sqlite'
        self.ledger = BankVerificationLedger(self.db)
        self.kwargs = dict(profile='16', account='120247991367350146',
                           payment_account='1483335817184793',
                           credential='credential_test_123',
                           card_id='card_' + 'b' * 24, flow='SDC')

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_concurrent_requests_reserve_exactly_one_authorization(self):
        values = await asyncio.gather(*(self.ledger.reserve(**self.kwargs) for _ in range(12)))
        self.assertEqual(sum(x['reserved'] for x in values), 1)
        self.assertEqual(len({x['attempt_id'] for x in values}), 1)
        self.assertTrue(all(x['same_card'] for x in values))
        first = values[0]['attempt_id']
        self.assertTrue(await self.ledger.mark(first, 'REQUEST_SENT'))
        self.assertTrue(await self.ledger.mark(first, 'RESULT_UNKNOWN'))
        self.assertFalse(await self.ledger.mark(first, 'REQUEST_SENT'))
        replay = await BankVerificationLedger(self.db).reserve(**self.kwargs)
        self.assertFalse(replay['reserved'])
        self.assertEqual(replay['stage'], 'RESULT_UNKNOWN')
        self.assertEqual(replay['attempt_id'], first)

    async def test_mixed_flows_across_processes_share_one_durable_reservation(self):
        scopes = [{**self.kwargs, 'flow': 'SDC' if i % 2 else 'THREEDS'} for i in range(12)]
        loop = asyncio.get_running_loop()
        # The Linux CI runner invokes discovery from stdin. Fork does not need
        # to import that non-file __main__, and still tests separate workers.
        with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context('fork')) as workers:
            values = await asyncio.gather(*(loop.run_in_executor(
                workers, reserve_in_worker, self.db, scope) for scope in scopes))
        self.assertEqual(sum(x['reserved'] for x in values), 1)
        self.assertEqual(len({x['attempt_id'] for x in values}), 1)
        for flow in ('SDC', 'THREEDS'):
            value = await BankVerificationLedger(self.db).reserve(**{**self.kwargs, 'flow': flow})
            self.assertFalse(value['reserved'])

    async def test_every_prior_stage_blocks_switching_flow(self):
        for index, stage in enumerate(('RESERVED', 'REQUEST_SENT', 'RESULT_UNKNOWN', 'CHALLENGE_READY',
                                      'SERVER_REJECTED', 'META_CONFIRMED')):
            scope = {**self.kwargs, 'account': str(123450000 + index)}
            first = await self.ledger.reserve(**scope)
            if stage != 'RESERVED':
                await self.ledger.mark(first['attempt_id'], 'REQUEST_SENT')
                if stage == 'META_CONFIRMED':
                    await self.ledger.record_meta_confirmation(**scope)
                elif stage != 'REQUEST_SENT':
                    await self.ledger.mark(first['attempt_id'], stage)
            existing = await self.ledger.read_existing(**{**scope, 'flow': 'THREEDS'})
            self.assertEqual(existing['stage'], stage)
            self.assertFalse(existing['same_flow'])
            self.assertFalse((await self.ledger.reserve(**{**scope, 'flow': 'THREEDS'}))['reserved'])

    async def test_changed_credential_cannot_reauthorize_same_vault_card(self):
        first = await self.ledger.reserve(**self.kwargs)
        alias = {**self.kwargs, 'credential': 'new_action_credential', 'flow': 'THREEDS'}
        blocked = await self.ledger.reserve(**alias)
        self.assertFalse(blocked['reserved'])
        self.assertTrue(blocked['same_card'])
        self.assertFalse(blocked['same_credential'])
        self.assertEqual(blocked['attempt_id'], first['attempt_id'])
        self.assertFalse(await self.ledger.record_meta_confirmation(**alias))

    async def test_legacy_duplicate_history_survives_and_cannot_submit_or_retry(self):
        # Build the old production schema before the new triggers exist.
        with sqlite3.connect(self.db) as con:
            con.execute('''CREATE TABLE card_bank_verification_intents (
                attempt_id TEXT PRIMARY KEY, profile TEXT NOT NULL, account TEXT NOT NULL,
                payment_account TEXT NOT NULL, credential_id TEXT NOT NULL,
                card_id TEXT NOT NULL, flow TEXT NOT NULL, stage TEXT NOT NULL,
                created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                UNIQUE(profile,account,payment_account,credential_id,flow))''')
            for flow, attempt in (('SDC', 'a'*24), ('THREEDS', 'b'*24)):
                con.execute('INSERT INTO card_bank_verification_intents VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (attempt,self.kwargs['profile'],self.kwargs['account'],
                     self.kwargs['payment_account'],self.kwargs['credential'],
                     self.kwargs['card_id'],flow,'RESERVED',1,1))
        for flow in ('SDC', 'THREEDS'):
            result = await self.ledger.reserve(**{**self.kwargs, 'flow': flow})
            self.assertFalse(result['reserved'])
            self.assertEqual(result['stage'], 'HISTORY_CONFLICT')
            self.assertEqual(result['history_count'], 2)
        self.assertFalse(await self.ledger.mark('a'*24, 'REQUEST_SENT'))
        self.assertFalse(await self.ledger.mark('b'*24, 'REQUEST_SENT'))
        # A verified response can update only its own flow, without deleting or
        # promoting the unknown other flow and without clearing the fence.
        self.assertTrue(await self.ledger.record_meta_confirmation(**self.kwargs))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT flow,stage FROM card_bank_verification_intents ORDER BY flow').fetchall(),
                [('SDC','META_CONFIRMED'), ('THREEDS','RESERVED')])
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE card_bank_verification_intents SET stage='REQUEST_SENT' WHERE flow='THREEDS'")

    async def test_database_fence_blocks_legacy_insert_even_without_dispatcher(self):
        await self.ledger.reserve(**self.kwargs)
        con = self.ledger.connect()
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute('''INSERT OR IGNORE INTO card_bank_verification_intents
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', ('d'*24, self.kwargs['profile'],
                    self.kwargs['account'],self.kwargs['payment_account'],
                    self.kwargs['credential'],self.kwargs['card_id'],'THREEDS','RESERVED',2,2))
        finally:
            con.close()

    async def test_confirmation_in_other_flow_never_promotes_prior_attempt(self):
        first = await self.ledger.reserve(**self.kwargs)
        await self.ledger.mark(first['attempt_id'], 'REQUEST_SENT')
        await self.ledger.mark(first['attempt_id'], 'RESULT_UNKNOWN')
        self.assertTrue(await self.ledger.record_meta_confirmation(**{**self.kwargs, 'flow':'THREEDS'}))
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute('SELECT flow,stage FROM card_bank_verification_intents ORDER BY flow').fetchall(),
                [('SDC','RESULT_UNKNOWN'), ('THREEDS','META_CONFIRMED')])
        self.assertFalse((await self.ledger.reserve(**self.kwargs))['reserved'])

    async def test_independent_exact_payment_scopes_do_not_share_confirmation(self):
        await self.ledger.record_meta_confirmation(**self.kwargs)
        for key, value in (('profile','17'), ('account','777888999'), ('payment_account','999888777')):
            scope = {**self.kwargs, key:value, 'flow':'THREEDS'}
            self.assertIsNone(await self.ledger.read_existing(**scope))
            self.assertTrue((await self.ledger.reserve(**scope))['reserved'])

    async def test_different_vault_card_cannot_claim_an_existing_authorization(self):
        first = await self.ledger.reserve(**self.kwargs)
        conflicting = await self.ledger.reserve(
            **{**self.kwargs, 'card_id':'card_'+'c'*24})
        self.assertFalse(conflicting['reserved'])
        self.assertFalse(conflicting['same_card'])
        self.assertEqual(first['attempt_id'], conflicting['attempt_id'])

    async def test_rejects_unsafe_scope_and_does_not_store_payment_secrets(self):
        for key,value in (('account','act_123'),('payment_account','123'),
                          ('credential','../payments'),('flow','ADD_FUNDS'),
                          ('card_id','4111111111111111')):
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.ledger.reserve(**{**self.kwargs,key:value})
        result = await self.ledger.reserve(**self.kwargs)
        self.assertTrue(result['reserved'])
        data = self.db.read_bytes()
        self.assertNotIn(b'4111111111111111',data)
        self.assertNotIn(b'bank_url',data)

    async def test_external_meta_confirmation_blocks_future_authorizations(self):
        recorded = await self.ledger.record_meta_confirmation(**self.kwargs)
        self.assertTrue(recorded)
        repeat = await self.ledger.reserve(**self.kwargs)
        self.assertFalse(repeat['reserved'])
        self.assertEqual(repeat['stage'], 'META_CONFIRMED')
        self.assertTrue(await self.ledger.record_meta_confirmation(**self.kwargs))
        collision = await self.ledger.record_meta_confirmation(
            **{**self.kwargs, 'card_id': 'card_'+'c'*24})
        self.assertFalse(collision)
        self.assertEqual((await self.ledger.reserve(**self.kwargs))['stage'], 'META_CONFIRMED')

    async def test_sent_authorization_transitions_to_confirmed_without_secrets(self):
        first = await self.ledger.reserve(**self.kwargs)
        self.assertTrue(await self.ledger.mark(first['attempt_id'], 'REQUEST_SENT'))
        self.assertTrue(await self.ledger.mark(first['attempt_id'], 'CHALLENGE_READY'))
        self.assertTrue(await self.ledger.record_meta_confirmation(**self.kwargs))
        self.assertEqual((await self.ledger.reserve(**self.kwargs))['stage'],'META_CONFIRMED')
        self.assertNotIn(b'METAPAY',self.db.read_bytes())


    async def test_retained_payment_scope_can_be_selected_for_the_exact_old_card(self):
        ledger=CardIntentLedger(self.db)
        older='card_'+'a'*24
        newer='card_'+'b'*24
        for card,attempt in ((older,'a'*24),(newer,'b'*24)):
            await ledger.submit('16','120247991367350146',card,attempt)
            await ledger.finish(attempt,{'status':'LINKED','account_id':'120247991367350146'})
        row=await ledger.source_read_intent('16','120247991367350146',older)
        self.assertEqual(row['card_id'],older)
        self.assertIsNone(await ledger.source_read_intent('17','120247991367350146',older))
