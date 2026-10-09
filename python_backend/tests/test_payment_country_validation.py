import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.contract_maintenance.payment_contract_audit import public_country_query_audit
from app.payment_card_requirements import TAX_DOC_ID, COUNTRY_BIN_DOC_ID, read_tax_country_validation
from app.static_payment_read import account_proof
from tests.test_payment_card_http import ACCOUNT, BUSINESS, NODE, PAYMENT, FakeHTTP, read_account


class CountryValidationTests(unittest.IsolatedAsyncioTestCase):
    def test_current_public_artifacts_pin_query_ids_variables_and_scope_fields(self):
        root=Path(__file__).with_name('fixtures')/'payment_reference'
        hashes={
            'BillingCountryVerificationUtils.current.js':'13d426df17c2af0a3a7a0f5cb5569e47673ca35b60d51cc8f28a0cb55870753c',
            'BillingCountryVerificationUtilsTaxCountryValidationDataQuery.graphql.current.js':'5d5f0dcad02ed0def5a4dc47a59c2e51449f1c0de6eed650cbae7db1ab698778',
            'BillingCountryVerificationUtilsTaxCountryValidationDataQuery_facebookRelayOperation.current.js':'cdfb46c6728d61e7be5b37eb0afe80bc43aee94c71e10a3415d35342cd45ad09',
            'BillingCountryVerificationUtilsBinPropertiesQuery.graphql.current.js':'71f4097f404b1da8c39ea9a0ef674e2c158dcb87bc781b3bc0aa6ec2de304a3a',
            'BillingCountryVerificationUtilsBinPropertiesQuery_facebookRelayOperation.current.js':'85e27042f9fd5c51ad0790f741bd4ac07e3f9b170644ff4783ea24611b3028d3'}
        modules=[]
        for name,digest in hashes.items():
            source=root.joinpath(name).read_text().rstrip('\n')
            self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),digest)
            modules.append({'source':source})
        queries={q['doc_id']:q for q in public_country_query_audit({'modules':modules})['queries']}
        self.assertEqual(queries[TAX_DOC_ID]['variables'],['paymentAccountID'])
        self.assertEqual(queries[COUNTRY_BIN_DOC_ID]['variables'],['bin','paymentAccountID','ptt'])
        paths={x['path'] for x in queries[TAX_DOC_ID]['fields']}
        self.assertIn('payment_account.id',paths)
        self.assertIn('payment_account.billable_account.id',paths)
        self.assertIn('payment_account.tax_country_validation_info.status',paths)

    async def test_tax_status_read_preserves_exact_account_business_and_payment(self):
        web=FakeHTTP();web.tax_status='CONFIRMED'
        result=await read_tax_country_validation(web,business_id=BUSINESS,evidence=account_proof(read_account(),ACCOUNT))
        self.assertTrue(result['tax_status_confirmed'])
        self.assertEqual(web.calls[0][1],{'paymentAccountID':PAYMENT})
        self.assertEqual(web.calls[0][2]['business_context_id'],BUSINESS)

    async def test_foreign_scope_rejected_even_when_tax_status_claims_confirmed(self):
        for changed in ('payment','account','errors'):
            web=FakeHTTP();web.tax_status='CONFIRMED'
            payload=await web.graphql(TAX_DOC_ID,{})
            if changed=='payment':payload['data']['payment_account']['id']='foreign-payment'
            elif changed=='account':payload['data']['payment_account']['billable_account']['id']='999999999'
            else:payload['errors']=[{'message':'private synthetic failure'}]
            reader=SimpleNamespace(graphql=AsyncMock(return_value=payload))
            result=await read_tax_country_validation(reader,business_id=BUSINESS,evidence=account_proof(read_account(),ACCOUNT))
            self.assertEqual(result['code'],'CARD_TAX_COUNTRY_QUERY_UNCONFIRMED')
            self.assertFalse(result['tax_status_confirmed'])
            self.assertNotIn('private',json.dumps(result))

    async def test_explicit_null_status_is_unconfirmed_and_missing_scope_still_rejects(self):
        web=FakeHTTP()
        payload=await web.graphql(TAX_DOC_ID,{})
        for info in (None,{'status':None}):
            payload['data']['payment_account']['tax_country_validation_info']=info
            reader=SimpleNamespace(graphql=AsyncMock(return_value=payload))
            result=await read_tax_country_validation(reader,business_id=BUSINESS,evidence=account_proof(read_account(),ACCOUNT))
            self.assertEqual(result['code'],'CARD_TAX_COUNTRY_STATUS_READ')
            self.assertFalse(result['tax_status_confirmed'])
        for broken in ({'data':None},{'data':{}},{'errors':[{}]}):
            reader=SimpleNamespace(graphql=AsyncMock(return_value=broken))
            result=await read_tax_country_validation(reader,business_id=BUSINESS,evidence=account_proof(read_account(),ACCOUNT))
            self.assertEqual(result['code'],'CARD_TAX_COUNTRY_QUERY_UNCONFIRMED')
