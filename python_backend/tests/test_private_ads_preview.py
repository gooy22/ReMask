import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_catalog import AdsManagerRouteCatalog
from app.private_ads_http import AdsManagerPrivateHttp, context_from_document
from app.private_ads_preview import read_preview
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document
from tests.test_private_ads_http import Web


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.catalog = AdsManagerRouteCatalog()
        self.web = Web()
        self.web.bootstrap = AsyncMock(return_value=SimpleNamespace(actor_id='123456',
            fb_dtsg='synthetic-private-csrf',lsd='synthetic-private-lsd',jazoest='2000',request_context={}))
        self.web._next_graphql_req = lambda:'1'
        http = AdsManagerPrivateHttp(self.web,context_from_document(document(),account_id='111111',business_id='222222',actor_id='123456'))
        self.client = SimpleNamespace(web=self.web,http=http,account='111111',business='222222',actor='123456')
        self.account = {'ad_account_promotable_objects':{'promotable_page_ids':['555555']}}
        self.inputs = {'ad_format':'MOBILE_FEED_STANDARD','creative':{'object_story_spec':{
            'page_id':'555555','link_data':{'image_hash':'fixture-image-hash','link':'https://example.com/'}}}}
        self.web.session.response_body = b'for (;;);{"__ar":1,"payload":{"markup":{"__html":"<iframe title=preview></iframe>"}}}'
        self.patcher = patch('app.private_ads_preview.verify_image',new=AsyncMock(return_value={'verified':True}))
        self.verify = self.patcher.start();self.addCleanup(self.patcher.stop)

    async def test_source_native_post_uses_profile_pool_and_private_csrf_without_sdk(self):
        result = await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(result['body'],'<iframe title=preview></iframe>')
        self.verify.assert_awaited_once_with(self.client.http,'fixture-image-hash')
        method,url,options = self.web.session.calls[0]
        self.assertEqual(method,'POST')
        self.assertEqual(urlsplit(url).path,'/ads/lwi/ad_preview_generator/')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['ad_account_id'],['111111'])
        self.assertEqual(json.loads(query['creative_spec'][0]),self.inputs['creative'])
        self.assertNotIn('synthetic-private-csrf',url)
        self.assertEqual(options['data']['fb_dtsg'],'synthetic-private-csrf')
        self.assertFalse(options['allow_redirects'])

    async def test_unowned_page_actor_or_media_stops_before_preview_post(self):
        self.account['ad_account_promotable_objects']['promotable_page_ids'] = []
        with self.assertRaises(ProvisioningError):
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(self.web.session.calls,[])
        self.account['ad_account_promotable_objects']['promotable_page_ids'] = ['555555']
        self.web.bootstrap.return_value.actor_id = '999999'
        with self.assertRaises(ProvisioningError):
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(self.web.session.calls,[])

    async def test_http1_and_invalid_markup_never_fall_back_to_browser_or_other_transport(self):
        self.web.session.protocol = 'HTTP/1.1'
        with self.assertRaises(ProvisioningError) as error:
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(error.exception.code,'PRIVATE_ADS_DRAFT_PROTOCOL_UNAVAILABLE')
        self.assertEqual(len(self.web.session.calls),1)
        self.web.session.protocol = 'HTTP/2'
        self.web.session.response_body = b'{"__ar":1,"payload":{}}'
        with self.assertRaises(ProvisioningError):
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(len(self.web.session.calls),2)

    async def test_changed_profile_and_unknown_preview_format_stop_before_native_dispatch(self):
        self.inputs['ad_format'] = 'UNREVIEWED_FORMAT'
        with self.assertRaises(ProvisioningError):
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.inputs['ad_format'] = 'MOBILE_FEED_STANDARD'
        self.web.profile.proxy = 'http://changed.invalid:8080'
        with self.assertRaises(ProvisioningError):
            await read_preview(self.client,self.catalog,self.account,self.inputs)
        self.assertEqual(self.web.session.calls,[])
