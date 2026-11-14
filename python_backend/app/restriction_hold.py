from __future__ import annotations

import os

from .provisioning.models import ProvisioningError


PROFILE_RESTRICTION_HOLD_ENV = "REMASK_RESTRICTION_HOLD_PROFILE_IDS"


def profile_restriction_hold(profile_id: object) -> dict[str, str] | None:
    """Return the operator's local safety hold for a profile, if configured."""
    profile = str(profile_id or "").strip()
    if not profile:
        return None

    held_profiles = {
        value.strip()
        for value in os.getenv(PROFILE_RESTRICTION_HOLD_ENV, "").split(",")
        if value.strip()
    }
    if profile not in held_profiles:
        return None

    return {
        "code": "META_RESTRICTION_HOLD",
        "status": "BLOCKED",
        "message": (
            f"ReMask stopped {profile} because an advertising-account restriction "
            "was confirmed in Ads Manager. No Meta write request was sent. "
            f"Remove this local hold only after Meta clears the restriction "
            f"({PROFILE_RESTRICTION_HOLD_ENV})."
        ),
    }


def enforce_profile_restriction_hold(profile_id: object, operation: str) -> None:
    hold = profile_restriction_hold(profile_id)
    if hold is None:
        return
    raise ProvisioningError(
        hold["code"],
        f"{hold['message']} Blocked operation: {operation}.",
        retryable=False,
    )
