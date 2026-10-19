import copy
import json
from contextlib import asynccontextmanager
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.private_ads_http import AdsManagerPrivateHttp, context_from_document
from app.private_ads_verify import verify_published_objects
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class Body:
    def __init__(self, payload):self.payload = payload
    async def read(self, size):
        return self.payload


class Session:
    proxy = "http://synthetic-proxy.invalid:8080"
    protocol = "HTTP/2"
    calls = []
    response_status = 200
    response_body = b'{"id":"555555"}'

    @asynccontextmanager
    async def request(self, method, url, **options):
        self.calls.append((method, url, options))
        yield SimpleNamespace(http_version=self.protocol, status=self.response_status,
                              content=Body(self.response_body))


class Web:
    def __init__(self):
        self.session = Session();self.session.calls = []
        self.profile = SimpleNamespace(proxy=self.session.proxy, cookies={"c_user": "123456"})

    async def _ensure_session(self):return self.session

    async def fetch_text(self, url, **kwargs):return 200, document(), url


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.web = Web()
        self.client = await AdsManagerPrivateHttp.open(self.web,
            account_id="111111", business_id="222222", actor_id="123456")
        self.claims = 0

    async def claim(self):self.claims += 1

    def test_literal_serverjs_initialization_with_module_hash(self):
        payload = json.loads(document())
        payload['require'] = [["WebApiApplication@reviewed-hash", "init", [],
            ["synthetic-session-value", "119211728144504"], None]]
        body = '<script>requireLazy(["ServerJS"],function(s){s.handle(' + json.dumps(payload) + ');});</script>'
        context = context_from_document(body, account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual(context.application_id, "119211728144504")
        self.assertNotIn("synthetic-session-value", repr(context))

    def test_arbitrary_javascript_wrapper_cannot_initialize_credentials(self):
        body = '<script>untrusted.handle(' + document() + ');</script>'
        with self.assertRaises(ProvisioningError):
            context_from_document(body, account_id="111111", business_id="222222", actor_id="123456")

    async def test_missing_initializer_renews_once_then_proves_exact_account(self):
        payload = json.loads(document());payload['require'] = []
        payload['define'].extend([
            ['DTSGInitialData',[],{'token':'synthetic-fresh-csrf'}],
            ['LSD',[],{'token':'synthetic-fresh-lsd'}],
            ['SprinkleConfig',[],{'param_name':'jazoest','version':2,'should_randomize':False}]])
        async def missing(url, **kwargs):return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap():return SimpleNamespace(actor_id='123456',fb_dtsg='synthetic-csrf',lsd='synthetic-lsd')
        self.web.bootstrap = bootstrap
        self.web._extract_request_context = lambda body: {'__rev':'12345','__hs':'synthetic-page-revision'}
        calls = []
        @asynccontextmanager
        async def request(method, url, **options):
            calls.append((method,url,options))
            result = ({'payload':{'access_token':'synthetic-renewed-session','client_id':'119211728144504'}}
                if method == 'POST' else {'id':'act_111111','business':{'id':'222222'}})
            raw = ('for (;;);'+json.dumps(result)).encode() if method == 'POST' else json.dumps(result).encode()
            yield SimpleNamespace(http_version='HTTP/2',status=200,content=Body(raw))
        self.web.session.request = request
        client = await AdsManagerPrivateHttp.open(self.web,
            account_id='111111',business_id='222222',actor_id='123456')
        self.assertEqual([row[0] for row in calls], ['POST','GET'])
        self.assertTrue(calls[0][1].endswith('/ads/manager/ajax/renew_access_token/'))
        self.assertEqual(calls[0][2]['data']['client_id'],'119211728144504')
        self.assertEqual(calls[0][2]['data']['fb_dtsg'],'synthetic-fresh-csrf')
        self.assertEqual(calls[0][2]['data']['lsd'],'synthetic-fresh-lsd')
        self.assertEqual(calls[0][2]['data']['jazoest'],'2'+str(sum(map(ord,'synthetic-fresh-csrf'))))
        self.assertEqual(calls[0][2]['data']['__rev'],'12345')
        self.assertTrue(calls[1][1].endswith('/act_111111'))
        self.assertFalse(client.read_only)
        self.assertNotIn('synthetic-renewed-session',repr(client.context))

    async def test_session_renewal_never_retries_meta_rejection_or_accepts_wrong_client(self):
        payload = json.loads(document());payload['require'] = []
        async def missing(url, **kwargs):return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap():return SimpleNamespace(actor_id='123456',fb_dtsg='synthetic-csrf')
        self.web.bootstrap = bootstrap
        for result in ({'error':1357001}, {'payload':{'client_id':'999999','access_token':'synthetic-renewed-session'}},
                       {'payload':{'client_id':'119211728144504','access_token':''}}):
            calls = []
            @asynccontextmanager
            async def request(method,url,**options):
                calls.append(method)
                yield SimpleNamespace(http_version='HTTP/2',status=200,content=Body(json.dumps(result).encode()))
            self.web.session.request = request
            with self.subTest(result=result), self.assertRaises(ProvisioningError):
                await AdsManagerPrivateHttp.open(self.web,
                    account_id='111111',business_id='222222',actor_id='123456')
            self.assertEqual(calls,['POST'])

    async def test_session_renewal_rejects_wrong_bootstrap_actor_before_post(self):
        payload = json.loads(document());payload['require'] = []
        async def missing(url, **kwargs):return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap():return SimpleNamespace(actor_id='999999',fb_dtsg='synthetic-csrf')
        self.web.bootstrap = bootstrap
        with self.assertRaises(ProvisioningError):
            await AdsManagerPrivateHttp.open(self.web,
                account_id='111111',business_id='222222',actor_id='123456')
        self.assertEqual(self.web.session.calls,[])

    async def test_post_claim_and_captured_encoding_on_profile_http2(self):
        result = await self.client.post("/act_111111/addrafts", {"enabled": False, "values": [1]}, before_submit=self.claim)
        self.assertEqual(self.claims, 1)
        method, url, options = self.web.session.calls[0]
        self.assertEqual(url, "https://adsmanager-graph.facebook.com/v22.0/act_111111/addrafts")
        self.assertNotIn("synthetic-session-value", url)
        self.assertEqual(options["data"]["enabled"], "false")
        self.assertEqual(options["data"]["values"], "[1]")
        self.assertFalse(options["allow_redirects"])
        self.assertEqual(result["id"], "555555")

    async def test_missing_proxy_fails_before_claim(self):
        self.web.profile.proxy = None
        with self.assertRaises(ProvisioningError):
            await self.client.post("/act_111111/addrafts", {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0);self.assertEqual(self.web.session.calls, [])

    async def test_changed_proxy_fails_before_claim(self):
        self.web.profile.proxy = "http://other.invalid:8080"
        with self.assertRaises(ProvisioningError):
            await self.client.post("/act_111111/addrafts", {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)

    def direct_fixture(self):
        self.web.profile.name = '21'
        self.web.profile.proxy = None
        self.web.session.proxy = None
        self.web.session.direct_test_until = 1600
        with patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE':'21','REMASK_DIRECT_TEST_UNTIL':'1600'}), \
                patch('app.direct_test.time.time',return_value=1000):
            self.client = AdsManagerPrivateHttp(self.web, self.client.context)

    async def test_explicit_profile_21_direct_lease_allows_http2_reads_and_claimed_post(self):
        self.direct_fixture()
        with patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '21', 'REMASK_DIRECT_TEST_UNTIL': '1600'}), \
                patch('app.direct_test.time.time', return_value=1000):
            self.client = AdsManagerPrivateHttp(self.web, self.client.context)
            await self.client.get('/act_111111', {'fields': ['id']})
            await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual([row[0] for row in self.web.session.calls], ['GET', 'POST'])
        self.assertEqual(self.claims, 1)
        self.assertTrue(all(row[2]['allow_redirects'] is False for row in self.web.session.calls))

    async def test_direct_lease_cannot_grant_another_profile_advertising_post(self):
        self.direct_fixture()
        self.web.profile.name = '22'
        with patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '21', 'REMASK_DIRECT_TEST_UNTIL': '1600'}), \
                patch('app.direct_test.time.time', return_value=1000), self.assertRaises(ProvisioningError):
            await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.web.session.calls, [])

    async def test_expired_or_changed_direct_lease_stops_before_claim(self):
        self.direct_fixture()
        for clock, until in ((1601, '1600'), (1000, '1601')):
            with self.subTest(clock=clock, until=until), \
                    patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '21', 'REMASK_DIRECT_TEST_UNTIL': until}), \
                    patch('app.direct_test.time.time', return_value=clock), self.assertRaises(ProvisioningError):
                await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.web.session.calls, [])

    async def test_session_without_a_matching_direct_lease_stops_before_claim(self):
        self.direct_fixture()
        self.web.session.direct_test_until = 0
        with patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '21', 'REMASK_DIRECT_TEST_UNTIL': '1600'}), \
                patch('app.direct_test.time.time', return_value=1000), self.assertRaises(ProvisioningError):
            await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.web.session.calls, [])

    async def test_changed_actor_foreign_account_and_unsafe_path_fail_before_claim(self):
        for path in ("/act_999999/addrafts", "/act_111111/addrafts?access_token=wrong", "https://other.invalid/path"):
            with self.subTest(path=path), self.assertRaises(ProvisioningError):
                await self.client.post(path, {}, before_submit=self.claim)
        self.web.profile.cookies["c_user"] = "999999"
        with self.assertRaises(ProvisioningError):
            await self.client.post("/act_111111/addrafts", {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)

    async def test_auth_material_cannot_be_supplied_in_user_parameters(self):
        with self.assertRaises(ProvisioningError):
            await self.client.post("/act_111111/addrafts", {"access_token": "other"}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)

    async def test_http1_cannot_be_accepted(self):
        self.web.session.protocol = "HTTP/1.1"
        with self.assertRaises(ProvisioningError):
            await self.client.get("/act_111111", {"fields": ["id"]})

    async def test_explicit_meta_error_preserves_typed_reason_without_secret_message(self):
        self.web.session.response_body = json.dumps({'error':{
            'code':100, 'error_subcode':1815753, 'type':'OAuthException', 'is_transient':False,
            'message':'synthetic-session-value', 'error_user_msg':'synthetic-private-content',
            'fbtrace_id':'synthetic-session-value'}}).encode()
        for status in (200, 400):
            self.web.session.response_status = status
            with self.subTest(status=status), self.assertRaises(ProvisioningError) as caught:
                await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
            error = caught.exception
            self.assertEqual(error.code, 'PRIVATE_ADS_DRAFT_REQUEST_REJECTED')
            self.assertEqual(error.diagnostic, {'http_status':status, 'code':100,
                'error_subcode':1815753, 'type':'OAuthException', 'is_transient':False})
            self.assertIn('code=100', str(error))
            self.assertNotIn('synthetic-', str(error))

    async def test_http_rejection_without_json_still_preserves_received_status(self):
        self.web.session.response_status = 403
        self.web.session.response_body = b'<html>synthetic-private-content</html>'
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.get('/act_111111', {})
        self.assertEqual(caught.exception.diagnostic, {'http_status':403})
        self.assertNotIn('synthetic-', str(caught.exception))

    async def test_malformed_error_metadata_cannot_enter_diagnostics(self):
        self.web.session.response_body = json.dumps({'error':{'code':True,
            'error_subcode':'synthetic-session-value', 'type':{'access_token':'synthetic-session-value'},
            'is_transient':'yes'}}).encode()
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.get('/act_111111', {})
        self.assertEqual(caught.exception.diagnostic, {'http_status':200})

    async def test_foreign_bootstrap_url_is_rejected(self):
        async def foreign(url, **kwargs):return 200, document(), url.replace("act=111111", "act=999999")
        self.web.fetch_text = foreign
        with self.assertRaises(ProvisioningError):
            await AdsManagerPrivateHttp.open(self.web, account_id="111111", business_id="222222", actor_id="123456")

    async def test_default_account_redirect_requires_independent_exact_account_read(self):
        async def redirected(url, **kwargs):
            return 200, document(), url.replace("act=111111", "act=999999")
        self.web.fetch_text = redirected
        self.web.session.response_body = json.dumps({"id":"act_111111", "business":{"id":"222222"}}).encode()
        client = await AdsManagerPrivateHttp.open(self.web,
            account_id="111111", business_id="222222", actor_id="123456")
        self.assertFalse(client.read_only)
        self.assertEqual(len(self.web.session.calls), 1)
        method, url, options = self.web.session.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/act_111111"))
        self.assertEqual(json.loads(options["params"]["fields"]), ["id", "account_id", "business"])

    async def test_native_account_id_proves_distinct_graph_object_identity(self):
        async def redirected(url, **kwargs):
            return 200, document(), url.replace("act=111111", "")
        self.web.fetch_text = redirected
        for native, business, accepted in [("111111","222222",True),
                (111111,"222222",True), ("999999","222222",False),
                ("111111","999999",False), (True,"222222",False),
                (111111.0,"222222",False)]:
            self.web.session.calls = []
            self.web.session.response_body = json.dumps({"id":"4590117541236414",
                "account_id":native,"business":{"id":business}}).encode()
            with self.subTest(native=native,business=business):
                if accepted:
                    client = await AdsManagerPrivateHttp.open(self.web,
                        account_id="111111", business_id="222222", actor_id="123456")
                    self.assertFalse(client.read_only)
                else:
                    with self.assertRaises(ProvisioningError):
                        await AdsManagerPrivateHttp.open(self.web,
                            account_id="111111", business_id="222222", actor_id="123456")
            self.assertEqual([call[0] for call in self.web.session.calls], ["GET"])

    async def test_redirect_proof_accepts_only_lossless_numeric_id(self):
        requested = "120250985990010713"
        async def redirected(url, **kwargs):
            return 200, document(), url.replace("act=" + requested, "")
        self.web.fetch_text = redirected
        for value in (requested, "act_" + requested, int(requested), float(requested), True, 999999):
            self.web.session.calls = []
            self.web.session.response_body = json.dumps({"id":value,"business":{"id":"222222"}}).encode()
            with self.subTest(value=value):
                if type(value) in (str, int) and str(value).removeprefix("act_") == requested:
                    client = await AdsManagerPrivateHttp.open(self.web,
                        account_id=requested, business_id="222222", actor_id="123456")
                    self.assertFalse(client.read_only)
                else:
                    with self.assertRaises(ProvisioningError):
                        await AdsManagerPrivateHttp.open(self.web,
                            account_id=requested, business_id="222222", actor_id="123456")
            self.assertEqual([call[0] for call in self.web.session.calls], ["GET"])

    async def test_redirect_account_proof_rejects_other_business_or_account(self):
        async def redirected(url, **kwargs):
            return 200, document(), url.replace("act=111111", "")
        self.web.fetch_text = redirected
        for row in ({"id":"act_111111","business":{"id":"999999"}},
                    {"id":"act_999999","business":{"id":"222222"}},
                    {"id":"act_111111"}):
            self.web.session.calls = []
            self.web.session.response_body = json.dumps(row).encode()
            with self.subTest(row=row), self.assertRaises(ProvisioningError):
                await AdsManagerPrivateHttp.open(self.web,
                    account_id="111111", business_id="222222", actor_id="123456")
            self.assertEqual([call[0] for call in self.web.session.calls], ["GET"])


    async def test_changed_non_actor_cookie_requires_fresh_document_before_claim(self):
        self.web.profile.cookies['xs'] = 'new-synthetic-cookie'
        with self.assertRaises(ProvisioningError):
            await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.web.session.calls, [])

    async def test_profile_change_during_claim_cannot_dispatch(self):
        async def change_profile():
            self.claims += 1
            self.web.profile.cookies['xs'] = 'new-synthetic-cookie'
        with self.assertRaises(ProvisioningError):
            await self.client.post('/act_111111/addrafts', {}, before_submit=change_profile)
        self.assertEqual(self.claims, 1)
        self.assertEqual(self.web.session.calls, [])

    async def test_video_start_uses_formdata_and_transfer_uses_waterfall_header(self):
        waterfall = 'a' * 32
        await self.client.post('/act_111111/advideos', {'upload_phase': 'start', 'waterfall_id': waterfall},
                               before_submit=self.claim, multipart=True)
        self.assertEqual(self.web.session.calls[-1][2]['files']['upload_phase'], (None, 'start'))
        await self.client.post('/act_111111/advideos', {'upload_phase': 'transfer', 'waterfall_id': waterfall},
                               before_submit=self.claim, files={'video_file_chunk': ('chunk', b'synthetic', 'video/mp4')})
        self.assertEqual(self.web.session.calls[-1][2]['headers']['X_FB_VIDEO_WATERFALL_ID'], waterfall)

    async def test_read_only_publication_session_cannot_claim_any_post(self):
        self.client.read_only = True
        with self.assertRaises(ProvisioningError):
            await self.client.post('/act_111111/addrafts', {}, before_submit=self.claim)
        self.assertEqual(self.claims, 0)
        self.assertEqual(self.web.session.calls, [])


