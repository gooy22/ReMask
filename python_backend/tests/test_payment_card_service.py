import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.payment_card_intents import CardIntentLedger
from app.payment_card_service import profile_payment_card_http
from app.payment_card_input import build_client_info
from tests.test_payment_card_http import FakeHTTP, read_screen
from tests.test_payment_ptt import VALUES
from tests.test_static_payment_card import ACCOUNT, BUSINESS, PAYMENT


class ServiceHTTP(FakeHTTP):
    async def graphql(self, doc, variables, **kwargs):
        if doc == '28388533884149241':
            self.calls.append((doc, copy.deepcopy(variables), kwargs))
            payload = read_screen()
            payload['data']['payment_account']['payment_legacy_account_id'] = PAYMENT
            payload['data']['payment_account']['billable_account']['currency'] = 'USD'
            return payload
        result = await super().graphql(doc, variables, **kwargs)
        if doc == '28814526004898205':
            for row in result['data']['billable_account_by_asset_id']['billing_payment_account']['billing_payment_methods_allowlist_customized']:
                row['credential']['needs_verification'] = self.bank_required
        return result


class CardServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = SimpleNamespace(path=Path(self.tmp.name)/'state.sqlite', set_payment_link_state=AsyncMock())
        self.resolver = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user':'111999'})))
        self.web = ServiceHTTP()
        self.session = SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.cm = AsyncMock()
        self.cm.__aenter__.return_value = self.session
        self.payload = {'operation':'bind', 'account_id':ACCOUNT, 'card_id':'card_'+'a'*24,
            'attempt_id':'b'*24, 'card':{k:v for k,v in VALUES.items() if k != 'cvv'}, 'cvv':VALUES['cvv'],
            'client_info':build_client_info(color_depth=24, viewport_width=1440, viewport_height=900)}

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def call(self, payload=None, *, profile='15'):
        # Fixtures replace the live Meta transport while retaining the exact
        # profile/RK resolution, card intent, and save-then-verify boundaries.
        effective = {**(payload or self.payload), 'account_id':ACCOUNT}
        with patch('app.payment_card_service.resolve_payment_asset', AsyncMock(return_value={'business_id':BUSINESS})), \
             patch('app.session.ProfileSession', return_value=self.cm), \
             patch('app.payment_card_http.encrypt_card_token', return_value='synthetic_token'):
            return await profile_payment_card_http(self.resolver,profile,effective,state=self.state)

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

    async def test_preferred_country_is_applied_and_verified_before_card_save(self):
        from tests.test_payment_country_setup import CountryHTTP
        from app.payment_country_setup import UPDATE_DOC
        from app.payment_card_http import SAVE_DOC_ID
        self.web=CountryHTTP();self.session.facebook_web.return_value=self.web
        result=await self.call({**self.payload,'billing_setup':{'country':'UA','country_mode':'prefer_ua'}})
        self.assertEqual(result['status'],'LINKED')
        docs=[c[0] for c in self.web.calls]
        self.assertLess(docs.index(UPDATE_DOC),docs.index(SAVE_DOC_ID))
        save=next(c for c in self.web.calls if c[0]==SAVE_DOC_ID)
        self.assertEqual(save[1]['input']['billing_address']['country_code'],'UA')

    async def test_unconfirmed_country_update_stops_before_save_and_card_ledger_submit(self):
        from tests.test_payment_country_setup import CountryHTTP
        from app.payment_card_http import SAVE_DOC_ID
        self.web=CountryHTTP();self.web.readback_wrong=True;self.session.facebook_web.return_value=self.web
        result=await self.call({**self.payload,'billing_setup':{'country':'UA','country_mode':'prefer_ua'}})
        self.assertEqual(result['code'],'CARD_COUNTRY_UPDATE_VERIFY_PENDING')
        self.assertFalse(result['submitted']);self.assertNotIn(SAVE_DOC_ID,[c[0] for c in self.web.calls])
        self.state.set_payment_link_state.assert_not_awaited()

    async def test_lost_save_restart_never_replays(self):
        self.web.lose_save=True
        self.assertEqual((await self.call())['status'],'SUBMITTED_UNVERIFIED')
        self.web.calls.clear()
        result=await self.call({**self.payload,'attempt_id':'c'*24})
        self.assertEqual(result['code'],'CARD_LINK_CONFIRMED')
        self.assertEqual([x[0] for x in self.web.calls],['28797973873175785','28814526004898205','28797973873175785','24871928132404465'])
        self.assertEqual(result['status'],'LINKED')

    async def test_lost_verify_reconciles_exact_credential_without_card_or_cvv(self):
        self.web.lose_verification=True
        self.assertEqual((await self.call())['code'],'CARD_SAVE_LINK_VERIFICATION_PENDING')
        self.web.lose_verification=False
        self.web.calls.clear()
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'LINKED')
        self.assertEqual([x[0] for x in self.web.calls],['28797973873175785','28814526004898205','28797973873175785','24871928132404465'])

    async def test_failed_reconcile_reports_read_failure_without_replaying_save(self):
        self.web.lose_save=True
        await self.call()
        self.web.calls.clear()
        self.session.facebook_web.side_effect=TimeoutError('private response must not be exposed')
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED')
        self.assertTrue(result['retry_blocked'])
        self.assertEqual(result['reconcile_error_code'],'PAYMENT_HTTP_TIMEOUT')
        self.assertNotIn('private response',json.dumps(result))
        self.assertEqual(self.web.calls,[])

    async def test_lost_save_reply_recovers_nonprimary_card_from_unfiltered_collection(self):
        self.web.lose_save=True
        await self.call()
        original=self.web.graphql
        async def only_primary(doc, variables, **kwargs):
            if doc=='28814526004898205':
                from tests.test_payment_card_http import read_methods
                self.web.calls.append((doc,copy.deepcopy(variables),kwargs))
                return read_methods([])
            return await original(doc,variables,**kwargs)
        self.web.graphql=only_primary
        self.web.calls.clear()
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'LINKED')
        self.assertTrue(result['funding']['inventory_complete'])
        self.assertFalse(result['funding_verified'])
        self.assertNotIn('28619313357728847',[c[0] for c in self.web.calls])

    async def test_reviewed_retry_requires_fresh_complete_empty_and_keeps_old_attempt_history(self):
        import sqlite3, time
        self.web.lose_save=True
        await self.call()
        self.web.saved=False;self.web.lose_save=False
        con=sqlite3.connect(self.state.path)
        con.execute('UPDATE card_http_intents SET updated_at=?',(int(time.time())-240,))
        con.commit();con.close()
        result=await self.call({**self.payload,'attempt_id':'c'*24,'reviewed_attempt_id':'b'*24})
        self.assertEqual(result['status'],'LINKED')
        con=sqlite3.connect(self.state.path)
        phases=con.execute('SELECT phase FROM card_http_intents ORDER BY attempt_id').fetchall();con.close()
        self.assertEqual(phases,[('REVIEWED_EMPTY',),('LINKED',)])

    async def test_reviewed_retry_cannot_supersede_existing_card_or_pending_bank_action(self):
        self.web.lose_save=True
        await self.call()
        self.web.calls.clear()
        result=await self.call({**self.payload,'attempt_id':'c'*24,'reviewed_attempt_id':'b'*24})
        self.assertEqual(result['status'],'LINKED')
        self.assertNotIn('28619313357728847',[c[0] for c in self.web.calls])

    async def test_selected_profile_17_can_complete_without_profile_15_success(self):
        # Dynamic selected RK on profile 17 is independent of any other RK.
        result = await self.call(profile='17')
        self.assertEqual(result['status'], 'LINKED')
        self.assertTrue(result['submitted'])
        self.resolver.resolve.assert_awaited_once_with('17')
        confirmed = await CardIntentLedger(self.state.path).confirmed(
            '17', ACCOUNT, self.payload['card_id'])
        self.assertTrue(confirmed)

    async def test_new_profile_or_rk_reaches_durable_guard_without_allowlist(self):
        for profile, account in (
            ('17', '120251439661740682'),
            ('16', '120247991367350146'),
            ('41', '120251352568830122'),
        ):
            with self.subTest(profile=profile, account=account):
                result = await profile_payment_card_http(
                    self.resolver, profile, {'operation':'bind', 'account_id':account},
                    state=self.state)
                self.assertEqual(result['code'], 'CARD_DURABLE_INTENT_REQUIRED')
                self.assertFalse(result['submitted'])
        self.resolver.resolve.assert_not_awaited()

    async def test_unknown_selected_rk_never_submits_before_meta_scope_proof(self):
        with patch('app.payment_card_service.resolve_payment_asset', AsyncMock(return_value={})), \
             patch('app.session.ProfileSession', return_value=self.cm):
            result = await profile_payment_card_http(
                self.resolver, '17', self.payload, state=self.state)
        self.assertEqual(result['code'], 'PAYMENT_ACCOUNT_BINDING_MISSING')
        self.assertFalse(result['submitted'])
        self.assertEqual(self.web.calls, [])
        self.resolver.resolve.assert_not_awaited()

    async def test_foreign_business_scope_stops_dynamic_rk_before_save(self):
        self.web.foreign_business = True
        result = await self.call(profile='17')
        self.assertNotEqual(result['status'], 'LINKED')
        self.assertFalse(result['submitted'])
        self.assertNotIn('28619313357728847', [call[0] for call in self.web.calls])
        self.state.set_payment_link_state.assert_not_awaited()

    async def test_lost_reply_never_commits_foreign_business_or_ambiguous_new_credentials(self):
        self.web.lose_save=True
        await self.call()
        self.web.foreign_business=True
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertNotEqual(result['status'],'LINKED')
        self.state.set_payment_link_state.assert_not_awaited()
        self.web.foreign_business=False
        original=self.web.graphql
        async def ambiguous(doc, variables, **kwargs):
            result=await original(doc, variables, **kwargs)
            if doc=='28814526004898205':
                rows=result['data']['billable_account_by_asset_id']['billing_payment_account']['billing_payment_methods_allowlist_customized']
                other=copy.deepcopy(rows[0]);other['credential']['id']='other-card-node';rows.append(other)
            return result
        self.web.graphql=ambiguous
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertNotEqual(result['status'],'LINKED')
        self.state.set_payment_link_state.assert_not_awaited()

    async def test_bank_required_stays_pending_until_independent_verification_flag_clears(self):
        self.web.bank_required=True
        self.assertEqual((await self.call())['status'],'ACTION_REQUIRED')
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'ACTION_REQUIRED')
        self.state.set_payment_link_state.assert_not_awaited()
        self.web.bank_required=False
        result=await self.call({'operation':'reconcile','card_id':self.payload['card_id']})
        self.assertEqual(result['status'],'LINKED')

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

    async def test_public_reconciliation_finishes_lost_save_without_replaying_card_fields(self):
        from app.payment_card_binding import profile_payment_card
        self.web.lose_save = True
        with patch('app.payment_card_service.resolve_payment_asset', AsyncMock(return_value={'business_id':BUSINESS})), \
             patch('app.session.ProfileSession', return_value=self.cm), \
             patch('app.payment_card_http.encrypt_card_token', return_value='synthetic_token'):
            first = await profile_payment_card(self.resolver, '15', {**self.payload, 'account_id':ACCOUNT}, state=self.state)
            self.assertEqual(first['status'], 'SUBMITTED_UNVERIFIED')
            self.web.calls.clear()
            result = await profile_payment_card(self.resolver, '15', {'operation':'reconcile',
                'account_id':ACCOUNT, 'card_id':self.payload['card_id']}, state=self.state)
        self.assertEqual(result['status'], 'LINKED')
        self.assertEqual([call[0] for call in self.web.calls], ['28797973873175785','28814526004898205','28797973873175785','24871928132404465'])
        for call in self.web.calls:
            self.assertNotIn('synthetic_token', json.dumps(call[1]))
            self.assertNotIn(VALUES['number'], json.dumps(call[1]))

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

