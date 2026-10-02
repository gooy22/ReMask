"""Read-only payment inspection through the existing profile-bound browser.

No card entry, payment submission, Graph calls or raw payment data persistence.
An observed masked method proves linkage only, never charge/verification success.
"""
from __future__ import annotations

import asyncio
import logging
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .facebook_business_browser import BrowserBusinessError


def account_id(value: str) -> str:
    clean = re.sub(r"^act_", "", str(value or "").strip())
    if not re.fullmatch(r"\d{5,30}", clean):
        raise ValueError("A numeric ad account ID is required")
    return clean


def _billing_url_matches(url: str, target: str, *, require_scope: bool) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "business.facebook.com", "www.facebook.com", "adsmanager.facebook.com",
    }:
        return False
    path = parsed.path.casefold()
    if not re.search(r"/(?:billing(?:_hub)?|payments?)(?:/|$)", path):
        return False
    if re.search(r"/(?:submit|checkout|pay|process|add|edit|remove|delete|confirm|verify|attach)(?:/|$)", path):
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    scopes = [query[key] for key in ("act", "asset_id", "ad_account_id") if key in query]
    return (bool(scopes) or not require_scope) and all(values == [target] for values in scopes)


def payment_summary(target: str, url: str, text: str) -> dict[str, Any]:
    """Interpret visible billing evidence, returning only safe masked fields."""
    target = account_id(target)
    scoped = _billing_url_matches(url, target, require_scope=True)
    # A requested URL alone does not prove which account Meta actually rendered.
    visible_account = bool(re.search(rf"(?<!\d){re.escape(target)}(?!\d)", text))
    billing = bool(re.search(
        r"payment methods|payment settings|billing.{0,12}payments|"
        r"способ[ыа] оплаты|настройки платеж|платіжн[іи] метод|способи оплати",
        text, re.I,
    ))
    exact = scoped and visible_account and billing
    methods = []
    if exact:
        pattern = re.compile(
            r"\b(Visa|Mastercard|Master\s+Card|American Express|Amex|Discover)\b"
            r"[^\n\d]{0,35}(?:[*•·●xX]{2,}|ending\s+in|ends\s+in|"
            r"заканчивается\s+на|останні\s+цифри)\s*(\d{4})(?!\d)", re.I,
        )
        seen = set()
        for match in pattern.finditer(text):
            brand = re.sub(r"\s+", " ", match[1]).strip()
            key = (brand.casefold(), match[2])
            if key in seen:
                continue
            seen.add(key)
            methods.append({"type": brand, "last4": match[2], "linkage_status": "OBSERVED"})
            if len(methods) >= 20:
                break
    empty = exact and bool(re.search(
        r"no payment methods|haven.t added (?:any )?payment|"
        r"нет (?:добавленных )?способов оплаты|немає (?:доданих )?способів оплати",
        text, re.I,
    ))
    status = "LINKED" if methods else "NONE" if empty else "UNVERIFIED"
    return {
        "account_id": target,
        "funding_verified": False,
        "account_scope_verified": exact,
        "verification_status": status,
        "payment_methods": methods,
        "card_linked": True if methods else False if empty else None,
        "source": "private_facebook_billing_ui",
        "checked_live": True,
    }


def saved_payment_business(profile_id: str, target: str, *, path: Path = Path('/var/lib/remask/workspace-live-meta-snapshots.json')) -> str:
    """Use only an exact saved profile/RK pair to choose the lighter Settings UI."""
    try:
        snapshot = json.loads(path.read_text()).get(str(profile_id), {})
        businesses = {
            str(row.get('business_id') or '')
            for row in snapshot.get('ad_accounts', []) if isinstance(row,dict)
            and str(row.get('profile',profile_id)) == str(profile_id)
            and re.sub(r'^act_', '', str(row.get('id') or row.get('account_id') or '')) == target
            and re.fullmatch(r'\d{5,30}', str(row.get('business_id') or ''))
        }
        return next(iter(businesses)) if len(businesses) == 1 else ''
    except (OSError, ValueError, AttributeError, TypeError):
        return ''


