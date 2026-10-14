import unittest
from unittest.mock import AsyncMock, patch
from app.private_ads_catalog import AdsManagerRouteCatalog
from app.private_ads_helpers import read_form_helper
from app.provisioning.models import ProvisioningError


class HelperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = AdsManagerRouteCatalog()
        self.values = {'profile_id':'19','ad_account_id':'111111','business_id':'222222'}
        self.client = type('Client', (), {})()
        self.client.account, self.client.business = '111111', '222222'
        self.client.account_record = AsyncMock(return_value={})
        self.client.request = AsyncMock(return_value={'data':[{'estimate_ready':True,
            'estimate_mau_lower_bound':1000,'estimate_mau_upper_bound':5000}]})
        self.patcher = patch('app.private_ads_helpers._ReadClient', return_value=self.client)
        self.patcher.start(); self.addCleanup(self.patcher.stop)
        self.inputs = {'targeting_spec':{'geo_locations':{'countries':['UA']}},'optimization_goal':'LINK_CLICKS'}

    async def test_scoped_estimate_uses_reviewed_read_and_preserves_unknown_bounds(self):
        result = await read_form_helper(None, self.catalog, self.values, 'audience_estimate', self.inputs)
        self.assertEqual((result['lower_bound'],result['upper_bound']), (1000,5000))
        self.assertTrue(result['checked_live'])
        self.assertFalse(result['browser_started'])
        self.client.account_record.assert_awaited_once()
        self.client.account_record.assert_awaited_once_with(require_active=False,currency=True)
        self.assertEqual(self.client.request.await_args.args[0], 'DELIVERY_ESTIMATE')
        self.client.request.return_value = {'data':[{'estimate_ready':False,'estimate_mau_lower_bound':-1}]}
        result = await read_form_helper(None, self.catalog, self.values, 'audience_estimate', self.inputs)
        self.assertIsNone(result['lower_bound']); self.assertIsNone(result['upper_bound'])

    async def test_estimate_currency_comes_only_from_fresh_exact_account(self):
        self.client.account_record.return_value={'currency':'JPY'}
        result=await read_form_helper(None,self.catalog,self.values,'audience_estimate',
            {**self.inputs,'currency':'USD','daily_budget':500,'is_cbo_enabled':True})
        self.assertEqual(result['currency'],'JPY')
        params=self.client.request.await_args.kwargs['params']
        self.assertEqual(params['currency'],'JPY');self.assertTrue(params['is_cbo_enabled'])
        self.assertNotIn('daily_budget',params)

    async def test_foreign_account_scope_stops_before_estimate_dispatch(self):
        self.client.account_record.side_effect = ProvisioningError('RESOURCE_SCOPE_MISMATCH','foreign account',retryable=False)
        with self.assertRaises(ProvisioningError):
            await read_form_helper(None, self.catalog, self.values, 'audience_estimate', self.inputs)
        self.client.request.assert_not_awaited()

    async def test_only_live_source_known_placement_values_are_reported(self):
        self.client.request.return_value = {'data':[
            {'type':'facebook_positions','key':'facebook_reels'},
            {'type':'facebook_positions','key':'invented_future_position'},
            {'type':'instagram_positions','id':'story'}]}
        result = await read_form_helper(None,self.catalog,self.values,'placements',{})
        self.assertEqual(result['options'], {'facebook_positions':['facebook_reels'],'instagram_positions':['story']})
        self.client.account_record.assert_awaited_once_with(require_active=False,currency=False)
        self.assertEqual(self.client.request.await_args.args[0], 'TARGETING_BROWSE')
        self.client.request.return_value = {'data':[]}
        result = await read_form_helper(None,self.catalog,self.values,'placements',{})
        self.assertEqual(result['source'], 'meta_unavailable')
        self.assertEqual(result['options'], {})

    async def test_partial_placement_inventory_is_never_promoted_to_capabilities(self):
        self.client.request.return_value = {'data':[{'type':'facebook_positions','key':'feed'}],
            'paging':{'next':'https://invalid.example/?access_token=synthetic'}}
        with self.assertRaises(ProvisioningError):
            await read_form_helper(None,self.catalog,self.values,'placements',{})
        self.assertEqual(self.client.request.await_count,1)

    async def test_unknown_targeting_and_credentials_stop_before_helper_request(self):
        for inputs in ({'targeting_spec':{'unknown':True}}, {'targeting_spec':{'access_token':'synthetic'}}):
            with self.subTest(inputs=inputs),self.assertRaises(ProvisioningError):
                await read_form_helper(None,self.catalog,self.values,'audience_estimate',inputs)
        self.client.request.assert_not_awaited()
