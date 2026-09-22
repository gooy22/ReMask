from __future__ import annotations
from typing import Any
import logging
import asyncio
import os

from fb_worker import (
    WebProfile, 
    WebSessionManager, 
    BusinessLogicController,
    AuthenticationError,
    RemoteRequestError,
    ProxyError
)
from .models import ProvisioningError

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
    
    cookies = dict(context.cookies)
    proxy = context.proxy
    user_agent = context.user_agent

    # 1. СТРОГАЯ НОРМАЛИЗАЦИЯ ИМЕНИ БМ И ЗАЩИТА ОТ ПРОБЕЛОВ
    bm_name = str(params.get("name") or params.get("bm_name") or f"BM_{profile_id}").strip()
    if not bm_name:
        raise ProvisioningError("INVALID_INPUT", "BUSINESS.name is required and cannot be empty", retryable=False)

    # 2. ИЗВЛЕЧЕНИЕ И ОЧИСТКА PAGE_ID ИЗ FRONTEND PAYLOAD
    page_id_raw = params.get("page_id") or params.get("primary_page_id")
    page_id = str(page_id_raw).strip() if page_id_raw is not None else ""
    if not page_id or page_id.lower() == "none":
        raise ProvisioningError(
            "PRIMARY_PAGE_REQUIRED",
            "BUSINESS.page_id is required for Business Manager creation",
            retryable=False,
        )

    log.info(
        "[%s] BUSINESS start name=%s primary_page_id=%s key=%s",
        profile_id,
        bm_name,
        page_id,
        idempotency_key,
    )

    profile_obj = WebProfile(
        name=profile_id,
        cookies=cookies,
        proxy=proxy,
        user_agent=user_agent
    )
    
    try:
        async with WebSessionManager(profile_obj) as боевая_сессия:
            controller = BusinessLogicController(боевая_сессия)
            
            doc_id = str(os.getenv("REMASK_BM_DOC_ID") or "").strip()
            if doc_id:
                bm_id = await controller.create_business_manager(
                    name=bm_name,
                    page_id=page_id,
                    doc_id=doc_id,
                )
            else:
                bm_id = await controller.create_business_manager(
                    name=bm_name,
                    page_id=page_id,
                )
            
            if not bm_id:
                raise ProvisioningError("INVALID_RESULT", "Facebook returned empty Business ID", retryable=False)
                
            return {
                "business_id": str(bm_id),
                "transport": "facebook_web_graphql",
                "primary_page_id": page_id,
            }
            
    except AuthenticationError as exc:
        raise ProvisioningError("SESSION_EXPIRED", f"FB Session expired: {exc}", retryable=False)
        
    except RemoteRequestError as exc:
        exc_msg = str(exc).lower()
        
        if any(k in exc_msg for k in ['"code":190', "oauth", "session expired", "checkpoint"]):
            err_code = "SESSION_EXPIRED"
            is_retry = False
        elif any(k in exc_msg for k in ['"code":4', '"code":17', "rate limit", "too many calls"]):
            err_code = "RATE_LIMITED"
            is_retry = True
        elif any(k in exc_msg for k in ["permission", "not authorized", "insufficient permission"]):
            err_code = "PERMISSION_DENIED"
            is_retry = False
        elif any(k in exc_msg for k in ["limit", "max business", "reached maximum"]):
            err_code = "BUSINESS_LIMIT_REACHED"
            is_retry = False
        elif any(k in exc_msg for k in ["policy", "ban", "restrict", "403", "disabled"]):
            err_code = "ACCOUNT_RESTRICTED"
            is_retry = False
        elif any(k in exc_msg for k in ["timeout", "connect", "disconnect", "500", "502", "503", "network"]):
            err_code = "REMOTE_TIMEOUT"
            is_retry = True
        else:
            err_code = "META_UNKNOWN_ERROR"
            is_retry = False
            
        log.error(f"[{profile_id}] Сбой БМ GraphQL. Код: {err_code}. Ошибка: {exc}")
        raise ProvisioningError(err_code, f"Meta request failure: {exc}", retryable=is_retry)
        
    except ProxyError as exc:
        raise ProvisioningError("PROXY_DEAD", f"Proxy failure: {exc}", retryable=True)
    except asyncio.TimeoutError as exc:
        raise ProvisioningError("REMOTE_TIMEOUT", f"Network connection timeout: {exc}", retryable=True)
    except Exception:
        raise
