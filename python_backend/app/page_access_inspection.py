"""Read-only, exact RK Page permission evidence from Meta's live Relay traffic."""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .facebook_business_browser import (
    FacebookBusinessBrowser, BrowserBusinessError, _request_graphql_meta,
    _decode_graphql_text, _ads_manager_scope_account_from_request,
    _cgroup_memory_snapshot_mb,
)
from .facebook_page_discovery import _iter_connection_rows, _normalize_page
from .payment_inspection import account_id, saved_payment_business


def advertiser_phone_status(text: str, exact_scope: bool) -> str:
    if not exact_scope: return 'UNKNOWN'
    text=' '.join(str(text or '').lower().split())
    if re.search(r'(?:before you (?:can )?(?:run|publish|start)|to run ads|to publish ads).{0,220}(?:verify|verified).{0,60}(?:phone|number)',text) or re.search(r'(?:прежде чем|перед).{0,60}(?:реклам|оголош).{0,160}(?:подтверд|підтверд).{0,60}(?:телефон|номер)',text):
        return 'REQUIRED'
    if re.search(r'phone number[\s:–—-]*(?:verified|confirmed)\b|phone verification[\s:–—-]*complete\b',text):
        return 'VERIFIED'
    return 'UNKNOWN'


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


def allowed_readonly_request(request: Any) -> bool:
    """The identity-form probe may send queries, never a Meta asset mutation."""
    meta = _request_graphql_meta(request)
    parsed = urlsplit(str(meta.get('url') or ''))
    host = parsed.hostname or ''
    if host != 'facebook.com' and not host.endswith('.facebook.com'): return True
    friendly = str(meta.get('friendly_name') or '').lower()
    if 'mutation' in friendly: return False
    query = parse_qs(parsed.query)
    try:
        raw = getattr(request,'post_data',None) or ''
    except Exception:
        return False
    body = parse_qs(raw,keep_blank_values=True) if isinstance(raw,str) else {}
    overrides = [str(v).upper() for v in [*query.get('method',[]),*body.get('method',[])]]
    if any(v not in {'GET','POST'} for v in overrides): return False
    method = str(getattr(request,'method','') or '').upper()
    if 'graphql' in str(meta.get('url') or '').lower():
        return 'query' in friendly
    # Ads Manager's own GET reads can be tunneled through POST. These are
    # observed browser requests, not a separate API/token client.
    if host == 'graph.facebook.com':
        batch = body.get('batch') or query.get('batch') or []
        if batch:
            try:
                rows = json.loads(batch[0]) if len(batch)==1 else None
            except (ValueError,TypeError):
                rows = None
            return bool(isinstance(rows,list) and rows and len(set(overrides)) <= 1
                and all(isinstance(row,dict) and str(row.get('method') or '').upper()=='GET'
                    and not any(v.upper()!='GET' for v in parse_qs(urlsplit(str(row.get('relative_url') or '')).query).get('method',[]))
                    for row in rows))
        return (method in {'GET','HEAD','OPTIONS'} and not overrides) or bool(overrides and set(overrides)=={'GET'})
    if method in {'GET','HEAD','OPTIONS'} and (not overrides or set(overrides)=={'GET'}): return True
    # This exact POST is Meta's lazy JavaScript route-definition loader;
    # blocking it leaves the objective dialog at "Loading Creation".
    return method == 'POST' and parsed.path == '/ajax/bulk-route-definitions/'


def promotable_pages(payload: Any, meta: dict, target: str) -> list[dict]:
    """A managed Page or actor-wide Page list is never RK permission evidence."""
    friendly = str(meta.get('friendly_name') or '').lower()
    if 'mutation' in friendly:
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


def unverified_result(profile: str, target: str, code: str, diagnostic: dict | None = None) -> dict:
    return {'profile_id':profile, 'account_id':target, 'checked_live':False,
        'account_scope_verified':False, 'status':'UNVERIFIED',
        'ad_account_page_access_verified':False, 'data':[], 'checked_at':int(time.time()),
        'diagnostic':{**(diagnostic or {}), 'code':code}}


