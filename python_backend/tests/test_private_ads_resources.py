import copy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_resources import AdsManagerResourceCatalog, read_pixels
from app.private_advertising_reads import AdvertisingReadCatalog, AdvertisingResourceStore
from app.provisioning.models import ProvisioningError


class PixelReads(unittest.IsolatedAsyncioTestCase):
    def client(self, *responses):
        return SimpleNamespace(context=SimpleNamespace(account_id='111111', business_id='222222'),
                               get=AsyncMock(side_effect=responses))

    async def test_paging_keeps_account_route_and_never_follows_next_url(self):
        client = self.client({'data':[], 'paging':{'next':'https://other.invalid/?access_token=secret',
            'cursors':{'after':'first-page-cursor'}}}, {'data':[
                {'id':'777771','name':'Shared pixel','owner_ad_account':{'account_id':'999999'}},
                {'id':'777772','name':'Unavailable','is_unavailable':True}]})
        result = await read_pixels(client)
        self.assertEqual(result['data'], [{'id':'777771','name':'Shared pixel'}])
        self.assertTrue(result['inventory_complete'])
        self.assertTrue(result['account_scope_verified'])
        self.assertEqual([call.args[0] for call in client.get.await_args_list],
                         ['/act_111111/adspixels', '/act_111111/adspixels'])
        self.assertEqual(client.get.await_args_list[1].args[1]['after'], 'first-page-cursor')
        self.assertNotIn('secret', str(client.get.await_args_list))
        self.assertNotIn('secret', str(result))

    async def test_repeated_or_missing_cursor_cannot_mark_inventory_complete(self):
        for paging in ({'next':'next'}, {'next':'next','cursors':{'after':'repeat'}}):
            response = {'data':[], 'paging':paging}
            client = self.client(response, response)
            with self.subTest(paging=paging), self.assertRaises(ProvisioningError):
                await read_pixels(client)
            self.assertLessEqual(client.get.await_count, 2)

    async def test_conflicting_duplicate_or_error_response_cannot_pass(self):
        for response in ({'data':[{'id':'777771','name':'x'}, {'id':'777771','name':'x','is_unavailable':True}]},
                         {'data':[], 'error':{'code':100}}, {'data':'not-a-list'}):
            with self.subTest(response=response), self.assertRaises(ProvisioningError):
                await read_pixels(self.client(response))

    async def test_filter_and_limit_never_hide_truncation(self):
        rows = [{'id':'777771','name':'PIXEL One'}, {'id':'777772','name':'Pixel Two'}]
        result = await read_pixels(self.client({'data':rows}), query='pixel', limit=1)
        self.assertEqual(result['data'], rows[:1])
        self.assertFalse(result['inventory_complete'])
        self.assertTrue(result['truncated'])

    async def test_catalog_proves_current_account_business_before_pixel_edge(self):
        catalog = AdsManagerResourceCatalog(AdvertisingReadCatalog())
        values = {'profile_id':'19','ad_account_id':'111111','business_id':'222222',
                  'actor_id':'123456','query':'','limit':100}
        client = self.client({'id':'act_111111','business':{'id':'999999'}})
        with patch('app.private_ads_resources.AdsManagerPrivateHttp.open',new=AsyncMock(return_value=client)):
            with self.assertRaises(ProvisioningError):
                await catalog.read(SimpleNamespace(), 'pixels', values)
        self.assertEqual(client.get.await_count, 1)

    async def test_existing_resource_cache_does_not_claim_live_pixel_access(self):
        from pathlib import Path
        catalog = AdsManagerResourceCatalog(AdvertisingReadCatalog())
        values = {'profile_id':'19','ad_account_id':'111111','business_id':'222222',
                  'actor_id':'123456','query':'','limit':100}
        client = self.client({'id':'act_111111','business':{'id':'222222'}},
                            {'data':[{'id':'777771','name':'Pixel'}]})
        with tempfile.TemporaryDirectory() as root, \
                patch('app.private_ads_resources.AdsManagerPrivateHttp.open',new=AsyncMock(return_value=client)):
            store = AdvertisingResourceStore(catalog);store.root = Path(root)
            live = await store.get(name='pixels', values=values, actor_id='123456', force=True, web=SimpleNamespace())
            cached = await store.get(name='pixels', values=values, actor_id='123456')
        self.assertTrue(live['checked_live'])
        self.assertEqual(cached['data'], [{'id':'777771','name':'Pixel'}])
        self.assertFalse(cached['checked_live'])
        self.assertFalse(cached['account_scope_verified'])
