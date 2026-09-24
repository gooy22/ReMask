from __future__ import annotations

import asyncio
import logging
from typing import Any

from fb_worker import AuthenticationError, ProxyError, RemoteRequestError

from ..facebook_ad_account_create import AdAccountMutationError
from .meta_errors import classify_meta_request_error
from .models import ProvisioningError


log = logging.getLogger("remask_worker")


def _clean(value: Any) -> str:
    return str(value or "").strip()


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
        state.get("profile_id")
        or getattr(context, "profile_id", "")
    )

    if not profile_id or profile_id.lower() == "none":
        raise ProvisioningError(
            "INVALID_INPUT",
            "profile_id is missing or invalid",
            retryable=False,
        )

    # Add RK may run in its own stable scope (add-rk-bm-<business_id>), so the
    # confirmed BM ID can be supplied explicitly instead of forcing BUSINESS
    # to run again.
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

    log.info(
        "[%s] AD_ACCOUNT start business=%s currency=%s timezone=%s key=%s",
        profile_id,
        business_id,
        currency,
        timezone_id,
        idempotency_key,
    )

    try:
        controller = await session.facebook_controller()
        result = await controller.create_ad_account_detailed(
            business_id=business_id,
            account_name=rk_name,
            currency=currency,
            timezone_id=timezone_id,
        )

        rk_id = _clean(result.ad_account_id)
        if not rk_id.isdigit():
            raise ProvisioningError(
                "INVALID_RESULT",
                "Facebook returned invalid Ad Account ID",
                retryable=False,
            )

        return {
            "ad_account_id": rk_id,
            "business_id": business_id,
            "name": rk_name,
            "currency": currency,
            "timezone_id": timezone_id,
            "transport": "facebook_web_graphql_browser_native",
            "create_response_friendly_name": result.candidate.friendly_name,
            "create_response_doc_id": result.candidate.doc_id,
            "create_response_path": result.response_path,
        }

    except AdAccountMutationError as exc:
        log.error(
            "[%s] AD_ACCOUNT mutation failed code=%s retryable=%s: %s",
            profile_id,
            exc.code,
            exc.retryable,
            exc,
        )
        raise ProvisioningError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc

    except AuthenticationError as exc:
        raise ProvisioningError(
            "SESSION_EXPIRED",
            f"FB Session expired: {exc}",
            retryable=False,
        ) from exc

    except RemoteRequestError as exc:
        err_code, is_retry, diagnostic = classify_meta_request_error(
            exc,
            entity="AD_ACCOUNT",
        )
        log.error(
            "[%s] AD_ACCOUNT GraphQL failed code=%s retryable=%s %s",
            profile_id,
            err_code,
            is_retry,
            diagnostic,
        )
        raise ProvisioningError(
            err_code,
            diagnostic,
            retryable=is_retry,
        ) from exc

    except ProxyError as exc:
        raise ProvisioningError(
            "PROXY_DEAD",
            f"Proxy failure: {exc}",
            retryable=True,
        ) from exc
    except asyncio.TimeoutError as exc:
        raise ProvisioningError(
            "REMOTE_TIMEOUT",
            f"Network connection timeout: {exc}",
            retryable=True,
        ) from exc
