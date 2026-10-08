"""Profile-scoped HTTP/2 transport for the existing private action engine.

Keep streaming, proxy binding and the context-manager interface at this boundary.
Protocol negotiation never triggers a second application request.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from http.cookiejar import Cookie
import logging
from typing import Any

import httpx

log = logging.getLogger("remask_worker")


class _Body:
    def __init__(self, response: httpx.Response) -> None:
        self._chunks = response.aiter_bytes()
        self._pending = b""

    async def read(self, size: int = -1) -> bytes:
        data = bytearray(self._pending)
        self._pending = b""
        while size < 0 or len(data) < size:
            try:
                data.extend(await anext(self._chunks))
            except StopAsyncIteration:
                break
        if size >= 0:
            self._pending = bytes(data[size:])
            del data[size:]
        return bytes(data)


class PrivateResponse:
    def __init__(self, response: httpx.Response) -> None:
        self.status = response.status_code
        self.headers = response.headers
        self.url = response.url
        self.charset = response.charset_encoding
        self.http_version = response.http_version
        self.content = _Body(response)

    async def text(self) -> str:
        # Bootstrap/GraphQL bodies have a finite decompressed budget too.
        raw = await self.content.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise httpx.DecodingError("Private response exceeds body budget")
        try:
            return raw.decode(self.charset or "utf-8", errors="replace")
        except LookupError:
            return raw.decode("utf-8", errors="replace")


class PrivateHttpClient:
    def __init__(self, *, profile_name: str, cookies: dict[str, str],
                 proxy: str | None, user_agent: str, timeout_seconds: int,
                 pool_size: int) -> None:
        self.proxy = proxy
        self.profile_name = profile_name
        self.timeout_seconds = timeout_seconds
        limits = httpx.Limits(max_connections=pool_size,
                              max_keepalive_connections=pool_size)
        transport = httpx.AsyncHTTPTransport(
            proxy=proxy, http2=True, retries=0, limits=limits, trust_env=False,
        )
        jar = httpx.Cookies()
        for name, value in cookies.items():
            # Do not forward Facebook credentials to proxy-check/CDN hosts.
            jar.jar.set_cookie(Cookie(
                version=0, name=name, value=value, port=None,
                port_specified=False, domain=".facebook.com",
                domain_specified=True, domain_initial_dot=True, path="/",
                path_specified=True, secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False,
            ))
        # Business Settings operations must negotiate HTTP/2. Disallow HTTP/1
        # at the origin transport, with no application replay. CONNECT
        # to the profile proxy may still use its own supported protocol.
        business_transport = httpx.AsyncHTTPTransport(
            proxy=proxy, http1=False, http2=True, retries=0, limits=limits, trust_env=False,
        )
        self._client = httpx.AsyncClient(
            transport=transport, mounts={"https://business.facebook.com": business_transport}, http2=True, trust_env=False,
            timeout=httpx.Timeout(timeout_seconds), cookies=jar,
            headers={"User-Agent": user_agent,
                     "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"},
        )

    @property
    def closed(self) -> bool:
        return self._client.is_closed

    async def close(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def request(self, method: str, url: str, **kwargs: Any):
        requested_proxy = kwargs.pop("proxy", self.proxy)
        if requested_proxy != self.proxy:
            raise httpx.ProxyError("Private request cannot change profile proxy")
        redirects = kwargs.pop("allow_redirects", False)
        # HTTPX timeouts bound individual I/O phases. Also preserve the old
        # whole-request deadline, including stream consumption and redirects.
        async with asyncio.timeout(self.timeout_seconds):
            async with self._client.stream(
                method, url, follow_redirects=redirects, **kwargs,
            ) as response:
                log.info(
                    "[%s] private_http method=%s host=%s status=%d protocol=%s",
                    self.profile_name, method.upper(), response.url.host,
                    response.status_code, response.http_version,
                )
                yield PrivateResponse(response)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)
