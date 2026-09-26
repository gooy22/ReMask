from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fb_worker import AuthenticationError, ProxyError, RemoteRequestError

from ..facebook_ad_account_create import (
    AdAccountMutationError,
    _normalize_ad_account_id,
    create_ad_account_with_docids,
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


def _known_final_click_unmatched_empty_inventory(result: Any) -> bool:
    """Recognize a false-uncertain final click with strong no-create evidence.

    Recovery still requires a *fresh* inventory reconciliation in the new Job.
    This helper only validates the stored prior evidence.
    """
    if not isinstance(result, dict):
        return False

    diagnostic = result.get("browser_diagnostic")
    if not isinstance(diagnostic, dict):
        diagnostic = {}

    stage = _clean(diagnostic.get("stage")).lower()
    activity = _clean(result.get("activity")).upper()
    if not (
        stage == "ad_account_final_click_unmatched"
        or activity == "AD_ACCOUNT_FINAL_CLICK_UNMATCHED"
    ):
        return False

    state_after = diagnostic.get("state_after")
    if not isinstance(state_after, dict):
        state_after = {}

    state_text = " ".join(
        _clean(state_after.get(key))
        for key in ("signature", "body_excerpt")
    ).casefold()
    controls = state_after.get("controls")
    if isinstance(controls, list):
        state_text += " " + " ".join(
            _clean(value).casefold() for value in controls
        )

    empty_inventory_markers = (
        "aucun compte publicitaire ajouté",
        "no ad accounts added",
        "no advertising accounts added",
        "нет добавленных рекламных аккаунтов",
        "рекламних акаунтів не додано",
        "keine werbekonten hinzugefügt",
    )
    if not any(marker in state_text for marker in empty_inventory_markers):
        return False

    candidates: list[dict[str, Any]] = []
    for source in (
        result.get("graphql_candidates"),
        diagnostic.get("graphql_candidates"),
    ):
        if isinstance(source, list):
            candidates.extend(
                row for row in source if isinstance(row, dict)
            )

    for row in candidates:
        if bool(row.get("matched_create")):
            return False
        friendly = _clean(row.get("friendly_name")).casefold()
        if (
            "createadaccount" in friendly
            and "usagestep" not in friendly
            and "query" not in friendly
        ):
            return False

    return True


def _inventory_repeatedly_confirms_empty(
    diagnostics: Any,
    *,
    required_checks: int = 3,
) -> bool:
    if not isinstance(diagnostics, list) or required_checks < 1:
        return False
    empty_checks = 0
    for row in diagnostics:
        if not isinstance(row, dict):
            continue
        if (
            row.get("stage") == "inventory"
            and row.get("result") == "ok"
            and int(row.get("count") or 0) == 0
        ):
            empty_checks += 1
    return empty_checks >= required_checks


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


async def _reconcile_existing_browser_inventory(
    session: Any,
    *,
    business_id: str,
    account_name: str,
) -> tuple[str, dict[str, Any]]:
    """Read-only fallback using Meta Business Settings GraphQL inventory."""
    try:
        async with FacebookBusinessBrowser(
            session.context,
            timeout_seconds=45,
        ) as browser:
            result = await browser.find_ad_account_in_inventory(
                business_id=business_id,
                account_name=account_name,
                timeout_seconds=10.0,
            )
    except Exception as exc:
        return "", {
            "confirmed": False,
            "source": "business_settings_graphql_inventory",
            "error": f"{exc.__class__.__name__}: {_clean(exc)}"[:500],
        }

    if not isinstance(result, dict):
        return "", {
            "confirmed": False,
            "source": "business_settings_graphql_inventory",
            "reason": "invalid_result",
        }

    found_id = _normalize_ad_account_id(result.get("ad_account_id"))
    if bool(result.get("confirmed")) and found_id:
        return found_id, result
    return "", result


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

            # Do not let an old ambiguous click block this Business forever.
            # Recheck Meta inventory three times. Only when all checks are
            # conclusive and all report zero RK do we allow a fresh CREATE.
            inventory_evidence = list(diagnostics)
            for retry_index in range(2):
                if retry_index:
                    await asyncio.sleep(1.0)
                retry_id, retry_diag = await _reconcile_existing(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
                inventory_evidence.extend(retry_diag)
                if retry_id:
                    await provisioning_state.remember_entity(
                        profile_id,
                        scope_key,
                        ProvisioningStep.AD_ACCOUNT,
                        {"ad_account_id": retry_id},
                    )
                    return {
                        "ad_account_id": retry_id,
                        "business_id": business_id,
                        "name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "reused": True,
                        "cross_job_resume": True,
                        "recovered_after_uncertainty": True,
                        "transport": "graph_inventory_reconciliation",
                        "reconciliation": inventory_evidence,
                    }

            if _inventory_repeatedly_confirms_empty(
                inventory_evidence,
                required_checks=3,
            ):
                cross_job = {}
            else:
                browser_found_id, browser_inventory = (
                    await _reconcile_existing_browser_inventory(
                        session,
                        business_id=business_id,
                        account_name=rk_name,
                    )
                )
                if browser_found_id:
                    await provisioning_state.remember_entity(
                        profile_id,
                        scope_key,
                        ProvisioningStep.AD_ACCOUNT,
                        {"ad_account_id": browser_found_id},
                    )
                    return {
                        "ad_account_id": browser_found_id,
                        "business_id": business_id,
                        "name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "reused": True,
                        "cross_job_resume": True,
                        "recovered_after_uncertainty": True,
                        "transport": (
                            "business_settings_graphql_inventory"
                        ),
                        "reconciliation": inventory_evidence,
                        "browser_inventory": browser_inventory,
                    }

                # Meta's own read-only Business Settings inventory is stronger
                # than localized UI text. Two exact-Business empty inventory
                # observations prove that the old ambiguous click left no RK,
                # so the stale cross-Job duplicate guard can be cleared.
                if bool(browser_inventory.get("confirmed_empty")):
                    cross_job = {}
                else:
                    ui_inventory = {}
                    try:
                        async with FacebookBusinessBrowser(
                            session.context,
                            timeout_seconds=45,
                        ) as inventory_browser:
                            ui_inventory = (
                                await inventory_browser.verify_ad_account_inventory_empty(
                                    business_id=business_id,
                                )
                            )
                    except Exception as exc:
                        ui_inventory = {
                            "confirmed_empty": False,
                            "error": (
                                f"{exc.__class__.__name__}: {_clean(exc)}"
                            )[:500],
                        }

                    if bool(ui_inventory.get("confirmed_empty")):
                        cross_job = {}
                    else:
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

        browser_found_id, browser_inventory = (
            await _reconcile_existing_browser_inventory(
                session,
                business_id=business_id,
                account_name=rk_name,
            )
        )
        if browser_found_id:
            await provisioning_state.remember_entity(
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {"ad_account_id": browser_found_id},
            )
            return {
                "ad_account_id": browser_found_id,
                "business_id": business_id,
                "name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "recovered_after_uncertainty": True,
                "transport": "business_settings_graphql_inventory",
                "reconciliation": diagnostics,
                "browser_inventory": browser_inventory,
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

    private_submit_started = False

    async def private_before_submit() -> None:
        nonlocal private_submit_started
        private_submit_started = True
        await browser_checkpoint(
            {
                "phase": "CREATE_SUBMIT_INTENT",
                "activity": "AD_ACCOUNT_PRIVATE_GRAPHQL_SUBMIT_INTENT",
                "activity_at": int(time.time()),
                "transport": "facebook_private_graphql",
            }
        )

    try:
        await browser_checkpoint(
            {
                "phase": "CREATE_DISCOVERING",
                "activity": "AD_ACCOUNT_PRIVATE_GRAPHQL_DISCOVERY",
                "activity_at": int(time.time()),
                "transport": "facebook_private_graphql",
            }
        )
        web_session = await session.facebook_web()
        result = await asyncio.wait_for(
            create_ad_account_with_docids(
                web_session,
                business_id=business_id,
                account_name=rk_name,
                currency=currency,
                timezone_id=timezone_id,
                profile_id=profile_id,
                before_submit=private_before_submit,
            ),
            timeout=120.0,
        )

    except asyncio.TimeoutError as exc:
        code = (
            "CREATE_AD_ACCOUNT_RESULT_UNKNOWN"
            if private_submit_started
            else "CREATE_AD_ACCOUNT_MUTATION_NOT_DISCOVERED"
        )
        mutation_exc = AdAccountMutationError(
            code,
            (
                "Private CREATE_AD_ACCOUNT timed out after submit; "
                "inventory reconciliation is required before retry."
                if private_submit_started
                else (
                    "Private CREATE_AD_ACCOUNT discovery/transport timed out "
                    "before any CREATE was submitted."
                )
            ),
            retryable=True,
        )
        mutation_exc.__cause__ = exc
        exc = mutation_exc

        if exc.code == "CREATE_AD_ACCOUNT_RESULT_UNKNOWN":
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
                    "transport": "facebook_private_graphql",
                },
            )

            last_diagnostics: list[dict[str, Any]] = []
            for attempt in range(3):
                found_id, diagnostics = await _reconcile_existing(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
                last_diagnostics = diagnostics
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

            browser_found_id, browser_inventory = (
                await _reconcile_existing_browser_inventory(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
            )
            if browser_found_id:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": browser_found_id},
                )
                return {
                    "ad_account_id": browser_found_id,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "recovered_after_uncertainty": True,
                    "transport": "business_settings_graphql_inventory",
                    "reconciliation": last_diagnostics,
                    "browser_inventory": browser_inventory,
                }

            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                str(exc),
                retryable=True,
            ) from exc

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
                "transport": "facebook_private_graphql",
            },
        )
        raise ProvisioningError(
            exc.code,
            str(exc),
            retryable=True,
        ) from exc

    except AdAccountMutationError as exc:
        if exc.code == "CREATE_AD_ACCOUNT_RESULT_UNKNOWN":
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
                    "mutation_payload": (
                        exc.payload if isinstance(exc.payload, dict) else {}
                    ),
                    "transport": "facebook_private_graphql",
                },
            )

            last_diagnostics: list[dict[str, Any]] = []
            for attempt in range(3):
                found_id, diagnostics = await _reconcile_existing(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
                last_diagnostics = diagnostics
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

            browser_found_id, browser_inventory = (
                await _reconcile_existing_browser_inventory(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
            )
            if browser_found_id:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": browser_found_id},
                )
                return {
                    "ad_account_id": browser_found_id,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "recovered_after_uncertainty": True,
                    "transport": "business_settings_graphql_inventory",
                    "reconciliation": last_diagnostics,
                    "browser_inventory": browser_inventory,
                }

            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                str(exc),
                retryable=True,
            ) from exc

        safe_pre_submit_codes = {
            "CREATE_AD_ACCOUNT_MUTATION_NOT_DISCOVERED",
            "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT",
            "CREATE_AD_ACCOUNT_BROWSER_TRANSPORT_UNAVAILABLE",
            "SESSION_EXPIRED",
        }
        if exc.code in safe_pre_submit_codes:
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
                    "transport": "facebook_private_graphql",
                },
            )
            raise ProvisioningError(
                exc.code,
                str(exc),
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
                "mutation_payload": (
                    exc.payload if isinstance(exc.payload, dict) else {}
                ),
                "transport": "facebook_private_graphql",
            },
        )
        raise ProvisioningError(
            exc.code,
            str(exc),
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
            "create_response_friendly_name": result.candidate.friendly_name,
            "create_response_doc_id": result.candidate.doc_id,
            "create_response_path": result.response_path,
            "transport": "facebook_private_graphql",
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
        "transport": "facebook_private_graphql",
        "create_response_friendly_name": result.candidate.friendly_name,
        "create_response_doc_id": result.candidate.doc_id,
        "create_response_path": result.response_path,
    }
