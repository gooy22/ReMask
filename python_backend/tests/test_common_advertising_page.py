import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock,patch

from app.provisioning.advertising_page import AdvertisingPageStore,ensure_common_page
from app.provisioning.models import ProvisioningError,ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.provisioning.service import ProvisioningService
from app.provisioning.page_access_handler import page_access_handler,_ads_only,_request_target_page_access
from app.facebook_business_browser import BrowserBusinessError

PAGE='1270757506131209'; BM='1476521050987548'; RK='958245207339458'


class CommonPageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        lock=asyncio.Lock()
        self.enterContext(patch('app.provisioning.advertising_page._PAGE_LOCK',lock))
        self.enterContext(patch('app.provisioning.page_access_handler._PAGE_LOCK',lock))
        self.state=ProvisioningStateStore(str(Path(self.tmp.name)/'state.sqlite'))
        await self.state.init()

    async def test_parallel_units_create_and_confirm_one_page_and_reuse_saved_identity(self):
        calls=[]
        async def create(session,params,snapshot,**kwargs):
            calls.append((params,kwargs))
            await kwargs['provisioning_state'].checkpoint(kwargs['item_id'],kwargs['profile_id'],kwargs['scope_key'],
                ProvisioningStep.FAN_PAGES,{'phase':'PAGE_CREATE_CLICK_INTENT'})
            await asyncio.sleep(.01)
            return {'pages':[{'id':PAGE,'name':'PrgssTeam','main_business_confirmed':True}],'page_ids':[PAGE]}
        session=SimpleNamespace(context=SimpleNamespace(profile_id='9'))
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',side_effect=create):
            a,b=await asyncio.gather(*[ensure_common_page(session,{},self.state,None) for _ in range(2)])
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0]['names'],['PrgssTeam'])
        self.assertFalse(calls[0][0]['confirm_main_business'])
        self.assertEqual(a['page_ids'],b['page_ids'])
        self.assertEqual((await AdvertisingPageStore(self.state).get())['page_id'],PAGE)
        self.assertEqual((await self.state.latest_profile_fan_pages('9'))[0]['id'],PAGE)

    async def test_many_jobs_create_one_page_per_real_facebook_profile(self):
        calls=[]
        async def create(session,params,snapshot,**kwargs):
            profile=session.context.profile_id; calls.append(profile)
            return {'pages':[{'id':'123456789'+profile,'name':'PrgssTeam'}]}
        contexts=[SimpleNamespace(profile_id=profile,cookies={'c_user':'6150000000000'+profile},pages=[])
            for profile in ['1','2','3']]
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',side_effect=create):
            results=await asyncio.gather(*[ensure_common_page(SimpleNamespace(context=context),
                {'policies_accepted':True},self.state,None) for context in contexts for _ in range(3)])
        self.assertEqual(sorted(calls),['1','2','3'])
        for index,context in enumerate(contexts):
            self.assertEqual({result['page_ids'][0] for result in results[index*3:index*3+3]},
                {'123456789'+context.profile_id})

    async def test_duplicate_local_profiles_of_same_facebook_uid_reuse_page(self):
        a=SimpleNamespace(profile_id='2',cookies={'c_user':'61594285240608'},pages=[])
        b=SimpleNamespace(profile_id='5',cookies={'c_user':'61594285240608'},pages=[])
        await AdvertisingPageStore.for_context(self.state,a).patch(page_id=PAGE,name='PrgssTeam')
        create=AsyncMock()
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',create):
            result=await ensure_common_page(SimpleNamespace(context=b),{},self.state,None)
        create.assert_not_awaited(); self.assertEqual(result['page_ids'],[PAGE])

    async def test_existing_profile_page_is_reused_without_cross_profile_owner(self):
        context=SimpleNamespace(profile_id='8',cookies={'c_user':'61594897075733'},
            pages=[{'id':'111111111','name':'PrgssTeam'}])
        await AdvertisingPageStore(self.state).patch(page_id=PAGE,name='PrgssTeam',owner_profile_id='9')
        create=AsyncMock()
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',create):
            result=await ensure_common_page(SimpleNamespace(context=context),{},self.state,None)
        self.assertEqual(result['page_ids'],['111111111']); create.assert_not_awaited()
        self.assertEqual((await AdvertisingPageStore(self.state).get())['page_id'],PAGE)

    async def test_existing_brand_page_does_not_require_personal_rk_onboarding_confirm(self):
        await self.state.complete('existing','9','old',ProvisioningStep.FAN_PAGES,
            {'pages':[{'id':PAGE,'name':'PrgssTeam','main_business_confirmed':False}]})
        handler=AsyncMock(return_value={'pages':[{'id':PAGE,'name':'PrgssTeam','main_business_confirmed':True}]})
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',handler):
            await ensure_common_page(SimpleNamespace(context=SimpleNamespace(profile_id='9')),{},self.state,None)
        handler.assert_not_awaited()
        self.assertEqual((await AdvertisingPageStore(self.state).get())['page_id'],PAGE)
        self.assertFalse((await AdvertisingPageStore(self.state).get())['main_business_confirmed'])

    async def test_same_name_managed_pages_choose_one_stable_identity_without_picker(self):
        await self.state.complete('old','9','old',ProvisioningStep.FAN_PAGES,
            {'pages':[{'id':p,'name':'PrgssTeam'} for p in [PAGE,'999999999']]})
        create=AsyncMock()
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',create):
            result=await ensure_common_page(SimpleNamespace(context=SimpleNamespace(profile_id='9')),{},self.state,None)
        self.assertEqual(result['page_ids'],['999999999']); create.assert_not_awaited()
        self.assertEqual((await AdvertisingPageStore(self.state).get())['page_id'],'999999999')

    async def test_personal_rk_is_rejected_before_any_page_creation_or_permission_change(self):
        with patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as exc:
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':BM})),{},
                    {'business_id':BM,'ad_account_id':RK},provisioning_state=self.state,profile_id='9',item_id='bad',scope_key='bad')
        self.assertEqual(exc.exception.code,'CREATED_BUSINESS_RK_REQUIRED'); create.assert_not_awaited()

    async def test_page_access_retry_preserves_completed_rk_and_does_not_create_another(self):
        calls=[]
        async def rk(session,params,snapshot,**kwargs):
            calls.append('RK')
            await self.state.remember_entity('9','one',ProvisioningStep.BUSINESS,{'business_id':BM})
            return {'ad_account_id':RK,'business_id':BM}
        async def access(*args,**kwargs):
            calls.append('PAGE')
            if calls.count('PAGE')==1: raise ProvisioningError('PAGE_SHARE_RESULT_UNKNOWN','pending',retryable=True)
            return {'page_id':PAGE,'business_id':BM,'ad_account_id':RK,'ad_account_page_access_verified':True}
        service=ProvisioningService(self.state)
        session=SimpleNamespace(close_business_browser=AsyncMock())
        request=dict(item_id='one',profile_id='9',context=SimpleNamespace(),session=session,
            payload={'scope_key':'one','steps':['AD_ACCOUNT'],'parameters':{'AD_ACCOUNT':{'use_common_page':True}}})
        with patch('app.provisioning.service.get_handler',side_effect=lambda step:rk if step=='AD_ACCOUNT' else access), \
             patch('app.provisioning.service._await_profile_mutation_cooldown',new=AsyncMock()):
            with self.assertRaises(ProvisioningError): await service.run(**request)
            self.assertEqual((await self.state.step('one',ProvisioningStep.AD_ACCOUNT))['status'],'SUCCESS')
            await service.run(**request)
        self.assertEqual(calls,['RK','PAGE','PAGE'])

    async def test_existing_rk_requires_live_exact_business_binding_before_page_mutation(self):
        await self.state.set_running("existing","9","existing",ProvisioningStep.PAGE_ACCESS)
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock(return_value=(False,[]))) as verify, \
             patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as exc:
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'})),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='existing',scope_key='existing')
        self.assertEqual(exc.exception.code,'BUSINESS_RK_RELATION_UNVERIFIED')
        self.assertEqual(verify.call_args.kwargs['business_id'],BM)
        self.assertEqual(verify.call_args.kwargs['expected_ad_account_id'],RK)
        create.assert_not_awaited()

    async def test_owner_created_rk_reconciliation_uses_saved_exact_create_without_false_live_claim(self):
        await AdvertisingPageStore(self.state).patch(page_id=PAGE,name='PrgssTeam',owner_profile_id='9',owner_business_id=BM)
        await self.state.complete('original','9','original',ProvisioningStep.AD_ACCOUNT,{'business_id':BM,'ad_account_id':'act_'+RK})
        await self.state.set_running('recovery','9','recovery',ProvisioningStep.PAGE_ACCESS)
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock()) as verify, \
             patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock(side_effect=RuntimeError('stop before Page mutation'))):
            with self.assertRaisesRegex(RuntimeError,'stop before Page mutation'):
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'})),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='recovery',scope_key='recovery')
        verify.assert_not_awaited()
        result=(await self.state.step('recovery',ProvisioningStep.PAGE_ACCESS))['result']
        self.assertTrue(result['created_binding_confirmed'])
        self.assertFalse(result['inventory_binding_verified'])

    async def test_saved_access_before_rk_runs_rk_first_and_reuses_business(self):
        await self.state.complete('ordered','9','ordered',ProvisioningStep.BUSINESS,{'business_id':BM})
        observed=[]
        async def rk(session,params,snapshot,**kwargs):
            observed.append('RK'); self.assertEqual(snapshot['business_id'],BM)
            return {'business_id':BM,'ad_account_id':RK}
        async def access(session,params,snapshot,**kwargs):
            observed.append('PAGE'); self.assertEqual(snapshot['ad_account_id'],RK)
            return {'business_id':BM,'ad_account_id':RK,'page_id':PAGE}
        with patch('app.provisioning.service.get_handler',side_effect=lambda step:rk if step=='AD_ACCOUNT' else access), \
             patch('app.provisioning.service._await_profile_mutation_cooldown',new=AsyncMock()):
            await ProvisioningService(self.state).run(item_id='ordered',profile_id='9',context=SimpleNamespace(),session=SimpleNamespace(),
                payload={'scope_key':'ordered','steps':['BUSINESS','PAGE_ACCESS','AD_ACCOUNT'],
                    'parameters':{'AD_ACCOUNT':{'use_common_page':True}}})
        self.assertEqual(observed,['RK','PAGE'])

    async def test_existing_personal_rk_rejected_before_inventory_or_sharing(self):
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock()) as verify:
            with self.assertRaises(ProvisioningError):
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':BM})),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='personal',scope_key='personal')
        verify.assert_not_awaited()

    async def test_target_bm_access_does_not_require_owner_bm_or_heavy_ads_editor(self):
        await AdvertisingPageStore(self.state,'8','61594882851656').patch(page_id=PAGE,name='PrgssTeam',owner_profile_id='9',
            owner_business_id='999999999',ownership_phase='PAGE_ADD_CLICK_INTENT')
        await self.state.complete('one','8','one',ProvisioningStep.AD_ACCOUNT,{'business_id':BM,'ad_account_id':'act_'+RK})
        await self.state.set_running('one','8','one',ProvisioningStep.PAGE_ACCESS)
        browser=SimpleNamespace(add_existing_page=AsyncMock())
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False
        session=SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'}))
        with patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()), \
             patch('app.provisioning.page_access_handler.FacebookBusinessBrowser',return_value=Lease()) as factory, \
             patch('app.provisioning.page_access_handler._request_target_page_access',new=AsyncMock(return_value=True)) as share, \
             patch('app.provisioning.page_access_handler._assign_operator',new=AsyncMock()) as assign, \
             patch('app.provisioning.page_access_handler.inspect_browser_pages',new=AsyncMock()) as inspect:
            result=await page_access_handler(session,{}, {'business_id':BM,'ad_account_id':'act_'+RK},
                provisioning_state=self.state,profile_id='8',item_id='one',scope_key='one')
        self.assertTrue(result['page_shared_to_business'])
        self.assertTrue(result['operator_ads_access_assigned'])
        self.assertFalse(result['ad_account_page_access_verified'])
        self.assertEqual(result['ad_account_id'],RK)
        self.assertEqual(result['identity_verification'],'not_requested')
        inspect.assert_not_awaited()
        self.assertEqual(share.call_args.args[2],BM)
        self.assertEqual(assign.call_args.args[2],BM)
        self.assertEqual(factory.call_args.kwargs['v8_old_space_mb'],256)
        browser.add_existing_page.assert_not_awaited()
        self.assertEqual((await AdvertisingPageStore(self.state,'8','61594882851656').get())['ownership_phase'],'PAGE_ADD_CLICK_INTENT')

    async def test_crash_replaces_old_owner_claim_diagnostic_with_current_target_stage(self):
        await AdvertisingPageStore(self.state).patch(page_id=PAGE,name='PrgssTeam',owner_profile_id='9')
        await self.state.complete('one','9','one',ProvisioningStep.AD_ACCOUNT,{'business_id':BM,'ad_account_id':RK})
        await self.state.set_running('one','9','one',ProvisioningStep.PAGE_ACCESS)
        await self.state.checkpoint('one','9','one',ProvisioningStep.PAGE_ACCESS,
            {'diagnostic':{'stage':'owner_page_claim_reconciliation','surface':'obsolete'}})
        browser=SimpleNamespace(page=SimpleNamespace(url='https://business.facebook.com/latest/settings/pages/?business_id='+BM),
            _diagnostic=AsyncMock(side_effect=RuntimeError('Page crashed')))
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False
        with patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()), \
             patch('app.provisioning.page_access_handler.FacebookBusinessBrowser',return_value=Lease()), \
             patch('app.provisioning.page_access_handler._request_target_page_access',new=AsyncMock(side_effect=RuntimeError('Page crashed'))):
            with self.assertRaisesRegex(RuntimeError,'Page crashed'):
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'})),{},
                    {'business_id':BM,'ad_account_id':RK},provisioning_state=self.state,profile_id='9',item_id='one',scope_key='one')
        diagnostic=(await self.state.step('one',ProvisioningStep.PAGE_ACCESS))['result']['diagnostic']
        self.assertEqual(diagnostic['stage'],'target_page_access')
        self.assertNotIn('obsolete',str(diagnostic))
        self.assertEqual(diagnostic['v8_old_space_mb'],256)
    async def test_pending_target_request_is_not_resent_and_never_uses_owner_claim(self):
        browser=SimpleNamespace(verify_page_attached=AsyncMock(return_value=False),_open_pages_add_action=AsyncMock())
        checkpoint=AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await _request_target_page_access(browser,{'page_id':PAGE},BM,checkpoint,
                {'phase':'TARGET_PAGE_ACCESS_SUBMITTED'})
        self.assertEqual(caught.exception.code,'PAGE_SHARE_RESULT_UNKNOWN')
        browser._open_pages_add_action.assert_not_awaited()
        checkpoint.assert_not_awaited()
        browser.verify_page_attached.assert_awaited_once_with(business_id=BM,page_id=PAGE)

    async def test_missing_share_option_never_falls_back_to_add_existing_page(self):
        browser=SimpleNamespace(verify_page_attached=AsyncMock(return_value=False),
            _open_pages_add_action=AsyncMock(return_value=True),_click_named=AsyncMock(return_value=False),
            add_existing_page=AsyncMock())
        with self.assertRaises(BrowserBusinessError) as caught:
            await _request_target_page_access(browser,{'page_id':PAGE},BM,AsyncMock(),{})
        self.assertEqual(caught.exception.code,'PAGE_SHARE_UI_UNAVAILABLE')
        browser._open_pages_add_action.assert_awaited_once_with(BM)
        self.assertEqual(browser._click_named.call_args.args[0],
            ('Request shared access to a Facebook Page','Request access to a Page'))
        browser.add_existing_page.assert_not_awaited()

    async def test_native_confirm_access_action_preserves_submit_checkpoint(self):
        events=[]
        async def checkpoint(patch): events.append(patch['phase'])
        async def click(**kwargs): events.append('CLICK_CONFIRM')
        submit=SimpleNamespace(count=AsyncMock(return_value=1),is_visible=AsyncMock(return_value=True),
            is_enabled=AsyncMock(return_value=True),click=AsyncMock(side_effect=click))
        def dialog_role(role,**kwargs):
            self.assertEqual(role,'button'); self.assertIsNotNone(kwargs['name'].fullmatch('Confirm'))
            self.assertIsNone(kwargs['name'].fullmatch('Add Page'))
            return submit
        dialog=SimpleNamespace(count=AsyncMock(return_value=1),get_by_role=dialog_role)
        dialog.filter=lambda **kwargs:dialog
        next_button=SimpleNamespace(count=AsyncMock(return_value=0)); next_button.filter=lambda **kwargs:next_button
        page=SimpleNamespace(wait_for_timeout=AsyncMock(),
            get_by_role=lambda role,**kwargs:dialog if role=='dialog' else next_button)
        browser=SimpleNamespace(page=page,verify_page_attached=AsyncMock(side_effect=[False,True]),
            _open_pages_add_action=AsyncMock(return_value=True),_click_named=AsyncMock(return_value=True),
            _fill_page_add_identifier=AsyncMock(return_value=True),_click_exact_page_search_name=AsyncMock(return_value=True))
        with patch('app.provisioning.page_access_handler._ads_only',new=AsyncMock()) as ads:
            self.assertTrue(await _request_target_page_access(browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,checkpoint,{}))
        self.assertEqual(events,['TARGET_PAGE_ACCESS_CLICK_INTENT','CLICK_CONFIRM','TARGET_PAGE_ACCESS_SUBMITTED'])
        ads.assert_awaited_once_with(dialog)


class AdsPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ads_sharing_never_silently_accepts_full_control(self):
        full=SimpleNamespace(is_checked=AsyncMock(return_value=True))
        locator=SimpleNamespace(all=AsyncMock(return_value=[full]))
        dialog=SimpleNamespace(get_by_role=lambda *args,**kwargs:locator)
        with self.assertRaises(BrowserBusinessError) as exc: await _ads_only(dialog)
        self.assertEqual(exc.exception.code,'PAGE_SHARE_PERMISSION_REVIEW_REQUIRED')
