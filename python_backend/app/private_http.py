"""Profile-scoped HTTP/2 transport for the existing private action engine.

Keep streaming, proxy binding and the context-manager interface at this boundary.
Protocol negotiation never triggers a second application request.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from http.cookiejar import Cookie
import logging
from typing import Any

import httpx
from .direct_test import direct_test_deadline
from .private_http_metrics import begin_request, end_request, request_hook, response_hook, error_kind
from .private_http_stages import PrivateRequestStages

log = logging.getLogger("remask_worker")
# HTTPX's INFO messages include full redirect URLs (including session IDs).
# This transport emits only method, host, status and negotiated protocol below.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def buffered_response(method, open_response, size, *, retry_read=False, validate=None, reader=None):
    """Finish a bounded read before exposing bytes; retry an interrupted GET once.

    Callers keep the same profile client and request arguments. HTTP rejection,
    decoding, timeout, cancellation and application errors are never retried.
    The method guard also prevents a caller from enabling a POST replay.
    """
    attempts = 2 if method == 'GET' and retry_read else 1
    for attempt in range(attempts):
        try:
            async with open_response() as response:
                if validate is not None:
                    validate(response)
                raw = await reader(response) if reader is not None else await response.content.read(size)
                return response, raw
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as error:
            kind = next(name for cls, name in (
                (httpx.ConnectError, 'ConnectError'), (httpx.ReadError, 'ReadError'),
                (httpx.RemoteProtocolError, 'RemoteProtocolError')) if isinstance(error, cls))
            log.warning('private_buffered_read interrupted method=%s kind=%s retry=%s',
                'GET' if method == 'GET' else 'other', kind, attempt + 1 < attempts)
            if attempt + 1 == attempts:
                raise


class _Body:
    def __init__(self, response: httpx.Response, metric=None) -> None:
        self._chunks = response.aiter_bytes()
        self._pending = b""
        self._metric = metric

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
        if self._metric is not None:
            self._metric.delivered += len(data)
        return bytes(data)


class PrivateResponse:
    def __init__(self, response: httpx.Response, metric=None) -> None:
        self.status = response.status_code
        self.headers = response.headers
        self.url = response.url
        self.charset = response.charset_encoding
        self.http_version = response.http_version
        self.content = _Body(response, metric)

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
        self._actor_id = str(cookies.get('c_user') or '')
        self.direct_test_until = direct_test_deadline(profile_name, self._actor_id) if not proxy else 0
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
        # Every private Facebook origin must negotiate HTTP/2. Disallow HTTP/1
        # at the origin transport, with no application replay. CONNECT
        # to the profile proxy may still use its own supported protocol.
        facebook_transport = httpx.AsyncHTTPTransport(
            proxy=proxy, http1=False, http2=True, retries=0, limits=limits, trust_env=False,
        )
        self._client = httpx.AsyncClient(
            transport=transport, mounts={"https://*facebook.com": facebook_transport,
                "https://*fbcdn.net": facebook_transport}, http2=True, trust_env=False,
            timeout=httpx.Timeout(timeout_seconds), cookies=jar,
            headers={"User-Agent": user_agent,
                     "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"},
            event_hooks={'request': [request_hook], 'response': [response_hook]},
        )

    @property
    def closed(self) -> bool:
        return self._client.is_closed

    def facebook_cookie_snapshot(self) -> dict[str, str]:
        """Return the current Facebook cookie jar without exposing it to logs."""
        result: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            domain = str(cookie.domain or "").lstrip(".").casefold()
            # The flat stored profile cannot represent host/path-specific
            # cookies safely. Only persist the reviewed shared root scope.
            if domain != "facebook.com" or cookie.path != "/":
                continue
            if not cookie.name or cookie.value is None or cookie.is_expired():
                continue
            name, value = str(cookie.name), str(cookie.value)
            if name in result and result[name] != value:
                return {}  # Ambiguous cookies must never overwrite a profile.
            result[name] = value
        return result

    async def close(self) -> None:
        await self._client.aclose()

    @asynccontextmanager
    async def request(self, method: str, url: str, **kwargs: Any):
        # Profile credentials must never leave through a direct connection,
        # even when a caller bypasses ProfileResolver or drops its proxy.
        if not self.proxy and not self.direct_test_until:
            raise httpx.ProxyError("Private request requires the profile proxy")
        if self.direct_test_until and direct_test_deadline(self.profile_name, self._actor_id) != self.direct_test_until:
            raise httpx.ProxyError('DIRECT_TEST_EXPIRED')
        requested_proxy = kwargs.pop("proxy", self.proxy)
        if requested_proxy != self.proxy:
            raise httpx.ProxyError("Private request cannot change profile proxy")
        redirects = kwargs.pop("allow_redirects", False)
        request_budget = kwargs.pop("request_timeout_seconds", self.timeout_seconds)
        request_budget = max(0.001, min(float(request_budget), 300.0))
        if request_budget != self.timeout_seconds:
            kwargs["timeout"] = httpx.Timeout(request_budget)
        log.info("[%s] private_http start method=%s host=%s", self.profile_name,
                 method.upper(), httpx.URL(url).host)
        # HTTPX timeouts bound individual I/O phases. Also preserve the old
        # whole-request deadline, including stream consumption and redirects.
        metric, token = begin_request(self.profile_name)
        stages = PrivateRequestStages()
        extensions = dict(kwargs.pop('extensions', {}) or {})
        if extensions.get('trace') is None:
            extensions['trace'] = stages.trace
        else:
            stages.external_trace = True
        kwargs['extensions'] = extensions
        error = None
        try:
            async with asyncio.timeout(request_budget):
                async with self._client.stream(
                    method, url, follow_redirects=redirects, **kwargs,
                ) as response:
                    log.info(
                        "[%s] private_http method=%s host=%s status=%d protocol=%s",
                        self.profile_name, method.upper(), response.url.host,
                        response.status_code, response.http_version,
                    )
                    yield PrivateResponse(response, metric)
        except BaseException as exc:
            error = exc
            try:
                log.warning('[%s] private_http_failure method=%s host=%s kind=%s stages=%s',
                    self.profile_name, method.upper(), httpx.URL(url).host, error_kind(exc),
                    json.dumps(stages.snapshot(), sort_keys=True, separators=(',', ':')))
            except Exception:
                pass
            raise
        finally:
            end_request(metric, token, error)

    def get(self, url: str, **kwargs: Any):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any):
        return self.request("POST", url, **kwargs)
