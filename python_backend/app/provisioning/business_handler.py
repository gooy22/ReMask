from __future__ import annotations
from typing import Any
import logging
import asyncio

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

    # 1. ЗАЩИТА ОТ ПУСТЫХ ПРОБЕЛОВ ("   ") СТРОГО ПО ЗАМЕЧАНИЮ БОТА
    bm_name = str(params.get("name") or params.get("bm_name") or f"BM_{profile_id}").strip()
    if not bm_name:
        raise ProvisioningError("INVALID_INPUT", "BUSINESS.name is required and cannot be empty", retryable=False)

    log.info(f"[{profile_id}] Запуск финального БМ-хэндлера. Имя: {bm_name}, Key: {idempotency_key}")

    profile_obj = WebProfile(
        name=profile_id,
        cookies=cookies,
        proxy=proxy,
        user_agent=user_agent
    )
    
    try:
        async with WebSessionManager(profile_obj) as боевая_сессия:
            controller = BusinessLogicController(боевая_сессия)
            
            # Стреляем в Facebook приватной мутацией (doc_id подтянется из fb_worker.py)
            bm_id = await controller.create_business_manager(name=bm_name)
            
            if not bm_id:
                raise ProvisioningError("INVALID_RESULT", "Facebook returned empty Business ID", retryable=False)
                
            return {
                "business_id": str(bm_id)
            }
            
    except AuthenticationError as exc:
        raise ProvisioningError("SESSION_EXPIRED", f"FB Session expired: {exc}", retryable=False)
        
    except RemoteRequestError as exc:
        exc_msg = str(exc).lower()
        
        # 2. УБРАЛИ РАЗМЫТЫЙ "count" И "can't create" ПО ТРЕБОВАНИЮ СЕНЬОРА
        if any(k in exc_msg for k in ["limit", "max business", "reached maximum"]):
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
