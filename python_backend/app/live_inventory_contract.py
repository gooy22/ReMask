from __future__ import annotations

from typing import Any, Iterable

PROFILE_FULL = "profile_full"
BUSINESS_SCOPED = "business_scoped"


def _digits(value: Any) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("act_"):
        raw = raw[4:]
    return raw if raw.isdigit() else ""


def normalize_ids(values: Iterable[Any] | None) -> set[str]:
    return {
        clean
        for clean in (_digits(value) for value in (values or []))
        if clean
    }


def compute_inventory_readiness(
    *,
    scope: str,
    session_ready: bool,
    business_inventory_ready: bool,
    business_ids: Iterable[Any] | None,
    rk_ready_by_business: dict[str, bool] | None,
    pages_inventory_ready: bool,
    requested_business_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Compute Sync completeness without confusing emptiness with failure.

    PROFILE_FULL:
      - Business inventory must itself be confirmed, including confirmed-empty.
      - Every discovered Business must have a confirmed RK inventory.
      - A profile with zero Businesses is valid once Business inventory is ready.
      - Page inventory must be confirmed, including confirmed-empty.

    BUSINESS_SCOPED:
      - Every requested Business must be confirmed by its RK inventory.
      - Page inventory remains profile-level and must also be confirmed because
        Add-BM consumes Pages from the same profile snapshot.
    """
    clean_scope = (
        BUSINESS_SCOPED
        if str(scope or "").strip() == BUSINESS_SCOPED
        else PROFILE_FULL
    )
    discovered = normalize_ids(business_ids)
    requested = normalize_ids(requested_business_ids)
    ready_map = {
        _digits(key): bool(value)
        for key, value in (rk_ready_by_business or {}).items()
        if _digits(key)
    }

    targets = requested if clean_scope == BUSINESS_SCOPED else discovered
    all_rk_ready = bool(business_inventory_ready) and all(
        ready_map.get(business_id, False)
        for business_id in targets
    )

    # In profile-full mode an authoritative empty Business collection is
    # complete and therefore has a vacuously complete RK dimension.
    if (
        clean_scope == PROFILE_FULL
        and business_inventory_ready
        and not discovered
    ):
        all_rk_ready = True

    # In scoped mode, "no target" is never a successful scoped inventory.
    if clean_scope == BUSINESS_SCOPED and not requested:
        all_rk_ready = False

    complete = bool(
        session_ready
        and business_inventory_ready
        and all_rk_ready
        and pages_inventory_ready
    )

    return {
        "scope": clean_scope,
        "session_ready": bool(session_ready),
        "business_inventory_ready": bool(business_inventory_ready),
        "businesses_confirmed_empty": bool(
            business_inventory_ready and not discovered
        ),
        "rk_inventory_ready": bool(all_rk_ready),
        "pages_inventory_ready": bool(pages_inventory_ready),
        "sync_complete": complete,
        "business_ids": sorted(discovered),
        "requested_business_ids": sorted(requested),
        "rk_unconfirmed_business_ids": sorted(
            business_id
            for business_id in targets
            if not ready_map.get(business_id, False)
        ),
    }


def page_business_ownership(page: dict[str, Any] | None) -> str:
    row = page if isinstance(page, dict) else {}
    explicit = str(row.get("business_ownership") or "").strip()
    if explicit in {
        "owned_by_business",
        "unowned_confirmed",
        "unknown",
    }:
        return explicit
    return "owned_by_business" if _digits(row.get("business_id")) else "unknown"


def page_auto_selectable(page: dict[str, Any] | None) -> bool:
    row = page if isinstance(page, dict) else {}
    restriction = row.get("advertising_restriction_info")
    restricted = bool(
        isinstance(restriction, dict)
        and restriction.get("is_restricted") is True
    )
    return (
        page_business_ownership(row) == "unowned_confirmed"
        and not restricted
    )


__all__ = [
    "PROFILE_FULL",
    "BUSINESS_SCOPED",
    "compute_inventory_readiness",
    "normalize_ids",
    "page_business_ownership",
    "page_auto_selectable",
]
