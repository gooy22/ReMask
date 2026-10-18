import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import main as api


class SelectedBusinessInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_expected_name_uses_existing_exact_profile_binding_without_inventing_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bindings.json'
            path.write_text(json.dumps({'8':{'ad_accounts':{'1632909278268870':{'business_id':'1632909278268870','ad_account_id':'1758104775449075','account_name':'ReMask RK 8 20261002'}}},'Other':{'business_id':'999999999','ad_account_id':'888888888','account_name':'Other RK'}}))
            names=api._sync_expected_account_names('8',[],str(path))
            self.assertEqual(names,{'1632909278268870':'ReMask RK 8 20261002'})
            self.assertEqual(api._sync_expected_account_names('Missing',[],str(path)),{})
            path.write_text('{broken')
            self.assertEqual(api._sync_expected_account_names('8',[{'business_id':'123456789','account_name':'Worker RK'}],str(path)),{'123456789':'Worker RK'})

    def fixtures(self, bindings=(), responder=None):
        from urllib.parse import parse_qs, urlsplit
        from app.session import ProfileContext
        store=SimpleNamespace(**{name:AsyncMock(return_value=[]) for name in (
            'confirmed_ad_account_bindings_for_profile','confirmed_business_page_bindings_for_profile',
            'latest_profile_fan_pages','latest_profile_fan_page_batch')})
        store.confirmed_ad_account_bindings_for_profile.return_value=list(bindings)
        store.latest_profile_entities=AsyncMock(return_value={})
        context=ProfileContext(profile_id='fixture',pages=[],cookies={'c_user':'999999999999'},
            proxy='http://fixture:8080',user_agent='fixture')
        pool=SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)),provisioning_state=store)
        calls=[]
        async def fetch(url,**kwargs):
            calls.append(url)
            business=parse_qs(urlsplit(url).query).get('business_id',[''])[0]
            value=responder(url,business) if responder else self.inventory(business or '111111111',[])
            if isinstance(value,BaseException): raise value
            if isinstance(value,tuple): return value
            return 200,'<script type="application/json">'+json.dumps(value)+'</script>',url
        web=SimpleNamespace(fetch_text=fetch,calls=calls,graphql=AsyncMock())
        session=SimpleNamespace(facebook_web=AsyncMock(return_value=web),facebook_business_browser=AsyncMock())
        factory=MagicMock()
        factory.return_value.__aenter__=AsyncMock(return_value=session)
        factory.return_value.__aexit__=AsyncMock(return_value=False)
        return pool,web,session,factory

    @staticmethod
    def inventory(business,accounts,partial=False):
        return {'data':{'business':{'__typename':'Business','id':business,'name':'Fixture BM',
            'ad_accounts':{'edges':[{'node':{'__typename':'AdAccount','id':account,'name':'Fixture RK'}} for account in accounts],
                'page_info':{'has_next_page':partial}}}}}

    async def sync(self,fixtures,**kwargs):
        pool,web,session,factory=fixtures
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory), \
                patch.object(api,'list_pages_via_private_graphql',AsyncMock(side_effect=RuntimeError('optional Page read'))):
            result=await api.profile_live_inventory('fixture',**kwargs)
        session.facebook_business_browser.assert_not_awaited()
        return result

    async def test_selected_empty_business_is_probed_without_profile_discovery(self):
        fixtures=self.fixtures()
        result=await self.sync(fixtures,business_ids='1632909278268870')
        self.assertTrue(result['live_ready'])
        self.assertTrue(result['businesses'][0]['confirmed_empty'])
        self.assertEqual(len(fixtures[1].calls),1)
        self.assertIn('business_id=1632909278268870',fixtures[1].calls[0])

    async def test_full_profile_discovers_businesses_even_with_old_account_binding(self):
        def respond(url,business):
            return self.inventory(business or '333333333',['222222222'] if business=='111111111' else ['444444444'])
        fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],respond)
        result=await self.sync(fixtures)
        self.assertTrue(result['live_ready'])
        self.assertEqual({row['id'] for row in result['businesses']},{'111111111','333333333'})
        self.assertTrue(any('/latest/home' in url for url in fixtures[1].calls))

    async def test_full_profile_confirmed_empty_selector_reaches_ui_as_live_empty(self):
        fixtures = self.fixtures()
        fixtures[1].graphql.return_value = {'data': {'viewer': {'meta_business_scoping': {
            'business_scopes': {'nodes': [], 'page_info': {'has_next_page': False}}}}}}
        result = await self.sync(fixtures)
        self.assertTrue(result['live_ready'])
        self.assertTrue(result['business_inventory_confirmed_empty'])
        self.assertEqual(result['businesses'], [])
        self.assertEqual(fixtures[1].calls, [])

    async def test_unconfirmed_selected_account_hint_does_not_become_live_inventory(self):
        fixtures=self.fixtures([{'business_id':'1632909278268870','ad_account_id':'120251669477430356'}])
        result=await self.sync(fixtures,business_ids='1632909278268870')
        self.assertFalse(result['live_ready'])
        self.assertFalse(result['business_inventory_confirmed_empty'])
        self.assertFalse(result['browser_started'])
        self.assertFalse(any('/latest/home' in url for url in fixtures[1].calls))

    async def test_settings_timeout_can_recover_from_same_profile_ads_http_response(self):
        def respond(url,business):
            if '/settings/ad_accounts' in url:return asyncio.TimeoutError()
            return self.inventory(business or '111111111',['222222222'])
        fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],respond)
        result=await self.sync(fixtures,business_ids='111111111')
        self.assertTrue(result['live_ready'])
        self.assertEqual(result['businesses'][0]['ad_accounts'][0]['id'],'222222222')
        self.assertTrue(any('adsmanager.facebook.com' in url for url in fixtures[1].calls))

    async def test_all_private_surfaces_timing_out_remains_failure(self):
        fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],
            lambda url,business:asyncio.TimeoutError())
        result=await self.sync(fixtures,business_ids='111111111')
        self.assertFalse(result['live_ready'])
        self.assertEqual(result['sync_error_kind'],'PRIVATE_HTTP_READ_TIMEOUT')
        self.assertFalse(result['business_inventory_confirmed_empty'])

    async def test_query_sender_can_confirm_inventory_when_html_has_no_rows(self):
        from tests.test_private_inventory_queries import artifact,response
        def respond(url,business):
            return 200,'<script>'+artifact()+'</script>',url
        fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],respond)
        web=fixtures[1]
        web.graphql.return_value=response(business='111111111',accounts=['222222222'])
        result=await self.sync(fixtures,business_ids='111111111')
        self.assertTrue(result['live_ready'])
        web.graphql.assert_awaited_once()
        self.assertEqual(web.graphql.await_args.args[1]['businessID'],'111111111')

    async def test_single_live_account_is_partial_when_source_is_paginated(self):
        for selected in (False,True):
            with self.subTest(selected=selected):
                fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],
                    lambda url,business:self.inventory(business or '111111111',['222222222'],partial=True))
                result=await self.sync(fixtures,business_ids='111111111' if selected else '')
                self.assertTrue(result['live_ready'])
                self.assertTrue(result['businesses'][0]['ad_accounts_partial'])
                self.assertFalse(result['businesses'][0]['inventory_complete'])
                self.assertEqual(result['businesses'][0]['ad_accounts_count'],1)

    async def test_personal_scope_cannot_hide_the_known_real_business(self):
        fixtures=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}],
            lambda url,business:self.inventory(business or '999999999999',['222222222']))
        result=await self.sync(fixtures)
        self.assertTrue(result['live_ready'])
        self.assertEqual([row['id'] for row in result['businesses']],['111111111'])
        self.assertFalse(any('business_id=999999999999' in url for url in fixtures[1].calls))

    async def test_display_name_hint_cannot_confirm_account_absence(self):
        fixtures=self.fixtures(responder=lambda url,business:{'data':{'business':{'__typename':'Business','id':business}}})
        fixtures[0].provisioning_state.ad_account_name_hints_for_profile=AsyncMock(return_value={
            '111111111':'Fixture RK'})
        result=await self.sync(fixtures,business_ids='111111111')
        self.assertFalse(result['live_ready'])
        self.assertFalse(result['business_inventory_confirmed_empty'])
        self.assertFalse(result['businesses'][0]['confirmed_empty'])

    def test_personal_scope_and_request_metadata_do_not_prove_an_empty_inventory(self):
        self.assertFalse(api._business_inventory_confirmed_empty({
            'stage':'complete','rejected_personal_scope_ids':['61594848550001'],
            'business_inventory_confirmed_empty':True,'queries':[]}))
        self.assertFalse(api._business_inventory_confirmed_empty({
            'stage':'complete','queries':[{'phase':'selector_request',
            'friendly_name':'NorthStarBusinessUnifiedScopingSelectorQuery','live_business_ids':[]}]}))
