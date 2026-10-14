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
        self.responses['/act_111111/targetingsearch']={'data':[{'id':'777777','name':'Fitness'}]}
        result=await self.catalog.read(self.web,'interests',{**self.values,'query':'fit'})
        self.assertEqual(result['data'][0]['id'],'777777')
        self.assertEqual(self.calls[-1][0],'/act_111111/targetingsearch')
        self.assertEqual(self.calls[-1][1]['q'],'fit')
