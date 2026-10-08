from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import aiohttp

from ..session import MetaSession, ProfileContext, ProxyCheckError
from .models import ENTITY_RESULT_KEYS, ProvisioningError, ProvisioningStep
from .funding_handler import validate_funding_result
from .timeouts import browser_step_timeout
from .proxy import ProxyChecker
from .registry import get_handler
from .state import ProvisioningStateStore
from .transport import ProvisioningTransport, TransportError
from .meta_transport import MetaTransportRouter


_MUTATING_BROWSER_STEPS = {
    ProvisioningStep.FAN_PAGES,
    ProvisioningStep.BUSINESS,
    ProvisioningStep.AD_ACCOUNT,
    ProvisioningStep.PAGE_ACCESS,
}
_PROFILE_MUTATION_LAST_FINISHED: dict[str, float] = {}
_PROFILE_MUTATION_COOLDOWN_SECONDS = max(
    0.0,
    min(
        120.0,
        float(os.getenv("REMASK_PROFILE_MUTATION_COOLDOWN_SECONDS") or "8"),
    ),
)


def normalize_add_bm_payload(
    payload: dict[str, Any],
    *,
    task_idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Server-side guard for ordinary Add BM submitted by stale Workspace JS."""
    source = dict(payload or {})
    parameters = source.get("parameters")
    if not isinstance(parameters, dict):
        return source

    business = parameters.get("BUSINESS")
    if business is None:
        business = parameters.get("business")
    if not isinstance(business, dict):
        return source

    task_key = str(task_idempotency_key or "").strip()
    scope_key = str(source.get("scope_key") or "").strip()
    add_bm_intent = (
        task_key.startswith("add-bm-")
        or scope_key.startswith("add-bm-")
    )
    if not add_bm_intent or business.get("attach_page", False) is not False:
        return source

    clean_business = dict(business)
    clean_business["attach_page"] = False
    clean_business.pop("page_id", None)
    clean_business.pop("primary_page_id", None)

    clean_parameters = dict(parameters)
    clean_parameters["BUSINESS"] = clean_business
    clean_parameters.pop("business", None)
    clean_parameters.pop("PAGE_ACCESS", None)
    clean_parameters.pop("page_access", None)
    clean_parameters.pop("AD_ACCOUNT", None)
    clean_parameters.pop("ad_account", None)

    clean_scope = task_key if task_key.startswith("add-bm-") else scope_key
    if clean_scope.startswith("add-bm-page-"):
        clean_scope = task_key if task_key and not task_key.startswith("add-bm-page-") else (
            "add-bm-independent-" + clean_scope[len("add-bm-page-"):]
        )

    source["steps"] = ["PROXY_CHECK", "BUSINESS"]
    source["parameters"] = clean_parameters
    if clean_scope:
        source["scope_key"] = clean_scope
    return source


async def _await_profile_mutation_cooldown(profile_id: str) -> float:
    """Serialize bursts on one FB profile without trying to mimic human timing."""
    if _PROFILE_MUTATION_COOLDOWN_SECONDS <= 0:
        return 0.0

    key = str(profile_id or "").strip()
    if not key:
        return 0.0

    last = float(_PROFILE_MUTATION_LAST_FINISHED.get(key) or 0.0)
    if last <= 0:
        return 0.0

    elapsed = max(0.0, time.monotonic() - last)
    remaining = max(0.0, _PROFILE_MUTATION_COOLDOWN_SECONDS - elapsed)
    if remaining > 0:
        await asyncio.sleep(remaining)
    return remaining


def _mark_profile_mutation_finished(profile_id: str) -> None:
    key = str(profile_id or "").strip()
    if key:
        _PROFILE_MUTATION_LAST_FINISHED[key] = time.monotonic()


class ProvisioningService:
    def __init__(
        self,
        state: ProvisioningStateStore,
        transport: ProvisioningTransport | None = None,
        profile_resolver: Any = None,
    ) -> None:
        self.state = state
        self.transport = transport or ProvisioningTransport()
        self.profile_resolver = profile_resolver

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
        payload = normalize_add_bm_payload(
            payload,
            task_idempotency_key=task_idempotency_key,
        )
        steps = self._parse_steps(payload.get("steps"))
        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ProvisioningError("INVALID_INPUT", "parameters must be an object")
        rk_params=parameters.get('AD_ACCOUNT',parameters.get('ad_account',{}))
        if ProvisioningStep.AD_ACCOUNT in steps and isinstance(rk_params,dict) and rk_params.get('use_common_page') is True:
            # Persisted jobs can contain PAGE_ACCESS before AD_ACCOUNT. Always
            # enforce the dependency, including when resuming that old payload.
            if ProvisioningStep.PAGE_ACCESS in steps:
                steps.remove(ProvisioningStep.PAGE_ACCESS)
            steps.insert(steps.index(ProvisioningStep.AD_ACCOUNT)+1,ProvisioningStep.PAGE_ACCESS)
            if rk_params.get('page_policies_accepted') is True:
                parameters.setdefault('PAGE_ACCESS',{})['policies_accepted']=True

        scope_key = str(
            payload.get("scope_key")
            or task_idempotency_key
            or "default"
        ).strip() or "default"

        completed: list[dict[str, Any]] = []
        meta_transport = MetaTransportRouter(session, private_only=(
            payload.get("transport_mode") == "private_http_only" or scope_key.startswith("prepare:")
            or any(step in {ProvisioningStep.BUSINESS, ProvisioningStep.AD_ACCOUNT} for step in steps)
        ))

        for step in steps:
            prior = await self.state.step(item_id, step)
            if prior and prior.get("status") == "SUCCESS":
                if step is ProvisioningStep.FUNDING:
                    snapshot = await self.state.snapshot(profile_id, scope_key)
                    funding_params = parameters.get("FUNDING", parameters.get("funding", {}))
                    source = str(funding_params.get("funding_source_id") or "").strip() if isinstance(funding_params, dict) else ""
                    try:
                        validate_funding_result(prior.get("result") or {}, str(snapshot.ad_account_id or ""), source)
                    except ProvisioningError as error:
                        await self.state.fail(item_id, profile_id, scope_key, step, error.code, str(error))
                        raise
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

            if (
                existing_id
                and step
                not in {
                    ProvisioningStep.BUSINESS,
                    ProvisioningStep.AD_ACCOUNT,
                    ProvisioningStep.FUNDING,
                }
            ):
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
            await self.state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                step,
                {
                    "action_phase": "PRECHECK",
                    "action_contract_version": 1,
                },
            )

            try:
                if step is ProvisioningStep.PROXY_CHECK:
                    await self.state.checkpoint(
                        item_id,
                        profile_id,
                        scope_key,
                        step,
                        {"action_phase": "EXECUTE"},
                    )
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

                    if step is ProvisioningStep.BUSINESS and step_params.get("use_created_page") is True:
                        page_step = await self.state.step(item_id, ProvisioningStep.FAN_PAGES)
                        page_ids = (page_step or {}).get("result", {}).get("page_ids") or []
                        if (page_step or {}).get("status") != "SUCCESS" or len(page_ids) != 1 or not str(page_ids[0]).isdigit():
                            raise ProvisioningError("CREATED_PAGE_REQUIRED", "Automatic BM requires exactly one confirmed Page from this work item")
                        step_params = {**step_params, "page_id": str(page_ids[0])}

                    state = snapshot.as_dict()
                    step_key = (
                        f"{task_idempotency_key}:{step.value}"
                        if task_idempotency_key
                        else f"{profile_id}:{scope_key}:{step.value}"
                    )

                    handler = get_handler(step.value)

                    policy = meta_transport.policy(step.value)
                    await self.state.checkpoint(
                        item_id,
                        profile_id,
                        scope_key,
                        step,
                        {
                            "action_phase": "EXECUTE",
                            "action_idempotency_key": step_key,
                            "transport_primary": policy.primary,
                            "transport_fallback": policy.fallback,
                        },
                    )

                    if step in _MUTATING_BROWSER_STEPS:
                        await _await_profile_mutation_cooldown(profile_id)
                        step_timeout = browser_step_timeout(step)
                        timeout_code = (
                            "PAGE_ACCESS_TIMEOUT"
                            if step is ProvisioningStep.PAGE_ACCESS else
                            "FAN_PAGES_TIMEOUT"
                            if step is ProvisioningStep.FAN_PAGES
                            else "BUSINESS_TIMEOUT"
                            if step is ProvisioningStep.BUSINESS
                            else "AD_ACCOUNT_TIMEOUT"
                        )
                        timeout_label = (
                            "Facebook advertising Page access watchdog"
                            if step is ProvisioningStep.PAGE_ACCESS else
                            "Facebook Fan Page total queue/runtime watchdog"
                            if step is ProvisioningStep.FAN_PAGES
                            else "Meta Business total queue/runtime watchdog"
                            if step is ProvisioningStep.BUSINESS
                            else "Meta Ad Account total queue/runtime watchdog"
                        )

                        try:
                            try:
                                result = await asyncio.wait_for(
                                    self._run_handler(handler, step,
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
                                        profile_resolver=self.profile_resolver,
                                        meta_transport=meta_transport,
                                    ),
                                    timeout=step_timeout,
                                )
                            finally:
                                # Count every mutation attempt, including a safe
                                # pre-submit failure, so an immediate manual Retry
                                # cannot hammer the same FB profile in a burst.
                                _mark_profile_mutation_finished(profile_id)
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

                await self.state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    step,
                    {"action_phase": "VERIFY"},
                )
                self._verify_result_contract(
                    step,
                    result,
                    step_params if step is not ProvisioningStep.PROXY_CHECK else {},
                    snapshot.as_dict(),
                )
                result = {
                    **result,
                    "action_phase": "COMMIT",
                    "action_contract_version": 1,
                }

                await self.state.complete(
                    item_id, profile_id, scope_key, step, result
                )
                if step in _MUTATING_BROWSER_STEPS:
                    release_browser = getattr(session, "close_business_browser", None)
                    if callable(release_browser):
                        await release_browser()
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
    def _verify_result_contract(
        step: ProvisioningStep,
        result: dict[str, Any],
        params: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> None:
        """Common VERIFY gate before durable COMMIT.

        Handlers remain responsible for Meta-specific reconciliation. This gate
        enforces the shared action contract so an arbitrary/partial dict cannot
        be committed as a successful provisioning step.
        """

        def numeric(value: Any) -> str:
            clean = str(value or "").strip()
            if clean.startswith("act_"):
                clean = clean[4:]
            return clean if clean.isdigit() else ""

        if step is ProvisioningStep.BUSINESS:
            if not numeric(result.get("business_id")):
                raise ProvisioningError(
                    "VERIFY_BUSINESS_ID_INVALID",
                    "BUSINESS VERIFY requires a numeric business_id",
                    retryable=True,
                )
            return

        if step is ProvisioningStep.AD_ACCOUNT:
            account_id = numeric(
                result.get("ad_account_id")
                or result.get("account_id")
            )
            if not account_id:
                raise ProvisioningError(
                    "VERIFY_AD_ACCOUNT_ID_INVALID",
                    "AD_ACCOUNT VERIFY requires a numeric ad_account_id",
                    retryable=True,
                )
            target_business = numeric(
                params.get("business_id")
                or snapshot.get("business_id")
            )
            result_business = numeric(result.get("business_id"))
            if (
                target_business
                and result_business
                and result_business != target_business
            ):
                raise ProvisioningError(
                    "VERIFY_AD_ACCOUNT_BUSINESS_MISMATCH",
                    (
                        "AD_ACCOUNT VERIFY returned an RK for a different "
                        "Business Portfolio"
                    ),
                    retryable=True,
                )
            return

        if step is ProvisioningStep.FAN_PAGES:
            page_ids = result.get("page_ids")
            if not isinstance(page_ids, list) or not page_ids:
                raise ProvisioningError(
                    "VERIFY_FAN_PAGE_MISSING",
                    "FAN_PAGES VERIFY requires at least one confirmed page_id",
                    retryable=True,
                )
            if any(not numeric(value) for value in page_ids):
                raise ProvisioningError(
                    "VERIFY_FAN_PAGE_ID_INVALID",
                    "FAN_PAGES VERIFY returned a non-numeric page_id",
                    retryable=True,
                )
            return

        if step is ProvisioningStep.PAGE_ACCESS:
            for key in (
                "page_id",
                "business_id",
                "ad_account_id",
            ):
                if not numeric(result.get(key)):
                    raise ProvisioningError(
                        "VERIFY_PAGE_ACCESS_ID_INVALID",
                        f"PAGE_ACCESS VERIFY requires numeric {key}",
                        retryable=True,
                    )
            # Current full-control flow proves both relation stages.
            # Older verified PAGE_ACCESS results may instead expose the
            # stronger aggregate ad_account_page_access_verified flag.
            if result.get("ad_account_page_access_verified") is True:
                return
            if result.get("page_shared_to_business") is not True:
                raise ProvisioningError(
                    "VERIFY_PAGE_SHARE_UNCONFIRMED",
                    "PAGE_ACCESS VERIFY did not confirm Page sharing to the Business",
                    retryable=True,
                )
            if result.get("operator_ads_access_assigned") is not True:
                raise ProvisioningError(
                    "VERIFY_RK_PAGE_ACCESS_UNCONFIRMED",
                    "PAGE_ACCESS VERIFY did not confirm advertising access",
                    retryable=True,
                )
            return

        # FUNDING performs its stronger RK/source verification inside the
        # funding handler. PROXY_CHECK has no remote entity identity to verify.


    async def _run_handler(self, handler, step, session, params, snapshot, **kwargs):
        if step is ProvisioningStep.FAN_PAGES and params.get('common_page') is True:
            from .advertising_page import ensure_common_page,AdvertisingPageStore
            try:
                return await ensure_common_page(kwargs.get('meta_transport') or session,params,self.state,self.profile_resolver)
            except ProvisioningError:
                config=await AdvertisingPageStore.for_context(self.state,session.context).get()
                creation_item=str(config.get('creation_item_id') or '')
                legacy_item='workspace-common-page-'+str(config['owner_profile_id'])
                if not creation_item:
                    creation_item=('workspace-common-page-facebook-'+str(config.get('owner_facebook_uid'))
                        if config.get('owner_facebook_uid') else legacy_item)
                saved=await self.state.step(creation_item,step)
                if saved is None and creation_item != legacy_item:
                    saved=await self.state.step(legacy_item,step)
                await self.state.checkpoint(kwargs['item_id'],kwargs['profile_id'],kwargs['scope_key'],step,
                    {**((saved or {}).get('result') or {}),'common_page_creation_item_id':creation_item})
                raise
        return await handler(session,params,snapshot,**kwargs)

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
        if any(marker in str(exc).casefold() for marker in ("page crashed", "target crashed")):
            # A renderer failure is resumable. Mutation checkpoints decide
            # whether retry may CREATE or must only reconcile a prior submit.
            return ProvisioningError("BROWSER_PAGE_CRASHED", str(exc), retryable=True)
        if "writeunixtransport" in str(exc).casefold() and "handler is closed" in str(exc).casefold():
            return ProvisioningError("BROWSER_CONNECTION_CLOSED", str(exc), retryable=True)
        return ProvisioningError("TASK_FAILED", str(exc))
