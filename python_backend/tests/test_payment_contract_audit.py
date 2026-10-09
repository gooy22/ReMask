import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.contract_maintenance.payment_contract_audit import public_country_query_audit, inspect_payment_contract_audit


def artifact(kind='query', doc='123456789', name='BillingTaxCountryQuery'):
    value = {'params': {'name': name, 'id': doc, 'operationKind': kind},
        'operation': {'kind': 'Operation', 'name': name,
            'argumentDefinitions': [{'kind': 'LocalArgument', 'name': 'paymentAccountID',
                'defaultValue': 'fixture-secret-default'}],
            'selections': [{'kind': 'LinkedField', 'name': 'payment_account', 'concreteType': 'PaymentAccount',
                'args': [{'kind': 'Variable', 'name': 'id', 'variableName': 'paymentAccountID'},
                         {'kind': 'Literal', 'name': 'context', 'value': 'fixture-secret-literal'}],
                'selections': [{'kind': 'ScalarField', 'name': 'status'}]}]}}
    source = '__d("' + name + '.graphql",[],function(a,b,c,d,e){e.exports=' + json.dumps(value) + ';});'
    return {'name': name + '.graphql', 'source': source}


class PaymentContractAuditTests(unittest.IsolatedAsyncioTestCase):
    def test_only_public_query_schema_is_exported_never_sources_literals_or_runtime_data(self):
        row = artifact()
        snapshot = {'modules': [row, {'name': 'BillingCountryVerificationUtils',
            'source': '__d("BillingCountryVerificationUtils",[],function(){var cookie="fixture-cookie";});'}],
            'payment_account_probe': {'email': 'fixture-email'}, 'scripts_not_read': 0, 'export_truncated': False}
        audit = public_country_query_audit(snapshot)
        self.assertTrue(audit['country_utility_present'])
        self.assertTrue(audit['capture_complete'])
        self.assertEqual(audit['queries'][0]['variables'], ['paymentAccountID'])
        self.assertEqual(audit['queries'][0]['fields'][0]['arguments'], [
            {'name': 'id', 'kind': 'Variable', 'variable': 'paymentAccountID'},
            {'name': 'context', 'kind': 'Literal'}])
        self.assertEqual(audit['queries'][0]['fields'][1]['path'], 'payment_account.status')
        text = json.dumps(audit)
        for forbidden in ('fixture-secret', 'fixture-cookie', 'fixture-email', '__d(', 'defaultValue', 'sourceURL'):
            self.assertNotIn(forbidden, text)
        self.assertFalse(audit['execution_enabled']); self.assertFalse(audit['submitted'])

    def test_mutations_conflicting_versions_and_unexported_decoys_do_not_compile(self):
        for rows in ([artifact(kind='mutation')], [artifact(), artifact(doc='987654321')],
                     [{'source': artifact()['source'].replace('e.exports=', 'var decoy=')}],
                     [artifact(name='BillingCardSaveQuery')]):
            with self.subTest(rows=len(rows)):
                self.assertEqual(public_country_query_audit({'modules': rows})['queries'], [])

    async def test_boundary_drops_every_unrecognized_capture_key(self):
        capture = AsyncMock(return_value={'modules': [artifact()], 'source': 'fixture-secret',
            'payment_methods_probe': {'credential_id': 'fixture-credential'}, 'cookies': 'fixture-cookie'})
        with patch('app.contract_maintenance.payment_sources.inspect_profile_payment_sources', capture):
            result = await inspect_payment_contract_audit(SimpleNamespace(), '15', '123456789', state=None)
        self.assertEqual(result['account_id'], '123456789')
        self.assertNotIn('fixture-', json.dumps(result))
        self.assertFalse(result['capture_complete'])
