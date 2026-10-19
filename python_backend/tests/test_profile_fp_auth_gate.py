import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as api
from fb_worker import AuthenticationError
from app.facebook_business_browser import BrowserBusinessError, BrowserPreflightResult, BROWSER_TERMINAL_ACCESS_CODES


class ProfileFpAuthGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_100_automatic_profiles_enqueue_without_http_browser_preflights(self):
        request=api.CreateJobRequest(profiles=[{'profile_id':str(i),'tasks':[{
            'action':'provisioning','payload':{'steps':['PROXY_CHECK','FAN_PAGES','PAGE_ACCESS'],
                'parameters':{'FAN_PAGES':{'common_page':True}}}}]} for i in range(1,101)])
        fake=SimpleNamespace(create_job=AsyncMock(return_value=('bulk-100',True)),
            job_view=AsyncMock(return_value={'status':'QUEUED','items_total':100}))
        worker=SimpleNamespace(enqueue_job=AsyncMock())
        with patch.object(api,'store',fake),patch.object(api,'pool',worker), \
             patch.object(api,'mirror',SimpleNamespace(enabled=False)), \
             patch.object(api,'_require_fp_auth_ready',new=AsyncMock(side_effect=AssertionError('HTTP browser preflight'))) as preflight:
            accepted=await api.create_job(request)
        self.assertEqual(accepted.items_total,100)
        worker.enqueue_job.assert_awaited_once_with('bulk-100'); preflight.assert_not_awaited()

    async def test_automatic_bulk_retry_does_not_preflight_or_block_other_profiles(self):
        view={'items':[{'profile_id':str(i),'status':'FAILED','tasks':[{'payload':{
            'steps':['FAN_PAGES'],'parameters':{'FAN_PAGES':{'common_page':True}}}}]}
            for i in range(1,101)]}
        fake=SimpleNamespace(job_view=AsyncMock(return_value=view),retry_failed=AsyncMock(return_value=100))
        worker=SimpleNamespace(enqueue_job=AsyncMock())
        with patch.object(api,'store',fake),patch.object(api,'pool',worker), \
             patch.object(api,'mirror',SimpleNamespace(enabled=False)), \
             patch.object(api,'_require_fp_auth_ready',new=AsyncMock(side_effect=AssertionError('HTTP browser preflight'))) as preflight:
            result=await api.retry_failed('bulk-100')
        self.assertEqual(result.requeued,100); preflight.assert_not_awaited()
        worker.enqueue_job.assert_awaited_once_with('bulk-100')

    async def test_page_policy_consent_rejects_unrelated_failed_job(self):
        fake=SimpleNamespace(job_view=AsyncMock(return_value={'items':[{'error_code':'SESSION_EXPIRED','tasks':[]}]}))
        with patch.object(api,'store',fake):
            with self.assertRaises(api.HTTPException) as failure:
                await api.retry_failed('saved-job',{'consent_page_policies':True})
        self.assertEqual(failure.exception.status_code,409)

    async def test_page_policy_consent_is_bound_to_exact_reviewed_common_page(self):
        view={'items':[{'error_code':'PAGE_POLICIES_CONFIRMATION_REQUIRED','tasks':[{
            'payload':{'steps':['FAN_PAGES'],'parameters':{'FAN_PAGES':{'common_page':True}}}}]}]}
        fake=SimpleNamespace(job_view=AsyncMock(return_value=view),retry_failed=AsyncMock(return_value=1))
        worker=SimpleNamespace(provisioning_state=object(),enqueue_job=AsyncMock())
        page_store=SimpleNamespace(get=AsyncMock(return_value={'name':'PrgssTeam','owner_profile_id':'9'}),patch=AsyncMock())
        with patch.object(api,'store',fake),patch.object(api,'pool',worker),patch.object(api,'mirror',SimpleNamespace(enabled=False)), \
             patch.object(api,'_require_fp_auth_ready',new=AsyncMock()), \
             patch('app.provisioning.advertising_page.AdvertisingPageStore',return_value=page_store):
            result=await api.retry_failed('saved-job',{'consent_page_policies':True})
        self.assertEqual(result.requeued,1)
        self.assertEqual(page_store.patch.call_args.kwargs['policies_name'],'PrgssTeam')
        self.assertEqual(page_store.patch.call_args.kwargs['policies_owner_profile_id'],'9')
        worker.enqueue_job.assert_awaited_once_with('saved-job')

    async def test_page_auth_failure_is_checked_by_private_HTTP_without_browser(self):
        for code in BROWSER_TERMINAL_ACCESS_CODES:
            with self.subTest(code=code):
                context=SimpleNamespace(pages=[],cookies={'c_user':'123'},access_token='')
                web=SimpleNamespace(bootstrap=AsyncMock(side_effect=BrowserBusinessError(code,'Blocked',retryable=False)))
                session=SimpleNamespace(proxy_check=AsyncMock(return_value={}),facebook_web=AsyncMock(return_value=web),
                    facebook_business_browser=AsyncMock(side_effect=AssertionError('No browser')))
                factory=MagicMock();factory.return_value.__aenter__=AsyncMock(return_value=session)
                factory.return_value.__aexit__=AsyncMock(return_value=False)
                worker=SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)))
                with patch.object(api,'pool',worker),patch.object(api,'ProfileSession',factory),patch.dict(api._FP_AUTH_GATE_CACHE,{},clear=True):
                    result=await api.profile_preflight('9',purpose='fan_pages')
                    self.assertTrue(result['auth_blocked']);self.assertFalse(result['facebook_session_ready'])
                    self.assertEqual(result['auth_error_code'],code)
                    self.assertFalse(result['browser_started'])
                    with self.assertRaises(api.HTTPException):await api._require_fp_auth_ready(['9'])
                session.facebook_business_browser.assert_not_awaited()

    async def test_fp_gate_uses_HTTP_pages_and_does_not_depend_on_Business_Suite(self):
        from app.facebook_page_discovery import PageDiscoveryResult
        context=SimpleNamespace(pages=[],cookies={'c_user':'123'},access_token='')
        web=SimpleNamespace(bootstrap=AsyncMock(return_value=SimpleNamespace(actor_id='123',fb_dtsg='fixture')))
        session=SimpleNamespace(proxy_check=AsyncMock(return_value={}),facebook_web=AsyncMock(return_value=web),
            facebook_business_browser=AsyncMock(side_effect=AssertionError('No browser')))
        factory=MagicMock();factory.return_value.__aenter__=AsyncMock(return_value=session)
        factory.return_value.__aexit__=AsyncMock(return_value=False)
        worker=SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)))
        with patch.object(api,'pool',worker),patch.object(api,'ProfileSession',factory),patch.dict(api._FP_AUTH_GATE_CACHE,{},clear=True), \
             patch('app.private_page_preflight.list_pages_via_private_graphql',new=AsyncMock(return_value=PageDiscoveryResult(pages=[],source='private',inventory_complete=True))):
            result=await api.profile_preflight('9',purpose='fan_pages')
            self.assertTrue(result['facebook_session_ready']);self.assertFalse(result['auth_blocked'])
            self.assertFalse(result['bm_route_ready']);self.assertTrue(result['private_pages']['inventory_complete'])
            await api._require_fp_auth_ready(['9'])
        session.facebook_business_browser.assert_not_awaited()
