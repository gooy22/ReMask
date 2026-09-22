from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aiohttp


class TransportError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class RouteConfig:
    url: str
    timeout: int = 30
    headers: dict[str, str] | None = None


class ProvisioningTransport:
    """Route-based transport for provisioning mutation handlers."""

    REQUIRED_RESULT_KEYS = {
        "BUSINESS": "business_id",
        "AD_ACCOUNT": "ad_account_id",
        "FUNDING": "funding_source_id",
    }
    BLOCKED_PAYMENT_KEYS = {
        "card_number",
        "cardnumber",
        "pan",
        "cvv",
        "cvc",
        "csc",
        "security_code",
    }

    def __init__(self, raw_config: str | None = None) -> None:
        raw = raw_config if raw_config is not None else os.getenv(
            "REMASK_PROVISIONING_ROUTES_JSON", "{}"
        )
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise TransportError(
                "TRANSPORT_CONFIG_ERROR",
                "REMASK_PROVISIONING_ROUTES_JSON is invalid JSON",
            ) from exc
        if not isinstance(parsed, dict):
            raise TransportError(
                "TRANSPORT_CONFIG_ERROR",
                "REMASK_PROVISIONING_ROUTES_JSON must be an object",
            )

        self.routes: dict[str, RouteConfig] = {}
        for step in self.REQUIRED_RESULT_KEYS:
            cfg = parsed.get(step)
            if cfg is None:
                continue
            if isinstance(cfg, str):
                cfg = {"url": cfg}
            if not isinstance(cfg, dict):
                raise TransportError(
                    "TRANSPORT_CONFIG_ERROR",
                    f"{step} route config must be an object",
                )

            url = str(cfg.get("url") or "").strip()
            parsed_url = urlparse(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                raise TransportError(
                    "TRANSPORT_CONFIG_ERROR",
                    f"{step} route URL is invalid",
                )

            host = parsed_url.hostname.lower()
            if host == "facebook.com" or host.endswith(".facebook.com"):
                if host != "graph.facebook.com":
                    raise TransportError(
                        "TRANSPORT_CONFIG_ERROR",
                        f"{step} private Facebook web route is not allowed",
                    )

            headers = cfg.get("headers")
            clean_headers: dict[str, str] = {}
            if isinstance(headers, dict):
                clean_headers = {str(k): str(v) for k, v in headers.items()}

            self.routes[step] = RouteConfig(
                url=url,
                timeout=max(1, min(int(cfg.get("timeout") or 30), 120)),
                headers=clean_headers,
            )

    async def business(
        self,
        *,
        profile_id: str,
        params: dict[str, Any],
        state: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return await self._execute(
            "BUSINESS", profile_id, params, state, idempotency_key
        )

    async def ad_account(
        self,
        *,
        profile_id: str,
        params: dict[str, Any],
        state: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not str(state.get("business_id") or "").strip():
            raise TransportError(
                "BUSINESS_REQUIRED",
                "AD_ACCOUNT requires a saved business_id",
            )
        return await self._execute(
            "AD_ACCOUNT", profile_id, params, state, idempotency_key
        )

    async def funding(
        self,
        *,
        profile_id: str,
        params: dict[str, Any],
        state: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if not str(state.get("ad_account_id") or "").strip():
            raise TransportError(
                "AD_ACCOUNT_REQUIRED",
                "FUNDING requires a saved ad_account_id",
            )
        self._reject_raw_payment_data(params)
        return await self._execute(
            "FUNDING", profile_id, params, state, idempotency_key
        )

    def _reject_raw_payment_data(self, value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).lower().replace("-", "_").replace(" ", "_")
                if normalized in self.BLOCKED_PAYMENT_KEYS:
                    raise TransportError(
                        "RAW_PAYMENT_DATA_REJECTED",
                        f"raw payment field is not accepted: {key}",
                    )
                self._reject_raw_payment_data(child)
        elif isinstance(value, list):
            for child in value:
                self._reject_raw_payment_data(child)

    async def _execute(
        self,
        step: str,
        profile_id: str,
        params: dict[str, Any],
        state: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        route = self.routes.get(step)
        if route is None:
            raise TransportError(
                "TRANSPORT_NOT_CONFIGURED",
                f"no route configured for {step}",
            )
        if not isinstance(params, dict) or not isinstance(state, dict):
            raise TransportError("INVALID_INPUT", "params/state must be objects")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(route.headers or {}),
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        timeout = aiohttp.ClientTimeout(
            total=route.timeout,
            connect=route.timeout,
            sock_read=route.timeout,
        )
        body = {
            "step": step,
            "profile_id": profile_id,
            "params": params,
            "state": state,
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as client:
                async with client.post(route.url, json=body) as response:
                    raw = await response.text()
                    try:
                        data = json.loads(raw) if raw else {}
                    except json.JSONDecodeError as exc:
                        raise TransportError(
                            "INVALID_RESULT",
                            f"{step} returned invalid JSON",
                        ) from exc

                    if response.status == 429:
                        raise TransportError(
                            "RATE_LIMITED",
                            f"{step} returned HTTP 429",
                            retryable=True,
                        )
                    if response.status in {401, 403}:
                        raise TransportError(
                            "SESSION_EXPIRED",
                            f"{step} returned HTTP {response.status}",
                        )
                    if response.status >= 500:
                        raise TransportError(
                            "REMOTE_HTTP_ERROR",
                            f"{step} returned HTTP {response.status}",
                            retryable=True,
                        )
                    if response.status >= 400:
                        raise TransportError(
                            "REMOTE_HTTP_ERROR",
                            f"{step} returned HTTP {response.status}: {raw[:500]}",
                        )
                    if not isinstance(data, dict):
                        raise TransportError(
                            "INVALID_RESULT",
                            f"{step} response must be a JSON object",
                        )

                    required_key = self.REQUIRED_RESULT_KEYS[step]
                    if not str(data.get(required_key) or "").strip():
                        raise TransportError(
                            "INVALID_RESULT",
                            f"{step} response is missing {required_key}",
                        )
                    return data
        except asyncio.TimeoutError as exc:
            raise TransportError(
                "REMOTE_TIMEOUT",
                f"{step} request timed out",
                retryable=True,
            ) from exc
        except aiohttp.ClientError as exc:
            raise TransportError(
                "REMOTE_NETWORK_ERROR",
                f"{step} network error: {exc.__class__.__name__}",
                retryable=True,
            ) from exc
