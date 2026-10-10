"""Bank authorization reservations are durable and never blindly retried."""
import asyncio
import tempfile
from pathlib import Path
import unittest

from app.payment_card_intents import BankVerificationLedger


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
