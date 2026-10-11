"""Meta-observed SDC authorisation and descriptor-code HTTP contracts."""
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.payment_card_intents import BankVerificationLedger
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

    async def test_concurrent_dispatch_reaches_meta_once_and_other_scope_is_independent(self):
        mock = AsyncMock(return_value={'data':{'send_dynamic_descriptor_auth':{'sent':True}}})
        async def fenced(*args, **kwargs):
            await kwargs['before_submit']()
            return await mock(*args, **kwargs)
        web = SimpleNamespace(graphql=fenced)
        results = await asyncio.gather(*(
            _send_sdc_once(web, state=self.state, common=self.common, **self.scope)
            for _ in range(2)))
        self.assertCountEqual([r['code'] for r in results],
                              ['SDC_AUTH_SENT_WAIT_CODE', 'SDC_AUTH_ALREADY_ATTEMPTED'])
        self.assertEqual(mock.await_count, 1)
        other = {**self.scope, 'profile':'17', 'account':'987654321',
                 'payment_account':'777888999'}
        result = await _send_sdc_once(web, state=self.state, common=self.common, **other)
        self.assertEqual(result['code'], 'SDC_AUTH_SENT_WAIT_CODE')
        self.assertEqual(mock.await_count, 2)

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
            business='987654321', credential='creditcard_node', common=self.common,
            state=self.state, profile='16', card_id='card_'+'f'*24)
        self.assertEqual(result['code'],'SDC_CODE_VERIFIED_BY_META')
        self.assertTrue(result['verification_recorded'])
        proof=await BankVerificationLedger(self.state.path).reserve(
            profile='16',account='123456789',payment_account='555666777',
            credential='creditcard_node',card_id='card_'+'f'*24,flow='SDC')
        self.assertFalse(proof['reserved'])
        self.assertEqual(proof['stage'], 'META_CONFIRMED')
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

    async def pending_result(self, operation):
        payload = {'operation':operation,'account_id':'123456789',
            'card_id':'card_'+'f'*24,'card_brand':'Visa','card_last4':'1234'}
        evidence = {'account_id':'123456789','account_scope_verified':True,
                    'payment_account_id':'555666777','payment_account_node_id':'node_55'}
        methods = {'account_scope_verified':True,'business_scope_verified':True,
                   'payment_account_relation_verified':True,'methods_query_verified':True,
                   'payment_methods':[{'credential_id':'creditcard_node','type':'Visa',
                                      'last4':'1234','needs_verification':False,
                                      'verification_tasks_observed':True,'verification_tasks':[],
                                      'card_confirmation_status':'CLEAR'}]}
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
        self.assertEqual([r.args[1] for r in read.await_args_list],
                         ['READ_ACCOUNT','READ_METHODS','READ_SDC_CANDIDATES'])
        return result

    async def test_failed_code_preserves_reason_without_secrets_or_new_auth(self):
        async def graphql(doc, variables, **kw):
            self.assertEqual(doc, SDC_VERIFY_CODE_DOC_ID)
            await kw['before_submit']()
            return {'errors':[{'message':'Code A9B2 rejected for secret_credential_987 555666777',
                               'extensions':{'code':12345}}], 'data':None}
        result=await _verify_sdc_code(SimpleNamespace(graphql=graphql),
            code='A9B2', account='123456789',payment_account='555666777',
            business='987654321', credential='secret_credential_987', common=self.common)
        self.assertEqual(result['code'],'SDC_CODE_RESULT_UNVERIFIED')
        self.assertEqual(result['verification_response_kind'],'graphql_error')
        self.assertEqual(result['meta_error_codes'],[12345])
        self.assertTrue(result['submitted'])
        self.assertFalse(result['verification_triggered'])
        for secret in ('A9B2','secret_credential_987','555666777'):
            self.assertNotIn(secret,str(result))

    async def test_incomplete_code_response_is_not_incorrect_code(self):
        for response in ({'data':None},{'data':{'billing_verify_sdc_code':{}}}):
            mock=AsyncMock(return_value=response)
            result=await _verify_sdc_code(SimpleNamespace(graphql=mock),
                code='A9B2',account='123456789',payment_account='555666777',
                business='987654321',credential='credential',common=self.common)
            self.assertEqual(result['code'],'SDC_CODE_RESULT_UNVERIFIED')
            self.assertEqual(mock.await_count,1)

    async def test_code_transport_timeout_keeps_submission_truth_and_never_retries(self):
        calls=[]
        async def graphql(doc,variables,**kw):
            calls.append(doc)
            await kw['before_submit']()
            raise TimeoutError('untrusted secret body A9B2')
        result=await _verify_sdc_code(SimpleNamespace(graphql=graphql),
            code='A9B2',account='123456789',payment_account='555666777',
            business='987654321',credential='credential',common=self.common)
        self.assertTrue(result['submitted'])
        self.assertFalse(result['verification_triggered'])
        self.assertNotIn('A9B2',str(result))
        self.assertEqual(calls,[SDC_VERIFY_CODE_DOC_ID])

    async def test_pending_without_local_attempt_is_ready_but_read_does_not_dispatch(self):
        HttpFixture.graphql = AsyncMock()
        result = await self.pending_result('verify')
        self.assertEqual(result['code'], 'SDC_AUTH_READY')
        self.assertEqual(result['verification_stage'], 'PENDING_VERIFICATION')
        self.assertFalse(result['submitted'])
        self.assertFalse(result['verification_triggered'])
        HttpFixture.graphql.assert_not_awaited()

    async def test_pending_first_authorization_sends_exact_meta_action_id_once(self):
        mock = AsyncMock(return_value={'data':{'send_dynamic_descriptor_auth':{'sent':True}}})
        async def fenced(*args, **kwargs):
            await kwargs['before_submit']()
            return await mock(*args, **kwargs)
        HttpFixture.graphql = staticmethod(fenced)
        first = await self.pending_result('authorize')
        self.assertEqual(first['code'], 'SDC_AUTH_SENT_WAIT_CODE')
        self.assertTrue(first['submitted'])
        self.assertTrue(first['verification_triggered'])
        self.assertEqual(mock.await_args.args[1]['input']['credential_id'], 'meta_sdc_credential_123')
        second = await self.pending_result('authorize')
        self.assertEqual(second['code'], 'SDC_AUTH_PENDING_WAIT_CODE')
        self.assertEqual(second['bank_request_evidence'], 'REMASK_LOCAL_REQUEST')
        self.assertEqual(second['previous_request_stage'], 'CHALLENGE_READY')
        self.assertEqual(mock.await_count, 1)

    async def test_pending_unreadable_history_never_dispatches(self):
        HttpFixture.graphql = AsyncMock()
        with patch.object(BankVerificationLedger, 'read_existing',
                          AsyncMock(side_effect=OSError('unavailable'))):
            result = await self.pending_result('authorize')
        self.assertEqual(result['code'], 'SDC_AUTH_PENDING_WAIT_CODE')
        self.assertEqual(result['bank_request_evidence'], 'LOCAL_HISTORY_UNAVAILABLE')
        HttpFixture.graphql.assert_not_awaited()

    async def test_pending_prior_attempts_including_crash_and_confirmed_never_dispatch(self):
        HttpFixture.graphql = AsyncMock()
        for stage in ('RESERVED', 'REQUEST_SENT', 'RESULT_UNKNOWN',
                      'CHALLENGE_READY', 'SERVER_REJECTED', 'META_CONFIRMED'):
            with self.subTest(stage=stage), patch.object(BankVerificationLedger, 'read_existing',
                    AsyncMock(return_value={'same_card': True, 'stage': stage})):
                result = await self.pending_result('authorize')
                self.assertEqual(result['code'], 'SDC_AUTH_PENDING_WAIT_CODE')
                self.assertEqual(result['previous_request_stage'], stage)
        HttpFixture.graphql.assert_not_awaited()

    async def test_previous_bank_attempt_can_be_inspected_without_new_request(self):
        ledger = BankVerificationLedger(self.state.path)
        scope = {name: self.scope[name] for name in
                 ('profile', 'account', 'payment_account', 'credential', 'card_id')}
        self.assertIsNone(await ledger.read_existing(**scope))
        attempt = await ledger.reserve(**scope, flow='SDC')
        self.assertTrue(attempt['reserved'])
        self.assertTrue(await ledger.mark(attempt['attempt_id'], 'REQUEST_SENT'))
        self.assertTrue(await ledger.mark(attempt['attempt_id'], 'CHALLENGE_READY'))
        self.assertEqual(await ledger.read_existing(**scope),
                         {'stage':'CHALLENGE_READY', 'same_card':True})
        self.assertEqual(await ledger.read_existing(
            **{**scope, 'card_id':'card_'+'a'*24}),
            {'stage':'CHALLENGE_READY', 'same_card':False})
        self.assertIsNone(await ledger.read_existing(
            **{**scope, 'account':'99999999'}))
