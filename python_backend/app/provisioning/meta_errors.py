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


def classify_meta_request_error(
    exc: RemoteRequestError,
    *,
    entity: str,
) -> tuple[str, bool, str]:
    """
    Convert the structured Meta error carried by fb_worker.RemoteRequestError
    into a stable provisioning error code.

    Returns: (code, retryable, diagnostic)
    """

    entity_key = str(entity or "").strip().upper()
    payload = exc.meta_payload if isinstance(exc.meta_payload, dict) else {}
    http_status = exc.http_status

    codes: set[int] = set()
    messages: list[str] = []
    transient: list[bool] = []

    _walk_meta(payload, codes=codes, messages=messages, transient=transient)

    text = " | ".join(messages + [str(exc)]).lower()
    code_list = ",".join(str(code) for code in sorted(codes)) or "-"
    diagnostic = (
        f"http={http_status if http_status is not None else '-'} "
        f"meta_codes={code_list} message={str(exc)}"
    )

    if http_status == 401 or 190 in codes or "checkpoint" in text or "session expired" in text:
        return "SESSION_EXPIRED", False, diagnostic

    if 4 in codes or 17 in codes or 32 in codes or 613 in codes:
        return "RATE_LIMITED", True, diagnostic

    if any(transient) or http_status in {408, 425, 429, 500, 502, 503, 504}:
        return "REMOTE_TIMEOUT", True, diagnostic

    if 10 in codes or 200 in codes or "permission" in text or "not authorized" in text:
        return "PERMISSION_DENIED", False, diagnostic

    if entity_key == "BUSINESS" and any(
        token in text
        for token in (
            "max business",
            "maximum business",
            "business limit",
            "reached maximum",
            "too many businesses",
        )
    ):
        return "BUSINESS_LIMIT_REACHED", False, diagnostic

    if entity_key == "AD_ACCOUNT" and any(
        token in text
        for token in (
            "max account",
            "maximum ad account",
            "account count",
            "ad account limit",
            "too many ad accounts",
        )
    ):
        return "AD_ACCOUNT_LIMIT_REACHED", False, diagnostic

    if http_status == 403 or any(
        token in text
        for token in (
            "restricted",
            "disabled",
            "policy",
            "not allowed",
            "cannot perform",
        )
    ):
        return "ACCOUNT_RESTRICTED", False, diagnostic

    if any(
        token in text
        for token in (
            "timeout",
            "network",
            "connection",
            "disconnect",
        )
    ):
        return "REMOTE_TIMEOUT", True, diagnostic

    return "META_UNKNOWN_ERROR", False, diagnostic
