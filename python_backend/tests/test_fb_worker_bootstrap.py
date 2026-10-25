import unittest
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
from unittest.mock import AsyncMock

from fb_worker import AuthenticationError, FacebookWebSession, WebProfile, _has_login_form


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
