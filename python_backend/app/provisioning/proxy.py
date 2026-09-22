from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from ..session import ProxyCheckError


class ProxyChecker:
    """Cookie-free proxy validation."""

    def __init__(self, proxy: str | None, user_agent: str, timeout_seconds: int = 15) -> None:
        self.proxy = str(proxy or "").strip() or None
        self.user_agent = user_agent
        self.timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=timeout_seconds,
            sock_read=timeout_seconds,
        )

    async def check(self) -> dict[str, Any]:
        if not self.proxy:
            raise ProxyCheckError("proxy is not configured")

        started = time.monotonic()
        facebook_status: int | None = None
        exit_ip = ""
        ip_lookup_error = ""

        # ReMask uses the proxy to reach Facebook, so Facebook connectivity is
        # the authoritative transport test. Third-party IP echo services are
        # diagnostics only and must not create a false PROXY_DEAD.
        fb_timeout = aiohttp.ClientTimeout(
            total=max(5, min(int(self.timeout.total or 15), 15)),
            connect=max(5, min(int(self.timeout.total or 15), 15)),
            sock_read=max(5, min(int(self.timeout.total or 15), 15)),
        )
        try:
            async with aiohttp.ClientSession(
                timeout=fb_timeout,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/plain,*/*;q=0.8",
                },
            ) as client:
                async with client.get(
                    "https://www.facebook.com/robots.txt",
                    proxy=self.proxy,
                    allow_redirects=False,
                ) as response:
                    facebook_status = int(response.status)
                    await response.content.read(256)

                    if facebook_status == 407:
                        raise ProxyCheckError(
                            "proxy authentication rejected (HTTP 407)"
                        )
                    if facebook_status <= 0 or facebook_status >= 500:
                        raise ProxyCheckError(
                            f"Facebook transport returned HTTP {facebook_status}"
                        )
        except ProxyCheckError:
            raise
        except asyncio.TimeoutError as exc:
            raise ProxyCheckError(
                "proxy could not reach Facebook before timeout"
            ) from exc
        except aiohttp.ClientError as exc:
            raise ProxyCheckError(
                "proxy could not reach Facebook: "
                f"{exc.__class__.__name__}"
            ) from exc

        # Best-effort exit IP lookup. Failure is informational because the same
        # proxy has already returned a Facebook HTTP response.
        ip_timeout = aiohttp.ClientTimeout(
            total=7,
            connect=7,
            sock_read=7,
        )
        try:
            async with aiohttp.ClientSession(
                timeout=ip_timeout,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
            ) as client:
                async with client.get(
                    "https://api.ipify.org",
                    params={"format": "json"},
                    proxy=self.proxy,
                ) as response:
                    if response.status == 200:
                        payload = await response.json(content_type=None)
                        if isinstance(payload, dict):
                            exit_ip = str(payload.get("ip") or "").strip()
                    else:
                        ip_lookup_error = f"HTTP {response.status}"
        except Exception as exc:
            ip_lookup_error = exc.__class__.__name__

        return {
            "exit_ip": exit_ip,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "facebook_reachable": True,
            "facebook_http_status": facebook_status,
            "ip_lookup_ok": bool(exit_ip),
            "ip_lookup_error": ip_lookup_error,
        }
