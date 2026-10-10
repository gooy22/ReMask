import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.private_contract_discovery import _module_nodes, _pairs, _walk
from app.private_inventory_queries import QueryArtifacts
from app.static_payment_read import (MANIFEST, account_proof, command, credit_card_metadata, execute)

ACCOUNT, BUSINESS, PAYMENT = '123456789', '987654321', '555666777'


class PaymentReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_options_use_observed_intent_but_never_dispatch_a_bank_mutation(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={}))
        await execute(web, 'READ_VERIFY_OPTIONS', payment=PAYMENT, business_id=BUSINESS)
        args, kwargs = web.graphql.await_args
        self.assertEqual(args[0], '29195809800004536')
        self.assertEqual(args[1]['userIntent'], 'VERIFY_PAYMENT_METHOD')
        self.assertEqual(args[1]['paymentAccountID'], PAYMENT)
        self.assertNotIn('input', args[1]); self.assertNotIn('before_submit', kwargs)

    def response(self):
        return {'data': {'billable_account_by_asset_id': {'__typename': 'AdAccount', 'id': ACCOUNT,
            'billing_payment_account': {'payment_legacy_account_id': PAYMENT, 'id': 'relay-ui-id'},
            'billing_permissions': ['ADMIN'], 'name': 'sensitive fixture'}}}

    async def test_native_executor_sends_exact_query_and_context_without_browser_or_card_data(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value=self.response()))
        result = await execute(web, 'READ_ACCOUNT', account=ACCOUNT, business_id=BUSINESS, now=86400 * 40 + 15)
        args, kwargs = web.graphql.await_args
        self.assertEqual(args[0], '28797973873175785'); self.assertEqual(args[1]['assetID'], ACCOUNT)
        self.assertEqual(args[1]['billingTxnsStartTime'], 86400 * 26)
        self.assertEqual(args[1]['pendingPaymentsStartTime'], 86400 * 10)
        self.assertFalse(args[1]['shouldFetchPaymentActivitySummary'])
        self.assertEqual(kwargs['business_context_id'], BUSINESS)
        self.assertEqual(kwargs['endpoint_url'], 'https://business.facebook.com/api/graphql/')
        self.assertNotIn('before_submit', kwargs)
        proof = account_proof(result, ACCOUNT)
        self.assertTrue(proof['account_scope_verified']); self.assertEqual(proof['payment_account_id'], PAYMENT)
        self.assertIsNone(proof['card_linked']); self.assertFalse(proof['inventory_complete'])
        self.assertFalse(proof['funding_verified']); self.assertEqual(proof['verification_status'], 'UNVERIFIED')
        self.assertNotIn('sensitive fixture', json.dumps(proof))

    async def test_invalid_scope_stops_before_dispatch_and_mutation_is_not_in_catalog(self):
        web = SimpleNamespace(graphql=AsyncMock())
        for business in ('act_123456789', True, '', '123456789 extra'):
            with self.subTest(business=business), self.assertRaises(ValueError):
                await execute(web, 'READ_ACCOUNT', account=ACCOUNT, business_id=business)
        with self.assertRaises(KeyError):
            await execute(web, 'ATTACH_CARD', account=ACCOUNT, business_id=BUSINESS)
        web.graphql.assert_not_awaited()

    def test_foreign_or_untyped_or_error_response_never_proves_account_or_card_absence(self):
        cases = []
        value = self.response(); value['data']['billable_account_by_asset_id']['id'] = '444555666'; cases.append(value)
        value = self.response(); value['data']['billable_account_by_asset_id']['__typename'] = 'Business'; cases.append(value)
        value = self.response(); value['errors'] = [{'message': 'sensitive fixture'}]; cases.append(value)
        value = self.response(); value['data']['billable_account_by_asset_id']['billing_payment_account']['payment_legacy_account_id'] = None; cases.append(value)
        cases.extend([{'data': None}, {'data': {'unrelated': self.response()['data']}}])
        for value in cases:
            with self.subTest(value=value):
                proof = account_proof(value, ACCOUNT)
                self.assertFalse(proof['account_scope_verified']); self.assertIsNone(proof['card_linked'])
                self.assertFalse(proof['inventory_complete']); self.assertNotIn('payment_account_id', proof)

    def test_credit_card_metadata_is_exact_masked_and_does_not_prove_rk_relation(self):
        payload = {'data': {'node': {'__typename': 'ExternalCreditCard', 'id': PAYMENT,
            'last_four_digits': '1234', 'card_association_name': 'VISA', 'billing_address': 'private fixture'}}}
        self.assertEqual(credit_card_metadata(payload, PAYMENT), {'id': PAYMENT, 'type': 'Visa', 'last4': '1234'})
        self.assertIsNone(credit_card_metadata(payload, ACCOUNT))
        payload['data']['node']['last_four_digits'] = '4111111111111111'
        self.assertIsNone(credit_card_metadata(payload, PAYMENT))

    def test_returned_commands_cannot_mutate_cached_manifest(self):
        first = command('READ_CREDENTIAL', credential=PAYMENT)
        first['doc_id'] = '1'; first['variables']['paymentMethodID'] = '1'
        second = command('READ_CREDENTIAL', credential=PAYMENT)
        self.assertEqual(second['doc_id'], '27586872297608269')
        self.assertEqual(second['variables'], {'paymentMethodID': PAYMENT})

    def test_pinned_ids_kinds_argument_names_and_module_hashes_match_observed_sources(self):
        fixture = Path(__file__).with_name('fixtures') / 'meta_payment_read_observed_20261009.js'
        source = fixture.read_text() + '\n' + fixture.with_name('meta_payment_methods_observed_20261009.js').read_text()
        nodes = dict(_module_nodes(source))
        manifest = json.loads(MANIFEST.read_text())
        for name, expected in {**manifest['evidence']['module_hashes'], **manifest['evidence']['additional_module_hashes']}.items():
            definition = '__d(' + json.dumps(name) + ',[],' + nodes[name].text.decode() + ');'
            self.assertEqual(hashlib.sha256(definition.encode()).hexdigest(), expected, name)
        metadata = QueryArtifacts()
        metadata.modules = {name: {'observed': node} for name, node in nodes.items()}
        for operation, row in manifest['operations'].items():
            if operation == 'READ_SDC_CANDIDATES':
                # Observed 2026-10-10 as BillingSDCAuthScreenQuery, after
                # the historical 2026-10-09 captured module fixture. Keep
                # the independent query-only contract strict rather than
                # fabricating a historical JS module in that fixture.
                self.assertEqual(row['doc_id'], '25160732503612508')
                self.assertEqual(row['operation_kind'], 'query')
                self.assertEqual(row['variables'], {'paymentAccountID': '$payment'})
                continue
            candidates = []
            for node in _walk(nodes[row['friendly_name'] + '.graphql']):
                if node.type != 'object': continue
                try:
                    pairs = _pairs(node)
                    if {'params', 'operation', 'kind'}.issubset(pairs): candidates.append(pairs)
                except ValueError: continue
            self.assertEqual(len(candidates), 1, operation)
            params = _pairs(candidates[0]['params'])
            self.assertEqual(metadata._read(params['id']), row['doc_id'])
            self.assertEqual(metadata._read(params['operationKind']), 'query')
            args = metadata._read(_pairs(candidates[0]['operation'])['argumentDefinitions'])
            self.assertTrue(set(row['variables']).issubset({a['name'] for a in args}))
