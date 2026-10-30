import copy
import httpx
import ast
import json
from contextlib import asynccontextmanager
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_http import AdsManagerPrivateHttp, context_from_document
from app.private_ads_verify import verify_published_objects
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class Body:
    def __init__(self, payload):self.payload = payload
    async def read(self, size):
        return self.payload


class LaunchTransportIsolationTests(unittest.TestCase):
    def test_launch_import_graph_does_not_load_browser_automation(self):
        completed = subprocess.run([sys.executable, '-W', 'ignore', '-c',
            'import sys; import app.private_launch; import app.private_ads_launch; '
            'assert "app.facebook_business_browser" not in sys.modules'],
            cwd=Path(__file__).parents[1], env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'},
            capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_private_launch_transport_modules_have_no_browser_automation_imports(self):
        root = Path(__file__).parents[1] / 'app'
        modules = ('private_ads_launch.py', 'private_ads_http.py', 'private_ads_document.py',
            'private_launch_read.py', 'private_ads_draft.py',
            'private_ads_state.py', 'private_ads_publication.py', 'private_launch_media.py',
            'private_ads_media.py', 'private_ads_video.py', 'private_ads_creative.py',
            'media_preflight.py')
        for name in modules:
            source = (root / name).read_text()
            tree = ast.parse(source, filename=name)
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or '')
            with self.subTest(module=name):
                self.assertFalse(any('facebook_business_browser' in item or 'playwright' in item.casefold()
                                     for item in imported), imported)
                self.assertNotIn('FacebookBusinessBrowser', source)


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
        self.fetch_calls = []
        self.profile = SimpleNamespace(proxy=self.session.proxy, cookies={"c_user": "123456"})

    async def _ensure_session(self):return self.session

    async def fetch_text(self, url, **kwargs):
        self.fetch_calls.append((url, kwargs))
        return 200, document(), url


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.web = Web()
        self.client = await AdsManagerPrivateHttp.open(self.web,
            account_id="111111", business_id="222222", actor_id="123456")
        self.claims = 0

    async def claim(self):self.claims += 1

    async def test_ads_manager_bootstrap_opts_into_exact_document_timeout_retry(self):
        self.assertEqual(len(self.web.fetch_calls), 1)
        url, kwargs = self.web.fetch_calls[0]
        self.assertEqual(url,
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222")
        self.assertEqual(kwargs, {
            "max_bytes": 5_000_001,
            "document_navigation": True,
            "retry_timeout": True,
        })

    async def test_ads_manager_timeout_falls_back_to_exact_business_document(self):
        from fb_worker import RemoteRequestError
        web = Web()
        calls = []

        async def fetch(url, **kwargs):
            calls.append((url, kwargs))
            if len(calls) == 1:
                raise RemoteRequestError("Facebook fetch timeout",
                    meta_payload={"transport_failure": {"kind": "TimeoutError"}},
                    request_may_have_been_sent=False, transport_stage="fetch")
            return 200, document(), url

        web.fetch_text = fetch
        client = await AdsManagerPrivateHttp.open(web,
            account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual([row[0] for row in calls], [
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222",
            "https://business.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222"])
        self.assertEqual(calls[1][1], {
            "max_bytes": 5_000_001, "document_navigation": True, "retry_timeout": False})
        self.assertEqual(client.document_origin, "https://business.facebook.com")

    async def test_ads_manager_non_timeout_does_not_switch_document_host(self):
        from fb_worker import RemoteRequestError
        web = Web()
        calls = []

        async def fetch(url, **kwargs):
            calls.append(url)
            raise RemoteRequestError("Facebook fetch network failure",
                meta_payload={"transport_failure": {"kind": "ReadError"}},
                request_may_have_been_sent=False, transport_stage="fetch")

        web.fetch_text = fetch
        with self.assertRaises(RemoteRequestError):
            await AdsManagerPrivateHttp.open(web,
                account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual(calls, [
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222"])

    async def test_interrupted_account_read_retries_once_on_same_session(self):
        calls = []
        @asynccontextmanager
        async def request(method, url, **options):
            calls.append((method, url, options))
            if len(calls) == 1:
                raise httpx.ReadError('synthetic interrupted read')
            yield SimpleNamespace(http_version='HTTP/2',status=200,content=Body(b'{"data":[]}'))
        self.web.session.request = request
        self.assertEqual(await self.client.get('/act_111111/campaigns', {'fields':['id']}), {'data':[]})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(self.claims, 0)

    async def test_timed_out_advertising_post_is_not_replayed_and_claims_once(self):
        calls = []
        @asynccontextmanager
        async def request(method, url, **options):
            calls.append(method)
            raise TimeoutError('synthetic response timeout')
            yield
        self.web.session.request = request
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.post('/act_111111/addrafts', {'name':'fixture'}, before_submit=self.claim)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_HTTP_UNAVAILABLE')
        self.assertEqual((calls, self.claims), (['POST'], 1))

    async def test_interrupted_advertising_post_is_not_replayed_and_claims_once(self):
        calls = []
        @asynccontextmanager
        async def request(method, url, **options):
            calls.append(method)
            raise httpx.ReadError('synthetic interrupted reply')
            yield
        self.web.session.request = request
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.post('/act_111111/addrafts', {'name':'fixture'}, before_submit=self.claim)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_HTTP_UNAVAILABLE')
        self.assertEqual((calls, self.claims), (['POST'], 1))

    async def test_changed_profile_blocks_the_retry_before_network_dispatch(self):
        calls = []
        @asynccontextmanager
        async def request(method, url, **options):
            calls.append(method)
            self.web.profile.cookies['c_user'] = '999999'
            raise httpx.ReadError('synthetic interrupted read')
            yield
        self.web.session.request = request
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.get('/act_111111/campaigns', {'fields':['id']})
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_PROFILE_CONTEXT_UNAVAILABLE')
        self.assertEqual(calls, ['GET'])

    def test_literal_serverjs_initialization_with_module_hash(self):
        payload = json.loads(document())
        payload['require'] = [["WebApiApplication@reviewed-hash", "init", [],
            ["synthetic-session-value", "119211728144504"], None]]
        body = '<script>requireLazy(["ServerJS"],function(s){s.handle(' + json.dumps(payload) + ');});</script>'
        context = context_from_document(body, account_id="111111", business_id="222222", actor_id="123456")
        self.assertEqual(context.application_id, "119211728144504")
        self.assertNotIn("synthetic-session-value", repr(context))

    def test_strict_http_decoder_accepts_bytes_and_rejects_ambiguous_bytes(self):
        from app.private_ads_http import _strict_response_json
        self.assertEqual(_strict_response_json(b'{"account_id":"111111"}'),
                         {'account_id': '111111'})
        for body in (b'{"id":"111111","id":"999999"}',
                     b'{"size":1e999}', b'{"size":NaN}'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                _strict_response_json(body)

    def test_bootstrap_json_rejects_ambiguous_fields_in_json_and_serverjs(self):
        from app.private_ads_document import _json_payloads
        from app.private_ads_http import _ads_document_payloads
        ambiguous = '{"define":[],"define":[["CurrentUserInitialData",[],{"USER_ID":"999999"}]]}'
        self.assertEqual(_json_payloads(ambiguous), [])
        server_js = '<script>requireLazy(["ServerJS"],function(s){s.handle(' + ambiguous + ');});</script>'
        self.assertEqual(_ads_document_payloads(server_js), [])
        for invalid in ('{"data":NaN}', '{"data":Infinity}', '{"data":-Infinity}',
                        '{"data":1e999}', '{"data":-1e999}'):
            with self.subTest(invalid=invalid):
                self.assertEqual(_json_payloads(invalid), [])

    def test_nested_encoded_bootstrap_json_with_duplicate_fields_is_not_interpreted(self):
        from app.private_ads_document import _json_payloads
        embedded = '{"id":"111111","id":"999999"}'
        rows = _json_payloads(json.dumps({'data': embedded}))
        self.assertEqual(rows, [{'data': embedded}])

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
        bootstrap_options = []
        async def bootstrap(**kwargs):
            bootstrap_options.append(kwargs)
            return SimpleNamespace(actor_id='123456',
                fb_dtsg='synthetic-csrf',lsd='synthetic-lsd',
            request_context={'__aaid':'0','__bid':'synthetic-bid','__hblp':'synthetic-hblp',
                '__hsdp':'synthetic-hsdp','__jssesw':'1','__crn':'synthetic-crn'})
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
        self.assertEqual(bootstrap_options, [{}])
        self.assertEqual([row[0] for row in calls], ['POST','GET'])
        self.assertTrue(calls[0][1].endswith('/ads/manager/ajax/renew_access_token/'))
        self.assertEqual(calls[0][2]['data']['client_id'],'119211728144504')
        self.assertEqual(calls[0][2]['data']['fb_dtsg'],'synthetic-fresh-csrf')
        self.assertEqual(calls[0][2]['data']['lsd'],'synthetic-fresh-lsd')
        self.assertEqual(calls[0][2]['data']['jazoest'],'2'+str(sum(map(ord,'synthetic-fresh-csrf'))))
        self.assertEqual(calls[0][2]['data']['__rev'],'12345')
        for name,value in {'__aaid':'0','__bid':'synthetic-bid','__hblp':'synthetic-hblp',
            '__hsdp':'synthetic-hsdp','__jssesw':'1','__crn':'synthetic-crn'}.items():
            self.assertEqual(calls[0][2]['data'][name],value)
        self.assertEqual(calls[0][2]["headers"]["Referer"], "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222")
        self.assertTrue(calls[1][1].endswith('/act_111111'))
        self.assertFalse(client.read_only)
        self.assertNotIn('synthetic-renewed-session',repr(client.context))

    async def test_session_renewal_never_retries_meta_rejection_or_accepts_wrong_client(self):
        payload = json.loads(document());payload['require'] = []
        async def missing(url, **kwargs):return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap(**kwargs):return SimpleNamespace(actor_id='123456',fb_dtsg='synthetic-csrf')
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

    async def test_session_renewal_rejects_duplicate_tokens_and_empty_error_envelopes(self):
        payload = json.loads(document()); payload['require'] = []
        async def missing(url, **kwargs):
            return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap(**kwargs):
            return SimpleNamespace(actor_id='123456',  fb_dtsg='synthetic-csrf')
        self.web.bootstrap = bootstrap
        normal = {'payload': {'access_token': 'synthetic-renewed-session',
                              'client_id': '119211728144504'}}
        duplicated = ('{"payload":{"access_token":"synthetic-renewed-session",'
                      '"access_token":"other-session","client_id":"119211728144504"}}')
        errors = json.dumps({**normal, 'errors': []})
        for raw in (duplicated, errors):
            calls = []
            @asynccontextmanager
            async def request(method, url, **options):
                calls.append(method)
                yield SimpleNamespace(http_version='HTTP/2', status=200,
                                      content=Body(raw.encode()))
            self.web.session.request = request
            with self.subTest(raw=raw), self.assertRaises(ProvisioningError):
                await AdsManagerPrivateHttp.open(self.web,
                    account_id='111111', business_id='222222', actor_id='123456')
            self.assertEqual(calls, ['POST'])

    async def test_session_renewal_rejects_wrong_bootstrap_actor_before_post(self):
        payload = json.loads(document());payload['require'] = []
        async def missing(url, **kwargs):return 200, json.dumps(payload), url
        self.web.fetch_text = missing
        async def bootstrap(**kwargs):return SimpleNamespace(actor_id='999999',fb_dtsg='synthetic-csrf')
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

    async def test_http_429_is_classified_without_replaying_or_leaking_response(self):
        self.web.session.response_status = 429
        self.web.session.response_body = json.dumps({"error": {
            "code": 4, "type": "OAuthException", "message": "synthetic-private-secret",
            "fbtrace_id": "synthetic-private-trace"}}).encode()
        for method in ("GET", "POST"):
            with self.subTest(method=method), self.assertRaises(ProvisioningError) as caught:
                if method == "GET":
                    await self.client.get("/act_111111", {"fields": ["id"]})
                else:
                    await self.client.post("/act_111111/addrafts", {}, before_submit=self.claim)
            error = caught.exception
            self.assertEqual(error.code, "PRIVATE_ADS_DRAFT_RATE_LIMITED")
            self.assertTrue(error.retryable)
            self.assertEqual(error.diagnostic, {"http_status": 429,
                "code": 4, "type": "OAuthException"})
            self.assertNotIn("synthetic-private", str(error))
        self.assertEqual(self.claims, 1)
        self.assertEqual([row[0] for row in self.web.session.calls], ["GET", "POST"])

    async def test_empty_error_envelopes_and_unfinished_responses_fail_closed(self):
        responses = [
            ({"id":"act_111111", "error":{}}, "PRIVATE_ADS_DRAFT_REQUEST_REJECTED"),
            ({"id":"act_111111", "errors":[]}, "PRIVATE_ADS_DRAFT_REQUEST_REJECTED"),
            ({"id":"act_111111", "data":[], "hasNext":True}, "PRIVATE_ADS_DRAFT_RESPONSE_INCOMPLETE"),
            ({"id":"act_111111", "extensions":{"is_final":False}},
             "PRIVATE_ADS_DRAFT_RESPONSE_INCOMPLETE"),
        ]
        for value, code in responses:
            with self.subTest(value=value):
                self.web.session.response_body = json.dumps(value).encode()
                with self.assertRaises(ProvisioningError) as caught:
                    await self.client.get("/act_111111", {"fields":["id"]})
                self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.claims, 0)
        self.assertEqual(len(self.web.session.calls), len(responses))

    async def test_ambiguous_or_nonfinite_json_reply_is_never_accepted_as_account_proof(self):
        bodies = [
            b'{"id":"act_111111","account_id":"999999","account_id":"111111"}',
            b'{"id":"act_111111","nested":{"state":1,"state":2}}',
            b'{"id":"act_111111","estimate":NaN}',
            b'{"id":"act_111111","estimate":Infinity}',
            b'{"id":"act_111111","estimate":1e999}',
            b'{"id":"act_111111","estimate":-1e999}',
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.web.session.response_body = body
                with self.assertRaises(ProvisioningError) as caught:
                    await self.client.get("/act_111111", {"fields": ["id", "account_id"]})
                self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_RESPONSE_INVALID")
                self.assertNotIn(body.decode(), str(caught.exception))
        self.assertEqual(self.claims, 0)
        self.assertEqual(len(self.web.session.calls), len(bodies))

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

    async def test_permission_hint_is_an_allowlisted_label_without_response_text(self):
        self.web.session.response_body = json.dumps({'error':{'code':10, 'type':'OAuthException',
            'message':'Application does not have permission for this action synthetic-private-value'}}).encode()
        with self.assertRaises(ProvisioningError) as caught:
            await self.client.get('/act_111111', {})
        self.assertEqual(caught.exception.diagnostic['permission_hint'], 'application_permission')
        self.assertNotIn('synthetic-private-value', str(caught.exception))

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

    def alias_fixture(self, *, native='999999', business='222222'):
        from tests.test_private_inventory_queries import connected_response
        payload = connected_response(business=business, accounts=['111111'])
        asset = payload['data']['business']['connected_objects']['edges'][0]['nameColumn']['bizkit_settings_render_strategy_no_business_id']['business_object']
        asset['business_object_ui_id'] = native
        self.web.graphql = AsyncMock(return_value=payload)
        self.web.session.response_body = json.dumps({'id':'999999', 'account_id':'999999',
            'account_status':1, 'business':{'id':'222222'}}).encode()
        return payload

    async def test_redirect_alias_requires_fresh_exact_settings_pair(self):
        self.alias_fixture()
        async def redirected(url, **kwargs):
            return 200, document(), url.replace('act=111111', 'act=999999')
        self.web.fetch_text = redirected
        client = await AdsManagerPrivateHttp.open(self.web,
            account_id='111111', business_id='222222', actor_id='123456')
        self.assertFalse(client.read_only)
        self.assertEqual(client.context.account_id, '111111')
        self.assertEqual(client._native_account_id, '999999')
        self.web.graphql.assert_awaited_once()
        self.assertEqual(self.web.graphql.call_args.kwargs['business_context_id'], '222222')
        self.assertTrue(all(call[0] == 'GET' for call in self.web.session.calls))

    async def test_exact_url_account_read_resolves_alias_for_resource_guards(self):
        self.alias_fixture()
        from app.private_ads_catalog import AdsManagerRouteCatalog
        from app.private_ads_reads import _ReadClient
        reader = _ReadClient(self.web, AdsManagerRouteCatalog(),
            {'ad_account_id':'111111', 'business_id':'222222'})
        row = await reader.account_record()
        self.assertEqual(row['id'], '111111')
        self.assertEqual(row['account_id'], '111111')
        self.assertEqual(reader.http._native_account_id, '999999')
        self.assertEqual(self.web.graphql.await_count, 1)
        await reader.account_record()
        self.assertEqual(self.web.graphql.await_count, 1)

    async def test_alias_never_uses_generic_ui_ids_foreign_business_or_conflicts(self):
        for case in ('missing', 'wrong_native', 'foreign_business', 'conflict', 'duplicate_conflict', 'errors'):
            payload = self.alias_fixture()
            edge = payload['data']['business']['connected_objects']['edges'][0]
            asset = edge['nameColumn']['bizkit_settings_render_strategy_no_business_id']['business_object']
            if case == 'missing':
                asset.pop('business_object_ui_id'); edge['node']['id'] = '999999'
            elif case == 'wrong_native': asset['business_object_ui_id'] = '888888'
            elif case == 'foreign_business': payload['data']['business']['id'] = '333333'
            elif case == 'conflict': edge['node']['business_object_ui_id'] = '888888'
            elif case == 'duplicate_conflict':
                other = copy.deepcopy(edge)
                other['nameColumn']['bizkit_settings_render_strategy_no_business_id']['business_object']['business_object_ui_id'] = '888888'
                payload['data']['business']['connected_objects']['edges'].append(other)
            else: payload['errors'] = [{'message':'incomplete'}]
            async def redirected(url, **kwargs):
                return 200, document(), url.replace('act=111111', 'act=999999')
            self.web.fetch_text = redirected
            with self.subTest(case=case), self.assertRaises(ProvisioningError) as caught:
                await AdsManagerPrivateHttp.open(self.web,
                    account_id='111111', business_id='222222', actor_id='123456')
            self.assertIn('SCOPE_UNVERIFIED', caught.exception.code)
        self.assertEqual(self.claims, 0)

    async def test_proven_alias_maps_account_fields_without_changing_object_ids(self):
        self.alias_fixture()
        await self.client.get('/act_111111', {'fields':['id','account_id','business']})
        self.web.session.response_body = json.dumps({'data':[
            {'id':'999999', 'account_id':'act_999999', 'campaign_id':'999999'},
            {'id':'444444', 'account_id':'888888'}]}).encode()
        result = await self.client.get('/act_111111/campaigns', {'fields':['id','account_id']})
        self.assertEqual(result['data'][0], {'id':'999999', 'account_id':'111111', 'campaign_id':'999999'})
        self.assertEqual(result['data'][1]['account_id'], '888888')
        from app.private_ads_publication import AdsManagerDraftPublisher
        AdsManagerDraftPublisher._account(result['data'][0], '111111')
        with self.assertRaises(ProvisioningError):
            AdsManagerDraftPublisher._account(result['data'][1], '111111')
        with self.assertRaises(ProvisioningError):
            await self.client.get('/act_888888/campaigns', {})
        self.web.session.response_body = json.dumps({'id':'999999', 'account_id':'888888',
            'business':{'id':'222222'}}).encode()
        with self.assertRaises(ProvisioningError):
            await self.client.get('/act_111111', {'fields':['id','account_id','business']})
        self.assertEqual(self.claims, 0)

    async def test_proven_alias_uses_native_wire_edges_without_changing_journal_or_object_ids(self):
        self.alias_fixture()
        await self.client.get('/act_111111', {'fields':['id','account_id','business']})
        params = {'ad_account_id':'111111', 'fields':['id','is_connected_to_ad_account.ad_account_id(111111)'],
            'creative_spec':{'object_story_id':'111111', 'page_id':'111111'}}
        await self.client.get('/act_111111/instagram_accounts', params)
        _, url, options = self.web.session.calls[-1]
        self.assertTrue(url.endswith('/act_999999/instagram_accounts'))
        self.assertEqual(options['params']['ad_account_id'], '999999')
        self.assertEqual(json.loads(options['params']['fields']), ['id','is_connected_to_ad_account.ad_account_id(999999)'])
        self.assertEqual(json.loads(options['params']['creative_spec']), {'object_story_id':'111111','page_id':'111111'})
        self.assertEqual(params['ad_account_id'], '111111')
        self.assertIn('(111111)', params['fields'][1])
        search = {'accountId':'111111', 'q':'fitness', 'whitelisted_types':['interests']}
        await self.client.get('/act_111111/targetingsearch', search)
        _, url, options = self.web.session.calls[-1]
        self.assertTrue(url.endswith('/act_999999/targetingsearch'))
        self.assertEqual(options['params']['accountId'], '999999')
        self.assertEqual(search['accountId'], '111111')
        await self.client.post('/act_111111/addrafts', {'name':'synthetic draft'}, before_submit=self.claim)
        self.assertTrue(self.web.session.calls[-1][1].endswith('/act_999999/addrafts'))
        self.assertEqual(self.client.context.account_id, '111111')
        self.assertEqual(self.claims, 1)
        _, video = self.client._account_request('/act_111111/advideos', {'target_id':'111111','video_id':'111111'})
        self.assertEqual(video, {'target_id':'999999','video_id':'111111'})
        _, other = self.client._account_request('/555555', {'target_id':'111111'})
        self.assertEqual(other, {'target_id':'111111'})

    async def test_proven_alias_preserves_the_exact_root_lookup_for_repeated_scope_reads(self):
        self.alias_fixture()
        fields = ['id','account_id','business','account_status','ad_account_promotable_objects']
        await self.client.get('/act_111111', {'fields':['id','account_id','business']})
        self.web.session.response_body = json.dumps({'id':'999999','account_id':'999999',
            'business':{'id':'222222'},'account_status':1,
            'ad_account_promotable_objects':{'promotable_page_ids':['333333']}}).encode()
        result = await self.client.get('/act_111111', {'fields':fields})
        self.assertTrue(self.web.session.calls[-1][1].endswith('/act_111111'))
        self.assertEqual(result['id'],'111111')
        self.assertEqual(result['account_id'],'111111')
        self.assertEqual(result['ad_account_promotable_objects']['promotable_page_ids'],['333333'])
        self.assertEqual(json.loads(self.web.session.calls[-1][2]['params']['fields']),fields)
        self.assertEqual(self.web.graphql.await_count,1)
        self.assertEqual(self.claims,0)

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

 
class ReadOnlyReferenceBootstrapReuseTests(unittest.IsolatedAsyncioTestCase):
    """A warm reference search does not replay Ads Manager HTML/CDN downloads."""

    async def asyncSetUp(self):
        from app.private_ads_http import _REFERENCE_CONTEXT_CACHE
        _REFERENCE_CONTEXT_CACHE.clear()
        self.addCleanup(_REFERENCE_CONTEXT_CACHE.clear)
        self.web = Web()
        self.web.profile.name = "reference-profile-fixture"
        self.web.profile.user_agent = "reference-UA"
        self.web.fetch_text = AsyncMock(wraps=self.web.fetch_text)
        self.web.refresh_reviewed_modules = AsyncMock()

    async def open_reference(self):
        return await AdsManagerPrivateHttp.open(self.web, account_id="111111",
            business_id="222222", actor_id="123456", reference_read=True)

    async def _mock_business_gate_session(self, account_business="222222"):
        gate_url = "https://business.facebook.com/business/loginpage/"
        return_url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            "?act=111111&business_id=222222")
        gate_body = """
            <form id="handoff" action="/business/loginpage/continue" method="post">
              <input type="hidden" name="fb_dtsg" value="synthetic-gate-csrf">
              <button type="submit" name="continue" value="1">
                Продолжить с аккаунтом Facebook
              </button>
            </form>
        """
        self.web.fetch_text = AsyncMock(return_value=(200, gate_body, gate_url))
        self.web.profile.cookies["xs"] = "synthetic-xs"
        ads_document = json.loads(document())
        ads_document["require"] = []
        ads_document["define"].extend([
            ["DTSGInitialData", [], {"token": "synthetic-fresh-csrf"}],
            ["LSD", [], {"token": "synthetic-fresh-lsd"}],
            ["SprinkleConfig", [], {"param_name": "jazoest", "version": 2,
                                    "should_randomize": False}],
        ])
        ads_document = json.dumps(ads_document)
        self.web.bootstrap = AsyncMock(return_value=SimpleNamespace(
            actor_id="123456", profile_actor_verified=True,
            fb_dtsg="synthetic-fresh-csrf", lsd="synthetic-fresh-lsd",
            jazoest="", request_context={"__rev": "12345"}))
        self.web.session.facebook_cookie_snapshot = lambda: dict(self.web.profile.cookies)
        calls = []

        @asynccontextmanager
        async def request(method, url, **options):
            calls.append((method, url, options))
            headers = {}
            if url == "https://business.facebook.com/business/loginpage/continue":
                status, headers, raw = 302, {"Location": "/latest/home"}, b""
            elif url == "https://business.facebook.com/latest/home":
                status, raw = 200, b"continued"
            elif url == return_url:
                status, raw = 200, ads_document.encode()
            elif url == "https://adsmanager.facebook.com/ads/manager/ajax/renew_access_token/":
                status = 200
                raw = ("for (;;);" + json.dumps({"payload": {
                    "access_token": "synthetic-renewed-session",
                    "client_id": "119211728144504"}})).encode()
            elif url.startswith("https://adsmanager-graph.facebook.com/v22.0/act_111111"):
                status, raw = 200, json.dumps({
                    "id": "act_111111", "account_id": "111111",
                    "business": {"id": account_business}}).encode()
            else:
                raise AssertionError("Unexpected profile HTTP request: " + method + " " + url)
            yield SimpleNamespace(http_version="HTTP/2", status=status, content=Body(raw),
                                  url=url, headers=headers)

        self.web.session.calls = []
        self.web.session.request = request
        return calls

    async def _assert_business_gate_http_flow(self, calls):
        self.assertEqual([row[0] for row in calls], ["POST", "GET", "GET", "POST", "GET"])
        self.assertEqual(calls[0][1],
            "https://business.facebook.com/business/loginpage/continue")
        self.assertEqual(calls[0][2]["content"],
            "fb_dtsg=synthetic-gate-csrf&continue=1")
        self.assertEqual(calls[1][1], "https://business.facebook.com/latest/home")
        self.assertEqual(calls[2][1],
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            "?act=111111&business_id=222222")
        self.assertEqual(calls[3][1],
            "https://adsmanager.facebook.com/ads/manager/ajax/renew_access_token/")
        self.assertEqual(calls[3][2]["headers"]["Origin"],
            "https://adsmanager.facebook.com")
        self.assertEqual(calls[3][2]["headers"]["Referer"],
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            "?act=111111&business_id=222222")
        self.assertEqual(calls[4][1].split("?", 1)[0],
            "https://adsmanager-graph.facebook.com/v22.0/act_111111")

    async def test_reference_business_loginpage_uses_http_handoff_then_fresh_document(self):
        calls = await self._mock_business_gate_session()
        client = await self.open_reference()
        self.assertTrue(client.read_only)
        self.assertEqual(client.context.actor_id, "123456")
        await self._assert_business_gate_http_flow(calls)
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_paused_draft_gate_uses_http_handoff_and_exact_account_proof(self):
        calls = await self._mock_business_gate_session()
        client = await AdsManagerPrivateHttp.open(self.web, account_id="111111",
            business_id="222222", actor_id="123456", allow_draft_gate_recovery=True)
        self.assertFalse(client.read_only)
        self.assertEqual(client.context.business_id, "222222")
        await self._assert_business_gate_http_flow(calls)
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_paused_draft_gate_renews_after_noninteractive_handoff_redirect(self):
        calls = await self._mock_business_gate_session()
        self.web._business_login_gate_outcome = "native_root_missing"
        with patch("app.private_ads_http.continue_business_login_gate",
                   new=AsyncMock(return_value=None)):
            client = await AdsManagerPrivateHttp.open(self.web, account_id="111111",
                business_id="222222", actor_id="123456", allow_draft_gate_recovery=True)
        self.assertFalse(client.read_only)
        self.assertEqual(client.context.actor_id, "123456")
        self.assertEqual(client.context.business_id, "222222")
        self.assertEqual([row[0] for row in calls], ["POST", "GET"])
        self.assertEqual(calls[0][1],
            "https://adsmanager.facebook.com/ads/manager/ajax/renew_access_token/")
        self.assertEqual(calls[0][2]["headers"]["Referer"],
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            "?act=111111&business_id=222222")
        self.assertEqual(calls[1][1].split("?", 1)[0],
            "https://adsmanager-graph.facebook.com/v22.0/act_111111")

    async def test_paused_draft_gate_does_not_renew_when_business_gate_has_login_form(self):
        await self._mock_business_gate_session()
        gate_url = "https://business.facebook.com/business/loginpage/"
        self.web.fetch_text.return_value = (200,
            '<form id="login_form"><input type="password" name="pass"></form>', gate_url)
        self.web._business_login_gate_outcome = "continuation_redirected_to_auth"
        with patch("app.private_ads_http.continue_business_login_gate",
                   new=AsyncMock(return_value=None)):
            with self.assertRaises(ProvisioningError) as caught:
                await AdsManagerPrivateHttp.open(self.web, account_id="111111",
                    business_id="222222", actor_id="123456", allow_draft_gate_recovery=True)
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE")
        self.assertEqual(self.web.session.calls, [])

    async def test_saved_publication_gate_uses_http_handoff_and_exact_account_proof(self):
        calls = await self._mock_business_gate_session()
        client = await AdsManagerPrivateHttp.open(self.web, account_id="111111",
            business_id="222222", actor_id="123456",
            allow_saved_publication_gate_recovery=True)
        self.assertFalse(client.read_only)
        self.assertEqual(client.context.actor_id, "123456")
        self.assertEqual(client.context.business_id, "222222")
        await self._assert_business_gate_http_flow(calls)
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_draft_gate_rejects_another_business_before_writes(self):
        calls = await self._mock_business_gate_session(account_business="999999")
        with self.assertRaises(ProvisioningError) as caught:
            await AdsManagerPrivateHttp.open(self.web, account_id="111111",
                business_id="222222", actor_id="123456", allow_draft_gate_recovery=True)
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_BOOTSTRAP_SCOPE_UNVERIFIED")
        self.assertEqual([row[0] for row in calls], ["POST", "GET", "GET", "POST", "GET"])
        self.assertTrue(calls[3][1].startswith(
            "https://adsmanager.facebook.com/ads/manager/ajax/renew_access_token/"))
        self.assertEqual(calls[3][2]["headers"]["Referer"],
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
            "?act=111111&business_id=222222")

    async def test_business_loginpage_still_blocks_calls_without_draft_or_read_intent(self):
        self.web.fetch_text = AsyncMock(return_value=(200, "<html>Business login route</html>",
            "https://business.facebook.com/business/loginpage/"))
        with self.assertRaises(ProvisioningError) as caught:
            await AdsManagerPrivateHttp.open(self.web, account_id="111111",
                business_id="222222", actor_id="123456")
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE")
        self.assertEqual(self.web.session.calls, [])

    async def test_two_reference_reads_open_one_document_and_never_refresh_cdn(self):
        from app.private_ads_catalog import AdsManagerRouteCatalog
        from app.private_ads_reads import _ReadClient
        self.web.session.response_body = (b'{"id":"act_111111","account_status":1,'
            b'"business":{"id":"222222"}}')
        catalog = AdsManagerRouteCatalog()
        for _ in range(2):
            reader = _ReadClient(self.web, catalog,
                {"ad_account_id":"111111","business_id":"222222"},
                reference_read=True)
            row = await reader.account_record(reference=True)
            self.assertEqual(row["id"], "act_111111")
            self.assertTrue(reader.http.read_only)
        self.assertEqual(self.web.fetch_text.await_count, 1)
        self.assertEqual(len(self.web.session.calls), 2)  # Fresh account proof on EACH read.
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_parallel_reference_reads_coalesce_html_fetch(self):
        import asyncio
        results = await asyncio.gather(*(self.open_reference() for _ in range(8)))
        self.assertTrue(all(client.read_only for client in results))
        self.assertEqual(self.web.fetch_text.await_count, 1)

    async def test_reference_context_survives_slow_targeting_request_window(self):
        from app.private_ads_http import _REFERENCE_CONTEXT_CACHE
        await self.open_reference()
        key = next(iter(_REFERENCE_CONTEXT_CACHE))
        observed, context, origin = _REFERENCE_CONTEXT_CACHE[key]
        # Live ReMask searches can take 40+ seconds; keep this account-bound,
        # read-only bootstrap reusable across targeting fields in that window.
        _REFERENCE_CONTEXT_CACHE[key] = (observed - 120, context, origin)
        await self.open_reference()
        self.assertEqual(self.web.fetch_text.await_count, 1)

    async def test_cookie_account_and_expired_cache_must_reinitialize(self):
        from app.private_ads_http import _REFERENCE_CONTEXT_CACHE, _REFERENCE_CONTEXT_TTL_SECONDS
        await self.open_reference()
        self.web.profile.cookies["xs"] = "changed"
        await self.open_reference()
        self.assertEqual(self.web.fetch_text.await_count, 2)
        key = next(reversed(_REFERENCE_CONTEXT_CACHE))
        observed, context, origin = _REFERENCE_CONTEXT_CACHE[key]
        _REFERENCE_CONTEXT_CACHE[key] = (observed - _REFERENCE_CONTEXT_TTL_SECONDS - 1, context, origin)
        await self.open_reference()
        self.assertEqual(self.web.fetch_text.await_count, 3)
        await AdsManagerPrivateHttp.open(self.web, account_id="333333",
            business_id="222222", actor_id="123456", reference_read=True)
        self.assertEqual(self.web.fetch_text.await_count, 4)

    async def test_reference_client_cannot_post_and_launch_open_is_always_fresh(self):
        client = await self.open_reference()
        async def claim():
            self.fail("Reference-only sender must never claim a write")
        with self.assertRaises(ProvisioningError):
            await client.post("/act_111111/campaigns", {}, before_submit=claim)
        self.assertEqual(len(self.web.session.calls), 0)
        other = await AdsManagerPrivateHttp.open(self.web, account_id="111111",
            business_id="222222", actor_id="123456")
        self.assertFalse(other.read_only)
        self.assertEqual(self.web.fetch_text.await_count, 2)
        self.assertEqual(self.web.refresh_reviewed_modules.await_count, 1)

    async def test_benchmark_old_vs_reused_reference_traffic_fixture(self):
        """Controlled encoded-body comparison; NEVER proxy provider billing.

        The legacy path deliberately keeps CDN maintenance disabled here:
        savings come solely from avoiding repeat Ads Manager document reads.
        The selected account is still read independently on each iteration.
        """
        import gzip
        import hashlib
        from app.private_ads_http import _REFERENCE_CONTEXT_CACHE

        # Synthetic document with a large unrelated field. This is not
        # captured Meta HTML, and its compressed size is not a production
        # observation. The same payload is used in each scenario.
        source = json.loads(document())
        source["synthetic_padding"] = "".join(
            hashlib.sha256(str(i).encode()).hexdigest() for i in range(1024))
        page = json.dumps(source, separators=(",", ":"))
        account = (b'{"id":"act_111111","account_id":"111111",'
                   b'"business":{"id":"222222"}}')
        html_wire_fixture_bytes = len(gzip.compress(page.encode(), mtime=0))
        account_fixture_bytes = len(account)

        async def scenario(*, count, optimized):
            _REFERENCE_CONTEXT_CACHE.clear()
            web = Web()
            web.profile.name = "benchmark-profile"
            web.profile.user_agent = "fixture-UA"
            web.session.response_body = account
            documents = []
            async def fetch_page(url, **kwargs):
                documents.append(url)
                return (200, page, url)
            web.fetch_text = fetch_page
            web.refresh_reviewed_modules = AsyncMock()
            for _ in range(count):
                if optimized:
                    client = await AdsManagerPrivateHttp.open(web,
                        account_id="111111", business_id="222222",
                        actor_id="123456", reference_read=True)
                else:
                    # Same code path as legacy open, without optional CDN work.
                    client = await AdsManagerPrivateHttp._open_uncached(web,
                        account_id="111111", business_id="222222",
                        actor_id="123456", refresh_modules=False)
                    client.read_only = True
                result = await client.get("/act_111111",
                    {"fields":["id","account_id","business"]})
                self.assertEqual(result["account_id"], "111111")
                self.assertEqual(result["business"]["id"], "222222")
            return {
                "html_gets": len(documents),
                "account_gets": len(web.session.calls),
                "fixture_encoded_response_bytes":
                    len(documents) * html_wire_fixture_bytes
                    + len(web.session.calls) * account_fixture_bytes,
                "fixture_decoded_response_bytes":
                    len(documents) * len(page.encode())
                    + len(web.session.calls) * account_fixture_bytes,
                "cdn_refreshes": web.refresh_reviewed_modules.await_count,
            }

        for count in (2, 5, 10):
            legacy = await scenario(count=count, optimized=False)
            reused = await scenario(count=count, optimized=True)
            self.assertEqual(legacy["html_gets"], count)
            self.assertEqual(reused["html_gets"], 1)
            self.assertEqual(legacy["account_gets"], count)
            self.assertEqual(reused["account_gets"], count)
            self.assertEqual(legacy["cdn_refreshes"], 0)
            self.assertEqual(reused["cdn_refreshes"], 0)
            self.assertLess(reused["fixture_encoded_response_bytes"],
                            legacy["fixture_encoded_response_bytes"])
            print("REMASK_TRAFFIC_FIXTURE " + json.dumps({
                "reads": count, "legacy": legacy, "optimized": reused,
                "fixture_gzip_doc_bytes": html_wire_fixture_bytes,
                "scope": "synthetic_gzip_body_only_not_proxy_billing"
            }, sort_keys=True), flush=True)
