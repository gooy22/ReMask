from __future__ import annotations

import asyncio
import json
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

AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES = {
    "AD_ACCOUNT_CREATE_UI_CHANGED",
    "AD_ACCOUNT_CREATE_UI_UNAVAILABLE",
    "AD_ACCOUNT_CREATE_MUTATION_NOT_CAPTURED",
}

AD_ACCOUNT_SAFE_REPLAY_RETRY_CODES = {
    "CREATE_AD_ACCOUNT_LIVE_CAPTURE_REQUIRED",
    "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
    "CREATE_AD_ACCOUNT_LIVE_CAPTURE_STALE",
    "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT",
}


def _compact_browser_diagnostic(value: Any) -> dict[str, Any]:
    diagnostic = value if isinstance(value, dict) else {}
    return {
        key: diagnostic.get(key)
        for key in (
            "stage",
            "add_attempt_summary",
            "add_clicked",
            "action_surface_ready",
            "post_add_candidates",
            "section_clicked",
            "section_reload_attempted",
            "section_activation",
            "action_candidates",
            "ui_state",
            "right_pane_snapshot",
            "submit_attempts",
            "graphql_candidates",
        )
        if key in diagnostic
    }



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


def _known_pre_submit_capture_crash(result: Any) -> bool:
    """Return True only when saved crash telemetry proves CREATE was not armed/sent."""
    if not isinstance(result, dict):
        return False

    if not bool(result.get("page_crashed")):
        return False

    final_armed = result.get("final_capture_armed")
    create_sent = result.get("create_may_have_been_sent")
    phase = _clean(result.get("browser_phase")).upper()

    return (
        final_armed is False
        and create_sent is False
        and phase not in {
            "CAPTURE_FINAL_ARMED",
            "CREATE_CLICK_INTENT",
            "CREATE_SUBMIT_INTENT",
            "CREATE_SUBMITTED",
        }
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


async def _prove_empty_after_uncertainty(
    session: Any,
    *,
    business_id: str,
    account_name: str,
    initial_graph_diagnostics: Any = None,
    graph_required_checks: int = 3,
    browser_required_checks: int = 3,
    delay_seconds: float = 1.5,
) -> tuple[str, bool, dict[str, Any]]:
    """Resolve an ambiguous CREATE without allowing an endless duplicate lock.

    A fresh CREATE is allowed only after strong read-only evidence:
    - 3 conclusive Graph inventory empties + one exact Business Settings empty; or
    - 3 exact Business Settings GraphQL empties from fresh browser sessions; or
    - 2 exact Business Settings GraphQL empties + an explicit empty UI marker.

    Finding any RK wins immediately and returns its ID.
    """
    graph_evidence: list[dict[str, Any]] = [
        dict(row)
        for row in (
            initial_graph_diagnostics
            if isinstance(initial_graph_diagnostics, list)
            else []
        )
        if isinstance(row, dict)
    ]

    def graph_empty_count() -> int:
        return sum(
            1
            for row in graph_evidence
            if (
                row.get("stage") == "inventory"
                and row.get("result") == "ok"
                and int(row.get("count") or 0) == 0
            )
        )

    graph_attempts_needed = max(
        0,
        int(graph_required_checks) - graph_empty_count(),
    )
    for attempt in range(graph_attempts_needed):
        found_id, diagnostics = await _reconcile_existing(
            session,
            business_id=business_id,
            account_name=account_name,
        )
        graph_evidence.extend(diagnostics)
        if found_id:
            return found_id, False, {
                "strategy": "uncertain_inventory_v2",
                "found_via": "graph_inventory",
                "graph": graph_evidence[-16:],
            }
        if attempt < graph_attempts_needed - 1:
            await asyncio.sleep(max(0.25, float(delay_seconds)))

    graph_empty_confirmed = _inventory_repeatedly_confirms_empty(
        graph_evidence,
        required_checks=max(1, int(graph_required_checks)),
    )

    browser_checks: list[dict[str, Any]] = []
    browser_empty_confirmations = 0
    browser_goal = (
        1 if graph_empty_confirmed else max(2, int(browser_required_checks))
    )

    for attempt in range(max(1, int(browser_required_checks))):
        found_id, browser_inventory = (
            await _reconcile_existing_browser_inventory(
                session,
                business_id=business_id,
                account_name=account_name,
            )
        )
        browser_checks.append(
            {
                "attempt": attempt + 1,
                **(
                    browser_inventory
                    if isinstance(browser_inventory, dict)
                    else {}
                ),
            }
        )
        if found_id:
            return found_id, False, {
                "strategy": "uncertain_inventory_v2",
                "found_via": "business_settings_graphql_inventory",
                "graph": graph_evidence[-16:],
                "browser_checks": browser_checks[-6:],
            }
        if bool(
            isinstance(browser_inventory, dict)
            and browser_inventory.get("confirmed_empty")
        ):
            browser_empty_confirmations += 1

        if browser_empty_confirmations >= browser_goal:
            break
        if attempt < max(1, int(browser_required_checks)) - 1:
            await asyncio.sleep(max(0.25, float(delay_seconds)))

    ui_inventory: dict[str, Any] = {}
    ui_empty_confirmed = False
    if not (
        graph_empty_confirmed and browser_empty_confirmations >= 1
    ) and browser_empty_confirmations < max(2, int(browser_required_checks)):
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
        ui_empty_confirmed = bool(ui_inventory.get("confirmed_empty"))

    graph_plus_browser = (
        graph_empty_confirmed and browser_empty_confirmations >= 1
    )
    browser_consensus = (
        browser_empty_confirmations >= max(2, int(browser_required_checks))
    )
    browser_plus_ui = (
        browser_empty_confirmations >= 2 and ui_empty_confirmed
    )
    proven_empty = bool(
        graph_plus_browser or browser_consensus or browser_plus_ui
    )

    proof = {
        "strategy": "uncertain_inventory_v2",
        "graph_empty_confirmed": graph_empty_confirmed,
        "graph": graph_evidence[-16:],
        "browser_empty_confirmations": browser_empty_confirmations,
        "browser_required_checks": max(2, int(browser_required_checks)),
        "browser_checks": browser_checks[-6:],
        "ui_empty_confirmed": ui_empty_confirmed,
        "ui_inventory": ui_inventory,
        "proof_path": (
            "graph_plus_browser"
            if graph_plus_browser
            else "browser_consensus"
            if browser_consensus
            else "browser_plus_ui"
            if browser_plus_ui
            else "inconclusive"
        ),
        "proven_empty": proven_empty,
    }
    return "", proven_empty, proof


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
            and not _known_pre_submit_capture_crash(prior)
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

            proof_found_id, proven_empty, inventory_proof = (
                await _prove_empty_after_uncertainty(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                    initial_graph_diagnostics=diagnostics,
                )
            )
            if proof_found_id:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": proof_found_id},
                )
                return {
                    "ad_account_id": proof_found_id,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "reused": True,
                    "cross_job_resume": True,
                    "recovered_after_uncertainty": True,
                    "transport": "uncertain_inventory_v2_reconciliation",
                    "inventory_proof": inventory_proof,
                }

            if proven_empty:
                checkpoint = await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "resume_from": "CREATE",
                        "business_id": business_id,
                        "account_name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "last_error_code": (
                            "CROSS_JOB_UNKNOWN_CLEARED_BY_INVENTORY"
                        ),
                        "last_error": "",
                        "inventory_proof": inventory_proof,
                        "activity": "AD_ACCOUNT_DUPLICATE_GUARD_CLEARED",
                        "activity_at": int(time.time()),
                    },
                )
            else:
                raise ProvisioningError(
                    "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                    (
                        f"A previous Job may already have submitted CREATE for "
                        f"Business {business_id}. Strong read-only inventory "
                        "proof is still inconclusive, so ReMask will not submit "
                        "a duplicate CREATE."
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
            or _known_pre_submit_capture_crash(checkpoint)
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
                    "PRE_SUBMIT_CAPTURE_CRASH_RECOVERED"
                    if _known_pre_submit_capture_crash(checkpoint)
                    else "PRE_SUBMIT_USAGE_STEP_FALSE_UNCERTAIN_RECOVERED"
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
        # A submit may have reached Meta. Resolve it with the same strong,
        # read-only proof used for cross-Job duplicate protection. This avoids
        # a permanent lock when Graph API is unavailable but Business Settings
        # repeatedly proves the exact Business has zero RK.
        proof_found_id, proven_empty, inventory_proof = (
            await _prove_empty_after_uncertainty(
                session,
                business_id=business_id,
                account_name=rk_name,
            )
        )
        if proof_found_id:
            await provisioning_state.remember_entity(
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {"ad_account_id": proof_found_id},
            )
            return {
                "ad_account_id": proof_found_id,
                "business_id": business_id,
                "name": rk_name,
                "currency": currency,
                "timezone_id": timezone_id,
                "recovered_after_uncertainty": True,
                "transport": "uncertain_inventory_v2_reconciliation",
                "inventory_proof": inventory_proof,
            }

        if proven_empty:
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "CREATE_NOT_SUBMITTED",
                    "resume_from": "CREATE",
                    "business_id": business_id,
                    "account_name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "last_error_code": (
                        "CURRENT_JOB_UNKNOWN_CLEARED_BY_INVENTORY"
                    ),
                    "last_error": "",
                    "inventory_proof": inventory_proof,
                    "activity": "AD_ACCOUNT_UNCERTAINTY_CLEARED",
                    "activity_at": int(time.time()),
                },
            )
            phase = "CREATE_NOT_SUBMITTED"
        else:
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
                    "inventory_proof": inventory_proof,
                    "activity": "AD_ACCOUNT_RECONCILE_EXHAUSTED",
                    "activity_at": int(time.time()),
                },
            )
            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                (
                    f"CREATE for Business {business_id} may already have reached "
                    "Meta. Strong read-only inventory proof is still "
                    "inconclusive; duplicate CREATE remains blocked."
                ),
                retryable=True,
            )

    # Read-only preflight enforces the 1 BM = 1 RK invariant. A CREATE must
    # never proceed merely because one inventory transport is unavailable:
    # fall back to Meta Business Settings inventory first so mass jobs cannot
    # create a second RK when Graph permissions/cache are temporarily missing.
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

    graph_inventory_conclusive = any(
        isinstance(row, dict)
        and row.get("stage") == "inventory"
        and row.get("result") == "ok"
        for row in inventory_before
    )
    browser_inventory_before: dict[str, Any] = {}
    browser_inventory_attempts: list[dict[str, Any]] = []
    ui_inventory_before: dict[str, Any] = {}

    if not graph_inventory_conclusive:
        # The public/Graph inventory transport is frequently unavailable for
        # browser-only Meta sessions. Prefer Meta Business Settings' own
        # read-only GraphQL inventory and give it one fresh-session retry
        # before falling back to localized UI evidence.
        browser_found_id = ""
        for browser_inventory_attempt in range(2):
            (
                browser_found_id,
                browser_inventory_before,
            ) = await _reconcile_existing_browser_inventory(
                session,
                business_id=business_id,
                account_name=rk_name,
            )
            browser_inventory_attempts.append(
                {
                    "attempt": browser_inventory_attempt + 1,
                    **(
                        browser_inventory_before
                        if isinstance(browser_inventory_before, dict)
                        else {}
                    ),
                }
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
                    "transport": (
                        "business_settings_graphql_inventory_preflight"
                    ),
                    "reconciliation": inventory_before,
                    "browser_inventory": browser_inventory_before,
                    "browser_inventory_attempts": (
                        browser_inventory_attempts
                    ),
                }

            if bool(browser_inventory_before.get("confirmed_empty")):
                break

            if browser_inventory_attempt == 0:
                await asyncio.sleep(0.75)

        if not bool(browser_inventory_before.get("confirmed_empty")):
            try:
                async with FacebookBusinessBrowser(
                    session.context,
                    timeout_seconds=45,
                ) as inventory_browser:
                    ui_inventory_before = (
                        await inventory_browser.verify_ad_account_inventory_empty(
                            business_id=business_id,
                        )
                    )
            except Exception as exc:
                ui_inventory_before = {
                    "confirmed_empty": False,
                    "error": (
                        f"{exc.__class__.__name__}: {_clean(exc)}"
                    )[:500],
                }

            if not bool(ui_inventory_before.get("confirmed_empty")):
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "resume_from": "CREATE",
                        "business_id": business_id,
                        "account_name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "last_error_code": "AD_ACCOUNT_INVENTORY_UNAVAILABLE",
                        "last_error": (
                            "RK inventory is inconclusive before CREATE; "
                            "duplicate-safe preflight blocked submission."
                        ),
                        "inventory_before": inventory_before,
                        "browser_inventory_before": browser_inventory_before,
                        "browser_inventory_attempts": browser_inventory_attempts,
                        "ui_inventory_before": ui_inventory_before,
                    },
                )
                raise ProvisioningError(
                    "AD_ACCOUNT_INVENTORY_UNAVAILABLE",
                    (
                        f"Business {business_id} inventory could not prove "
                        "that no RK exists. CREATE was not submitted."
                    ),
                    retryable=True,
                )

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

    async def reconcile_after_uncertain(
        *,
        reason: str,
    ) -> dict[str, Any]:
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
                "last_error": reason[:4000],
                "transport": "facebook_private_graphql_live_capture",
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
            reason,
            retryable=True,
        )

    # Phase 1: reproduce Meta's own current wizard and capture the exact
    # private CREATE request. The interceptor aborts it BEFORE Meta receives it.
    #
    # This phase is intentionally self-healing. UI churn before the definitive
    # CREATE request is observed is safe to retry because the interceptor never
    # lets the CREATE mutation reach Meta. A single flaky React render must not
    # force the operator to launch a brand-new Job manually.
    captured_request: dict[str, Any] = {}
    capture_failures: list[dict[str, Any]] = []
    capture_attempt_limit = 3

    for capture_attempt in range(1, capture_attempt_limit + 1):
        await browser_checkpoint(
            {
                "phase": "CREATE_CAPTURE_PREPARING",
                "activity": "AD_ACCOUNT_LIVE_CAPTURE_OPENING",
                "activity_at": int(time.time()),
                "capture_attempt": capture_attempt,
                "capture_attempt_limit": capture_attempt_limit,
                "capture_failures": capture_failures[-3:],
                "transport": "business_suite_live_capture",
            }
        )
        browser: FacebookBusinessBrowser | None = None
        try:
            async with FacebookBusinessBrowser(
                session.context,
                timeout_seconds=90,
            ) as browser:
                captured_request = await browser.capture_ad_account_create_request(
                    business_id=business_id,
                    account_name=rk_name,
                    currency=currency,
                    timezone_id=timezone_id,
                )
            break
        except BrowserBusinessError as exc:
            browser_diag = (
                exc.diagnostic if isinstance(exc.diagnostic, dict) else {}
            )
            compact_diag = _compact_browser_diagnostic(browser_diag)
            failure = {
                "attempt": capture_attempt,
                "code": exc.code,
                "retryable": bool(exc.retryable),
                "message": str(exc)[:1200],
                "diagnostic": compact_diag,
            }
            capture_failures.append(failure)

            try:
                log.warning(
                    "[%s] AD_ACCOUNT live-capture browser failure "
                    "item=%s business=%s attempt=%s/%s code=%s "
                    "retryable=%s diagnostic=%s",
                    profile_id,
                    item_id,
                    business_id,
                    capture_attempt,
                    capture_attempt_limit,
                    exc.code,
                    exc.retryable,
                    json.dumps(
                        compact_diag,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    )[:12000],
                )
            except Exception:
                pass

            safe_retry = (
                bool(exc.retryable)
                and exc.code in AD_ACCOUNT_SAFE_CAPTURE_RETRY_CODES
                and capture_attempt < capture_attempt_limit
            )

            # A final CTA was involved but the definitive mutation was not
            # observed. Even though the capture gate aborts strong unknown
            # candidates, treat this as potentially escaped CREATE until two
            # independent inventory paths prove the Business is still empty.
            if (
                exc.code == "AD_ACCOUNT_CREATE_REQUEST_NOT_OBSERVED"
                and bool(exc.retryable)
            ):
                uncertain_inventory: list[dict[str, Any]] = []
                uncertain_found = ""
                for inventory_attempt in range(3):
                    uncertain_found, inventory_diag = await _reconcile_existing(
                        session,
                        business_id=business_id,
                        account_name=rk_name,
                    )
                    uncertain_inventory.extend(inventory_diag)
                    if uncertain_found:
                        break
                    if inventory_attempt < 2:
                        await asyncio.sleep(2.0)

                if uncertain_found:
                    await provisioning_state.remember_entity(
                        profile_id,
                        scope_key,
                        ProvisioningStep.AD_ACCOUNT,
                        {"ad_account_id": uncertain_found},
                    )
                    return {
                        "ad_account_id": uncertain_found,
                        "business_id": business_id,
                        "name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "reused": True,
                        "recovered_after_uncertainty": True,
                        "transport": "capture_escape_inventory_reconciliation",
                        "reconciliation": uncertain_inventory,
                    }

                graph_empty = _inventory_repeatedly_confirms_empty(
                    uncertain_inventory,
                    required_checks=3,
                )
                browser_found, browser_inventory = (
                    await _reconcile_existing_browser_inventory(
                        session,
                        business_id=business_id,
                        account_name=rk_name,
                    )
                )
                if browser_found:
                    await provisioning_state.remember_entity(
                        profile_id,
                        scope_key,
                        ProvisioningStep.AD_ACCOUNT,
                        {"ad_account_id": browser_found},
                    )
                    return {
                        "ad_account_id": browser_found,
                        "business_id": business_id,
                        "name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "reused": True,
                        "recovered_after_uncertainty": True,
                        "transport": "capture_escape_business_settings_inventory",
                        "reconciliation": uncertain_inventory,
                        "browser_inventory": browser_inventory,
                    }

                secondary_empty = bool(
                    browser_inventory.get("confirmed_empty")
                )
                ui_inventory: dict[str, Any] = {}
                if not secondary_empty:
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
                    except Exception as inventory_exc:
                        ui_inventory = {
                            "confirmed_empty": False,
                            "error": (
                                f"{inventory_exc.__class__.__name__}: "
                                f"{_clean(inventory_exc)}"
                            )[:500],
                        }
                    secondary_empty = bool(
                        ui_inventory.get("confirmed_empty")
                    )

                if (
                    graph_empty
                    and secondary_empty
                    and capture_attempt < capture_attempt_limit
                ):
                    safe_retry = True
                    failure["uncertain_reconciled_empty"] = True
                    failure["browser_inventory"] = browser_inventory
                    failure["ui_inventory"] = ui_inventory
                else:
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
                            "capture_attempt": capture_attempt,
                            "last_error_code": (
                                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN"
                            ),
                            "last_error": (
                                "Final capture action was not observed as a "
                                "definitive mutation and inventory is not "
                                "independently conclusive."
                            ),
                            "reconciliation": uncertain_inventory,
                            "browser_inventory": browser_inventory,
                            "ui_inventory": ui_inventory,
                            "browser_diagnostic": browser_diag,
                        },
                    )
                    raise ProvisioningError(
                        "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                        (
                            "Meta final capture action was not matched to a "
                            "definitive CREATE. Independent inventory checks "
                            "do not safely prove the Business is empty, so "
                            "ReMask will not risk a duplicate CREATE."
                        ),
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
                    "account_name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "capture_attempt": capture_attempt,
                    "capture_attempt_limit": capture_attempt_limit,
                    "capture_failures": capture_failures[-3:],
                    "last_error_code": exc.code,
                    "last_error": str(exc)[:4000],
                    "browser_diagnostic": browser_diag,
                    "transport": "business_suite_live_capture",
                },
            )

            if safe_retry:
                await asyncio.sleep(0.75 * capture_attempt)
                continue

            detail = str(exc)
            if compact_diag:
                detail += " diagnostic=" + json.dumps(
                    compact_diag,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )[:3500]
            if len(capture_failures) > 1:
                detail += " capture_attempts=" + json.dumps(
                    capture_failures[-3:],
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )[:2500]

            raise ProvisioningError(
                exc.code,
                detail,
                retryable=exc.retryable,
            ) from exc
        except Exception as exc:
            # Unknown exceptions do not prove whether the browser had reached
            # the final CTA. Reconcile immediately instead of either blindly
            # retrying or forcing a manual Retry Failed.
            failure = {
                "attempt": capture_attempt,
                "code": "AD_ACCOUNT_CAPTURE_BROWSER_EXCEPTION",
                "retryable": True,
                "message": (
                    f"{exc.__class__.__name__}: {_clean(exc)}"
                )[:1200],
            }
            capture_failures.append(failure)

            browser_phase = (
                browser.ad_account_runtime_phase
                if browser is not None
                else "BROWSER_NOT_ENTERED"
            )
            final_capture_armed = bool(
                browser is not None
                and browser.ad_account_final_capture_armed
            )
            create_may_have_been_sent = bool(
                browser is not None
                and browser.ad_account_create_may_have_been_sent
            )
            failure["browser_phase"] = browser_phase
            failure["final_capture_armed"] = final_capture_armed
            failure["create_may_have_been_sent"] = (
                create_may_have_been_sent
            )

            exception_text = (
                f"{exc.__class__.__name__}: {_clean(exc)}"
            ).casefold()
            page_crashed = any(
                marker in exception_text
                for marker in (
                    "page crashed",
                    "targetclosederror",
                    "target page, context or browser has been closed",
                )
            )
            failure["page_crashed"] = page_crashed

            # Renderer crashes before the final CREATE gate are provably
            # pre-submit. No final CTA was armed and no CREATE transport was
            # marked sent, so restart Chromium and continue the same Job
            # instead of converting a safe crash into RESULT_UNKNOWN.
            if (
                page_crashed
                and not final_capture_armed
                and not create_may_have_been_sent
                and capture_attempt < capture_attempt_limit
            ):
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "resume_from": "CREATE",
                        "business_id": business_id,
                        "account_name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "capture_attempt": capture_attempt,
                        "capture_attempt_limit": capture_attempt_limit,
                        "capture_failures": capture_failures[-3:],
                        "last_error_code": (
                            "AD_ACCOUNT_CAPTURE_PAGE_CRASH_PRE_FINAL"
                        ),
                        "last_error": failure["message"],
                        "browser_phase": browser_phase,
                        "final_capture_armed": False,
                        "create_may_have_been_sent": False,
                        "transport": "business_suite_live_capture",
                    },
                )
                try:
                    log.warning(
                        "[%s] AD_ACCOUNT renderer crashed pre-final; "
                        "restarting capture item=%s business=%s "
                        "attempt=%s/%s phase=%s",
                        profile_id,
                        item_id,
                        business_id,
                        capture_attempt,
                        capture_attempt_limit,
                        browser_phase,
                    )
                except Exception:
                    pass
                await asyncio.sleep(1.0 * capture_attempt)
                continue

            unknown_inventory: list[dict[str, Any]] = []
            unknown_found = ""
            for inventory_attempt in range(3):
                unknown_found, inventory_diag = await _reconcile_existing(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
                unknown_inventory.extend(inventory_diag)
                if unknown_found:
                    break
                if inventory_attempt < 2:
                    await asyncio.sleep(2.0)

            if unknown_found:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": unknown_found},
                )
                return {
                    "ad_account_id": unknown_found,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "reused": True,
                    "recovered_after_uncertainty": True,
                    "transport": "capture_exception_inventory_reconciliation",
                    "reconciliation": unknown_inventory,
                }

            graph_empty = _inventory_repeatedly_confirms_empty(
                unknown_inventory,
                required_checks=3,
            )
            browser_found, browser_inventory = (
                await _reconcile_existing_browser_inventory(
                    session,
                    business_id=business_id,
                    account_name=rk_name,
                )
            )
            if browser_found:
                await provisioning_state.remember_entity(
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {"ad_account_id": browser_found},
                )
                return {
                    "ad_account_id": browser_found,
                    "business_id": business_id,
                    "name": rk_name,
                    "currency": currency,
                    "timezone_id": timezone_id,
                    "reused": True,
                    "recovered_after_uncertainty": True,
                    "transport": "capture_exception_business_settings_inventory",
                    "reconciliation": unknown_inventory,
                    "browser_inventory": browser_inventory,
                }

            secondary_empty = bool(browser_inventory.get("confirmed_empty"))
            ui_inventory: dict[str, Any] = {}
            if not secondary_empty:
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
                except Exception as inventory_exc:
                    ui_inventory = {
                        "confirmed_empty": False,
                        "error": (
                            f"{inventory_exc.__class__.__name__}: "
                            f"{_clean(inventory_exc)}"
                        )[:500],
                    }
                secondary_empty = bool(ui_inventory.get("confirmed_empty"))

            if (
                graph_empty
                and secondary_empty
                and capture_attempt < capture_attempt_limit
            ):
                failure["uncertain_reconciled_empty"] = True
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "resume_from": "CREATE",
                        "business_id": business_id,
                        "account_name": rk_name,
                        "currency": currency,
                        "timezone_id": timezone_id,
                        "capture_attempt": capture_attempt,
                        "capture_attempt_limit": capture_attempt_limit,
                        "capture_failures": capture_failures[-3:],
                        "last_error_code": failure["code"],
                        "last_error": failure["message"],
                        "reconciliation": unknown_inventory,
                        "browser_inventory": browser_inventory,
                        "ui_inventory": ui_inventory,
                        "transport": "business_suite_live_capture",
                    },
                )
                await asyncio.sleep(0.75 * capture_attempt)
                continue

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
                    "capture_attempt": capture_attempt,
                    "capture_attempt_limit": capture_attempt_limit,
                    "capture_failures": capture_failures[-3:],
                    "last_error_code": failure["code"],
                    "last_error": failure["message"],
                    "browser_phase": browser_phase,
                    "final_capture_armed": final_capture_armed,
                    "create_may_have_been_sent": (
                        create_may_have_been_sent
                    ),
                    "page_crashed": page_crashed,
                    "reconciliation": unknown_inventory,
                    "browser_inventory": browser_inventory,
                    "ui_inventory": ui_inventory,
                    "transport": "business_suite_live_capture",
                },
            )
            raise ProvisioningError(
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                (
                    "Unexpected live Add-RK capture exception occurred "
                    f"at phase={browser_phase} final_armed="
                    f"{final_capture_armed} sent="
                    f"{create_may_have_been_sent}. Independent inventory "
                    "checks are not conclusive, so duplicate CREATE remains "
                    "blocked. "
                    + failure["message"]
                ),
                retryable=True,
            ) from exc

    if not captured_request:
        raise ProvisioningError(
            "AD_ACCOUNT_CAPTURE_EXHAUSTED",
            "Live Add-RK capture exhausted without a request. No CREATE was sent.",
            retryable=True,
        )

    capture_doc_id = _clean(captured_request.get("doc_id"))
    capture_friendly = _clean(captured_request.get("friendly_name"))
    capture_variables = captured_request.get("variables")
    if (
        not capture_doc_id.isdigit()
        or not isinstance(capture_variables, dict)
        or not capture_variables
    ):
        raise ProvisioningError(
            "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
            (
                "Meta's live Add-RK request was intercepted but did not expose "
                "a usable doc_id + variables pair. No CREATE was sent."
            ),
            retryable=True,
        )

    await provisioning_state.checkpoint(
        item_id,
        profile_id,
        scope_key,
        ProvisioningStep.AD_ACCOUNT,
        {
            "phase": "CREATE_CAPTURED",
            "resume_from": "CREATE",
            "business_id": business_id,
            "account_name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "capture_doc_id": capture_doc_id,
            "capture_friendly_name": capture_friendly,
            "capture_variable_keys": sorted(capture_variables.keys()),
            "transport": "business_suite_live_capture",
        },
    )

    # Phase 2: replay exactly the request captured above through the same
    # browser-native Facebook session. Safe failures that are explicitly
    # proven PRE-SUBMIT are retried inside this Job; ambiguous failures are
    # never replayed and go straight to inventory reconciliation.
    result = None
    replay_failures: list[dict[str, Any]] = []
    replay_attempt_limit = 2

    for replay_attempt in range(1, replay_attempt_limit + 1):
        submit_started = False

        async def before_private_submit() -> None:
            nonlocal submit_started
            await browser_checkpoint(
                {
                    "phase": "CREATE_SUBMIT_INTENT",
                    "activity": "AD_ACCOUNT_PRIVATE_CAPTURE_REPLAY_SUBMIT_INTENT",
                    "activity_at": int(time.time()),
                    "capture_doc_id": capture_doc_id,
                    "capture_friendly_name": capture_friendly,
                    "replay_attempt": replay_attempt,
                    "replay_attempt_limit": replay_attempt_limit,
                    "transport": "facebook_private_graphql_live_capture",
                }
            )
            submit_started = True

        try:
            web_session = await session.facebook_web()
            result = await asyncio.wait_for(
                create_ad_account_with_docids(
                    web_session,
                    business_id=business_id,
                    account_name=rk_name,
                    currency=currency,
                    timezone_id=timezone_id,
                    profile_id=profile_id,
                    captured_request=captured_request,
                    before_submit=before_private_submit,
                ),
                timeout=90.0,
            )
            break

        except asyncio.TimeoutError as exc:
            if submit_started:
                return await reconcile_after_uncertain(
                    reason=(
                        "Live-captured private CREATE timed out after submit "
                        f"intent on replay attempt {replay_attempt}; inventory "
                        "reconciliation is required before retry."
                    )
                )

            failure = {
                "attempt": replay_attempt,
                "code": "CREATE_AD_ACCOUNT_PRE_SUBMIT_TIMEOUT",
                "message": "Live-captured private CREATE timed out before submit.",
            }
            replay_failures.append(failure)
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "CREATE_NOT_SUBMITTED",
                    "resume_from": "CREATE",
                    "business_id": business_id,
                    "replay_attempt": replay_attempt,
                    "replay_attempt_limit": replay_attempt_limit,
                    "replay_failures": replay_failures[-2:],
                    "last_error_code": failure["code"],
                    "last_error": failure["message"],
                    "transport": "facebook_private_graphql_live_capture",
                },
            )
            if replay_attempt < replay_attempt_limit:
                await asyncio.sleep(0.75)
                continue
            raise ProvisioningError(
                failure["code"],
                failure["message"],
                retryable=True,
            ) from exc

        except AdAccountMutationError as exc:
            if exc.code == "CREATE_AD_ACCOUNT_RESULT_UNKNOWN":
                return await reconcile_after_uncertain(reason=str(exc))

            pre_submit_codes = {
                "CREATE_AD_ACCOUNT_LIVE_CAPTURE_REQUIRED",
                "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
                "CREATE_AD_ACCOUNT_LIVE_CAPTURE_STALE",
                "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT",
                "CREATE_AD_ACCOUNT_BROWSER_TRANSPORT_UNAVAILABLE",
                "SESSION_EXPIRED",
            }
            if exc.code in pre_submit_codes:
                failure = {
                    "attempt": replay_attempt,
                    "code": exc.code,
                    "retryable": bool(exc.retryable),
                    "message": str(exc)[:1600],
                }
                replay_failures.append(failure)
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.AD_ACCOUNT,
                    {
                        "phase": "CREATE_NOT_SUBMITTED",
                        "resume_from": "CREATE",
                        "business_id": business_id,
                        "replay_attempt": replay_attempt,
                        "replay_attempt_limit": replay_attempt_limit,
                        "replay_failures": replay_failures[-2:],
                        "last_error_code": exc.code,
                        "last_error": str(exc)[:4000],
                        "mutation_payload": (
                            exc.payload
                            if isinstance(exc.payload, dict)
                            else {}
                        ),
                        "transport": "facebook_private_graphql_live_capture",
                    },
                )

                # Only a transport failure explicitly classified as PRE-SUBMIT
                # can safely replay the same captured mutation. A stale capture
                # needs a fresh UI capture; auth/browser availability failures
                # need operator/session recovery and must not loop blindly.
                safe_same_capture_retry = (
                    exc.code == "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT"
                    and bool(exc.retryable)
                    and replay_attempt < replay_attempt_limit
                )
                if safe_same_capture_retry:
                    await asyncio.sleep(0.75)
                    continue

                needs_fresh_capture = (
                    exc.code in {
                        "CREATE_AD_ACCOUNT_LIVE_CAPTURE_REQUIRED",
                        "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
                        "CREATE_AD_ACCOUNT_LIVE_CAPTURE_STALE",
                    }
                    and bool(exc.retryable)
                    and replay_attempt < replay_attempt_limit
                )
                if needs_fresh_capture:
                    await browser_checkpoint(
                        {
                            "phase": "CREATE_CAPTURE_PREPARING",
                            "activity": "AD_ACCOUNT_LIVE_RECAPTURE_AFTER_STALE",
                            "activity_at": int(time.time()),
                            "replay_attempt": replay_attempt,
                            "recapture_reason": exc.code,
                            "transport": "business_suite_live_capture",
                        }
                    )
                    try:
                        async with FacebookBusinessBrowser(
                            session.context,
                            timeout_seconds=90,
                        ) as browser:
                            refreshed_capture = (
                                await browser.capture_ad_account_create_request(
                                    business_id=business_id,
                                    account_name=rk_name,
                                    currency=currency,
                                    timezone_id=timezone_id,
                                )
                            )
                    except BrowserBusinessError as recapture_exc:
                        recapture_diag = _compact_browser_diagnostic(
                            recapture_exc.diagnostic
                            if isinstance(recapture_exc.diagnostic, dict)
                            else {}
                        )
                        raise ProvisioningError(
                            recapture_exc.code,
                            (
                                "Automatic re-capture after stale private "
                                "mutation failed: "
                                + str(recapture_exc)
                                + (
                                    " diagnostic="
                                    + json.dumps(
                                        recapture_diag,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                        default=str,
                                    )[:2500]
                                    if recapture_diag
                                    else ""
                                )
                            ),
                            retryable=recapture_exc.retryable,
                        ) from recapture_exc

                    refreshed_doc_id = _clean(
                        refreshed_capture.get("doc_id")
                    )
                    refreshed_variables = refreshed_capture.get("variables")
                    if (
                        not refreshed_doc_id.isdigit()
                        or not isinstance(refreshed_variables, dict)
                        or not refreshed_variables
                    ):
                        raise ProvisioningError(
                            "CREATE_AD_ACCOUNT_LIVE_CAPTURE_INVALID",
                            (
                                "Automatic re-capture after stale mutation "
                                "did not produce doc_id + variables. "
                                "No CREATE was sent."
                            ),
                            retryable=True,
                        )

                    captured_request = refreshed_capture
                    capture_doc_id = refreshed_doc_id
                    capture_friendly = _clean(
                        refreshed_capture.get("friendly_name")
                    )
                    capture_variables = refreshed_variables

                    await browser_checkpoint(
                        {
                            "phase": "CREATE_CAPTURED",
                            "activity": "AD_ACCOUNT_LIVE_RECAPTURE_CONFIRMED",
                            "activity_at": int(time.time()),
                            "capture_doc_id": capture_doc_id,
                            "capture_friendly_name": capture_friendly,
                            "capture_variable_keys": sorted(
                                capture_variables.keys()
                            ),
                            "replay_attempt": replay_attempt,
                            "recapture_reason": exc.code,
                            "transport": "business_suite_live_capture",
                        }
                    )
                    await asyncio.sleep(0.5)
                    continue

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
                    "resume_from": "CREATE" if exc.retryable else "STOP",
                    "business_id": business_id,
                    "last_error_code": exc.code,
                    "last_error": str(exc)[:4000],
                    "mutation_payload": (
                        exc.payload if isinstance(exc.payload, dict) else {}
                    ),
                    "capture_doc_id": capture_doc_id,
                    "capture_friendly_name": capture_friendly,
                    "replay_attempt": replay_attempt,
                    "transport": "facebook_private_graphql_live_capture",
                },
            )
            raise ProvisioningError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc

    if result is None:
        raise ProvisioningError(
            "CREATE_AD_ACCOUNT_REPLAY_EXHAUSTED",
            "Private Add-RK replay exhausted before a result was produced.",
            retryable=True,
        )

    rk_id = _normalize_ad_account_id(result.ad_account_id)
    if not rk_id:
        return await reconcile_after_uncertain(
            reason=(
                "Live-captured private CREATE returned no provable "
                "Ad Account ID."
            )
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
            "transport": "facebook_private_graphql_live_capture",
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
        "transport": "facebook_private_graphql_live_capture",
        "create_response_friendly_name": result.candidate.friendly_name,
        "create_response_doc_id": result.candidate.doc_id,
        "create_response_path": result.response_path,
    }

