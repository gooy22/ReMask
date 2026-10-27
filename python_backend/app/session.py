from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from fb_worker import (
    BusinessLogicController,
    FacebookWebSession,
    WebProfile,
)
from .facebook_graph_api import FacebookGraphApi
from .direct_test import direct_test_deadline, direct_test_result


class ProfileContextError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        category: str = "profile_context",
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.category = str(category or "profile_context").strip()

    @property
    def code(self) -> str:
        """Preserve typed Meta auth gates while keeping resolver failures distinct."""
        return {
            "business_login_gate": "BUSINESS_LOGIN_GATE",
            "session_expired": "SESSION_EXPIRED",
            "checkpoint_required": "CHECKPOINT_REQUIRED",
            "two_factor_required": "TWO_FACTOR_REQUIRED",
        }.get(self.category, "PROFILE_CONTEXT_ERROR")


class ProxyCheckError(RuntimeError):
    pass


@dataclass(slots=True)
class ProfileContext:
    profile_id: str
    cookies: dict[str, str]
    proxy: str | None
    user_agent: str
    access_token: str = ""
    display_name: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    pages: list[dict[str, Any]] | None = None
    businesses: list[dict[str, Any]] | None = None
    ad_accounts: list[dict[str, Any]] | None = None
    inventory_updated_at: int = 0
    inventory_source: str = ""
    direct_test_until: int = 0
    _cookie_persistor: Any = field(default=None, repr=False, compare=False)
    _cookie_revoker: Any = field(default=None, repr=False, compare=False)
    _loaded_cookies: dict[str, str] = field(default_factory=dict, repr=False, compare=False)



