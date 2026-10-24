"""Persist and reload Meta-issued profile-session updates safely."""
from __future__ import annotations

from typing import Any


async def refresh_saved_auth_context(
    resolver: Any,
    context: Any,
    *,
    expected_xs: str | None = None,
    expected_actor_id: str | None = None,
) -> bool:
    profile = str(getattr(context, "profile_id", "") or "").strip()
    old = getattr(context, "cookies", {}) or {}
    if not profile or not isinstance(old, dict):
        return False
    actor_id = str(expected_actor_id or old.get("c_user") or "").strip()
    previous_xs = (
        str(old.get("xs") or "")
        if expected_xs is None
        else str(expected_xs or "")
    ).strip()
    if (
        not actor_id
        or not previous_xs
        or str(old.get("c_user") or "") != actor_id
    ):
        return False

    fresh = await resolver.resolve(profile)
    cookies = getattr(fresh, "cookies", {}) or {}
    if (
        str(getattr(fresh, "profile_id", "") or "") != profile
        or not isinstance(cookies, dict)
        or str(cookies.get("c_user") or "") != actor_id
        or not str(cookies.get("xs") or "").strip()
    ):
        return False

    # Compare against the session at the start of the failed operation. The
    # MetaSession close path may already have copied Meta's Set-Cookie result
    # into context.cookies before the saved profile is reloaded.
    if str(cookies.get("xs") or "") == previous_xs:
        return False

    context.cookies = dict(cookies)
    # MetaSession.close() uses this baseline for its cookie compare-and-swap.
    # Move it with the session or later Meta-issued updates will be discarded.
    context._loaded_cookies = dict(cookies)
    fresh_persistor = getattr(fresh, "_cookie_persistor", None)
    if callable(fresh_persistor):
        context._cookie_persistor = fresh_persistor
    context.proxy = getattr(fresh, "proxy", None)
    context.user_agent = str(getattr(fresh, "user_agent", "") or "")
    return True


async def persist_and_refresh_saved_auth_context(
    session: Any,
    resolver: Any,
    context: Any,
) -> bool:
    """Flush this profile's HTTP/2 cookie jar, then reload only a changed session."""
    profile = str(getattr(context, "profile_id", "") or "").strip()
    cookies = getattr(context, "cookies", {}) or {}
    if (
        not profile
        or not isinstance(cookies, dict)
        or getattr(session, "context", None) is not context
    ):
        return False
    actor_id = str(cookies.get("c_user") or "").strip()
    expected_xs = str(cookies.get("xs") or "").strip()
    if not actor_id or not expected_xs:
        return False
    close = getattr(session, "close", None)
    if not callable(close):
        return False

    await close()
    current = getattr(context, "cookies", {}) or {}
    if (
        str(getattr(context, "profile_id", "") or "").strip() != profile
        or not isinstance(current, dict)
        or str(current.get("c_user") or "") != actor_id
    ):
        return False
    return await refresh_saved_auth_context(
        resolver,
        context,
        expected_xs=expected_xs,
        expected_actor_id=actor_id,
    )
