"""Offline cases for the source-derived account/resource adapter."""
import copy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_catalog import AdsManagerRouteCatalog
from app.private_ads_http import context_from_document
from app.private_ads_reads import AdsManagerReadCatalog
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class BootstrapCases(unittest.TestCase):
    def test_literal_web_application_initializes_same_private_context(self):
        body = json.loads(document())
        body['require'] = [['WebApiApplication', 'init', [],
            ['synthetic-session-value', '119211728144504', None, None, None]]]
        context = context_from_document(json.dumps(body), account_id='111111', business_id='222222', actor_id='123456')
        self.assertEqual(context.application_id, '119211728144504')
        self.assertNotIn('synthetic-session-value', repr(context))

    def test_conflicting_initializers_fail_without_exposing_values(self):
        body = json.loads(document())
        body['require'] += [['WebApiApplication', 'init', [], ['other-synthetic-session', '119211728144504']]]
        with self.assertRaises(ProvisioningError) as raised:
            context_from_document(json.dumps(body), account_id='111111', business_id='222222', actor_id='123456')
        self.assertNotIn('other-synthetic-session', str(raised.exception))


class ResourceCases(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = AdsManagerReadCatalog(AdsManagerRouteCatalog())
        self.web = SimpleNamespace(profile=SimpleNamespace(cookies={'c_user': '123456'}))
        self.values = {'ad_account_id': '111111', 'business_id': '222222', 'query': '', 'limit': 100}
        self.calls = []
        self.account = {'id': 'act_111111', 'account_status': 1, 'business': {'id': '222222'},
                        'ad_account_promotable_objects': {'promotable_page_ids': ['555555']}}
        self.responses = {}
        self.client = SimpleNamespace(get=self.get)
        context = patch('app.private_ads_reads.AdsManagerPrivateHttp.open', new=AsyncMock(return_value=self.client))
        context.start()
        self.addCleanup(context.stop)

    async def get(self, path, params):
        self.calls.append((path, copy.deepcopy(params)))
        if path == '/act_111111':
            return copy.deepcopy(self.account)
        return copy.deepcopy(self.responses[path])

    async def test_all_source_targeting_families_use_the_same_exact_sender(self):
        from app.private_advertising_reads import TARGETING_TYPES
        for kind in TARGETING_TYPES:
            self.calls.clear()
            self.responses['/act_111111/targetingbrowse']={'data':[
                {'key':'root','name':'Root','parent':'root'},
                {'id':2020 if kind=='college_years' else 777777,'key':'leaf','name':'Fixture '+kind,'type':kind,'parent':'root'},
                {'id':'999999','key':'foreign','name':'Wrong type','type':'unrequested_type','parent':'root'}]}
            result=await self.catalog.read(self.web,kind,self.values)
            self.assertEqual(result['data'][0]['id'],'2020' if kind=='college_years' else '777777')
            self.assertEqual(len(result['data']),1)
            self.assertEqual(self.calls[-1][0],'/act_111111/targetingbrowse')
            self.assertEqual(self.calls[-1][1]['whitelisted_types'],[kind])
            self.assertTrue(result['account_scope_verified'])
        self.responses['/act_111111/targetingsearch']={'data':[
            {'id':777777,'name':'Exact employer','type':'work_employers'},
            {'id':'999999','name':'Wrong type','type':'interests'}]}
        result=await self.catalog.read(self.web,'work_employers',{**self.values,'query':'Exact'})
        self.assertEqual(result['data'],[{'id':'777777','name':'Exact employer','type':'work_employers'}])
        self.assertEqual(self.calls[-1][1]['whitelisted_types'],['work_employers'])

    async def test_scoped_resources_reject_conflicting_duplicate_ids(self):
        path = '/act_111111/customaudiences'
        first = {'id': '777777', 'name': 'Original audience'}
        same_id = {'id': '777777', 'name': 'Different audience'}
        self.responses[path] = {'data': [first, same_id]}
        with self.assertRaises(ProvisioningError) as caught:
            await self.catalog.read(self.web, 'custom_audiences', self.values)
        self.assertIn('RESOURCE_READ_INCOMPLETE', caught.exception.code)
        self.responses[path] = {'data': [first, dict(first)]}
        result = await self.catalog.read(self.web, 'custom_audiences', self.values)
        self.assertEqual(result['data'], [first])

    async def test_malformed_or_rejected_resource_envelopes_never_count_as_complete(self):
        for resource, path in (
            ("custom_audiences", "/act_111111/customaudiences"),
            ("interests", "/act_111111/targetingbrowse"),
        ):
            for invalid in ([], None, {"data": [], "error": {}}, {"data": [], "errors": []}):
                with self.subTest(resource=resource, invalid=invalid):
                    self.responses[path] = invalid
                    with self.assertRaises(ProvisioningError) as caught:
                        await self.catalog.read(self.web, resource, self.values)
                    self.assertIn("RESOURCE_READ_INCOMPLETE", caught.exception.code)

    async def test_rejected_instagram_media_envelope_is_not_a_successful_empty_inventory(self):
        self.responses["/act_111111/instagram_accounts"] = {"data": [{
            "id": "888888", "id_v2": "888889", "has_create_ads_access": True,
            "is_connected_to_ad_account": True}]}
        for invalid in ([], {"data": [], "error": {}}, {"data": [], "errors": []}):
            with self.subTest(invalid=invalid):
                self.responses["/888889/advertisable_media"] = invalid
                with self.assertRaises(ProvisioningError) as caught:
                    await self.catalog.read(self.web, "instagram_media", self.values)
                self.assertIn("RESOURCE_READ_INCOMPLETE", caught.exception.code)

    async def test_readonly_references_do_not_require_publishing_readiness(self):
        self.account['account_status']=2
        self.responses['/act_111111/targetingbrowse']={'data':[{'id':'777777','name':'Exact interest','type':'interests'}]}
        result=await self.catalog.read(self.web,'interests',self.values)
        self.assertTrue(result['checked_live']);self.assertEqual(result['data'][0]['id'],'777777')
        self.assertNotIn('account_status', self.calls[0][1]['fields'])
        self.assertNotIn('ad_account_promotable_objects', self.calls[0][1]['fields'])
        with self.assertRaises(ProvisioningError):await self.catalog.read(self.web,'images',self.values)
        self.account['business']['id']='foreign'
        with self.assertRaises(ProvisioningError):await self.catalog.read(self.web,'interests',self.values)

    async def test_unrelated_resources_do_not_request_promotable_page_permissions(self):
        self.responses['/act_111111/customaudiences'] = {'data':[]}
        await self.catalog.read(self.web, 'custom_audiences', self.values)
        self.assertNotIn('ad_account_promotable_objects', self.calls[0][1]['fields'])
        self.calls.clear()
        await self.catalog.read(self.web, 'pages', self.values)
        self.assertIn('ad_account_promotable_objects', self.calls[0][1]['fields'])

    async def test_account_scope_rejects_conflicting_secondary_account_id(self):
        self.account['account_id'] = '999999'
        with self.assertRaises(ProvisioningError) as caught:
            await self.catalog.read(self.web, 'pages', self.values)
        self.assertIn('RESOURCE_SCOPE_MISMATCH', caught.exception.code)
        self.account['account_id'] = '111111'
        result = await self.catalog.read(self.web, 'pages', self.values)
        self.assertEqual(result['account_id'], '111111')
        self.account['account_id'] = 'act_111111'
        result = await self.catalog.read(self.web, 'pages', self.values)
        self.assertEqual(result['data'][0]['id'], '555555')

    async def test_exact_business_and_nested_promotable_pages_are_required(self):
        result = await self.catalog.pages(self.web, self.values)
        self.assertEqual(result['data'][0]['id'], '555555')
        self.assertTrue(result['data'][0]['ad_account_page_access_verified'])
        self.account['business']['id'] = '999999'
        with self.assertRaises(ProvisioningError):
            await self.catalog.pages(self.web, self.values)

    async def test_pagination_never_follows_server_url_and_rejects_repeated_cursor(self):
        self.responses['/act_111111/adspixels'] = {'data': [{'id': '777777', 'name': 'pixel'}],
            'paging': {'next': 'https://foreign.invalid/?access_token=synthetic', 'cursors': {'after': 'repeated'}}}
        with self.assertRaises(ProvisioningError):
            await self.catalog.read(self.web, 'pixels', self.values)
        self.assertEqual([path for path, _ in self.calls], ['/act_111111', '/act_111111/adspixels', '/act_111111/adspixels'])

    async def test_invalid_resource_page_limits_fail_before_meta_http(self):
        for limit in (0, -1, True, False, 1.5, 'NaN', '0', '', None, [], {}):
            with self.subTest(limit=limit):
                with self.assertRaises(ProvisioningError) as caught:
                    await self.catalog.read(self.web, 'custom_audiences', {**self.values, 'limit': limit})
                self.assertIn('INVALID_RESOURCE_LIMIT', caught.exception.code)
                self.assertEqual(self.calls, [])
        self.responses['/act_111111/customaudiences'] = {'data': []}
        rows = await self.catalog.read(self.web, 'custom_audiences', {**self.values, 'limit': '10'})
        self.assertEqual(rows['data'], [])
        self.assertEqual(self.calls[-1][1]['limit'], 10)

    async def test_resource_route_rejects_foreign_account_without_http(self):
        reader = self.catalog.client(self.web, self.values)
        with self.assertRaises(ProvisioningError) as caught:
            await reader.request("INSTAGRAM_MEDIA_READ",
                                 values={"instagram_id": "888889", "ad_account_id": "999999"})
        self.assertIn("RESOURCE_SCOPE_MISMATCH", caught.exception.code)
        self.assertEqual(self.calls, [])

    async def test_missing_account_object_fails_with_scoped_resource_error(self):
        for missing in (None, [], "not an account"):
            with self.subTest(missing=missing):
                self.account = missing
                with self.assertRaises(ProvisioningError) as caught:
                    await self.catalog.read(self.web, "pages", self.values)
                self.assertIn("RESOURCE_READ_INCOMPLETE", caught.exception.code)

    async def test_instagram_media_rejects_foreign_account_actor_and_conflicting_duplicate(self):
        actors_path = "/act_111111/instagram_accounts"
        media_path = "/888889/advertisable_media"
        self.responses[actors_path] = {"data": [{
            "id": "888888", "id_v2": "888889", "has_create_ads_access": True,
            "is_connected_to_ad_account": True}]}
        rows = [
            [{"id": "777777", "account_id": "999999"}],
            [{"id": "777777", "instagram_actor_id": "999999"}],
            [{"id": "777777", "instagram_user_id": "999999"}],
            [{"id": "777777", "caption": "first"}, {"id": "777777", "caption": "changed"}],
        ]
        for selection in rows:
            with self.subTest(rows=selection):
                self.responses[media_path] = {"data": selection}
                with self.assertRaises(ProvisioningError):
                    await self.catalog.read(self.web, "instagram_media", self.values)
        self.responses[media_path] = {"data": [{"id": "777777", "caption": "same"},
                                               {"id": "777777", "caption": "same"}]}
        confirmed = await self.catalog.read(self.web, "instagram_media", self.values)
        self.assertEqual(len(confirmed["data"]), 1)
        self.assertEqual(confirmed["data"][0]["instagram_actor_id"], "888888")

    async def test_instagram_media_carries_its_eligible_account_actor(self):
        self.responses['/act_111111/instagram_accounts'] = {'data': [
            {'id': '888888', 'id_v2': '888889', 'username': 'synthetic', 'has_create_ads_access': True,
             'is_connected_to_ad_account': True},
            {'id': '999999', 'id_v2': '999998', 'has_create_ads_access': False, 'is_connected_to_ad_account': True}]}
        self.responses['/888889/advertisable_media'] = {'data': [{'id': '777777', 'caption': 'synthetic caption'}]}
        result = await self.catalog.read(self.web, 'instagram_media', self.values)
        self.assertEqual(result['data'][0]['instagram_actor_id'], '888888')
        self.assertEqual(result['data'][0]['instagram_user_id'], '888889')
        self.assertFalse(any(path.startswith('/999998') for path, _ in self.calls))

    async def test_unused_creatives_are_not_invented_from_ads_edge(self):
        self.responses['/act_111111/ads'] = {'data': [{'id': '777771', 'account_id': '111111',
            'creative': {'id': '777774', 'account_id': '999999', 'name': 'foreign'}}]}
        with self.assertRaises(ProvisioningError):
            await self.catalog.read(self.web, 'creatives', self.values)

    async def test_empty_interest_query_reads_tree_skips_headings_and_preserves_path(self):
        self.responses['/act_111111/targetingbrowse']={'data':[
            {'key':'__ROOT__','name':'__ROOT__','parent':'__ROOT__'},
            {'key':'fitness','name':'Fitness','parent':'__ROOT__'},
            {'key':'weight','id':'777777','name':'Weight training','type':'interests','parent':'fitness'},
            {'key':'device','id':'888888','name':'Device','type':'behaviors','parent':'__ROOT__'}]}
        result=await self.catalog.read(self.web,'interests',self.values)
        self.assertEqual(result['data'],[{'id':'777777','name':'Weight training','path':['Fitness']}])
        self.assertTrue(result['checked_live'])
        self.assertEqual(self.calls[-1][1]['whitelisted_types'],['interests'])

    async def test_browse_rejects_partial_response_and_conflicting_nodes(self):
        for response in ({'data':[], 'paging':{'next':'https://foreign.invalid'}},
            {'data':[{'key':'x','name':'one'},{'key':'x','name':'two'}]}):
            self.responses['/act_111111/targetingbrowse']=response
            with self.assertRaises(ProvisioningError):
                await self.catalog.read(self.web,'behaviors',self.values)

    async def test_typed_interest_search_still_uses_source_sender(self):
        self.responses['/act_111111/targetingsearch']={'data':[{'id':'777777','name':'Fitness'},
            {'id':'888888','name':'Unavailable interest','valid':False}]}
        result=await self.catalog.read(self.web,'interests',{**self.values,'query':'fit'})
        self.assertEqual(result['data'][0]['id'],'777777')
        self.assertEqual(self.calls[-1][0],'/act_111111/targetingsearch')
        self.assertEqual(self.calls[-1][1]['q'],'fit')
        self.assertEqual(len(result['data']),1)
        self.assertIn('valid', self.calls[-1][1]['fields'])

    async def test_complete_browse_tree_can_warm_template_cache_beyond_500_rows(self):
        self.responses['/act_111111/targetingbrowse']={'data':[
            {'id':str(700000+n),'name':'Interest '+str(n),'type':'interests'} for n in range(601)]}
        result=await self.catalog.read(self.web,'interests',{**self.values,'limit':20000})
        self.assertEqual(len(result['data']),601)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['catalog_count'],601)
