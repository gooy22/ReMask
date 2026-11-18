from __future__ import annotations

from typing import Any

from fb_worker import RemoteRequestError


def _walk_meta(value: Any, *, codes: set[int], messages: list[str], transient: list[bool]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in {"code", "error_code", "error_subcode"}:
                try:
                    codes.add(int(child))
                except (TypeError, ValueError):
                    pass
            elif normalized in {"message", "error_user_msg", "error_user_title", "description"}:
                text = str(child or "").strip()
                if text:
                    messages.append(text)
            elif normalized in {"is_transient", "transient"}:
                transient.append(bool(child))
            _walk_meta(child, codes=codes, messages=messages, transient=transient)
    elif isinstance(value, list):
        for child in value:
            _walk_meta(child, codes=codes, messages=messages, transient=transient)


def classify_meta_payload_error(
    payload: Any,
    *,
    entity: str,
    http_status: int | None = None,
    fallback_message: str = "",
) -> tuple[str, bool, str]:
    """Classify an explicit Meta error payload without losing its user message.

    A Meta rate limit or advertising restriction is terminal for the current
    provisioning attempt. Replaying CREATE after that response can intensify
    the restriction, so callers must stop and surface the returned code.
    """

    entity_key = str(entity or "").strip().upper()
    body = payload if isinstance(payload, dict) else {}

    codes: set[int] = set()
    messages: list[str] = []
    transient: list[bool] = []
    _walk_meta(body, codes=codes, messages=messages, transient=transient)

    meta_message = " | ".join(dict.fromkeys(messages))
    fallback = str(fallback_message or "").strip()
    combined = " | ".join(value for value in (meta_message, fallback) if value)
    text = combined.lower()
    code_list = ",".join(str(code) for code in sorted(codes)) or "-"
    display_message = (meta_message or fallback or "Meta returned an error without a message")[:1200]
    diagnostic = (
        f"http={http_status if http_status is not None else '-'} "
        f"meta_codes={code_list} message={display_message}"
    )

    if http_status == 401 or 190 in codes or "checkpoint" in text or "session expired" in text:
        return "SESSION_EXPIRED", False, diagnostic

    restriction_markers = (
        "restricted from advertising", "account restricted from advertising",
        "account is restricted from advertising", "ad account restricted",
        "ad account is restricted", "ad account has been restricted",
        "ad account disabled", "ad account is disabled",
        "advertising account restricted", "advertising account is restricted",
        "advertising access restricted", "advertising access is restricted",
        "advertising access disabled", "advertising privileges restricted",
        "can't advertise", "cannot advertise", "unable to advertise",
        "temporarily blocked from advertising",
        "ограничен для рекламы", "ограничена рекламная деятельность",
        "ограничен доступ к рекламе", "доступ к рекламе ограничен",
        "рекламный аккаунт ограничен", "ограничение рекламного аккаунта",
        "не можете размещать рекламу", "заблокирован доступ к рекламе",
        "обмежено рекламування", "обмежений доступ до реклами",
        "доступ до реклами обмежено", "рекламний акаунт обмежено",
        "обліковий запис обмежено для реклами", "не можете рекламувати",
    )
    if any(token in text for token in restriction_markers):
        return "ACCOUNT_RESTRICTED", False, diagnostic

    rate_limit_markers = (
        "rate limit", "too many requests", "we limit how often",
        "doing that too often", "too fast", "too quickly",
        "suspiciously fast", "suspicious activity", "unusual activity",
        "slow down", "try again later",
        "подозрительно быстр", "подозрительно быстрая", "слишком быстро",
        "слишком часто", "слишком много запрос", "повторите позже",
        "попробуйте позже", "попробуйте через некоторое время",
        "підозріло швид", "занадто швидко", "надто часто",
        "забагато запит", "спробуйте пізніше",
    )
    if http_status == 429 or codes.intersection({4, 17, 32, 613}) or any(
        token in text for token in rate_limit_markers
    ):
        return "RATE_LIMITED", False, diagnostic

    if any(transient) or http_status in {408, 425, 500, 502, 503, 504}:
        return "REMOTE_TIMEOUT", True, diagnostic

    if http_status == 403 or 10 in codes or 200 in codes or "permission" in text or "not authorized" in text:
        return "PERMISSION_DENIED", False, diagnostic

    if entity_key == "BUSINESS" and any(
        token in text
        for token in (
            "max business", "maximum business", "business limit",
            "reached maximum", "too many businesses",
        )
    ):
        return "BUSINESS_LIMIT_REACHED", False, diagnostic

    if entity_key == "AD_ACCOUNT" and any(
        token in text
        for token in (
            "max account", "maximum ad account", "account count",
            "ad account limit", "too many ad accounts",
        )
    ):
        return "AD_ACCOUNT_LIMIT_REACHED", False, diagnostic

    if any(token in text for token in ("timeout", "network", "connection", "disconnect")):
        return "REMOTE_TIMEOUT", True, diagnostic

    return "META_UNKNOWN_ERROR", False, diagnostic


def classify_meta_request_error(
    exc: RemoteRequestError,
    *,
    entity: str,
) -> tuple[str, bool, str]:
    """Classify the structured Meta error carried by a private HTTP response."""

    payload = exc.meta_payload if isinstance(exc.meta_payload, dict) else {}
    return classify_meta_payload_error(
        payload,
        http_status=exc.http_status,
        entity=entity,
        fallback_message=str(exc),
    )
