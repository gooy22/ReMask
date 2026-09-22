from __future__ import annotations
from typing import Any
import logging
import asyncio
import re

from fb_worker import (
    AuthenticationError,
    RemoteRequestError,
    ProxyError,
)
from .models import ProvisioningError
from .meta_errors import classify_meta_request_error

log = logging.getLogger("remask_worker")

async def business_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args, **kwargs
) -> dict[str, Any]:
    
    context = session.context
    profile_id = str(state.get("profile_id") or context.profile_id).strip()
    
    if not profile_id or profile_id.lower() == "none":
        raise ProvisioningError("INVALID_INPUT", "profile_id is missing or invalid", retryable=False)
        
    idempotency_key = kwargs.get("idempotency_key") or f"{profile_id}:BUSINESS"
    
    # 1. СТРОГАЯ НОРМАЛИЗАЦИЯ ИМЕНИ БМ И ЗАЩИТА ОТ ПРОБЕЛОВ
    bm_name = str(params.get("name") or params.get("bm_name") or "").strip()
    if not bm_name:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is required and cannot be empty",
            retryable=False,
        )
    if len(bm_name) > 255:
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.name is too long",
            retryable=False,
        )

    # Primary Page is optional for the current Business creation flow.
    # It is still used by the legacy fallback mutation when available.
    page_id_raw = params.get("page_id") or params.get("primary_page_id")
    page_id = str(page_id_raw).strip() if page_id_raw is not None else ""
    if page_id.lower() == "none":
        page_id = ""
    if page_id and not re.fullmatch(r"\d{5,30}", page_id):
        raise ProvisioningError(
            "INVALID_PRIMARY_PAGE",
            "BUSINESS.page_id must be a numeric Facebook Page ID",
            retryable=False,
        )

    user_email = str(
        params.get("user_email")
        or params.get("email")
        or getattr(context, "email", "")
        or ""
    ).strip()
    user_first_name = str(
        params.get("user_first_name")
        or params.get("first_name")
        or getattr(context, "first_name", "")
        or ""
    ).strip()
    user_last_name = str(
        params.get("user_last_name")
        or params.get("last_name")
        or getattr(context, "last_name", "")
        or ""
    ).strip()
    display_name = str(
        getattr(context, "display_name", "")
        or profile_id
    ).strip()
    vertical = str(params.get("vertical") or "ADVERTISING").strip().upper()
    explicit_doc_id = str(params.get("doc_id") or "").strip() or None

    log.info(
        "[%s] BUSINESS start name=%s primary_page_id=%s email_present=%s "
        "identity_name_present=%s explicit_doc_id=%s key=%s",
        profile_id,
        bm_name,
        page_id or "<none>",
        bool(user_email),
        bool(user_first_name or user_last_name or display_name),
        explicit_doc_id or "<registry>",
        idempotency_key,
    )

    try:
        controller = await session.facebook_controller()
        bm_id = await controller.create_business_manager(
            name=bm_name,
            page_id=page_id,
            doc_id=explicit_doc_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=display_name,
            vertical=vertical,
        )

        if not bm_id:
            raise ProvisioningError(
                "INVALID_RESULT",
                "Facebook returned empty Business ID",
                retryable=False,
            )

        return {
            "business_id": str(bm_id),
            "transport": "facebook_web_graphql",
            "primary_page_id": page_id,
        }
            
    except AuthenticationError as exc:
        raise ProvisioningError("SESSION_EXPIRED", f"FB Session expired: {exc}", retryable=False)
        
    except RemoteRequestError as exc:
        err_code, is_retry, diagnostic = classify_meta_request_error(
            exc,
            entity="BUSINESS",
        )
        log.error(
            "[%s] BUSINESS GraphQL failed code=%s retryable=%s %s",
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
        raise ProvisioningError("PROXY_DEAD", f"Proxy failure: {exc}", retryable=True)
    except asyncio.TimeoutError as exc:
        raise ProvisioningError("REMOTE_TIMEOUT", f"Network connection timeout: {exc}", retryable=True)
    except Exception:
        raise
