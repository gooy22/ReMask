import unittest
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
from unittest.mock import AsyncMock, Mock, patch

from fb_worker import AuthenticationError, RemoteRequestError, FacebookBootstrap, FacebookWebSession, WebProfile, _has_login_form
from app.meta_auth_document import auth_route_gate


class FacebookDtsgBootstrapParserTests(unittest.TestCase):
    def test_login_module_names_and_script_templates_are_not_login_forms(self):
        for body in ('<script>var login_form = true;</script>',
                     '<script>const template = `<form id="login_form">`;</script>',
                     '<!-- <form action="/login.php"> -->',
                     '<form action="/settings/save">'):
            self.assertFalse(_has_login_form(body))
        for body in ('<form id="login_form">', '<FORM ID=login_form>',
                     '<form action="https://www.facebook.com/login.php?next=business">'):
            self.assertTrue(_has_login_form(body))
    def test_meta_index_adsmanager_return_redirect_is_an_auth_gate(self):
        url = ("https://www.facebook.com/index.php?next="
               "https%3A%2F%2Fwww.facebook.com%2Fadsmanager%2Fmanage%2Fcampaigns"
               "&deoia=1&no_universal_links=1")
        self.assertEqual(auth_route_gate(url), "META_LOGIN_REQUIRED")
        self.assertEqual(auth_route_gate(
            "https://www.facebook.com/index.php?next=https%3A%2F%2Fwww.facebook.com%2Fme"), "")

    def extract(self, source: str) -> str:
        return FacebookWebSession._first_match(
            source,
            list(FacebookWebSession.FB_DTSG_PATTERNS),
        )

    def test_plain_bootloader_payload(self) -> None:
        source = '["DTSGInitialData",[],{"token":"NA_plain_token"}]'
        self.assertEqual(self.extract(source), "NA_plain_token")

    def test_html_entity_escaped_payload(self) -> None:
        source = (
            '[&quot;DTSGInitialData&quot;,[],'
            '{&quot;token&quot;:&quot;NA_html_token&quot;}]'
        )
        self.assertEqual(self.extract(source), "NA_html_token")

    def test_javascript_escaped_payload(self) -> None:
        source = r'[\"DTSGInitialData\",[],{\"token\":\"NA_js_token\"}]'
        self.assertEqual(self.extract(source), "NA_js_token")

    def test_dtsg_init_data_payload(self) -> None:
        source = (
            '["DTSGInitData",[],'
            '{"token":"NA_init_token","async_get_token":"NA_async"}]'
        )
        self.assertEqual(self.extract(source), "NA_init_token")

    def test_dtsg_object_payload(self) -> None:
        source = '{"dtsg":{"token":"NA_object_token"}}'
        self.assertEqual(self.extract(source), "NA_object_token")

    def test_named_json_input_payload(self) -> None:
        source = '{"name":"fb_dtsg","value":"NA_named_token"}'
        self.assertEqual(self.extract(source), "NA_named_token")

    def test_unicode_escaped_init_payload(self) -> None:
        source = (
            r'[\u0022DTSGInitData\u0022,[],'
            r'{\u0022token\u0022:\u0022NA_unicode_token\u0022}]'
        )
        self.assertEqual(self.extract(source), "NA_unicode_token")

    def test_hidden_input_payload(self) -> None:
        source = '<input type="hidden" name="fb_dtsg" value="NA_input_token">'
        self.assertEqual(self.extract(source), "NA_input_token")

    def test_ajax_dtsg_refresh_payload(self) -> None:
        source = 'for (;;);{"payload":{"token":"NA_refresh_token"}}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_refresh_token",
        )

    def test_ajax_dtsg_refresh_direct_token(self) -> None:
        source = '{"token":"NA_refresh_direct"}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_refresh_direct",
        )

    def test_ajax_dtsg_refresh_regex_fallback(self) -> None:
        source = 'prefix token payload: {"token" : "NA_refresh_fallback"} trailing'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_refresh_fallback",
        )

    def test_ajax_dtsg_nested_string_payload(self) -> None:
        source = 'for (;;);{"payload":"{\\\"token\\\":\\\"NA_nested_refresh\\\"}"}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_nested_refresh",
        )

    def test_ajax_dtsg_while_prefix(self) -> None:
        source = 'while(1);{"data":{"token":"NA_while_refresh"}}'
        self.assertEqual(
            FacebookWebSession._parse_dtsg_refresh_response(source),
            "NA_while_refresh",
        )


