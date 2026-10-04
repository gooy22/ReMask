import asyncio
import json
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode

from app.page_access_inspection import promotable_pages, request_accounts, inspect_browser_pages

RK='2279305019588057'
BM='61594882851656'
PAGE='1270757506131209'


class PageAccessEvidenceTests(unittest.TestCase):
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
