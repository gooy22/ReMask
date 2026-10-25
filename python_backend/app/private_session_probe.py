"""One isolated Ads Manager document GET; imported cookies are never saved."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlsplit

from .private_ads_document import _auth_gate
from .private_ads_http import _bootstrap_evidence, context_from_document
from .provisioning.models import ProvisioningError


def _reject(code):
    return ProvisioningError("PRIVATE_SESSION_PROBE_" + code,
        "Session comparison was not performed; saved profile data is unchanged.", retryable=False)


async def probe_document(context, payload, *, web_factory=None):
    if not isinstance(payload, dict) or set(payload) != {"cookies", "business_id", "ad_account_id"}:
        raise _reject("INVALID_INPUT")
    business, account = payload["business_id"], payload["ad_account_id"]
    if any(not isinstance(value, str) or not re.fullmatch(r"\d{5,30}", value)
           for value in (business, account)):
        raise _reject("INVALID_TARGET")
    cookies = payload["cookies"]
    if (not isinstance(cookies, dict) or not 2 <= len(cookies) <= 64
            or not cookies.get("xs") or sum(len(str(v)) for v in cookies.values()) > 65536):
        raise _reject("INVALID_COOKIES")
    for name, value in cookies.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,100}", name)
                or not isinstance(value, str) or not 1 <= len(value) <= 8192
                or any(ord(ch) < 33 or ord(ch) > 126 or ch in '\\";,' for ch in value)):
            raise _reject("INVALID_COOKIES")
    actor = str(context.cookies.get("c_user") or "")
    if not actor.isdigit() or cookies.get("c_user") != actor:
        raise _reject("ACTOR_MISMATCH")
    if not context.proxy or not context.user_agent:
        raise _reject("PROFILE_ROUTE_UNAVAILABLE")
    # Durable IDs are navigation targets only. This does not establish access.
    if not any(isinstance(row, dict)
            and str(row.get("ad_account_id") or row.get("account_id") or row.get("id") or "").removeprefix("act_") == account
            and str(row.get("business_id") or "") == business
            for row in (context.ad_accounts or [])):
        raise _reject("ACCOUNT_CONTEXT_UNAVAILABLE")
    from fb_worker import FacebookWebSession, WebProfile
    profile = WebProfile(str(context.profile_id), dict(cookies), context.proxy, context.user_agent)
    web = (web_factory or FacebookWebSession)(profile)
    web.private_only = True
    saved = WebProfile(str(context.profile_id), dict(context.cookies), context.proxy, context.user_agent)
    result = {"profile_id": str(context.profile_id), "transport": "private_http_h2",
        "persisted": False, "objects_created": False, "browser_started": False,
        "saved_auth_cookie_fingerprint": _bootstrap_evidence("", "", saved, actor)["auth_cookie_fingerprint"]}
    url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
           f"?act={account}&business_id={business}")
    try:
        async with asyncio.timeout(35):
            status, body, final_url = await web.fetch_text(url, max_bytes=5_000_001, document_navigation=True)
        evidence = _bootstrap_evidence(final_url, body, profile, actor)
        final = urlsplit(final_url)
        scope = (status == 200 and len(body.encode()) <= 5_000_000 and final.scheme == "https"
            and final.hostname in {"adsmanager.facebook.com", "business.facebook.com"}
            and final.path == "/adsmanager/manage/campaigns"
            and parse_qs(final.query).get("act") == [account]
            and parse_qs(final.query).get("business_id") == [business])
        confirmed, context_code = False, "not_checked"
        if scope and not _auth_gate(final_url, body):
            try:
                context_from_document(body, account_id=account, business_id=business, actor_id=actor)
                confirmed, context_code = True, "confirmed"
            except ProvisioningError as error:
                context_code = error.code
        return {**result, "response_received": True, "http_status": status,
            "legacy_gate": _auth_gate(final_url, body) or "none",
            "evidence": evidence, "document_scope_matches": scope,
            "bootstrap_context_confirmed": confirmed, "bootstrap_context_code": context_code}
    except Exception as error:
        # No exception text/response fragments and no automatic network retry.
        return {**result, "response_received": False,
            "error_code": "PRIVATE_SESSION_PROBE_READ_UNAVAILABLE",
            "error_type": type(error).__name__ if re.fullmatch(r"[A-Za-z_]{1,60}", type(error).__name__) else "Exception"}
    finally:
        await web.close()