class FacebookBootstrapAuthGateTests(unittest.IsolatedAsyncioTestCase):
    def session(self, body, url):
        class Response:
            status = 200
            async def text(self): return body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        response = Response(); response.url = url
        class Client:
            calls = []
            def get(self, url, **kwargs):
                self.calls.append(url)
                return response
        session = FacebookWebSession(WebProfile(name='35', cookies={'c_user':'123456789'}, proxy=None, user_agent='fixture'))
        client = Client()
        session._ensure_session = AsyncMock(return_value=client)
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=('', ''))
        session.refresh_reviewed_modules = AsyncMock()
        return session, client

    async def test_business_login_redirect_falls_through_and_classifies_generic_failure_as_session_expired(self):
        session, client = self.session('<form id="login_form">',
            'https://business.facebook.com/business/loginpage/?secret=must-not-leak')
        with self.assertRaises(AuthenticationError) as caught:
            await session.bootstrap()
        self.assertGreater(len(client.calls), 1)
        session._fetch_dtsg_refresh_token.assert_awaited_once()
        self.assertFalse(caught.exception.request_may_have_been_sent)
        from app.private_auth import private_auth_error
        self.assertEqual(private_auth_error(caught.exception).code, 'SESSION_EXPIRED')
        self.assertNotIn('must-not-leak', str(caught.exception.meta_payload))

        business_error = AuthenticationError("exact Business precheck failed")
        business_error.meta_payload = {"business_precheck": [{
            "auth_reason": "login_redirect",
            "final_url": "https://business.facebook.com/business/loginpage/",
        }]}
        business_error.transport_stage = "business_auth_precheck"
        self.assertEqual(private_auth_error(business_error).code, "BUSINESS_LOGIN_GATE")

    async def test_adsmanager_login_return_is_not_accepted_as_profile_bootstrap(self):
        url = ("https://www.facebook.com/index.php?next="
               "https%3A%2F%2Fwww.facebook.com%2Fadsmanager%2Fmanage%2Fcampaigns"
               "&deoia=1&no_universal_links=1")
        body = ('<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
                '["DTSGInitialData",[],{"token":"login-page-token"}]')
        session, client = self.session(body, url)
        with self.assertRaises(AuthenticationError) as caught:
            await session.bootstrap()
        self.assertGreater(len(client.calls), 1)
        self.assertFalse(session._profile_session_identity_verified)
        from app.private_auth import private_auth_error
        self.assertEqual(private_auth_error(caught.exception).code, "META_LOGIN_REQUIRED")
        session._fetch_dtsg_refresh_token.assert_awaited_once()

    async def test_bundle_bootstrap_follows_native_business_handoff_before_profile_fallback(self):
        session, client = self.session('<a href="/business/loginrequest/fb/">Log in with Facebook</a>',
            'https://business.facebook.com/business/loginpage/')
        recovered = {
            "status": 200,
            "body": '["DTSGInitialData",[],{"token":"business-token"}]',
            "url": "https://business.facebook.com/latest/home",
            "headers": {},
        }
        with patch("fb_worker.continue_business_login_gate", new_callable=AsyncMock,
                   return_value=recovered) as handoff:
            bootstrap = await session.bootstrap()

        self.assertEqual(bootstrap.fb_dtsg, "business-token")
        self.assertEqual(bootstrap.source_url, FacebookWebSession.ADS_MANAGER_URL)
        self.assertEqual(len(client.calls), 1)
        handoff.assert_awaited_once()
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_profile_probe_restores_saved_cookies_before_same_facebook_redirect(self):
        actor = "123456789"
        saved = {"c_user": actor, "xs": "fixture-xs"}
        profile_html = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"profile-token"}]'
        )
        calls = []

        class Response:
            def __init__(self, url, status, headers, body):
                self.url, self.status, self.headers, self.body = url, status, headers, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False

        class Client:
            def __init__(self):
                self.current = dict(saved)
            def restore_profile_cookies(self):
                self.current = dict(saved)
            def facebook_cookie_snapshot(self):
                return dict(self.current)
            def get(self, url, **kwargs):
                calls.append((url, kwargs.get("allow_redirects"), dict(self.current)))
                if url == "https://www.facebook.com/me":
                    # Model Meta clearing root-scope cookies on the redirect response.
                    self.current = {}
                    return Response(url, 302, {"location": "/"}, "")
                if url == "https://www.facebook.com/":
                    return Response(url, 200, {}, profile_html)
                raise AssertionError("profile bootstrap followed an unreviewed route")

        client = Client()
        session = FacebookWebSession(WebProfile(
            name="15", cookies=dict(saved), proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=client)
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual([call[0] for call in calls], [
            "https://www.facebook.com/me",
            "https://www.facebook.com/",
        ])
        self.assertEqual([call[1] for call in calls], [False, False])
        self.assertEqual([call[2] for call in calls], [saved, saved])

    async def test_business_loginpage_probe_requires_same_actor_profile_document(self):
        expected_actor = "123456789"
        profile_html = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"profile-token"}]'
        )
        calls = []
        class Response:
            status = 200
            def __init__(self, url, body):
                self.url = url
                self.body = body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append((url, kwargs))
                if url == "https://www.facebook.com/me":
                    return Response(url, profile_html)
                return Response("https://business.facebook.com/business/loginpage/", '<form id="login_form">')
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        generic = await session.bootstrap()
        self.assertEqual(generic.fb_dtsg, "profile-token")
        self.assertEqual(generic.actor_id, expected_actor)
        self.assertFalse(generic.profile_actor_verified)
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[0][0], FacebookWebSession.ADS_MANAGER_URL)
        self.assertEqual(calls[-1][0], "https://www.facebook.com/me")

        generic_call_count = len(calls)
        session.refresh_reviewed_modules.reset_mock()
        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)
        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual([call[0] for call in calls[generic_call_count:]], [
            "https://www.facebook.com/me",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()
        session.refresh_reviewed_modules.assert_not_awaited()

    async def test_scoped_read_bootstraps_business_home_before_profile_routes(self):
        expected_actor = "123456789"
        business_html = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"business-token"}]'
        )
        calls = []

        class Response:
            status = 200
            def __init__(self, url, body):
                self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False

        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                return Response(url, business_html)

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(
            allow_business_loginpage_probe=True, require_actor_match=False,
            prefer_business_home=True,
        )

        self.assertEqual(bootstrap.fb_dtsg, "business-token")
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(calls, [FacebookWebSession.ADS_MANAGER_URL])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_business_loginpage_fallback_preserves_scoped_read_policy(self):
        expected_actor = "123456789"
        calls = []

        class Response:
            status = 200
            def __init__(self, url, body):
                self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False

        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                if url == FacebookWebSession.ADS_MANAGER_URL:
                    return Response(
                        "https://business.facebook.com/business/loginpage/",
                        '<form id="login_form">',
                    )
                if url == "https://www.facebook.com/me":
                    return Response(url, '["DTSGInitialData",[],{"token":"profile-token"}]')
                return Response(url, "")

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(
            allow_business_loginpage_probe=True, require_actor_match=False,
            prefer_business_home=True,
        )

        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertFalse(bootstrap.profile_actor_verified)
        self.assertEqual(calls, [
            FacebookWebSession.ADS_MANAGER_URL,
            "https://www.facebook.com/me",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_business_loginpage_probe_uses_same_actor_profile_route_with_dtsg(self):
        expected_actor = "123456789"
        calls = []
        class Response:
            status = 200
            def __init__(self, url, body):
                self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                if url == "https://www.facebook.com/me":
                    # /me may verify identity while omitting the app token.
                    return Response(url, '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>')
                if url == "https://www.facebook.com/settings":
                    # A dedicated profile surface supplies the token after
                    # /me confirms the actor but omits it.
                    return Response(url, '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
                        '["DTSGInitialData",[],{"token":"profile-token"}]')
                return Response("https://business.facebook.com/business/loginpage/", '<form id="login_form">')
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()
        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)
        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()
        session.refresh_reviewed_modules.assert_not_awaited()

    async def test_profile_probe_tries_an_alternate_surface_after_transport_failure(self):
        import httpx
        calls = []
        profile_html = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"profile-token"}]'
        )
        class Response:
            status = 200
            def __init__(self, url, body):
                self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                if url == "https://www.facebook.com/me":
                    raise httpx.ReadTimeout("temporary profile route timeout")
                if url == "https://www.facebook.com/settings":
                    return Response(url, profile_html)
                raise AssertionError("the exact profile probe must not visit Business Suite")
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": "123456789"}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()
        session._bootstrap = FacebookBootstrap(
            actor_id="123456789", fb_dtsg="stale-business-token",
        )

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, "123456789")
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_profile_probe_checks_settings_after_home_candidate_mismatch(self):
        expected_actor = "123456789"
        mismatched_home = '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
        verified_settings = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"profile-token"}]'
        )
        calls = []
        class Response:
            status = 200
            def __init__(self, url, body):
                self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                if url == "https://www.facebook.com/me":
                    return Response("https://www.facebook.com/", mismatched_home)
                if url == "https://www.facebook.com/settings":
                    return Response(url, verified_settings)
                raise AssertionError("the profile probe must stay on read-only Facebook routes")
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_scoped_bootstrap_uses_verified_ads_manager_after_profile_routes_mismatch(self):
        expected_actor = "123456789"
        mismatched = '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
        verified_ads_manager = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"adsmanager-token"}]'
        )
        calls = []
        timeouts = {}
        ads_manager_url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
        class Response:
            status = 200
            def __init__(self, url, body): self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                timeouts[url] = kwargs.get("request_timeout_seconds")
                if url == ads_manager_url:
                    return Response(url, verified_ads_manager)
                return Response(url, mismatched)

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "adsmanager-token")
        self.assertEqual(bootstrap.source_url, ads_manager_url)
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
            "https://www.facebook.com/",
            "https://www.facebook.com/profile.php",
            ads_manager_url,
        ])
        self.assertEqual(timeouts["https://www.facebook.com/me"], 8.0)
        self.assertEqual(timeouts[ads_manager_url], 15.0)
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_scoped_bootstrap_uses_mobile_profile_after_business_loginpage(self):
        expected_actor = "123456789"
        mismatched = '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
        verified_mobile = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"mobile-profile-token"}]'
        )
        calls = []
        ads_manager_url = "https://adsmanager.facebook.com/adsmanager/manage/campaigns"
        mobile_url = "https://m.facebook.com/"
        class Response:
            status = 200
            def __init__(self, url, body): self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                if url == ads_manager_url:
                    return Response("https://business.facebook.com/business/loginpage/", "<html>Business login</html>")
                if url == mobile_url:
                    return Response(url, verified_mobile)
                return Response(url, mismatched)

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "mobile-profile-token")
        self.assertEqual(bootstrap.source_url, mobile_url)
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
            "https://www.facebook.com/",
            "https://www.facebook.com/profile.php",
            ads_manager_url,
            mobile_url,
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_scoped_bootstrap_uses_legacy_profile_php_after_generic_routes_mismatch(self):
        expected_actor = "123456789"
        mismatched = '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
        verified_profile = (
            '<script>CurrentUserInitialData = {"USER_ID":"123456789"};</script>'
            '["DTSGInitialData",[],{"token":"legacy-profile-token"}]'
        )
        calls = []
        legacy_profile_url = "https://www.facebook.com/profile.php"
        class Response:
            status = 200
            def __init__(self, url, body): self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                return Response(url, verified_profile if url == legacy_profile_url else mismatched)

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "legacy-profile-token")
        self.assertEqual(bootstrap.source_url, legacy_profile_url)
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
            "https://www.facebook.com/",
            legacy_profile_url,
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_scoped_bootstrap_rejects_mismatched_identity_after_all_profile_routes(self):
        expected_actor = "123456789"
        calls = []
        class Response:
            status = 200
            def __init__(self, url, body): self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def get(self, url, **kwargs):
                calls.append(url)
                return Response(url,
                    '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
                    '["DTSGInitialData",[],{"token":"profile-token"}]')

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": expected_actor}, proxy=None, user_agent="fixture"))
        session._ensure_session = AsyncMock(return_value=Client())
        session._fetch_dtsg_refresh_token = AsyncMock(return_value=("", ""))
        session.refresh_reviewed_modules = AsyncMock()

        with self.assertRaises(AuthenticationError):
            await session.bootstrap(allow_business_loginpage_probe=True)

        self.assertFalse(session._profile_session_identity_verified)
        self.assertIsNone(session._bootstrap)
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
            "https://www.facebook.com/",
            "https://www.facebook.com/profile.php",
            "https://adsmanager.facebook.com/adsmanager/manage/campaigns",
            "https://m.facebook.com/",
            "https://mbasic.facebook.com/",
            "https://mbasic.facebook.com/profile.php",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_exact_scoped_business_operation_continues_after_generic_business_loginpage(self):
        actor = "123456789"
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": actor}, proxy=None, user_agent="fixture"))
        bootstrap = FacebookBootstrap(actor_id=actor, fb_dtsg="profile-token",
            source_url="https://www.facebook.com/me", profile_actor_verified=False)
        class CookieSession:
            restored = False
            def facebook_cookie_snapshot(self): return {"xs": ""}
            def restore_profile_cookies(self): self.restored = True
        cookie_session = CookieSession()
        session._ensure_session = AsyncMock(return_value=cookie_session)
        session.fetch_text = AsyncMock(return_value=(
            200, '<form id="login_form">', "https://business.facebook.com/business/loginpage/"
        ))

        result = await session._business_bootstrap(
            bootstrap, business_loginpage_probe="READ_BM",
        )

        self.assertIs(result, bootstrap)
        self.assertTrue(cookie_session.restored)
        session.fetch_text.assert_awaited_once()

    async def test_business_loginpage_bypass_is_limited_to_exact_pinned_contract_and_verified_reads(self):
        from app.static_meta_contracts import command
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": "123456789"}, proxy=None, user_agent="fixture"))
        row = command("READ_BM")
        self.assertEqual(session._validate_business_loginpage_probe(
            "READ_BM", row["doc_id"], row["variables"],
            friendly_name=row["friendly_name"], endpoint_url=row["endpoint_url"],
            request_envelope=None, before_submit=None, business_context_id="",
            multipart_files=None, read_timeout_seconds=30), "READ_BM")
        altered = dict(row["variables"])
        altered["fetchNumberForBusinessScopes"] = 999
        with self.assertRaises(RemoteRequestError):
            session._validate_business_loginpage_probe(
                "READ_BM", row["doc_id"], altered,
                friendly_name=row["friendly_name"], endpoint_url=row["endpoint_url"],
                request_envelope=None, before_submit=None, business_context_id="",
                multipart_files=None, read_timeout_seconds=30)

        create = command("CREATE_BM", actor="123456789", name="Test BM",
            first="Test", last="User", email="test@example.com",
            mutation="mutation-id", join="join-id")
        before_submit = AsyncMock()
        with self.assertRaises(RemoteRequestError):
            session._validate_business_loginpage_probe(
                "CREATE_BM", create["doc_id"], create["variables"],
                friendly_name=create["friendly_name"], endpoint_url=create["endpoint_url"],
                request_envelope=None, before_submit=before_submit, business_context_id="",
                multipart_files=None, read_timeout_seconds=None)
        session._bootstrap = FacebookBootstrap(actor_id="123456789", fb_dtsg="fixture")
        session.mark_business_read_verified("READ_BM", complete=True)
        self.assertEqual(session._validate_business_loginpage_probe(
            "CREATE_BM", create["doc_id"], create["variables"],
            friendly_name=create["friendly_name"], endpoint_url=create["endpoint_url"],
            request_envelope=None, before_submit=before_submit, business_context_id="",
            multipart_files=None, read_timeout_seconds=None), "CREATE_BM")
        self.assertFalse(session.has_fresh_business_read_verified("READ_RK", "555555555"))

    async def test_private_rk_inventory_probe_is_bound_to_exact_business_read(self):
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": "123456789"}, proxy=None, user_agent="fixture"))
        values = {
            "operation": "PRIVATE_RK_INVENTORY",
            "doc_id": "26033539376343649",
            "variables": {"businessID": "987654321", "assetFilters": None},
            "friendly_name": "ObservedBusinessAdAccounts",
            "endpoint_url": "https://business.facebook.com/api/graphql/",
            "request_envelope": None,
            "before_submit": AsyncMock(),
            "business_context_id": "987654321",
            "multipart_files": None,
            "read_timeout_seconds": None,
            "private_inventory_query_contract": {
                "operation_kind": "query",
                "business_id": "987654321",
                "doc_id": "26033539376343649",
                "friendly_name": "ObservedBusinessAdAccounts",
                "variables": {"businessID": "987654321", "assetFilters": None},
                "asset_scope": True,
                "response_collection": "connected_objects",
            },
        }
        self.assertEqual(session._validate_business_loginpage_probe(**values), "PRIVATE_RK_INVENTORY")
        for change in (
            {"variables": {"businessID": "555555555"}},
            {"endpoint_url": "https://www.facebook.com/api/graphql/"},
            {"business_context_id": "555555555"},
            {"doc_id": "not-a-doc-id"},
            {"request_envelope": {"__user": "123456789"}},
            {"read_timeout_seconds": 30},
            {"private_inventory_query_contract": {**values["private_inventory_query_contract"], "operation_kind": "mutation"}},
            {"private_inventory_query_contract": {**values["private_inventory_query_contract"], "business_id": "555555555"}},
            {"private_inventory_query_contract": {**values["private_inventory_query_contract"], "doc_id": "123456789012345"}},
        ):
            with self.subTest(change=change), self.assertRaises(RemoteRequestError):
                session._validate_business_loginpage_probe(**{**values, **change})

    async def test_list_pages_probe_requires_exact_profile_scoped_read_contract(self):
        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": "123456789"}, proxy=None, user_agent="fixture"))
        values = {
            "operation": "LIST_PAGES",
            "doc_id": "123456789012345",
            "variables": {"assetOwnerId": "123456789"},
            "friendly_name": "AccountQualityUserPagesWrapper_UserPageQuery",
            "endpoint_url": "https://www.facebook.com/api/graphql/",
            "request_envelope": None,
            "before_submit": None,
            "business_context_id": "",
            "multipart_files": None,
            "read_timeout_seconds": 30,
        }
        self.assertEqual(session._validate_business_loginpage_probe(**values), "LIST_PAGES")
        for change in (
            {"variables": {"assetOwnerId": "987654321"}},
            {"endpoint_url": "https://business.facebook.com/api/graphql/"},
            {"friendly_name": "UnexpectedMutation"},
            {"read_timeout_seconds": None},
            {"request_envelope": {"__user": "123456789"}},
            {"business_context_id": "555555555"},
        ):
            with self.subTest(change=change), self.assertRaises(RemoteRequestError):
                session._validate_business_loginpage_probe(**{**values, **change})

    async def test_authenticated_document_with_login_module_name_keeps_usable_session(self):
        session, client = self.session('<script>var login_form=true;</script>'
            '["DTSGInitialData",[],{"token":"fixture-token"}]',
            'https://business.facebook.com/latest/home?ref=login')
        self.assertEqual((await session.bootstrap()).fb_dtsg, 'fixture-token')
        self.assertEqual(len(client.calls), 1)

    async def test_inventory_bootstrap_does_not_download_cdn_modules(self):
        from unittest.mock import patch
        session, client = self.session(
            '["DTSGInitialData",[],{"token":"fixture-token"}]',
            'https://business.facebook.com/latest/home')
        # Exercise the real method, including its implicit bootstrap call.
        session.refresh_reviewed_modules = FacebookWebSession.refresh_reviewed_modules.__get__(session)
        session.defer_module_refresh = True
        with patch('app.contract_maintenance.module_refresh.refresh_from_document',
                   new_callable=AsyncMock) as collect:
            self.assertEqual((await session.bootstrap()).fb_dtsg, 'fixture-token')
        collect.assert_not_awaited()
        self.assertEqual(len(client.calls), 1)

    async def test_bootstrap_recovers_interrupted_text_on_same_route_with_document_budget(self):
        for error in (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            session, _ = self.session('', '')
            session.timeout_seconds = 15
            calls = []
            @asynccontextmanager
            async def get(url, **kwargs):
                calls.append((url, kwargs))
                async def text():
                    if len(calls) == 1:
                        raise error('SECRET connection failure')
                    return '["DTSGInitialData",[],{"token":"fixture-token"}]'
                yield SimpleNamespace(status=200, url=url, text=text)
            session._ensure_session = AsyncMock(return_value=SimpleNamespace(get=get))
            self.assertEqual((await session.bootstrap()).fb_dtsg, 'fixture-token')
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(calls[0][1]['request_timeout_seconds'], 30)
            session._fetch_dtsg_refresh_token.assert_not_awaited()

    async def test_bootstrap_keeps_a_larger_existing_document_budget(self):
        session, client = self.session('["DTSGInitialData",[],{"token":"fixture-token"}]',
            'https://business.facebook.com/latest/home')
        session.timeout_seconds = 45
        observed = []
        original = client.get
        def get(url, **kwargs):
            observed.append(kwargs)
            return original(url, **kwargs)
        client.get = get
        await session.bootstrap()
        self.assertEqual(observed[0]['request_timeout_seconds'], 45)

    async def test_html_read_budget_and_retry_are_scoped_to_document_requests(self):
        for url, navigation, expected in (
            ('https://business.facebook.com/latest/settings', False, True),
            ('https://adsmanager.facebook.com/adsmanager/manage/campaigns', True, True),
            ('https://www.facebook.com/', False, False),
            ('https://business.facebook.com/api/graphql/', False, False),
            ('https://static.xx.fbcdn.net/bundle.js', True, False)):
            session, _ = self.session('', '')
            session.timeout_seconds = 15
            calls = []
            @asynccontextmanager
            async def get(target, **kwargs):
                calls.append(kwargs)
                yield SimpleNamespace(status=200,url=target,charset='utf-8',headers={},
                    content=SimpleNamespace(read=AsyncMock(return_value=b'fixture')))
            session._ensure_session = AsyncMock(return_value=SimpleNamespace(get=get))
            self.assertEqual((await session.fetch_text(url, document_navigation=navigation))[1], 'fixture')
            self.assertEqual(calls[0].get('request_timeout_seconds'), 30 if expected else None)

    async def test_business_document_recovers_one_disconnect_without_a_post(self):
        session, _ = self.session('', '')
        calls = []
        @asynccontextmanager
        async def get(url, **kwargs):
            calls.append((url,kwargs))
            if len(calls) == 1:
                raise httpx.ConnectError('SECRET')
            yield SimpleNamespace(status=200,url=url,charset='utf-8',headers={},
                content=SimpleNamespace(read=AsyncMock(return_value=b'fixture')))
        session._ensure_session = AsyncMock(return_value=SimpleNamespace(get=get))
        self.assertEqual((await session.fetch_text('https://business.facebook.com/latest/home'))[1], 'fixture')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])

    async def test_document_timeout_does_not_start_another_origin_or_csrf_refresh(self):
        from fb_worker import RemoteRequestError
        session, _ = self.session('', '')
        calls = []
        @asynccontextmanager
        async def get(url, **kwargs):
            calls.append((url,kwargs))
            raise asyncio.TimeoutError('SECRET')
            yield
        session._ensure_session = AsyncMock(return_value=SimpleNamespace(get=get))
        with self.assertRaises(RemoteRequestError) as caught:
            await session.bootstrap()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1]['request_timeout_seconds'], 30)
        session._fetch_dtsg_refresh_token.assert_not_awaited()
        self.assertNotIn('SECRET', str(caught.exception))


class PreviousSessionReadRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_same_xs_candidate_needs_a_changed_meta_auth_cookie(self):
        actor = "123456789"
        current = {"c_user": actor, "xs": "fixture-current", "fr": "fixture-old"}
        allowed = {"c_user": actor, "xs": "fixture-current", "fr": "fixture-new"}
        session = FacebookWebSession(WebProfile(
            name="27", cookies=dict(current), proxy="http://fixture-proxy:80",
            user_agent="Fixture",
            recovery_cookie_candidates=[
                dict(current),
                {"c_user": actor, "xs": "fixture-current", "fr": "fixture-old", "locale": "en_US"},
                dict(allowed),
            ],
        ))
        candidates, reason = session._take_previous_session_candidates()
        self.assertEqual(reason, "")
        self.assertEqual(candidates, [allowed])

    class Client:
        def __init__(self, cookies):
            self.cookies = dict(cookies)
            self.closed = False

        def facebook_cookie_snapshot(self):
            return dict(self.cookies)

        async def close(self):
            self.closed = True

    async def test_read_recovery_tries_next_verified_same_actor_snapshot(self):
        actor = "123456789"
        current_cookies = {"c_user": actor, "xs": "fixture-current"}
        first_cookies = {"c_user": actor, "xs": "fixture-archive-one"}
        second_cookies = {"c_user": actor, "xs": "fixture-archive-two"}
        refreshed_cookies = {"c_user": actor, "xs": "fixture-refreshed"}
        session = FacebookWebSession(WebProfile(
            name="15", cookies=dict(current_cookies), proxy="http://fixture-proxy:80",
            user_agent="Fixture",
            recovery_cookie_candidates=[dict(first_cookies), dict(second_cookies)],
        ))
        old_client = self.Client(current_cookies)
        new_client = self.Client(refreshed_cookies)
        session.session = old_client
        rejected = SimpleNamespace(
            bootstrap=AsyncMock(side_effect=AuthenticationError("fixture auth gate")),
            session=None,
            defer_module_refresh=False,
        )
        verified = SimpleNamespace(
            profile=SimpleNamespace(cookies=dict(second_cookies)),
            session=new_client,
            _bootstrap=FacebookBootstrap(
                fb_dtsg="fixture-token", actor_id=actor, profile_actor_verified=True,
            ),
            _profile_session_identity_verified=True,
            _business_read_proofs={},
            _reviewed_module_ids={},
            bootstrap=AsyncMock(),
            graphql=AsyncMock(return_value={"data": {"adaccounts": []}}),
            close=AsyncMock(),
        )
        with patch("fb_worker.FacebookWebSession", side_effect=[rejected, verified]) as construct:
            result = await session._retry_scoped_read_with_previous_session(
                "26033539376343649",
                {"businessID": "987654321"},
                friendly_name="BusinessAdAccounts",
                endpoint_url="https://business.facebook.com/api/graphql/",
                request_envelope=None,
                business_context_id="987654321",
                business_loginpage_probe="READ_RK",
                read_timeout_seconds=30,
            )

        self.assertEqual(result, {"data": {"adaccounts": []}})
        self.assertEqual(construct.call_count, 2)
        rejected.bootstrap.assert_awaited_once_with(
            allow_business_loginpage_probe=True, require_actor_match=True,
        )
        verified.bootstrap.assert_awaited_once_with(
            allow_business_loginpage_probe=True, require_actor_match=True,
        )
        verified.graphql.assert_awaited_once()
        self.assertEqual(session.profile.cookies, refreshed_cookies)
        self.assertIs(session.session, new_client)
        self.assertTrue(old_client.closed)
    async def test_read_recovery_adopts_only_verified_same_actor_session(self):
        actor = "123456789"
        current_cookies = {"c_user": actor, "xs": "fixture-current"}
        previous_cookies = {"c_user": actor, "xs": "fixture-previous"}
        refreshed_cookies = {"c_user": actor, "xs": "fixture-refreshed"}
        session = FacebookWebSession(WebProfile(
            name="15", cookies=dict(current_cookies), proxy="http://fixture-proxy:80",
            user_agent="Fixture", recovery_cookies=dict(previous_cookies),
        ))
        old_client = self.Client(current_cookies)
        new_client = self.Client(refreshed_cookies)
        session.session = old_client
        verified = SimpleNamespace(
            profile=SimpleNamespace(cookies=dict(previous_cookies)),
            session=new_client,
            _bootstrap=FacebookBootstrap(
                fb_dtsg="fixture-token", actor_id=actor, profile_actor_verified=True,
            ),
            _profile_session_identity_verified=True,
            _business_read_proofs={("READ_RK", "business", "account", actor, "fixture"): 1.0},
            _reviewed_module_ids={"READ_RK": "fixture-doc"},
            bootstrap=AsyncMock(),
            graphql=AsyncMock(return_value={"data": {"adaccounts": []}}),
            close=AsyncMock(),
        )
        with patch("fb_worker.FacebookWebSession", return_value=verified):
            result = await session._retry_scoped_read_with_previous_session(
                "26033539376343649",
                {"businessID": "987654321"},
                friendly_name="BusinessAdAccounts",
                endpoint_url="https://business.facebook.com/api/graphql/",
                request_envelope=None,
                business_context_id="987654321",
                business_loginpage_probe="READ_RK",
                read_timeout_seconds=30,
            )

        self.assertEqual(result, {"data": {"adaccounts": []}})
        verified.bootstrap.assert_awaited_once_with(
            allow_business_loginpage_probe=True, require_actor_match=True,
        )
        verified.graphql.assert_awaited_once()
        self.assertEqual(verified.graphql.await_args.kwargs["business_loginpage_probe"], "READ_RK")
        self.assertEqual(session.profile.cookies, refreshed_cookies)
        self.assertIs(session.session, new_client)
        self.assertIsNone(session.profile.recovery_cookies)
        self.assertTrue(old_client.closed)
        self.assertTrue(session._profile_session_identity_verified)
        self.assertIn(("READ_RK", "business", "account", actor, "fixture"),
                      session._business_read_proofs)

    async def test_inventory_read_recovers_business_precheck_with_diagnostic_callback(self):
        session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": "123456789", "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
        ))
        session._validate_business_loginpage_probe = Mock(return_value="PRIVATE_RK_INVENTORY")
        session.bootstrap = AsyncMock(side_effect=AuthenticationError("fixture auth gate"))
        callback = AsyncMock()
        session._retry_scoped_read_with_previous_session = AsyncMock(
            return_value={"data": {"business": {"id": "987654321"}}},
        )
        session._ensure_session = AsyncMock()

        result = await session.graphql(
            "26033539376343649",
            {"businessID": "987654321"},
            friendly_name="ObservedBusinessAdAccounts",
            endpoint_url="https://business.facebook.com/api/graphql/",
            before_submit=callback,
            business_context_id="987654321",
            business_loginpage_probe="PRIVATE_RK_INVENTORY",
            private_inventory_query_contract={
                "operation_kind": "query", "business_id": "987654321",
                "doc_id": "26033539376343649", "friendly_name": "ObservedBusinessAdAccounts",
                "variables": {"businessID": "987654321"}, "asset_scope": True,
                "response_collection": "",
            },
        )

        self.assertEqual(result, {"data": {"business": {"id": "987654321"}}})
        session._retry_scoped_read_with_previous_session.assert_awaited_once()
        self.assertIs(session._retry_scoped_read_with_previous_session.await_args.kwargs["before_submit"], callback)
        callback.assert_not_awaited()
        session._ensure_session.assert_not_awaited()

    async def test_scoped_read_recovers_an_auth_gate_before_sending_its_post(self):
        session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": "123456789", "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
        ))
        session._validate_business_loginpage_probe = Mock(return_value="READ_RK")
        session.bootstrap = AsyncMock(side_effect=AuthenticationError("fixture auth gate"))
        session._retry_scoped_read_with_previous_session = AsyncMock(
            return_value={"data": {"adaccounts": []}},
        )
        session._ensure_session = AsyncMock()

        result = await session.graphql(
            "fixture-doc-id",
            {"businessID": "987654321"},
            friendly_name="BusinessAdAccounts",
            endpoint_url="https://business.facebook.com/api/graphql/",
            business_context_id="987654321",
            business_loginpage_probe="READ_RK",
        )

        self.assertEqual(result, {"data": {"adaccounts": []}})
        session._retry_scoped_read_with_previous_session.assert_awaited_once()
        self.assertIsNone(session._retry_scoped_read_with_previous_session.await_args.kwargs["read_timeout_seconds"])
        session._ensure_session.assert_not_awaited()

    async def test_scoped_read_recovers_graphql_auth_rejection_without_explicit_timeout(self):
        actor = "123456789"
        session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": actor, "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
        ))
        session._validate_business_loginpage_probe = Mock(return_value="READ_RK")
        bootstrap = FacebookBootstrap(fb_dtsg="fixture-token", actor_id=actor)
        session.bootstrap = AsyncMock(return_value=bootstrap)
        session._business_bootstrap = AsyncMock(return_value=bootstrap)
        response = SimpleNamespace(status=200, headers={},
            text=AsyncMock(return_value='{"error":"1357001"}'))

        @asynccontextmanager
        async def post(*_args, **_kwargs):
            yield response

        client = SimpleNamespace(post=Mock(side_effect=lambda *_args, **_kwargs: post()))
        session._ensure_session = AsyncMock(return_value=client)
        session._retry_scoped_read_with_previous_session = AsyncMock(
            return_value={"data": {"business": {"id": "987654321"}}},
        )

        result = await session.graphql(
            "26033539376343649",
            {"businessID": "987654321"},
            friendly_name="BusinessAdAccounts",
            endpoint_url="https://business.facebook.com/api/graphql/",
            business_context_id="987654321",
            business_loginpage_probe="READ_RK",
        )

        self.assertEqual(result, {"data": {"business": {"id": "987654321"}}})
        session.bootstrap.assert_awaited_once_with()
        session._retry_scoped_read_with_previous_session.assert_awaited_once()
        self.assertIsNone(session._retry_scoped_read_with_previous_session.await_args.kwargs["read_timeout_seconds"])
        session._ensure_session.assert_awaited_once()
        client.post.assert_called_once()

    async def test_inventory_read_retries_meta_auth_rejection_with_diagnostic_callback(self):
        actor = "123456789"
        session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": actor, "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
        ))
        session._validate_business_loginpage_probe = Mock(return_value="PRIVATE_RK_INVENTORY")
        bootstrap = FacebookBootstrap(fb_dtsg="fixture-token", actor_id=actor)
        session.bootstrap = AsyncMock(return_value=bootstrap)
        session._business_bootstrap = AsyncMock(return_value=bootstrap)
        response = SimpleNamespace(status=200, headers={},
            text=AsyncMock(return_value='{"error":"1357001"}'))

        @asynccontextmanager
        async def post(*_args, **_kwargs):
            yield response

        client = SimpleNamespace(post=Mock(side_effect=lambda *_args, **_kwargs: post()))
        session._ensure_session = AsyncMock(return_value=client)
        session._retry_scoped_read_with_previous_session = AsyncMock(
            return_value={"data": {"business": {"id": "987654321"}}},
        )
        callback = AsyncMock()

        result = await session.graphql(
            "26033539376343649",
            {"businessID": "987654321"},
            friendly_name="ObservedBusinessAdAccounts",
            endpoint_url="https://business.facebook.com/api/graphql/",
            before_submit=callback,
            business_context_id="987654321",
            business_loginpage_probe="PRIVATE_RK_INVENTORY",
            private_inventory_query_contract={
                "operation_kind": "query", "business_id": "987654321",
                "doc_id": "26033539376343649", "friendly_name": "ObservedBusinessAdAccounts",
                "variables": {"businessID": "987654321"}, "asset_scope": True,
                "response_collection": "",
            },
        )

        self.assertEqual(result, {"data": {"business": {"id": "987654321"}}})
        callback.assert_awaited_once()
        session._retry_scoped_read_with_previous_session.assert_awaited_once()
        self.assertIs(session._retry_scoped_read_with_previous_session.await_args.kwargs["before_submit"], callback)
        client.post.assert_called_once()

    async def test_previous_session_cannot_recover_mutations_or_another_actor(self):
        actor = "123456789"
        session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": actor, "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
            recovery_cookies={"c_user": "999999999", "xs": "fixture-previous"},
        ))
        with patch("fb_worker.FacebookWebSession") as construct:
            result = await session._retry_scoped_read_with_previous_session(
                "fixture-doc",
                {"businessID": "987654321"},
                friendly_name="CreateAdAccount",
                endpoint_url="https://business.facebook.com/api/graphql/",
                request_envelope=None,
                business_context_id="987654321",
                business_loginpage_probe="CREATE_RK",
                read_timeout_seconds=30,
            )
        self.assertIsNone(result)
        construct.assert_not_called()

        same_actor_session = FacebookWebSession(WebProfile(
            name="15", cookies={"c_user": actor, "xs": "fixture-current"},
            proxy="http://fixture-proxy:80", user_agent="Fixture",
            recovery_cookies={"c_user": "999999999", "xs": "fixture-previous"},
        ))
        with patch("fb_worker.FacebookWebSession") as construct:
            result = await same_actor_session._retry_scoped_read_with_previous_session(
                "fixture-doc",
                {"businessID": "987654321"},
                friendly_name="BusinessAdAccounts",
                endpoint_url="https://business.facebook.com/api/graphql/",
                request_envelope=None,
                business_context_id="987654321",
                business_loginpage_probe="READ_RK",
                read_timeout_seconds=30,
            )
        self.assertIsNone(result)
        construct.assert_not_called()


if __name__ == "__main__":
    unittest.main()
