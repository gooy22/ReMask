import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

from app.payment_card_requirements import (BIN_DOC_ID, bin_command, read_card_requirements,
    requirements_proof, country_policy_proof)
from tests.test_payment_card_http import read_bin, read_screen, ACCOUNT, BUSINESS, PAYMENT
from tests.test_payment_ptt import VALUES, node_parity
from app.private_inventory_queries import QueryArtifacts
from app.private_contract_discovery import _module_nodes, _walk, _pairs


class CardRequirementsTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_query_sends_only_observed_top_level_variables_and_selected_business(self):
        web=SimpleNamespace(graphql=AsyncMock(return_value=read_bin()))
        await read_card_requirements(web,business_id=BUSINESS,payment=PAYMENT,
            number=VALUES['number'],country='US',currency='USD')
        call=web.graphql.await_args
        self.assertEqual(call.args,(BIN_DOC_ID,{'paymentAccountID':PAYMENT,
            'bin':VALUES['number'][:8],'country':'US','currency':'USD'}))
        self.assertEqual(call.kwargs['business_context_id'],BUSINESS)
        self.assertTrue(web.private_only)

    def test_source_contract_is_pinned_to_uploaded_operation_and_exact_arguments(self):
        fixtures=Path(__file__).with_name('fixtures')/'payment_reference'
        hashes={'useBillingBinInfoQuery_facebookRelayOperation.current.js':'8283559bafc25ff3d41cba61ceb895b6e98c7ddf2632d9f81c88867b7faa566d',
            'useBillingBinInfoQuery.graphql.current.js':'04ab11efbfa1acd5bd2201032c8c47c8b3f49a8baef21aa140f902e6ab5f4a7f',
            'BillingEMandateConsentUtils.current.js':'9ad1c180fed2f6b7bfb12352dc7bb8824a2fda3659f3c5d81d002f6e0d8aac01'}
        for name,digest in hashes.items():
            self.assertEqual(hashlib.sha256(fixtures.joinpath(name).read_text().rstrip('\n').encode()).hexdigest(),digest)
        operation=fixtures.joinpath('useBillingBinInfoQuery_facebookRelayOperation.current.js').read_text()
        self.assertIn('"'+BIN_DOC_ID+'"',operation)
        source=fixtures.joinpath('useBillingBinInfoQuery.graphql.current.js').read_text()+operation
        nodes=dict(_module_nodes(source));metadata=QueryArtifacts()
        metadata.modules={name:{'observed':node} for name,node in nodes.items()}
        artifacts=[]
        for node in _walk(nodes['useBillingBinInfoQuery.graphql']):
            if node.type!='object':continue
            try:pairs=_pairs(node)
            except ValueError:continue
            if {'params','operation','kind'}.issubset(pairs):artifacts.append(pairs)
        self.assertEqual(len(artifacts),1)
        params=metadata._read(artifacts[0]['params']);query=metadata._read(artifacts[0]['operation'])
        self.assertEqual(params['id'],BIN_DOC_ID);self.assertEqual(params['operationKind'],'query')
        self.assertEqual({x['name'] for x in query['argumentDefinitions']},
            set(bin_command(payment=PAYMENT,number=VALUES['number'],country='US',currency='USD')))
        self.assertEqual(query['selections'][0]['name'],'credit_card_bin_info_shim')

    def proof(self,payload=None,values=None,**kwargs):
        return requirements_proof(read_bin() if payload is None else payload,
            values=VALUES if values is None else values,is_prepaid_only=kwargs.get('prepaid',False),
            recurring_consent=kwargs.get('consent'))

    def test_incomplete_or_rejected_reads_do_not_turn_into_supported_card(self):
        for payload in ({'data':{}}, {'data':{'credit_card_bin_info_shim':None}},
                        {**read_bin(),'errors':[{'message':'private error'}]}):
            self.assertFalse(self.proof(payload)['card_requirements_verified'])
        payload=read_bin();payload['data']['credit_card_bin_info_shim'].pop('require_3ds')
        self.assertFalse(self.proof(payload)['card_requirements_verified'])

    def test_required_fields_return_only_field_names_never_card_values(self):
        payload=read_bin();payload['data']['credit_card_bin_info_shim'].update(
            request_postal_code=True,require_phone_number_or_email=True)
        values={**VALUES,'postal_code':' ','holder':' '}
        result=self.proof(payload,values)
        self.assertEqual(result['required_fields'],['holder','postal_code','email_or_phone'])
        self.assertNotIn(VALUES['number'][:8],json.dumps(result));self.assertNotIn(VALUES['cvv'],json.dumps(result))
        self.assertTrue(self.proof(payload,{**VALUES,'email':'synthetic@example.invalid'})['card_requirements_verified'])

    def test_unsupported_and_invalid_flags_block_without_requesting_save(self):
        for value in (False,'true',1):
            payload=read_bin();payload['data']['credit_card_bin_info_shim']['is_supported']=value
            self.assertFalse(self.proof(payload)['card_requirements_verified'])

    def test_consent_rule_keeps_prepaid_exception_and_does_not_auto_accept(self):
        payload=read_bin();payload['data']['credit_card_bin_info_shim']['require_emandate']=True
        self.assertEqual(self.proof(payload)['code'],'CARD_RECURRING_CONSENT_REQUIRED')
        self.assertFalse(self.proof(payload,consent=1)['card_requirements_verified'])
        self.assertTrue(self.proof(payload,consent=True)['card_requirements_verified'])
        self.assertTrue(self.proof(payload,prepaid=True)['card_requirements_verified'])
        payload['data']['credit_card_bin_info_shim']['supports_recurring']=False
        self.assertTrue(self.proof(payload)['card_requirements_verified'])

    def test_recurring_consent_requirement_matches_original_module_for_each_branch(self):
        for mandate in (False,True):
            for recurring in (False,True):
                for prepaid in (False,True):
                    payload=read_bin();row=payload['data']['credit_card_bin_info_shim']
                    row.update(require_emandate=mandate,supports_recurring=recurring)
                    expected=node_parity({'mode':'consent','prepaid':prepaid,
                        'bin_info':{'requireEmandate':mandate,'isSupported':True,'supportsRecurring':recurring}})
                    self.assertEqual(self.proof(payload,prepaid=prepaid)['code']=='CARD_RECURRING_CONSENT_REQUIRED',expected)

    def test_bank_verification_is_reported_and_cvv_exemption_never_skips_input_validation(self):
        payload=read_bin();payload['data']['credit_card_bin_info_shim'].update(require_3ds=True,skip_cvv_for_eea_save=True)
        self.assertTrue(self.proof(payload)['bank_verification_required'])
        from app.payment_card_input import card_auth_fields
        with self.assertRaisesRegex(ValueError,'CARD_DATA_INVALID'):
            card_auth_fields({**VALUES,'cvv':''})

    def test_current_no_mismatch_country_branch_requires_live_country_and_payment_modes(self):
        result=country_policy_proof(read_screen(),country='US')
        self.assertTrue(result['country_policy_verified']);self.assertFalse(result['is_prepaid_only'])
        payload=read_screen();payload['data']['payment_account']['billable_account']['payment_modes']=['SUPPORTS_PREPAY']
        self.assertTrue(country_policy_proof(payload,country='US')['is_prepaid_only'])
        self.assertEqual(country_policy_proof(payload,country='DE')['code'],'CARD_BILLING_COUNTRY_MISMATCH')

    def test_tax_mismatch_missing_policy_or_unknown_modes_never_approve_country(self):
        for field,value in (('billing_flags',['TAX_COUNTRY_MISMATCH']),('billing_flags',None),
                ('payment_modes',[]),('billable_account_tax_info',None)):
            payload=read_screen();payload['data']['payment_account']['billable_account'][field]=value
            result=country_policy_proof(payload,country='US')
            self.assertFalse(result['country_policy_verified'])
            self.assertNotIn('hasAcknowledgedCountryMismatch',result)


if __name__=='__main__':unittest.main()