async def inspect_payment_methods(browser: Any, target: str, *, business_id: str = '') -> dict[str, Any]:
    """Discover Billing from the authenticated Ads Manager UI; never guess it."""
    target = account_id(target)
    start_url = browser.ADS_MANAGER_URL + '?act=' + target
    if re.fullmatch(r'\d{5,30}', business_id):
        start_url = browser.SETTINGS_AD_ACCOUNTS_URLS[0].format(business_id=business_id)
    await browser._goto(
        start_url,
        timeout_ms=25000, settle_ms=700, attempts=1,
    )
    page = browser.page
    if page is None:
        raise BrowserBusinessError("BROWSER_NOT_READY", "Payment browser is not open.", retryable=False)

    # Read a rendered navigation link, validate its destination, then navigate.
    # We do not consume internal Relay stores or capture payment network payloads.
    read_links = """() => Array.from(document.querySelectorAll('a[href]'))
      .filter(a => a.getClientRects().length)
      .map(a => ({href:a.href,label:(a.innerText||a.getAttribute('aria-label')||'').trim()}))
      .filter(a => /billing|payment|платеж|платіж|оплат/i.test(a.label)).slice(0,20)"""
    # DOMContentLoaded precedes Meta's SPA navigation hydration. Wait for an
    # actual rendered navigation control, rather than treating a loading shell
    # as a permanently missing Billing menu.
    navigation_ready = """() => Array.from(document.querySelectorAll('a[href],button,[role="button"],[role="link"]'))
      .some(a => a.getClientRects().length && /billing|payment|платеж|платіж|оплат|^all tools$|^все инструменты$|^усі інструменти$/i.test(
        (a.innerText||a.getAttribute('aria-label')||'').trim()))"""
    try:
        await page.wait_for_function(navigation_ready, timeout=5000)
    except Exception:
        pass
    links = await page.evaluate(read_links)
    if not links:
        # Meta may keep Billing inside the rendered All tools drawer.
        # Open an exact observed menu control once; no guessed Billing URL.
        menu_name = re.compile(r"^(All tools|Все инструменты|Усі інструменти)$", re.I)
        for role in ("button", "link"):
            menu = page.get_by_role(role, name=menu_name)
            if await menu.count() == 1 and await menu.is_visible():
                await menu.click(timeout=4000)
                await browser._assert_authenticated()
                try:
                    await page.wait_for_function("""() => Array.from(document.querySelectorAll('a[href]'))
                      .some(a => a.getClientRects().length && /billing|payment|платеж|платіж|оплат/i.test(
                        (a.innerText||a.getAttribute('aria-label')||'').trim()))""", timeout=4000)
                except Exception:
                    pass
                links = await page.evaluate(read_links)
                break
    billing_url = ""
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        candidate = str(link.get("href") or "")
        # Never follow a payment submission, login or unrelated account link.
        if not _billing_url_matches(candidate, target, require_scope=False):
            continue
        billing_url = candidate
        break
    if not billing_url:
        parsed = urlsplit(str(page.url))
        diagnostic = {'stage':'payment_navigation_missing', 'host':parsed.hostname,
                      'path':parsed.path, 'target_account_id':target,
                      'rendered_billing_links':len(links) if isinstance(links,list) else 0}
        logging.getLogger('remask.payment_inspection').info('payment navigation missing profile=%s diagnostic=%s',browser.profile_id,diagnostic)
        raise BrowserBusinessError(
            "PAYMENT_UI_UNAVAILABLE",
            "Ads Manager did not expose a rendered Billing / Payments navigation link.",
            retryable=False, diagnostic=diagnostic,
        )
    await browser._goto(billing_url, timeout_ms=25000, settle_ms=700, attempts=1)
    # Body text remains in memory only. Return explicitly whitelisted masked data.
    text = await page.locator("body").inner_text(timeout=5000)
    result = payment_summary(target, str(page.url), text)
    result["profile_id"] = browser.profile_id
    return result


async def inspect_profile_payment_methods(resolver: Any, profile_id: str, target: str) -> dict[str, Any]:
    from .session import ProfileSession

    target = account_id(target)
    context = await resolver.resolve(profile_id)
    async with ProfileSession(context) as session:
        browser = await session.facebook_business_browser()
        # Existing browser profile lock/global semaphore limits concurrent load.
        try:
            return await asyncio.wait_for(inspect_payment_methods(browser, target,
                business_id=saved_payment_business(profile_id,target)), timeout=65)
        except BrowserBusinessError:
            raise
        except Exception as exc:
            if 'Target crashed' not in str(exc):
                raise
            from .facebook_business_browser import _cgroup_memory_snapshot_mb
            logging.getLogger('remask.payment_inspection').warning('payment browser crashed profile=%s account=%s memory=%s',profile_id,target,_cgroup_memory_snapshot_mb())
            raise BrowserBusinessError('PAYMENT_BROWSER_CRASHED',
                'Meta payment browser crashed before payment methods were read.',retryable=False) from exc
