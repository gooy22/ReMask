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
from app.provisioning.page_access_handler import page_access_handler,_ads_only,_request_target_page_access,_approve_owner_page_access,_resolve_owner_page_actor,_resolve_target_business_name,_owner_active_partner_ads_access,_pick_owner_review_request,_select_page,_assign_operator
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

    async def test_alias_retry_sees_original_page_create_intent_after_failure(self):
        a=SimpleNamespace(profile_id='2',cookies={'c_user':'61594285240608'},pages=[])
        b=SimpleNamespace(profile_id='5',cookies={'c_user':'61594285240608'},pages=[])
        calls=[]
        async def create(session,params,snapshot,**kwargs):
            calls.append((kwargs['item_id'],kwargs['profile_id']))
            if len(calls)==1:
                await self.state.checkpoint(kwargs['item_id'],kwargs['profile_id'],kwargs['scope_key'],
                    ProvisioningStep.FAN_PAGES,{'phase':'PAGE_CREATE_CLICK_INTENT',
                        'active_page_name':'PrgssTeam','active_before_ids':[]})
                raise ProvisioningError('PAGE_CREATE_RESULT_UNKNOWN','submitted',retryable=True)
            prior=await self.state.step(kwargs['item_id'],ProvisioningStep.FAN_PAGES)
            self.assertEqual(prior['result']['phase'],'PAGE_CREATE_CLICK_INTENT')
            self.assertEqual(prior['profile_id'],'2')
            return {'pages':[{'id':PAGE,'name':'PrgssTeam','reused':True}]}
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',side_effect=create):
            with self.assertRaises(ProvisioningError):
                await ensure_common_page(SimpleNamespace(context=a),{},self.state,None)
            result=await ensure_common_page(SimpleNamespace(context=b),{},self.state,None)
        self.assertEqual(calls[0],calls[1])
        self.assertEqual(result['page_ids'],[PAGE])
        self.assertEqual((await AdvertisingPageStore.for_context(self.state,b).get())['creation_profile_id'],'2')

    async def test_common_page_creation_continues_legacy_local_checkpoint(self):
        context=SimpleNamespace(profile_id='8',cookies={'c_user':'61594897075733'},pages=[])
        item='workspace-common-page-8'
        await self.state.set_running(item,'8','workspace-common-page',ProvisioningStep.FAN_PAGES)
        await self.state.checkpoint(item,'8','workspace-common-page',ProvisioningStep.FAN_PAGES,
            {'phase':'PAGE_CREATE_CLICK_INTENT','active_page_name':'PrgssTeam'})
        create=AsyncMock(return_value={'pages':[{'id':PAGE,'name':'PrgssTeam'}]})
        with patch('app.provisioning.fan_pages_handler.fan_pages_handler',create):
            await ensure_common_page(SimpleNamespace(context=context),{},self.state,None)
        self.assertEqual(create.call_args.kwargs['item_id'],item)

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

    async def test_existing_rk_inconclusive_live_probe_is_not_false_negative(self):
        await self.state.set_running("existing","9","existing",ProvisioningStep.PAGE_ACCESS)
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock(return_value=(False,[{
                'source':'business_settings_inventory',
                'confirmed':False,
                'confirmed_empty':False,
                'error':'BUSINESS_LOGIN_GATE',
            }]))) as verify, \
             patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as exc:
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'})),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='existing',scope_key='existing')
        self.assertEqual(exc.exception.code,'BUSINESS_RK_RELATION_INCONCLUSIVE')
        self.assertEqual(verify.call_args.kwargs['business_id'],BM)
        self.assertEqual(verify.call_args.kwargs['expected_ad_account_id'],RK)
        create.assert_not_awaited()

    async def test_existing_rk_exact_negative_inventory_remains_unverified(self):
        await self.state.set_running("negative","9","negative",ProvisioningStep.PAGE_ACCESS)
        evidence=[{
            'source':'business_settings_inventory',
            'confirmed':False,
            'confirmed_empty':False,
            'evidence':{
                'exact_business_context':True,
                'inventory_observed':True,
                'inventory_ids':['999999999999999'],
            },
        }]
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock(return_value=(False,evidence))), \
             patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()) as create:
            with self.assertRaises(ProvisioningError) as exc:
                await page_access_handler(SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'})),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='negative',scope_key='negative')
        self.assertEqual(exc.exception.code,'BUSINESS_RK_RELATION_UNVERIFIED')
        create.assert_not_awaited()

    async def test_existing_rk_uses_resolved_last_confirmed_context_binding(self):
        await self.state.set_running("context-binding","9","context-binding",ProvisioningStep.PAGE_ACCESS)
        context=SimpleNamespace(
            cookies={'c_user':'61594882851656'},
            inventory_updated_at=1791370000,
            ad_accounts=[{
                'profile_id':'9',
                'business_id':BM,
                'ad_account_id':RK,
                'source':'workspace_last_confirmed_live',
            }],
        )
        with patch('app.provisioning.ad_account_handler._verify_expected_ad_account_in_business',new=AsyncMock()) as verify, \
             patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock(side_effect=RuntimeError('stop before Page mutation'))):
            with self.assertRaisesRegex(RuntimeError,'stop before Page mutation'):
                await page_access_handler(SimpleNamespace(context=context),
                    {'existing_target':True,'business_id':BM,'ad_account_id':RK}, {},
                    provisioning_state=self.state,profile_id='9',item_id='context-binding',scope_key='context-binding')
        verify.assert_not_awaited()
        result=(await self.state.step('context-binding',ProvisioningStep.PAGE_ACCESS))['result']
        self.assertTrue(result['context_binding_confirmed'])
        self.assertFalse(result['inventory_binding_verified'])
        self.assertEqual(result['binding_evidence'][0]['source'],'workspace_last_confirmed_live')

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
            return {
                'business_id':BM,
                'ad_account_id':RK,
                'page_id':PAGE,
                'page_shared_to_business':True,
                'operator_ads_access_assigned':True,
            }
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

    async def _shared_access_handler_fixture(
        self,
        *,
        request_side_effect=True,
        approve_side_effect=True,
        refresh_side_effect=None,
    ):
        item='shared-'+str(getattr(self,'shared_fixture_count',0))
        self.shared_fixture_count=getattr(self,'shared_fixture_count',0)+1
        await AdvertisingPageStore(self.state,'8','61594882851656').patch(
            page_id=PAGE,name='PrgssTeam',owner_profile_id='8')
        await self.state.complete(
            item,'8',item,ProvisioningStep.AD_ACCOUNT,
            {'business_id':BM,'ad_account_id':'act_'+RK,'account_name':'Fixture Ads'}
        )
        await self.state.set_running(
            item,'8',item,ProvisioningStep.PAGE_ACCESS
        )

        browser=SimpleNamespace(
            page=SimpleNamespace(url='https://www.facebook.com/'),
            context=SimpleNamespace(cookies={'c_user':'61594882851656'}),
            _goto=AsyncMock(return_value='https://www.facebook.com/'),
            _diagnostic=AsyncMock(return_value={}),
        )
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False

        session=SimpleNamespace(
            context=SimpleNamespace(
                profile_id='8',
                cookies={'c_user':'61594882851656'},
                pages=[],
                ad_accounts=[],
                inventory_updated_at=0,
            ),
            browser_lease=lambda **kwargs: Lease(),
        )
        resolver=SimpleNamespace(resolve=AsyncMock())

        request=AsyncMock()
        if isinstance(request_side_effect,(list,tuple,BaseException)):
            request.side_effect=request_side_effect
        else:
            request.return_value=request_side_effect
        approve=AsyncMock()
        if isinstance(approve_side_effect,(list,tuple,BaseException)):
            approve.side_effect=approve_side_effect
        else:
            approve.return_value=approve_side_effect
        assign=AsyncMock(return_value={
            'source':'exact_page_people_ads_access',
            'operator_uid':'61594882851656',
            'row':'You Ads',
        })
        refresh=AsyncMock(
            return_value=bool(refresh_side_effect)
            if refresh_side_effect is not None else False
        )

        with patch(
            'app.provisioning.page_access_handler.ensure_common_page',
            new=AsyncMock(),
        ), patch(
            'app.provisioning.page_access_handler._request_target_page_access',
            request,
        ), patch(
            'app.provisioning.page_access_handler._approve_owner_page_access',
            approve,
        ), patch(
            'app.provisioning.page_access_handler._assign_operator',
            assign,
        ), patch(
            'app.provisioning.page_access_handler.ensure_existing_page_full_control',
            new=AsyncMock(),
        ) as ownership, patch(
            'app.provisioning.page_access_handler.ensure_ad_account_full_control',
            new=AsyncMock(),
        ) as rk_full, patch(
            'app.provisioning.page_access_handler.refresh_saved_auth_context',
            refresh,
        ):
            result=await page_access_handler(
                session,{},
                {'business_id':BM,'ad_account_id':'act_'+RK},
                provisioning_state=self.state,profile_id='8',
                item_id=item,scope_key=item,profile_resolver=resolver,
            )
        return result,request,approve,assign,ownership,rk_full,refresh,browser

    async def test_default_page_access_uses_shared_ads_not_ownership(self):
        result,request,approve,assign,ownership,rk_full,refresh,browser=(
            await self._shared_access_handler_fixture(
                request_side_effect=True,
            )
        )
        request.assert_awaited_once()
        approve.assert_not_awaited()
        assign.assert_awaited_once()
        ownership.assert_not_awaited()
        rk_full.assert_not_awaited()
        refresh.assert_not_awaited()
        browser._goto.assert_awaited_once()
        self.assertEqual(result['access_mode'],'shared_ads_access')
        self.assertEqual(
            result['transport'],
            'target_business_page_advertising_access',
        )
        self.assertTrue(result['page_shared_to_business'])
        self.assertTrue(result['operator_ads_access_assigned'])
        config=await AdvertisingPageStore(
            self.state,'8','61594882851656'
        ).get()
        self.assertFalse(bool(config.get('owner_business_confirmed')))
        self.assertNotEqual(str(config.get('owner_business_id') or ''),BM)

    async def test_business_login_gate_refreshes_same_profile_once_then_retries(self):
        gate=BrowserBusinessError(
            'BUSINESS_LOGIN_GATE',
            'Meta Business login gate',
            retryable=True,
            diagnostic={'login_path':'/business/loginpage/'},
        )
        result,request,approve,assign,ownership,rk_full,refresh,browser=(
            await self._shared_access_handler_fixture(
                request_side_effect=[gate,True],
                refresh_side_effect=True,
            )
        )
        self.assertEqual(request.await_count,2)
        refresh.assert_awaited_once()
        approve.assert_not_awaited()
        assign.assert_awaited_once()
        self.assertTrue(result['page_shared_to_business'])

    async def test_business_login_gate_without_fresh_session_does_not_loop(self):
        gate=BrowserBusinessError(
            'BUSINESS_LOGIN_GATE',
            'Meta Business login gate',
            retryable=True,
            diagnostic={'login_path':'/business/loginpage/'},
        )
        item='shared-no-refresh'
        await AdvertisingPageStore(self.state,'8','61594882851656').patch(
            page_id=PAGE,name='PrgssTeam',owner_profile_id='8')
        await self.state.complete(
            item,'8',item,ProvisioningStep.AD_ACCOUNT,
            {'business_id':BM,'ad_account_id':'act_'+RK,'account_name':'Fixture Ads'}
        )
        await self.state.set_running(item,'8',item,ProvisioningStep.PAGE_ACCESS)
        browser=SimpleNamespace(
            page=SimpleNamespace(url='https://www.facebook.com/'),
            context=SimpleNamespace(cookies={'c_user':'61594882851656'}),
            _goto=AsyncMock(return_value='https://www.facebook.com/'),
            _diagnostic=AsyncMock(return_value={}),
        )
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False
        session=SimpleNamespace(
            context=SimpleNamespace(
                profile_id='8',cookies={'c_user':'61594882851656'},
                pages=[],ad_accounts=[],inventory_updated_at=0,
            ),
            browser_lease=lambda **kwargs: Lease(),
        )
        resolver=SimpleNamespace(resolve=AsyncMock())
        request=AsyncMock(side_effect=gate)
        refresh=AsyncMock(return_value=False)
        with patch(
            'app.provisioning.page_access_handler.ensure_common_page',
            new=AsyncMock(),
        ), patch(
            'app.provisioning.page_access_handler._request_target_page_access',
            request,
        ), patch(
            'app.provisioning.page_access_handler.refresh_saved_auth_context',
            refresh,
        ):
            with self.assertRaises(ProvisioningError) as caught:
                await page_access_handler(
                    session,{},
                    {'business_id':BM,'ad_account_id':'act_'+RK},
                    provisioning_state=self.state,profile_id='8',
                    item_id=item,scope_key=item,profile_resolver=resolver,
                )
        self.assertEqual(caught.exception.code,'BUSINESS_LOGIN_GATE')
        self.assertEqual(request.await_count,1)
        refresh.assert_awaited_once()

    async def _full_access_handler_fixture(self, prior=None, failure=None):
        item='full-'+str(getattr(self,'full_fixture_count',0))
        self.full_fixture_count=getattr(self,'full_fixture_count',0)+1
        self.last_full_item=item
        await AdvertisingPageStore(self.state,'8','61594882851656').patch(
            page_id=PAGE,name='PrgssTeam',owner_profile_id='8',
            grants={BM:prior or {}})
        await self.state.complete(item,'8',item,ProvisioningStep.AD_ACCOUNT,
            {'business_id':BM,'ad_account_id':'act_'+RK,'account_name':'Fixture Ads'})
        await self.state.set_running(item,'8',item,ProvisioningStep.PAGE_ACCESS)
        browser=SimpleNamespace(page=SimpleNamespace(url='about:blank'),
            _diagnostic=AsyncMock(return_value={}))
        class Lease:
            async def __aenter__(self): return browser
            async def __aexit__(self,*args): return False
        session=SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'61594882851656'}))
        async def full(browser_arg, config, business, checkpoint, resume):
            self.assertIs(browser_arg,browser)
            self.assertEqual(business,BM)
            self.assertEqual(resume,prior or {})
            if failure:
                raise failure
            await checkpoint({'phase':'TARGET_PAGE_OPERATOR_FULL_ASSIGN_CONFIRMED',
                'access_mode':'existing_page_full_control','page_owned_by_business':True,
                'operator_full_control_verified':True})
            return {'page_owned_by_business':True,'operator_full_control_verified':True,
                'transport':'existing_page_full_control'}
        with patch('app.provisioning.page_access_handler.ensure_common_page',new=AsyncMock()), \
             patch('app.provisioning.page_access_handler.FacebookBusinessBrowser',return_value=Lease()) as factory, \
             patch('app.provisioning.page_access_handler.ensure_existing_page_full_control',new=AsyncMock(side_effect=full)) as access, \
             patch('app.provisioning.page_access_handler.ensure_ad_account_full_control',new=AsyncMock(return_value={'rk_operator_full_control_verified':True})) as rk_access, \
             patch('app.provisioning.page_access_handler._request_target_page_access',new=AsyncMock()) as partial, \
             patch('app.provisioning.page_access_handler.inspect_browser_pages',new=AsyncMock()) as inspect:
            result=await page_access_handler(
                session,
                {'access_mode':'existing_page_full_control'},
                {'business_id':BM,'ad_account_id':'act_'+RK},
                provisioning_state=self.state,profile_id='8',item_id=item,scope_key=item
            )
        self.assertEqual(factory.call_count,1)
        partial.assert_not_awaited()
        inspect.assert_not_awaited()
        access.assert_awaited_once()
        rk_access.assert_awaited_once()
        self.assertEqual(rk_access.await_args.args[1:4],(BM,RK,'Fixture Ads'))
        return result

    async def test_target_bm_uses_existing_page_and_proves_full_operator_control(self):
        result=await self._full_access_handler_fixture()
        self.assertTrue(result['page_owned_by_business'])
        self.assertTrue(result['operator_full_control_verified'])
        self.assertTrue(result['rk_operator_full_control_verified'])
        self.assertEqual(result['transport'],'existing_page_full_control')
        self.assertFalse(result['ad_account_page_access_verified'])
        self.assertEqual(result['ad_account_id'],RK)
        config=await AdvertisingPageStore(self.state,'8','61594882851656').get()
        self.assertEqual(config['owner_business_id'],BM)
        self.assertTrue(config['owner_business_confirmed'])

    async def test_legacy_ads_and_rk_checkpoints_do_not_skip_full_control_upgrade(self):
        for phase in ('TARGET_PAGE_ACCESS_OWNER_CONFIRMED','TARGET_PAGE_ACCESS_RK_CONFIRMED',
                      'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED','TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
                      'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT'):
            with self.subTest(phase=phase):
                result=await self._full_access_handler_fixture({'phase':phase})
                self.assertTrue(result['operator_full_control_verified'])
                self.assertFalse(result['ad_account_page_access_verified'])

    async def test_full_control_pending_assignment_reaches_verification_only_reconciliation(self):
        from app.facebook_business_browser import BrowserBusinessError
        prior={'phase':'TARGET_PAGE_OPERATOR_FULL_ASSIGN_SUBMITTED',
            'access_mode':'existing_page_full_control'}
        with self.assertRaises(ProvisioningError) as caught:
            await self._full_access_handler_fixture(prior,BrowserBusinessError(
                'PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN','pending',retryable=True))
        self.assertEqual(caught.exception.code,'PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN')

    async def test_full_control_crash_has_current_diagnostic_without_claiming_success(self):
        with self.assertRaisesRegex(RuntimeError,'Page crashed'):
            await self._full_access_handler_fixture(failure=RuntimeError('Page crashed'))
        state=(await self.state.step(self.last_full_item,ProvisioningStep.PAGE_ACCESS))['result']
        self.assertEqual(state['diagnostic']['stage'],'existing_page_full_control')
        self.assertEqual(state['diagnostic']['v8_old_space_mb'],256)
        self.assertNotIn('operator_full_control_verified',state)

    async def test_pending_target_request_is_not_resent_and_never_uses_owner_claim(self):
        for phase in (
            'TARGET_PAGE_ACCESS_SUBMITTED',
            'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
            'TARGET_PAGE_ACCESS_OWNER_APPROVED',
        ):
            browser=SimpleNamespace(verify_page_attached=AsyncMock(return_value=False),_open_pages_add_action=AsyncMock())
            checkpoint=AsyncMock()
            self.assertFalse(await _request_target_page_access(browser,{'page_id':PAGE},BM,checkpoint,
                {'phase':phase}))
            browser._open_pages_add_action.assert_not_awaited()
            checkpoint.assert_not_awaited()
            browser.verify_page_attached.assert_not_awaited()

        # Every post-owner durable phase must bypass relation navigation.
        for phase in (
            'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
            'TARGET_PAGE_ACCESS_RK_CONFIRMED',
            'TARGET_PAGE_ACCESS_RK_PROBE_BLOCKED',
            'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
            'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
            'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
        ):
            browser=SimpleNamespace(
                verify_page_attached=AsyncMock(side_effect=AssertionError('must not navigate')),
                _open_pages_add_action=AsyncMock(),
            )
            checkpoint=AsyncMock()
            self.assertTrue(await _request_target_page_access(
                browser,{'page_id':PAGE},BM,checkpoint,{'phase':phase}))
            browser.verify_page_attached.assert_not_awaited()
            browser._open_pages_add_action.assert_not_awaited()
            checkpoint.assert_not_awaited()

    async def test_operator_page_selection_never_navigates_selected_asset_route(self):
        class Locator:
            def __init__(self, rows=None, visible=False):
                self.rows=rows
                self.visible=visible
                self.first=self
            async def count(self):
                return len(self.rows) if self.rows is not None else (1 if self.visible else 0)
            def nth(self,index): return self.rows[index]
            async def is_visible(self): return self.visible
            def filter(self,**kwargs): return self
            def get_by_role(self,*args,**kwargs): return Locator()
            def get_by_text(self,*args,**kwargs): return Locator()

        rows=Locator(rows=[])
        page=SimpleNamespace(
            get_by_role=lambda role,**kwargs: rows if role=='row' else Locator(),
            get_by_text=lambda *args,**kwargs: Locator(),
            locator=lambda *args,**kwargs: SimpleNamespace(
                inner_text=AsyncMock(return_value='Business assets Pages')),
            url='https://business.facebook.com/latest/settings/pages/?business_id='+BM,
        )
        browser=SimpleNamespace(
            page=page,
            _goto=AsyncMock(side_effect=AssertionError('selected_asset_id navigation forbidden')),
            _diagnostic=AsyncMock(return_value={'stage':'operator'}),
        )
        with self.assertRaises(BrowserBusinessError) as caught:
            await _select_page(browser,'PrgssTeam',PAGE,BM)
        self.assertEqual(caught.exception.code,'PAGE_OPERATOR_PAGE_SELECTION_UNAVAILABLE')
        browser._goto.assert_not_awaited()

    async def test_operator_page_selection_prefers_exact_page_id_row_over_duplicate_name_text(self):
        clicks=[]
        class Locator:
            def __init__(self, rows=None, visible=True, evidence=None, label=''):
                self.rows=rows; self.visible=visible; self.evidence=evidence or {}
                self.label=label; self.first=self
            async def count(self): return len(self.rows) if self.rows is not None else (1 if self.visible else 0)
            def nth(self,index): return self.rows[index] if self.rows is not None else self
            async def is_visible(self): return self.visible
            async def click(self,**kwargs): clicks.append(self.label)
            async def evaluate(self,*args,**kwargs): return self.evidence
            def filter(self,**kwargs): return self
            def get_by_role(self,role,**kwargs):
                if self.label=='target-row' and role=='button':
                    return Locator(label='target-button')
                return Locator(visible=False)
            def get_by_text(self,*args,**kwargs): return Locator(visible=False)

        wrong=Locator(evidence={'text':'PrgssTeam','links':['?selected_asset_id=999999999'],'id_match':False},label='wrong-row')
        target=Locator(evidence={'text':'PrgssTeam','links':['?selected_asset_id='+PAGE],'id_match':True},label='target-row')
        rows=Locator(rows=[wrong,target])
        page=SimpleNamespace(
            get_by_role=lambda role,**kwargs: rows if role=='row' else Locator(visible=False),
            _unused=True,
        )
        browser=SimpleNamespace(page=page,_diagnostic=AsyncMock(return_value={'stage':'operator'}))
        proof=await _select_page(browser,'PrgssTeam',PAGE,BM)
        self.assertEqual(proof['source'],'row_exact_page_id')
        self.assertEqual(clicks,['target-button'])

    async def test_preconfirmed_operator_skips_relation_navigation_and_checkpoints_submit(self):
        events=[]
        class Locator:
            def __init__(self,count=0,label=''):
                self._count=count; self.label=label; self.first=self
            async def count(self): return self._count
            async def is_visible(self): return self._count>0
            async def is_enabled(self): return self._count>0
            async def click(self,**kwargs): events.append('CLICK_'+self.label)
            async def check(self,**kwargs): events.append('CHECK_'+self.label)
            def get_by_role(self,role,**kwargs):
                pattern=getattr(kwargs.get('name'),'pattern',str(kwargs.get('name') or ''))
                if role=='checkbox' and 'You' in pattern: return Locator(1,'YOU')
                if role=='button' and ('Assign' in pattern or 'Save' in pattern): return Locator(1,'SAVE')
                return Locator()
        assign=Locator(1,'ASSIGN')
        dialog=Locator(1,'DIALOG')
        def page_role(role,**kwargs):
            pattern=getattr(kwargs.get('name'),'pattern',str(kwargs.get('name') or ''))
            if role=='dialog': return dialog
            if role=='button' and ('Assign people' in pattern or 'Add people' in pattern):
                return assign
            return Locator()
        page=SimpleNamespace(get_by_role=page_role,wait_for_timeout=AsyncMock())
        browser=SimpleNamespace(
            page=page,
            context=SimpleNamespace(cookies={'c_user':'61594882851656'}),
            verify_page_attached=AsyncMock(side_effect=AssertionError('redundant relation navigation')),
            _diagnostic=AsyncMock(return_value={'stage':'operator_submitted'}),
        )
        patches=[]
        async def checkpoint(patch): patches.append(patch)
        proof={'source':'exact_page_people_ads_access',
            'operator_uid':'61594882851656','row':'You Ads'}
        with patch('app.provisioning.page_access_handler._select_page',
                   new=AsyncMock(return_value={'source':'row_exact_page_id'})), \
             patch('app.provisioning.page_access_handler._ads_only',new=AsyncMock()), \
             patch('app.provisioning.page_access_handler._operator_ads_proof',
                   new=AsyncMock(side_effect=[None,proof])):
            result=await _assign_operator(
                browser,{'name':'PrgssTeam','page_id':PAGE},BM,checkpoint,
                relation_preconfirmed=True)
        browser.verify_page_attached.assert_not_awaited()
        self.assertEqual(result,proof)
        self.assertEqual(events,['CLICK_ASSIGN','CHECK_YOU','CLICK_SAVE'])
        self.assertEqual(
            [row.get('phase') for row in patches],
            ['TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
             'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
             'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED'])
        self.assertEqual(patches[0]['business_id'],BM)
        self.assertEqual(patches[0]['page_id'],PAGE)

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
            _diagnostic=AsyncMock(return_value={'stage':'target_page_access_submit_response'}),
            _open_pages_add_action=AsyncMock(return_value=True),_click_named=AsyncMock(return_value=True),
            _fill_page_add_identifier=AsyncMock(return_value=True),_click_exact_page_search_name=AsyncMock(return_value=True))
        with patch('app.provisioning.page_access_handler._ads_only',new=AsyncMock()) as ads:
            self.assertTrue(await _request_target_page_access(browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,checkpoint,{}))
        self.assertEqual(events,['TARGET_PAGE_ACCESS_CLICK_INTENT','CLICK_CONFIRM','TARGET_PAGE_ACCESS_SUBMITTED'])
        ads.assert_awaited_once_with(dialog)


    async def test_owner_actor_is_discovered_live_when_session_context_has_no_page(self):
        live={'id':PAGE,'profile_id':'777777777','name':'PrgssTeam'}
        browser=SimpleNamespace(
            context=SimpleNamespace(pages=[]),
            discover_managed_pages=AsyncMock(return_value=[live]),
            _last_page_inventory_diagnostic={'stage':'done'},
        )
        actor,evidence=await _resolve_owner_page_actor(
            browser,{'page_id':PAGE,'name':'PrgssTeam'})
        self.assertEqual(actor,'777777777')
        self.assertEqual(evidence['source'],'live_managed_pages_profile_id')
        browser.discover_managed_pages.assert_awaited_once_with(
            fast=True,navigation_timeout_ms=9000)

    async def test_exact_business_id_maps_to_live_name_before_owner_reconciliation(self):
        browser=SimpleNamespace(
            snapshot_businesses=AsyncMock(return_value={
                BM:'Orchid Studio a5b1ce0a76',
                '999999999':'Other Business',
            }))
        name,evidence=await _resolve_target_business_name(browser,BM)
        self.assertEqual(name,'Orchid Studio a5b1ce0a76')
        self.assertEqual(evidence['business_id'],BM)
        self.assertEqual(evidence['source'],'snapshot_businesses')

    async def test_duplicate_business_names_cannot_prove_exact_partner_identity(self):
        browser=SimpleNamespace(snapshot_businesses=AsyncMock(return_value={
            BM:'Prgss Business','999999999':'Prgss  Business'}))
        name,evidence=await _resolve_target_business_name(browser,BM)
        self.assertEqual(name,'')
        self.assertEqual(evidence['source'],'ambiguous_business_name')

    async def test_exact_created_bm_name_survives_personal_only_live_selector(self):
        browser=SimpleNamespace(snapshot_businesses=AsyncMock(return_value={'61594882851656':''}))
        recorded={'business_id':BM,'business_name':'Orchid Studio a5b1ce0a76',
            'source':'python_worker_confirmed_create','create_confirmed':True,'profile_id':'9',
            'known_business_names':{BM:'Orchid Studio a5b1ce0a76'}}
        name,evidence=await _resolve_target_business_name(browser,BM,recorded)
        self.assertEqual(name,'Orchid Studio a5b1ce0a76')
        self.assertEqual(evidence['source'],'recorded_profile_business_create')
        self.assertEqual(evidence['business_id'],BM)
        browser.snapshot_businesses.assert_not_awaited()

    async def test_recorded_name_requires_exact_confirmed_creation_and_unique_identity(self):
        for change in [{'business_id':'999999999'},{'create_confirmed':False},
                {'source':'unverified_inventory'},
                {'known_business_names':{BM:'Orchid','999999999':'Orchid'}}]:
            with self.subTest(change=change):
                browser=SimpleNamespace(snapshot_businesses=AsyncMock(return_value={'61594882851656':''}))
                recorded={'business_id':BM,'business_name':'Orchid',
                    'source':'python_worker_confirmed_create','create_confirmed':True,**change}
                name,evidence=await _resolve_target_business_name(browser,BM,recorded)
                self.assertEqual(name,'')
                browser.snapshot_businesses.assert_awaited_once()

    async def test_owner_request_matches_exact_business_including_single_request(self):
        item=SimpleNamespace(evaluate=AsyncMock())
        for context,expected in [
            ('Review request from business '+BM,True),
            ('Review request from business 9'+BM,False),
            ('Review request from business '+BM+'0',False),
            ('Review request from another Business',False),
        ]:
            with self.subTest(context=context):
                item.evaluate.return_value=context
                with patch('app.provisioning.page_access_handler._visible_owner_review_requests',
                        new=AsyncMock(return_value=[item])):
                    selected,evidence=await _pick_owner_review_request(SimpleNamespace(),BM)
                self.assertEqual(selected is item,expected)
                self.assertEqual(evidence[0]['business_id_match'],expected)

    async def test_owner_request_can_use_uniquely_resolved_business_name(self):
        wrong=SimpleNamespace(evaluate=AsyncMock(return_value='Other Business Review request'))
        target=SimpleNamespace(evaluate=AsyncMock(return_value='Orchid Studio a5b1ce0a76 Review request'))
        with patch('app.provisioning.page_access_handler._visible_owner_review_requests',
                new=AsyncMock(return_value=[wrong,target])):
            selected,evidence=await _pick_owner_review_request(
                SimpleNamespace(),BM,'Orchid Studio a5b1ce0a76')
        self.assertIs(selected,target)
        self.assertTrue(evidence[1]['business_name_match'])

    async def test_owner_request_with_repeated_target_identity_is_not_selected(self):
        items=[SimpleNamespace(evaluate=AsyncMock(return_value='Review request '+BM)) for _ in range(2)]
        with patch('app.provisioning.page_access_handler._visible_owner_review_requests',
                new=AsyncMock(return_value=items)):
            selected,evidence=await _pick_owner_review_request(SimpleNamespace(),BM)
        self.assertIsNone(selected)
        self.assertEqual(len(evidence),2)

    async def test_owner_page_access_proves_existing_partner_ads_row(self):
        class Item:
            async def is_visible(self): return True
            async def evaluate(self,*args):
                return {
                    'row':'Orchid Studio a5b1ce0a76 Insights, Ads',
                    'section':'Partners with access Orchid Studio a5b1ce0a76 Insights, Ads',
                }
        class Locator:
            async def count(self): return 1
            def nth(self,index): return Item()
        page=SimpleNamespace(
            get_by_text=lambda text,exact=False:Locator())
        proof=await _owner_active_partner_ads_access(
            page,'Orchid Studio a5b1ce0a76')
        self.assertIsNotNone(proof)
        self.assertEqual(proof['source'],'page_access_partners_with_ads')
        self.assertIn('Ads',proof['row'])

    async def test_partner_card_uses_exact_native_menu_when_name_is_not_a_separate_element(self):
        item=SimpleNamespace(is_visible=AsyncMock(return_value=True),evaluate=AsyncMock(return_value={
            'row':'Orchid Studio a5b1ce0a76 Insights, Ads',
            'section':'Partners with access Orchid Studio a5b1ce0a76 Insights, Ads'}))
        empty=SimpleNamespace(count=AsyncMock(return_value=0))
        menu=SimpleNamespace(count=AsyncMock(return_value=1),nth=lambda index:item)
        page=SimpleNamespace(get_by_text=lambda *args,**kwargs:empty,
            get_by_role=lambda role,**kwargs:menu)
        trace={}
        proof=await _owner_active_partner_ads_access(page,'Orchid Studio a5b1ce0a76',diagnostic=trace)
        self.assertEqual(proof['anchor'],'partner_menu')
        self.assertEqual(trace['exact_name_count'],0)
        self.assertEqual(trace['partner_menu_count'],1)

    async def test_duplicate_partner_cards_cannot_prove_a_unique_business(self):
        duplicate=SimpleNamespace(count=AsyncMock(return_value=2))
        page=SimpleNamespace(get_by_text=lambda *args,**kwargs:duplicate,
            get_by_role=lambda *args,**kwargs:duplicate)
        self.assertIsNone(await _owner_active_partner_ads_access(page,'Orchid'))

    async def test_owner_reconciliation_accepts_already_active_exact_partner(self):
        browser_context=SimpleNamespace(
            clear_cookies=AsyncMock(),add_cookies=AsyncMock())
        browser=SimpleNamespace(
            page=SimpleNamespace(),
            _browser_context=browser_context,
            context=SimpleNamespace(
                cookies={'c_user':'100','i_user':'100'},
                pages=[{'id':PAGE,'profile_id':'777777777'}]),
            verify_page_attached=AsyncMock(return_value=False),
            _goto=AsyncMock(),_assert_authenticated=AsyncMock(),
        )
        checkpoints=[]
        async def checkpoint(patch): checkpoints.append(patch)
        with patch(
            'app.provisioning.page_access_handler._resolve_target_business_name',
            new=AsyncMock(return_value=(
                'Orchid Studio a5b1ce0a76',
                {'source':'snapshot_businesses','business_id':BM},
            )),
        ), patch(
            'app.provisioning.page_access_handler._owner_active_partner_ads_access',
            new=AsyncMock(return_value={
                'source':'page_access_partners_with_ads',
                'business_name':'Orchid Studio a5b1ce0a76',
                'row':'Orchid Studio a5b1ce0a76 Insights, Ads',
            }),
        ), patch(
            'app.provisioning.page_access_handler._pick_owner_review_request',
            new=AsyncMock(),
        ) as pending:
            self.assertTrue(await _approve_owner_page_access(
                browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,checkpoint))
        pending.assert_not_awaited()
        self.assertEqual(browser.verify_page_attached.await_count,1)
        confirmed=[
            row for row in checkpoints
            if row.get('phase')=='TARGET_PAGE_ACCESS_OWNER_CONFIRMED'
        ]
        self.assertEqual(len(confirmed),1)
        self.assertEqual(
            confirmed[0]['owner_relation_proof']['source'],
            'page_access_partners_with_ads',
        )

    async def test_owner_approval_switches_to_exact_page_then_restores_user_and_verifies(self):
        events=[]
        class Locator:
            def __init__(self, *, count=0, checked=False, context='', router=None, label=''):
                self._count=count; self._checked=checked; self._context=context
                self._router=router; self.label=label; self.first=self
            async def count(self): return self._count
            def nth(self,index): return self
            async def is_visible(self): return self._count>0
            async def is_enabled(self): return self._count>0
            async def is_checked(self): return self._checked
            async def get_attribute(self,name): return 'true' if name=='aria-checked' and self._checked else None
            async def click(self,**kwargs): events.append('CLICK_'+self.label)
            async def fill(self,*args,**kwargs): events.append('FILL_'+self.label)
            async def evaluate(self,*args,**kwargs): return self._context
            def filter(self,**kwargs): return self
            def get_by_role(self,role,**kwargs):
                return self._router(role,kwargs.get('name')) if self._router else Locator()

        review=Locator(count=1,label='REVIEW',context='Review request from '+BM)
        next_button=Locator(count=1,label='NEXT')
        approve=Locator(count=1,label='APPROVE')
        empty=Locator()
        def dialog_router(role,name):
            text=getattr(name,'pattern',str(name or ''))
            if role=='button' and text=='^Next$': return next_button
            if role=='button' and 'Accept' in text: return approve
            return empty
        dialog=Locator(count=1,router=dialog_router)
        def page_role(role,name=None,**kwargs):
            text=getattr(name,'pattern',str(name or ''))
            if role=='dialog': return dialog
            if role=='button' and 'Review request' in text: return review
            return empty
        page=SimpleNamespace(
            get_by_role=page_role,
            locator=lambda selector:empty,
            wait_for_timeout=AsyncMock())
        browser_context=SimpleNamespace(clear_cookies=AsyncMock(),add_cookies=AsyncMock())
        browser=SimpleNamespace(
            page=page,_browser_context=browser_context,
            context=SimpleNamespace(cookies={'c_user':'100','i_user':'100'},
                pages=[{'id':PAGE,'profile_id':'777777777'}]),
            verify_page_attached=AsyncMock(side_effect=[False,True]),
            _goto=AsyncMock(),_assert_authenticated=AsyncMock(),
            _diagnostic=AsyncMock(return_value={'stage':'owner'}))
        checkpoints=[]
        async def checkpoint(patch): checkpoints.append(patch)
        self.assertTrue(await _approve_owner_page_access(
            browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,checkpoint))
        self.assertEqual(events,['CLICK_REVIEW','CLICK_NEXT','CLICK_APPROVE'])
        browser._goto.assert_awaited_once_with(
            'https://www.facebook.com/settings/?tab=profile_access',
            timeout_ms=12000,wait_until='commit',settle_ms=1100,attempts=1)
        self.assertEqual(browser_context.add_cookies.await_args_list[0].args[0][0]['value'],'777777777')
        self.assertEqual(browser_context.add_cookies.await_args_list[-1].args[0][0]['value'],'100')
        phases=[row.get('phase') for row in checkpoints if row.get('phase')]
        self.assertEqual(phases,[
            'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
            'TARGET_PAGE_ACCESS_OWNER_APPROVED',
            'TARGET_PAGE_ACCESS_OWNER_CONFIRMED'])
        browser.verify_page_attached.assert_awaited_with(business_id=BM,page_id=PAGE)

    async def test_owner_approval_never_guesses_between_multiple_pending_requests(self):
        events=[]
        class Locator:
            def __init__(self, items=None, context=''):
                self.items=items; self.context=context; self.first=self
            async def count(self): return len(self.items) if self.items is not None else 1
            def nth(self,index): return self.items[index] if self.items is not None else self
            async def is_visible(self): return True
            async def is_enabled(self): return True
            async def evaluate(self,*args,**kwargs): return self.context
            async def click(self,**kwargs): events.append('CLICK')
            def filter(self,**kwargs): return self
        a=Locator(context='Request from Other Business A')
        b=Locator(context='Request from Other Business B')
        reviews=Locator(items=[a,b])
        empty=Locator(items=[])
        def page_role(role,name=None,**kwargs):
            text=getattr(name,'pattern',str(name or ''))
            if role=='button' and 'Review request' in text: return reviews
            return empty
        page=SimpleNamespace(get_by_role=page_role,wait_for_timeout=AsyncMock())
        browser_context=SimpleNamespace(clear_cookies=AsyncMock(),add_cookies=AsyncMock())
        browser=SimpleNamespace(
            page=page,_browser_context=browser_context,
            context=SimpleNamespace(cookies={'c_user':'100'},
                pages=[{'id':PAGE,'profile_id':'777777777'}]),
            verify_page_attached=AsyncMock(side_effect=[False,False]),
            _goto=AsyncMock(),_assert_authenticated=AsyncMock(),
            _diagnostic=AsyncMock(return_value={'stage':'owner'}))
        with self.assertRaises(BrowserBusinessError) as caught:
            await _approve_owner_page_access(
                browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,AsyncMock())
        self.assertEqual(caught.exception.code,'PAGE_OWNER_REQUEST_AMBIGUOUS')
        self.assertEqual(events,[])

    async def test_owner_approval_refuses_selected_full_control(self):
        events=[]
        class Locator:
            def __init__(self, *, count=0, checked=False, router=None, label=''):
                self._count=count; self._checked=checked; self._router=router
                self.label=label; self.first=self
            async def count(self): return self._count
            def nth(self,index): return self
            async def is_visible(self): return self._count>0
            async def is_enabled(self): return self._count>0
            async def is_checked(self): return self._checked
            async def get_attribute(self,name): return 'true' if self._checked else None
            async def click(self,**kwargs): events.append('CLICK_'+self.label)
            async def evaluate(self,*args,**kwargs): return 'Review request from '+BM
            def filter(self,**kwargs): return self
            def get_by_role(self,role,**kwargs):
                return self._router(role,kwargs.get('name')) if self._router else Locator()
        review=Locator(count=1,label='REVIEW')
        full=Locator(count=1,checked=True,label='FULL')
        empty=Locator()
        def dialog_router(role,name):
            text=getattr(name,'pattern',str(name or ''))
            if role=='checkbox' and 'full control' in text: return full
            return empty
        dialog=Locator(count=1,router=dialog_router)
        def page_role(role,name=None,**kwargs):
            text=getattr(name,'pattern',str(name or ''))
            if role=='dialog': return dialog
            if role=='button' and 'Review request' in text: return review
            return empty
        page=SimpleNamespace(get_by_role=page_role,wait_for_timeout=AsyncMock())
        browser=SimpleNamespace(
            page=page,
            _browser_context=SimpleNamespace(clear_cookies=AsyncMock(),add_cookies=AsyncMock()),
            context=SimpleNamespace(cookies={'c_user':'100'},
                pages=[{'id':PAGE,'profile_id':'777777777'}]),
            verify_page_attached=AsyncMock(side_effect=[False,False]),
            _goto=AsyncMock(),_assert_authenticated=AsyncMock(),
            _diagnostic=AsyncMock(return_value={'stage':'owner'}))
        with self.assertRaises(BrowserBusinessError) as caught:
            await _approve_owner_page_access(
                browser,{'page_id':PAGE,'name':'PrgssTeam'},BM,AsyncMock())
        self.assertEqual(caught.exception.code,'PAGE_SHARE_PERMISSION_REVIEW_REQUIRED')
        self.assertEqual(events,['CLICK_REVIEW'])


class AdsPermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_ads_sharing_never_silently_accepts_full_control(self):
        full=SimpleNamespace(is_checked=AsyncMock(return_value=True))
        locator=SimpleNamespace(all=AsyncMock(return_value=[full]))
        dialog=SimpleNamespace(get_by_role=lambda *args,**kwargs:locator)
        with self.assertRaises(BrowserBusinessError) as exc: await _ads_only(dialog)
        self.assertEqual(exc.exception.code,'PAGE_SHARE_PERMISSION_REVIEW_REQUIRED')
