"""Read-only, exact RK Page permission evidence from Meta's live Relay traffic."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .facebook_business_browser import (
    FacebookBusinessBrowser, BrowserBusinessError, _request_graphql_meta,
    _decode_graphql_text, _ads_manager_scope_account_from_request,
)
from .facebook_page_discovery import _iter_connection_rows, _normalize_page
from .payment_inspection import account_id, saved_payment_business


def request_accounts(variables: Any) -> set[str]:
    """Only explicit ad account variables; actor, Page and business IDs do not count."""
    out: set[str] = set()
    if isinstance(variables, dict):
        for key, value in variables.items():
            if re.sub(r'[^a-z]', '', str(key).lower()) in {'adaccountid', 'accountid'}:
                clean = str(value or '').removeprefix('act_')
                if re.fullmatch(r'\d{5,30}', clean): out.add(clean)
            elif isinstance(value, (dict, list)):
                out.update(request_accounts(value))
    elif isinstance(variables, list):
        for value in variables: out.update(request_accounts(value))
    return out


def promotable_pages(payload: Any, meta: dict, target: str) -> list[dict]:
    """A managed Page or actor-wide Page list is never RK permission evidence."""
    friendly = str(meta.get('friendly_name') or '').lower()
    if any(word in friendly for word in ('mutation', 'create', 'update', 'delete')):
        return []
    requested = request_accounts(meta.get('variables'))
    if requested and requested != {target}: return []
    output: dict[str, dict] = {}

    def walk(value: Any, scoped: bool = False) -> None:
        if isinstance(value, list):
            for child in value: walk(child, scoped)
        elif isinstance(value, dict):
            if value.get('errors') or value.get('error'): return
            typename = re.sub(r'[^a-z]', '', str(value.get('__typename') or '').lower())
            if typename in {'adaccount', 'adsaccount'}:
                node_id = str(value.get('account_id') or value.get('id') or '').removeprefix('act_')
                scoped = node_id == target
                if not scoped: return
            for key, child in value.items():
                compact = re.sub(r'[^a-z]', '', str(key).lower())
                if compact in {'promotablepages', 'pagescanadvertise'} and (scoped or requested == {target}):
                    for raw in _iter_connection_rows(child):
                        row = raw.get('page') if isinstance(raw.get('page'), dict) else raw
                        page = _normalize_page(row)
                        if not page: continue
                        kind = str(row.get('__typename') or '').lower()
                        if kind and kind != 'page': continue
                        restriction = row.get('advertising_restriction_info')
                        restriction = restriction if isinstance(restriction, dict) else {}
                        if restriction.get('is_restricted') is True: continue
                        if row.get('can_advertise') is False or row.get('is_promotable') is False: continue
                        output[page['id']] = {**page, 'account_id':target,
                            'ad_account_page_access_verified':True,
                            'source':'scoped_private_promotable_pages',
                            'operation':str(meta.get('friendly_name') or '')[:180]}
                else:
                    walk(child, scoped)
    walk(payload)
    return list(output.values())


def response_shape(payload: Any) -> list[str]:
    """Field names only, for diagnosing unknown schemas without session data."""
    keys: set[str] = set()
    def walk(value: Any, depth: int = 0):
        if depth > 7 or len(keys) > 100: return
        if isinstance(value, dict):
            for key, child in value.items():
                keys.add(str(key)[:80]); walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:3]: walk(child, depth + 1)
    walk(payload)
    return sorted(keys)[:100]


async def inspect_browser_pages(browser: Any, target: str, business: str, *, timeout: float = 22) -> dict:
    page = browser.page
    pages: dict[str, dict] = {}
    observed: set[str] = set()
    diagnostics: list[dict] = []
    tasks: set[asyncio.Task] = set()
    deadline = time.monotonic() + timeout

    def on_request(request):
        meta = _request_graphql_meta(request)
        selected = _ads_manager_scope_account_from_request(meta, business_id=business)
        if selected: observed.add(selected)

    async def inspect(response):
        try:
            response_url = urlsplit(str(response.url))
            if response_url.hostname not in {'www.facebook.com','business.facebook.com','adsmanager.facebook.com','graph.facebook.com'} or 'graphql' not in response_url.path.lower(): return
            meta = _request_graphql_meta(response.request)
            friendly = str(meta.get('friendly_name') or '').lower()
            if 'page' not in friendly or any(w in friendly for w in ('mutation','create','update','delete')): return
            payload = _decode_graphql_text(await response.text())
            found = promotable_pages(payload, meta, target)
            pages.update({p['id']:p for p in found})
            diagnostics.append({'operation':str(meta.get('friendly_name') or '')[:180],
                'variable_keys':sorted(str(k) for k in (meta.get('variables') or {}))[:40],
                'account_ids':sorted(request_accounts(meta.get('variables'))),
                'response_fields':response_shape(payload), 'verified_page_ids':[p['id'] for p in found]})
            del diagnostics[:-12]
        except Exception:
            return

    def on_response(response):
        task = asyncio.create_task(inspect(response)); tasks.add(task)
        task.add_done_callback(tasks.discard)

    page.on('request', on_request); page.on('response', on_response)
    try:
        await browser._goto('https://adsmanager.facebook.com/adsmanager/manage/ads?act=' + target +
            '&business_id=' + business, timeout_ms=12000, wait_until='commit', settle_ms=0, attempts=1)
        while time.monotonic() < deadline:
            await browser._assert_authenticated()
            if pages and observed == {target}: break
            await page.wait_for_timeout(250)
        parsed = urlsplit(str(page.url)); query = parse_qs(parsed.query)
        exact = parsed.hostname in {'adsmanager.facebook.com','business.facebook.com'} and observed == {target} and query.get('act') == [target] and query.get('business_id') == [business]
        verified = list(pages.values()) if exact else []
        return {'account_id':target, 'business_id':business, 'checked_live':True,
            'account_scope_verified':exact,
            'status':'VERIFIED' if verified else 'UNVERIFIED',
            'ad_account_page_access_verified':bool(verified), 'data':verified,
            'checked_at':int(time.time()),
            'diagnostic':{'observed_account_ids':sorted(observed), 'queries':diagnostics,
                'code':'' if verified else 'PAGE_ACCESS_EVIDENCE_MISSING'}}
    finally:
        page.remove_listener('request', on_request); page.remove_listener('response', on_response)
        for task in list(tasks): task.cancel()
        if tasks: await asyncio.gather(*list(tasks), return_exceptions=True)


async def inspect_profile_pages(resolver: Any, profile: str, target: str) -> dict:
    target = account_id(target)
    business = saved_payment_business(profile, target)
    if not business: raise ValueError('RK is absent from this profile inventory')
    context = await asyncio.wait_for(resolver.resolve(profile), timeout=12)
    async def probe():
        async with FacebookBusinessBrowser(context, v8_old_space_mb=256) as browser:
            return await inspect_browser_pages(browser, target, business)
    result = await asyncio.wait_for(probe(), timeout=48)
    return {'profile_id':profile, **result}
