import asyncio
import json
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

from app.page_access_inspection import promotable_pages, request_accounts, inspect_browser_pages, allowed_readonly_request,advertiser_phone_status

RK='2279305019588057'
BM='61594882851656'
PAGE='1270757506131209'


class PageAccessEvidenceTests(unittest.TestCase):
    def test_phone_requirement_needs_explicit_meta_block_and_exact_rk(self):
        required='Before you can run ads, an admin needs to add a verified phone number.'
        self.assertEqual(advertiser_phone_status(required,True),'REQUIRED')
        self.assertEqual(advertiser_phone_status(required,False),'UNKNOWN')
        self.assertEqual(advertiser_phone_status('Phone number: Verified',True),'VERIFIED')
        for text in ['Phone number: Not verified','Verify your phone number','A verified phone number is recommended','Campaigns Create']:
            self.assertEqual(advertiser_phone_status(text,True),'UNKNOWN')

    def test_identity_editor_cannot_save_any_meta_mutation_or_unknown_post(self):
        for friendly, allowed in [('AdsCreatePageIdentityQuery',True),('SaveCampaignMutation',False),('',False)]:
            request=SimpleNamespace(method='POST',url='https://adsmanager.facebook.com/api/graphql/',
                post_data=urlencode({'fb_api_req_friendly_name':friendly}))
            self.assertEqual(allowed_readonly_request(request),allowed)
        self.assertFalse(allowed_readonly_request(SimpleNamespace(method='POST',url='https://graph.facebook.com/act_'+RK+'/campaigns',post_data='')))
        self.assertFalse(allowed_readonly_request(SimpleNamespace(method='GET',url='https://graph.facebook.com/graphql?fb_api_req_friendly_name=SaveCampaignMutation',post_data='')))
        self.assertFalse(allowed_readonly_request(SimpleNamespace(method='GET',url='https://graph.facebook.com/act_'+RK+'?method=delete',post_data='')))
        self.assertTrue(allowed_readonly_request(SimpleNamespace(method='GET',url='https://graph.facebook.com/v22.0/act_'+RK,post_data='')))
        self.assertTrue(allowed_readonly_request(SimpleNamespace(method='POST',url='https://graph.facebook.com/v22.0/act_'+RK,post_data='method=GET')))
        for batch, allowed in [([{'method':'GET','relative_url':'act_'+RK}],True),
                               ([{'method':'GET','relative_url':'act_'+RK},{'method':'POST','relative_url':'act_'+RK+'/campaigns'}],False),
                               ([{'method':'GET','relative_url':'act_'+RK+'?method=delete'}],False),([],False)]:
            req=SimpleNamespace(method='POST',url='https://graph.facebook.com/',post_data=urlencode({'batch':json.dumps(batch)}))
            self.assertEqual(allowed_readonly_request(req),allowed)
        get_batch=urlencode({'batch':json.dumps([{'method':'POST','relative_url':'act_'+RK+'/campaigns'}])})
        self.assertFalse(allowed_readonly_request(SimpleNamespace(method='GET',url='https://graph.facebook.com/?'+get_batch,post_data='')))
        read_batch=urlencode({'method':'POST','batch':json.dumps([{'method':'GET','relative_url':'act_'+RK}])})
        self.assertTrue(allowed_readonly_request(SimpleNamespace(method='POST',url='https://graph.facebook.com/',post_data=read_batch)))
        self.assertTrue(allowed_readonly_request(SimpleNamespace(method='POST',url='https://adsmanager.facebook.com/ajax/bulk-route-definitions/',post_data='')))
        self.assertFalse(allowed_readonly_request(SimpleNamespace(method='POST',url='https://adsmanager.facebook.com/ajax/save_campaign/',post_data='')))
    def payload(self, key='promotable_pages', rk=RK):
        return {'data':{'node':{'__typename':'AdAccount','id':rk,
            key:{'edges':[{'node':{'__typename':'Page','id':PAGE,'name':'ReMask Page'}}]}}}}

    def test_exact_account_promotable_connection(self):
        result=promotable_pages(self.payload(), {'friendly_name':'AdsPageQuery'}, RK)
        self.assertEqual([p['id'] for p in result], [PAGE])
        self.assertEqual(result[0]['account_id'], RK)
        self.assertTrue(result[0]['ad_account_page_access_verified'])

    def test_actor_owned_pages_and_bare_page_id_are_not_ad_permission(self):
        meta={'friendly_name':'AdsPromotablePageListQuery','variables':{'userId':BM}}
        for payload in [self.payload('owned_pages'),
                        {'data':{'user':{'id':BM,'promotable_pages':{'nodes':[{'id':PAGE,'name':'Page'}]}}}},
                        {'data':{'id':PAGE,'name':'Page','can_manage':True}}]:
            self.assertEqual(promotable_pages(payload,meta,RK),[])

    def test_wrong_account_and_conflicting_request_scope_rejected(self):
        self.assertEqual(promotable_pages(self.payload(rk='999999999'),{},RK),[])
        for variables in [{'adAccountID':'999999999'}, {'accountID':RK,'nested':{'adAccountId':'999999999'}}]:
            self.assertEqual(promotable_pages(self.payload(),{'variables':variables},RK),[])

    def test_scoped_request_without_account_node(self):
        payload={'data':{'promotable_pages':{'nodes':[{'id':PAGE,'name':'Page'}]}}}
        meta={'friendly_name':'AdsPromotablePageListQuery','variables':{'adAccountID':'act_'+RK}}
        self.assertEqual(promotable_pages(payload,meta,RK)[0]['id'],PAGE)
        self.assertEqual(request_accounts({'actorID':RK,'businessId':RK,'pageId':RK}),set())

    def test_errors_mutations_restrictions_and_profile_alias_rejected(self):
        for change in [{'__typename':'User'}, {'can_advertise':False},
                       {'advertising_restriction_info':{'is_restricted':True}}]:
            payload=self.payload(); payload['data']['node']['promotable_pages']['edges'][0]['node'].update(change)
            self.assertEqual(promotable_pages(payload,{},RK),[])
        self.assertEqual(promotable_pages({**self.payload(),'errors':[{'message':'denied'}]}, {},RK),[])
        self.assertEqual(promotable_pages(self.payload(),{'friendly_name':'CreatePageMutation'},RK),[])


class PageAccessBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def probe(self, scoped=True):
        callbacks={}; emitted=[]
        class Page:
            url='about:blank'
            def on(self,event,fn): callbacks[event]=fn
            def remove_listener(self,event,fn): callbacks.pop(event,None)
            async def route(self,pattern,fn): pass
            async def unroute(self,pattern,fn): raise AssertionError('write barrier must survive until context close')
            def get_by_role(self,*args,**kwargs):
                class Empty:
                    async def count(self): return 0
                return Empty()
            def locator(self,*args,**kwargs): return self.get_by_role()
            async def wait_for_timeout(self,ms): await asyncio.sleep(.001)
        page=Page()
        class Browser:
            async def _goto(self,url,**kwargs):
                emitted.append(url); page.url=url
                scope={'friendly_name':'AdsNorthStarBusinessUnifiedScopingSelectorQuery',
                    'variables':{'firstLevelScopeId':BM,'zeroLevelScopeId':RK if scoped else '999999999','businessIdForAddAA':BM}}
                def request(meta):
                    return SimpleNamespace(method='POST',url='https://adsmanager.facebook.com/api/graphql/',
                        post_data=urlencode({'fb_api_req_friendly_name':meta['friendly_name'],
                            'variables':json.dumps(meta['variables'])}))
                callbacks['request'](request(scope))
                req=request({'friendly_name':'AdsPromotablePageListQuery','variables':{'adAccountID':RK}})
                async def body(): return json.dumps(PageAccessEvidenceTests().payload())
                callbacks['response'](SimpleNamespace(url=req.url,request=req,text=body))
            async def _assert_authenticated(self): pass
        browser=Browser(); browser.page=page
        result=await inspect_browser_pages(browser,RK,BM,timeout=.05)
        self.assertEqual(callbacks,{})
        self.assertEqual(len(emitted),1)
        return result

    async def test_live_evidence_requires_selector_and_exact_account_url(self):
        result=await self.probe()
        self.assertEqual(result['status'],'VERIFIED')
        self.assertEqual(result['data'][0]['id'],PAGE)
        self.assertNotIn('cookies',json.dumps(result))

    async def test_wrong_selector_cannot_reuse_page_response(self):
        result=await self.probe(scoped=False)
        self.assertEqual(result['status'],'UNVERIFIED')
        self.assertEqual(result['data'],[])


class PageAccessApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_created_binding_is_navigation_only_and_conflicts_block_probe(self):
        from app.page_access_inspection import inspect_profile_pages
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=object()))
        state=SimpleNamespace(confirmed_ad_account_bindings_for_profile=AsyncMock(return_value=[
            {'business_id':BM,'ad_account_id':'act_'+RK}]))
        class Browser:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        probe=AsyncMock(return_value={'status':'UNVERIFIED','data':[], 'account_scope_verified':False})
        with patch('app.page_access_inspection.saved_payment_business',return_value=''), \
             patch('app.page_access_inspection.FacebookBusinessBrowser',return_value=Browser()), \
             patch('app.page_access_inspection.inspect_browser_pages',probe):
            result=await inspect_profile_pages(resolver,'9',RK,state=state)
            self.assertFalse(result['account_scope_verified'])
            self.assertEqual(probe.call_args.args[1:],(RK,BM))
            state.confirmed_ad_account_bindings_for_profile.assert_awaited_once_with('9')
            state.confirmed_ad_account_bindings_for_profile.return_value.append({'business_id':'999999999','ad_account_id':RK})
            with self.assertRaises(ValueError): await inspect_profile_pages(resolver,'9',RK,state=state)
            self.assertEqual(probe.await_count,1)

    async def test_memory_pressure_closes_browser_and_never_claims_page_permission(self):
        from app.page_access_inspection import inspect_profile_pages
        closed=[]
        class Browser:
            page=SimpleNamespace(locator=lambda *_:SimpleNamespace(inner_text=AsyncMock(return_value='Loading Creation')))
            async def __aenter__(self): return self
            async def __aexit__(self,*args): closed.append(True)
        async def stalled_probe(*args,**kwargs): await asyncio.Future()
        with patch('app.page_access_inspection.saved_payment_business',return_value=BM), \
             patch('app.page_access_inspection.FacebookBusinessBrowser',return_value=Browser()), \
             patch('app.page_access_inspection._cgroup_memory_snapshot_mb',return_value={'current_mb':940,'limit_mb':953}), \
             patch('app.page_access_inspection.inspect_browser_pages',side_effect=stalled_probe):
            result=await inspect_profile_pages(SimpleNamespace(resolve=AsyncMock(return_value=object())),'9',RK)
        self.assertEqual(closed,[True])
        self.assertEqual(result['diagnostic']['code'],'PAGE_ACCESS_MEMORY_LIMIT')
        self.assertEqual(result['diagnostic']['surface'],'Loading Creation')
        self.assertFalse(result['ad_account_page_access_verified'])
        self.assertEqual(result['data'],[])

    async def test_interrupted_browser_returns_safe_progress_after_context_close(self):
        from app.page_access_inspection import inspect_profile_pages
        closed=[]
        class Browser:
            async def __aenter__(self): return self
            async def __aexit__(self,*args): closed.append(True)
        async def probe(browser,target,business,**kwargs):
            kwargs['progress'].update({'stage':'waiting_for_identity_form','editor_steps':['objective_dialog_opened']})
            raise asyncio.TimeoutError
        with patch('app.page_access_inspection.saved_payment_business',return_value=BM), \
             patch('app.page_access_inspection.FacebookBusinessBrowser',return_value=Browser()), \
             patch('app.page_access_inspection.inspect_browser_pages',side_effect=probe):
            result=await inspect_profile_pages(SimpleNamespace(resolve=AsyncMock(return_value=object())),'9',RK)
        self.assertEqual(closed,[True])
        self.assertEqual(result['diagnostic']['stage'],'waiting_for_identity_form')
        self.assertEqual(result['diagnostic']['editor_steps'],['objective_dialog_opened'])
        self.assertEqual(result['diagnostic']['code'],'PAGE_ACCESS_INSPECTION_TIMEOUT')
        self.assertFalse(result['ad_account_page_access_verified'])

    async def test_saved_confirm_survives_live_timeout_without_claiming_ad_access(self):
        import main as api
        state=SimpleNamespace(latest_profile_fan_pages=AsyncMock(return_value=[
            {'id':PAGE,'main_business_id':BM,'main_business_confirmed':True},
            {'id':'123456','main_business_id':BM,'main_business_confirmed':False}]))
        with patch.object(api,'pool',SimpleNamespace(resolver=object(),provisioning_state=state)), \
             patch('app.page_access_inspection.inspect_profile_pages',AsyncMock(side_effect=asyncio.TimeoutError)):
            result=await api.profile_page_access('9','act_'+RK)
        self.assertEqual(result['account_id'],RK)
        self.assertEqual(result['diagnostic']['code'],'PAGE_ACCESS_INSPECTION_TIMEOUT')
        self.assertFalse(result['ad_account_page_access_verified'])
        self.assertFalse(result['checked_live'])
        self.assertEqual(result['data'],[])
        self.assertEqual(result['page_confirmations'][0]['id'],PAGE)
        self.assertEqual(len(result['page_confirmations']),1)
        state.latest_profile_fan_pages.assert_awaited_once_with('9')

    async def test_invalid_target_cannot_return_confirmation_or_run_browser(self):
        import main as api
        inspector=AsyncMock()
        with patch('app.page_access_inspection.inspect_profile_pages',inspector):
            with self.assertRaises(api.HTTPException) as caught:
                await api.profile_page_access('9','not-an-account')
        self.assertEqual(caught.exception.status_code,400)
        inspector.assert_not_awaited()
