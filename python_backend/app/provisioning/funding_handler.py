from __future__ import annotations

from typing import Any

from .models import ProvisioningError
from .transport import ProvisioningTransport


def validate_funding_result(result: dict[str, Any], ad_account_id: str, funding_source_id: str) -> None:
    result_account = str(result.get("ad_account_id") or "").strip()
    if result_account.startswith("act_"):
        result_account = result_account[4:]
    target_account = ad_account_id[4:] if ad_account_id.startswith("act_") else ad_account_id
    if (
        not target_account
        or not funding_source_id
        or result_account != target_account
        or str(result.get("funding_source_id") or "").strip() != funding_source_id
        or result.get("funding_verified") is not True
    ):
        raise ProvisioningError(
            "FUNDING_RESULT_UNVERIFIED",
            "Funding assignment was not verified for the requested RK and source.",
            retryable=False,
        )


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
    validate_funding_result(result, ad_account_id, funding_source_id)
    return {
        "funding_source_id": str(result["funding_source_id"]),
        **{key: value for key, value in result.items() if key != "funding_source_id"},
    }
