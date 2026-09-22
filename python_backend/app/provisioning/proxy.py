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
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        try:
            async with aiohttp.ClientSession(timeout=self.timeout, headers=headers) as client:
                async with client.get(
                    "https://api.ipify.org",
                    params={"format": "json"},
                    proxy=self.proxy,
                ) as response:
                    payload = await response.json(content_type=None)
                    if response.status != 200:
                        raise ProxyCheckError(f"proxy check HTTP {response.status}")
                    if not isinstance(payload, dict) or not payload.get("ip"):
                        raise ProxyCheckError("proxy checker returned no exit IP")
        except asyncio.TimeoutError as exc:
            raise ProxyCheckError("proxy timeout") from exc
        except aiohttp.ClientError as exc:
            raise ProxyCheckError(f"proxy network error: {exc.__class__.__name__}") from exc
        except ValueError as exc:
            raise ProxyCheckError("proxy checker returned invalid JSON") from exc

        return {
            "exit_ip": str(payload["ip"]),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
