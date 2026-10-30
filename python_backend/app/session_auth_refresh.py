"""Reload saved profile authentication without changing Facebook identity."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar


_READ_AUTH_CODES = frozenset({
    "BUSINESS_LOGIN_GATE", "SESSION_EXPIRED",
    "PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE", "PRIVATE_ADS_DRAFT_SESSION_EXPIRED",
})
AUTH_COOKIE_KEYS = ("fr", "sb", "datr", "ps_l", "ps_n")
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

    replacement = dict(cookies)
    old_xs = str(old.get("xs") or "")
    if str(replacement.get("xs") or "") == old_xs and not auth_cookie_variant(old, replacement):
        raw_candidates = getattr(fresh, "recovery_cookie_candidates", None)
        if not isinstance(raw_candidates, list):
            legacy = getattr(fresh, "recovery_cookies", None)
            raw_candidates = [legacy] if isinstance(legacy, dict) else []
        replacement = {}
        for raw in raw_candidates[:8]:
            if not isinstance(raw, dict) or not raw or len(raw) > 500:
                continue
            if any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in raw.items()):
                continue
            candidate = dict(raw)
            if (str(candidate.get("c_user") or "") != str(old.get("c_user") or "")
                    or not str(candidate.get("xs") or "").strip()):
                continue
            candidate_xs = str(candidate.get("xs") or "")
            if (candidate_xs != old_xs
                    or auth_cookie_variant(old, candidate)):
                replacement = candidate
                break
        if not replacement:
            return False

    context.cookies = replacement
    if hasattr(context, "_loaded_cookies"):
        # These credentials were just read from the durable store. Future
        # HTTP clients must compare against this saved version, not the one
        # that caused the previous authentication failure.
        context._loaded_cookies = dict(replacement)
    context.proxy = getattr(fresh, "proxy", None)
    context.user_agent = str(getattr(fresh, "user_agent", "") or "")
    return True


def auth_cookie_variant(current: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """Allow a same-xs snapshot only when a Meta authentication cookie changed."""
    return any(
        str(current.get(name) or "") != str(candidate.get(name) or "")
        for name in AUTH_COOKIE_KEYS
    )

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
                refresh_saved_auth_context(resolver, context), timeout=5.0)
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

