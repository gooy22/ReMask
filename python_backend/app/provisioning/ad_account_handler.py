from __future__ import annotations

from typing import Any

from .transport import ProvisioningTransport


async def ad_account_handler(
    session: Any,
    params: dict[str, Any],
    state: dict[str, Any],
    *,
    transport: ProvisioningTransport | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    profile_id = str(
        state.get("profile_id")
        or getattr(getattr(session, "context", None), "profile_id", "")
    ).strip()
    if not profile_id:
        raise ValueError("profile_id is required")

    business_id = str(state.get("business_id") or "").strip()
    if not business_id:
        raise ValueError("business_id is required before AD_ACCOUNT")

    name = str(params.get("name") or "").strip()
    currency = str(params.get("currency") or "").strip()
    if not name:
        raise ValueError("AD_ACCOUNT.name is required")
    if not currency:
        raise ValueError("AD_ACCOUNT.currency is required")
    if "timezone_id" not in params:
        raise ValueError("AD_ACCOUNT.timezone_id is required")

    client = transport or ProvisioningTransport()
    result = await client.ad_account(
        profile_id=profile_id,
        params=params,
        state=state,
        idempotency_key=idempotency_key or f"{profile_id}:{business_id}:AD_ACCOUNT",
    )
    return {
        "ad_account_id": str(result["ad_account_id"]),
        **{key: value for key, value in result.items() if key != "ad_account_id"},
    }
