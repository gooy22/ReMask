from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiohttp

from fb_worker import (
    BusinessLogicController,
    FacebookWebSession,
    WebProfile,
)
from .facebook_graph_api import FacebookGraphApi


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

        proxy = str(payload.get("proxy") or "").strip() or None
        if not proxy:
            raise ProfileContextError(
                "profile proxy is not configured",
                retryable=False,
                category="invalid_profile_context",
            )

        return ProfileContext(
            profile_id=profile_id,
            cookies=cookie_map,
            proxy=proxy,
            user_agent=user_agent,
            access_token=str(payload.get("access_token") or "").strip(),
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
        )


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
        self._graph_api: FacebookGraphApi | None = None
        self._graph_lock = asyncio.Lock()
        self._business_browser: Any | None = None
        self._business_browser_lock = asyncio.Lock()

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
                    cookies=self.context.cookies,
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
        session = await self._get_session()
        return await session.request(method, url, **kwargs)

    async def facebook_web(self) -> FacebookWebSession:
        current = self._facebook_session
        if current is not None:
            return current

        async with self._facebook_lock:
            current = self._facebook_session
            if current is None:
                profile = WebProfile(
                    name=self.context.profile_id,
                    cookies=dict(self.context.cookies),
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

    def _business_browser_timeout(self) -> int:
        try:
            browser_timeout = int(
                os.getenv("REMASK_BM_BROWSER_TIMEOUT_SECONDS", "45")
            )
        except (TypeError, ValueError):
            browser_timeout = 45
        return max(20, min(browser_timeout, 90))

    async def facebook_business_browser(self):
        current = self._business_browser
        if current is not None:
            return current

        async with self._business_browser_lock:
            current = self._business_browser
            if current is None:
                from .facebook_business_browser import FacebookBusinessBrowser

                current = FacebookBusinessBrowser(
                    self.context,
                    timeout_seconds=self._business_browser_timeout(),
                )
                await current.open()
                self._business_browser = current
            return current

    @asynccontextmanager
    async def fresh_facebook_business_browser(
        self,
        *,
        timeout_seconds: int | None = None,
    ):
        """Yield one fresh profile browser without self-deadlocking the pool.

        ReMask production normally permits one Chromium lease at a time. Older
        reconciliation code opened an independent FacebookBusinessBrowser while
        the ProfileSession still owned its cached browser, so the second browser
        waited on the global semaphore until BROWSER_QUEUE_TIMEOUT. Reset the
        cached lease first, then open the fresh observation in the same session.
        """
        from .facebook_business_browser import FacebookBusinessBrowser

        async with self._business_browser_lock:
            previous = self._business_browser
            self._business_browser = None
            if previous is not None:
                await previous.close()

            bounded_timeout = (
                self._business_browser_timeout()
                if timeout_seconds is None
                else max(20, min(int(timeout_seconds), 90))
            )
            current = FacebookBusinessBrowser(
                self.context,
                timeout_seconds=bounded_timeout,
            )
            await current.open()
            self._business_browser = current

        try:
            yield current
        finally:
            async with self._business_browser_lock:
                if self._business_browser is current:
                    self._business_browser = None
                await current.close()

    async def graph_api(self) -> FacebookGraphApi:
        current = self._graph_api
        if current is not None:
            return current

        async with self._graph_lock:
            current = self._graph_api
            if current is None:
                if not self.context.access_token:
                    raise ProfileContextError(
                        "profile access token is not configured"
                    )
                current = FacebookGraphApi(
                    access_token=self.context.access_token,
                    proxy=self.context.proxy,
                    user_agent=self.context.user_agent,
                    timeout_seconds=max(15, int(self.timeout.total or 15)),
                )
                self._graph_api = current
            return current

    async def close(self) -> None:
        async with self._business_browser_lock:
            if self._business_browser is not None:
                await self._business_browser.close()
                self._business_browser = None

        async with self._graph_lock:
            if self._graph_api is not None:
                await self._graph_api.close()
                self._graph_api = None

        async with self._facebook_lock:
            if self._facebook_session is not None:
                await self._facebook_session.close()
                self._facebook_session = None

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

        return await ProxyChecker(
            self.context.proxy,
            self.context.user_agent,
        ).check()


class ProfileSession(MetaSession):
    """Backward-compatible name used by the existing worker registry."""

    pass
