import copy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_resources import AdsManagerResourceCatalog, read_pixels
from app.private_ads_media import _image_receipt, verify_image
from app.private_advertising_reads import AdvertisingReadCatalog, AdvertisingResourceStore
from app.provisioning.models import ProvisioningError


class PixelReads(unittest.IsolatedAsyncioTestCase):
    def client(self, *responses):
        return SimpleNamespace(context=SimpleNamespace(account_id='111111', business_id='222222'),
                               get=AsyncMock(side_effect=responses))

    async def test_image_receipt_and_scoped_read_reject_error_envelopes(self):
        image_name = 'synthetic.png'
        clean_receipt = {'images': {image_name: {'hash': 'aabbccdd11223344'}}}
        self.assertEqual(_image_receipt(clean_receipt, image_name)['image_hash'], 'aabbccdd11223344')
        for nested in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(nested=nested):
                with self.assertRaises(ProvisioningError):
                    _image_receipt({'images': {image_name: {
                        'hash': 'aabbccdd11223344', **nested}}}, image_name)
        for envelope in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(envelope=envelope):
                with self.assertRaises(ProvisioningError):
                    _image_receipt({**clean_receipt, **envelope}, image_name)
        good = {'data': [{'hash': 'aabbccdd11223344', 'account_id': '111111'}]}
        client = self.client(good)
        self.assertTrue((await verify_image(client, 'aabbccdd11223344'))['verified'])
        for page in ({'next': 'https://foreign.invalid/ignored'}, [], 'malformed'):
            with self.subTest(page=page):
                client = self.client({**good, 'paging': page})
                with self.assertRaises(ProvisioningError):
                    await verify_image(client, 'aabbccdd11223344')
        for envelope in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(envelope=envelope):
                client = self.client({**good, **envelope})
                with self.assertRaises(ProvisioningError):
                    await verify_image(client, 'aabbccdd11223344')
                self.assertEqual(client.get.await_count, 1)

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

    async def test_pixel_inputs_are_validated_before_any_http_request(self):
        for limit in (0, -3, False, True, 1.5, [], None, 'bogus', '0'):
            with self.subTest(limit=limit):
                client = self.client()
                with self.assertRaises(ProvisioningError) as caught:
                    await read_pixels(client, limit=limit)
                self.assertIn('INVALID_RESOURCE_LIMIT', caught.exception.code)
                client.get.assert_not_awaited()
        for query in (None, 42, 'x' * 257):
            with self.subTest(query=query):
                client = self.client()
                with self.assertRaises(ProvisioningError):
                    await read_pixels(client, query=query)
                client.get.assert_not_awaited()
        client = self.client({'data': []})
        self.assertEqual((await read_pixels(client, limit='10'))['data'], [])
        self.assertEqual(client.get.await_args.args[1]['limit'], 300)

    async def test_empty_error_envelopes_cannot_become_empty_confirmed_pixel_inventory(self):
        for envelope in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(envelope=envelope):
                response = {'data': [], **envelope}
                with self.assertRaises(ProvisioningError) as caught:
                    await read_pixels(self.client(response))
                self.assertIn('RESOURCE_READ_INCOMPLETE', caught.exception.code)

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

    async def test_pixel_account_lookup_rejects_conflicting_secondary_identity(self):
        catalog = AdsManagerResourceCatalog(AdvertisingReadCatalog())
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456',
                  'query': '', 'limit': 100}
        client = self.client({'id': 'act_111111', 'account_id': '999999',
                              'business': {'id': '222222'}})
        with patch('app.private_ads_resources.AdsManagerPrivateHttp.open',
                   new=AsyncMock(return_value=client)):
            with self.assertRaises(ProvisioningError) as caught:
                await catalog.read(SimpleNamespace(), 'pixels', values)
        self.assertIn('RESOURCE_SCOPE_MISMATCH', caught.exception.code)
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

    async def test_cached_page_permissions_are_never_represented_as_live_proof(self):
        from pathlib import Path
        from time import time
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456', 'query': ''}
        confirmed = {'data': [{'id': '555555', 'name': 'Saved Page',
                               'ad_account_page_access_verified': True}],
                     'account_id': '111111', 'checked_live': True,
                     'account_scope_verified': True,
                     'ad_account_page_access_verified': True,
                     'checked_at': int(time())}
        mock = SimpleNamespace(rows={'pages': {'source': 'fixture'}},
                               read=AsyncMock(return_value=confirmed))
        with tempfile.TemporaryDirectory() as folder:
            store = AdvertisingResourceStore(mock); store.root = Path(folder)
            observed = await store.get(name='pages', values=values,
                                       actor_id='123456', force=True, web=object())
            cached = await store.get(name='pages', values=values, actor_id='123456')
        self.assertTrue(observed['ad_account_page_access_verified'])
        self.assertFalse(cached['ad_account_page_access_verified'])
        self.assertFalse(cached['account_scope_verified'])
        self.assertFalse(cached['checked_live'])
        self.assertNotIn('ad_account_page_access_verified', cached['data'][0])

    async def test_partial_or_unverified_refresh_keeps_the_previous_confirmed_cache(self):
        from pathlib import Path
        from time import time
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456', 'query': '', 'limit': 100}
        complete = {'data': [{'hash': 'saved-image-hash', 'name': 'Saved'}],
                    'account_id': '111111', 'checked_live': True,
                    'account_scope_verified': True, 'inventory_complete': True,
                    'checked_at': int(time()), 'source': 'fixture'}
        mock = SimpleNamespace(rows={'images': {'source': 'fixture'}},
                               read=AsyncMock(return_value=complete))
        with tempfile.TemporaryDirectory() as folder:
            store = AdvertisingResourceStore(mock); store.root = Path(folder)
            await store.get(name='images', values=values, actor_id='123456', force=True, web=object())
            previous = await store.get(name='images', values=values, actor_id='123456')
            self.assertEqual(previous['data'], complete['data'])
            for changes in (
                {'data': [], 'truncated': True},
                {'data': [], 'query_required': True, 'checked_live': False},
                {'data': [], 'inventory_complete': False},
                {'data': [], 'account_scope_verified': False},
            ):
                with self.subTest(changes=changes):
                    mock.read.return_value = {**complete, **changes}
                    preview = await store.get(name='images', values=values,
                                              actor_id='123456', force=True, web=object())
                    self.assertTrue(preview['_cache']['stale'])
                    cached = await store.get(name='images', values=values, actor_id='123456')
                    self.assertEqual(cached['data'], complete['data'])
                    self.assertFalse(cached['checked_live'])
            mock.read.return_value = {**complete, 'account_id': '999999', 'data': []}
            with self.assertRaises(ProvisioningError):
                await store.get(name='images', values=values, actor_id='123456',
                                force=True, web=object())
            self.assertEqual((await store.get(name='images', values=values,
                                              actor_id='123456'))['data'], complete['data'])

    async def test_duplicate_json_fields_in_disk_cache_are_not_loaded(self):
        from pathlib import Path
        from time import time
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456', 'query': ''}
        result = {'data': [{'hash': 'cached-image'}], 'account_id': '111111',
                  'checked_live': True, 'account_scope_verified': True,
                  'checked_at': int(time())}
        mock = SimpleNamespace(rows={'images': {'source': 'fixture'}},
                               read=AsyncMock(return_value=result))
        with tempfile.TemporaryDirectory() as folder:
            store = AdvertisingResourceStore(mock); store.root = Path(folder)
            await store.get(name='images', values=values, actor_id='123456', force=True, web=object())
            path = next(Path(folder).glob('*.json'))
            valid = path.read_text()
            self.assertIn('"resource": "images"', valid)
            path.write_text(valid.replace('"resource": "images"',
                                          '"resource": "images", "resource": "images"', 1))
            cached = await store.get(name='images', values=values, actor_id='123456')
            self.assertEqual(cached['availability'], 'NOT_LOADED')
            self.assertFalse(cached['checked_live'])

    async def test_resource_timestamps_cannot_poison_or_replace_a_valid_cache(self):
        from pathlib import Path
        from time import time
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456', 'query': ''}
        good = {'data': [{'hash': 'saved-media-hash'}], 'account_id': '111111',
                'checked_live': True, 'account_scope_verified': True,
                'checked_at': int(time())}
        reader = SimpleNamespace(rows={'images': {'source': 'fixture'}},
                                 read=AsyncMock(return_value=good))
        with tempfile.TemporaryDirectory() as folder:
            store = AdvertisingResourceStore(reader); store.root = Path(folder)
            await store.get(name='images', values=values, actor_id='123456', force=True, web=object())
            for malformed in (None, 'today', True, 0, -100, 1.5, float('nan'),
                              int(time()) + 3600):
                with self.subTest(timestamp=malformed):
                    reader.read.return_value = {**good, 'data': [], 'checked_at': malformed}
                    with self.assertRaises(ProvisioningError) as caught:
                        await store.get(name='images', values=values, actor_id='123456',
                                        force=True, web=object())
                    self.assertIn('RESOURCE_READ_INCOMPLETE', caught.exception.code)
                    snapshot = await store.get(name='images', values=values, actor_id='123456')
                    self.assertEqual(snapshot['data'], good['data'])
                    self.assertFalse(snapshot['checked_live'])

    async def test_malformed_cached_resource_returns_unloaded_instead_of_live_proof(self):
        from pathlib import Path
        from time import time
        import json
        values = {'profile_id': '19', 'ad_account_id': '111111',
                  'business_id': '222222', 'actor_id': '123456', 'query': ''}
        response = {'data': [{'hash': 'fixture-hash'}], 'account_id': '111111',
                    'checked_live': True, 'account_scope_verified': True,
                    'checked_at': int(time())}
        mock = SimpleNamespace(rows={'images': {'source': 'fixture'}},
                               read=AsyncMock(return_value=response))
        with tempfile.TemporaryDirectory() as folder:
            store = AdvertisingResourceStore(mock); store.root = Path(folder)
            await store.get(name='images', values=values, actor_id='123456', force=True, web=object())
            path = next(Path(folder).glob('*.json'))
            saved = json.loads(path.read_text())
            for malformed in ({'data': [None], 'account_id': '111111'},
                              {'data': [], 'account_id': '999999'},
                              {'data': [], 'account_id': '111111', 'checked_at': 'now'},
                              {**response, 'truncated': True},
                              {**response, 'inventory_complete': False},
                              {**response, 'query_required': True},
                              {**response, 'checked_live': False},
                              {**response, 'account_scope_verified': False},
                              {**response, 'checked_at': True},
                              {**response, 'checked_at': -1},
                              {**response, 'checked_at': float('nan')},
                              {**response, 'checked_at': int(time()) + 3600}):
                with self.subTest(malformed=malformed):
                    saved['result'] = malformed
                    path.write_text(json.dumps(saved))
                    result = await store.get(name='images', values=values, actor_id='123456')
                    self.assertEqual(result['availability'], 'NOT_LOADED')
                    self.assertFalse(result['checked_live'])
