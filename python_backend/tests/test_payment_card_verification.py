"""No-network contracts for the HTTP-only verification preflight."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.payment_card_verification import _card_identity, sdc_candidate_proof, verify_payment_card_http


class SessionFixture:
    def __init__(self, context, timeout_seconds=30):
        self.context = context
    async def __aenter__(self):
        return self
    async def __aexit__(self, *_):
        pass
    async def facebook_web(self):
        return object()


class CardVerificationHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.payload = {'operation': 'verify', 'account_id': '123456789',
                        'card_id': 'card_' + 'a' * 24, 'card_brand': 'Visa',
                        'card_last4': '1234'}
        self.resolver = SimpleNamespace(resolve=AsyncMock(
            return_value=SimpleNamespace(cookies={'c_user': '111111111'})))
        self.asset = {'business_id': '222222222'}
        self.evidence = {'account_id': '123456789', 'account_scope_verified': True,
                         'payment_account_id': '333333333', 'payment_account_node_id': 'node_333'}
        self.methods = {'account_id': '123456789', 'account_scope_verified': True,
                        'business_scope_verified': True,
                        'payment_account_relation_verified': True,
                        'methods_query_verified': True, 'payment_methods': [
                            {'credential_id': 'cred_1', 'type': 'Visa',
                             'last4': '1234', 'needs_verification': True,
                             'card_confirmation_status': 'REQUIRED',
                             'verification_tasks_observed': True,
                             'verification_tasks': ['statement_code']}],
                        'code': 'PAYMENT_METHODS_OBSERVED'}

    def test_card_metadata_must_be_masked(self):
        self.assertIsNone(_card_identity({'card_brand': 'Visa', 'card_last4': '4111111111111111'}))
        self.assertIsNone(_card_identity({'card_brand': 'Other', 'card_last4': '1234'}))
        self.assertEqual(_card_identity(self.payload), ('Visa', '1234'))

    async def test_required_task_is_read_only_and_does_not_claim_bank_dispatch(self):
        with patch('app.payment_card_verification.resolve_payment_asset',
                   AsyncMock(return_value=self.asset)), \
             patch('app.session.ProfileSession', SessionFixture), \
             patch('app.payment_card_verification.account_proof', return_value=self.evidence), \
             patch('app.payment_card_verification.methods_proof', return_value=self.methods), \
             patch('app.payment_card_verification.sdc_candidate_proof', return_value={'sdc_screen_verified': True, 'sdc_candidate': False, 'sdc_credential_match': False}), \
             patch('app.payment_card_verification.payment_page_proof', return_value=True), \
             patch('app.payment_card_verification.sdc_candidate_proof', return_value={'sdc_screen_verified': True, 'sdc_candidate': False, 'sdc_credential_match': False}), \
             patch('app.payment_card_verification.execute', AsyncMock(return_value={})) as execute:
            result = await verify_payment_card_http(self.resolver, 'Fixture', self.payload)
        self.assertEqual(result['status'], 'ACTION_REQUIRED')
        self.assertEqual(result['code'], 'CARD_VERIFICATION_MUTATION_NOT_PINNED')
        self.assertIs(result['verification_triggered'], False)
        self.assertIs(result['browser_started'], False)
        self.assertIs(result['submitted'], False)
        self.assertEqual([c.args[1] for c in execute.await_args_list],
                         ['READ_ACCOUNT', 'READ_METHODS', 'READ_SDC_CANDIDATES', 'READ_VERIFY_OPTIONS'])
        self.assertEqual(result['credential_id'], 'cred_1')

    async def test_no_task_never_claims_bank_authorization(self):
        self.methods['payment_methods'][0].update(
            needs_verification=False, verification_tasks=[],
            card_confirmation_status='CLEAR')
        with patch('app.payment_card_verification.resolve_payment_asset',
                   AsyncMock(return_value=self.asset)), \
             patch('app.session.ProfileSession', SessionFixture), \
             patch('app.payment_card_verification.account_proof', return_value=self.evidence), \
             patch('app.payment_card_verification.methods_proof', return_value=self.methods), \
             patch('app.payment_card_verification.sdc_candidate_proof', return_value={'sdc_screen_verified': True, 'sdc_candidate': False, 'sdc_credential_match': False}), \
             patch('app.payment_card_verification.execute', AsyncMock(return_value={})) as execute:
            result = await verify_payment_card_http(self.resolver, 'Fixture', self.payload)
        self.assertEqual(result['code'], 'CARD_VERIFICATION_NO_REQUIRED_TASK')
        self.assertIs(result['funding_verified'], False)
        self.assertEqual(len(execute.await_args_list), 3)

    async def test_unconfirmed_business_scope_stops_before_verification(self):
        self.methods['business_scope_verified'] = False
        with patch('app.payment_card_verification.resolve_payment_asset',
                   AsyncMock(return_value=self.asset)), \
             patch('app.session.ProfileSession', SessionFixture), \
             patch('app.payment_card_verification.account_proof', return_value=self.evidence), \
             patch('app.payment_card_verification.methods_proof', return_value=self.methods), \
             patch('app.payment_card_verification.sdc_candidate_proof', return_value={'sdc_screen_verified': True, 'sdc_candidate': False, 'sdc_credential_match': False}), \
             patch('app.payment_card_verification.execute', AsyncMock(return_value={})) as execute:
            result = await verify_payment_card_http(self.resolver, 'Fixture', self.payload)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertFalse(result['verification_triggered'])
        self.assertEqual(len(execute.await_args_list), 2)


    def test_sdc_candidate_requires_exact_credential_match(self):
        response = {'data': {'payment_account': {'billing_payment_methods': [{
            'usability': 'PENDING_VERIFICATION',
            'credential': {'__typename': 'ExternalCreditCard',
                           'id': 'creditcard_node_123', 'credential_id': 'cred_1',
                           'card_association_name': 'Visa', 'last_four_digits': '1234',
                           'sdc_auth_amount_localized': {'amount_with_offset': 123,
                                                         'currency': 'USD'}}
        }]}}}
        result = sdc_candidate_proof(response, 'cred_1', 'Visa', '1234')
        self.assertTrue(result['sdc_screen_verified'])
        self.assertTrue(result['sdc_candidate'])
        self.assertTrue(result['sdc_credential_match'])
        self.assertNotIn('sdc_auth_amount_localized', result)
        self.assertFalse(sdc_candidate_proof(response, 'different', 'Visa', '1234')['sdc_credential_match'])
        self.assertFalse(sdc_candidate_proof({'errors': [{'message': 'no'}]}, 'cred_1', 'Visa', '1234')['sdc_screen_verified'])
        duplicate = response['data']['payment_account']['billing_payment_methods']
        duplicate.append(dict(duplicate[0]))
        self.assertFalse(sdc_candidate_proof(response, 'cred_1', 'Visa', '1234')['sdc_screen_verified'])

    def test_sdc_empty_only_proves_no_sdc_candidate(self):
        result = sdc_candidate_proof({'data': {'payment_account': {'billing_payment_methods': []}}},
                                     'cred_1', 'Visa', '1234')
        self.assertTrue(result['sdc_screen_verified'])
        self.assertFalse(result['sdc_candidate'])
        self.assertFalse(result['sdc_credential_match'])

    async def test_inconsistent_card_selection_refuses_mutation(self):
        self.methods['payment_methods'].append(
            {**self.methods['payment_methods'][0], 'credential_id': 'cred_2'})
        with patch('app.payment_card_verification.resolve_payment_asset',
                   AsyncMock(return_value=self.asset)), \
             patch('app.session.ProfileSession', SessionFixture), \
             patch('app.payment_card_verification.account_proof', return_value=self.evidence), \
             patch('app.payment_card_verification.methods_proof', return_value=self.methods), \
             patch('app.payment_card_verification.sdc_candidate_proof', return_value={'sdc_screen_verified': True, 'sdc_candidate': False, 'sdc_credential_match': False}), \
             patch('app.payment_card_verification.execute', AsyncMock(return_value={})) as execute:
            result = await verify_payment_card_http(self.resolver, 'Fixture', self.payload)
        self.assertEqual(result['code'], 'CARD_VERIFICATION_CREDENTIAL_UNVERIFIED')
        self.assertEqual(len(execute.await_args_list), 2)
