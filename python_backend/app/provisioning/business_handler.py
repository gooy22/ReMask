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

    # 2. ИЗВЛЕЧЕНИЕ И ОЧИСТКА PAGE_ID ИЗ FRONTEND PAYLOAD
    page_id_raw = params.get("page_id") or params.get("primary_page_id")
    page_id = str(page_id_raw).strip() if page_id_raw is not None else ""
    if not page_id or page_id.lower() == "none":
        raise ProvisioningError(
            "PRIMARY_PAGE_REQUIRED",
            "BUSINESS.page_id is required for Business Manager creation",
            retryable=False,
        )
    if not re.fullmatch(r"\d{5,30}", page_id):
        raise ProvisioningError(
            "INVALID_PRIMARY_PAGE",
            "BUSINESS.page_id must be a numeric Facebook Page ID",
            retryable=False,
        )

    log.info(
        "[%s] BUSINESS start name=%s primary_page_id=%s key=%s",
        profile_id,
        bm_name,
        page_id,
        idempotency_key,
    )

    try:
        controller = await session.facebook_controller()
        bm_id = await controller.create_business_manager(
            name=bm_name,
            page_id=page_id,
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
