from __future__ import annotations

from typing import Any

from .transport import ProvisioningTransport


async def funding_handler(
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

    ad_account_id = str(state.get("ad_account_id") or "").strip()
    if not ad_account_id:
        raise ValueError("ad_account_id is required before FUNDING")

    funding_source_id = str(params.get("funding_source_id") or "").strip()
    if not funding_source_id:
        raise ValueError("FUNDING.funding_source_id is required")

    client = transport or ProvisioningTransport()
    result = await client.funding(
        profile_id=profile_id,
        params=params,
        state=state,
        idempotency_key=idempotency_key or f"{profile_id}:{ad_account_id}:FUNDING",
    )
    return {
        "funding_source_id": str(result["funding_source_id"]),
        **{key: value for key, value in result.items() if key != "funding_source_id"},
    }
