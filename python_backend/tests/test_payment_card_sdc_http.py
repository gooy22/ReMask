"""Meta-observed SDC authorisation and descriptor-code HTTP contracts."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.payment_card_verification import (
    SDC_SEND_DOC_ID, SDC_VERIFY_CODE_DOC_ID, _send_sdc_once,
    _verify_sdc_code, verify_payment_card_http,
)


class HttpFixture:
    graphql = None
    def __init__(self, ctx, timeout_seconds=30):
        self.ctx = ctx
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def facebook_web(self):
        return SimpleNamespace(graphql=self.graphql)


class CardSDCHttpTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = SimpleNamespace(path=str(Path(self.tmp.name)/'state.sqlite'))
        self.common = {'profile_id':'16', 'account_id':'123456789',
                       'browser_started':False, 'verification_triggered':False,
                       'funding_verified':False}
        self.scope = {'profile':'16', 'account':'123456789',
                      'payment_account':'555666777','business':'987654321',
                      'credential':'creditcard_node','card_id':'card_'+'f'*24}
    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_send_once_persists_auth_before_post_and_cannot_resend(self):
        mock = AsyncMock(return_value={'data':{'send_dynamic_descriptor_auth':{'sent': True}}})
        async def fenced(*args, **kwargs):
            await kwargs['before_submit']()
            return await mock(*args, **kwargs)
        web = SimpleNamespace(graphql=fenced)
        first = await _send_sdc_once(web, state=self.state, common=self.common, **self.scope)
        self.assertEqual(first['code'], 'SDC_AUTH_SENT_WAIT_CODE')
        self.assertTrue(first['verification_triggered'])
        self.assertEqual(mock.await_count, 1)
        args, kw = mock.await_args
        self.assertEqual(args[0], SDC_SEND_DOC_ID)
        self.assertEqual(args[1], {'input':{
            'billable_account_payment_legacy_account_id': '555666777',
            'credential_id':'creditcard_node','intent':'SFI'}})
        self.assertEqual(kw['business_context_id'],'987654321')
        second = await _send_sdc_once(web, state=self.state, common=self.common, **self.scope)
        self.assertEqual(second['code'],'SDC_AUTH_ALREADY_ATTEMPTED')
        self.assertEqual(mock.await_count,1)

    async def test_ambiguous_response_never_sends_again(self):
        mock = AsyncMock(side_effect=TimeoutError('timeout'))
        async def fenced(*args, **kwargs):
            await kwargs['before_submit']()
            return await mock(*args, **kwargs)
        web = SimpleNamespace(graphql=fenced)
        result = await _send_sdc_once(web, state=self.state, common=self.common, **self.scope)
        self.assertEqual(result['code'],'SDC_AUTH_RESULT_UNKNOWN')
        self.assertEqual(result['verification_stage'],'RESULT_UNKNOWN')
        await _send_sdc_once(web, state=self.state, common=self.common, **self.scope)
        self.assertEqual(mock.await_count,1)

    async def test_auth_precheck_failure_does_not_reserve_or_charge(self):
        first_web = SimpleNamespace(graphql=AsyncMock(side_effect=ValueError('business login gate')))
        first = await _send_sdc_once(first_web, state=self.state,
                                     common=self.common, **self.scope)
        self.assertEqual(first['code'], 'SDC_AUTH_PRECHECK_UNAVAILABLE')
        self.assertFalse(first['verification_triggered'])
        mock = AsyncMock(return_value={'data':{'send_dynamic_descriptor_auth':{'sent':True}}})
        async def fenced(*args, **kwargs):
            await kwargs['before_submit']()
            return await mock(*args, **kwargs)
        second = await _send_sdc_once(SimpleNamespace(graphql=fenced),
                                      state=self.state, common=self.common, **self.scope)
        self.assertEqual(second['code'], 'SDC_AUTH_SENT_WAIT_CODE')
        self.assertEqual(mock.await_count, 1)

    async def test_transport_without_submit_fence_can_never_claim_bank_request(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={
            'data': {'send_dynamic_descriptor_auth': {'sent': True}}}))
        result = await _send_sdc_once(web, state=self.state,
                                      common=self.common, **self.scope)
        self.assertEqual(result['code'], 'SDC_AUTH_SUBMIT_FENCE_MISSING')
        self.assertFalse(result['verification_triggered'])

    async def test_code_is_sent_only_to_meta_and_never_returned(self):
        mock = AsyncMock(return_value={'data':{'billing_verify_sdc_code':{'verified': True}}})
        result = await _verify_sdc_code(SimpleNamespace(graphql=mock),
            code='A9B2', account='123456789',payment_account='555666777',
            business='987654321', credential='creditcard_node', common=self.common)
        self.assertEqual(result['code'],'SDC_CODE_VERIFIED_BY_META')
        self.assertEqual(result['status'],'VERIFIED')
        self.assertNotIn('A9B2',str(result))
        args, kwargs = mock.await_args
        self.assertEqual(args[0],SDC_VERIFY_CODE_DOC_ID)
        self.assertEqual(args[1]['input']['verification_code'],'A9B2')
        mock.reset_mock()
        invalid = await _verify_sdc_code(SimpleNamespace(graphql=mock),
            code='12345678', account='123456789', payment_account='555666777',
            business='987654321',credential='creditcard_node',common=self.common)
        self.assertEqual(invalid['code'],'SDC_CODE_REQUIRED')
        mock.assert_not_awaited()

    async def test_pending_state_refuses_new_authorization_even_when_requested(self):
        payload = {'operation':'authorize','account_id':'123456789',
            'card_id':'card_'+'f'*24,'card_brand':'Visa','card_last4':'1234'}
        evidence = {'account_id':'123456789','account_scope_verified':True,
                    'payment_account_id':'555666777','payment_account_node_id':'node_55'}
        methods = {'account_scope_verified':True,'business_scope_verified':True,
                   'payment_account_relation_verified':True,'methods_query_verified':True,
                   'payment_methods':[{'credential_id':'creditcard_node','type':'Visa',
                                      'last4':'1234','needs_verification':False,
                                      'verification_tasks_observed':True,'verification_tasks':[],
                                      'card_confirmation_status':'CLEAR'}]}
        HttpFixture.graphql = AsyncMock()
        with patch('app.payment_card_verification.resolve_payment_asset',
                   AsyncMock(return_value={'business_id':'987654321'})), \
             patch('app.session.ProfileSession',HttpFixture), \
             patch('app.payment_card_verification.account_proof',return_value=evidence), \
             patch('app.payment_card_verification.methods_proof',return_value=methods), \
             patch('app.payment_card_verification.sdc_candidate_proof',return_value={
                 'sdc_screen_verified':True,'sdc_candidate':True,'sdc_credential_match':True,
                 'sdc_usability':'PENDING_VERIFICATION',
                 'sdc_action_credential_id':'meta_sdc_credential_123'}), \
             patch('app.payment_card_verification.execute',AsyncMock(return_value={})) as read:
            result=await verify_payment_card_http(
                SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user':'1'}))),
                '16',payload,state=self.state)
        self.assertEqual(result['code'],'SDC_AUTH_PENDING_WAIT_CODE')
        self.assertFalse(result['verification_triggered'])
        HttpFixture.graphql.assert_not_awaited()
        self.assertEqual([r.args[1] for r in read.await_args_list],
                         ['READ_ACCOUNT','READ_METHODS','READ_SDC_CANDIDATES'])
