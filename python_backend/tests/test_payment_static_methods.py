import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from app.static_payment_read import account_proof, execute, inspect_methods, methods_proof, payment_page_proof
from app.payment_inspection import inspect_profile_payment_methods
from app.facebook_business_browser import BrowserBusinessError
from fb_worker import AuthenticationError

ACCOUNT, BM, PAYMENT = '123456789', '987654321', '555666777'
NODE, CRED = 'relay-payment-node', 'relay-card-node'

def account_response():
    return {'data': {'billable_account_by_asset_id': {'__typename': 'AdAccount', 'id': ACCOUNT,
        'billing_payment_account': {'payment_legacy_account_id': PAYMENT, 'id': NODE}}}}

def methods_response():
    credential = {'id': CRED, '__typename': 'ExternalCreditCard', 'card_association_name': 'VISA',
                  'last_four_digits': '1234', 'is_expired': False, 'needs_verification': True,
                  'supports_recurring': True, 'billing_address': 'PRIVATE ADDRESS', 'pan': '4111111111111111'}
    row = {'credential': credential}
    return {'data': {'billable_account_by_asset_id': {'__typename': 'AdAccount', 'id': ACCOUNT,
        'owning_business': {'id': BM}, 'billing_payment_account': {'id': NODE,
            'primary': [{'credential': {'id': CRED, '__typename': 'ExternalCreditCard'}}],
            'billing_payment_methods_allowlist_customized': [copy.deepcopy(row)],
            'primary_funding_source_customized': [copy.deepcopy(row)]}}}}

