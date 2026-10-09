import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.payment_card_intents import CardIntentLedger
from app.payment_card_service import profile_payment_card_http, CANARY_SCOPE
from app.payment_card_input import build_client_info
from tests.test_payment_card_http import FakeHTTP, read_screen
from tests.test_payment_ptt import VALUES
from tests.test_static_payment_card import BUSINESS, PAYMENT


class ServiceHTTP(FakeHTTP):
    async def graphql(self, doc, variables, **kwargs):
        if doc == '28388533884149241':
            self.calls.append((doc, copy.deepcopy(variables), kwargs))
            payload = read_screen()
            payload['data']['payment_account']['payment_legacy_account_id'] = PAYMENT
            payload['data']['payment_account']['billable_account']['currency'] = 'USD'
            return payload
        return await super().graphql(doc, variables, **kwargs)


class CardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = SimpleNamespace(path=Path(self.tmp.name)/'state.sqlite', set_payment_link_state=AsyncMock())
        self.resolver = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user':'111999'})))
        self.web = ServiceHTTP()
        self.session = SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.cm = AsyncMock()
        self.cm.__aenter__.return_value = self.session
        self.payload = {'operation':'bind', 'account_id':CANARY_SCOPE[1], 'card_id':'card_'+'a'*24,
            'attempt_id':'b'*24, 'card':{k:v for k,v in VALUES.items() if k != 'cvv'}, 'cvv':VALUES['cvv'],
            'client_info':build_client_info(color_depth=24, viewport_width=1440, viewport_height=900)}

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def call(self, payload=None):
        # Test engine scope uses fixture IDs; canary is explicitly isolated.
        from tests.test_static_payment_card import ACCOUNT
        effective = {**(payload or self.payload), 'account_id':ACCOUNT}
        with patch('app.payment_card_service.CANARY_SCOPE', ('15', ACCOUNT)), \
             patch('app.payment_card_service.resolve_payment_asset', AsyncMock(return_value={'business_id':BUSINESS})), \
             patch('app.session.ProfileSession', return_value=self.cm), \
             patch('app.payment_card_http.encrypt_card_token', return_value='synthetic_token'):
            return await profile_payment_card_http(self.resolver,'15',effective,state=self.state)

    async def test_button_envelope_to_save_to_exact_verification_and_durable_state(self):
        result = await self.call()
        self.assertEqual(result['status'],'LINKED')
        self.assertFalse(result['browser_started'])
        self.state.set_payment_link_state.assert_awaited_once()
        con = CardIntentLedger(self.state.path).connect()
        try:
            row = dict(con.execute('SELECT * FROM card_http_intents').fetchone())
        finally:
            con.close()
        self.assertEqual(row['phase'],'LINKED')
        for secret in (VALUES['number'], 'synthetic_token'):
            self.assertNotIn(secret, row['result'])
        self.assertNotIn('cvv', row['result']);self.assertNotIn('csc', row['result'])

    async def test_lost_save_restart_never_replays(self):
        self.web.lose_save=True
        self.assertEqual((await self.call())['status'],'SUBMITTED_UNVERIFIED')
        self.web.calls.clear()
        result=await self.call({**self.payload,'attempt_id':'c'*24})
        self.assertEqual(result['code'],'CARD_BINDING_RECONCILE_REQUIRED')
        self.assertEqual(self.web.calls,[])

    async def test_lost_verify_reconciles_exact_credential_without_card_or_cvv(self):
        self.web.lose_verification=True
        self.assertEqual((await self.call())['code'],'CARD_SAVE_LINK_VERIFICATION_PENDING')
        self.web.lose_verification=False
        self.web.calls.clear()
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'LINKED')
        self.assertEqual([x[0] for x in self.web.calls],['28797973873175785','28814526004898205'])

    async def test_no_attempt_or_client_context_never_opens_session(self):
        for missing, code in [('attempt_id','CARD_DURABLE_INTENT_REQUIRED'),('client_info','CARD_CLIENT_CONTEXT_REQUIRED')]:
            payload=dict(self.payload);payload.pop(missing)
            result=await self.call(payload)
            self.assertEqual(result['code'],code)
        self.assertEqual(self.web.calls,[])

    async def test_unknown_country_never_defaults_or_submits(self):
        with patch('app.payment_card_service.setup_context',side_effect=ValueError('PAYMENT_ACCOUNT_SETUP_REQUIRED')):
            result=await self.call()
        self.assertFalse(result['submitted'])
        self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SETUP_REQUIRED')
        self.assertFalse(any(x[0]=='28619313357728847' for x in self.web.calls))

    async def test_public_entry_never_calls_legacy_browser_executor(self):
        from app.payment_card_binding import profile_payment_card
        with patch('app.payment_card_service.profile_payment_card_http',AsyncMock(return_value={'status':'BLOCKED','code':'FIXTURE','submitted':False})) as http, \
             patch('app.payment_card_binding._profile_payment_card_execute',AsyncMock(side_effect=AssertionError('Browser forbidden'))) as browser:
            await profile_payment_card(self.resolver,'15',self.payload,state=self.state)
        http.assert_awaited_once();browser.assert_not_awaited()

    async def test_concurrent_submit_guards_and_restart(self):
        ledger=CardIntentLedger(self.state.path)
        results=await asyncio.gather(ledger.submit('15','111111','card_'+'a'*24,'a'*24),
            ledger.submit('15','111111','card_'+'b'*24,'b'*24),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
        pending=await CardIntentLedger(self.state.path).pending('15','111111')
        self.assertIsNotNone(pending)
        await ledger.finish(pending['attempt_id'],{'status':'SUBMITTED_UNVERIFIED','code':'CARD_SAVE_RESULT_UNKNOWN',
            'number':VALUES['number'],'cvv':VALUES['cvv'],'platform_trust_token':'synthetic_token'})
        stored=await ledger.pending('15','111111')
        self.assertEqual(set(json.loads(stored['result'])),{'status','code'})

