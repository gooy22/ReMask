from __future__ import annotations

import asyncio
import os
from functools import wraps

from .provisioning.models import ProvisioningError


PROFILE_RESTRICTION_HOLD_ENV = "REMASK_RESTRICTION_HOLD_PROFILE_IDS"
_PROFILE_ACTIVITY_LOCKS: dict[str, asyncio.Lock] = {}


def profile_activity_lock(profile_id: object) -> asyncio.Lock:
    """Return the shared in-process lock for one Meta profile."""
    key = str(profile_id or "").strip()
    if not key:
        raise ValueError("profile_id is required for Meta activity")
    lock = _PROFILE_ACTIVITY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PROFILE_ACTIVITY_LOCKS[key] = lock
    return lock


def serialize_profile_activity(function):
    """Serialize provisioning steps that can touch one profile."""
    @wraps(function)
    async def wrapped(self, *args, **kwargs):
        profile_id = kwargs.get("profile_id")
        if not profile_id:
            return await function(self, *args, **kwargs)
        async with profile_activity_lock(profile_id):
            return await function(self, *args, **kwargs)
    return wrapped



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
            "was confirmed in Ads Manager. No Meta request was sent. "
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