class StaticMethodsTests(unittest.IsolatedAsyncioTestCase):
    def test_server_driven_verification_tasks_override_legacy_false_without_leaking_parameters(self):
        value = methods_response()
        payment = value['data']['billable_account_by_asset_id']['billing_payment_account']
        for key in ('billing_payment_methods_allowlist_customized', 'primary_funding_source_customized'):
            card = payment[key][0]['credential']
            card['needs_verification'] = False
            card['required_tasks'] = [{'__typename': 'CVCOSoftDescriptorVerificationTask',
                'billing_task_name': 'risk_sdc_verification', 'verification_parameters': 'PRIVATE BANK DATA'}]
        proof = self.proof(value)
        card = proof['payment_methods'][0]
        self.assertEqual(card['verification_tasks'], ['statement_code'])
        self.assertEqual(card['card_confirmation_status'], 'REQUIRED')
        self.assertTrue(proof['card_linked']); self.assertFalse(proof['funding_verified'])
        self.assertNotIn('PRIVATE BANK DATA', json.dumps(proof))
        for key in ('billing_payment_methods_allowlist_customized', 'primary_funding_source_customized'):
            payment[key][0]['credential']['required_tasks'] = []
        self.assertEqual(self.proof(value)['payment_methods'][0]['card_confirmation_status'], 'CLEAR')
        del payment['primary_funding_source_customized'][0]['credential']['required_tasks']
        self.assertEqual(self.proof(value)['payment_methods'][0]['card_confirmation_status'], 'CLEAR')
        payment['billing_payment_methods_allowlist_customized'][0]['credential']['required_tasks'] = [
            {'__typename': 'ThreeDSVerificationTask'}]
        self.assertEqual(self.proof(value)['payment_methods'][0]['verification_tasks'], ['three_ds'])

    def proof(self, payload):
        return methods_proof(payload, ACCOUNT, business_id=BM, account_evidence=account_proof(account_response(), ACCOUNT))

    async def test_queries_use_exact_asset_and_confirmed_payment_identity_without_mutation(self):
        web = SimpleNamespace(graphql=AsyncMock(side_effect=[account_response(), methods_response()]))
        result = await inspect_methods(web, account=ACCOUNT, business_id=BM)
        self.assertEqual(web.graphql.await_count, 2)
        args, kwargs = web.graphql.await_args_list[1]
        self.assertEqual(args, ('28814526004898205', {'assetID': ACCOUNT, 'paymentAccountID': PAYMENT}))
        self.assertEqual(kwargs['business_context_id'], BM)
        self.assertEqual(len(result['payment_methods']), 1)
        self.assertTrue(result['card_linked']); self.assertEqual(result['verification_status'], 'LINKED')
        self.assertTrue(result['business_scope_verified']); self.assertTrue(result['payment_account_relation_verified'])
        self.assertFalse(result['funding_verified']); self.assertFalse(result['inventory_complete'])
        self.assertFalse(result['browser_started'])
        self.assertTrue(web.private_only)
        self.assertTrue(result['payment_methods'][0]['needs_verification'])
        self.assertNotIn('PRIVATE ADDRESS', json.dumps(result)); self.assertNotIn('4111111111111111', json.dumps(result))

    async def test_unconfirmed_account_never_sends_methods_query(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={'data': None}))
        result = await inspect_methods(web, account=ACCOUNT, business_id=BM)
        self.assertEqual(web.graphql.await_count, 1); self.assertIsNone(result['card_linked'])

    def test_foreign_owner_account_payment_or_incomplete_response_never_proves_linkage(self):
        variants = []
        value = methods_response(); value['errors'] = [{'message': 'private fixture'}]; variants.append(value)
        value = methods_response(); value['hasNext'] = True; variants.append(value)
        value = methods_response(); value['extensions'] = {'is_final': False}; variants.append(value)
        value = methods_response(); value['data']['billable_account_by_asset_id']['id'] = '111222333'; variants.append(value)
        value = methods_response(); value['data']['billable_account_by_asset_id']['owning_business']['id'] = '111222333'; variants.append(value)
        value = methods_response(); value['data']['billable_account_by_asset_id']['billing_payment_account']['id'] = 'foreign'; variants.append(value)
        value = methods_response(); del value['data']['billable_account_by_asset_id']['billing_payment_account']['primary']; variants.append(value)
        value = methods_response(); value['data']['billable_account_by_asset_id']['billing_payment_account']['primary'] = [None]; variants.append(value)
        for value in variants:
            with self.subTest(value=value):
                proof = self.proof(value)
                self.assertFalse(proof['account_scope_verified']); self.assertIsNone(proof['card_linked'])
                self.assertEqual(proof['payment_methods'], []); self.assertFalse(proof['inventory_complete'])

    def test_filtered_empty_is_not_absence_and_noncard_method_is_not_a_card(self):
        for rows in ([], [{'credential': {'id': 'paypal-node', '__typename': 'PaymentPaypalBillingAgreement'}}]):
            value = methods_response(); payment = value['data']['billable_account_by_asset_id']['billing_payment_account']
            payment['primary'] = []; payment['primary_funding_source_customized'] = []
            payment['billing_payment_methods_allowlist_customized'] = rows
            proof = self.proof(value)
            self.assertTrue(proof['methods_query_verified']); self.assertIsNone(proof['card_linked'])
            self.assertEqual(proof['verification_status'], 'UNVERIFIED'); self.assertFalse(proof['inventory_complete'])

    def test_distinct_credentials_with_same_mask_remain_distinct_and_conflict_fails_closed(self):
        value = methods_response(); payment = value['data']['billable_account_by_asset_id']['billing_payment_account']
        second = copy.deepcopy(payment['billing_payment_methods_allowlist_customized'][0])
        second['credential']['id'] = 'second-card'
        payment['billing_payment_methods_allowlist_customized'].append(second)
        self.assertEqual(len(self.proof(value)['payment_methods']), 2)
        second['credential']['id'] = CRED; second['credential']['last_four_digits'] = '9999'
        self.assertEqual(self.proof(value)['code'], 'PAYMENT_METHODS_CREDENTIAL_CONFLICT')
        value = methods_response(); value['data']['billable_account_by_asset_id']['billing_payment_account']['primary_funding_source_customized'][0]['credential']['last_four_digits'] = '4111111111111111'
        self.assertIsNone(self.proof(value)['card_linked'])

    async def test_setup_and_option_queries_are_static_read_only_and_identity_checked(self):
        payload = {'data': {'payment_account': {'payment_legacy_account_id': PAYMENT,
            'billable_account': {'__typename': 'AdAccount', 'id': ACCOUNT}}}}
        self.assertTrue(payment_page_proof(payload, ACCOUNT, PAYMENT))
        self.assertFalse(payment_page_proof(payload, ACCOUNT, '111222333'))
        web = SimpleNamespace(graphql=AsyncMock(return_value=payload))
        for operation, doc in (('READ_SETUP', '28388533884149241'), ('READ_OPTIONS', '29195809800004536')):
            await execute(web, operation, payment=PAYMENT, business_id=BM)
            args, kwargs = web.graphql.await_args
            self.assertEqual(args[0], doc); self.assertEqual(args[1]['paymentAccountID'], PAYMENT)
            self.assertFalse(args[1]['skipDeferredFragments']); self.assertNotIn('input', args[1])

