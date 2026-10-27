import unittest
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
from unittest.mock import AsyncMock

from fb_worker import AuthenticationError, RemoteRequestError, FacebookBootstrap, FacebookWebSession, WebProfile, _has_login_form


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

    async def test_actual_login_redirect_stops_before_other_routes_refresh_or_post(self):
        session, client = self.session('<form id="login_form">',
            'https://business.facebook.com/business/loginpage/?secret=must-not-leak')
        with self.assertRaises(AuthenticationError) as caught:
            await session.bootstrap()
        self.assertEqual(len(client.calls), 1)
        session._fetch_dtsg_refresh_token.assert_not_awaited()
        self.assertFalse(caught.exception.request_may_have_been_sent)
        from app.private_auth import private_auth_error
        self.assertEqual(private_auth_error(caught.exception).code, 'BUSINESS_LOGIN_GATE')
        self.assertNotIn('must-not-leak', str(caught.exception.meta_payload))

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

        with self.assertRaises(AuthenticationError):
            await session.bootstrap()
        self.assertEqual(len(calls), 1)
        bootstrap = await session.bootstrap(allow_business_loginpage_probe=True)
        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, expected_actor)
        self.assertEqual(bootstrap.fb_dtsg, "profile-token")
        self.assertEqual([call[0] for call in calls], [
            "https://business.facebook.com/latest/home",
            "https://www.facebook.com/me",
        ])
        session._fetch_dtsg_refresh_token.assert_not_awaited()
        session.refresh_reviewed_modules.assert_not_awaited()

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

    async def test_business_loginpage_probe_rejects_actor_mismatch_and_other_login_routes(self):
        calls = []
        class Response:
            status = 200
            def __init__(self, url, body): self.url, self.body = url, body
            async def text(self): return self.body
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        class Client:
            def __init__(self, profile_url, body):
                self.profile_url, self.body = profile_url, body
            def get(self, url, **kwargs):
                calls.append(url)
                return Response(self.profile_url, self.body)

        session = FacebookWebSession(WebProfile(name="15",
            cookies={"c_user": "123456789"}, proxy=None, user_agent="fixture"))
        bad = Client("https://www.facebook.com/me",
            '<script>CurrentUserInitialData = {"USER_ID":"987654321"};</script>'
            '["DTSGInitialData",[],{"token":"profile-token"}]')
        session._ensure_session = AsyncMock(return_value=bad)
        with self.assertRaises(AuthenticationError) as caught:
            await session.bootstrap(allow_business_loginpage_probe=True)
        self.assertFalse(caught.exception.request_may_have_been_sent)
        self.assertFalse(caught.exception.meta_payload["business_precheck"][-1]["actor_verified"])
        self.assertEqual(calls, [
            "https://www.facebook.com/me",
            "https://www.facebook.com/settings",
            "https://www.facebook.com/",
        ])

        calls.clear()
        other = Client("https://www.facebook.com/login.php", '<form id="login_form">')
        session._ensure_session = AsyncMock(return_value=other)
        session.invalidate_bootstrap()
        with self.assertRaises(AuthenticationError):
            await session.bootstrap(allow_business_loginpage_probe=True)
        self.assertEqual(calls, ["https://www.facebook.com/me"])

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


if __name__ == "__main__":
    unittest.main()
