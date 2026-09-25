from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fb_worker import AuthenticationError, ProxyError, RemoteRequestError

from ..facebook_ad_account_create import (
    AdAccountMutationError,
    _normalize_ad_account_id,
)
from ..facebook_business_browser import (
    BrowserBusinessError,
    FacebookBusinessBrowser,
)
from .meta_errors import classify_meta_request_error
from .models import ProvisioningError, ProvisioningStep


log = logging.getLogger("remask_worker")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _checkpoint_result(step_state: Any) -> dict[str, Any]:
    if not isinstance(step_state, dict):
        return {}
    result = step_state.get("result")
    return dict(result) if isinstance(result, dict) else {}


def _known_pre_submit_navigation_failure(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    message = _clean(
        result.get("last_error")
        or result.get("error")
        or result.get("message")
    )
    lowered = message.lower()
    return (
        "page.goto" in lowered
        and "timeout" in lowered
        and "navigating to" in lowered
        and "business.facebook.com/latest/home" in lowered
    )


def _known_pre_submit_usage_step_failure(result: Any) -> bool:
    """Recognize the old false-uncertain state caused by Meta's usage-step Query.

    Before the usage-step fix, ReMask treated the intermediate
    BizKitSettingsCreateAdAccountUsageStepQuery as if the final CREATE might
    have been submitted. That Query only advances the wizard; it is not the
    ad-account CREATE mutation.
    """
    if not isinstance(result, dict):
        return False

    activity = _clean(result.get("activity")).upper()
    diagnostic = result.get("browser_diagnostic")
    if not isinstance(diagnostic, dict):
        diagnostic = {}

    stage = _clean(diagnostic.get("stage")).lower()
    candidates = []
    for source in (
        result.get("graphql_candidates"),
        diagnostic.get("graphql_candidates"),
    ):
        if isinstance(source, list):
            candidates.extend(source)

    saw_usage_query = False
    saw_create_like = False
    for row in candidates:
        if not isinstance(row, dict):
            continue
        friendly = _clean(row.get("friendly_name")).casefold()
        matched = bool(row.get("matched_create"))
        if (
            "createadaccountusagestep" in friendly
            or "adaccountusagestep" in friendly
        ) and "query" in friendly:
            saw_usage_query = True
        if matched or (
            "createadaccount" in friendly
            and "usagestep" not in friendly
            and "query" not in friendly
        ):
            saw_create_like = True

    return (
        saw_usage_query
        and not saw_create_like
        and (
            activity in {
                "AD_ACCOUNT_FINAL_CLICK_UNMATCHED",
                "AD_ACCOUNT_USAGE_STEP_OPENED",
            }
            or stage == "ad_account_final_click_unmatched"
        )
    )


async def _reconcile_existing(
    session: Any,
    *,
    business_id: str,
    account_name: str,
) -> tuple[str, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    try:
        graph = await session.graph_api()
        rows = await graph.list_ad_accounts_for_business(business_id)
    except Exception as exc:
        diagnostics.append(
            {
                "stage": "inventory",
                "result": "unavailable",
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
        return "", diagnostics

    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        account_id = _normalize_ad_account_id(
            row.get("id") or row.get("account_id")
        )
        if not account_id:
            continue
        normalized.append(
            {
                "id": account_id,
                "name": _clean(row.get("name")),
            }
        )

    diagnostics.append(
        {
            "stage": "inventory",
            "result": "ok",
            "business_id": business_id,
            "count": len(normalized),
            "ids": [row["id"] for row in normalized[:20]],
        }
    )

    if not normalized:
        return "", diagnostics

    # User's invariant: one BM must have only one RK. If there is exactly one,
    # reuse it regardless of name. A unique exact-name match is also safe.
    if len(normalized) == 1:
        return str(normalized[0]["id"]), diagnostics

    name_matches = [
        row
        for row in normalized
        if _clean(row.get("name")).casefold() == _clean(account_name).casefold()
    ]
    if len(name_matches) == 1:
        return str(name_matches[0]["id"]), diagnostics

    raise ProvisioningError(
        "AD_ACCOUNT_INVENTORY_AMBIGUOUS",
        (
            f"Business {business_id} already exposes {len(normalized)} ad "
            "accounts. ReMask will not create another RK because the configured "
            "model is 1 BM = 1 RK."
        ),
        retryable=False,
    )


async def ad_account_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    del args

    context = session.context
    profile_id = _clean(
        kwargs.get("profile_id")
        or state.get("profile_id")
        or getattr(context, "profile_id", "")
    )
    scope_key = _clean(
        kwargs.get("scope_key")
        or state.get("scope_key")
        or params.get("scope_key")
        or "default"
    ) or "default"
    item_id = _clean(kwargs.get("item_id"))
    provisioning_state = kwargs.get("provisioning_state")

    if not profile_id or profile_id.lower() == "none":
        raise ProvisioningError(
            "INVALID_INPUT",
            "profile_id is missing or invalid",
            retryable=False,
        )
    if provisioning_state is None or not item_id:
        raise ProvisioningError(
            "INTERNAL_STATE_ERROR",
            "AD_ACCOUNT resumable state context is missing",
            retryable=False,
        )

    business_id = _clean(
        params.get("business_id")
        or params.get("bm_id")
        or state.get("business_id")
    )
    if not business_id.isdigit():
        raise ProvisioningError(
            "INVALID_RESULT",
            "Missing or invalid business_id for RK creation",
            retryable=False,
        )

    rk_name = _clean(params.get("name") or params.get("rk_name"))
    if not rk_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "AD_ACCOUNT.name is required",
            retryable=False,
        )

    currency = _clean(params.get("currency")).upper()
    if not currency:
        raise ProvisioningError(
            "INVALID_INPUT",
            "AD_ACCOUNT.currency is required",
            retryable=False,
        )

    raw_timezone = params.get("timezone_id")
    if raw_timezone in (None, "", "None"):
        raise ProvisioningError(
            "INVALID_INPUT",
            "AD_ACCOUNT.timezone_id is required",
            retryable=False,
        )
    try:
        timezone_id = int(raw_timezone)
    except (TypeError, ValueError) as exc:
        raise ProvisioningError(
            "INVALID_INPUT",
            f"AD_ACCOUNT.timezone_id must be an integer, got: {raw_timezone}",
            retryable=False,
        ) from exc

    idempotency_key = kwargs.get("idempotency_key") or f"{profile_id}:RK:{business_id}"

    # The Add-RK scope is distinct from Add-BM. Seed the confirmed Business
    # into this scope so the state becomes profile -> BM -> exactly one RK.
    await provisioning_state.remember_entity(
        profile_id,
        scope_key,
        ProvisioningStep.BUSINESS,
        {"business_id": business_id},
    )

    existing_state_id = _normalize_ad_account_id(state.get("ad_account_id"))
    if existing_state_id:
        return {
            "ad_account_id": existing_state_id,
            "business_id": business_id,
            "name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "reused": True,
            "transport": "provisioning_state",
        }

    step_state = kwargs.get("step_state")
    if not isinstance(step_state, dict):
        step_state = await provisioning_state.step(
            item_id,
            ProvisioningStep.AD_ACCOUNT,
        )
    checkpoint = _checkpoint_result(step_state)

    checkpoint_business = _clean(checkpoint.get("business_id"))
    if checkpoint_business and checkpoint_business != business_id:
        raise ProvisioningError(
            "AD_ACCOUNT_CHECKPOINT_MISMATCH",
            (
                f"Saved AD_ACCOUNT checkpoint belongs to Business "
                f"{checkpoint_business}, requested {business_id}. "
                "No CREATE will be sent."
            ),
            retryable=False,
        )

    checkpoint_id = _normalize_ad_account_id(
        checkpoint.get("ad_account_id")
        or checkpoint.get("create_response_ad_account_id")
    )
    if checkpoint_id:
        await provisioning_state.remember_entity(
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {"ad_account_id": checkpoint_id},
        )
        return {
            "ad_account_id": checkpoint_id,
            "business_id": business_id,
            "name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "reused": True,
            "transport": _clean(checkpoint.get("transport")) or "checkpoint",
        }

    # Protect against a new Job being launched after an ambiguous previous
    # CREATE. Cross-job state is keyed by profile + Business ID.
    cross_job = await provisioning_state.latest_ad_account_resume_for_business(
        profile_id,
        business_id,
        exclude_item_id=item_id,
    )
    if cross_job:
        prior = (
            cross_job.get("result")
            if isinstance(cross_job.get("result"), dict)
            else {}
        )
        prior_id = _normalize_ad_account_id(
            prior.get("ad_account_id")
            or prior.get("create_response_ad_account_id")
        )
        if prior_id:
            await provisioning_state.remember_entity(
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {"ad_account_id": prior_id},
            )
            return {
                "ad_account_id": prior_id,
                "business_id": business_id,
                "name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "reused": True,
                "cross_job_resume": True,
                "transport": _clean(prior.get("transport")) or "checkpoint",
            }

        prior_phase = _clean(
            prior.get("phase") or prior.get("resume_from")
        ).upper()
        if (
            prior_phase in {
                "CREATE_CLICK_INTENT",
                "CREATE_SUBMIT_INTENT",
                "CREATE_SUBMITTED",
                "CREATE_RESULT_UNKNOWN",
                "RECONCILE_CREATE",
            }
            and not _known_pre_submit_navigation_failure(prior)
            and not _known_pre_submit_usage_step_failure(prior)
        ):
            found_id, diagnostics = await _reconcile_existing(
                session,
                business_id=business_id,
                account_name=rk_name,
            )
            if found_id:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": found_id},
                )
                return {
                    "ad_account_id": found_id,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "reused": True,
                    "cross_job_resume": True,
                    "recovered_after_uncertainty": True,
                    "transport": "graph_inventory_reconciliation",
                    "reconciliation": diagnostics,
                }
            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                (
                    f"A previous Job may already have submitted CREATE for "
                    f"Business {business_id}. Inventory does not prove the RK "
                    "yet, so ReMask will not submit a duplicate CREATE."
                ),
                retryable=True,
            )

    phase = _clean(
        checkpoint.get("phase") or checkpoint.get("resume_from")
    ).upper()

    if (
        phase in {
            "CREATE_CLICK_INTENT",
            "CREATE_SUBMIT_INTENT",
            "CREATE_SUBMITTED",
            "CREATE_RESULT_UNKNOWN",
            "RECONCILE_CREATE",
        }
        and (
            _known_pre_submit_navigation_failure(checkpoint)
            or _known_pre_submit_usage_step_failure(checkpoint)
        )
    ):
        checkpoint = await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {
                "phase": "CREATE_NOT_SUBMITTED",
                "resume_from": "CREATE",
                "business_id": business_id,
                "last_error_code": (
                    "PRE_SUBMIT_USAGE_STEP_FALSE_UNCERTAIN_RECOVERED"
                    if _known_pre_submit_usage_step_failure(checkpoint)
                    else "PRE_SUBMIT_NAVIGATION_TIMEOUT_RECOVERED"
                ),
                "last_error": "",
            },
        )
        phase = "CREATE_NOT_SUBMITTED"

    if phase in {
        "CREATE_CLICK_INTENT",
        "CREATE_SUBMIT_INTENT",
        "CREATE_SUBMITTED",
        "CREATE_RESULT_UNKNOWN",
        "RECONCILE_CREATE",
    }:
        found_id, diagnostics = await _reconcile_existing(
            session,
            business_id=business_id,
            account_name=rk_name,
        )
        if found_id:
            await provisioning_state.remember_entity(
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {"ad_account_id": found_id},
            )
            return {
                "ad_account_id": found_id,
                "business_id": business_id,
                "name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "recovered_after_uncertainty": True,
                "transport": "graph_inventory_reconciliation",
                "reconciliation": diagnostics,
            }

        await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {
                "phase": "CREATE_RESULT_UNKNOWN",
                "resume_from": "RECONCILE_CREATE",
                "business_id": business_id,
                "account_name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "reconciliation": diagnostics,
            },
        )
        raise ProvisioningError(
            "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
            (
                f"CREATE for Business {business_id} may already have reached "
                "Meta. Inventory is not conclusive; duplicate CREATE blocked."
            ),
            retryable=True,
        )

    # Read-only preflight enforces the 1 BM = 1 RK invariant. If Graph inventory
    # is unavailable, diagnostics are kept but an initial CREATE is still
    # allowed; only an uncertain previous submit blocks future CREATEs.
    found_id, inventory_before = await _reconcile_existing(
        session,
        business_id=business_id,
        account_name=rk_name,
    )
    if found_id:
        await provisioning_state.remember_entity(
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {"ad_account_id": found_id},
        )
        return {
            "ad_account_id": found_id,
            "business_id": business_id,
            "name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "reused": True,
            "transport": "graph_inventory_preflight",
            "reconciliation": inventory_before,
        }

    await provisioning_state.checkpoint(
        item_id,
        profile_id,
        scope_key,
        ProvisioningStep.AD_ACCOUNT,
        {
            "phase": "CREATE_PREPARING",
            "resume_from": "CREATE",
            "business_id": business_id,
            "account_name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "inventory_before": inventory_before,
            "activity": "BUSINESS_SETTINGS_CREATE_OPENING",
        },
    )

    async def browser_checkpoint(patch: dict[str, Any]) -> None:
        phase_value = _clean(patch.get("phase")).upper()
        if phase_value in {
            "CREATE_CLICK_INTENT",
            "CREATE_SUBMIT_INTENT",
            "CREATE_SUBMITTED",
            "CREATE_RESULT_UNKNOWN",
        }:
            resume_from = "RECONCILE_CREATE"
        elif phase_value == "CREATE_CONFIRMED":
            resume_from = "DONE"
        elif phase_value == "CREATE_REJECTED":
            resume_from = "STOP"
        else:
            resume_from = "CREATE"

        await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {
                "business_id": business_id,
                "account_name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "resume_from": resume_from,
                **patch,
            },
        )

    log.info(
        "[%s] AD_ACCOUNT Business Settings create business=%s "
        "currency=%s timezone=%s key=%s",
        profile_id,
        business_id,
        currency,
        timezone_id,
        idempotency_key,
    )

    try:
        await browser_checkpoint(
            {
                "phase": "WAITING_BROWSER_SLOT",
                "activity": "AD_ACCOUNT_WAITING_BROWSER_SLOT",
                "activity_at": int(time.time()),
            }
        )
        async with FacebookBusinessBrowser(
            context,
            timeout_seconds=60,
        ) as browser:
            await browser_checkpoint(
                {
                    "phase": "BROWSER_SLOT_ACQUIRED",
                    "activity": "AD_ACCOUNT_BROWSER_SLOT_ACQUIRED",
                    "activity_at": int(time.time()),
                }
            )
            try:
                result = await asyncio.wait_for(
                    browser.create_ad_account(
                        business_id=business_id,
                        account_name=rk_name,
                        currency=currency,
                        timezone_id=timezone_id,
                        before_submit=browser_checkpoint,
                    ),
                    timeout=115.0,
                )
            except asyncio.TimeoutError as exc:
                diagnostic = await browser.ad_account_runtime_timeout_diagnostic()
                if browser.ad_account_create_may_have_been_sent:
                    raise BrowserBusinessError(
                        "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                        (
                            "Meta Add-RK flow exceeded the internal 115s budget "
                            "after CREATE may have been sent. Reconcile inventory "
                            "before retry."
                        ),
                        retryable=True,
                        diagnostic=diagnostic,
                    ) from exc
                raise BrowserBusinessError(
                    "AD_ACCOUNT_CREATE_UI_TIMEOUT",
                    (
                        "Meta Add-RK UI exceeded the internal 115s budget "
                        "before any CREATE request was sent."
                    ),
                    retryable=True,
                    diagnostic=diagnostic,
                ) from exc

    except BrowserBusinessError as exc:
        diagnostic = (
            exc.diagnostic
            if isinstance(exc.diagnostic, dict)
            else {}
        )

        if exc.code == "AD_ACCOUNT_CREATE_RESULT_UNKNOWN":
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "CREATE_RESULT_UNKNOWN",
                    "resume_from": "RECONCILE_CREATE",
                    "business_id": business_id,
                    "account_name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "last_error_code": exc.code,
                    "last_error": str(exc)[:4000],
                    "browser_diagnostic": diagnostic,
                },
            )

            for attempt in range(3):
                found_id, diagnostics = await _reconcile_existing(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
                if found_id:
                    await provisioning_state.remember_entity(
                        profile_id,
                        scope_key,
                        ProvisioningStep.AD_ACCOUNT,
                        {"ad_account_id": found_id},
                    )
                    return {
                        "ad_account_id": found_id,
                        "business_id": business_id,
                        "name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "recovered_after_uncertainty": True,
                        "transport": "graph_inventory_reconciliation",
                        "reconciliation": diagnostics,
                    }
                if attempt < 2:
                    await asyncio.sleep(2.0)

            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                (
                    str(exc)
                    + " diagnostic="
                    + str(diagnostic)[:3000]
                ),
                retryable=True,
            ) from exc

        pre_submit_codes = {
            "AD_ACCOUNT_CREATE_UI_UNAVAILABLE",
            "AD_ACCOUNT_CREATE_UI_CHANGED",
            "AD_ACCOUNT_CREATE_UI_TIMEOUT",
            "FACEBOOK_NAVIGATION_FAILED",
            "CREATE_CHECKPOINT_FAILED_BEFORE_SEND",
            "BROWSER_UNAVAILABLE",
            "PROXY_INVALID",
            "SESSION_COOKIES_MISSING",
            "SESSION_EXPIRED",
            "CHECKPOINT_REQUIRED",
            "TWO_FACTOR_REQUIRED",
            "FACEBOOK_TEMPORARILY_BLOCKED",
        }
        if exc.code in pre_submit_codes:
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "CREATE_NOT_SUBMITTED",
                    "resume_from": "CREATE",
                    "business_id": business_id,
                    "last_error_code": exc.code,
                    "last_error": str(exc)[:4000],
                    "browser_diagnostic": diagnostic,
                },
            )
            raise ProvisioningError(
                exc.code,
                (
                    str(exc)
                    + " diagnostic="
                    + str(diagnostic)[:3000]
                ),
                retryable=exc.retryable,
            ) from exc

        await provisioning_state.checkpoint(
            item_id,
            profile_id,
            scope_key,
            ProvisioningStep.AD_ACCOUNT,
            {
                "phase": "CREATE_REJECTED",
                "resume_from": "STOP",
                "business_id": business_id,
                "last_error_code": exc.code,
                "last_error": str(exc)[:4000],
                "browser_diagnostic": diagnostic,
            },
        )
        raise ProvisioningError(
            exc.code,
            (
                str(exc)
                + " diagnostic="
                + str(diagnostic)[:3000]
            ),
            retryable=exc.retryable,
        ) from exc

    rk_id = _normalize_ad_account_id(result.ad_account_id)
    if not rk_id:
        raise ProvisioningError(
            "INVALID_RESULT",
            "Facebook returned invalid Ad Account ID",
            retryable=False,
        )

    await provisioning_state.checkpoint(
        item_id,
        profile_id,
        scope_key,
        ProvisioningStep.AD_ACCOUNT,
        {
            "phase": "CREATE_CONFIRMED",
            "resume_from": "DONE",
            "business_id": business_id,
            "ad_account_id": rk_id,
            "create_response_ad_account_id": rk_id,
            "create_response_friendly_name": result.response_friendly_name,
            "create_response_doc_id": result.response_doc_id,
            "create_response_path": result.response_path,
            "transport": "facebook_business_settings_ui",
        },
    )
    await provisioning_state.remember_entity(
        profile_id,
        scope_key,
        ProvisioningStep.AD_ACCOUNT,
        {"ad_account_id": rk_id},
    )

    return {
        "ad_account_id": rk_id,
        "business_id": business_id,
        "name": rk_name,
        "currency": currency,
        "timezone_id": timezone_id,
        "transport": "facebook_business_settings_ui",
        "create_response_friendly_name": result.response_friendly_name,
        "create_response_doc_id": result.response_doc_id,
        "create_response_path": result.response_path,
    }
