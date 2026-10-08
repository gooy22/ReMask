import asyncio
from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, StreamEnded

from app.private_http import PrivateHttpClient
from fb_worker import FacebookBootstrap, FacebookWebSession, RemoteRequestError, WebProfile


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"first"
        yield b"second"


class PrivateHttpTests(unittest.IsolatedAsyncioTestCase):
    def make(self, proxy=None, name="fixture", uid="123"):
        client = PrivateHttpClient(profile_name=name, cookies={"c_user": uid},
            proxy=proxy, user_agent="fixture-UA", timeout_seconds=5, pool_size=2)
        self.addAsyncCleanup(client.close)
        return client

    async def mock(self, client, handler):
        cookies = client._client.cookies
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
            cookies=cookies, trust_env=False)

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
        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"}, None, "fixture-UA"))
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

    async def test_stream_reset_after_create_retains_ambiguous_intent_without_retry(self):
        web = FacebookWebSession(WebProfile("fixture", {"c_user": "123"}, None, "fixture-UA"))
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
            client = self.make()
            async with client.get("http://127.0.0.1:" + str(server.sockets[0].getsockname()[1])) as response:
                self.assertEqual(response.http_version, "HTTP/1.1")
                self.assertEqual(await response.text(), "ok")
            self.assertEqual(len(calls), 1)
        finally:
            server.close()
            await server.wait_closed()

    async def test_business_origin_has_http2_only_transport_without_retries(self):
        client = self.make()
        transport = client._client._transport_for_url(httpx.URL('https://business.facebook.com/api/graphql/'))
        self.assertTrue(transport._pool._http2)
        self.assertFalse(transport._pool._http1)
        self.assertEqual(transport._pool._retries, 0)
        other = client._client._transport_for_url(httpx.URL('https://api.ipify.org'))
        self.assertTrue(other._pool._http1)
