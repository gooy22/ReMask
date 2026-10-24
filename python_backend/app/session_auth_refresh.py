"""Reload saved profile authentication without changing Facebook identity."""
from __future__ import annotations

from typing import Any


async def refresh_saved_auth_context(resolver: Any, context: Any) -> bool:
    profile = str(getattr(context, "profile_id", "") or "").strip()
    old = getattr(context, "cookies", {}) or {}
    if not profile or not isinstance(old, dict):
        return False
    fresh = await resolver.resolve(profile)
    cookies = getattr(fresh, "cookies", {}) or {}
    if (str(getattr(fresh, "profile_id", "") or "") != profile
            or not isinstance(cookies, dict)
            or not str(cookies.get("c_user") or "").strip()
            or str(cookies.get("c_user") or "") != str(old.get("c_user") or "")
            or not str(cookies.get("xs") or "").strip()):
        return False
    # A login redirect cannot be repaired by repeating requests with the same
    # expired credentials. Use a newly saved session when one is available.
    if cookies == old:
        return False
    context.cookies = dict(cookies)
    context.proxy = getattr(fresh, "proxy", None)
    context.user_agent = str(getattr(fresh, "user_agent", "") or "")
    return True