async def inspect_browser_pages(browser: Any, target: str, business: str, *, timeout: float = 45, progress: dict | None = None) -> dict:
    page = browser.page
    pages: dict[str, dict] = {}
    observed: set[str] = set()
    diagnostics: list[dict] = []
    operations: list[dict] = []
    blocked: list[dict] = []
    editor: list[str] = []
    tasks: set[asyncio.Task] = set()
    deadline = time.monotonic() + timeout
    progress = progress if progress is not None else {}
    progress.update({'queries':diagnostics, 'operations':operations, 'blocked_writes':blocked,
        'editor_steps':editor, 'observed_account_ids':[], 'stage':'navigation'})

    async def readonly_route(route, request):
        if allowed_readonly_request(request):
            await route.fallback()
        else:
            meta = _request_graphql_meta(request)
            if len(blocked) < 20:
                parsed = urlsplit(str(request.url))
                body = parse_qs(getattr(request,'post_data',None) or '')
                batch = body.get('batch') or parse_qs(parsed.query).get('batch') or []
                try: rows = json.loads(batch[0]) if len(batch)==1 else []
                except (ValueError,TypeError): rows = []
                blocked.append({'method':str(request.method), 'path':parsed.path[:180],
                    'operation':str(meta.get('friendly_name') or '')[:180],
                    'method_overrides':body.get('method',[])[:3],
                    'batch_methods':[str(row.get('method') or '')[:12] for row in rows[:10] if isinstance(row,dict)] if isinstance(rows,list) else []})
            await route.abort()

    await page.route('**/*', readonly_route)

    def on_request(request):
        meta = _request_graphql_meta(request)
        friendly = str(meta.get('friendly_name') or '')[:180]
        if friendly and len(operations) < 20:
            variables = meta.get('variables') or {}
            operations.append({'operation':friendly, 'variable_keys':sorted(str(k) for k in variables)[:40],
                'account_ids':sorted(request_accounts(variables))})
        selected = _ads_manager_scope_account_from_request(meta, business_id=business)
        if selected:
            observed.add(selected)
            progress['observed_account_ids'] = sorted(observed)

    async def inspect(response):
        try:
            response_url = urlsplit(str(response.url))
            if response_url.hostname not in {'www.facebook.com','business.facebook.com','adsmanager.facebook.com','graph.facebook.com'} or 'graphql' not in str(response.url).lower(): return
            meta = _request_graphql_meta(response.request)
            friendly = str(meta.get('friendly_name') or '').lower()
            if 'mutation' in friendly or ('page' not in friendly and request_accounts(meta.get('variables')) != {target}): return
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
        await browser._goto('https://adsmanager.facebook.com/adsmanager/manage/campaigns?act=' + target +
            '&business_id=' + business, timeout_ms=12000, wait_until='commit', settle_ms=0, attempts=1)
        progress['stage'] = 'waiting_for_identity_form'
        opened = False
        while time.monotonic() < deadline:
            await browser._assert_authenticated()
            if pages and observed == {target}: break
            if not opened and observed == {target}:
                create = page.get_by_role('button',name='Create',exact=True)
                loading_dialog = page.locator('[data-surface*="ads_progress_dialog_modal"]')
                loading = await loading_dialog.count() and await loading_dialog.first.is_visible()
                if not loading and await create.count() == 1 and await create.is_visible():
                    opened = True
                    try:
                        await create.click(timeout=6000); editor.append('objective_dialog_opened')
                    except Exception:
                        editor.append('create_entry_unavailable')
            if opened and 'traffic_selected_locally' not in editor:
                traffic = page.get_by_role('radio',name='Traffic',exact=True)
                if await traffic.count() != 1:
                    traffic = page.get_by_text('Traffic',exact=True)
                if await traffic.count() == 1 and await traffic.is_visible():
                    try:
                        await traffic.click(timeout=4000); editor.append('traffic_selected_locally')
                    except Exception:
                        editor.append('objective_selection_unavailable'); break
            if 'traffic_selected_locally' in editor and 'identity_form_requested_readonly' not in editor:
                proceed = page.get_by_role('button',name='Continue',exact=True)
                if await proceed.count() == 1 and await proceed.is_enabled():
                    try:
                        await proceed.click(timeout=4000); editor.append('identity_form_requested_readonly')
                    except Exception:
                        editor.append('identity_form_unavailable'); break
            await page.wait_for_timeout(250)
        parsed = urlsplit(str(page.url)); query = parse_qs(parsed.query)
        exact = parsed.hostname in {'adsmanager.facebook.com','business.facebook.com'} and observed == {target} and query.get('act') == [target] and (not query.get('business_id') or query.get('business_id') == [business])
        verified = list(pages.values()) if exact else []
        surface = ''
        try:
            surface = str(await asyncio.wait_for(page.locator('body').inner_text(timeout=1500),timeout=2))[:12000]
        except Exception:
            pass
        phone=advertiser_phone_status(surface,exact)
        return {'account_id':target, 'business_id':business, 'checked_live':True,
            'account_scope_verified':exact,
            'status':'VERIFIED' if verified else 'UNVERIFIED',
            'ad_account_page_access_verified':bool(verified), 'data':verified,
            'advertiser_phone':{'status':phone,'checked_live':exact,'source':'ads_manager_visible_requirement'},
            'checked_at':int(time.time()),
            'diagnostic':{'observed_account_ids':sorted(observed), 'queries':diagnostics,
                'operations':operations, 'url':str(page.url), 'surface':re.sub(r'\d{6,}','[id]',surface[:1800]),
                'editor_steps':editor, 'blocked_writes':blocked,
                'code':'' if verified else 'PAGE_ACCESS_EVIDENCE_MISSING'}}
    finally:
        page.remove_listener('request', on_request); page.remove_listener('response', on_response)
        # Keep the write barrier until this dedicated browser context closes:
        # the editor can schedule an autosave after the probe has returned.
        for task in list(tasks): task.cancel()
        if tasks:
            # A stalled renderer must not extend cancellation past the wall deadline.
            await asyncio.wait(list(tasks),timeout=.5)


