from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fb_worker import (
    AuthenticationError,
    ProxyError,
    RemoteRequestError,
)

from ..facebook_business_create import BusinessMutationError
from .meta_errors import classify_meta_request_error
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

    bm_name = _clean(
        params.get("name")
        or params.get("bm_name")
    )
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

    page_id = _clean(
        params.get("page_id")
        or params.get("primary_page_id")
    )
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
            "BUSINESS.user_email is required by current private Business creation flow",
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

    qpl_join_id = _clean(
        params.get("qpl_join_id")
    )

    request_envelope = (
        params.get("request_envelope")
        if isinstance(params.get("request_envelope"), dict)
        else {}
    )

    manual_doc_id = _clean(
        params.get("manual_doc_id")
        or params.get("doc_id")
    )
    if manual_doc_id and not re.fullmatch(r"\d{5,40}", manual_doc_id):
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.manual_doc_id must contain 5-40 digits",
            retryable=False,
        )

    step_state = kwargs.get("step_state")
    if not isinstance(step_state, dict):
        step_state = await provisioning_state.step(
            item_id,
            ProvisioningStep.BUSINESS,
        )

    checkpoint = _checkpoint_result(step_state)

    checkpoint_business_id = _clean(checkpoint.get("business_id"))
    checkpoint_resume_from = _clean(
        checkpoint.get("resume_from")
    ).upper()

    resumed = bool(
        checkpoint_business_id.isdigit()
        and checkpoint_resume_from == "ATTACH_PAGE"
    )

    controller = await session.facebook_controller()

    if resumed:
        business_id = checkpoint_business_id

        checkpoint_page_id = _clean(
            checkpoint.get("primary_page_id")
            or checkpoint.get("page_id")
        )
        if checkpoint_page_id and checkpoint_page_id != page_id:
            raise ProvisioningError(
                "BUSINESS_CHECKPOINT_MISMATCH",
                (
                    f"Saved BUSINESS checkpoint belongs to Page "
                    f"{checkpoint_page_id}, but retry requested Page {page_id}. "
                    "CREATE will not be repeated."
                ),
                retryable=False,
            )

        checkpoint_name = _clean(checkpoint.get("business_name"))
        if checkpoint_name:
            bm_name = checkpoint_name

        log.warning(
            "[%s] BUSINESS resume item=%s business_id=%s page_id=%s",
            profile_id,
            item_id,
            business_id,
            page_id,
        )
    else:
        log.info(
            "[%s] BUSINESS CREATE item=%s name=%s page=%s manual_doc_id=%s",
            profile_id,
            item_id,
            bm_name,
            page_id,
            manual_doc_id or "<none>",
        )

        try:
            create_result = await controller.create_business_manager_v2(
                params={
                    "name": bm_name,
                    "user_email": user_email,
                    "user_first_name": first_name,
                    "user_last_name": last_name,
                    "profile_display_name": display_name,
                    "qpl_join_id": qpl_join_id,
                    "request_envelope": request_envelope,
                    "manual_doc_id": manual_doc_id,
                },
                profile_id=profile_id,
            )
        except BusinessMutationError as exc:
            raise ProvisioningError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc
        except AuthenticationError as exc:
            raise ProvisioningError(
                "SESSION_EXPIRED",
                str(exc),
                retryable=False,
            ) from exc
        except RemoteRequestError as exc:
            error_code, retryable, diagnostic = classify_meta_request_error(
                exc,
                entity="BUSINESS",
            )
            raise ProvisioningError(
                error_code,
                diagnostic,
                retryable=retryable,
            ) from exc
        except ProxyError as exc:
            raise ProvisioningError(
                "PROXY_DEAD",
                str(exc),
                retryable=True,
            ) from exc
        except asyncio.TimeoutError as exc:
            raise ProvisioningError(
                "REMOTE_TIMEOUT",
                "Facebook BUSINESS create request timeout",
                retryable=True,
            ) from exc

        business_id = _clean(create_result.business_id)
        if not business_id.isdigit():
            raise ProvisioningError(
                "INVALID_RESULT",
                "CREATE_BM returned invalid business_id",
                retryable=False,
            )

        checkpoint = {
            "business_id": business_id,
            "business_name": bm_name,
            "primary_page_id": page_id,
            "resume_from": "ATTACH_PAGE",
            "create_doc_id": _clean(create_result.candidate.doc_id),
            "create_friendly_name": _clean(
                create_result.candidate.friendly_name
            ),
            "create_source": _clean(create_result.candidate.source),
            "create_variables_mode": _clean(
                create_result.candidate.variables_mode
            ),
            "attach_attempts": 0,
            "last_attach_error": "",
        }

        try:
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                checkpoint,
            )
        except Exception as exc:
            log.critical(
                "[%s] BUSINESS created id=%s but atomic checkpoint failed item=%s: %s",
                profile_id,
                business_id,
                item_id,
                exc,
            )
            raise ProvisioningError(
                "BUSINESS_CREATE_CHECKPOINT_FAILED",
                (
                    f"Business {business_id} was created, but the local "
                    "ATTACH_PAGE checkpoint could not be committed. "
                    "CREATE must not be retried automatically."
                ),
                retryable=False,
            ) from exc

    try:
        attach_result = await controller.attach_page_to_business(
            business_id=business_id,
            business_name=bm_name,
            page_id=page_id,
            profile_id=profile_id,
        )
    except Exception as exc:
        attach_attempts = int(checkpoint.get("attach_attempts") or 0) + 1

        try:
            checkpoint = await provisioning_state.checkpoint(
                item_id,
                profile_id,
                scope_key,
                ProvisioningStep.BUSINESS,
                {
                    "business_id": business_id,
                    "business_name": bm_name,
                    "primary_page_id": page_id,
                    "resume_from": "ATTACH_PAGE",
                    "attach_attempts": attach_attempts,
                    "last_attach_error": (
                        f"{exc.__class__.__name__}: {exc}"
                    )[:4000],
                },
            )
        except Exception as checkpoint_exc:
            log.error(
                "[%s] failed to persist ATTACH_PAGE failure checkpoint "
                "item=%s business_id=%s: %s",
                profile_id,
                item_id,
                business_id,
                checkpoint_exc,
            )

        if isinstance(exc, BusinessMutationError):
            detail = str(exc)
        elif isinstance(exc, AuthenticationError):
            detail = f"SESSION_EXPIRED: {exc}"
        elif isinstance(exc, RemoteRequestError):
            _, _, detail = classify_meta_request_error(
                exc,
                entity="BUSINESS_PAGE_ATTACH",
            )
        elif isinstance(exc, ProxyError):
            detail = f"PROXY_DEAD: {exc}"
        elif isinstance(exc, asyncio.TimeoutError):
            detail = "REMOTE_TIMEOUT: Facebook Page attach request timeout"
        else:
            detail = f"{exc.__class__.__name__}: {exc}"

        raise ProvisioningError(
            "BUSINESS_CREATED_PAGE_ATTACH_FAILED",
            (
                f"Business {business_id} already exists and is checkpointed. "
                f"resume_from=ATTACH_PAGE. Primary Page {page_id} attachment "
                f"failed: {detail}"
            ),
            retryable=True,
        ) from exc

    return {
        "business_id": business_id,
        "primary_page_id": page_id,
        "resume_from": "DONE",
        "resumed": resumed,
        "transport": "facebook_web_graphql_scope_selector_plus_primary_page",
        "create": {
            "doc_id": _clean(checkpoint.get("create_doc_id")),
            "friendly_name": _clean(
                checkpoint.get("create_friendly_name")
            ),
            "variables_mode": _clean(
                checkpoint.get("create_variables_mode")
            ),
            "source": _clean(checkpoint.get("create_source")),
        },
        "attach": {
            "doc_id": _clean(attach_result.candidate.doc_id),
            "friendly_name": _clean(
                attach_result.candidate.friendly_name
            ),
            "variables_mode": _clean(
                attach_result.candidate.variables_mode
            ),
            "source": _clean(attach_result.candidate.source),
        },
    }
