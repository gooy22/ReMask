import asyncio
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from app.payment_card_bank import (
    INIT_DOC, VERIFY_DOC, bank_page_proof, start_bank_once, verify_bank_result, _challenge,
    _response_diagnostic, _client_success,
)
from app.payment_card_intents import BankVerificationLedger


class BankCardTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = SimpleNamespace(path=str(Path(self.tmp.name)/'state.sqlite'))
        self.scope = dict(state=self.state, profile='16', account='123456789',
            payment_account='555666777', business='987654321', credential='actual_action', card_id='card_'+'f'*24)
        self.common = dict(bank_page_verified=True, submitted=False, verification_triggered=False)

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_observed_callers_have_exact_pinned_hashes(self):
        source = (Path(__file__).parent/'fixtures/meta_bank_native_observed_20261010.js').read_text()
        hashes = {
            'useBillingRiskInitThreeDSMutation':'f358e2c37e27e2d2c7edcfce45983486abf02ce79052bae4a4b4c8819296e5da',
            'useBillingRiskVerifyThreeDSMutation':'52da159e1e18ce64dab0616a22b01db3b6445d5d055f1c693819ec4dde9f0475',
            'BillingRiskVerifyThreeDSPageViewManager.react':'398e071fcb2c69dc745c71a084502fb765c79344cac1e7a35abdf3f65fd93aaf'}
        for name, expected in hashes.items():
            row = next(line for line in source.splitlines() if line.startswith('__d("'+name+'"'))
            self.assertEqual(hashlib.sha256(row.encode()).hexdigest(), expected)

    def web(self, response):
        mock = AsyncMock(return_value=response)
        async def graphql(*args, **kw):
            if kw.get('before_submit'):
                await kw['before_submit']()
            return await mock(*args, **kw)
        return SimpleNamespace(graphql=graphql, _bootstrap=SimpleNamespace(fb_dtsg='fixture_secret')), mock

    async def test_scope_and_currency_must_be_fresh_before_any_bank_mutation(self):
        good = {'data':{'payment_account':{'payment_legacy_account_id':'555666777',
            'billable_account':{'currency':'EUR'}},'node':{'id':'relay_node','credential_id':'actual_action'}}}
        web, _ = self.web(good)
        args = dict(payment='555666777', business='987654321', node='relay_node', credential='actual_action')
        self.assertEqual((await bank_page_proof(web, **args))['currency'], 'EUR')
        for bad in ('foreign_credential', 'relay_node'):
            self.assertIsNone(await bank_page_proof(web, **{**args,'credential':bad}))
        self.assertIsNone(await bank_page_proof(web, **{**args,'payment':'111222333'}))

    async def test_init_once_no_amount_or_sdc_and_second_rk_has_separate_guard(self):
        response = {'data':{'risk_init_credential_verification':{
            'client_result':{'__typename':'XFBIBillingClientSuccess'},
            'authentication_request':{'url':'https://www.facebook.com/fixture/bank',
                                      'params':[{'key':'fixture','value':'issuer_value'}]}}}}
        web, mock = self.web(response)
        results = await asyncio.gather(*(start_bank_once(web, **self.scope, currency='EUR', common=self.common) for _ in range(8)))
        self.assertEqual(mock.await_count, 1)
        challenge = next(r for r in results if r['code']=='BANK_AUTH_CHALLENGE_READY')
        self.assertTrue(challenge['verification_triggered'])
        self.assertEqual(mock.await_args.args[0], INIT_DOC)
        self.assertEqual(mock.await_args.args[1], {'input': {
            'account_currency':'EUR','authentication_response_type':'REDIRECT',
            'authentication_response_url':'','credential_id':'actual_action',
            'payment_account_id':'555666777','payment_item_type':'MOR_ADS_INVOICE'}})
        again = await start_bank_once(web, **self.scope, currency='EUR', common=self.common)
        self.assertEqual(again['code'],'BANK_AUTH_ALREADY_ATTEMPTED')
        other = {**self.scope, 'profile':'17', 'account':'777888999','payment_account':'111222333'}
        self.assertEqual((await start_bank_once(web, **other, currency='EUR', common=self.common))['code'], 'BANK_AUTH_CHALLENGE_READY')
        self.assertEqual(mock.await_count,2)
        self.assertNotIn(b'fixture_secret', Path(self.state.path).read_bytes())
        self.assertNotIn(b'issuer_value', Path(self.state.path).read_bytes())

    async def test_transport_unknown_cannot_reinit(self):
        web, mock = self.web({})
        first = await start_bank_once(web, **self.scope, currency='EUR', common=self.common)
        self.assertEqual(first['code'],'BANK_AUTH_RESULT_UNKNOWN')
        self.assertTrue(first['submitted'])
        self.assertEqual(first['bank_response_kind'], 'MISSING_RESULT')
        self.assertIsNone(first['verification_triggered'])
        await start_bank_once(web, **self.scope, currency='EUR', common=self.common)
        self.assertEqual(mock.await_count,1)

    def test_unknown_response_diagnostics_distinguish_missing_step_without_secrets(self):
        field = 'risk_init_credential_verification'
        cases = [
            ({'errors':[{'message':'private_token','extensions':{'code':123}}]}, 'GRAPHQL_ERROR'),
            ({'hasNext':True}, 'INCOMPLETE_RESPONSE'),
            ({}, 'MISSING_RESULT'),
            ({'data':{field:{'client_result':{'__typename':'private_token'}}}}, 'CLIENT_RESULT_NOT_SUCCESS'),
            ({'data':{field:{'client_result':{'__typename':'XFBIBillingClientSuccess'},
                'authentication_request':{'url':'https://issuer.example/private_token','params':[]}}}}, 'CHALLENGE_UNAVAILABLE')]
        for response, kind in cases:
            result = _response_diagnostic(response, field, initializing=True)
            self.assertEqual(result['bank_response_kind'], kind)
            self.assertNotIn('private_token', str(result))
        self.assertEqual(_response_diagnostic(cases[0][0], field)['meta_error_codes'], [123])

    async def test_relay_abstract_success_discriminator_accepts_concrete_subtype(self):
        # The observed init/verify operation uses an abstract InlineFragment:
        # type XFBIBillingClientSuccess, abstractKey __isXFBIBillingClientSuccess.
        client = {'__typename':'ConcreteSuccessFixture',
                  '__isXFBIBillingClientSuccess':'ConcreteSuccessFixture'}
        self.assertTrue(_client_success({'client_result':client}))
        self.assertFalse(_client_success({'client_result':{'__typename':'ConcreteSuccessFixture'}}))
        self.assertFalse(_client_success({'client_result':{**client,
            '__isXFBIBillingClientSuccess':'ForeignTypeFixture'}}))
        self.assertFalse(_client_success({'client_result':{**client,'next_tasks':[{}]}}))
        web, _ = self.web({'data':{'risk_init_credential_verification':{
            'client_result':client,'authentication_request':{
                'url':'https://www.facebook.com/fixture/bank','params':[]}}}})
        result = await start_bank_once(web, **self.scope, currency='EUR', common=self.common)
        self.assertEqual(result['code'], 'BANK_AUTH_CHALLENGE_READY')
        web, _ = self.web({'data':{'validate_risk_credential_verification':{
            'verified':True,'client_result':client}}})
        result = await verify_bank_result(web, **self.scope, common=self.common)
        self.assertEqual(result['code'], 'BANK_CARD_VERIFIED_BY_META')

    async def test_validate_never_initializes_and_only_exact_meta_success_confirms(self):
        web, mock = self.web({'data':{'validate_risk_credential_verification':{
            'verified':True,'client_result':{'__typename':'XFBIBillingClientSuccess'}}}})
        result = await verify_bank_result(web, **self.scope, common=self.common)
        self.assertEqual(result['code'],'BANK_CARD_VERIFIED_BY_META')
        self.assertEqual(mock.await_args.args[0], VERIFY_DOC)
        self.assertEqual(mock.await_args.args[1], {'input':{
            'credential_id':'actual_action','payment_legacy_account_id':'555666777'},'paymentAccountID':'555666777'})
        for response in ({}, {'errors':[{'message':'fixture'}]},
                {'data':{'validate_risk_credential_verification':{'verified':False}}},
                {'data':{'validate_risk_credential_verification':{'verified':True,'client_result':{
                    '__typename':'XFBIBillingClientSuccess','next_tasks':[{}]}}}}):
            web, _ = self.web(response)
            self.assertNotEqual((await verify_bank_result(web, **self.scope, common=self.common))['status'],'VERIFIED')

    def test_meta_csrf_never_sent_to_issuer_or_untrusted_url(self):
        for url in ('https://issuer.example/challenge','https://evilfacebook.com/','javascript:alert(1)',
                    'https://www.facebook.com@evil.example/','https://www.facebook.com:8443/'):
            self.assertIsNone(_challenge({'url':url,'params':[]},'secret'))

    def test_observed_page_supports_nullable_params_and_values(self):
        url = 'https://www.facebook.com/fixture/bank'
        self.assertEqual(_challenge({'url':url,'params':None}, 'secret'), {'url':url,'params':[]})
        self.assertEqual(_challenge({'url':url,'params':[{'key':'optional','value':None}]}, 'secret'),
            {'url':url,'params':[{'key':'optional','value':''},{'key':'fb_dtsg','value':'secret'}]})