async def inspect_profile_pages(resolver: Any, profile: str, target: str) -> dict:
    target = account_id(target)
    business = saved_payment_business(profile, target)
    if not business: raise ValueError('RK is absent from this profile inventory')
    context = await asyncio.wait_for(resolver.resolve(profile), timeout=12)
    progress = {'stage':'opening_browser'}
    async def probe():
        async with FacebookBusinessBrowser(context, v8_old_space_mb=128) as browser:
            async def memory_guard():
                while True:
                    memory = _cgroup_memory_snapshot_mb()
                    current,limit = memory.get('current_mb',0),memory.get('limit_mb',0)
                    if limit and current >= max(limit*.85,limit-120):
                        progress['memory'] = memory
                        return
                    await asyncio.sleep(.2)
            inspection=asyncio.create_task(inspect_browser_pages(browser,target,business,progress=progress))
            guard=asyncio.create_task(memory_guard())
            try:
                done,_=await asyncio.wait({inspection,guard},timeout=48,return_when=asyncio.FIRST_COMPLETED)
                if inspection in done: return inspection.result()
                code='PAGE_ACCESS_MEMORY_LIMIT' if guard in done else 'PAGE_ACCESS_INSPECTION_TIMEOUT'
                try:
                    progress['surface']=str(await asyncio.wait_for(browser.page.locator('body').inner_text(timeout=700),timeout=1))[:1800]
                except Exception: pass
                return unverified_result(profile,target,code,progress)
            finally:
                for task in (inspection,guard):
                    if not task.done(): task.cancel()
                    task.add_done_callback(lambda finished: None if finished.cancelled() else finished.exception())
                await asyncio.wait({inspection,guard},timeout=.6)
    try:
        result = await asyncio.wait_for(probe(), timeout=65)
    except asyncio.TimeoutError:
        return unverified_result(profile,target,'PAGE_ACCESS_INSPECTION_TIMEOUT',progress)
    return {'profile_id':profile, **result}
