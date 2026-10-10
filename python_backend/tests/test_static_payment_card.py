import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_contract_discovery import _module_nodes, _pairs, _walk
from app.private_inventory_queries import QueryArtifacts
from app.static_payment_card import (MANIFEST, card_screen_command, read_card_screen,
    card_screen_proof, save_response_proof, confirm_saved_card, submission_contract_status,
    inspect_card_form, prepare_profile_card_form)
from app.static_payment_read import account_proof, methods_proof

ACCOUNT, BUSINESS, PAYMENT = '123456789', '987654321', '555666777'
NODE, CREDENTIAL = 'relay-payment-node', 'relay-card-node'
MASK = {'type': 'Visa', 'last4': '1234'}


def account_evidence():
    return account_proof({'data': {'billable_account_by_asset_id': {'__typename': 'AdAccount',
        'id': ACCOUNT, 'billing_payment_account': {'id': NODE, 'payment_legacy_account_id': PAYMENT}}}}, ACCOUNT)


def payment_node():
    return {'id': NODE, '__typename': 'PaymentAccount',
            'billable_account': {'__typename': 'AdAccount', 'id': 'act_' + ACCOUNT}}


def save_payload(status='SUCCESS'):
    return {'data': {'xfb_billing_save_card_credential': {
        'payment_account': payment_node(), 'linked_payment_account': None,
        'credit_card': {'__typename': 'ExternalCreditCard', 'id': CREDENTIAL,
            'credential_id': 'ent-card-credential', 'card_association_name': 'VISA',
            'last_four_digits': '1234', 'email': 'private email', 'user_display_name': 'private name'},
        'card_verification_status': status,
        'card_verification': {'nonce': 'private nonce', 'external_uri': 'https://private-bank.invalid',
            'params': {'payload': 'private bank payload'}},
        'risk_verification_info': {'use_case': 'private risk fixture'}}}}


def saved(payload=None, expected=None):
    return save_response_proof(save_payload() if payload is None else payload, ACCOUNT,
                              account_evidence=account_evidence(), expected_card=MASK if expected is None else expected)


def linked_methods(credential=CREDENTIAL, last4='1234'):
    card = {'__typename': 'ExternalCreditCard', 'id': credential,
            'card_association_name': 'VISA', 'last_four_digits': last4}
    return methods_proof({'data': {'billable_account_by_asset_id': {
        '__typename': 'AdAccount', 'id': ACCOUNT, 'owning_business': {'id': BUSINESS},
        'billing_payment_account': {'id': NODE, 'primary': [],
            'billing_payment_methods_allowlist_customized': [{'credential': card}],
            'primary_funding_source_customized': []}}}}, ACCOUNT,
            business_id=BUSINESS, account_evidence=account_evidence())


class StaticPaymentCardTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_http_checks_exact_scope_then_screen_and_does_not_claim_save_ready(self):
        from tests.test_payment_card_http import read_account, read_methods
        web=SimpleNamespace(graphql=AsyncMock(side_effect=[read_account(),read_methods(),self.screen()]))
        result=await inspect_card_form(web,account=ACCOUNT,business_id=BUSINESS)
        self.assertEqual(result['status'],'FORM_CONFIRMED');self.assertFalse(result['submitted'])
        self.assertFalse(result['execution_enabled']);self.assertFalse(result['browser_started'])
        self.assertEqual([c.args[0] for c in web.graphql.await_args_list],
                         ['28797973873175785','28814526004898205','27759194723782263'])
        self.assertNotIn('private',json.dumps(result))

    async def test_prepare_foreign_bm_never_reads_form(self):
        from tests.test_payment_card_http import read_account, read_methods
        foreign=read_methods();foreign['data']['billable_account_by_asset_id']['owning_business']['id']='999888777'
        web=SimpleNamespace(graphql=AsyncMock(side_effect=[read_account(),foreign]))
        result=await inspect_card_form(web,account=ACCOUNT,business_id=BUSINESS)
        self.assertEqual(result['status'],'BLOCKED');self.assertEqual(web.graphql.await_count,2)

    async def test_prepare_session_failure_stays_http_and_diagnostics_exclude_secrets(self):
        resolver=SimpleNamespace(resolve=AsyncMock(side_effect=RuntimeError('private credentials')))
        with patch('app.payment_inspection.resolve_payment_asset',AsyncMock(return_value={'business_id':BUSINESS})), \
             patch('app.session.ProfileSession') as session:
            result=await prepare_profile_card_form(resolver,'Fixture',ACCOUNT)
        session.assert_not_called();self.assertEqual(result['code'],'PAYMENT_HTTP_UNAVAILABLE')
        self.assertFalse(result['browser_started']);self.assertNotIn('private',json.dumps(result))

    async def test_card_screen_read_uses_exact_observed_nullable_arguments_and_no_browser(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={}))
        await read_card_screen(web, business_id=BUSINESS, payment=PAYMENT)
        args, kwargs = web.graphql.await_args
        self.assertEqual(args, ('27759194723782263', {
            'paymentAccountID': PAYMENT, 'country': None, 'currency': None, 'intent': None}))
        self.assertEqual(kwargs, {'friendly_name': 'BillingAddCreditCardScreenQuery',
            'endpoint_url': 'https://business.facebook.com/api/graphql/', 'business_context_id': BUSINESS})
        self.assertTrue(web.private_only)
        for invalid in ('act_' + PAYMENT, True, ''):
            with self.assertRaises(ValueError):
                await read_card_screen(web, business_id=BUSINESS, payment=invalid)
        self.assertEqual(web.graphql.await_count, 1)

    def screen(self):
        p = payment_node()
        p['billing_payment_method_options'] = [{'__typename': 'AdAccountNewCreditCardOption',
            'check_make_default': True, 'can_save_to_business': False, 'verify_tokenization_required': True}]
        p['billable_account']['billing_safe_mode_state'] = {'status': 'PLACED'}
        return {'data': {'payment_account': p, 'viewer': {'primary_email': 'private email'}}}

    def test_form_scope_uses_relay_identity_and_exact_account_without_legacy_id_in_query(self):
        proof = card_screen_proof(self.screen(), ACCOUNT, account_evidence=account_evidence())
        self.assertTrue(proof['card_form_verified']); self.assertTrue(proof['bank_verification_required'])
        self.assertFalse(proof['submitted']); self.assertFalse(proof['execution_enabled'])
        self.assertNotIn('private email', json.dumps(proof))
        payload = self.screen(); payload['data']['payment_account']['id'] = 'foreign-payment'
        self.assertFalse(card_screen_proof(payload, ACCOUNT, account_evidence=account_evidence())['card_form_verified'])

    def test_form_reports_business_wallet_route_without_changing_or_exposing_parent_id(self):
        p=self.screen()
        node=p['data']['payment_account']['billable_account']
        node['owner_business_payment_account']={'id':'parent-payment-account'}
        option=p['data']['payment_account']['billing_payment_method_options'][0]
        option['can_save_to_business']=True
        proof=card_screen_proof(p, ACCOUNT, account_evidence=account_evidence())
        self.assertTrue(proof['card_form_verified'])
        self.assertTrue(proof['business_wallet_route_possible'])
        self.assertNotIn('parent-payment-account', json.dumps(proof))
        option['can_save_to_business']=False
        direct=card_screen_proof(p, ACCOUNT, account_evidence=account_evidence())
        self.assertFalse(direct['business_wallet_route_possible'])
        option['can_save_to_business']=True
        node['owner_business_payment_account']=None
        unresolved=card_screen_proof(p, ACCOUNT, account_evidence=account_evidence())
        self.assertFalse(unresolved['business_wallet_route_possible'])
        self.assertTrue(unresolved['business_wallet_route_unresolved'])

    def test_form_unknown_duplicate_or_malformed_options_and_partial_reads_are_inconclusive(self):
        for change in ('duplicate', 'wrong_type', 'not_boolean', 'foreign_account', 'errors', 'partial'):
            with self.subTest(change=change):
                p = self.screen(); node = p['data']['payment_account']; options = node['billing_payment_method_options']
                if change == 'duplicate': options.append(copy.deepcopy(options[0]))
                if change == 'wrong_type': options[0]['__typename'] = 'BusinessCreditCardOption'
                if change == 'not_boolean': options[0]['check_make_default'] = 1
                if change == 'foreign_account': node['billable_account']['id'] = BUSINESS
                if change == 'errors': p['errors'] = [{'message': 'private failure'}]
                if change == 'partial': p['hasNext'] = True
                self.assertFalse(card_screen_proof(p, ACCOUNT, account_evidence=account_evidence())['card_form_verified'])

    def test_save_success_needs_exact_instrument_read_not_just_matching_mask(self):
        result = saved()
        self.assertEqual(result['status'], 'VERIFYING'); self.assertTrue(result['retry_blocked'])
        self.assertNotIn('private', json.dumps(result))
        wrong = confirm_saved_card(result, linked_methods('other-credential'), business_id=BUSINESS)
        self.assertEqual(wrong['status'], 'VERIFYING')
        correct = confirm_saved_card(result, linked_methods(), business_id=BUSINESS)
        self.assertEqual(correct['status'], 'LINKED'); self.assertTrue(correct['card_linked'])
        self.assertFalse(correct['funding_verified']); self.assertTrue(correct['retry_blocked'])

    def test_exact_credential_id_alias_is_accepted_only_with_unique_meta_match(self):
        # Save provides both card.id and credential_id; Meta's inventory may
        # expose either. Never infer linkage from the masked number alone.
        result = confirm_saved_card(saved(), linked_methods('ent-card-credential'), business_id=BUSINESS)
        self.assertEqual(result['status'], 'LINKED')
        self.assertEqual(result['code'], 'CARD_LINK_CONFIRMED')
        wrong = confirm_saved_card(saved(), linked_methods('unrelated-card-node'), business_id=BUSINESS)
        self.assertEqual(wrong['status'], 'VERIFYING')
        self.assertEqual(wrong['verification_stage'], 'save_credential_not_in_rk')

    def test_two_exact_id_variants_in_inventory_are_ambiguous(self):
        methods=linked_methods(CREDENTIAL)
        second=copy.deepcopy(methods['payment_methods'][0])
        second['credential_id']='ent-card-credential'
        methods['payment_methods'].append(second)
        result=confirm_saved_card(saved(), methods, business_id=BUSINESS)
        self.assertEqual(result['status'], 'VERIFYING')
        self.assertEqual(result['verification_stage'], 'save_credential_ambiguous')

    def test_exact_card_bank_verification_does_not_claim_linked(self):
        methods=linked_methods('ent-card-credential')
        methods['payment_methods'][0]['needs_verification']=True
        result=confirm_saved_card(saved(), methods, business_id=BUSINESS)
        self.assertEqual(result['status'], 'ACTION_REQUIRED')
        self.assertEqual(result['code'], 'CARD_BANK_CONFIRMATION_REQUIRED')
        self.assertEqual(result['verification_stage'], 'bank_confirmation_pending')

    def test_confirmation_rejects_foreign_scope_missing_proofs_conflicting_masks_and_duplicates(self):
        for change in ('business', 'account', 'payment', 'missing_proof', 'last4', 'duplicate', 'empty'):
            with self.subTest(change=change):
                methods = linked_methods()
                if change in ('business', 'account'): methods[change + '_id'] = '999888777'
                if change == 'payment': methods['payment_account_id'] = '999888777'
                if change == 'missing_proof': methods['business_scope_verified'] = False
                if change == 'last4': methods['payment_methods'][0]['last4'] = '4321'
                if change == 'duplicate': methods['payment_methods'].append(copy.deepcopy(methods['payment_methods'][0]))
                if change == 'empty': methods['payment_methods'] = []
                self.assertEqual(confirm_saved_card(saved(), methods, business_id=BUSINESS)['status'], 'VERIFYING')

    def test_bank_confirmation_retains_intent_without_exporting_bank_secrets_or_faking_success(self):
        result = saved(save_payload('AUTHENTICATION_REQUIRED'))
        self.assertEqual(result['status'], 'ACTION_REQUIRED'); self.assertTrue(result['retry_blocked'])
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(confirm_saved_card(result, linked_methods(), business_id=BUSINESS), result)

    def test_lost_partial_foreign_or_invalid_save_response_never_unlocks_resubmit(self):
        for change in ('lost', 'errors', 'partial', 'foreign', 'no_credential', 'wrong_mask', 'wrong_brand', 'unknown_status'):
            with self.subTest(change=change):
                p = save_payload(); root = p['data']['xfb_billing_save_card_credential']
                if change == 'lost': p = {}
                if change == 'errors': p['errors'] = [{'message': 'private failure'}]
                if change == 'partial': p['extensions'] = {'is_final': False}
                if change == 'foreign': root['payment_account']['billable_account']['id'] = BUSINESS
                if change == 'no_credential': root['credit_card']['credential_id'] = None
                if change == 'wrong_mask': root['credit_card']['last_four_digits'] = '4321'
                if change == 'wrong_brand': root['credit_card']['card_association_name'] = 'Mastercard'
                if change == 'unknown_status': root['card_verification_status'] = 'UNRECOGNIZED'
                result = saved(p)
                self.assertEqual(result['status'], 'SUBMITTED_UNVERIFIED')
                self.assertTrue(result['submitted']); self.assertTrue(result['retry_blocked'])
                self.assertFalse(result['funding_verified']); self.assertNotIn('private', json.dumps(result))

    def test_business_save_without_rk_link_is_not_success_but_linked_payment_account_is_supported(self):
        p = save_payload(); root = p['data']['xfb_billing_save_card_credential']
        root['payment_account'] = {'id': 'business-payment-node', 'billable_account': {'__typename': 'AdBusiness', 'id': BUSINESS}}
        self.assertEqual(saved(p)['status'], 'SUBMITTED_UNVERIFIED')
        root['linked_payment_account'] = payment_node()
        self.assertEqual(saved(p)['status'], 'VERIFYING')

    def test_observed_save_id_and_top_level_schema_do_not_enable_unverified_input_or_ptt(self):
        status = submission_contract_status()
        self.assertTrue(status['save_operation_verified']); self.assertFalse(status['execution_enabled'])
        self.assertEqual(status['missing_source_modules'], ['BillingCreditCardUtils', 'getPTTUtils'])
        self.assertFalse(status['submitted']); self.assertFalse(status['tokenization_verified'])
        command = card_screen_command(PAYMENT); command['variables']['intent'] = 'invented'
        self.assertIsNone(card_screen_command(PAYMENT)['variables']['intent'])

    def test_contract_ids_arguments_root_binding_and_hashes_match_uploaded_artifacts(self):
        source = Path(__file__).with_name('fixtures').joinpath('meta_payment_save_observed_20261009.js').read_text()
        nodes = dict(_module_nodes(source)); metadata = QueryArtifacts()
        metadata.modules = {name: {'observed': node} for name, node in nodes.items()}
        manifest = json.loads(MANIFEST.read_text())
        for name, expected in manifest['evidence']['module_hashes'].items():
            definition = '__d(' + json.dumps(name) + ',[],' + nodes[name].text.decode() + ');'
            self.assertEqual(hashlib.sha256(definition.encode()).hexdigest(), expected, name)
        for name, row in manifest['operations'].items():
            artifacts = []
            for node in _walk(nodes[row['friendly_name'] + '.graphql']):
                if node.type != 'object': continue
                try: pairs = _pairs(node)
                except ValueError: continue
                if {'params', 'operation', 'kind'}.issubset(pairs): artifacts.append(pairs)
            self.assertEqual(len(artifacts), 1)
            params = metadata._read(artifacts[0]['params']); operation = metadata._read(artifacts[0]['operation'])
            self.assertEqual(params['id'], row['doc_id']); self.assertEqual(params['operationKind'], row['operation_kind'])
            args = {a['name'] for a in operation['argumentDefinitions']}
            self.assertEqual(args, set(row.get('variables', row.get('top_level_arguments', []))))
            if name == 'SAVE_CARD':
                root = operation['selections'][0]
                self.assertEqual(root['name'], row['root_field'])
                self.assertEqual(root['args'], [{'kind': 'Variable', 'name': 'data', 'variableName': 'input'}])


if __name__ == '__main__':
    unittest.main()

