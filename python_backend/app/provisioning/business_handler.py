from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from ..facebook_business_browser import BrowserBusinessError
from .models import ProvisioningError, ProvisioningStep
from .state import ProvisioningStateStore


log = logging.getLogger("remask_worker")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _checkpoint_result(step_state: Any) -> dict[str, Any]:
    if not isinstance(step_state, dict):
        return {}
    result = step_state.get("result")
    return dict(result) if isinstance(result, dict) else {}


async def business_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Browser-driven Add BM.

    Important invariants:
    - Meta's own Business Suite UI submits CREATE and Page-add requests.
    - ReMask never needs a CREATE_BM doc_id / qpl_join_id / request envelope.
    - CREATE is checkpointed before the final click.
    - If a previous attempt reached CREATE_SUBMITTED, retry reconciles first and
      never blindly submits another CREATE.
    - The selected Page is attached and verified in the same profile-bound
      Chromium session.
    """
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
    if not isinstance(provisioning_state, ProvisioningStateStore):
        raise ProvisioningError(
            "INTERNAL_STATE_ERROR",
            "BUSINESS requires the system ProvisioningStateStore",
            retryable=False,
        )

    if not item_id:
        raise ProvisioningError(
            "INTERNAL_STATE_ERROR",
            "BUSINESS item_id is missing",
            retryable=False,
        )
    if not profile_id or profile_id.lower() == "none":
        raise ProvisioningError(
            "INVALID_INPUT",
            "profile_id is missing or invalid",
            retryable=False,
        )

    bm_name = _clean(params.get("name") or params.get("bm_name"))
    if not bm_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is required",
            retryable=False,
        )
    if len(bm_name) > 255:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is too long",
            retryable=False,
        )

    page_id = _clean(params.get("page_id") or params.get("primary_page_id"))
    if not re.fullmatch(r"\d{5,30}", page_id):
        raise ProvisioningError(
            "INVALID_PRIMARY_PAGE",
            "BUSINESS.page_id must be a numeric Facebook Page ID",
            retryable=False,
        )

    user_email = _clean(
        params.get("user_email")
        or params.get("email")
        or getattr(context, "email", "")
    )
    if not user_email:
        raise ProvisioningError(
            "BUSINESS_EMAIL_REQUIRED",
            "BUSINESS.user_email is required by Meta Business Suite",
            retryable=False,
        )
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", user_email):
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.user_email is invalid",
            retryable=False,
        )

    first_name = _clean(
        params.get("user_first_name")
        or params.get("first_name")
        or getattr(context, "first_name", "")
    )
    last_name = _clean(
        params.get("user_last_name")
        or params.get("last_name")
        or getattr(context, "last_name", "")
    )
    display_name = _clean(
        params.get("profile_display_name")
        or getattr(context, "display_name", "")
        or profile_id
    )

    step_state = kwargs.get("step_state")
    if not isinstance(step_state, dict):
        step_state = await provisioning_state.step(
            item_id,
            ProvisioningStep.BUSINESS,
        )
    checkpoint = _checkpoint_result(step_state)
    prior_error_code = _clean(
        step_state.get("error_code")
        if isinstance(step_state, dict)
        else ""
    ).upper()

    checkpoint_page = _clean(
        checkpoint.get("primary_page_id")
        or checkpoint.get("page_id")
    )
    if checkpoint_page and checkpoint_page != page_id:
        raise ProvisioningError(
            "BUSINESS_CHECKPOINT_MISMATCH",
            (
                f"Saved BUSINESS checkpoint belongs to Page {checkpoint_page}, "
                f"but retry requested Page {page_id}. CREATE will not be repeated."
            ),
            retryable=False,
        )

    checkpoint_name = _clean(checkpoint.get("business_name"))
    if checkpoint_name:
        bm_name = checkpoint_name

    phase = _clean(
        checkpoint.get("phase")
        or checkpoint.get("resume_from")
    ).upper()
    business_id = _clean(checkpoint.get("business_id"))
    recovered = False

    # The network gate aborts the Meta request if the atomic SUBMITTED
    # checkpoint cannot be persisted. In that specific case we know the
    # irreversible request did NOT reach Meta, so retry may safely submit
    # again instead of getting stuck forever in CLICK_INTENT.
    create_known_not_sent = (
        prior_error_code == "CREATE_CHECKPOINT_FAILED_BEFORE_SEND"
        and phase == "CREATE_CLICK_INTENT"
    )
    page_known_not_sent = (
        prior_error_code == "PAGE_CHECKPOINT_FAILED_BEFORE_SEND"
        and phase == "PAGE_ADD_CLICK_INTENT"
    )
    if create_known_not_sent:
        phase = "CREATE_NOT_SUBMITTED"
    if page_known_not_sent:
        phase = "CREATE_CONFIRMED"

    try:
        browser = await session.facebook_business_browser()

        # Legacy checkpoints produced by the previous GraphQL flow already
        # contain a created business_id. They are safe to resume at Page attach.
        if business_id.isdigit():
            phase = phase or "CREATE_CONFIRMED"
            log.info(
                "[%s] BUSINESS resume existing business_id=%s phase=%s page=%s",
                profile_id,
                business_id,
                phase,
                page_id,
            )

        elif phase in {
            "CREATE_SUBMITTED",
            "CREATE_CLICK_INTENT",
            "CREATE_PENDING_SUBMIT",
            "CREATE_RESULT_UNKNOWN",
        }:
            before_ids = [
                str(value)
                for value in (checkpoint.get("business_ids_before") or [])
                if str(value).isdigit()
            ]
            if not before_ids:
                raise ProvisioningError(
                    "CREATE_RESULT_UNKNOWN",
                    (
                        "A previous CREATE may have been submitted, but the "
                        "pre-submit Business snapshot is missing. ReMask will "
                        "not submit another CREATE automatically."
                    ),
                    retryable=False,
                )

            log.warning(
                "[%s] BUSINESS reconcile after uncertain CREATE item=%s phase=%s",
                profile_id,
                item_id,
                phase,
            )
            recovered_result = await browser.reconcile_created_business(
                before_ids=before_ids,
                business_name=bm_name,
            )
            business_id = _clean(recovered_result.business_id)
            recovered = True
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                {
                    "phase": "CREATE_CONFIRMED",
                    "resume_from": "PAGE_ADD",
                    "business_id": business_id,
                    "business_name": bm_name,
                    "primary_page_id": page_id,
                    "recovered_after_create_uncertainty": True,
                },
            )

        else:
            before_map = await browser.snapshot_businesses()
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                {
                    "phase": "BUSINESS_SNAPSHOT",
                    "business_name": bm_name,
                    "primary_page_id": page_id,
                    "business_ids_before": sorted(before_map),
                },
            )

            async def before_create_submit(patch: dict[str, Any]) -> None:
                await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.BUSINESS,
                    {
                        **patch,
                        "business_name": bm_name,
                        "primary_page_id": page_id,
                    },
                )

            create_result = await browser.create_business(
                business_name=bm_name,
                user_email=user_email,
                user_first_name=first_name,
                user_last_name=last_name,
                profile_display_name=display_name,
                before_snapshot=before_map,
                before_submit=before_create_submit,
            )
            business_id = _clean(create_result.business_id)
            if not business_id.isdigit():
                raise ProvisioningError(
                    "INVALID_RESULT",
                    "Browser CREATE returned invalid business_id",
                    retryable=False,
                )

            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                {
                    "phase": "CREATE_CONFIRMED",
                    "resume_from": "PAGE_ADD",
                    "business_id": business_id,
                    "business_name": bm_name,
                    "primary_page_id": page_id,
                    "business_ids_before": create_result.before_ids,
                    "business_ids_after": create_result.after_ids,
                    "create_response_business_id": (
                        create_result.response_business_id
                    ),
                    "create_response_friendly_name": (
                        create_result.response_friendly_name
                    ),
                    "recovered_after_create_uncertainty": bool(
                        create_result.recovered
                    ),
                },
            )

        if not business_id.isdigit():
            raise ProvisioningError(
                "INVALID_RESULT",
                "BUSINESS has no confirmed business_id",
                retryable=False,
            )

        # If a prior Page-add submit was interrupted, verify before doing
        # anything else. We do not blindly click Add again.
        phase = _clean(checkpoint.get("phase")).upper()
        if phase in {"PAGE_ADD_SUBMITTED", "PAGE_ADD_CLICK_INTENT"}:
            if await browser.verify_page_attached(
                business_id=business_id,
                page_id=page_id,
            ):
                checkpoint = await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.BUSINESS,
                    {
                        "phase": "PAGE_CONFIRMED",
                        "resume_from": "DONE",
                        "page_recovered_after_uncertainty": True,
                    },
                )
            else:
                raise ProvisioningError(
                    "PAGE_ATTACH_RESULT_UNKNOWN",
                    (
                        f"Business {business_id} exists, but a previous Page-add "
                        f"submit for Page {page_id} cannot yet be confirmed. "
                        "Retry performs verification only; ReMask will not "
                        "blindly submit the Page-add action again."
                    ),
                    retryable=True,
                )

        if _clean(checkpoint.get("phase")).upper() != "PAGE_CONFIRMED":
            if await browser.verify_page_attached(
                business_id=business_id,
                page_id=page_id,
            ):
                checkpoint = await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.BUSINESS,
                    {
                        "phase": "PAGE_CONFIRMED",
                        "resume_from": "DONE",
                        "page_already_attached": True,
                    },
                )
            else:
                async def before_page_submit(patch: dict[str, Any]) -> None:
                    await provisioning_state.checkpoint(
                        item_id,
                        profile_id,
                        scope_key,
                        ProvisioningStep.BUSINESS,
                        {
                            **patch,
                            "business_id": business_id,
                            "business_name": bm_name,
                            "primary_page_id": page_id,
                        },
                    )

                page_result = await browser.add_existing_page(
                    business_id=business_id,
                    page_id=page_id,
                    before_submit=before_page_submit,
                )

                checkpoint = await provisioning_state.checkpoint(
                    item_id,
                    profile_id,
                    scope_key,
                    ProvisioningStep.BUSINESS,
                    {
                        "phase": "PAGE_CONFIRMED",
                        "resume_from": "DONE",
                        "business_id": business_id,
                        "business_name": bm_name,
                        "primary_page_id": page_id,
                        "page_already_attached": bool(
                            page_result.already_attached
                        ),
                    },
                )

    except ProvisioningError:
        raise
    except BrowserBusinessError as exc:
        log.warning(
            "[%s] BUSINESS browser failure item=%s code=%s retryable=%s: %s",
            profile_id,
            item_id,
            exc.code,
            exc.retryable,
            exc,
        )
        try:
            await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                {
                    "last_browser_error_code": exc.code,
                    "last_browser_error": str(exc)[:4000],
                    "last_browser_diagnostic": (
                        exc.diagnostic
                        if isinstance(exc.diagnostic, dict)
                        else {}
                    ),
                },
            )
        except Exception:
            log.exception(
                "[%s] failed to persist BUSINESS browser diagnostic item=%s",
                profile_id,
                item_id,
            )

        diagnostic_suffix = ""
        if isinstance(exc.diagnostic, dict) and exc.diagnostic:
            try:
                diagnostic_suffix = " diagnostic=" + json.dumps(
                    exc.diagnostic,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )[:5000]
            except Exception:
                diagnostic_suffix = ""

        raise ProvisioningError(
            exc.code,
            str(exc) + diagnostic_suffix,
            retryable=exc.retryable,
        ) from exc
    except asyncio.TimeoutError as exc:
        raise ProvisioningError(
            "REMOTE_TIMEOUT",
            "Meta Business browser workflow timed out",
            retryable=True,
        ) from exc
    except Exception as exc:
        log.exception(
            "[%s] BUSINESS browser workflow crashed item=%s: %s",
            profile_id,
            item_id,
            exc,
        )
        raise ProvisioningError(
            "BUSINESS_BROWSER_FAILED",
            f"{exc.__class__.__name__}: {exc}",
            retryable=True,
        ) from exc

    return {
        "business_id": business_id,
        "primary_page_id": page_id,
        "phase": "PAGE_CONFIRMED",
        "resume_from": "DONE",
        "resumed": bool(
            recovered
            or checkpoint.get("recovered_after_create_uncertainty")
        ),
        "transport": "facebook_business_suite_ui",
        "create": {
            "response_business_id": _clean(
                checkpoint.get("create_response_business_id")
            ),
            "friendly_name": _clean(
                checkpoint.get("create_response_friendly_name")
            ),
            "recovered": bool(
                checkpoint.get("recovered_after_create_uncertainty")
            ),
        },
        "page": {
            "already_attached": bool(
                checkpoint.get("page_already_attached")
            ),
            "recovered": bool(
                checkpoint.get("page_recovered_after_uncertainty")
            ),
        },
    }
