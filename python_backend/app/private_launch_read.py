"""Read advertising readiness using the profile HTTP session only.

Billing uses the deployed static query catalog. Page eligibility must come from
an exact-account promotable-pages response, never a managed Page list. The fixed
Ads Manager document is a read-only bootstrap source; no JS bundles are scanned
and no draft, campaign, payment verification or bank request is submitted here.
"""
from __future__ import annotations

import time
import asyncio
from urllib.parse import parse_qs, urlsplit

from .private_inventory import _auth_gate, _json_payloads
from .page_access_inspection import promotable_pages, response_shape
from .provisioning.models import ProvisioningError


def _final_tree(value):
    if isinstance(value, dict):
        if (value.get("errors") or value.get("error") or value.get("hasNext") is True
                or (isinstance(value.get("extensions"), dict)
                    and value["extensions"].get("is_final") is False)):
            return None
        return {key: _final_tree(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_final_tree(child) for child in value]
    return value


async def inspect_pages(web, *, account: str, business_id: str) -> dict:
    # Import validation shared with the static billing catalog.
    from .static_payment_read import _identity
    account, business_id = _identity(account), _identity(business_id)
    web.private_only = True
    url = ("https://adsmanager.facebook.com/adsmanager/manage/campaigns"
           f"?act={account}&business_id={business_id}")
    status, body, final_url = await web.fetch_text(
        url, max_bytes=5_000_000, document_navigation=True)
    gate = _auth_gate(final_url, body)
    if gate:
        raise ProvisioningError(gate, "Meta requires restoration of this profile's HTTP session.", retryable=True)
    final = urlsplit(final_url)
    query = parse_qs(final.query)
    exact = (200 <= status < 300 and final.scheme == "https"
             and final.hostname in {"adsmanager.facebook.com", "business.facebook.com"}
             and final.path == "/adsmanager/manage/campaigns"
             and query.get("act") == [account]
             and query.get("business_id") == [business_id])
    pages, fields = {}, set()
    if exact:
        # No request-side variables are supplied as evidence. Every accepted
        # response must identify this RK inside its own AdAccount subtree.
        for payload in _json_payloads(body):
            for page in promotable_pages(_final_tree(payload), {"variables": {}}, account):
                pages[page["id"]] = page
            fields.update(response_shape(payload))
    return {"account_id": account, "business_id": business_id,
            "checked_live": exact, "account_scope_verified": exact,
            "ad_account_page_access_verified": bool(pages),
            "status": "VERIFIED" if pages else "UNVERIFIED", "data": list(pages.values()),
            "checked_at": int(time.time()), "browser_started": False,
            "source": "private_http_ads_manager_promotable_pages",
            "diagnostic": {"code": "PAGE_ACCESS_HTTP_VERIFIED" if pages else "PAGE_ACCESS_HTTP_UNCONFIRMED",
                           "http_status": status, "response_fields": sorted(fields)[:60]}}


async def inspect_profile_pages_http(resolver, profile, target, *, state=None, read_catalog=None):
    from .payment_inspection import account_id, resolve_payment_asset
    from .page_access_inspection import unverified_result
    from .session import ProfileSession
    from .private_auth import private_auth_error
    from fb_worker import AuthenticationError, RemoteRequestError

    target = account_id(target)
    asset = await resolve_payment_asset(profile, target, state)
    if not asset or not asset.get("business_id"):
        raise ValueError("RK is absent from this profile inventory")
    context = await resolver.resolve(profile)
    if asset["business_id"] == str(context.cookies.get("c_user") or ""):
        raise ValueError("PERSONAL_AD_ACCOUNT_EXCLUDED")
    try:
        async with ProfileSession(context) as session:
            web = await session.facebook_web()
            operation = (read_catalog.pages(web, {"profile_id": profile, "ad_account_id": target,
                         "business_id": asset["business_id"], "query": "", "limit": 500,
                         "actor_id":str(context.cookies.get("c_user") or "")})
                         if read_catalog and "pages" in read_catalog.rows else
                         inspect_pages(web, account=target, business_id=asset["business_id"]))
            result = await asyncio.wait_for(operation, timeout=65)
            if result.get("source") == "static_private_http_query":
                result.update({"status": "VERIFIED" if result["data"] else "UNVERIFIED", "browser_started": False,
                               "diagnostic": {"code": "PAGE_ACCESS_HTTP_VERIFIED" if result["data"] else "PAGE_ACCESS_HTTP_UNCONFIRMED"}})
        return {"profile_id": profile, **result}
    except AuthenticationError as exc:
        code = private_auth_error(exc).code
    except ProvisioningError as exc:
        code = exc.code
    except (RemoteRequestError, asyncio.TimeoutError):
        code = "PAGE_ACCESS_HTTP_UNAVAILABLE"
    return {**unverified_result(profile, target, code), "browser_started": False,
            "source": "private_http_ads_manager_promotable_pages"}
