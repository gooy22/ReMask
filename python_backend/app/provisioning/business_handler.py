from __future__ import annotations

import re
from typing import Any

from ..facebook_business_browser import BrowserBusinessError
from .models import ProvisioningError, ProvisioningStep
from .state import ProvisioningStateStore


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _checkpoint_result(step_state: Any) -> dict[str, Any]:
    if not isinstance(step_state, dict):
        return {}

    result = step_state.get("result")
    return dict(result) if isinstance(result, dict) else {}


def _numeric_ids(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []

    output: list[str] = []
    for item in value:
        clean = _clean(item)
        if re.fullmatch(r"\d{5,30}", clean) and clean not in output:
            output.append(clean)
    return output


def _raise_browser(exc: BrowserBusinessError) -> None:
    raise ProvisioningError(
        exc.code,
        str(exc),
        retryable=exc.retryable,
    ) from exc


async def business_handler(
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

    business_name = _clean(params.get("name") or params.get("bm_name"))
    if not business_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is required",
            retryable=False,
        )
    if len(business_name) > 255:
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
            "BUSINESS.user_email is required by the Meta Business creation UI",
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

    saved_page = _clean(
        checkpoint.get("primary_page_id")
        or checkpoint.get("page_id")
    )
    if saved_page and saved_page != page_id:
        raise ProvisioningError(
            "BUSINESS_CHECKPOINT_MISMATCH",
            (
                f"Saved BUSINESS checkpoint belongs to Page {saved_page}, "
                f"but retry requested Page {page_id}. CREATE will not be repeated."
            ),
            retryable=False,
        )

    saved_name = _clean(checkpoint.get("business_name"))
    if saved_name:
        business_name = saved_name

    business_id = _clean(checkpoint.get("business_id"))
    if business_id and not business_id.isdigit():
        raise ProvisioningError(
            "BUSINESS_CHECKPOINT_INVALID",
            "Saved BUSINESS checkpoint contains invalid business_id",
            retryable=False,
        )

    phase = _clean(checkpoint.get("phase")).upper()
    legacy_resume = (
        business_id.isdigit()
        and _clean(checkpoint.get("resume_from")).upper() == "ATTACH_PAGE"
    )

    if business_id.isdigit() and not phase:
        phase = "CREATE_CONFIRMED"
    if legacy_resume:
        phase = "CREATE_CONFIRMED"

    resumed = bool(
        business_id.isdigit()
        or phase in {
            "CREATE_SUBMITTED",
            "CREATE_CONFIRMED",
            "PAGE_ADD_SUBMITTED",
            "PAGE_CONFIRMED",
            "DONE",
        }
    )

    async def save_checkpoint(patch: dict[str, Any]) -> dict[str, Any]:
        nonlocal checkpoint
        try:
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                patch,
            )
        except Exception as exc:
            raise ProvisioningError(
                "BUSINESS_CHECKPOINT_FAILED",
                (
                    "BUSINESS state could not be persisted before an irreversible "
                    "Meta action. The action was not intentionally repeated."
                ),
                retryable=False,
            ) from exc
        return checkpoint

    try:
        browser_factory = getattr(session, "facebook_business_browser", None)
        if not callable(browser_factory):
            raise ProvisioningError(
                "BROWSER_UNAVAILABLE",
                "Current worker session does not provide the Business Suite browser driver",
                retryable=False,
            )

        async with browser_factory() as browser:
            # CREATE_SUBMITTED means the click may already have reached Meta.
            # Reconcile first; never issue CREATE again from this checkpoint.
            if not business_id and phase == "CREATE_SUBMITTED":
                before_ids = _numeric_ids(checkpoint.get("business_ids_before"))
                if not before_ids and checkpoint.get("business_ids_before") not in ([], None):
                    raise ProvisioningError(
                        "BUSINESS_CHECKPOINT_INVALID",
                        "CREATE_SUBMITTED checkpoint has invalid business_ids_before",
                        retryable=False,
                    )

                try:
                    recovered = await browser.reconcile_created_business(
                        before_ids=before_ids,
                        business_name=business_name,
                    )
                except BrowserBusinessError as exc:
                    _raise_browser(exc)

                business_id = _clean(recovered.business_id)
                await save_checkpoint(
                    {
                        "phase": "CREATE_CONFIRMED",
                        "business_id": business_id,
                        "business_name": business_name,
                        "primary_page_id": page_id,
                        "business_ids_before": recovered.before_ids,
                        "business_ids_after": recovered.after_ids,
                        "create_recovered": True,
                        "resume_from": "PAGE_ADD",
                    }
                )
                phase = "CREATE_CONFIRMED"

            if not business_id:
                before_snapshot = await browser.snapshot_businesses()

                async def before_create_submit(patch: dict[str, Any]) -> None:
                    await save_checkpoint(
                        {
                            **patch,
                            "business_name": business_name,
                            "primary_page_id": page_id,
                            "resume_from": "VERIFY_CREATE",
                        }
                    )

                try:
                    create_result = await browser.create_business(
                        business_name=business_name,
                        user_email=user_email,
                        user_first_name=first_name,
                        user_last_name=last_name,
                        profile_display_name=display_name,
                        before_snapshot=before_snapshot,
                        before_submit=before_create_submit,
                    )
                except BrowserBusinessError as exc:
                    _raise_browser(exc)

                business_id = _clean(create_result.business_id)
                if not business_id.isdigit():
                    raise ProvisioningError(
                        "INVALID_RESULT",
                        "Meta Business UI returned invalid business_id",
                        retryable=False,
                    )

                await save_checkpoint(
                    {
                        "phase": "CREATE_CONFIRMED",
                        "business_id": business_id,
                        "business_name": business_name,
                        "primary_page_id": page_id,
                        "business_ids_before": create_result.before_ids,
                        "business_ids_after": create_result.after_ids,
                        "create_response_business_id": create_result.response_business_id,
                        "create_friendly_name": create_result.response_friendly_name,
                        "create_recovered": bool(create_result.recovered),
                        "resume_from": "PAGE_ADD",
                    }
                )
                phase = "CREATE_CONFIRMED"

            if not business_id.isdigit():
                raise ProvisioningError(
                    "INVALID_RESULT",
                    "BUSINESS flow has no confirmed business_id",
                    retryable=False,
                )

            phase = _clean(checkpoint.get("phase") or phase).upper()

            # If Page-add was already submitted, verify only. Blindly clicking
            # Add again could turn an unknown response into a second mutation.
            if phase == "PAGE_ADD_SUBMITTED":
                try:
                    attached = await browser.verify_page_attached(
                        business_id=business_id,
                        page_id=page_id,
                    )
                except BrowserBusinessError as exc:
                    _raise_browser(exc)

                if not attached:
                    raise ProvisioningError(
                        "PAGE_ATTACH_RESULT_UNKNOWN",
                        (
                            f"Business {business_id} exists and Page add was already "
                            f"submitted for {page_id}, but attachment is not confirmed. "
                            "ReMask will not submit the Page-add mutation again blindly."
                        ),
                        retryable=False,
                    )

                await save_checkpoint(
                    {
                        "phase": "PAGE_CONFIRMED",
                        "business_id": business_id,
                        "primary_page_id": page_id,
                        "resume_from": "DONE",
                        "page_recovered": True,
                    }
                )
                phase = "PAGE_CONFIRMED"

            if phase not in {"PAGE_CONFIRMED", "DONE"}:
                try:
                    already_attached = await browser.verify_page_attached(
                        business_id=business_id,
                        page_id=page_id,
                    )
                except BrowserBusinessError as exc:
                    _raise_browser(exc)

                if already_attached:
                    await save_checkpoint(
                        {
                            "phase": "PAGE_CONFIRMED",
                            "business_id": business_id,
                            "primary_page_id": page_id,
                            "resume_from": "DONE",
                            "page_already_attached": True,
                        }
                    )
                else:
                    async def before_page_submit(patch: dict[str, Any]) -> None:
                        await save_checkpoint(
                            {
                                **patch,
                                "business_name": business_name,
                                "resume_from": "VERIFY_PAGE",
                            }
                        )

                    try:
                        page_result = await browser.add_existing_page(
                            business_id=business_id,
                            page_id=page_id,
                            before_submit=before_page_submit,
                        )
                    except BrowserBusinessError as exc:
                        _raise_browser(exc)

                    await save_checkpoint(
                        {
                            "phase": "PAGE_CONFIRMED",
                            "business_id": business_id,
                            "primary_page_id": page_id,
                            "resume_from": "DONE",
                            "page_already_attached": bool(
                                page_result.already_attached
                            ),
                        }
                    )

            return {
                "business_id": business_id,
                "primary_page_id": page_id,
                "phase": "DONE",
                "resume_from": "DONE",
                "resumed": resumed,
                "transport": "facebook_business_suite_ui",
                "create": {
                    "recovered": bool(checkpoint.get("create_recovered")),
                    "response_business_id": _clean(
                        checkpoint.get("create_response_business_id")
                    ),
                    "friendly_name": _clean(
                        checkpoint.get("create_friendly_name")
                    ),
                },
                "page": {
                    "recovered": bool(checkpoint.get("page_recovered")),
                    "already_attached": bool(
                        checkpoint.get("page_already_attached")
                    ),
                },
            }

    except BrowserBusinessError as exc:
        _raise_browser(exc)
