import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as api
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


    async def test_pending_page_auth_retry_checks_the_page_surface_before_requeue(self):
        pending={'phase':'PAGE_CREATE_CLICK_INTENT','active_page_name':'PrgssTeam','active_before_ids':['123456']}
        view={'items':[{'profile_id':'10','status':'FAILED','error_code':'CHECKPOINT_REQUIRED','retryable':False,
            'tasks':[{'status':'FAILED','error_code':'CHECKPOINT_REQUIRED','payload':{
                'steps':['FAN_PAGES','BUSINESS','AD_ACCOUNT'],
                'parameters':{'FAN_PAGES':{'common_page':True}}}}],
            'provisioning_steps':[{'step':'FAN_PAGES','result':pending}]}]}
        for ready in (False,True):
            with self.subTest(restored=ready):
                fake=SimpleNamespace(job_view=AsyncMock(return_value=view),retry_failed=AsyncMock(return_value=int(ready)))
                worker=SimpleNamespace(enqueue_job=AsyncMock())
                state={'facebook_session_ready':ready,'auth_blocked':not ready,
                    'auth_error_code':'' if ready else 'CHECKPOINT_REQUIRED'}
                with patch.object(api,'store',fake),patch.object(api,'pool',worker), \
                     patch.object(api,'mirror',SimpleNamespace(enabled=False)), \
                     patch.object(api,'profile_preflight',AsyncMock(return_value=state)) as preflight, \
                     patch.dict(api._FP_AUTH_GATE_CACHE,{},clear=True):
                    result=await api.retry_failed('saved-page-job')
                preflight.assert_awaited_once_with('10',purpose='fan_pages')
                fake.retry_failed.assert_awaited_once_with('saved-page-job',excluded_profile_ids=set() if ready else {'10'})
                self.assertEqual(result.requeued,int(ready))
                self.assertEqual(result.blocked_profiles,[] if ready else ['10'])
                if ready:worker.enqueue_job.assert_awaited_once_with('saved-page-job')
                else:worker.enqueue_job.assert_not_awaited()
                self.assertEqual(pending,{'phase':'PAGE_CREATE_CLICK_INTENT','active_page_name':'PrgssTeam','active_before_ids':['123456']})

    async def test_bulk_auth_retry_uses_the_failed_stage_not_the_original_fp_payload(self):
        item={'profile_id':'10','status':'FAILED','error_code':'CHECKPOINT_REQUIRED',
            'tasks':[{'status':'FAILED','error_code':'CHECKPOINT_REQUIRED','payload':{
                'steps':['FAN_PAGES','BUSINESS','AD_ACCOUNT'],
                'parameters':{'FAN_PAGES':{'common_page':True}}}}],
            'provisioning_steps':[{'step':'FAN_PAGES','status':'SUCCESS'},
                {'step':'BUSINESS','status':'FAILED'}]}
        view={'items':[item]}
        fake=SimpleNamespace(job_view=AsyncMock(return_value=view),retry_failed=AsyncMock(return_value=0))
        with patch.object(api,'store',fake),patch.object(api,'pool',SimpleNamespace(enqueue_job=AsyncMock())), \
             patch.object(api,'mirror',SimpleNamespace(enabled=False)), \
             patch.object(api,'profile_preflight',AsyncMock(return_value={
                 'facebook_session_ready':False,'auth_blocked':True,
                 'auth_error_code':'CHECKPOINT_REQUIRED'})) as preflight:
            result=await api.retry_failed('saved-bulk-job')
        preflight.assert_awaited_once_with('10',purpose='business')
        self.assertEqual(result.blocked_profiles,['10'])
        fake.retry_failed.assert_awaited_once_with('saved-bulk-job',excluded_profile_ids={'10'})

    async def test_business_auth_retry_keeps_business_surface_preflight(self):
        with patch.object(api,'profile_preflight',AsyncMock(return_value={
                'facebook_session_ready':True,'auth_blocked':False})) as preflight:
            ready,blocked=await api._partition_auth_retry_profiles(['10'])
        preflight.assert_awaited_once_with('10',purpose='business')
        self.assertEqual(ready,{'10'})
        self.assertEqual(blocked,{})

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

    async def test_page_auth_failure_overrides_missing_bm_create_surface(self):
        for code in BROWSER_TERMINAL_ACCESS_CODES:
            with self.subTest(code=code):
                browser = SimpleNamespace(
                    preflight_fan_pages=AsyncMock(side_effect=BrowserBusinessError(
                        code, 'Page session blocked', retryable=False,
                    )),
                    preflight=AsyncMock(side_effect=BrowserBusinessError(
                        'BUSINESS_CREATE_UI_UNAVAILABLE', 'No BM create entry', retryable=True,
                    )),
                    discover_managed_pages=AsyncMock(side_effect=BrowserBusinessError(
                        code, 'Page session blocked', retryable=False,
                    )),
                )
                context = SimpleNamespace(pages=[], email='', first_name='', last_name='', display_name='', access_token='')
                pool = SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)))
                session = SimpleNamespace(
                    proxy_check=AsyncMock(return_value={}),
                    facebook_business_browser=AsyncMock(return_value=browser),
                )
                factory = MagicMock()
                factory.return_value.__aenter__ = AsyncMock(return_value=session)
                factory.return_value.__aexit__ = AsyncMock(return_value=False)
                with patch.object(api, 'pool', pool), patch.object(api, 'ProfileSession', factory), patch.dict(api._FP_AUTH_GATE_CACHE, {}, clear=True):
                    result = await api.profile_preflight('9')
                    self.assertTrue(result['auth_blocked'])
                    self.assertFalse(result['facebook_session_ready'])
                    self.assertEqual(result['auth_error_code'], code)
                    self.assertIsNone(api._cached_fp_auth_gate('9'))
                    with self.assertRaises(api.HTTPException) as failure:
                        await api._require_fp_auth_ready(['9'])
                    self.assertEqual(failure.exception.status_code, 409)
                    self.assertIn('9:' + code, failure.exception.detail)
                    browser.preflight_fan_pages.assert_awaited_once()

    async def test_fp_gate_uses_pages_and_does_not_depend_on_business_suite(self):
        browser = SimpleNamespace(
            preflight=AsyncMock(side_effect=BrowserBusinessError('SESSION_EXPIRED', 'Business Suite login', retryable=False)),
            preflight_fan_pages=AsyncMock(return_value=BrowserPreflightResult(
                ready=False, current_url='https://www.facebook.com/pages/create',
                diagnostics=['facebook_pages_authenticated'],
            )),
            discover_managed_pages=AsyncMock(),
        )
        context = SimpleNamespace(pages=[], email='', first_name='', last_name='', display_name='', access_token='')
        pool = SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)))
        session = SimpleNamespace(proxy_check=AsyncMock(return_value={}), facebook_business_browser=AsyncMock(return_value=browser))
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch.object(api, 'pool', pool), patch.object(api, 'ProfileSession', factory), patch.dict(api._FP_AUTH_GATE_CACHE, {}, clear=True):
            bm = await api.profile_preflight('9')
            self.assertTrue(bm['auth_blocked'])
            self.assertIsNone(api._cached_fp_auth_gate('9'))
            result = await api.profile_preflight('9', purpose='fan_pages')
            self.assertTrue(result['facebook_session_ready'])
            self.assertFalse(result['auth_blocked'])
            self.assertFalse(result['bm_route_ready'])
            await api._require_fp_auth_ready(['9'])
            browser.preflight.assert_awaited_once()
            browser.preflight_fan_pages.assert_awaited_once()
            browser.discover_managed_pages.assert_not_awaited()
