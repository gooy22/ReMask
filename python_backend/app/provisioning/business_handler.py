from __future__ import annotations
from typing import Any
import logging
import asyncio
import re

from fb_worker import (
    RemoteRequestError,
    ProxyError,
)
from ..business_create_service import (
    BusinessCreateError,
    create_business_resilient,
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
    if user_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", user_email):
        raise ProvisioningError(
            "INVALID_INPUT",
            "BUSINESS.user_email must be a valid email when provided",
            retryable=False,
        )
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

    raw_require_page_backed = params.get("require_page_backed")
    if raw_require_page_backed is None:
        # ReMask Add BM is Fan-Page-backed by default. A caller must opt out
        # explicitly if it intentionally wants a non-Page Business flow.
        require_page_backed = True
    elif isinstance(raw_require_page_backed, bool):
        require_page_backed = raw_require_page_backed
    else:
        require_page_backed = str(raw_require_page_backed).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    if require_page_backed and not page_id:
        raise ProvisioningError(
            "PRIMARY_PAGE_REQUIRED",
            "Add BM requires a Fan Page for page-backed creation",
            retryable=False,
        )

    raw_timezone = params.get("timezone_id")
    timezone_id = None
    if raw_timezone not in (None, "", "None"):
        try:
            timezone_id = int(raw_timezone)
        except (TypeError, ValueError) as exc:
            raise ProvisioningError(
                "INVALID_INPUT",
                "BUSINESS.timezone_id must be an integer when provided",
                retryable=False,
            ) from exc

    log.info(
        "[%s] BUSINESS start name=%s primary_page_id=%s email_present=%s "
        "identity_name_present=%s explicit_doc_id=%s page_backed=%s key=%s",
        profile_id,
        bm_name,
        page_id or "<none>",
        bool(user_email),
        bool(user_first_name or user_last_name or display_name),
        explicit_doc_id or "<registry>",
        require_page_backed,
        idempotency_key,
    )

    try:
        result = await create_business_resilient(
            session,
            business_name=bm_name,
            page_id=page_id,
            user_email=user_email,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            profile_display_name=display_name,
            vertical=vertical,
            timezone_id=timezone_id,
            explicit_doc_id=explicit_doc_id,
            require_page_backed=require_page_backed,
        )

        if not result.business_id:
            raise ProvisioningError(
                "INVALID_RESULT",
                "Facebook returned empty Business ID",
                retryable=False,
            )

        log.info(
            "[%s] BUSINESS success id=%s transport=%s primary_page_id=%s",
            profile_id,
            result.business_id,
            result.transport,
            result.primary_page_id or "<none>",
        )

        return {
            "business_id": str(result.business_id),
            "transport": result.transport,
            "primary_page_id": result.primary_page_id,
            "diagnostics": result.diagnostics,
        }

    except BusinessCreateError as exc:
        log.error(
            "[%s] BUSINESS create failed code=%s retryable=%s message=%s diagnostics=%s",
            profile_id,
            exc.code,
            exc.retryable,
            str(exc),
            exc.diagnostics,
        )
        raise ProvisioningError(
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc

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
