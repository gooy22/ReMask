"""Reload saved profile authentication without changing Facebook identity."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar


_READ_AUTH_CODES = frozenset({
    "BUSINESS_LOGIN_GATE", "SESSION_EXPIRED",
    "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE", "PRIVATE_ADS_DRAFT_SESSION_EXPIRED",
})
_T = TypeVar("_T")
log = logging.getLogger(__name__)


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
    # First accept a newer saved session for this same actor. If none exists,
    # try the encrypted previous snapshot once, but only after its own
    # read-only Business Home request confirms the same Meta actor.
    if str(cookies.get("xs") or "") == str(old.get("xs") or ""):
        restore_previous = getattr(resolver, "restore_previous_profile_session_after_gate", None)
        if (not callable(restore_previous)
                or getattr(fresh, "_previous_profile_restore_attempted", False)):
            return False
        if not await restore_previous(fresh):
            return False
        cookies = getattr(fresh, "cookies", {}) or {}
        if (str(getattr(fresh, "profile_id", "") or "") != profile
                or not isinstance(cookies, dict)
                or str(cookies.get("c_user") or "") != str(old.get("c_user") or "")
                or not str(cookies.get("xs") or "")
                or str(cookies.get("xs") or "") == str(old.get("xs") or "")):
            return False
    context.cookies = dict(cookies)
    if hasattr(context, '_loaded_cookies'):
        # These credentials were just read from the durable store. Future
        # HTTP clients must compare against this saved version, not the one
        # that caused the previous authentication failure.
        context._loaded_cookies = dict(cookies)
    context.proxy = getattr(fresh, "proxy", None)
    context.user_agent = str(getattr(fresh, "user_agent", "") or "")
    return True


async def read_with_saved_auth_refresh(
    resolver: Any, context: Any, read_once: Callable[[], Awaitable[_T]],
) -> _T:
    """Retry a read once after loading newer authentication for the same actor.

    The callback must open and close its own ProfileSession on each attempt.
    Advertising mutations must never use this wrapper.
    """
    try:
        return await read_once()
    except Exception as error:
        code = getattr(error, "code", "")
        if not isinstance(code, str) or code not in _READ_AUTH_CODES:
            raise
        try:
            changed = await asyncio.wait_for(
                refresh_saved_auth_context(resolver, context), timeout=16.0)
        except Exception as refresh_error:
            log.info("private read auth refresh profile=%s code=%s error_type=%s",
                getattr(context, "profile_id", ""), code, type(refresh_error).__name__)
            raise error from None
        log.info("private read auth refresh profile=%s code=%s changed=%s",
            getattr(context, "profile_id", ""), code, changed)
        if not changed:
            raise
    # Keep the second attempt outside the handler: a second gate cannot loop.
    return await read_once()

