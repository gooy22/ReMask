"""Profile-scoped HTTP/2 transport for the existing private action engine.

Keep streaming and proxy binding at this boundary. Retry only a proxy TLS setup
failure that the trace proves happened before any HTTP/2 request data was sent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import asynccontextmanager
from http.cookiejar import Cookie
import logging
from typing import Any

import httpx
from .direct_test import direct_test_deadline
from .private_cookie_directives import auth_cookie_directive
from .private_http_metrics import begin_request, end_request, request_hook, response_hook, error_kind
from .private_http_stages import PrivateRequestStages
from .private_session_wire import cookie_evidence

log = logging.getLogger("remask_worker")

# HTTPX's INFO messages include full redirect URLs (including session IDs).
# This transport emits only method, host, status and negotiated protocol below.
logging.getLogger("httpx").setLevel(logging.WARNING)


async def buffered_response(method, open_response, size, *, retry_read=False, retry_timeout=False,
                            validate=None, reader=None):
    """Finish a bounded read before exposing bytes.

    Retry interrupted reads when retry_read is set. Timeout retries are a
    separate opt-in for a read-only GET; POSTs always get one attempt.
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
        except (httpx.TimeoutException, asyncio.TimeoutError) as error:
            kind = 'HttpxTimeout' if isinstance(error, httpx.TimeoutException) else 'RequestTimeout'
            retry = method == 'GET' and retry_timeout is True and attempt + 1 < attempts
            log.warning('private_buffered_read interrupted method=%s kind=%s retry=%s',
                'GET' if method == 'GET' else 'other', kind, retry)
            if not retry:
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

    async def read_some(self, size: int) -> bytes:
        """Return the next available decoded chunk without waiting for EOF."""
        size = max(0, int(size))
        if size == 0:
            return b""
        if self._pending:
            data = self._pending[:size]
            self._pending = self._pending[len(data):]
        else:
            while True:
                try:
                    chunk = await anext(self._chunks)
                except StopAsyncIteration:
                    return b""
                if chunk:
                    break
            data = chunk[:size]
            self._pending = chunk[len(data):]
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
        self.extensions = response.extensions
        self.content = _Body(response, metric)

    async def text_prefix(self, size: int, *, idle_timeout_seconds: float = 1.5) -> str:
        """Read a bounded body prefix, stopping when the response goes idle."""
        limit = max(0, min(int(size), 256 * 1024))
        if limit == 0:
            return ""
        raw = bytearray()
        try:
            async with asyncio.timeout(max(0.05, float(idle_timeout_seconds))):
                while len(raw) < limit:
                    chunk = await self.content.read_some(limit - len(raw))
                    if not chunk:
                        break
                    raw.extend(chunk)
        except asyncio.TimeoutError:
            # Some Meta handoff documents keep their response stream open after
            # sending the useful form/control. The caller can safely inspect the
            # prefix; the enclosing response context will close the remainder.
            pass
        try:
            return bytes(raw).decode(self.charset or "utf-8", errors="replace")
        except LookupError:
            return bytes(raw).decode("utf-8", errors="replace")

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
        self._user_agent = str(user_agent or '')
        # Keep the imported snapshot for same-actor rotation checks.
        # Explicit Meta redirect deletions are tracked separately and honored.
        self._profile_cookies = dict(cookies)
        self._trusted_meta_xs_fingerprints: set[str] = set()
        self._meta_redirect_auth_cookie_directive_pending = False
        self.meta_auth_cookie_deletions: set[str] = set()
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
        # Every private Facebook origin must negotiate HTTP/2. Disallow HTTP/1.
        # Keep proxy-pool retries disabled; the request boundary below retries
        # only when its trace proves a proxy TLS failure occurred before send.
        facebook_transport = httpx.AsyncHTTPTransport(
            proxy=proxy, http1=False, http2=True, retries=0, limits=limits, trust_env=False,
        )
        self._client = httpx.AsyncClient(
            transport=transport, mounts={"https://*facebook.com": facebook_transport,
                "https://*fbcdn.net": facebook_transport}, http2=True, trust_env=False,
            timeout=httpx.Timeout(timeout_seconds), cookies=jar,
            headers={"User-Agent": user_agent,
                     "Accept-Language": "en-US,en;q=0.9", "Accept": "*/*"},
            event_hooks={'request': [self._log_auth_cookie_evidence, request_hook],
                         'response': [response_hook, self._track_meta_xs_cookie_updates]},
        )

    def accept_same_actor_cookie_refresh(self) -> bool:
        """Adopt Meta-set cookies only while the selected actor and cookie set remain intact."""
        if self.meta_auth_cookie_deletions:
            return False
        original = dict(self._profile_cookies)
        snapshot = self.facebook_cookie_snapshot()
        # Meta may legitimately remove or rotate non-auth cookies during a
        # redirect. Keep that same-actor jar as long as both session auth
        # cookies remain valid; restoring every original cookie here would
        # overwrite Meta's update and resend stale values on the next hop.
        complete_same_actor = (
            bool(original)
            and bool(snapshot)
            and snapshot.get("c_user") == self._actor_id
            and bool(str(snapshot.get("xs") or "").strip())
        )
        if not complete_same_actor:
            return False
        self._profile_cookies = dict(snapshot)
        return True

    def _profile_cookie_snapshot_for_wire(self) -> dict[str, str]:
        """Preserve complete same-actor Meta refreshes across subsequent requests."""
        if not self.accept_same_actor_cookie_refresh():
            if self._meta_redirect_auth_cookie_directive_pending:
                return dict(self.facebook_cookie_snapshot())
            self.restore_profile_cookies()
        return dict(self._profile_cookies)

    @staticmethod
    def _serialize_profile_cookie_header(cookies: dict[str, str]) -> str | None:
        pairs = []
        for name, value in cookies.items():
            name, value = str(name), str(value)
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", name):
                return None
            if any(ord(char) < 33 or ord(char) > 126 or char in '";,\\\\' for char in value):
                return None
            pairs.append(f"{name}={value}")
        return "; ".join(pairs) if pairs else None

    @staticmethod
    def _cookie_header_carries_profile_snapshot(
        header: str, cookies: dict[str, str], actor_id: str,
    ) -> bool:
        if not header or not cookies or len(header) > 131072:
            return False
        values_by_name: dict[str, list[str]] = {}
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if not separator:
                continue
            values_by_name.setdefault(name, []).append(value.strip().strip('"'))
        for name, expected in cookies.items():
            values = values_by_name.get(str(name), [])
            if not values or str(expected) not in values:
                return False
        actor_values = values_by_name.get("c_user", [])
        xs_values = values_by_name.get("xs", [])
        if (not actor_values or any(value != actor_id for value in actor_values)
                or not xs_values or not any(value.strip() for value in xs_values)):
            return False
        return True

    async def _log_auth_cookie_evidence(self, request: httpx.Request) -> None:
        """Canonicalize and record the final Facebook Cookie header without exposing values."""
        try:
            host = request.url.host.lower()
            if ((host != "facebook.com" and not host.endswith(".facebook.com"))
                    or request.url.scheme.lower() != "https"):
                return
            profile_cookies = self._profile_cookie_snapshot_for_wire()
            canonical_header = self._serialize_profile_cookie_header(profile_cookies)
            original_header = request.headers.get("cookie", "")
            jar_header_matches_profile = self._cookie_header_carries_profile_snapshot(
                original_header, profile_cookies, self._actor_id,
            )
            rebuilt = canonical_header is not None and not jar_header_matches_profile
            if self._meta_redirect_auth_cookie_directive_pending:
                rebuilt = False
                actor_values = []
                for part in original_header.split(";"):
                    name, separator, value = part.strip().partition("=")
                    if separator and name.strip() == "c_user":
                        actor_values.append(value.strip().strip('"'))
                if any(value != self._actor_id for value in actor_values):
                    # A server-issued actor change must never cross profiles.
                    request.headers["cookie"] = ""
                    rebuilt = False
            elif canonical_header is not None and rebuilt:
                # If HTTPX omitted a saved profile cookie or exposed another
                # c_user, send the verified profile snapshot. Otherwise retain
                # its host/path-aware header, including same-actor Meta-issued
                # cookies from the current redirect chain.
                request.headers["cookie"] = canonical_header
            header = request.headers.get("cookie", "")
            evidence = cookie_evidence(header, self._actor_id)
            names = set(evidence.get("header_auth_cookie_names", []))
            path = request.url.path
            if path not in {
                "/", "/me", "/settings", "/profile.php",
                "/adsmanager/manage/campaigns", "/business/loginpage",
                "/business/loginpage/", "/latest/home", "/login", "/login/",
                "/login.php", "/checkpoint", "/checkpoint/",
            }:
                path = "unreviewed"
            wire_exact = canonical_header is not None and header == canonical_header
            user_agent_matches = request.headers.get("user-agent", "") == self._user_agent
            log.info(
                "[%s] private_http_auth_header method=%s host=%s path=%s actor_cookie_state=%s xs_present=%s cookie_count=%s cookie_wire_exact=%s cookie_header_rebuilt=%s user_agent_matches=%s",
                self.profile_name,
                request.method,
                host,
                path,
                evidence.get("header_auth_state", "unavailable"),
                "xs" in names,
                evidence.get("header_cookie_count", 0),
                wire_exact,
                rebuilt,
                user_agent_matches,
            )
        except Exception as exc:
            log.info(
                "[%s] private_http_auth_header state=unavailable kind=%s",
                self.profile_name,
                type(exc).__name__,
            )

    @property
    def closed(self) -> bool:
        return self._client.is_closed

    def facebook_cookie_snapshot(self) -> dict[str, str]:
        """Return shared-scope cookies when scoped actor cookies stay on the selected profile."""
        result: dict[str, str] = {}
        scoped_auth: list[tuple[str, str]] = []
        for cookie in self._client.cookies.jar:
            domain = str(cookie.domain or "").lstrip(".").casefold()
            if domain != "facebook.com" and not domain.endswith(".facebook.com"):
                continue
            if not cookie.name or cookie.value is None or cookie.is_expired():
                continue
            name, value = str(cookie.name), str(cookie.value)
            if domain == "facebook.com" and cookie.path == "/":
                if name in result and result[name] != value:
                    return {}  # Ambiguous cookies must never overwrite a profile.
                result[name] = value
            elif name in {"c_user", "xs"}:
                # A Business host/path cookie can shadow the shared cookie in
                # the next HTTP request. Force callers to restore the imported
                # profile jar instead of treating the root snapshot as valid.
                scoped_auth.append((name, value))
        for name, value in scoped_auth:
            shared_value = result.get(name)
            if name == "c_user" and shared_value is not None and shared_value != value:
                return {}  # A scoped actor switch must never replace the profile.
            if (name == "xs" and shared_value is not None and shared_value != value
                    and hashlib.sha256(value.encode("utf-8")).hexdigest()
                    not in self._trusted_meta_xs_fingerprints):
                return {}  # Ignore manually injected or unverified shadow cookies.
        return result

    async def _track_meta_xs_cookie_updates(self, response: httpx.Response) -> None:
        """Track Meta-issued xs cookies and auth-cookie directives on redirects."""
        try:
            host = str(response.url.host or "").casefold()
            if host != "facebook.com" and not host.endswith(".facebook.com"):
                return
            is_redirect = (
                response.status_code in {301, 302, 303, 307, 308}
                and bool(response.headers.get("location"))
            )
            auth_directives: set[str] = set()
            auth_deletions: set[str] = set()
            auth_deletion_scopes: dict[str, dict[str, str]] = {}
            auth_scope_evidence: set[str] = set()
            for raw in response.headers.get_list("set-cookie"):
                directive = auth_cookie_directive(raw)
                if directive is None:
                    continue
                cookie_name, is_cookie_deletion = directive
                pair = str(raw or "").split(";", 1)[0].strip()
                _, separator, value = pair.partition("=")
                if not separator:
                    continue
                value = value.strip().strip('"')
                if cookie_name in {"c_user", "xs"} and (is_redirect or is_cookie_deletion):
                    destination = auth_deletions if is_cookie_deletion else auth_directives
                    destination.add(cookie_name)
                    attributes: dict[str, str] = {}
                    for attribute in str(raw).split(";")[1:]:
                        key, _, attribute_value = attribute.strip().partition("=")
                        key = key.strip().casefold()
                        if key in {"domain", "path"}:
                            attributes[key] = attribute_value.strip()
                    domain = attributes.get("domain", "").lstrip(".").casefold().rstrip(".")
                    domain_scope = (
                        "shared" if domain == "facebook.com"
                        else "facebook_subdomain" if domain.endswith(".facebook.com")
                        else "host" if not domain
                        else "other"
                    )
                    path = attributes.get("path", "")
                    path_scope = (
                        "root" if path == "/"
                        else "narrow" if path.startswith("/")
                        else "default"
                    )
                    action = "delete" if is_cookie_deletion else "set"
                    auth_scope_evidence.add(
                        f"{cookie_name}:{action}:{domain_scope}:{path_scope}"
                    )
                    if is_cookie_deletion:
                        auth_deletion_scopes[cookie_name] = {
                            "domain": domain_scope,
                            "path": path_scope,
                        }
                if cookie_name != "xs" or is_cookie_deletion:
                    continue
                self._trusted_meta_xs_fingerprints.add(
                    hashlib.sha256(value.encode("utf-8")).hexdigest()
                )
                if len(self._trusted_meta_xs_fingerprints) > 64:
                    self._trusted_meta_xs_fingerprints.pop()
            if auth_deletions or (is_redirect and auth_directives):
                # Preserve Meta's explicit auth-cookie decisions for the
                # bootstrap guard. Cookie values are excluded from the evidence.
                response.extensions["remask_meta_auth_cookie_directive"] = {
                    "sets": sorted(auth_directives),
                    "deletions": sorted(auth_deletions),
                    "deletion_scopes": auth_deletion_scopes,
                }
                self._meta_redirect_auth_cookie_directive_pending = True
                self.meta_auth_cookie_deletions.update(auth_deletions)
                log.info(
                    "[%s] private_http_meta_auth_cookie_directive status=%d host=%s names=%s deleted=%s scope=%s",
                    self.profile_name, response.status_code, host,
                    ",".join(sorted(auth_directives | auth_deletions)),
                    ",".join(sorted(auth_deletions)),
                    ",".join(sorted(auth_scope_evidence)),
                )
        except Exception:
            # Cookie tracking is only an acceptance aid; never disrupt HTTP.
            return

    def restore_profile_cookies(self) -> None:
        """Reset Facebook cookies to the imported profile for an identity probe."""
        if self.meta_auth_cookie_deletions:
            return
        jar = self._client.cookies.jar
        for cookie in list(jar):
            domain = str(cookie.domain or "").lstrip(".").casefold()
            if domain == "facebook.com" or domain.endswith(".facebook.com"):
                try:
                    jar.clear(cookie.domain, cookie.path, cookie.name)
                except KeyError:
                    pass
        self._trusted_meta_xs_fingerprints.clear()
        self._meta_redirect_auth_cookie_directive_pending = False
        for name, value in self._profile_cookies.items():
            jar.set_cookie(Cookie(
                version=0, name=name, value=value, port=None,
                port_specified=False, domain=".facebook.com",
                domain_specified=True, domain_initial_dot=True, path="/",
                path_specified=True, secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False,
            ))

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
        cookie_jar_guard = kwargs.pop("cookie_jar_guard", None)
        if cookie_jar_guard is not None and not callable(cookie_jar_guard):
            raise TypeError("cookie_jar_guard must be callable")
        request_budget = kwargs.pop("request_timeout_seconds", self.timeout_seconds)
        request_budget = max(0.001, min(float(request_budget), 300.0))
        if request_budget != self.timeout_seconds:
            kwargs["timeout"] = httpx.Timeout(request_budget)
        log.info("[%s] private_http start method=%s host=%s", self.profile_name,
                 method.upper(), httpx.URL(url).host)
        # HTTPX timeouts bound individual I/O phases. Preserve a whole-attempt
        # deadline too, including stream consumption. A second budget is needed
        # only when the trace proves the first attempt failed before HTTP/2 send.
        metric, token = begin_request(self.profile_name)
        extensions = dict(kwargs.pop('extensions', {}) or {})
        external_trace = extensions.get('trace') is not None
        error = None
        stages = PrivateRequestStages()
        response_yielded = False
        try:
            for attempt in range(2):
                stages = PrivateRequestStages()
                stages.external_trace = external_trace
                request_extensions = dict(extensions)
                if not external_trace:
                    request_extensions['trace'] = stages.trace
                request_kwargs = dict(kwargs)
                request_kwargs['extensions'] = request_extensions
                try:
                    async with asyncio.timeout(request_budget):
                        async with self._client.stream(
                            method, url, follow_redirects=redirects, **request_kwargs,
                        ) as response:
                            log.info(
                                "[%s] private_http method=%s host=%s status=%d protocol=%s",
                                self.profile_name, method.upper(), response.url.host,
                                response.status_code, response.http_version,
                            )
                            if cookie_jar_guard is not None:
                                cookie_jar_guard()
                            response_yielded = True
                            yield PrivateResponse(response, metric)
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout, TimeoutError):
                    snapshot = stages.snapshot()
                    retry_before_send = (
                        attempt == 0 and not response_yielded and not external_trace
                        and snapshot['phase'] == 'proxy.start_tls'
                        and snapshot['state'] == 'failed'
                        and snapshot['tcp_connect_observed'] is True
                        and snapshot['tls_completed'] is False
                        and snapshot['h2_headers_sent'] is False
                        and snapshot['h2_body_sent'] is False
                        and snapshot['measurement_errors'] == 0
                    )
                    if not retry_before_send:
                        raise
                    log.warning(
                        '[%s] private_http_connect_retry method=%s host=%s phase=proxy.start_tls attempt=1',
                        self.profile_name, method.upper(), httpx.URL(url).host,
                    )
        except BaseException as exc:
            error = exc
            try:
                log.warning('[%s] private_http_failure method=%s host=%s kind=%s error_type=%s stages=%s',
                    self.profile_name, method.upper(), httpx.URL(url).host, error_kind(exc),
                    type(exc).__name__,
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
