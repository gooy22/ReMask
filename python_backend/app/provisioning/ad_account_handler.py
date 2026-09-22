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

async def ad_account_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *args, **kwargs
) -> dict[str, Any]:
    
    # 1. СТРОГОЕ ОПРЕДЕЛЕНИЕ И ВАЛИДАЦИЯ PROFILE_ID
    context = session.context
    profile_id = str(state.get("profile_id") or context.profile_id).strip()
    
    if not profile_id or profile_id.lower() == "none":
        raise ProvisioningError("INVALID_INPUT", "profile_id is missing or invalid", retryable=False)
        
    business_id = state.get("business_id")
    idempotency_key = kwargs.get("idempotency_key") or f"{profile_id}:RK"
    
    if not business_id:
        raise ProvisioningError("INVALID_RESULT", "Missing business_id context state for RK creation", retryable=False)
        
    cookies = dict(context.cookies)
    proxy = context.proxy
    user_agent = context.user_agent

    # 2. ЖЕСТКАЯ НОРМАЛИЗАЦИЯ И ЗАЩИТА ОТ NONE (Строго по ТЗ твоего бота)
    rk_name = str(params.get("name") or params.get("rk_name") or f"RK_{profile_id}").strip()
    
    # Защита от пустой валюты {"currency": None}
    currency = params.get("currency")
    currency = str(currency).strip().upper() if currency not in (None, "", "None") else "USD"

    # Защита от пустой таймзоны {"timezone_id": None}
    raw_timezone = params.get("timezone_id")
    if raw_timezone in (None, "", "None"):
        raw_timezone = 1

    try:
        timezone_id = int(raw_timezone)
    except (TypeError, ValueError) as exc:
        raise ProvisioningError(
            "INVALID_INPUT",
            f"AD_ACCOUNT.timezone_id must be an integer, got: {raw_timezone}",
            retryable=False,
        ) from exc

    log.info(f"[{profile_id}] Финал РК-хэндлера. БМ: {business_id}, Валюта: {currency}, ТЗ: {timezone_id}, Key: {idempotency_key}")

    profile_obj = WebProfile(
        name=profile_id,
        cookies=cookies,
        proxy=proxy,
        user_agent=user_agent
    )
    
    try:
        async with WebSessionManager(profile_obj) as боевая_сессия:
            controller = BusinessLogicController(боевая_сессия)
            
            # Стреляем в Facebook через нашу новую keyword-only сигнатуру в fb_worker.py!
            rk_id = await controller.create_ad_account(
                business_id=str(business_id), 
                account_name=rk_name,
                currency=currency,
                timezone_id=timezone_id
            )
            
            if not rk_id:
                raise ProvisioningError("INVALID_RESULT", "Facebook returned empty Ad Account ID", retryable=False)
                
            return {
                "ad_account_id": str(rk_id)
            }
            
    except AuthenticationError as exc:
        raise ProvisioningError("SESSION_EXPIRED", f"FB Session expired: {exc}", retryable=False)
        
    except RemoteRequestError as exc:
        exc_msg = str(exc).lower()
        
        # Ювелирный разбор ошибок, убрали слишком размытый "can't create"
        if any(k in exc_msg for k in ["limit", "max account", "account count"]):
            err_code = "AD_ACCOUNT_LIMIT_REACHED"
            is_retry = False
        elif any(k in exc_msg for k in ["policy", "ban", "restrict", "403", "disabled"]):
            err_code = "ACCOUNT_RESTRICTED"
            is_retry = False
        elif any(k in exc_msg for k in ["timeout", "connect", "disconnect", "500", "502", "503", "network"]):
            err_code = "REMOTE_TIMEOUT"
            is_retry = True
        else:
            err_code = "INVALID_RESULT"
            is_retry = False
            
        log.error(f"[{profile_id}] Сбой GraphQL Meta. Код: {err_code}. Ошибка: {exc}")
        raise ProvisioningError(err_code, f"Meta request failure: {exc}", retryable=is_retry)
        
    except ProxyError as exc:
        raise ProvisioningError("PROXY_DEAD", f"Proxy failure: {exc}", retryable=True)
    except asyncio.TimeoutError as exc:
        raise ProvisioningError("REMOTE_TIMEOUT", f"Network connection timeout: {exc}", retryable=True)
    except Exception:
        raise
