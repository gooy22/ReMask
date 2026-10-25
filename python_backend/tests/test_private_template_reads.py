import asyncio
import unittest
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.private_ads_catalog import AdsManagerRouteCatalog
from app.private_template_reads import read_template_reference, _sources
from app.provisioning.models import ProvisioningError


class TemplateReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _sources.clear()
        self.catalog = AdsManagerRouteCatalog()
        self.context = SimpleNamespace(profile_id='20', ad_accounts=[])
        self.web = SimpleNamespace(profile=SimpleNamespace(name='20', cookies={'c_user':'999999'},
            proxy='http://fixture.invalid', user_agent='Fixture'))
        self.inputs = {'targeting_spec':{'geo_locations':{'countries':['UA']}}, 'include_forecast':False}
        self.account = {'id':'111111', 'business_id':'222222', 'account_status':1}
        self.snapshot = {'ready':False, 'businesses':[{'id':'222222','ad_accounts':[self.account]}]}
        self.discovery = AsyncMock(return_value=self.snapshot)
        self.businesses = AsyncMock(return_value={'rows':{}, 'complete':True})
        self.accounts = AsyncMock(return_value={'ids':[], 'complete':True})
        self.helper = AsyncMock(return_value={'checked_live':True, 'account_scope_verified':True,
            'profile':'20', 'account_id':'111111', 'business_id':'222222', 'transport':'private_http_h2'})
        for name, mock in [('private_inventory_snapshot',self.discovery), ('read_form_helper',self.helper),
                ('read_business_inventory',self.businesses), ('read_ad_account_inventory',self.accounts)]:
            patcher = patch('app.private_template_reads.'+name, mock)
            patcher.start(); self.addCleanup(patcher.stop)

    async def read(self, operation='audience_estimate', inputs=None, resource_store=None):
        return await read_template_reference(self.web,self.catalog,self.context,operation,
            self.inputs if inputs is None else inputs, resource_store=resource_store)

    async def test_new_profile_discovers_reference_without_saved_inventory_or_global_ready(self):
        result = await self.read()
        self.assertTrue(result['source_context_discovered'])
        self.assertFalse(result['template_account_bound'])
        self.assertEqual(self.context.ad_accounts, [])
        self.assertEqual(self.helper.await_args.args[2]['business_id'], '222222')
        self.assertEqual(self.helper.await_args.args[2]['ad_account_id'], '111111')
        self.assertTrue(self.discovery.await_args.kwargs['execute_read_queries'])
        self.assertTrue(self.discovery.await_args.kwargs['fast_sync'])
        self.assertEqual(self.discovery.await_args.kwargs['personal_scope_id'],'999999')

    async def test_cached_hint_still_requires_fresh_account_read_and_cookie_change_rediscovers(self):
        await self.read(); await self.read()
        self.assertEqual(self.discovery.await_count,1)
        self.assertEqual(self.helper.await_count,2)
        self.web.profile.cookies['xs']='fixture-renewal'
        await self.read()
        self.assertEqual(self.discovery.await_count,2)

    async def test_native_read_modules_supply_missing_html_inventory_then_revalidate_exact_pair(self):
        self.discovery.return_value={'businesses':[], 'diagnostics':[]}
        self.businesses.return_value={'rows':{'222222':'BM'}, 'complete':False}
        self.accounts.return_value={'ids':['111111'], 'complete':False}
        result = await self.read()
        self.accounts.assert_awaited_once_with(self.web, '222222', '')
        self.assertEqual(self.helper.await_args.args[2]['ad_account_id'], '111111')
        self.assertEqual(self.helper.await_args.args[2]['business_id'], '222222')
        self.assertTrue(result['account_scope_verified'])
        self.assertEqual(self.context.ad_accounts, [])

    async def test_discovered_business_hints_avoid_second_business_listing(self):
        self.discovery.return_value = {
            'businesses': [], 'discovered_business_ids': ['222222'],
            'diagnostics': []}
        self.accounts.return_value = {'ids': ['111111'], 'complete': False}
        result = await self.read()
        self.businesses.assert_not_awaited()
        self.accounts.assert_awaited_once_with(self.web, '222222', '')
        self.assertTrue(result['account_scope_verified'])
        self.assertEqual(self.helper.await_count, 1)

    async def test_discovered_business_hints_still_require_fresh_account_proof(self):
        self.discovery.return_value = {
            'businesses': [], 'discovered_business_ids': ['222222'],
            'diagnostics': []}
        self.accounts.return_value = {'ids': ['111111'], 'complete': False}
        self.helper.return_value['account_scope_verified'] = False
        with self.assertRaises(ProvisioningError):
            await self.read()
        self.businesses.assert_not_awaited()
        self.assertEqual(_sources, {})

    async def test_native_discovery_never_substitutes_personal_scope_or_invalid_ids(self):
        self.discovery.return_value={'businesses':[]}
        self.businesses.return_value={'rows':{'999999':'Personal','invalid':'Bad'}}
        with self.assertRaises(ProvisioningError): await self.read()
        self.accounts.assert_not_awaited(); self.helper.assert_not_awaited()

    async def test_auth_gate_stops_before_native_inventory_modules(self):
        self.discovery.return_value={'businesses':[], 'diagnostics':[{'auth_gate':'CHECKPOINT_REQUIRED'}]}
        with self.assertRaises(ProvisioningError): await self.read()
        self.businesses.assert_not_awaited(); self.accounts.assert_not_awaited()

    async def test_native_inventory_never_replaces_fresh_account_scope_proof(self):
        self.discovery.return_value={'businesses':[]}
        self.businesses.return_value={'rows':{'222222':'BM'}}
        self.accounts.return_value={'ids':['111111']}
        self.helper.return_value['account_scope_verified']=False
        with self.assertRaises(ProvisioningError): await self.read()
        self.assertEqual(_sources,{})

    async def test_foreign_cached_pair_falls_through_to_another_same_profile_pair(self):
        self.context.ad_accounts=[self.account,{'id':'333333','business_id':'444444'}]
        self.helper.side_effect=[ProvisioningError('RESOURCE_SCOPE_MISMATCH','foreign pair',retryable=False),
            {**self.helper.return_value,'account_id':'333333','business_id':'444444'}]
        await self.read()
        self.assertEqual(self.helper.await_count,2)
        self.assertEqual(self.helper.await_args.args[2]['ad_account_id'],'333333')
        self.discovery.assert_not_awaited()

    async def test_reference_search_uses_the_same_reviewed_reader_and_transport(self):
        reader=SimpleNamespace(read=AsyncMock(return_value={'checked_live':True,
            'account_scope_verified':True,'account_id':'111111','data':[]}))
        with patch('app.private_template_reads.AdsManagerReadCatalog',return_value=reader):
            result=await self.read('interests',{'q':'fitness','limit':50})
        self.assertEqual(reader.read.await_args.args[1],'interests')
        self.assertEqual(reader.read.await_args.args[2]['query'],'fitness')
        self.assertEqual(result['transport'],'private_http_h2')
        self.assertFalse(result['browser_started'])

    async def test_browse_cache_request_keeps_complete_tree_budget_but_typed_queries_stay_bounded(self):
        reader=SimpleNamespace(read=AsyncMock(return_value={'checked_live':True,
            'account_scope_verified':True,'account_id':'111111','data':[]}))
        with patch('app.private_template_reads.AdsManagerReadCatalog',return_value=reader):
            await self.read('interests',{'q':'','limit':20000})
            self.assertEqual(reader.read.await_args.args[2]['limit'],20000)
            await self.read('interests',{'q':'fitness','limit':20000})
            self.assertEqual(reader.read.await_args.args[2]['limit'],500)

    async def test_launch_targeting_reuses_scoped_persistent_index_before_meta(self):
        self.context.ad_accounts = [self.account]
        saved = {'data':[{'id':'987654','name':'Travel','type':'interests'}],
            'account_id':'111111','business_id':'222222','checked_live':False,
            'account_scope_verified':False,'checked_at':int(time.time()),
            'source':'private_meta_targeting_option_index',
            '_cache':{'hit':True,'cached_only':True,'index':True,'stale':False,
                'fetched_at':int(time.time())}}
        resources = SimpleNamespace(TARGETING_OPTION_TYPES=frozenset({'interests','behaviors','locations','locales'}),
            get=AsyncMock(return_value=saved))
        result = await self.read('interests', {'q':'trav','limit':50}, resource_store=resources)
        self.assertEqual(result['data'][0]['id'],'987654')
        self.assertFalse(result['checked_live'])
        self.assertFalse(result['account_scope_verified'])
        self.assertEqual(result['source'],'private_meta_targeting_option_index')
        self.assertEqual(result['profile'],'20')
        self.assertEqual(result['business_id'],'222222')
        self.discovery.assert_not_awaited()
        self.helper.assert_not_awaited()
        resources.get.assert_awaited_once()
        self.assertEqual(resources.get.await_args.kwargs['actor_id'],'999999')
        self.assertNotIn('force',resources.get.await_args.kwargs)

    async def test_launch_targeting_cache_miss_refreshes_through_persistent_resource_store(self):
        self.context.ad_accounts = [self.account]
        cached = {'data':[],'availability':'NOT_LOADED',
            '_cache':{'hit':False,'cached_only':True}}
        fresh = {'data':[{'id':'987654','name':'Travel','type':'interests'}],
            'account_id':'111111','checked_live':True,'account_scope_verified':True,
            'checked_at':int(time.time()),'source':'ads_manager_private_http',
            '_cache':{'hit':False,'cached_only':False,'stale':False}}
        resources = SimpleNamespace(TARGETING_OPTION_TYPES=frozenset({'interests','behaviors','locations','locales'}),
            get=AsyncMock(side_effect=[cached,fresh]))
        result = await self.read('interests', {'q':'Travel','limit':50}, resource_store=resources)
        self.assertEqual(result['data'][0]['id'],'987654')
        self.assertTrue(result['checked_live'])
        self.assertTrue(result['account_scope_verified'])
        self.assertEqual(result['profile'],'20')
        self.assertEqual(result['business_id'],'222222')
        self.assertEqual(resources.get.await_count,2)
        self.assertTrue(resources.get.await_args.kwargs['force'])
        self.assertIs(resources.get.await_args.kwargs['web'],self.web)
        self.assertEqual(resources.get.await_args.kwargs['values']['ad_account_id'],'111111')

    async def test_owned_resources_and_mutations_are_rejected_before_network(self):
        for operation,inputs in [('preview',{}),('PUBLISH',{}),('images',{}),
                ('audience_estimate',{'account_id':'111111'}),
                ('audience_estimate',{'targeting_spec':{'custom_audiences':[{'id':'123456'}]}}),
                ('audience_estimate',{'promoted_object':{'pixel_id':'123456'}})]:
            with self.subTest(operation=operation,inputs=inputs), self.assertRaises(ProvisioningError):
                await self.read(operation,inputs)
        self.discovery.assert_not_awaited();self.helper.assert_not_awaited()

    async def test_personal_scope_cannot_be_substituted_for_business(self):
        self.account['business_id']='999999'
        with self.assertRaises(ProvisioningError) as raised:
            await self.read()
        self.assertTrue(raised.exception.code.endswith('TEMPLATE_LOOKUP_NO_ACCOUNT'))
        self.helper.assert_not_awaited()

    async def test_authentication_failure_does_not_replay_on_another_account(self):
        self.context.ad_accounts=[self.account,{'id':'333333','business_id':'444444'}]
        self.helper.side_effect=ProvisioningError('SESSION_EXPIRED','expired',retryable=False)
        with self.assertRaises(ProvisioningError): await self.read()
        self.assertEqual(self.helper.await_count,1)

    async def test_discovery_timeout_and_auth_gate_never_invent_context(self):
        self.discovery.side_effect=asyncio.TimeoutError()
        with self.assertRaises(asyncio.TimeoutError): await self.read()
        self.discovery.side_effect=None
        self.discovery.return_value={'businesses':[], 'diagnostics':[{'auth_gate':'SESSION_EXPIRED'}]}
        with self.assertRaises(ProvisioningError) as raised: await self.read()
        self.assertTrue(raised.exception.code.endswith('SESSION_EXPIRED'))
        self.helper.assert_not_awaited();self.assertEqual(_sources,{})

    async def test_unverified_reference_result_cannot_warm_source_cache(self):
        self.helper.return_value['checked_live']=False
        with self.assertRaises(ProvisioningError): await self.read()
        self.assertEqual(_sources,{})

    async def test_foreign_response_cannot_warm_source_cache(self):
        self.helper.return_value['account_id']='555555'
        with self.assertRaises(ProvisioningError): await self.read()
        self.assertEqual(_sources,{})