class PublishedReads(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.objects = {"campaign": "777771", "ad_set": "777772", "ad": "777773"}
        story = {"page_id": "555555", "link_data": {"image_hash": "image-fixture", "link": "https://example.com", "message": "text"}}
        self.levels = {"campaign": {"name": "campaign", "status": "PAUSED", "daily_budget": 2000},
            "ad_set": {"name": "adset", "status": "PAUSED", "targeting": {"geo_locations": {"countries": ["UA"]}}},
            "ad": {"name": "ad", "status": "PAUSED", "creative": {"name": "creative", "object_story_spec": story}}}
        self.rows = {
            "777771": {"id": "777771", "account_id": "111111", "name": "campaign", "status": "PAUSED", "daily_budget": "2000"},
            "777772": {"id": "777772", "account_id": "111111", "name": "adset", "status": "PAUSED", "campaign_id": "777771", "targeting": self.levels["ad_set"]["targeting"]},
            "777773": {"id": "777773", "account_id": "111111", "name": "ad", "status": "PAUSED", "campaign_id": "777771", "adset_id": "777772", "creative": {"id": "777774"}},
            "777774": {"id": "777774", "account_id": "111111", "name": "creative", "object_story_spec": copy.deepcopy(story)}}
        self.client = SimpleNamespace(context=SimpleNamespace(account_id="111111"), get=self.get)
        self.calls = []

    async def get(self, path, params):
        self.calls.append(path)
        return copy.deepcopy(self.rows[path.removeprefix("/")])

    async def test_four_independent_object_reads_confirm_saved_settings(self):
        result = await verify_published_objects(self.client, self.objects, self.levels)
        self.assertTrue(result["verified"]);self.assertEqual(len(self.calls), 4)
        self.assertEqual(result["entities"]["creative_id"], "777774")

    async def test_foreign_account_wrong_parent_active_status_or_budget_reject(self):
        original = copy.deepcopy(self.rows)
        for key, field, value in (("777771", "account_id", "999999"), ("777772", "campaign_id", "999999"),
                                  ("777773", "status", "ACTIVE"), ("777771", "daily_budget", "2001")):
            self.rows = copy.deepcopy(original);self.rows[key][field] = value
            with self.subTest(field=field), self.assertRaises(ProvisioningError):
                await verify_published_objects(self.client, self.objects, self.levels)

    async def test_wrong_page_or_image_cannot_confirm_creative(self):
        for field, value in (("page_id", "999999"), ("link_data", {"image_hash": "another"})):
            self.rows["777774"]["object_story_spec"][field] = value
            with self.subTest(field=field), self.assertRaises(ProvisioningError):
                await verify_published_objects(self.client, self.objects, self.levels)
