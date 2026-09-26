from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp

from ..session import MetaSession, ProfileContext, ProxyCheckError
from .models import ENTITY_RESULT_KEYS, ProvisioningError, ProvisioningStep
from .timeouts import browser_step_timeout
from .proxy import ProxyChecker
from .registry import get_handler
from .state import ProvisioningStateStore
from .transport import ProvisioningTransport, TransportError


class ProvisioningService:
    def __init__(
        self,
        state: ProvisioningStateStore,
        transport: ProvisioningTransport | None = None,
    ) -> None:
        self.state = state
        self.transport = transport or ProvisioningTransport()

    async def run(
        self,
        *,
        item_id: str,
        profile_id: str,
        context: ProfileContext,
        session: MetaSession,
        payload: dict[str, Any],
        task_idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        steps = self._parse_steps(payload.get("steps"))
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ProvisioningError("INVALID_INPUT", "parameters must be an object")

        scope_key = str(
            payload.get("scope_key")
            or task_idempotency_key
            or "default"
        ).strip() or "default"

        completed: list[dict[str, Any]] = []

        for step in steps:
            prior = await self.state.step(item_id, step)
            if prior and prior.get("status") == "SUCCESS":
                completed.append(
                    {
                        "step": step.value,
                        "status": "SUCCESS",
                        "skipped": True,
                        "result": prior.get("result") or {},
                    }
                )
                continue

            snapshot = await self.state.snapshot(profile_id, scope_key)
            entity_key = ENTITY_RESULT_KEYS.get(step)
            existing_id = getattr(snapshot, entity_key, None) if entity_key else None

            if existing_id and step is not ProvisioningStep.BUSINESS:
                result = {entity_key: existing_id, "reused": True}
                await self.state.complete(
                    item_id, profile_id, scope_key, step, result
                )
                completed.append(
                    {
                        "step": step.value,
                        "status": "SUCCESS",
                        "skipped": True,
                        "result": result,
                    }
                )
                continue

            await self.state.set_running(item_id, profile_id, scope_key, step)

            try:
                if step is ProvisioningStep.PROXY_CHECK:
                    result = await ProxyChecker(
                        context.proxy,
                        context.user_agent,
                    ).check()
                else:
                    step_params = parameters.get(step.value)
                    if step_params is None:
                        step_params = parameters.get(step.value.lower(), {})
                    if not isinstance(step_params, dict):
                        raise ProvisioningError(
                            "INVALID_INPUT",
                            f"parameters.{step.value} must be an object",
                        )

                    state = snapshot.as_dict()
                    step_key = (
                        f"{task_idempotency_key}:{step.value}"
                        if task_idempotency_key
                        else f"{profile_id}:{scope_key}:{step.value}"
                    )

                    handler = get_handler(step.value)

                    if step in {
                        ProvisioningStep.FAN_PAGES,
                        ProvisioningStep.BUSINESS,
                        ProvisioningStep.AD_ACCOUNT,
                    }:
                        step_timeout = browser_step_timeout(step)
                        timeout_code = (
                            "FAN_PAGES_TIMEOUT"
                            if step is ProvisioningStep.FAN_PAGES
                            else "BUSINESS_TIMEOUT"
                            if step is ProvisioningStep.BUSINESS
                            else "AD_ACCOUNT_TIMEOUT"
                        )
                        timeout_label = (
                            "Facebook Fan Page total queue/runtime watchdog"
                            if step is ProvisioningStep.FAN_PAGES
                            else "Meta Business total queue/runtime watchdog"
                            if step is ProvisioningStep.BUSINESS
                            else "Meta Ad Account total queue/runtime watchdog"
                        )

                        try:
                            result = await asyncio.wait_for(
                                handler(
                                    session,
                                    step_params,
                                    state,
                                    transport=self.transport,
                                    idempotency_key=step_key,
                                    provisioning_state=self.state,
                                    item_id=item_id,
                                    profile_id=profile_id,
                                    scope_key=scope_key,
                                    step_state=prior,
                                ),
                                timeout=step_timeout,
                            )
                        except asyncio.TimeoutError as exc:
                            raise ProvisioningError(
                                timeout_code,
                                (
                                    f"{timeout_label} exceeded "
                                    f"{int(step_timeout)}s"
                                ),
                                retryable=True,
                            ) from exc
                    else:
                        result = await handler(
                            session,
                            step_params,
                            state,
                            transport=self.transport,
                            idempotency_key=step_key,
                        )

                if not isinstance(result, dict):
                    raise ProvisioningError(
                        "INVALID_RESULT",
                        f"{step.value} returned a non-object result",
                    )
                if entity_key and not str(result.get(entity_key) or "").strip():
                    raise ProvisioningError(
                        "INVALID_RESULT",
                        f"{step.value} result is missing {entity_key}",
                    )

                await self.state.complete(
                    item_id, profile_id, scope_key, step, result
                )
                completed.append(
                    {
                        "step": step.value,
                        "status": "SUCCESS",
                        "skipped": False,
                        "result": result,
                    }
                )
            except Exception as exc:
                error = self._classify(exc)
                await self.state.fail(
                    item_id,
                    profile_id,
                    scope_key,
                    step,
                    error.code,
                    str(error),
                )
                raise error from exc

        final_state = await self.state.snapshot(profile_id, scope_key)
        return {
            "profile_id": profile_id,
            "scope_key": scope_key,
            "steps": completed,
            "state": final_state.as_dict(),
        }

    @staticmethod
    def _parse_steps(raw: Any) -> list[ProvisioningStep]:
        if raw is None:
            return [
                ProvisioningStep.PROXY_CHECK,
                ProvisioningStep.BUSINESS,
                ProvisioningStep.AD_ACCOUNT,
                ProvisioningStep.FUNDING,
            ]
        if not isinstance(raw, list) or not raw:
            raise ProvisioningError("INVALID_INPUT", "steps must be a non-empty array")

        output: list[ProvisioningStep] = []
        seen: set[ProvisioningStep] = set()
        for value in raw:
            try:
                step = ProvisioningStep(str(value).strip().upper())
            except ValueError as exc:
                raise ProvisioningError(
                    "INVALID_INPUT",
                    f"unsupported provisioning step: {value}",
                ) from exc
            if step not in seen:
                output.append(step)
                seen.add(step)
        return output

    @staticmethod
    def _classify(exc: Exception) -> ProvisioningError:
        if isinstance(exc, ProvisioningError):
            return exc
        if isinstance(exc, TransportError):
            return ProvisioningError(exc.code, str(exc), retryable=exc.retryable)
        if isinstance(exc, ProxyCheckError):
            return ProvisioningError("PROXY_DEAD", str(exc), retryable=True)
        if isinstance(exc, asyncio.TimeoutError):
            return ProvisioningError(
                "REMOTE_TIMEOUT",
                "remote request timeout",
                retryable=True,
            )
        if isinstance(exc, aiohttp.ClientResponseError):
            if exc.status == 429:
                return ProvisioningError("RATE_LIMITED", str(exc), retryable=True)
            if exc.status in {401, 403}:
                return ProvisioningError("SESSION_EXPIRED", str(exc))
            return ProvisioningError(
                "REMOTE_HTTP_ERROR",
                str(exc),
                retryable=exc.status >= 500,
            )
        if isinstance(exc, aiohttp.ClientError):
            return ProvisioningError(
                "REMOTE_NETWORK_ERROR",
                f"network error: {exc.__class__.__name__}",
                retryable=True,
            )
        return ProvisioningError("TASK_FAILED", str(exc))