class ProfileResolver:
    def __init__(
        self,
        resolver_url: str | None,
        internal_key: str | None,
        timeout: int = 15,
    ) -> None:
        self.url = str(resolver_url or "").strip() or None
        self.key = internal_key

        clean_url = (self.url or "").lower()
        self.is_loopback = (
            clean_url.startswith("http://127.0.0.1")
            or clean_url.startswith("http://localhost")
            or clean_url.startswith("http://[::1]")
        )

        try:
            configured_attempts = int(
                os.getenv("REMASK_PROFILE_RESOLVER_ATTEMPTS")
                or ("6" if self.is_loopback else "3")
            )
        except (TypeError, ValueError):
            configured_attempts = 6 if self.is_loopback else 3
        self.attempts = max(1, min(configured_attempts, 10))

        try:
            configured_backoff = float(
                os.getenv("REMASK_PROFILE_RESOLVER_BACKOFF_SECONDS")
                or "0.20"
            )
        except (TypeError, ValueError):
            configured_backoff = 0.20
        self.backoff_seconds = max(0.0, min(configured_backoff, 2.0))

        try:
            configured_timeout = int(
                os.getenv("REMASK_PROFILE_RESOLVER_TIMEOUT_SECONDS")
                or timeout
            )
        except (TypeError, ValueError):
            configured_timeout = int(timeout)
        self.timeout = aiohttp.ClientTimeout(
            total=max(1, min(configured_timeout, 30))
        )

    @staticmethod
    def _detail(payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""
        raw_detail = payload.get("detail")
        if isinstance(raw_detail, dict):
            detail = str(raw_detail.get("message") or "").strip()
        elif raw_detail is not None:
            detail = str(raw_detail).strip()
        else:
            detail = ""
        if not detail:
            detail = str(payload.get("error") or "").strip()
        return detail

    @staticmethod
    def _retryable_http(status: int) -> bool:
        return status in {408, 425, 429} or status >= 500

    def _retry_delay(self, attempt: int) -> float:
        if self.backoff_seconds <= 0:
            return 0.0
        return min(
            2.0,
            self.backoff_seconds * (2 ** max(0, int(attempt) - 1)),
        )

    async def _fetch(
        self,
        *,
        params: dict[str, str],
        label: str,
    ) -> dict[str, Any]:
        if not self.url:
            raise ProfileContextError(
                "REMASK_PROFILE_RESOLVER_URL is not configured",
                retryable=False,
                category="configuration",
            )

        headers = {"Accept": "application/json"}
        if self.key:
            headers["X-Remask-Internal-Key"] = self.key

        for attempt in range(1, self.attempts + 1):
            try:
                async with aiohttp.ClientSession(
                    timeout=self.timeout,
                    headers=headers,
                ) as client:
                    async with client.get(self.url, params=params) as response:
                        try:
                            payload = await response.json(content_type=None)
                        except (ValueError, UnicodeError) as exc:
                            retryable = (
                                response.status < 400
                                or self._retryable_http(response.status)
                            )
                            message = (
                                f"{label} returned invalid JSON "
                                f"(HTTP {response.status})"
                            )
                            if retryable and attempt < self.attempts:
                                logging.getLogger("remask.profile_resolver").warning(
                                    "%s attempt=%d/%d retryable=true error=%s",
                                    label,
                                    attempt,
                                    self.attempts,
                                    message,
                                )
                                delay = self._retry_delay(attempt)
                                if delay:
                                    await asyncio.sleep(delay)
                                continue
                            raise ProfileContextError(
                                message,
                                retryable=retryable,
                                category="resolver_response",
                            ) from exc

                        if response.status >= 400 or not isinstance(payload, dict):
                            auth_error = (payload.get("error") if isinstance(payload, dict) else "")
                            if response.status == 409 and auth_error == "BUSINESS_LOGIN_GATE":
                                raise ProfileContextError(
                                    "Saved Facebook session was explicitly invalidated by Meta.",
                                    retryable=False,
                                    category="business_login_gate",
                                )
                            detail = self._detail(payload)
                            suffix = f": {detail}" if detail else ""
                            retryable = self._retryable_http(response.status)
                            message = (
                                f"{label} HTTP {response.status}{suffix}"
                                if response.status >= 400
                                else f"{label} returned invalid response"
                            )
                            if retryable and attempt < self.attempts:
                                logging.getLogger("remask.profile_resolver").warning(
                                    "%s attempt=%d/%d retryable=true error=%s",
                                    label,
                                    attempt,
                                    self.attempts,
                                    message,
                                )
                                delay = self._retry_delay(attempt)
                                if delay:
                                    await asyncio.sleep(delay)
                                continue
                            raise ProfileContextError(
                                message,
                                retryable=retryable,
                                category="resolver_http",
                            )

                        if attempt > 1:
                            logging.getLogger("remask.profile_resolver").info(
                                "%s recovered attempt=%d/%d",
                                label,
                                attempt,
                                self.attempts,
                            )
                        return payload

            except ProfileContextError:
                raise
            except asyncio.TimeoutError as exc:
                message = (
                    f"{label} timeout"
                    if self.attempts == 1
                    else f"{label} timeout after {self.attempts} attempts"
                )
                if attempt < self.attempts:
                    logging.getLogger("remask.profile_resolver").warning(
                        "%s attempt=%d/%d retryable=true error=TimeoutError",
                        label,
                        attempt,
                        self.attempts,
                    )
                    delay = self._retry_delay(attempt)
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                raise ProfileContextError(
                    message,
                    retryable=True,
                    category="resolver_transport",
                ) from exc
            except aiohttp.ClientError as exc:
                message = (
                    f"{label} network error: {exc.__class__.__name__}"
                    if self.attempts == 1
                    else (
                        f"{label} network error after {self.attempts} attempts: "
                        f"{exc.__class__.__name__}"
                    )
                )
                if attempt < self.attempts:
                    logging.getLogger("remask.profile_resolver").warning(
                        "%s attempt=%d/%d retryable=true error=%s",
                        label,
                        attempt,
                        self.attempts,
                        exc.__class__.__name__,
                    )
                    delay = self._retry_delay(attempt)
                    if delay:
                        await asyncio.sleep(delay)
                    continue
                raise ProfileContextError(
                    message,
                    retryable=True,
                    category="resolver_transport",
                ) from exc

        raise ProfileContextError(
            f"{label} exhausted resolver attempts",
            retryable=True,
            category="resolver_transport",
        )

    async def list_profiles(self) -> list[dict[str, Any]]:
        payload = await self._fetch(
            params={"action": "list"},
            label="profile resolver list",
        )
        profiles = payload.get("profiles") or []
        if not isinstance(profiles, list):
            raise ProfileContextError(
                "profile resolver list returned invalid profiles",
                retryable=False,
                category="resolver_response",
            )
        return [p for p in profiles if isinstance(p, dict)]

    async def persist_cookie_refresh(
        self, profile_id: str, actor_id: str, expected_xs: str,
        cookies: dict[str, str],
    ) -> bool:
        """Write Meta-issued cookies back only to the same saved profile."""
        profile_id = str(profile_id or "").strip()
        actor_id = str(actor_id or "").strip()
        expected_xs = str(expected_xs or "")
        if (not self.url or not self.key or not profile_id or not actor_id
                or not expected_xs or not isinstance(cookies, dict)
                or len(cookies) > 500
                or str(cookies.get("c_user") or "") != actor_id
                or not str(cookies.get("xs") or "").strip()):
            return False
        if any(not isinstance(k, str) or not k or not isinstance(v, str)
               or len(k) > 256 or len(v) > 8192 for k, v in cookies.items()):
            return False
        headers = {
            "Accept": "application/json",
            "X-Remask-Internal-Key": str(self.key),
        }
        payload = {
            "action": "refresh_profile_cookies",
            "profile_id": profile_id,
            "actor_id": actor_id,
            "expected_xs": expected_xs,
            "cookies": cookies,
        }
        try:
            # This is a single idempotent local profile-store update. Never
            # retry with stale auth material or include it in diagnostics.
            async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as client:
                async with client.post(self.url, json=payload) as response:
                    if response.status >= 400:
                        logging.getLogger("remask.profile_resolver").warning(
                            "profile_cookie_refresh_rejected profile=%s status=%d",
                            profile_id, response.status,
                        )
                        return False
                    result = await response.json(content_type=None)
                    return (isinstance(result, dict) and result.get("ok") is True
                            and result.get("updated") is True)
        except Exception as exc:
            logging.getLogger("remask.profile_resolver").warning(
                "profile_cookie_refresh_failed profile=%s kind=%s",
                profile_id, type(exc).__name__,
            )
            return False

    async def persist_cookie_revocation(
        self, profile_id: str, actor_id: str, expected_xs: str,
    ) -> bool:
        """Persist a same-profile marker after Meta explicitly deletes auth cookies."""
        profile_id = str(profile_id or "").strip()
        actor_id = str(actor_id or "").strip()
        expected_xs = str(expected_xs or "")
        if not self.url or not self.key or not profile_id or not actor_id or not expected_xs:
            return False
        payload = {
            "action": "mark_profile_cookie_auth_revoked",
            "profile_id": profile_id,
            "actor_id": actor_id,
            "expected_xs_sha256": hashlib.sha256(expected_xs.encode("utf-8")).hexdigest(),
        }
        headers = {
            "Accept": "application/json",
            "X-Remask-Internal-Key": str(self.key),
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as client:
                async with client.post(self.url, json=payload) as response:
                    if response.status >= 400:
                        logging.getLogger("remask.profile_resolver").warning(
                            "profile_cookie_revocation_rejected profile=%s status=%d",
                            profile_id, response.status,
                        )
                        return False
                    result = await response.json(content_type=None)
                    return (isinstance(result, dict) and result.get("ok") is True
                            and result.get("updated") is True)
        except Exception as exc:
            logging.getLogger("remask.profile_resolver").warning(
                "profile_cookie_revocation_failed profile=%s kind=%s",
                profile_id, type(exc).__name__,
            )
            return False

    async def persist_cookie_auth_verification(
        self, profile_id: str, actor_id: str, expected_xs_sha256: str,
    ) -> bool:
        """Clear a revocation marker only after a read-only Meta identity check."""
        profile_id = str(profile_id or "").strip()
        actor_id = str(actor_id or "").strip()
        expected_hash = str(expected_xs_sha256 or "").strip().lower()
        if (not self.url or not self.key or not profile_id or not actor_id
                or len(expected_hash) != 64
                or any(char not in "0123456789abcdef" for char in expected_hash)):
            return False
        payload = {
            "action": "clear_profile_cookie_auth_verified",
            "profile_id": profile_id,
            "actor_id": actor_id,
            "expected_xs_sha256": expected_hash,
        }
        headers = {
            "Accept": "application/json",
            "X-Remask-Internal-Key": str(self.key),
        }
        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as client:
                async with client.post(self.url, json=payload) as response:
                    if response.status >= 400:
                        logging.getLogger("remask.profile_resolver").warning(
                            "profile_cookie_auth_verification_rejected profile=%s status=%d",
                            profile_id, response.status,
                        )
                        return False
                    result = await response.json(content_type=None)
                    return isinstance(result, dict) and result.get("ok") is True
        except Exception as exc:
            logging.getLogger("remask.profile_resolver").warning(
                "profile_cookie_auth_verification_failed profile=%s kind=%s",
                profile_id, type(exc).__name__,
            )
            return False

    async def _revalidate_marked_cookie_auth(
        self, context: "ProfileContext", expected_xs_sha256: str,
    ) -> None:
        """Recheck a marked profile with one read-only Business page GET."""
        actor_id = str(context.cookies.get("c_user") or "").strip()
        original_xs = str(context.cookies.get("xs") or "")
        expected_hash = str(expected_xs_sha256 or "").strip().lower()
        if (not actor_id or not original_xs
                or hashlib.sha256(original_xs.encode("utf-8")).hexdigest() != expected_hash):
            raise ProfileContextError(
                "Saved profile session changed before Meta revalidation.",
                retryable=True, category="profile_session_changed",
            )

        session = MetaSession(context)
        web = None
        auth_gate_code = ""
        evidence: dict[str, Any] = {}
        status = 0
        failure: Exception | None = None
        try:
            web = await session.facebook_web()
            status, body, final_url = await web.fetch_text(
                "https://business.facebook.com/latest/home/",
                max_bytes=5_000_000,
                document_navigation=True,
            )
            from .meta_auth_document import auth_gate
            auth_gate_code = auth_gate(final_url, body)
            if not auth_gate_code:
                from .private_ads_http import _bootstrap_evidence
                evidence = _bootstrap_evidence(final_url, body, web.profile, actor_id)
        except Exception as exc:
            failure = exc
        finally:
            try:
                await session.close()
            except Exception as exc:
                if failure is None:
                    failure = exc

        if failure is not None:
            raise ProfileContextError(
                "Meta session revalidation could not complete.",
                retryable=True, category="auth_revalidation_transport",
            ) from failure

        if auth_gate_code:
            category = {
                "BUSINESS_LOGIN_GATE": "business_login_gate",
                "SESSION_EXPIRED": "session_expired",
                "CHECKPOINT_REQUIRED": "checkpoint_required",
                "TWO_FACTOR_REQUIRED": "two_factor_required",
            }.get(auth_gate_code, "auth_revalidation_unconfirmed")
            raise ProfileContextError(
                "Meta returned an authentication gate during read-only revalidation.",
                retryable=False, category=category,
            )
        if bool(getattr(web, "auth_cookie_revoked", False)):
            raise ProfileContextError(
                "Meta removed an authentication cookie during read-only revalidation.",
                retryable=False, category="session_expired",
            )

        if (status != 200
                or evidence.get("final_host") != "business.facebook.com"
                or evidence.get("document_actor") != "matches"):
            if evidence.get("document_actor") in {"logged_out", "mismatch"}:
                raise ProfileContextError(
                    "Meta returned a logged-out or different actor for this profile.",
                    retryable=False, category="session_expired",
                )
            raise ProfileContextError(
                "Meta did not provide enough read-only identity evidence to clear the marker.",
                retryable=True, category="auth_revalidation_unconfirmed",
            )

        current_cookies = getattr(context, "cookies", {})
        current_actor = str(current_cookies.get("c_user") or "") if isinstance(current_cookies, dict) else ""
        current_xs = str(current_cookies.get("xs") or "") if isinstance(current_cookies, dict) else ""
        if current_actor != actor_id or not current_xs:
            raise ProfileContextError(
                "Meta session identity changed during revalidation.",
                retryable=True, category="profile_session_changed",
            )
        current_hash = hashlib.sha256(current_xs.encode("utf-8")).hexdigest()
        if current_hash != expected_hash:
            # Meta rotated cookies and the existing compare-and-swap refresh
            # already cleared the stale marker for the newly saved session.
            return
        if not await self.persist_cookie_auth_verification(
            str(context.profile_id), actor_id, expected_hash,
        ):
            raise ProfileContextError(
                "Saved profile changed during Meta revalidation.",
                retryable=True, category="profile_session_changed",
            )

    async def resolve(self, profile_id: str) -> ProfileContext:
        payload = await self._fetch(
            params={"profile_id": profile_id},
            label="profile resolver",
        )

        cookies = payload.get("cookies") or {}
        user_agent = str(payload.get("user_agent") or "").strip()
        if not isinstance(cookies, dict) or not user_agent:
            raise ProfileContextError(
                "profile resolver returned incomplete context",
                retryable=False,
                category="invalid_profile_context",
            )

        cookie_map = {str(k): str(v) for k, v in cookies.items()}
        missing_auth_cookies = [
            key for key in ("c_user", "xs")
            if not str(cookie_map.get(key) or "").strip()
        ]
        if missing_auth_cookies:
            raise ProfileContextError(
                "profile resolver returned no logged-in Facebook session: missing "
                + ", ".join(missing_auth_cookies),
                retryable=False,
                category="invalid_profile_context",
            )

        direct_until = direct_test_deadline(profile_id, cookie_map.get('c_user', ''))
        proxy = None if direct_until else str(payload.get("proxy") or "").strip() or None
        if not proxy and not direct_until:
            raise ProfileContextError(
                "profile proxy is not configured",
                retryable=False,
                category="invalid_profile_context",
            )

        context = ProfileContext(
            profile_id=profile_id,
            cookies=cookie_map,
            proxy=proxy,
            direct_test_until=direct_until,
            user_agent=user_agent,
            access_token="",
            display_name=(
                ""
                if (
                    str(payload.get("display_name") or "").strip() == profile_id
                    or str(payload.get("display_name") or "").strip().isdigit()
                )
                else str(payload.get("display_name") or "").strip()
            ),
            email=str(payload.get("email") or "").strip(),
            first_name=str(payload.get("first_name") or "").strip(),
            last_name=str(payload.get("last_name") or "").strip(),
            pages=[
                row
                for row in (payload.get("pages") or [])
                if isinstance(row, dict)
                and str(row.get("id") or "").strip().isdigit()
            ],
            businesses=[
                row
                for row in (payload.get("businesses") or [])
                if isinstance(row, dict)
                and str(row.get("business_id") or row.get("id") or "").strip().isdigit()
            ],
            ad_accounts=[
                row
                for row in (payload.get("ad_accounts") or [])
                if isinstance(row, dict)
                and str(
                    row.get("ad_account_id")
                    or row.get("account_id")
                    or row.get("id")
                    or ""
                ).removeprefix("act_").strip().isdigit()
                and str(row.get("business_id") or "").strip().isdigit()
            ],
            inventory_updated_at=max(
                0,
                int(payload.get("inventory_updated_at") or 0),
            ),
            inventory_source=str(payload.get("inventory_source") or "").strip(),
        )
        context._loaded_cookies = dict(cookie_map)
        context._cookie_persistor = self.persist_cookie_refresh
        context._cookie_revoker = self.persist_cookie_revocation
        marker_hash = str(payload.get("cookie_auth_revocation_xs_sha256") or "").strip().lower()
        if marker_hash:
            if (len(marker_hash) != 64
                    or any(char not in "0123456789abcdef" for char in marker_hash)):
                raise ProfileContextError(
                    "profile resolver returned an invalid auth-state marker",
                    retryable=False, category="invalid_profile_context",
                )
            await self._revalidate_marked_cookie_auth(context, marker_hash)
        return context


class MetaSession:
    """Lazy profile session protected by a double-check asyncio lock."""

    def __init__(
        self,
        context: ProfileContext,
        timeout_seconds: int = 15,
        pool_size: int = 20,
    ) -> None:
        self.context = context
        self.timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=timeout_seconds,
            sock_read=timeout_seconds,
        )
        self.pool_size = max(1, pool_size)
        self._session: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()
        self._facebook_session: FacebookWebSession | None = None
        self._facebook_lock = asyncio.Lock()
        self._facebook_cookie_baseline: dict[str, str] | None = None
        self._facebook_profile_id = ""
        self._graph_api: FacebookGraphApi | None = None
        self._graph_lock = asyncio.Lock()
        self._business_browser: Any | None = None
        self._business_browser_lock = asyncio.Lock()
        self._direct_browser_timer: asyncio.Task | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        session = self._session
        if session is not None and not session.closed:
            return session

        async with self._session_lock:
            session = self._session
            if session is None or session.closed:
                connector = aiohttp.TCPConnector(
                    limit=self.pool_size,
                    limit_per_host=self.pool_size,
                    enable_cleanup_closed=True,
                )
                self._session = aiohttp.ClientSession(
                    timeout=self.timeout,
                    connector=connector,
                    headers={
                        "User-Agent": self.context.user_agent,
                        "Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                    },
                )
            return self._session

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> aiohttp.ClientResponse:
        if (self.context.direct_test_until and
                direct_test_deadline(self.context.profile_id, self.context.cookies.get('c_user', '')) != self.context.direct_test_until):
            raise ProfileContextError('DIRECT_TEST_EXPIRED', retryable=False)
        host=(urlsplit(str(url)).hostname or '').lower()
        if host == 'facebook.com' or host.endswith('.facebook.com') or host == 'fbcdn.net' or host.endswith('.fbcdn.net'):
            raise ProfileContextError('PRIVATE_HTTP_REQUIRED: use the profile HTTP/2 transport', retryable=False)
        session = await self._get_session()
        return await session.request(method, url, **kwargs)

    async def facebook_web(self) -> FacebookWebSession:
        current = self._facebook_session
        if current is not None:
            return current

        async with self._facebook_lock:
            current = self._facebook_session
            if current is None:
                cookies = dict(self.context.cookies)
                loaded = getattr(self.context, '_loaded_cookies', None) or cookies
                # Freeze this client's durable baseline before another session
                # can update the shared context. It is the CAS expectation,
                # never the context's possibly newer value at close time.
                self._facebook_cookie_baseline = dict(cookies) if loaded == cookies else None
                self._facebook_profile_id = str(self.context.profile_id)
                profile = WebProfile(
                    name=self.context.profile_id,
                    cookies=cookies,
                    proxy=self.context.proxy,
                    user_agent=self.context.user_agent,
                )
                current = FacebookWebSession(
                    profile,
                    timeout_seconds=max(15, int(self.timeout.total or 15)),
                    pool_size=self.pool_size,
                )
                await current.__aenter__()
                self._facebook_session = current
            return current

    async def facebook_controller(self) -> BusinessLogicController:
        return BusinessLogicController(await self.facebook_web())

    async def facebook_business_browser(self):
        direct_until = getattr(self.context, 'direct_test_until', 0)
        if direct_until and direct_test_deadline(self.context.profile_id, self.context.cookies.get('c_user', '')) != direct_until:
            raise ProfileContextError('DIRECT_TEST_EXPIRED', retryable=False)
        current = self._business_browser
        if current is not None:
            return current

        async with self._business_browser_lock:
            current = self._business_browser
            if current is None:
                from .facebook_business_browser import FacebookBusinessBrowser

                try:
                    browser_timeout = int(
                        os.getenv("REMASK_BM_BROWSER_TIMEOUT_SECONDS", "45")
                    )
                except (TypeError, ValueError):
                    browser_timeout = 45
                browser_timeout = max(
                    20,
                    min(
                        browser_timeout,
                        90,
                    ),
                )

                current = FacebookBusinessBrowser(
                    self.context,
                    timeout_seconds=browser_timeout,
                )
                await current.open()
                self._business_browser = current
                if direct_until:
                    async def expire_direct_browser():
                        await asyncio.sleep(max(0, direct_until - time.time()))
                        await self.close_business_browser()
                    self._direct_browser_timer = asyncio.create_task(expire_direct_browser())
            return current

    async def graph_api(self) -> FacebookGraphApi:
        raise ProfileContextError(
            'COOKIE_ONLY_GRAPH_DISABLED: use the profile cookie session',
            retryable=False,
        )

    async def close_business_browser(self) -> None:
        """Release Chromium between provisioning phases, retaining HTTP state."""
        async with self._business_browser_lock:
            timer = self._direct_browser_timer
            self._direct_browser_timer = None
            if timer is not None and timer is not asyncio.current_task():
                timer.cancel()
                await asyncio.gather(timer, return_exceptions=True)
            if self._business_browser is not None:
                await self._business_browser.close()
                self._business_browser = None

    async def close(self) -> None:
        await self.close_business_browser()

        async with self._graph_lock:
            if self._graph_api is not None:
                await self._graph_api.close()
                self._graph_api = None

        refreshed_cookies: dict[str, str] | None = None
        initial: dict[str, str] = {}
        auth_cookie_revoked = False
        profile_id = ""
        async with self._facebook_lock:
            if self._facebook_session is not None:
                initial = dict(self._facebook_cookie_baseline or {})
                profile_id = self._facebook_profile_id
                current = self._facebook_session
                await current.close()
                auth_cookie_revoked = bool(getattr(current, "auth_cookie_revoked", False))
                refreshed_cookies = dict(current.profile.cookies)
                self._facebook_session = None
                self._facebook_cookie_baseline = None
                self._facebook_profile_id = ""

        current_cookies = getattr(self.context, "cookies", None)
        actor_id = str(initial.get("c_user") or "")
        if (auth_cookie_revoked and actor_id and str(initial.get("xs") or "")
                and str(getattr(self.context, "profile_id", "")) == profile_id
                and isinstance(current_cookies, dict) and current_cookies == initial):
            revoker = getattr(self.context, "_cookie_revoker", None)
            if callable(revoker):
                try:
                    await revoker(profile_id, actor_id, str(initial.get("xs") or ""))
                except Exception as exc:
                    logging.getLogger("remask.profile_resolver").warning(
                        "profile_cookie_revocation_failed profile=%s kind=%s",
                        profile_id, type(exc).__name__,
                    )

        if (refreshed_cookies is not None and actor_id
                and str(getattr(self.context, 'profile_id', '')) == profile_id
                and isinstance(current_cookies, dict)
                and current_cookies == initial
                and refreshed_cookies.get("c_user") == actor_id
                and str(refreshed_cookies.get("xs") or "").strip()
                and refreshed_cookies != initial):
            persistor = getattr(self.context, "_cookie_persistor", None)
            persisted = not callable(persistor)
            if callable(persistor):
                try:
                    persisted = await persistor(
                        profile_id, actor_id,
                        str(initial.get("xs") or ""), refreshed_cookies,
                    )
                except Exception as exc:
                    logging.getLogger("remask.profile_resolver").warning(
                        "profile_cookie_refresh_failed profile=%s kind=%s",
                        profile_id, type(exc).__name__,
                    )
            if (persisted and getattr(self.context, 'cookies', None) == initial
                    and str(getattr(self.context, 'profile_id', '')) == profile_id):
                self.context.cookies = refreshed_cookies
                if hasattr(self.context, '_loaded_cookies'):
                    self.context._loaded_cookies = dict(refreshed_cookies)

        async with self._session_lock:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None

    async def __aenter__(self) -> "MetaSession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def proxy_check(self) -> dict[str, Any]:
        from .provisioning.proxy import ProxyChecker

        if getattr(self.context, 'direct_test_until', 0):
            result = direct_test_result(self.context.profile_id, self.context.cookies.get('c_user', ''))
            if result is None:
                raise ProxyCheckError('DIRECT_TEST_EXPIRED')
            return result
        return await ProxyChecker(
            self.context.proxy,
            self.context.user_agent,
        ).check()


class ProfileSession(MetaSession):
    """Backward-compatible name used by the existing worker registry."""

    pass
