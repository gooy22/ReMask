from __future__ import annotations

from typing import Any

from .transport import ProvisioningTransport


async def business_handler(
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

    client = transport or ProvisioningTransport()
    result = await client.business(
        profile_id=profile_id,
        params=params,
        state=state,
        idempotency_key=idempotency_key or f"{profile_id}:BUSINESS",
    )
    return {
        "business_id": str(result["business_id"]),
        **{key: value for key, value in result.items() if key != "business_id"},
    }
