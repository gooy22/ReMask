import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.contract_maintenance.payment_sources import public_payment_modules
from app.contract_maintenance.payment_verification_source import read_verification_page, verification_page_probe


class VerificationSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_read_uses_source_pinned_query_and_default_entrypoint_variables(self):
        source = Path(__file__).with_name('fixtures').joinpath('meta_bank_view_observed_20261010.js').read_text()
        rows = {r['name']: r for r in public_payment_modules(source)}
        expected = {
            'BillingThreeDSVerificationPageViewManagerQuery_facebookRelayOperation': 'f8034e7feb912d9b46daa42ece00afd0bc10d6164af0ded64e29602aa6acde56',
            'BillingThreeDSVerificationPageViewManagerQuery$Parameters': '719118d2453d98d15f2b6bb273b6818a4c83abe52df17f53d71f08ebfbe738c2',
            'BillingThreeDSVerificationPageViewManager.entrypoint': '401238c3497c582779057094e01be55d2294e113946a1af21fef74d438b82fd0',
            'buildBillingViewManagerEntrypoint.entrypointutils': 'b590757b9625aa3badf9587f4e6304ae363b4b5950f6a23a04bab69bfd2d8f98'}
        for name, digest in expected.items(): self.assertEqual(rows[name]['sha256'], digest)
        self.assertIn('operationKind:"query"', rows['BillingThreeDSVerificationPageViewManagerQuery$Parameters']['source'])
        self.assertIn('paymentAccountID:n.paymentAccountID', rows['buildBillingViewManagerEntrypoint.entrypointutils']['source'])
        web=SimpleNamespace(graphql=AsyncMock(return_value={}))
        await read_verification_page(web, '555666777', '987654321')
        args, kwargs=web.graphql.await_args
        self.assertEqual(args, ('26638815235726004', {'paymentAccountID':'555666777'}))
        self.assertNotIn('before_submit', kwargs)
        self.assertEqual(kwargs['business_context_id'], '987654321')

    def test_probe_rejects_foreign_scope_and_exports_field_names_without_values(self):
        payload={'data':{'payment_account':{'id':'scope-node', 'payment_legacy_account_id':'555666777',
            'billable_account':{'id':'123456789', '__typename':'AdAccount'},
            'bank_token':'fixture-secret', 'challenge_url':'https://private.example/'}}}
        result=verification_page_probe(payload, '123456789', '555666777', 'scope-node')
        self.assertTrue(result['account_scope_verified'])
        self.assertFalse(result['submitted'])
        self.assertNotIn('fixture-secret', json.dumps(result))
        self.assertNotIn('private.example', json.dumps(result))
        for node in (None, 'foreign'):
            self.assertFalse(verification_page_probe(payload, '123456789','555666777',node)['account_scope_verified'])
        payload['errors']=[{'message':'fixture-secret'}]
        self.assertEqual(verification_page_probe(payload,'123456789','555666777','scope-node')['code'], 'VERIFICATION_PAGE_QUERY_REJECTED')
