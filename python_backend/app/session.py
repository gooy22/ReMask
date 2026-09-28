from __future__ import annotations

import asyncio
import os
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
    pass


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
        self.url = resolver_url
        self.key = internal_key
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def list_profiles(self) -> list[dict[str, Any]]:
        if not self.url:
            raise ProfileContextError("REMASK_PROFILE_RESOLVER_URL is not configured")
        headers = {"Accept": "application/json"}
        if self.key:
            headers["X-Remask-Internal-Key"] = self.key
        try:
            async with aiohttp.ClientSession(
                timeout=self.timeout,
                headers=headers,
            ) as client:
                async with client.get(self.url, params={"action": "list"}) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400 or not isinstance(payload, dict):
                        detail = ""
                        if isinstance(payload, dict):
                            raw_detail = payload.get("detail")
                            if isinstance(raw_detail, dict):
                                detail = str(raw_detail.get("message") or "").strip()
                            elif raw_detail is not None:
                                detail = str(raw_detail).strip()
                            if not detail:
                                detail = str(payload.get("error") or "").strip()
                        suffix = f": {detail}" if detail else ""
                        raise ProfileContextError(
                            f"profile resolver list HTTP {response.status}{suffix}"
                        )
        except asyncio.TimeoutError as exc:
            raise ProfileContextError("profile resolver list timeout") from exc
        except aiohttp.ClientError as exc:
            raise ProfileContextError(
                f"profile resolver list network error: {exc.__class__.__name__}"
            ) from exc

        profiles = payload.get("profiles") or []
        if not isinstance(profiles, list):
            raise ProfileContextError("profile resolver list returned invalid profiles")
        return [p for p in profiles if isinstance(p, dict)]

    async def resolve(self, profile_id: str) -> ProfileContext:
        if not self.url:
            raise ProfileContextError("REMASK_PROFILE_RESOLVER_URL is not configured")
        headers = {"Accept": "application/json"}
        if self.key:
            headers["X-Remask-Internal-Key"] = self.key
        try:
            async with aiohttp.ClientSession(
                timeout=self.timeout,
                headers=headers,
            ) as client:
                async with client.get(
                    self.url,
                    params={"profile_id": profile_id},
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status >= 400 or not isinstance(payload, dict):
                        detail = ""
                        if isinstance(payload, dict):
                            raw_detail = payload.get("detail")
                            if isinstance(raw_detail, dict):
                                detail = str(raw_detail.get("message") or "").strip()
                            elif raw_detail is not None:
                                detail = str(raw_detail).strip()
                            if not detail:
                                detail = str(payload.get("error") or "").strip()
                        suffix = f": {detail}" if detail else ""
                        raise ProfileContextError(
                            f"profile resolver HTTP {response.status}{suffix}"
                        )
        except asyncio.TimeoutError as exc:
            raise ProfileContextError("profile resolver timeout") from exc
        except aiohttp.ClientError as exc:
            raise ProfileContextError(
                f"profile resolver network error: {exc.__class__.__name__}"
            ) from exc

        cookies = payload.get("cookies") or {}
        user_agent = str(payload.get("user_agent") or "").strip()
        if not isinstance(cookies, dict) or not user_agent:
            raise ProfileContextError("profile resolver returned incomplete context")

        cookie_map = {str(k): str(v) for k, v in cookies.items()}
        missing_auth_cookies = [
            key for key in ("c_user", "xs")
            if not str(cookie_map.get(key) or "").strip()
        ]
        if missing_auth_cookies:
            raise ProfileContextError(
                "profile resolver returned no logged-in Facebook session: missing "
                + ", ".join(missing_auth_cookies)
            )

        proxy = str(payload.get("proxy") or "").strip() or None
        if not proxy:
            raise ProfileContextError("profile proxy is not configured")

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

    async def facebook_business_browser(self):
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
            return current

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
