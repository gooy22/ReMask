import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.private_session_probe import probe_document, _login_shape, _transport_kind, _transport_reason
from app.provisioning.models import ProvisioningError
from tests.test_private_ads_draft import document


class SessionProbeTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return SimpleNamespace(profile_id="4", cookies={"c_user":"123456", "xs":"saved-fixture"},
            proxy="http://user:proxy-fixture-secret@proxy.invalid:80", user_agent="fixture-UA",
            ad_accounts=[{"id":"act_111111", "business_id":"222222"}])

    def payload(self):
        return {"cookies":{"c_user":"123456", "xs":"import-fixture-secret", "datr":"fixture-device"},
            "ad_account_id":"111111", "business_id":"222222"}

    def factory(self, final=None, body=None, error=None):
        self.web = SimpleNamespace(close=AsyncMock(), fetch_text=AsyncMock(
            side_effect=error, return_value=(200, document() if body is None else body,
                final or "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222")))
        return Mock(return_value=self.web)

    async def test_one_get_uses_exact_saved_proxy_ua_and_does_not_change_input_or_profile(self):
        context, payload, factory = self.context(), self.payload(), self.factory()
        before_context, before_payload = copy.deepcopy(vars(context)), copy.deepcopy(payload)
        result = await probe_document(context, payload, web_factory=factory)
        profile = factory.call_args.args[0]
        self.assertEqual(profile.name, "4")
        self.assertEqual(profile.proxy, context.proxy)
        self.assertEqual(profile.user_agent, context.user_agent)
        self.assertEqual(profile.cookies, payload["cookies"])
        self.assertIsNot(profile.cookies, payload["cookies"])
        self.assertEqual(vars(context), before_context)
        self.assertEqual(payload, before_payload)
        self.web.fetch_text.assert_awaited_once_with(
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=111111&business_id=222222",
            max_bytes=5_000_001, document_navigation=True)
        self.web.close.assert_awaited_once()
        self.assertTrue(self.web.private_only)
        self.assertTrue(result["bootstrap_context_confirmed"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["objects_created"])
        self.assertNotEqual(result["saved_auth_cookie_fingerprint"], result["evidence"]["auth_cookie_fingerprint"])
        for secret in ("saved-fixture", "import-fixture-secret", "proxy-fixture-secret", "synthetic-session-value"):
            self.assertNotIn(secret, str(result))

    async def test_real_login_reply_is_a_result_without_renewal_or_post(self):
        result = await probe_document(self.context(), self.payload(), web_factory=self.factory(
            final="https://business.facebook.com/business/loginpage/?next=secret", body="<h1>Login</h1>"))
        self.assertTrue(result["response_received"])
        self.assertEqual(result["evidence"]["final_path"], "/business/loginpage/")
        self.assertEqual(result["evidence"]["path_gate"], "BUSINESS_LOGIN_GATE")
        self.assertFalse(result["bootstrap_context_confirmed"])
        self.web.fetch_text.assert_awaited_once()

    async def test_another_actor_is_rejected_before_opening_transport(self):
        payload = self.payload(); payload["cookies"]["c_user"] = "999999"
        factory = self.factory()
        with self.assertRaises(ProvisioningError) as caught:
            await probe_document(self.context(), payload, web_factory=factory)
        self.assertEqual(caught.exception.code, "PRIVATE_SESSION_PROBE_ACTOR_MISMATCH")
        factory.assert_not_called()

    async def test_unrelated_account_or_business_is_rejected_before_network(self):
        for key in ("business_id", "ad_account_id"):
            payload = self.payload(); payload[key] = "999999"
            factory = self.factory()
            with self.assertRaises(ProvisioningError):
                await probe_document(self.context(), payload, web_factory=factory)
            factory.assert_not_called()

    async def test_proxy_is_mandatory_even_if_a_direct_test_context_exists(self):
        context = self.context(); context.proxy = None; context.direct_test_until = 9999999999
        factory = self.factory()
        with self.assertRaises(ProvisioningError):
            await probe_document(context, self.payload(), web_factory=factory)
        factory.assert_not_called()

    async def test_unreviewed_fields_invalid_cookies_and_headers_are_rejected(self):
        cases = []
        for extra in ("endpoint_url", "access_token", "password", "execution_mode"):
            value = self.payload(); value[extra] = "fixture"; cases.append(value)
        for cookies in ({}, [], {"c_user":"123456", "xs":""},
                {"c_user":"123456", "xs":"injected\r\nHeader: value"},
                {"c_user":"123456", "xs":"value; c_user=999999"},
                {"c_user":"123456", "xs":123}, {"c_user":"123456", "xs":"x"*8193}):
            value = self.payload(); value["cookies"] = cookies; cases.append(value)
        for payload in cases:
            factory = self.factory()
            with self.subTest(payload_keys=list(payload)), self.assertRaises(ProvisioningError):
                await probe_document(self.context(), payload, web_factory=factory)
            factory.assert_not_called()

    async def test_read_failure_is_not_retried_and_does_not_echo_exception_secrets(self):
        result = await probe_document(self.context(), self.payload(), web_factory=self.factory(
            error=TimeoutError("cookie=import-fixture-secret")))
        self.assertFalse(result["response_received"])
        self.assertEqual(result["error_type"], "TimeoutError")
        self.assertNotIn("import-fixture-secret", str(result))
        self.web.fetch_text.assert_awaited_once()
        self.web.close.assert_awaited_once()

    async def test_route_or_actor_mismatch_never_confirms_bootstrap(self):
        for final, body in (("https://business.facebook.com/latest/home", document()),
                (None, document("999999")),
                ("https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=999999&business_id=222222", document())):
            result = await probe_document(self.context(), self.payload(), web_factory=self.factory(final, body))
            self.assertFalse(result["bootstrap_context_confirmed"])

    async def test_fixed_identity_and_anonymous_login_reads_preserve_cookie_source(self):
        for kind, target in (("identity","https://www.facebook.com/me"),
                ("saved_identity","https://www.facebook.com/me"),
                ("login_document","https://www.facebook.com/login/")):
            context, payload, factory = self.context(), self.payload(), self.factory()
            payload['document_kind'] = kind
            before = copy.deepcopy(payload)
            await probe_document(context, payload, web_factory=factory)
            self.web.fetch_text.assert_awaited_once_with(target, max_bytes=5_000_001, document_navigation=True)
            sent = factory.call_args.args[0].cookies
            if kind == 'identity':
                self.assertEqual(sent, payload['cookies'])
            elif kind == 'saved_identity':
                self.assertEqual(sent, context.cookies)
            else:
                self.assertEqual(sent, {'datr':'fixture-device'})
                for secret in ('xs','c_user','fr','pas'):
                    self.assertNotIn(secret, sent)
            self.assertEqual(payload, before)

    async def test_document_selector_never_accepts_an_arbitrary_url_or_untyped_value(self):
        for kind in ('https://unreviewed.invalid/', 'facebook.com', {}, []):
            payload = self.payload(); payload['document_kind'] = kind
            factory = self.factory()
            with self.assertRaises(ProvisioningError):
                await probe_document(self.context(), payload, web_factory=factory)
            factory.assert_not_called()

    def test_form_shape_uses_real_elements_and_excludes_all_values_and_unknown_routes(self):
        body = '<script>const decoy = `<form action="/login.php"><input name="pass">`;</script>'
        body += '<form method="POST" action="/login/device-based/regular/login/?token=query-secret">'
        body += '<input name="email" value="user-secret"><input name="pass" value="password-secret">'
        body += '<input name="lsd" value="csrf-secret"></form>'
        body += '<form action="https://unreviewed.invalid/path-secret"><input name="private-name-secret" value="value-secret"></form>'
        shape = _login_shape(body, 'https://www.facebook.com/login/')
        self.assertEqual(len(shape['forms']), 2)
        self.assertEqual(shape['forms'][0], {'method':'POST','action_host':'www.facebook.com',
            'action_path':'/login/device-based/regular/login/','input_names':['email','pass','lsd']})
        self.assertEqual(shape['forms'][1]['action_path'], 'unreviewed')
        for secret in ('query-secret','user-secret','password-secret','csrf-secret','path-secret','private-name-secret','value-secret','unreviewed.invalid'):
            self.assertNotIn(secret, str(shape))

    def test_failure_kind_is_allowlisted_and_has_no_error_text_or_raw_payload(self):
        for payload, expected in (({'transport_failure':{'kind':'ReadTimeout','message':'secret'}}, 'ReadTimeout'),
                ({'transport_failure':{'kind':'secret'}}, 'unavailable'), ([], 'unavailable'),
                ({'transport_failure':[]}, 'unavailable')):
            error = RuntimeError('secret'); error.meta_payload = payload
            self.assertEqual(_transport_kind(error), expected)

    def test_server_text_is_bounded_and_excludes_scripts_input_values_raw_json_and_tokens(self):
        body = '<head><title>head-secret</title></head><body><h1>Log into Facebook</h1>'
        body += '<input value="input-secret"><script>script-secret</script><style>style-secret</style>'
        body += '<p>access_token=short-secret https://www.facebook.com/?token=url-secret ' + 'A'*80 + '</p></body>'
        text = _login_shape(body, 'https://www.facebook.com/login/')['server_text']
        self.assertIn('Log into Facebook', text)
        self.assertLessEqual(len(text), 800)
        for value in ('head-secret','input-secret','script-secret','style-secret','short-secret','url-secret','A'*80):
            self.assertNotIn(value, text)
        self.assertEqual(_login_shape(document(), 'https://www.facebook.com/')['server_text'], '')

    async def test_actual_wire_cookies_and_meta_deletions_are_visible_without_values(self):
        import httpx
        from http.cookiejar import Cookie
        calls = []
        def respond(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(302, headers=[('Location','https://business.facebook.com/business/loginpage/?next=uri-secret'),
                    ('Set-Cookie','c_user=deleted; Domain=.facebook.com; Path=/; Max-Age=0'),
                    ('Set-Cookie','xs=deleted; Domain=.facebook.com; Path=/; Max-Age=0')], extensions={'http_version':b'HTTP/2'})
            return httpx.Response(200, text=document('0'), extensions={'http_version':b'HTTP/2'})
        created = []
        class WireWeb:
            def __init__(self, profile):
                self.profile = profile
                jar = httpx.Cookies()
                for name, value in profile.cookies.items():
                    jar.jar.set_cookie(Cookie(0,name,value,None,False,'.facebook.com',True,True,'/',True,True,
                        None,True,None,None,{},False))
                self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), cookies=jar,
                    headers={'User-Agent':profile.user_agent})
                created.append(self)
            async def _ensure_session(self):
                return SimpleNamespace(_client=self.client)
            async def fetch_text(self, url, **kwargs):
                response = await self.client.get(url, follow_redirects=True)
                return response.status_code, response.text, str(response.url)
            async def close(self):
                await self.client.aclose()
        payload = self.payload()
        result = await probe_document(self.context(), payload, web_factory=WireWeb)
        self.assertEqual(len(calls), 2)
        self.assertEqual([row['header_auth_state'] for row in result['wire']['requests']], ['matches','absent'])
        self.assertEqual(result['wire']['requests'][0]['header_auth_cookie_names'], ['c_user','xs'])
        self.assertEqual(result['wire']['responses'][0]['auth_cookie_deleted'], ['c_user','xs'])
        self.assertEqual(result['wire']['measurement_errors'], 0)
        self.assertTrue(all(row['method'] == 'GET' and row['user_agent_matches'] for row in result['wire']['requests']))
        self.assertTrue(created[0].client.is_closed)
        self.assertEqual(created[0].client.event_hooks['request'], [])
        self.assertEqual(created[0].profile.cookies, payload['cookies'])
        for secret in ('import-fixture-secret','uri-secret','proxy-fixture-secret','synthetic-session-value'):
            self.assertNotIn(secret, str(result))

    async def test_wire_observer_failure_does_not_prevent_the_original_read(self):
        factory = self.factory()
        self.web._ensure_session = AsyncMock(side_effect=RuntimeError('private-secret'))
        result = await probe_document(self.context(), self.payload(), web_factory=factory)
        self.assertTrue(result['bootstrap_context_confirmed'])
        self.assertEqual(result['wire']['measurement_errors'], 1)
        self.web.fetch_text.assert_awaited_once()
        self.assertNotIn('private-secret', str(result))

    def test_actual_other_profile_link_has_bounded_metadata_and_never_returns_query_values(self):
        body = '<a href="/login.php?skip_api_login=1&amp;next=uri-secret"><span>Use another profile</span></a>'
        shape, private = _login_shape(body, 'https://www.facebook.com/login/', include_entry=True)
        self.assertTrue(shape['other_profile_entry']['ready'])
        self.assertEqual(shape['other_profile_entry']['path'], '/login.php')
        self.assertEqual(shape['other_profile_entry']['query_keys'], ['next','skip_api_login'])
        self.assertIn('next=uri-secret', private)
        self.assertNotIn('uri-secret', str(shape))
        for href in ('https://unreviewed.invalid/login/', 'http://www.facebook.com/login/',
                'https://www.facebook.com:443/login/', '/logout.php', '#switch'):
            shape, private = _login_shape('<a href="'+href+'">Use another profile</a>',
                'https://www.facebook.com/login/', include_entry=True)
            self.assertFalse(shape['other_profile_entry']['ready'])
            self.assertIsNone(private)

    async def test_other_profile_mode_follows_only_the_actual_reviewed_get_link_once(self):
        payload = self.payload(); payload['document_kind'] = 'login_other_profile'
        factory = self.factory()
        self.web.fetch_text.side_effect = [(200,
            '<a href="/login.php?next=uri-secret&amp;skip_api_login=1">Use another profile</a>',
            'https://www.facebook.com/login/'), (200,
            '<form method="post" action="/login/device-based/regular/login/"><input name="email"><input name="pass"><input name="lsd" value="csrf-secret"></form>',
            'https://www.facebook.com/login.php?next=uri-secret&skip_api_login=1')]
        result = await probe_document(self.context(), payload, web_factory=factory)
        self.assertTrue(result['other_profile_entry_followed'])
        self.assertEqual(self.web.fetch_text.await_count, 2)
        self.assertEqual(self.web.fetch_text.await_args_list[1].args[0],
            'https://www.facebook.com/login.php?next=uri-secret&skip_api_login=1')
        self.assertEqual(result['login_shape']['forms'][0]['input_names'], ['email','pass','lsd'])
        self.assertFalse(result['persisted'])
        self.assertFalse(result['objects_created'])
        self.assertNotIn('uri-secret', str(result))
        self.assertNotIn('csrf-secret', str(result))

    async def test_missing_or_same_document_entry_never_invents_a_second_url(self):
        for body in ('<div role="button">Use another profile</div>',
                '<a href="/login/">Use another profile</a>',
                '<a href="https://unreviewed.invalid/login/">Use another profile</a>'):
            payload = self.payload(); payload['document_kind'] = 'login_other_profile'
            factory = self.factory(final='https://www.facebook.com/login/', body=body)
            result = await probe_document(self.context(), payload, web_factory=factory)
            self.assertFalse(result['other_profile_entry_followed'])
            self.web.fetch_text.assert_awaited_once()

    def test_transport_reason_reduces_exception_chain_to_a_fixed_category(self):
        import ssl
        error = RuntimeError('https://user:proxy-secret@proxy.invalid/')
        error.__cause__ = ssl.SSLError('CERTIFICATE_VERIFY_FAILED private-secret')
        self.assertEqual(_transport_reason(error), 'certificate_verification')
        self.assertEqual(_transport_reason(ConnectionResetError('private-secret')), 'connection_reset')
        self.assertEqual(_transport_reason(RuntimeError('private-secret')), 'unavailable')
