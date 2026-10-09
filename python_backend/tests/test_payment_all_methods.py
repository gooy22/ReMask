import copy
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from app.static_payment_read import complete_methods
from tests.test_payment_static_methods import ACCOUNT, BM, PAYMENT, NODE, account_response, methods_response
from app.static_payment_read import account_proof, methods_proof


class CompleteMethodsTests(unittest.IsolatedAsyncioTestCase):
    def filtered(self):
        payload = methods_response()
        payment = payload['data']['billable_account_by_asset_id']['billing_payment_account']
        for name in ('primary', 'billing_payment_methods_allowlist_customized', 'primary_funding_source_customized'):
            payment[name] = []
        return methods_proof(payload, ACCOUNT, business_id=BM,
                             account_evidence=account_proof(account_response(), ACCOUNT))

    async def read(self, full, metadata=None):
        replies = [account_response(), full]
        if metadata is not None:
            replies.append(metadata)
        self.web = SimpleNamespace(graphql=AsyncMock(side_effect=replies))
        return await complete_methods(self.web, self.filtered(), business_id=BM)

    def full(self, rows=None):
        return {'data': {'payment_account': {'id': NODE, 'billing_payment_methods': [] if rows is None else rows}}}

    async def test_nonprimary_card_is_hydrated_by_exact_id_without_claiming_bank_verification(self):
        full = self.full([{'credential': {'id':'1234567','__typename':'ExternalCreditCard'}}])
        metadata = {'data': {'node': {'id':'1234567','__typename':'ExternalCreditCard',
            'card_association_name':'VISA','last_four_digits':'9112','private':'DO NOT EXPORT'}}}
        result = await self.read(full, metadata)
        self.assertTrue(result['inventory_complete'])
        self.assertEqual(result['payment_methods'][0]['credential_id'], '1234567')
        self.assertEqual(result['payment_methods'][0]['bank_verification_status'], 'UNVERIFIED')
        self.assertFalse(result['funding_verified'])
        self.assertNotIn('DO NOT EXPORT', str(result))
        self.assertEqual(self.web.graphql.await_args.args[1], {'paymentMethodID':'1234567'})

    async def test_unfiltered_empty_is_complete_only_for_exact_payment_node(self):
        result = await self.read(self.full())
        self.assertTrue(result['inventory_complete'])
        self.assertEqual(result['all_credential_ids'], [])
        self.assertEqual(result['verification_status'], 'NONE')
        full = self.full(); full['data']['payment_account']['id'] = 'foreign-node'
        result = await self.read(full)
        self.assertFalse(result['inventory_complete'])
        self.assertNotEqual(result['verification_status'], 'NONE')

    async def test_errors_missing_list_duplicate_ids_and_missing_metadata_never_prove_absence(self):
        variants = [self.full(), {'data': {'payment_account': {'id':NODE}}},
            self.full([{'credential': {'id':'1234567','__typename':'ExternalCreditCard'}}] * 2)]
        variants[0]['errors'] = [{'message':'private'}]
        for value in variants:
            result = await self.read(value)
            self.assertFalse(result['inventory_complete'])
            self.assertNotEqual(result['verification_status'], 'NONE')
        result = await self.read(self.full([{'credential': {'id':'1234567','__typename':'ExternalCreditCard'}}]), {'data':None})
        self.assertFalse(result['inventory_complete'])

    async def test_noncard_credentials_are_retained_and_not_reported_as_empty(self):
        result = await self.read(self.full([{'credential': {'id':'1234567','__typename':'PaymentPaypalBillingAgreement'}}]))
        self.assertTrue(result['inventory_complete'])
        self.assertEqual(result['all_credential_ids'], ['1234567'])
        self.assertNotEqual(result['verification_status'], 'NONE')

    async def test_unverified_business_never_dispatches_unfiltered_query(self):
        self.web = SimpleNamespace(graphql=AsyncMock())
        methods = self.filtered(); methods['business_scope_verified'] = False
        await complete_methods(self.web, methods, business_id=BM)
        self.web.graphql.assert_not_awaited()
