from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

from fb_worker import (
    BusinessLogicController,
    FacebookWebSession,
    WebProfile,
)


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
                        raise ProfileContextError(
                            f"profile resolver list HTTP {response.status}"
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
                        raise ProfileContextError(
                            f"profile resolver HTTP {response.status}"
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

        return ProfileContext(
            profile_id=profile_id,
            cookies={str(k): str(v) for k, v in cookies.items()},
            proxy=payload.get("proxy"),
            user_agent=user_agent,
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

    async def close(self) -> None:
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
