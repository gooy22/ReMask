import asyncio
from pathlib import Path
import ssl
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded

from app.private_http import PrivateHttpClient, buffered_response
from app.session import MetaSession, ProfileContext, ProfileResolver
from fb_worker import FacebookBootstrap, FacebookWebSession, RemoteRequestError, WebProfile


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"first"
        yield b"second"


class PrivateHttpTests(unittest.IsolatedAsyncioTestCase):
    def make(self, proxy="http://127.0.0.1:1", name="fixture", uid="123"):
        client = PrivateHttpClient(profile_name=name, cookies={"c_user": uid},
            proxy=proxy, user_agent="fixture-UA", timeout_seconds=5, pool_size=2)
        self.addAsyncCleanup(client.close)
        return client

    @staticmethod
    def authenticated_document(actor="123"):
        return (f'<script>CurrentUserInitialData = {{"USER_ID":"{actor}"}};</script>'
                '["DTSGInitialData",[],{"token":"fixture-token"}]')

    async def mock(self, client, handler):
        cookies = client._client.cookies
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
            cookies=cookies, trust_env=False)

    async def test_transport_failure_logs_only_reviewed_phase_metadata(self):
        client = self.make()
        async def handler(request):
            trace = request.extensions['trace']
            for event in ('connection.connect_tcp.started','proxy.start_tls.complete',
                    'http2.send_request_headers.complete','http2.send_request_body.complete',
                    'http2.receive_response_headers.started'):
                await trace(event, {'SECRET':'credentials, headers and payload'})
            raise httpx.ReadTimeout('SECRET', request=request)
        await self.mock(client, handler)
        with self.assertLogs('remask_worker', level='WARNING') as logs:
            with self.assertRaises(httpx.ReadTimeout):
                async with client.post('https://business.facebook.com/api/graphql/?SECRET',
                        data={'token':'SECRET'}) as response:
                    await response.text()
        text = str(logs.output)
        self.assertIn('http2.receive_response_headers', text)
        self.assertIn('h2_body_sent', text)
        self.assertNotIn('SECRET', text)

    async def test_existing_caller_trace_is_preserved(self):
        client = self.make()
        trace = AsyncMock()
        def handler(request):
            self.assertIs(request.extensions['trace'], trace)
            self.assertEqual(request.extensions['fixture_option'], 'unchanged')
            return httpx.Response(200,text='ok')
        await self.mock(client, handler)
        async with client.get('https://www.facebook.com/',
                extensions={'trace':trace,'fixture_option':'unchanged'}) as response:
            self.assertEqual(await response.text(),'ok')

    async def test_proxy_tls_connect_error_retries_once_before_http2_send(self):
        client = self.make()
        requests = []
        async def handler(request):
            requests.append(request)
            trace = request.extensions['trace']
            await trace('connection.connect_tcp.started', {})
            if len(requests) == 1:
                await trace('proxy.start_tls.failed', {})
                raise httpx.ConnectError('proxy TLS setup failed', request=request)
            await trace('proxy.start_tls.complete', {})
            return httpx.Response(200, content=b'ok', extensions={'http_version': b'HTTP/2'})
        await self.mock(client, handler)
        with self.assertLogs('remask_worker', level='WARNING') as logs:
            async with client.post('https://business.facebook.com/api/graphql',
                    data={'operation':'READ_RK'}) as response:
                self.assertEqual(await response.text(), 'ok')
        self.assertEqual(len(requests), 2)
        self.assertIn('private_http_connect_retry', str(logs.output))

    async def test_proxy_tls_request_timeout_retries_once_before_http2_send(self):
        client = self.make()
        requests = []
        async def handler(request):
            requests.append(request)
            trace = request.extensions['trace']
            await trace('connection.connect_tcp.started', {})
            if len(requests) == 1:
                await trace('proxy.start_tls.failed', {})
                await asyncio.sleep(0.03)
            else:
                await trace('proxy.start_tls.complete', {})
            return httpx.Response(200, content=b'ok', extensions={'http_version': b'HTTP/2'})
        await self.mock(client, handler)
        with self.assertLogs('remask_worker', level='WARNING') as logs:
            async with client.post('https://business.facebook.com/api/graphql',
                    data={'operation':'READ_RK'}, request_timeout_seconds=0.01) as response:
                self.assertEqual(await response.text(), 'ok')
        self.assertEqual(len(requests), 2)
        self.assertIn('private_http_connect_retry', str(logs.output))

    async def test_connect_error_after_http2_headers_is_not_retried(self):
        client = self.make()
        requests = []
        async def handler(request):
            requests.append(request)
            trace = request.extensions['trace']
            for event in ('connection.connect_tcp.started', 'proxy.start_tls.complete',
                    'http2.send_request_headers.complete', 'http2.send_request_body.complete'):
                await trace(event, {})
            raise httpx.ConnectError('connection failed after send', request=request)
        await self.mock(client, handler)
        with self.assertLogs('remask_worker', level='WARNING') as logs:
            with self.assertRaises(httpx.ConnectError):
                async with client.post('https://business.facebook.com/api/graphql',
                        data={'operation':'CREATE'}) as response:
                    await response.text()
        self.assertEqual(len(requests), 1)
        self.assertNotIn('private_http_connect_retry', str(logs.output))

    async def test_cookies_are_profile_scoped_and_not_sent_to_proxy_check_or_cdn(self):
        seen = []
        def handler(request):
            seen.append((request.url.host, request.headers.get("cookie", "")))
            return httpx.Response(200, text="ok")
        for uid in ("123", "456"):
            client = self.make(uid=uid)
            await self.mock(client, handler)
            for host in ("www.facebook.com", "business.facebook.com", "api.ipify.org", "static.xx.fbcdn.net"):
                async with client.get("https://" + host + "/") as response:
                    self.assertEqual(await response.text(), "ok")
            self.assertEqual(seen[-4][1], "c_user=" + uid)
            self.assertEqual(seen[-3][1], "c_user=" + uid)
            self.assertEqual(seen[-2][1], "")
            self.assertEqual(seen[-1][1], "")

    async def test_bounded_stream_reads_do_not_drop_chunks_and_report_negotiated_version(self):
        client = self.make()
        await self.mock(client, lambda request: httpx.Response(200, stream=Chunks(),
            extensions={"http_version": b"HTTP/2"}))
        with self.assertLogs("remask_worker", level="INFO") as logs:
            async with client.get("https://www.facebook.com/?secret=token") as response:
                self.assertEqual(await response.content.read(3), b"fir")
                self.assertEqual(await response.content.read(4), b"stse")
                self.assertEqual(await response.content.read(), b"cond")
                self.assertEqual(response.http_version, "HTTP/2")
        self.assertIn("protocol=HTTP/2", str(logs.output))
        self.assertNotIn("token", str(logs.output))
        self.assertNotIn("c_user", str(logs.output))

    async def test_buffered_get_discards_partial_body_and_reuses_the_same_cookie_jar(self):
        class Interrupted(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'partial SECRET'
                raise httpx.ReadError('SECRET credentials')
        client = self.make(uid='123456')
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, stream=Interrupted(), headers={
                'Set-Cookie':'xs=renewed; Domain=.facebook.com; Path=/; Secure'},
                extensions={'http_version':b'HTTP/2'}) if len(requests) == 1 else httpx.Response(
                    200, content=b'complete', extensions={'http_version':b'HTTP/2'})
        await self.mock(client, handler)
        with self.assertLogs('remask_worker', level='WARNING') as logs:
            response, raw = await buffered_response('GET', lambda: client.get(
                'https://www.facebook.com/?SECRET', proxy=client.proxy), 100, retry_read=True)
        self.assertEqual(raw, b'complete')
        self.assertEqual(response.http_version, 'HTTP/2')
        self.assertEqual(len(requests), 2)
        self.assertIn('c_user=123456', requests[1].headers['cookie'])
        self.assertIn('xs=renewed', requests[1].headers['cookie'])
        self.assertNotIn('SECRET', str(logs.output))

    async def test_buffered_get_has_only_one_retry(self):
        for error in (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError):
            client = self.make()
            requests = []
            def handler(request):
                requests.append(request)
                raise error('SECRET', request=request)
            await self.mock(client, handler)
            with self.assertRaises(error):
                await buffered_response('GET', lambda: client.get('https://www.facebook.com/'),
                    100, retry_read=True)
            self.assertEqual(len(requests), 2)

    async def test_buffered_post_is_never_replayed_even_with_retry_flag(self):
        client = self.make()
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadError('disconnected after submission', request=request)
        await self.mock(client, handler)
        with self.assertRaises(httpx.ReadError):
            await buffered_response('POST', lambda: client.post('https://www.facebook.com/api/graphql/',
                data={'input':'single submission'}), 100, retry_read=True, retry_timeout=True)
        self.assertEqual(len(calls), 1)

    async def test_buffered_get_does_not_retry_timeout_proxy_auth_or_application_errors(self):
        for error in (httpx.ReadTimeout, httpx.ProxyError, ValueError, asyncio.CancelledError):
            client = self.make()
            calls = []
            def handler(request):
                calls.append(request)
                raise error('SECRET')
            await self.mock(client, handler)
            with self.assertRaises(error):
                await buffered_response('GET', lambda: client.get('https://www.facebook.com/'),
                    100, retry_read=True)
            self.assertEqual(len(calls), 1)

    async def test_buffered_get_retries_timeout_only_with_explicit_flag(self):
        client = self.make()
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout('SECRET', request=request)
            return httpx.Response(200, content=b'ok')
        await self.mock(client, handler)
        response, raw = await buffered_response('GET',
            lambda: client.get('https://www.facebook.com/'), 100,
            retry_read=True, retry_timeout=True)
        self.assertEqual((response.status, raw, len(calls)), (200, b'ok', 2))
        self.assertEqual(calls[0].url, calls[1].url)

    async def test_buffered_get_returns_http_rejections_without_retry(self):
        for status in (302, 400, 401, 403, 429, 500):
            client = self.make()
            calls = []
            def handler(request):
                calls.append(request)
                return httpx.Response(status, content=b'rejected')
            await self.mock(client, handler)
            response, raw = await buffered_response('GET', lambda: client.get('https://www.facebook.com/'),
                100, retry_read=True)
            self.assertEqual((response.status, raw, len(calls)), (status, b'rejected', 1))

    async def test_buffered_protocol_validation_precedes_read_and_is_not_retried(self):
        client = self.make()
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, content=b'refused protocol')
        await self.mock(client, handler)
        def validate(response):
            if response.http_version != 'HTTP/2':
                raise ValueError('HTTP/2 required')
        with self.assertRaises(ValueError):
            await buffered_response('GET', lambda: client.get('https://www.facebook.com/'),
                100, retry_read=True, validate=validate)
        self.assertEqual(len(calls), 1)

    async def test_document_navigation_retries_a_disconnected_get_once(self):
        web = FacebookWebSession(WebProfile('fixture', {'c_user':'123456'},
            'http://127.0.0.1:1', 'fixture-UA'))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.RemoteProtocolError('SECRET reset', request=request)
            return httpx.Response(200, text='complete')
        await self.mock(client, handler)
        result = await web.fetch_text('https://adsmanager.facebook.com/', document_navigation=True)
        self.assertEqual((result[0], result[1], len(calls)), (200, 'complete', 2))

    async def test_ads_manager_bootstrap_document_timeout_retries_once_when_opted_in(self):
        url = ('https://adsmanager.facebook.com/adsmanager/manage/campaigns'
               '?act=111111&business_id=222222')
        web = FacebookWebSession(WebProfile('fixture', {'c_user':'123456'},
            'http://127.0.0.1:1', 'fixture-UA'))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ReadTimeout('SECRET timeout', request=request)
            return httpx.Response(200, text='complete')
        await self.mock(client, handler)
        result = await web.fetch_text(url, document_navigation=True, retry_timeout=True)
        self.assertEqual((result[0], result[1], len(calls)), (200, 'complete', 2))

    async def test_timeout_retry_flag_is_ignored_for_other_document_urls(self):
        web = FacebookWebSession(WebProfile('fixture', {'c_user':'123456'},
            'http://127.0.0.1:1', 'fixture-UA'))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        calls = []
        def handler(request):
            calls.append(request)
            raise httpx.ReadTimeout('SECRET timeout', request=request)
        await self.mock(client, handler)
        with self.assertRaises(RemoteRequestError):
            await web.fetch_text('https://business.facebook.com/business/loginpage/',
                document_navigation=True, retry_timeout=True)
        self.assertEqual(len(calls), 1)

    async def test_library_info_logging_does_not_expose_session_or_redirect_url(self):
        client = self.make()
        await self.mock(client, lambda request: httpx.Response(200, text='ok',
            extensions={'http_version': b'HTTP/2'}))
        with self.assertLogs(level='INFO') as logs:
            async with client.get('https://business.facebook.com/billing/?session_id=fixture-secret') as response:
                await response.text()
        self.assertIn('host=business.facebook.com', str(logs.output))
        self.assertNotIn('fixture-secret', str(logs.output))
        self.assertNotIn('/billing/', str(logs.output))

    async def test_redirected_post_is_not_followed_or_replayed(self):
        calls = []
        client = self.make()
        def handler(request):
            calls.append(request)
            return httpx.Response(307, headers={"Location": "https://www.facebook.com/login"})
        await self.mock(client, handler)
        async with client.post("https://www.facebook.com/api/graphql/", data={"x": "1"}) as response:
            self.assertEqual(response.status, 307)
        self.assertEqual(len(calls), 1)

    async def test_document_transport_failure_keeps_safe_kind_without_retry_or_url(self):
        for failure in (httpx.ProxyError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            web = FacebookWebSession(WebProfile('fixture', {'c_user':'123'}, 'http://127.0.0.1:1', 'fixture-UA'))
            self.addAsyncCleanup(web.close)
            client = await web._ensure_session()
            calls = []
            def handler(request):
                calls.append(request)
                raise failure('SECRET proxy credentials and cookies', request=request)
            await self.mock(client, handler)
            with self.assertRaises(RemoteRequestError) as caught:
                await web.fetch_text('https://www.facebook.com/?session=SECRET')
            self.assertEqual(len(calls), 1)
            self.assertEqual(caught.exception.meta_payload,
                             {'transport_failure':{'kind':failure.__name__}})
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertNotIn('facebook.com', str(caught.exception))

    async def test_profile_cookie_refresh_uses_internal_endpoint_once(self):
        calls = []

        class Response:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            async def json(self, content_type=None):
                return {'ok':True,'updated':True}

        class Client:
            def __init__(self, **kwargs): self.kwargs = kwargs
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
            def post(self, url, *, json):
                calls.append((url, json, self.kwargs['headers']))
                return Response()

        resolver = ProfileResolver(
            'https://remask.invalid/ajax/pythonProfileContext.php',
            'fixture-internal-key',
        )
        with patch('app.session.aiohttp.ClientSession',
                   side_effect=lambda **kwargs: Client(**kwargs)):
            result = await resolver.persist_cookie_refresh(
                'fixture','123','old-xs',{'c_user':'123','xs':'new-xs'},
            )
        self.assertTrue(result)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], resolver.url)
        self.assertEqual(calls[0][1]['action'], 'refresh_profile_cookies')
        self.assertEqual(calls[0][1]['profile_id'], 'fixture')
        self.assertEqual(calls[0][1]['actor_id'], '123')
        self.assertEqual(calls[0][2]['X-Remask-Internal-Key'], 'fixture-internal-key')

    async def test_bootstrap_cookie_deletion_restores_session_before_inventory_reads(self):
        actor = '123456789'
        original = {'c_user': actor, 'xs': 'imported-xs'}
        web = FacebookWebSession(WebProfile('fixture', dict(original),
            'http://127.0.0.1:1', 'fixture-UA'))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()

        def handler(request):
            return httpx.Response(200, text=self.authenticated_document(actor), headers=[
                ('Set-Cookie', 'xs=; Domain=.facebook.com; Path=/; Max-Age=0; Secure'),
            ])

        await self.mock(client, handler)
        web.refresh_reviewed_modules = AsyncMock()
        bootstrap = await web.bootstrap()
        self.assertEqual(bootstrap.actor_id, actor)
        self.assertTrue(web._profile_session_identity_verified)
        self.assertEqual(client.facebook_cookie_snapshot(), original)
        await web.close()
        self.assertEqual(web.profile.cookies, original)

    async def test_business_loginpage_cookie_deletion_does_not_poison_profile_probe(self):
        actor = '123456789'
        profile = WebProfile('fixture', {'c_user': actor, 'xs': 'imported-xs'},
            'http://127.0.0.1:1', 'fixture-UA')
        web = FacebookWebSession(profile)
        self.addAsyncCleanup(web.close)
        web.ADS_MANAGER_URL = 'https://business.facebook.com/latest/home'
        client = await web._ensure_session()
        requests = []

        async def handler(request):
            cookie = request.headers.get('cookie', '')
            requests.append((request.url.host, request.url.path, cookie))
            if request.url.path == '/latest/home':
                return httpx.Response(302, headers=[
                    ('Location', 'https://business.facebook.com/business/loginpage/'),
                    ('Set-Cookie', 'xs=; Domain=.facebook.com; Path=/; Max-Age=0; Secure'),
                ])
            if request.url.path == '/business/loginpage/':
                return httpx.Response(200, text='<form id="login_form">')
            if request.url.host == 'www.facebook.com' and request.url.path == '/me':
                if f'c_user={actor}' in cookie and 'xs=imported-xs' in cookie:
                    return httpx.Response(200, text=self.authenticated_document(actor), headers=[
                        ('Set-Cookie', 'xs=; Domain=.facebook.com; Path=/; Max-Age=0; Secure'),
                    ])
                return httpx.Response(200, text='<form id="login_form">')
            raise AssertionError(f'unexpected profile probe {request.url.host}{request.url.path}')

        await self.mock(client, handler)
        bootstrap = await web.bootstrap(allow_business_loginpage_probe=True)
        self.assertTrue(bootstrap.profile_actor_verified)
        self.assertEqual(bootstrap.actor_id, actor)
        self.assertEqual(bootstrap.fb_dtsg, 'fixture-token')
        profile_probe = next(row for row in requests if row[0] == 'www.facebook.com')
        self.assertIn(f'c_user={actor}', profile_probe[2])
        self.assertIn('xs=imported-xs', profile_probe[2])
        self.assertEqual(client.facebook_cookie_snapshot(),
            {'c_user': actor, 'xs': 'imported-xs'})

    async def test_meta_cookie_refresh_is_saved_to_same_profile(self):
        context = ProfileContext(
            profile_id='fixture',
            cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1',
            user_agent='fixture-UA',
        )
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(
            200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=new-xs; Domain=.facebook.com; Path=/; Secure'},
        ))
        await web.bootstrap()
        await session.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'new-xs'})
        context._cookie_persistor.assert_awaited_once_with(
            'fixture','123','old-xs',{'c_user':'123','xs':'new-xs'},
        )

    async def test_meta_cookie_refresh_never_changes_profile_actor(self):
        context = ProfileContext(
            profile_id='fixture',
            cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1',
            user_agent='fixture-UA',
        )
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(
            200, text=self.authenticated_document(),
            headers=[('Set-Cookie','c_user=456; Domain=.facebook.com; Path=/; Secure'),
                     ('Set-Cookie','xs=other-xs; Domain=.facebook.com; Path=/; Secure')],
        ))
        await web.bootstrap()
        await session.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'old-xs'})
        context._cookie_persistor.assert_not_awaited()

    async def test_cookie_persistence_rejection_keeps_the_original_context(self):
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=False)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=new-xs; Domain=.facebook.com; Path=/; Secure'}))
        await web.bootstrap()
        await session.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'old-xs'})
        self.assertEqual(context._loaded_cookies, context.cookies)

    async def test_closing_unchanged_parallel_session_cannot_roll_back_newer_cookies(self):
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        first, second = MetaSession(context), MetaSession(context)
        self.addAsyncCleanup(first.close)
        self.addAsyncCleanup(second.close)
        first_web, second_web = await first.facebook_web(), await second.facebook_web()
        for web, headers in ((first_web, {'Set-Cookie':'xs=new-xs; Domain=.facebook.com; Path=/; Secure'}),
                             (second_web, {})):
            client = await web._ensure_session()
            await self.mock(client, lambda request, headers=headers: httpx.Response(200, text=self.authenticated_document(), headers=headers))
            await web.bootstrap()
        await first.close()
        await second.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'new-xs'})
        self.assertEqual(context._loaded_cookies, context.cookies)
        context._cookie_persistor.assert_awaited_once_with(
            'fixture','123','old-xs',{'c_user':'123','xs':'new-xs'})

    async def test_old_http_session_cannot_write_over_a_reloaded_saved_session(self):
        from types import SimpleNamespace
        from app.session_auth_refresh import refresh_saved_auth_context
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=old-client-rotation; Domain=.facebook.com; Path=/; Secure'}))
        await web.bootstrap()
        fresh = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'saved-fresh'},
            proxy=context.proxy, user_agent=context.user_agent)
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        self.assertTrue(await refresh_saved_auth_context(resolver, context))
        await session.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'saved-fresh'})
        context._cookie_persistor.assert_not_awaited()

    async def test_reloaded_saved_session_can_persist_its_next_meta_cookie_rotation(self):
        from types import SimpleNamespace
        from app.session_auth_refresh import refresh_saved_auth_context
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        fresh = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'saved-fresh'},
            proxy=context.proxy, user_agent=context.user_agent)
        resolver = SimpleNamespace(resolve=AsyncMock(return_value=fresh))
        self.assertTrue(await refresh_saved_auth_context(resolver, context))
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=rotated-fresh; Domain=.facebook.com; Path=/; Secure'}))
        await web.bootstrap()
        await session.close()
        context._cookie_persistor.assert_awaited_once_with(
            'fixture','123','saved-fresh',{'c_user':'123','xs':'rotated-fresh'})
        self.assertEqual(context.cookies, {'c_user':'123','xs':'rotated-fresh'})
        self.assertEqual(context._loaded_cookies, context.cookies)

    async def test_cookie_writeback_cannot_follow_a_changed_local_profile_id(self):
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        context._cookie_persistor = AsyncMock(return_value=True)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=new-xs; Domain=.facebook.com; Path=/; Secure'}))
        await web.bootstrap()
        context.profile_id = 'different-profile'
        await session.close()
        context._cookie_persistor.assert_not_awaited()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'old-xs'})

    async def test_completed_writeback_cannot_replace_a_newer_context_loaded_while_awaiting(self):
        context = ProfileContext(profile_id='fixture', cookies={'c_user':'123','xs':'old-xs'},
            proxy='http://127.0.0.1:1', user_agent='fixture-UA')
        context._loaded_cookies = dict(context.cookies)
        async def persist(*args):
            # The HTTP CAS completed, then another saved-session reload won
            # before its response returned to this client's close operation.
            context.cookies = {'c_user':'123','xs':'newer-saved-session'}
            context._loaded_cookies = dict(context.cookies)
            return True
        context._cookie_persistor = AsyncMock(side_effect=persist)
        session = MetaSession(context)
        self.addAsyncCleanup(session.close)
        web = await session.facebook_web()
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200, text=self.authenticated_document(),
            headers={'Set-Cookie':'xs=client-rotation; Domain=.facebook.com; Path=/; Secure'}))
        await web.bootstrap()
        await session.close()
        self.assertEqual(context.cookies, {'c_user':'123','xs':'newer-saved-session'})
        self.assertEqual(context._loaded_cookies, context.cookies)

    async def test_host_or_path_scoped_cookie_cannot_become_a_shared_profile_cookie(self):
        web = FacebookWebSession(WebProfile('fixture', {'c_user':'123','xs':'old-xs'},
            'http://127.0.0.1:1','fixture-UA'))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        await self.mock(client, lambda request: httpx.Response(200,text='scoped',headers=[
            ('Set-Cookie','xs=host-xs; Path=/; Secure'),
            ('Set-Cookie','xs=path-xs; Domain=.facebook.com; Path=/business/; Secure')]))
        await web.fetch_text('https://business.facebook.com/business/',document_navigation=True)
        await web.close()
        self.assertEqual(web.profile.cookies, {'c_user':'123','xs':'old-xs'})

    async def test_shadowed_business_auth_cookie_invalidates_snapshot_and_restores_profile(self):
        actor = "123456789"
        original = {"c_user": actor, "xs": "imported-xs"}
        client = PrivateHttpClient(profile_name="fixture", cookies=original,
            proxy="http://127.0.0.1:1", user_agent="fixture-UA",
            timeout_seconds=5, pool_size=2)
        self.addAsyncCleanup(client.close)
        client._client.cookies.set("xs", "business-shadow", domain="business.facebook.com", path="/")

        self.assertEqual(client.facebook_cookie_snapshot(), {})
        FacebookWebSession._restore_invalid_profile_cookie_snapshot(client, actor)
        self.assertEqual(client.facebook_cookie_snapshot(), original)

        seen = []
        await self.mock(client, lambda request: (seen.append(request.headers.get("cookie", ""))
            or httpx.Response(200, text="ok")))
        async with client.get("https://business.facebook.com/business/loginrequest/fb/") as response:
            self.assertEqual(await response.text(), "ok")
        self.assertIn("c_user=" + actor, seen[-1])
        self.assertIn("xs=imported-xs", seen[-1])
        self.assertNotIn("business-shadow", seen[-1])

    async def test_graphql_multipart_stream_keeps_auth_and_claim_before_one_post(self):
        import io
        web = FacebookWebSession(WebProfile('fixture', {'c_user':'123'}, 'http://127.0.0.1:1', 'fixture-UA'))
        self.addAsyncCleanup(web.close)
        web.bootstrap = AsyncMock(return_value=FacebookBootstrap('fresh-dtsg','123'))
        client = await web._ensure_session()
        events=[]
        async def claim():
            events.append('claim')
        async def handler(request):
            events.append('post')
            self.assertIn('multipart/form-data; boundary=',request.headers['content-type'])
            body=await request.aread()
            for value in (b'fresh-dtsg',b'fixture image bytes',b'name="file"; filename="fixture.png"',b'123456789'):
                self.assertIn(value,body)
            return httpx.Response(200,json={'data':{'upload':{'hash':'fixture_hash'}}})
        await self.mock(client,handler)
        result=await web.graphql('123456789',{'account':'123'},endpoint_url='https://www.facebook.com/api/graphql/',
            before_submit=claim,multipart_files={'file':('fixture.png',io.BytesIO(b'fixture image bytes'),'image/png')})
        self.assertEqual(result['data']['upload']['hash'],'fixture_hash')
        self.assertEqual(events,['claim','post'])

    async def test_fresh_set_cookie_is_used_on_next_profile_request(self):
        client = self.make()
        seen = []
        def handler(request):
            seen.append(request.headers.get("cookie", ""))
            return httpx.Response(200, text="ok", headers={
                "Set-Cookie": "xs=fresh-session; Domain=.facebook.com; Path=/; Secure"})
        await self.mock(client, handler)
        for _ in range(2):
            async with client.get("https://business.facebook.com/") as response:
                await response.text()
        self.assertNotIn("xs=", seen[0])
        self.assertIn("xs=fresh-session", seen[1])

    async def test_failed_durable_intent_prevents_http2_post(self):
        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"}, "http://127.0.0.1:1", "fixture-UA"))
        self.addAsyncCleanup(web.close)
        web._bootstrap = FacebookBootstrap("fresh-dtsg", "123")
        web.bootstrap = AsyncMock(return_value=web._bootstrap)
        client = await web._ensure_session()
        handler = AsyncMock()
        await self.mock(client, handler)
        with self.assertRaises(RemoteRequestError) as caught:
            await web.graphql("123456789", {"input": {"actor_id": "123"}},
                friendly_name="CreatePageMutation", endpoint_url="https://www.facebook.com/api/graphql/",
                before_submit=AsyncMock(side_effect=OSError("intent storage unavailable")))
        self.assertFalse(caught.exception.request_may_have_been_sent)
        handler.assert_not_awaited()

    async def test_proxy_change_is_rejected_before_dispatch(self):
        client = self.make("http://127.0.0.1:1")
        handler = AsyncMock()
        await self.mock(client, handler)
        with self.assertRaises(httpx.ProxyError):
            async with client.post("https://www.facebook.com/", proxy=None):
                self.fail("must not dispatch")
        handler.assert_not_awaited()

    async def test_missing_proxy_blocks_every_request_before_dispatch(self):
        client = self.make(proxy=None)
        handler = AsyncMock()
        await self.mock(client, handler)
        for method in ("GET", "POST"):
            for host in ("business.facebook.com", "static.xx.fbcdn.net"):
                with self.subTest(method=method, host=host):
                    with self.assertRaises(httpx.ProxyError):
                        async with client.request(method, "https://" + host + "/"):
                            self.fail("missing proxy must block dispatch")
        handler.assert_not_awaited()

    async def test_failed_proxy_never_retries_or_connects_directly(self):
        # Every listener and DNS result is local. A direct connection or an
        # environment-proxy fallback would hit the destination trap below.
        direct, tunnels, tasks = [], [], set()
        async def destination(reader, writer):
            direct.append(True)
            writer.close()
            await writer.wait_closed()
        origin = await asyncio.start_server(destination, "127.0.0.1", 0)
        origin_port = origin.sockets[0].getsockname()[1]
        actual_dns = socket.getaddrinfo
        def local_dns(host, port, *args, **kwargs):
            hostname = host.decode() if isinstance(host, bytes) else str(host)
            if hostname.endswith("facebook.com"):
                host = "127.0.0.1"
            return actual_dns(host, port, *args, **kwargs)
        try:
            for failure in ("407", "503", "close", "timeout", "refused"):
                async def reject(reader, writer):
                    tasks.add(asyncio.current_task())
                    try:
                        tunnels.append(await reader.readuntil(b"\r\n\r\n"))
                        if failure == "timeout":
                            await reader.read()
                        elif failure != "close":
                            writer.write(("HTTP/1.1 " + failure + " Proxy unavailable\r\n"
                                          "Content-Length: 0\r\nConnection: close\r\n\r\n").encode())
                            await writer.drain()
                    finally:
                        writer.close()
                        await writer.wait_closed()
                server = await asyncio.start_server(reject, "127.0.0.1", 0)
                port = server.sockets[0].getsockname()[1]
                if failure == "refused":
                    server.close()
                    await server.wait_closed()
                client = self.make("http://fixture-user:fixture-password@127.0.0.1:" + str(port))
                # Exercise the proxy's declared failure, not a race with DNS
                # or CONNECT scheduling on a loaded CI runner. Keep the
                # deadline finite for the intentionally silent proxy too.
                client.timeout_seconds = 1.0
                before = len(tunnels)
                try:
                    with patch("socket.getaddrinfo", side_effect=local_dns), patch.dict("os.environ", {
                        "HTTP_PROXY": "http://127.0.0.1:" + str(origin_port),
                        "HTTPS_PROXY": "http://127.0.0.1:" + str(origin_port),
                        "ALL_PROXY": "http://127.0.0.1:" + str(origin_port),
                        "NO_PROXY": "business.facebook.com",
                    }), self.assertLogs("remask_worker", level="INFO") as logs:
                        for method in ("GET", "POST"):
                            with self.subTest(failure=failure, method=method):
                                with self.assertRaises((httpx.TransportError, TimeoutError)):
                                    async with client.request(method,
                                            f"https://business.facebook.com:{origin_port}/api/graphql/"):
                                        self.fail("failed proxy must not reach destination")
                        self.assertNotIn("fixture-password", str(logs.output))
                        self.assertNotIn("c_user", str(logs.output))
                    self.assertEqual(direct, [])
                    self.assertEqual(len(tunnels) - before, 0 if failure == "refused" else 2)
                    self.assertTrue(all(b"cookie:" not in row.lower() for row in tunnels))
                finally:
                    await client.close()
                    server.close()
                    await server.wait_closed()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            origin.close()
            await origin.wait_closed()

    async def test_bootstrap_network_failure_is_not_session_expiry_or_origin_retry(self):
        for failure in (httpx.ProxyError("407 fixture-secret"),
                        httpx.ConnectError("fixture-secret"),
                        httpx.ConnectTimeout("fixture-secret"), TimeoutError()):
            with self.subTest(failure=type(failure).__name__):
                web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"},
                    "http://127.0.0.1:1", "fixture-UA"))
                self.addAsyncCleanup(web.close)
                client = await web._ensure_session()
                calls = []
                def handler(request):
                    calls.append(request)
                    raise failure
                await self.mock(client, handler)
                web._fetch_dtsg_refresh_token = AsyncMock()
                with self.assertRaises(RemoteRequestError) as caught:
                    await web.bootstrap()
                self.assertEqual(len(calls), 2 if isinstance(failure, httpx.ConnectError) else 1)
                self.assertEqual({str(request.url) for request in calls}, {web.ADS_MANAGER_URL})
                self.assertEqual({request.method for request in calls}, {'GET'})
                self.assertEqual({request.headers.get('cookie') for request in calls}, {'c_user=123'})
                web._fetch_dtsg_refresh_token.assert_not_awaited()
                self.assertFalse(caught.exception.request_may_have_been_sent)
                self.assertEqual(caught.exception.transport_stage, "bootstrap")
                self.assertEqual(caught.exception.meta_payload["transport_failure"]["kind"],
                                 type(failure).__name__)
                self.assertNotIn("fixture-secret", str(caught.exception))
                self.assertIsNone(web._bootstrap)

    async def test_proxy_recovery_can_bootstrap_again_without_replacing_cookies(self):
        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123", "xs": "fixture-xs"},
            "http://127.0.0.1:1", "fixture-UA"))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        calls = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ProxyError("407")
            return httpx.Response(200, text='["DTSGInitialData",[],{"token":"fixture-dtsg"}]')
        await self.mock(client, handler)
        with self.assertRaises(RemoteRequestError):
            await web.bootstrap()
        bootstrap = await web.bootstrap()
        self.assertEqual(bootstrap.fb_dtsg, "fixture-dtsg")
        self.assertEqual(bootstrap.actor_id, "123")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].headers["cookie"], calls[1].headers["cookie"])
        self.assertEqual(web.profile.cookies["xs"], "fixture-xs")

    async def test_launch_proxy_failure_stops_before_read_or_create_without_browser(self):
        from types import SimpleNamespace
        import time
        from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore
        from app.provisioning.meta_transport import MetaTransportRouter
        from app.provisioning.models import ProvisioningError
        from app.provisioning.state import ProvisioningStateStore
        from tests.test_private_launch_service import contracts
        import json

        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"},
            "http://127.0.0.1:1", "fixture-UA"))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        handler = AsyncMock(side_effect=httpx.ProxyError("407"))
        await self.mock(client, handler)
        web.graphql = AsyncMock()
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=web),
            browser_lease=lambda **kwargs: self.fail("proxy outage must not start Chromium"))
        with tempfile.TemporaryDirectory() as directory:
            service = PrivateLaunchService(ProvisioningStateStore(str(Path(directory)/"state.sqlite")),
                contracts=PrivateLaunchContractStore(json.dumps(contracts())))
            context = SimpleNamespace(profile_id="fixture", inventory_updated_at=int(time.time()),
                ad_accounts=[{"ad_account_id": "123456789", "business_id": "987654321", "account_status": 1}])
            with self.assertRaises(ProvisioningError) as caught:
                await service._live_preflight(context=context, profile_id="fixture",
                    business_id="987654321", ad_account_id="123456789", page_id="111222333",
                    transport=MetaTransportRouter(session, private_only=True))
            self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_HTTP_PREFLIGHT_FAILED")
            self.assertTrue(caught.exception.retryable)
            handler.assert_awaited_once()
            web.graphql.assert_not_awaited()
            self.assertTrue(web.private_only)

    async def test_stream_reset_after_create_retains_ambiguous_intent_without_retry(self):
        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"}, "http://127.0.0.1:1", "fixture-UA"))
        self.addAsyncCleanup(web.close)
        web._bootstrap = FacebookBootstrap("fresh-dtsg", "123")
        web.bootstrap = AsyncMock(return_value=web._bootstrap)
        client = await web._ensure_session()
        calls = []
        class Reset(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'{"data":'
                raise httpx.RemoteProtocolError("stream reset")
        def handler(request):
            calls.append(request)
            return httpx.Response(200, stream=Reset(), extensions={"http_version": b"HTTP/2"})
        await self.mock(client, handler)
        intent = AsyncMock()
        with self.assertRaises(RemoteRequestError) as caught:
            await web.graphql("123456789", {"input": {"actor_id": "123"}},
                friendly_name="CreatePageMutation", endpoint_url="https://www.facebook.com/api/graphql/",
                before_submit=intent)
        intent.assert_awaited_once()
        self.assertEqual(len(calls), 1)
        self.assertTrue(caught.exception.request_may_have_been_sent)

    async def test_total_deadline_includes_body_consumption(self):
        client = self.make()
        client.timeout_seconds = 0.01
        class Slow(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(0.1)
                yield b"late"
        await self.mock(client, lambda request: httpx.Response(200, stream=Slow()))
        with self.assertRaises(TimeoutError):
            async with client.get("https://www.facebook.com/") as response:
                await response.text()

    async def test_real_tls_http2_negotiation_through_http11_connect_proxy(self):
        # Exercise ALPN, h2 frames and the proxy tunnel, rather than mocking a
        # response's version. Every socket is local; no Meta credentials used.
        with tempfile.TemporaryDirectory() as directory:
            cert, key = Path(directory) / "cert.pem", Path(directory) / "key.pem"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-nodes", "-days", "1", "-subj", "/CN=business.facebook.com",
                "-addext", "subjectAltName=DNS:business.facebook.com", "-keyout", str(key),
                "-out", str(cert)], check=True, capture_output=True)
            server_ssl = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ssl.load_cert_chain(cert, key)
            server_ssl.set_alpn_protocols(["h2"])
            streams, protocols, tunnels = [], [], []
            tasks = set()
            async def origin(reader, writer):
                tasks.add(asyncio.current_task())
                protocols.append(writer.get_extra_info("ssl_object").selected_alpn_protocol())
                conn = H2Connection(config=H2Configuration(client_side=False, header_encoding="utf-8"))
                conn.initiate_connection()
                writer.write(conn.data_to_send())
                try:
                    while data := await reader.read(65536):
                        for event in conn.receive_data(data):
                            if isinstance(event, RequestReceived):
                                streams.append(dict(event.headers))
                            if isinstance(event, DataReceived):
                                conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                            if isinstance(event, StreamEnded):
                                conn.send_headers(event.stream_id, [(":status", "200"), ("content-type", "text/plain")])
                                conn.send_data(event.stream_id, b"confirmed", end_stream=True)
                        writer.write(conn.data_to_send())
                        await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()
            origin_server = await asyncio.start_server(origin, "127.0.0.1", 0, ssl=server_ssl)
            origin_port = origin_server.sockets[0].getsockname()[1]
            async def proxy(reader, writer):
                tasks.add(asyncio.current_task())
                header = await reader.readuntil(b"\r\n\r\n")
                tunnels.append(header.split(b"\r\n")[0])
                upstream_r, upstream_w = await asyncio.open_connection("127.0.0.1", origin_port)
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await writer.drain()
                async def relay(source, dest):
                    try:
                        while data := await source.read(65536):
                            dest.write(data)
                            await dest.drain()
                    finally:
                        dest.close()
                await asyncio.gather(relay(reader, upstream_w), relay(upstream_r, writer))
            proxy_server = await asyncio.start_server(proxy, "127.0.0.1", 0)
            proxy_url = "http://127.0.0.1:" + str(proxy_server.sockets[0].getsockname()[1])
            verified_ssl = ssl.create_default_context(cafile=str(cert))
            actual_transport = httpx.AsyncHTTPTransport
            def trusted_transport(**kwargs):
                return actual_transport(verify=verified_ssl, **kwargs)
            try:
                with patch("app.private_http.httpx.AsyncHTTPTransport", side_effect=trusted_transport):
                    client = self.make(proxy_url)
                for method in ("GET", "POST"):
                    async with client.request(method, f"https://business.facebook.com:{origin_port}/", proxy=proxy_url) as response:
                        self.assertEqual(response.http_version, "HTTP/2")
                        self.assertEqual(await response.text(), "confirmed")
                await client.close()
                self.assertEqual(protocols, ["h2"])
                self.assertEqual(len(tunnels), 1)
                self.assertTrue(tunnels[0].endswith(b" HTTP/1.1"))
                self.assertEqual([row[":method"] for row in streams], ["GET", "POST"])
                self.assertTrue(all(row.get("cookie") == "c_user=123" for row in streams))
            finally:
                proxy_server.close()
                origin_server.close()
                await proxy_server.wait_closed()
                await origin_server.wait_closed()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_http11_only_server_is_reported_honestly(self):
        calls = []
        async def origin(reader, writer):
            calls.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        try:
            client = self.make("http://127.0.0.1:" + str(server.sockets[0].getsockname()[1]))
            async with client.get("http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])) as response:
                self.assertEqual(response.http_version, "HTTP/1.1")
                self.assertEqual(await response.text(), "ok")
            self.assertEqual(len(calls), 1)
        finally:
            server.close()
            await server.wait_closed()

    async def test_all_facebook_origins_use_http2_only_proxy_transport(self):
        client = self.make()
        transports = set()
        for host in ('facebook.com', 'business.facebook.com', 'www.facebook.com',
                     'secure.facebook.com', 'adsmanager.facebook.com', 'adsmanager-graph.facebook.com',
                     'm.facebook.com', 'static.xx.fbcdn.net'):
            with self.subTest(host=host):
                transport = client._client._transport_for_url(httpx.URL('https://' + host + '/api/graphql/'))
                self.assertTrue(transport._pool._http2)
                self.assertFalse(transport._pool._http1)
                self.assertEqual(transport._pool._retries, 0)
                transports.add(transport)
        self.assertEqual(len(transports), 1)  # one origin transport/pool per profile
        for host in ('api.ipify.org', 'evilfbcdn.net', 'fbcdn.net.evil.test', 'evilfacebook.com', 'facebook.com.evil.test'):
            with self.subTest(host=host):
                other = client._client._transport_for_url(httpx.URL('https://' + host))
                self.assertTrue(other._pool._http1)
                self.assertEqual(other._pool._retries, 0)


    async def test_targeting_read_uses_verified_profile_bootstrap_without_business_preflight(self):
        web = FacebookWebSession(WebProfile(
            "fixture", {"c_user": "123", "xs": "fixture-xs"},
            "http://127.0.0.1:1", "fixture-UA",
        ))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        web.bootstrap = AsyncMock(return_value=FacebookBootstrap(
            "fresh-dtsg", "123", source_url="https://www.facebook.com/me",
            profile_actor_verified=True,
        ))
        web._business_bootstrap = AsyncMock(side_effect=AssertionError(
            "read-only targeting must rely on its exact scoped response"
        ))
        requests = []

        async def handler(request):
            requests.append(request)
            form = dict(httpx.QueryParams((await request.aread()).decode()))
            self.assertEqual(form["fb_dtsg"], "fresh-dtsg")
            self.assertEqual(form["av"], "123")
            self.assertEqual(form["__user"], "123")
            self.assertEqual(form["doc_id"], "123456789")
            return httpx.Response(200, json={"data": {"result": True}})

        await self.mock(client, handler)
        with patch("app.contract_maintenance.module_refresh.effective_doc_id",
                   side_effect=lambda _web, _name, doc_id: doc_id):
            result = await web.graphql(
                "123456789", {"businessID": "456789", "adAccountID": "789"},
                friendly_name="InterestsSearchQuery",
                endpoint_url="https://business.facebook.com/api/graphql/",
                business_context_id="456789",
                profile_bootstrap_read_only=True,
            )
        self.assertEqual(result, {"data": {"result": True}})
        web.bootstrap.assert_awaited_once_with(
            allow_business_loginpage_probe=True, require_actor_match=True,
        )
        web._business_bootstrap.assert_not_awaited()
        self.assertEqual(len(requests), 1)

    async def test_targeting_profile_bootstrap_rejects_write_hooks_before_dispatch(self):
        web = FacebookWebSession(WebProfile(
            "fixture", {"c_user": "123", "xs": "fixture-xs"},
            "http://127.0.0.1:1", "fixture-UA",
        ))
        self.addAsyncCleanup(web.close)
        client = await web._ensure_session()
        web.bootstrap = AsyncMock(return_value=FacebookBootstrap(
            "fresh-dtsg", "123", source_url="https://www.facebook.com/me",
            profile_actor_verified=True,
        ))
        handler = AsyncMock()
        await self.mock(client, handler)
        with patch("app.contract_maintenance.module_refresh.effective_doc_id",
                   side_effect=lambda _web, _name, doc_id: doc_id):
            with self.assertRaises(RemoteRequestError):
                await web.graphql(
                    "123456789", {"businessID": "456789", "adAccountID": "789"},
                    friendly_name="InterestsSearchQuery",
                    endpoint_url="https://business.facebook.com/api/graphql/",
                    business_context_id="456789",
                    before_submit=AsyncMock(),
                    profile_bootstrap_read_only=True,
                )
        web.bootstrap.assert_not_awaited()
        handler.assert_not_awaited()
