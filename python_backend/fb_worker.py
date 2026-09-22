from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import aiohttp


log = logging.getLogger("remask_worker")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AutomationError(RuntimeError):
    pass


class ProxyError(AutomationError):
    pass


class AuthenticationError(AutomationError):
    pass


class RemoteRequestError(AutomationError):
    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        meta_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.meta_payload = meta_payload or {}


# ---------------------------------------------------------------------------
# Profile / Facebook web-session context
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class WebProfile:
    name: str
    cookies: dict[str, str]
    proxy: str | None
    user_agent: str


@dataclass(slots=True)
class FacebookBootstrap:
    fb_dtsg: str
    actor_id: str
    lsd: str = ""
    jazoest: str = ""


@dataclass(frozen=True, slots=True)
class GraphQLOperation:
    key: str
    env_name: str
    default_doc_id: str | None = None
    friendly_name: str = ""


# ---------------------------------------------------------------------------
# One shared Facebook Web Session
# ---------------------------------------------------------------------------

class FacebookWebSession:
    """
    Единая profile-bound Facebook Web Session.

    Один профиль:
        cookies
        proxy
        user-agent
        fb_dtsg
        lsd
        jazoest
        actor_id

    Все приватные GraphQL-запросы проходят через этот объект.
    """

    ADS_MANAGER_URL = "https://business.facebook.com/latest/home"

    GRAPHQL_URL = "https://business.facebook.com/api/graphql/"

    FB_DTSG_PATTERNS = (
        (
            r'["\']DTSG(?:Initial|Init)Data["\'].{0,16000}?'
            r'["\']token["\']\s*:\s*["\']([^"\']+)["\']'
        ),
        (
            r'\[\s*["\']DTSG(?:Initial|Init)Data["\']\s*,\s*\[\]\s*,\s*\{'
            r'.{0,6000}?["\']token["\']\s*:\s*["\']([^"\']+)["\']'
        ),
        (
            r'["\']dtsg["\']\s*:\s*\{.{0,800}?'
            r'["\']token["\']\s*:\s*["\']([^"\']+)["\']'
        ),
        (
            r'\{\s*["\']name["\']\s*:\s*["\']fb_dtsg["\']'
            r'.{0,600}?["\']value["\']\s*:\s*["\']([^"\']+)["\']'
        ),
        (
            r'name=["\']fb_dtsg["\']'
            r'[^>]*value=["\']([^"\']+)["\']'
        ),
        (
            r'["\']fb_dtsg["\']'
            r'\s*[:=]\s*["\']([^"\']+)["\']'
        ),
    )

    def __init__(
        self,
        profile: WebProfile,
        timeout_seconds: int = 25,
        pool_size: int = 20,
    ) -> None:
        self.profile = profile
        self.timeout_seconds = max(5, int(timeout_seconds))
        self.pool_size = max(1, int(pool_size))

        self.timeout = aiohttp.ClientTimeout(
            total=self.timeout_seconds,
            connect=self.timeout_seconds,
            sock_connect=self.timeout_seconds,
            sock_read=self.timeout_seconds,
        )

        self.connector: aiohttp.TCPConnector | None = None
        self.session: aiohttp.ClientSession | None = None

        self._session_lock = asyncio.Lock()
        self._bootstrap_lock = asyncio.Lock()

        self._bootstrap: FacebookBootstrap | None = None

    # ------------------------------------------------------------------
    # aiohttp lifecycle
    # ------------------------------------------------------------------

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self.session is not None and not self.session.closed:
            return self.session

        async with self._session_lock:
            if self.session is not None and not self.session.closed:
                return self.session

            self.connector = aiohttp.TCPConnector(
                limit=self.pool_size,
                limit_per_host=self.pool_size,
                enable_cleanup_closed=True,
            )

            self.session = aiohttp.ClientSession(
                timeout=self.timeout,
                connector=self.connector,
                cookies=self.profile.cookies,
                headers={
                    "User-Agent": self.profile.user_agent,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "*/*",
                },
            )

            return self.session

    async def __aenter__(self) -> "FacebookWebSession":
        await self._ensure_session()
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc: Any,
        tb: Any,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        async with self._session_lock:
            if self.session is not None and not self.session.closed:
                await self.session.close()

            self.session = None
            self.connector = None

        self._bootstrap = None

    # ------------------------------------------------------------------
    # Proxy
    # ------------------------------------------------------------------

    async def check_connection(self) -> bool:
        if not self.profile.proxy:
            raise ProxyError(
                "Proxy is required for this Facebook profile"
            )

        session = await self._ensure_session()

        try:
            async with session.get(
                "https://api.ipify.org",
                params={"format": "json"},
                proxy=self.profile.proxy,
            ) as response:

                raw = await response.text()

                if response.status != 200:
                    raise ProxyError(
                        f"Proxy check returned HTTP {response.status}"
                    )

                try:
                    payload = json.loads(raw)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ProxyError(
                        "Proxy checker returned invalid JSON"
                    ) from exc

                if not isinstance(payload, dict):
                    raise ProxyError(
                        "Proxy checker returned invalid response"
                    )

                ip = str(payload.get("ip") or "").strip()

                if not ip:
                    raise ProxyError(
                        "Proxy checker returned no exit IP"
                    )

                log.info(
                    "[%s] proxy OK; exit_ip=%s",
                    self.profile.name,
                    ip,
                )

                return True

        except ProxyError:
            raise

        except asyncio.TimeoutError as exc:
            raise ProxyError("Proxy timeout") from exc

        except aiohttp.ClientError as exc:
            raise ProxyError(
                f"Proxy network error: {exc.__class__.__name__}"
            ) from exc

    # ------------------------------------------------------------------
    # Facebook bootstrap
    # ------------------------------------------------------------------

    @staticmethod
    def _match_sources(source: str) -> list[str]:
        raw = str(source or "")
        variants = [raw]

        entity_decoded = html.unescape(raw)
        if entity_decoded not in variants:
            variants.append(entity_decoded)

        def decode_ascii_unicode(match: re.Match[str]) -> str:
            codepoint = int(match.group(1), 16)
            return chr(codepoint) if codepoint <= 0x7F else match.group(0)

        js_decoded = re.sub(
            r'\\u([0-9a-fA-F]{4})',
            decode_ascii_unicode,
            entity_decoded,
        )
        js_decoded = re.sub(
            r'\\x([0-9a-fA-F]{2})',
            lambda match: chr(int(match.group(1), 16)),
            js_decoded,
        )
        js_decoded = (
            js_decoded
            .replace(r'\/', '/')
            .replace(r'\"', '"')
            .replace(r"\'", "'")
        )
        if js_decoded not in variants:
            variants.append(js_decoded)

        return variants

    @classmethod
    def _first_match(
        cls,
        source: str,
        patterns: list[str],
    ) -> str:
        for candidate_source in cls._match_sources(source):
            for pattern in patterns:
                match = re.search(
                    pattern,
                    candidate_source,
                    flags=re.IGNORECASE | re.DOTALL,
                )

                if match:
                    value = html.unescape(
                        str(match.group(1) or "")
                    ).strip()

                    if value:
                        return value

        return ""

    async def bootstrap(
        self,
        *,
        force: bool = False,
    ) -> FacebookBootstrap:

        if self._bootstrap is not None and not force:
            return self._bootstrap

        async with self._bootstrap_lock:

            if self._bootstrap is not None and not force:
                return self._bootstrap

            session = await self._ensure_session()

            token_patterns = list(self.FB_DTSG_PATTERNS)

            bootstrap_urls = (
                self.ADS_MANAGER_URL,
                "https://business.facebook.com/latest/settings",
                "https://business.facebook.com/latest/overview",
                "https://www.facebook.com/adsmanager/manage/campaigns",
                "https://www.facebook.com/marketplace/",
                "https://www.facebook.com/",
                "https://www.facebook.com/me",
                "https://www.facebook.com/settings",
                "https://m.facebook.com/",
                "https://mbasic.facebook.com/",
                "https://mbasic.facebook.com/profile.php",
            )

            body = ""
            final_url = ""
            bootstrap_source = ""
            fb_dtsg = ""
            attempts: list[str] = []

            for bootstrap_url in bootstrap_urls:
                try:
                    async with session.get(
                        bootstrap_url,
                        proxy=self.profile.proxy,
                        headers={
                            "Accept": (
                                "text/html,application/xhtml+xml,"
                                "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
                            ),
                            "Accept-Language": "en-US,en;q=0.9",
                            "Cache-Control": "no-cache",
                            "Pragma": "no-cache",
                            "Sec-Fetch-Dest": "document",
                            "Sec-Fetch-Mode": "navigate",
                            "Sec-Fetch-Site": "none",
                            "Sec-Fetch-User": "?1",
                            "Upgrade-Insecure-Requests": "1",
                        },
                        allow_redirects=True,
                    ) as response:
                        candidate_body = await response.text()
                        candidate_url = str(response.url)

                        if response.status >= 500:
                            attempts.append(
                                f"{bootstrap_url}: HTTP {response.status}"
                            )
                            continue

                        lower_url = candidate_url.lower()
                        lower_body = candidate_body.lower()

                        if (
                            "/login" in lower_url
                            or "/checkpoint" in lower_url
                            or "login_form" in lower_body
                        ):
                            attempts.append(
                                f"{bootstrap_url}: login/checkpoint"
                            )
                            continue

                        candidate_dtsg = self._first_match(
                            candidate_body,
                            token_patterns,
                        )
                        if not candidate_dtsg:
                            normalized_sources = self._match_sources(candidate_body)
                            marker_names = [
                                marker
                                for marker in (
                                    "DTSGInitialData",
                                    "DTSGInitData",
                                    '"fb_dtsg"',
                                    'name="fb_dtsg"',
                                    "name='fb_dtsg'",
                                    "CurrentUserInitialData",
                                )
                                if any(marker in source for source in normalized_sources)
                            ]
                            surface = (
                                f"HTTP {response.status} final={candidate_url} "
                                f"bytes={len(candidate_body)}"
                            )
                            if marker_names:
                                attempts.append(
                                    f"{bootstrap_url}: {surface} markers="
                                    + ",".join(marker_names)
                                    + " but no usable fb_dtsg was parsed"
                                )
                            else:
                                attempts.append(
                                    f"{bootstrap_url}: {surface} no DTSG marker"
                                )
                            continue

                        body = candidate_body
                        final_url = candidate_url
                        bootstrap_source = bootstrap_url
                        fb_dtsg = candidate_dtsg
                        break

                except asyncio.TimeoutError:
                    attempts.append(f"{bootstrap_url}: timeout")
                    continue
                except aiohttp.ClientError as exc:
                    attempts.append(
                        f"{bootstrap_url}: network {exc.__class__.__name__}"
                    )
                    continue

            if not fb_dtsg:
                detail = " | ".join(attempts[-6:]) or "no bootstrap response"
                raise AuthenticationError(
                    "Facebook browser session has no usable fb_dtsg. "
                    "The saved cookies may be expired/incomplete, or Facebook "
                    "returned a bootstrap shape that does not expose the token. "
                    f"Attempts: {detail}"
                )

            lsd = self._first_match(
                body,
                [
                    (
                        r'"LSD".{0,1800}?'
                        r'"token"\s*:\s*"([^"]+)"'
                    ),
                    (
                        r'name=["\']lsd["\']'
                        r'[^>]*value=["\']([^"\']+)["\']'
                    ),
                ],
            )

            jazoest = self._first_match(
                body,
                [
                    (
                        r'name=["\']jazoest["\']'
                        r'[^>]*value=["\']([^"\']+)["\']'
                    ),
                    (
                        r'["\']jazoest["\']'
                        r'\s*[:=]\s*["\']([^"\']+)["\']'
                    ),
                ],
            )

            actor_id = str(
                self.profile.cookies.get("c_user")
                or self.profile.cookies.get("i_user")
                or ""
            ).strip()

            if not actor_id:
                raise AuthenticationError(
                    "Facebook actor_id is missing from c_user/i_user cookies"
                )

            bootstrap = FacebookBootstrap(
                fb_dtsg=fb_dtsg,
                actor_id=actor_id,
                lsd=lsd,
                jazoest=jazoest,
            )

            self._bootstrap = bootstrap

            log.info(
                "[%s] FB bootstrap ready actor=%s lsd=%s jazoest=%s "
                "source=%s final_url=%s",
                self.profile.name,
                actor_id,
                "yes" if lsd else "no",
                "yes" if jazoest else "no",
                bootstrap_source,
                final_url,
            )

            return bootstrap

    def invalidate_bootstrap(self) -> None:
        self._bootstrap = None

    async def fetch_text(
        self,
        url: str,
        *,
        max_bytes: int = 4_000_000,
        referer: str | None = None,
    ) -> tuple[int, str, str]:
        target = str(url or "").strip()
        if not target:
            raise RemoteRequestError("fetch_text URL is required")

        parts = urlsplit(target)
        hostname = str(parts.hostname or "").lower()
        allowed = (
            hostname == "facebook.com"
            or hostname.endswith(".facebook.com")
            or hostname == "fbcdn.net"
            or hostname.endswith(".fbcdn.net")
        )
        if parts.scheme != "https" or not allowed:
            raise RemoteRequestError(
                f"fetch_text blocked unsupported host: {hostname or '<empty>'}"
            )

        session = await self._ensure_session()
        headers = {
            "User-Agent": self.profile.user_agent,
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer

        try:
            async with session.get(
                target,
                proxy=self.profile.proxy,
                headers=headers,
                allow_redirects=True,
            ) as response:
                raw = await response.content.read(max(1, int(max_bytes)) + 1)
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]

                charset = response.charset or "utf-8"
                try:
                    body = raw.decode(charset, errors="replace")
                except LookupError:
                    body = raw.decode("utf-8", errors="replace")

                return response.status, body, str(response.url)

        except asyncio.TimeoutError as exc:
            raise RemoteRequestError(
                f"Facebook fetch timeout: {target}"
            ) from exc
        except aiohttp.ClientError as exc:
            raise RemoteRequestError(
                "Facebook fetch network failure: "
                f"{exc.__class__.__name__}"
            ) from exc

    async def extract_csrf_token(self) -> str:
        """
        Backward-compatible API.
        """
        bootstrap = await self.bootstrap()
        return bootstrap.fb_dtsg

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_graphql_body(
        raw_body: str,
    ) -> dict[str, Any]:

        body = str(raw_body or "").strip()

        # Facebook occasionally prefixes JSON responses.
        if body.startswith("for (;;);"):
            body = body[len("for (;;);"):].lstrip()

        if not body:
            raise RemoteRequestError(
                "Facebook returned an empty response"
            )

        try:
            payload = json.loads(body)

        except (json.JSONDecodeError, ValueError) as exc:
            preview = re.sub(
                r"\s+",
                " ",
                body,
            )[:1000]

            raise RemoteRequestError(
                "Facebook returned non-JSON response: "
                f"{preview}"
            ) from exc

        if not isinstance(payload, dict):
            raise RemoteRequestError(
                "Facebook returned unexpected JSON response type"
            )

        return payload

    # ------------------------------------------------------------------
    # Unified private GraphQL transport
    # ------------------------------------------------------------------

    async def graphql(
        self,
        doc_id: str,
        variables: dict[str, Any],
        *,
        friendly_name: str = "",
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:

        effective_doc_id = str(doc_id or "").strip()

        if not effective_doc_id:
            raise RemoteRequestError(
                "GraphQL doc_id is required"
            )

        if not isinstance(variables, dict):
            raise RemoteRequestError(
                "GraphQL variables must be an object"
            )

        endpoint = (
            str(endpoint_url or self.GRAPHQL_URL).strip()
            or self.GRAPHQL_URL
        )

        bootstrap = await self.bootstrap()
        session = await self._ensure_session()

        form: dict[str, str] = {
            "fb_dtsg": bootstrap.fb_dtsg,
            "fb_api_caller_class": "RelayModern",
            "doc_id": effective_doc_id,
            "variables": json.dumps(
                variables,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "__a": "1",
            "__aaid": "0",
            "server_timestamps": "true",
        }

        if bootstrap.actor_id:
            form["av"] = bootstrap.actor_id
            form["__user"] = bootstrap.actor_id

        if bootstrap.lsd:
            form["lsd"] = bootstrap.lsd

        if bootstrap.jazoest:
            form["jazoest"] = bootstrap.jazoest

        if friendly_name:
            form["fb_api_req_friendly_name"] = friendly_name

        endpoint_parts = urlsplit(endpoint)
        origin = (
            f"{endpoint_parts.scheme}://{endpoint_parts.netloc}"
            if endpoint_parts.scheme and endpoint_parts.netloc
            else "https://business.facebook.com"
        )
        referer = (
            self.ADS_MANAGER_URL
            if "business.facebook.com" in endpoint_parts.netloc
            else origin + "/"
        )

        headers = {
            "User-Agent": self.profile.user_agent,
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": origin,
            "Referer": referer,
        }

        comet_req = str(os.getenv("REMASK_FB_COMET_REQ") or "").strip()
        if comet_req:
            form["__comet_req"] = comet_req

        asbd_id = str(os.getenv("REMASK_FB_ASBD_ID") or "").strip()
        if asbd_id:
            headers["X-ASBD-ID"] = asbd_id

        if bootstrap.lsd:
            headers["X-FB-LSD"] = bootstrap.lsd

        if friendly_name:
            headers["X-FB-Friendly-Name"] = friendly_name

        try:
            async with session.post(
                endpoint,
                data=form,
                headers=headers,
                proxy=self.profile.proxy,
                allow_redirects=False,
            ) as response:

                raw_body = await response.text()

                # Login/checkpoint redirect.
                if response.status in {301, 302, 303, 307, 308}:
                    location = str(
                        response.headers.get("Location") or ""
                    )

                    if (
                        "login" in location.lower()
                        or "checkpoint" in location.lower()
                    ):
                        self.invalidate_bootstrap()

                        raise AuthenticationError(
                            "Facebook GraphQL redirected to "
                            "login/checkpoint"
                        )

                payload = self._decode_graphql_body(raw_body)

                if response.status in {401, 403}:
                    self.invalidate_bootstrap()

                    diagnostic = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )[:4000]

                    raise AuthenticationError(
                        f"Facebook authentication failure "
                        f"HTTP {response.status}: {diagnostic}"
                    )

                if response.status >= 400:
                    diagnostic = json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )[:4000]

                    raise RemoteRequestError(
                        f"Meta HTTP {response.status}: {diagnostic}",
                        http_status=response.status,
                        meta_payload=payload,
                    )

                return payload

        except (AuthenticationError, RemoteRequestError):
            raise

        except asyncio.TimeoutError as exc:
            raise RemoteRequestError(
                "Facebook GraphQL request timeout"
            ) from exc

        except aiohttp.ClientError as exc:
            raise RemoteRequestError(
                "Facebook GraphQL network failure: "
                f"{exc.__class__.__name__}"
            ) from exc

    async def send_post_request(
        self,
        endpoint_url: str,
        doc_id: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Backward-compatible method for existing project code.
        """

        return await self.graphql(
            doc_id=doc_id,
            variables=variables,
            endpoint_url=endpoint_url,
        )


# Compatibility with business_handler.py / ad_account_handler.py.
WebSessionManager = FacebookWebSession


# ---------------------------------------------------------------------------
# Facebook private operations
# ---------------------------------------------------------------------------

class BusinessLogicController:
    """
    High-level Facebook web operations.

    doc_id values can be changed through Railway env variables without
    editing/redeploying Python code.
    """

    OPERATIONS: dict[str, GraphQLOperation] = {
        "CREATE_BM": GraphQLOperation(
            key="CREATE_BM",
            env_name="REMASK_DOC_ID_CREATE_BM",
            default_doc_id="739201948201938",
            friendly_name="BusinessManagerCreateMutation",
        ),

        "CREATE_AD_ACCOUNT": GraphQLOperation(
            key="CREATE_AD_ACCOUNT",
            env_name="REMASK_DOC_ID_CREATE_AD_ACCOUNT",
            default_doc_id="684920184730193",
            friendly_name="AdAccountCreateMutation",
        ),

        "LINK_PAYMENT": GraphQLOperation(
            key="LINK_PAYMENT",
            env_name="REMASK_DOC_ID_LINK_PAYMENT",
            default_doc_id="582930491827304",
            friendly_name="PaymentCredentialLinkMutation",
        ),

        # No guessed value here.
        # Once captured from real Facebook traffic:
        #
        # REMASK_DOC_ID_LIST_PAGES=<actual doc_id>
        #
        "LIST_PAGES": GraphQLOperation(
            key="LIST_PAGES",
            env_name="REMASK_DOC_ID_LIST_PAGES",
            default_doc_id=None,
            friendly_name="PagesQuery",
        ),
    }

    def __init__(
        self,
        session: FacebookWebSession,
    ) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def resolve_doc_id(
        cls,
        operation: str,
        override: str | None = None,
    ) -> str:

        key = str(operation or "").strip().upper()

        config = cls.OPERATIONS.get(key)

        if config is None:
            raise RemoteRequestError(
                f"Unknown GraphQL operation: {key}"
            )

        value = str(
            override
            or os.getenv(config.env_name)
            or config.default_doc_id
            or ""
        ).strip()

        if not value:
            raise RemoteRequestError(
                f"{config.env_name} is not configured "
                f"for operation {key}"
            )

        if not re.fullmatch(r"\d{5,40}", value):
            raise RemoteRequestError(
                f"Invalid doc_id for {key}: {value!r}"
            )

        return value

    @staticmethod
    def _meta_errors(
        payload: dict[str, Any],
    ) -> list[Any]:

        errors = payload.get("errors")

        if isinstance(errors, list):
            return errors

        if errors:
            return [errors]

        return []

    @staticmethod
    def _diagnostic(
        payload: Any,
        *,
        limit: int = 5000,
    ) -> str:

        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except Exception:
            encoded = repr(payload)

        return encoded[:limit]

    async def execute_operation(
        self,
        operation: str,
        variables: dict[str, Any],
        *,
        doc_id: str | None = None,
    ) -> dict[str, Any]:

        key = str(operation).strip().upper()

        config = self.OPERATIONS.get(key)

        if config is None:
            raise RemoteRequestError(
                f"Unsupported operation: {key}"
            )

        effective_doc_id = self.resolve_doc_id(
            key,
            doc_id,
        )

        response = await self.session.graphql(
            effective_doc_id,
            variables,
            friendly_name=config.friendly_name,
        )

        if self._meta_errors(response):
            raise RemoteRequestError(
                f"{key} failed. Meta response: "
                f"{self._diagnostic(response)}",
                meta_payload=response,
            )

        return response

    # ------------------------------------------------------------------
    # Business Manager
    # ------------------------------------------------------------------

    async def create_business_manager_detailed(
        self,
        name: str,
        page_id: str | None = None,
        doc_id: str | None = None,
        *,
        user_email: str = "",
        user_first_name: str = "",
        user_last_name: str = "",
        profile_display_name: str = "",
        vertical: str = "ADVERTISING",
        allow_scope_selector_fallback: bool = True,
    ):
        clean_name = str(name or "").strip()
        clean_page_id = str(page_id or "").strip()

        if not clean_name:
            raise ValueError("Business Manager name is required")

        from app.facebook_business_create import (
            DocIdMutationError,
            create_business_with_docids,
        )

        try:
            result = await create_business_with_docids(
                self.session,
                business_name=clean_name,
                page_id=clean_page_id,
                user_email=str(user_email or "").strip(),
                user_first_name=str(user_first_name or "").strip(),
                user_last_name=str(user_last_name or "").strip(),
                profile_display_name=str(
                    profile_display_name
                    or self.session.profile.name
                    or ""
                ).strip(),
                vertical=str(vertical or "ADVERTISING").strip(),
                explicit_doc_id=doc_id,
                allow_scope_selector_fallback=allow_scope_selector_fallback,
            )
        except DocIdMutationError as exc:
            raise RemoteRequestError(
                str(exc),
                meta_payload=exc.payload,
            ) from exc

        log.info(
            "[%s] Business Manager created id=%s doc_id=%s "
            "friendly_name=%s mode=%s source=%s response_path=%s",
            self.session.profile.name,
            result.business_id,
            result.candidate.doc_id,
            result.candidate.friendly_name or "-",
            result.candidate.variables_mode,
            result.candidate.source,
            result.response_path,
        )

        return result

    async def create_business_manager(
        self,
        name: str,
        page_id: str | None = None,
        doc_id: str | None = None,
        *,
        user_email: str = "",
        user_first_name: str = "",
        user_last_name: str = "",
        profile_display_name: str = "",
        vertical: str = "ADVERTISING",
    ) -> str:
        result = await self.create_business_manager_detailed(
            name=name,
            page_id=page_id,
            doc_id=doc_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=profile_display_name,
            vertical=vertical,
        )
        return result.business_id

    # ------------------------------------------------------------------
    # Ad Account
    # ------------------------------------------------------------------

    async def create_ad_account(
        self,
        business_id: str,
        account_name: str,
        doc_id: str | None = None,
        *,
        currency: str = "USD",
        timezone_id: int = 1,
    ) -> str:

        clean_business_id = str(
            business_id or ""
        ).strip()

        clean_name = str(
            account_name or ""
        ).strip()

        clean_currency = str(
            currency or ""
        ).strip().upper()

        if not clean_business_id:
            raise ValueError(
                "business_id is required"
            )

        if not clean_name:
            raise ValueError(
                "Ad Account name is required"
            )

        if not clean_currency:
            raise ValueError(
                "currency is required"
            )

        try:
            timezone = int(timezone_id)

        except (TypeError, ValueError) as exc:
            raise ValueError(
                "timezone_id must be an integer"
            ) from exc

        variables = {
            "input": {
                "client_mutation_id": "1",
                "business_id": clean_business_id,
                "name": clean_name,
                "currency": clean_currency,
                "timezone_id": timezone,
            }
        }

        response = await self.execute_operation(
            "CREATE_AD_ACCOUNT",
            variables,
            doc_id=doc_id,
        )

        data = response.get("data")

        if not isinstance(data, dict):
            raise RemoteRequestError(
                "CREATE_AD_ACCOUNT returned no data. "
                f"Meta response: {self._diagnostic(response)}",
                meta_payload=response,
            )

        create_payload = data.get(
            "ad_account_create"
        )

        if not isinstance(create_payload, dict):
            raise RemoteRequestError(
                "CREATE_AD_ACCOUNT response has no "
                "ad_account_create. Meta response: "
                f"{self._diagnostic(response)}",
                meta_payload=response,
            )

        account = create_payload.get(
            "ad_account"
        )

        if not isinstance(account, dict):
            raise RemoteRequestError(
                "CREATE_AD_ACCOUNT response has no "
                "ad_account object. Meta response: "
                f"{self._diagnostic(response)}",
                meta_payload=response,
            )

        account_id = str(
            account.get("id") or ""
        ).strip()

        if not account_id:
            raise RemoteRequestError(
                "CREATE_AD_ACCOUNT returned empty ID. "
                f"Meta response: {self._diagnostic(response)}",
                meta_payload=response,
            )

        log.info(
            "[%s] Ad Account created id=%s business=%s",
            self.session.profile.name,
            account_id,
            clean_business_id,
        )

        return account_id

    # ------------------------------------------------------------------
    # Existing funding credential
    # ------------------------------------------------------------------

    async def link_payment_credential(
        self,
        target_id: str,
        credential_id: str,
        country: str = "US",
        zip_code: str = "10001",
        doc_id: str | None = None,
    ) -> bool:

        clean_id = str(
            target_id or ""
        ).strip()

        clean_credential = str(
            credential_id or ""
        ).strip()

        if clean_id.startswith("act_"):
            clean_id = clean_id[4:]

        if not clean_id:
            raise ValueError(
                "target ad account ID is required"
            )

        if not clean_credential:
            raise ValueError(
                "credential_id is required"
            )

        variables = {
            "input": {
                "client_mutation_id": "1",
                "ad_account_id": f"act_{clean_id}",
                "credential_id": clean_credential,
                "billing_address": {
                    "country_code": str(
                        country or "US"
                    ).strip().upper(),
                    "zip": str(
                        zip_code or ""
                    ).strip(),
                },
            }
        }

        response = await self.execute_operation(
            "LINK_PAYMENT",
            variables,
            doc_id=doc_id,
        )

        data = response.get("data")

        if not isinstance(data, dict):
            return False

        return bool(
            data.get("payment_credential_link")
        )

    # ------------------------------------------------------------------
    # Generic Pages operation
    # ------------------------------------------------------------------

    async def pages_graphql(
        self,
        variables: dict[str, Any],
        *,
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Private Pages query transport.

        Intentionally does NOT invent the variables schema or a doc_id.

        Once we capture the actual Pages request from Facebook, we put its
        doc_id into REMASK_DOC_ID_LIST_PAGES and call this through the same
        session as CREATE_BM.
        """

        return await self.execute_operation(
            "LIST_PAGES",
            variables,
            doc_id=doc_id,
        )


__all__ = [
    "AutomationError",
    "ProxyError",
    "AuthenticationError",
    "RemoteRequestError",
    "WebProfile",
    "FacebookBootstrap",
    "FacebookWebSession",
    "WebSessionManager",
    "BusinessLogicController",
]