class HttpInspectionTests(unittest.IsolatedAsyncioTestCase):
    async def run_inspection(self, result=None, error=None, business=BM):
        web = object()
        class Session:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def facebook_web(self): return web
            async def facebook_business_browser(self): raise AssertionError('No Chromium fallback')
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user': '111222333'})))
        state = SimpleNamespace(set_payment_link_state=AsyncMock())
        with patch('app.payment_inspection.resolve_payment_asset', AsyncMock(return_value={'business_id': business})), \
             patch('app.session.ProfileSession', return_value=Session()), \
             patch('app.static_payment_read.inspect_methods', AsyncMock(return_value=result, side_effect=error)):
            output = await inspect_profile_payment_methods(resolver, 'fixture', ACCOUNT, state=state)
        return output, state

    async def test_positive_link_is_committed_but_unconfirmed_read_preserves_previous_state(self):
        positive = methods_proof(methods_response(), ACCOUNT, business_id=BM, account_evidence=account_proof(account_response(), ACCOUNT))
        result, state = await self.run_inspection(positive)
        self.assertTrue(result['card_linked']); state.set_payment_link_state.assert_awaited_once()
        negative = {**positive, 'card_linked': None, 'payment_methods': [], 'verification_status': 'UNVERIFIED'}
        result, state = await self.run_inspection(negative)
        state.set_payment_link_state.assert_not_awaited()

    async def test_http_failure_and_auth_gate_never_open_browser_or_expose_response(self):
        checkpoint = AuthenticationError('checkpoint PRIVATE fixture')
        checkpoint.meta_payload = {'business_precheck': [{'auth_reason': 'checkpoint_redirect'}]}
        for error, code in ((RuntimeError('PRIVATE PAN fixture'), 'PAYMENT_HTTP_UNAVAILABLE'),
                            (AuthenticationError('login PRIVATE fixture'), 'SESSION_EXPIRED'),
                            (AuthenticationError('login/checkpoint PRIVATE fixture'), 'SESSION_EXPIRED'),
                            (checkpoint, 'CHECKPOINT_REQUIRED')):
            with self.subTest(code=code), self.assertRaises(BrowserBusinessError) as raised:
                await self.run_inspection(error=error)
            self.assertEqual(raised.exception.code, code); self.assertNotIn('PRIVATE', str(raised.exception))

    async def test_missing_binding_or_personal_business_stops_before_queries(self):
        for business, code in (('', 'PAYMENT_ACCOUNT_BINDING_MISSING'), ('111222333', 'PERSONAL_AD_ACCOUNT_EXCLUDED')):
            with self.subTest(code=code), self.assertRaises(ValueError) as raised:
                await self.run_inspection(business=business)
            self.assertEqual(str(raised.exception), code)
