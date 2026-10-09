import copy
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from app.payment_card_http import SaveContext, save_card_http, KEY_DOC_ID, SAVE_DOC_ID
from app.payment_card_input import build_client_info
from tests.test_payment_ptt import VALUES
from tests.test_static_payment_card import ACCOUNT, BUSINESS, PAYMENT, NODE, CREDENTIAL, save_payload, payment_node


def read_account():
    return {'data':{'billable_account_by_asset_id':{'__typename':'AdAccount','id':ACCOUNT,
        'billing_payment_account':{'id':NODE,'payment_legacy_account_id':PAYMENT}}}}


def read_methods(cards=None):
    return {'data':{'billable_account_by_asset_id':{'__typename':'AdAccount','id':ACCOUNT,
        'owning_business':{'id':BUSINESS},'billing_payment_account':{'id':NODE,'primary':[],
        'billing_payment_methods_allowlist_customized': [{'credential':x} for x in cards or []],
        'primary_funding_source_customized':[]}}}}


def read_screen():
    node=payment_node()
    node['billing_payment_method_options']=[{'__typename':'AdAccountNewCreditCardOption',
        'check_make_default':True,'can_save_to_business':False,'verify_tokenization_required':False}]
    return {'data':{'payment_account':node}}


class FakeHTTP:
    """Transport hooks model the actual before-submit boundary, including lost replies."""
    def __init__(self):
        self.calls=[]; self.saved=False; self.lose_save=False; self.foreign_business=False
        self.key_error=False; self.auth_after_key=False; self.bank_required=False; self.lose_verification=False
    async def graphql(self,doc,variables,**kwargs):
        self.calls.append((doc,copy.deepcopy(variables),kwargs))
        if doc=='28797973873175785':return read_account()
        if doc=='28814526004898205':
            if self.saved and self.lose_verification:raise TimeoutError('private read failure')
            card={'__typename':'ExternalCreditCard','id':CREDENTIAL,'card_association_name':'VISA','last_four_digits':'1111'}
            p=read_methods([card] if self.saved else [])
            if self.foreign_business:p['data']['billable_account_by_asset_id']['owning_business']['id']='999888777'
            return p
        if doc=='27759194723782263':return read_screen()
        if doc==KEY_DOC_ID:
            return {'data':{'get_server_encryption_key':{'client_mutation_id':variables['input']['client_mutation_id'],
                'trust_chain':['synthetic leaf','synthetic intermediate'],'payments_error':{} if self.key_error else None}}}
        if doc==SAVE_DOC_ID:
            if self.auth_after_key:raise RuntimeError('private session precheck rejected')
            await kwargs['before_submit']()
            self.saved=True
            if self.lose_save:raise TimeoutError('secret exception should never be exported')
            p=save_payload('AUTHENTICATION_REQUIRED' if self.bank_required else 'SUCCESS')
            p['data']['xfb_billing_save_card_credential']['credit_card']['last_four_digits']='1111'
            return p
        raise AssertionError('Unexpected operation')


class PaymentHTTPTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SaveContext(payment=PAYMENT,country='US',currency='USD',
            client_info=build_client_info(color_depth=24,viewport_width=1440,viewport_height=1000),
            logging_data={'session_id':'synthetic-session'},include_new_fragment=False,runtime_verified=True,
            country_policy_verified=True)
    async def run_flow(self,web,**kwargs):
        self.persist=AsyncMock()
        # Crypto is separately checked byte-for-byte against supplied JS.
        with patch('app.payment_card_http.encrypt_card_token',return_value='synthetic_token'):
            return await save_card_http(web,account=ACCOUNT,business_id=BUSINESS,values=VALUES,
                context=kwargs.get('context',self.context()),persist_submit_intent=self.persist)

    async def test_full_http_chain_commits_only_independent_exact_credential_read(self):
        web=FakeHTTP(); result=await self.run_flow(web)
        self.assertEqual(result['status'],'LINKED');self.assertFalse(result['browser_started'])
        self.assertFalse(result['funding_verified']);self.persist.assert_awaited_once()
        self.assertEqual([x[0] for x in web.calls],['28797973873175785','28814526004898205',
            '27759194723782263',KEY_DOC_ID,SAVE_DOC_ID,'28814526004898205'])
        save=next(x for x in web.calls if x[0]==SAVE_DOC_ID)
        self.assertEqual(set(save[1]),{'input','getRiskVerificationInfoForAllCredentialsOnPaymentAccount',
                                     'paymentAccountID','includeCreateNewFromOldFragment'})
        self.assertEqual(save[1]['input']['card_data']['csc'],{'sensitive_string_value':'$e2ee'})
        self.assertEqual(save[2]['business_context_id'],BUSINESS)
        key=next(x for x in web.calls if x[0]==KEY_DOC_ID)
        self.assertEqual(key[1]['input']['target_account_id'],PAYMENT)
        self.assertNotIn('payment_product_id',key[1]['input'])

    async def test_runtime_context_gate_prevents_all_network_calls(self):
        for context in (replace(self.context(),runtime_verified=False),replace(self.context(),country_policy_verified=False)):
            web=FakeHTTP();result=await self.run_flow(web,context=context)
            self.assertEqual(result['code'],'CARD_PRIVATE_RUNTIME_CONTEXT_UNCONFIRMED')
            self.assertEqual(web.calls,[]);self.persist.assert_not_awaited()

    async def test_foreign_bm_and_foreign_payment_never_request_key_or_save(self):
        for foreign in ('business','payment'):
            web=FakeHTTP();web.foreign_business=foreign=='business'
            context=replace(self.context(),payment='999888777') if foreign=='payment' else self.context()
            result=await self.run_flow(web,context=context)
            self.assertFalse(result['submitted']);self.persist.assert_not_awaited()
            self.assertNotIn(KEY_DOC_ID,[x[0] for x in web.calls]);self.assertNotIn(SAVE_DOC_ID,[x[0] for x in web.calls])

    async def test_invalid_client_context_never_enters_http_flow(self):
        web=FakeHTTP();result=await self.run_flow(web,context=replace(self.context(),client_info=None))
        self.assertEqual(result['code'],'CARD_CLIENT_CONTEXT_REQUIRED');self.assertEqual(web.calls,[])

    async def test_lost_save_response_keeps_no_replay_and_never_sends_second_save(self):
        web=FakeHTTP();web.lose_save=True;result=await self.run_flow(web)
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED');self.assertTrue(result['retry_blocked'])
        self.assertTrue(result['submitted']);self.persist.assert_awaited_once()
        self.assertEqual([x[0] for x in web.calls].count(SAVE_DOC_ID),1)
        self.assertNotIn('secret',str(result))

    async def test_bank_confirmation_does_not_commit_or_reveal_challenge(self):
        web=FakeHTTP();web.bank_required=True;result=await self.run_flow(web)
        self.assertEqual(result['status'],'ACTION_REQUIRED');self.assertTrue(result['retry_blocked'])
        self.assertNotIn('private',str(result));self.assertEqual([x[0] for x in web.calls].count('28814526004898205'),1)

    async def test_lost_verification_read_retains_exact_saved_credential_for_reconciliation(self):
        web=FakeHTTP();web.lose_verification=True;result=await self.run_flow(web)
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED')
        self.assertEqual(result['code'],'CARD_SAVE_LINK_VERIFICATION_PENDING')
        self.assertEqual(result['credential']['id'],CREDENTIAL);self.assertTrue(result['retry_blocked'])
        self.assertNotIn('private',str(result))

    async def test_key_rejection_and_session_failure_before_save_never_mark_submitted(self):
        for mode in ('key_error','auth_after_key'):
            web=FakeHTTP();setattr(web,mode,True);result=await self.run_flow(web)
            self.assertFalse(result['submitted']);self.persist.assert_not_awaited()
            self.assertNotIn('private',str(result))

    async def test_durable_intent_failure_prevents_transport_submit(self):
        web=FakeHTTP(); persist=AsyncMock(side_effect=RuntimeError('private storage failure'))
        with patch('app.payment_card_http.encrypt_card_token',return_value='synthetic_token'):
            result=await save_card_http(web,account=ACCOUNT,business_id=BUSINESS,values=VALUES,
                context=self.context(),persist_submit_intent=persist)
        self.assertFalse(result['submitted']);self.assertFalse(web.saved);self.assertNotIn('private',str(result))


if __name__=='__main__':unittest.main()
