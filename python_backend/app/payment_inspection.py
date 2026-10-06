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


def masked_payment_methods(text: str) -> list[dict[str,str]]:
    methods = []
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
    return methods


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
    methods = masked_payment_methods(text) if exact else []
    empty = exact and bool(re.search(
        r"no payment methods?\b|haven.t added (?:(?:a|any) )?payment|"
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


def selected_payment_asset(profile: str, target: str, path: Path = Path('/var/lib/remask/workspace-live-meta-snapshots.json')) -> dict[str, str]:
    try:
        rows = json.loads(path.read_text()).get(profile, {}).get('ad_accounts', [])
        matches = [r for r in rows if isinstance(r, dict) and str(r.get('profile', profile)) == profile
                   and re.sub(r'^act_', '', str(r.get('id') or r.get('account_id') or '')) == target]
        if len(matches) == 1 and re.fullmatch(r'\d{5,30}', str(matches[0].get('business_id') or '')):
            row = matches[0]
            return {key:str(row.get(key) or '') for key in ('business_id','business_asset_id','name')}
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return {}


def _merge_payment_asset(asset: dict[str,str], hinted: dict[str,str], *,
                         business_id: str = '', confirmed_name: str = '',
                         target: str = '') -> dict[str,str]:
    """Merge trusted ownership with fresh Workspace navigation labels.

    Workspace alias/name can be newer than the durable live snapshot, but they
    never prove identity: the browser still confirms the canonical RK inside
    the requested BM before payment UI is read or mutated.
    """
    business=business_id or asset.get('business_id') or hinted.get('business_id','')
    return {
        'business_id':business,
        'business_asset_id':hinted.get('business_asset_id') or asset.get('business_asset_id',''),
        'name':hinted.get('name') or confirmed_name or asset.get('name') or target,
    }


async def resolve_payment_asset(profile: str, target: str, state: Any = None,
                                hint: dict[str,Any] | None = None) -> dict[str,str]:
    """Resolve the selected RK's BM from saved evidence or a workspace hint.

    A hint only selects where to navigate. The native billing flow still has to
    prove the exact BM and RK in Meta before reading or entering card details.
    """
    hinted: dict[str,str] = {}
    if hint is not None:
        if not isinstance(hint,dict):return {}
        business=str(hint.get('business_id') or '').strip()
        alias=str(hint.get('business_asset_id') or '').strip()
        name=str(hint.get('name') or '').strip()
        if (not re.fullmatch(r'\d{5,30}',business)
                or (alias and not re.fullmatch(r'\d{5,30}',alias))
                or len(name)>160):return {}
        hinted={'business_id':business,'business_asset_id':alias,'name':name}
    asset=selected_payment_asset(profile,target)
    if state is None:
        if asset and hinted and asset.get('business_id')!=hinted['business_id']:return {}
        return _merge_payment_asset(asset,hinted,target=target) if hinted else asset
    matches=[row for row in await state.confirmed_ad_account_bindings_for_profile(profile)
        if str(row.get('ad_account_id') or '').removeprefix('act_')==target
        and re.fullmatch(r'\d{5,30}',str(row.get('business_id') or ''))]
    businesses={str(row['business_id']) for row in matches}
    if len(businesses)>1:
        return {}
    if not businesses:
        if asset and hinted and asset.get('business_id')!=hinted['business_id']:return {}
        return _merge_payment_asset(asset,hinted,target=target) if hinted else asset
    business=next(iter(businesses))
    if asset and asset.get('business_id')!=business:
        return {}
    if hinted and hinted['business_id']!=business:return {}
    confirmed_name=str(matches[0].get('account_name') or matches[0].get('name') or '')
    if asset or hinted:
        return _merge_payment_asset(asset,hinted,business_id=business,
            confirmed_name=confirmed_name,target=target)
    return {'business_id':business,'business_asset_id':'','name':confirmed_name or target}


async def _resolve_payment_account_name(page: Any, name: str) -> str:
    """Recover a placeholder name only from one rendered RK row with Details.

    The caller still must prove the exact canonical ID in that row's pane.
    """
    if name and not re.fullmatch(r'(?:act_)?\d{5,30}',name):return name
    details=page.get_by_role('link',name=re.compile(r'^(Details|Подробнее|Деталі)$',re.I))
    rows=page.get_by_role('row').filter(has=details).filter(visible=True)
    if await rows.count()!=1:return ''
    labels=await rows.get_by_role('button').all_text_contents()
    candidates=[]
    for label in labels:
        first=re.sub(r'[\u200b-\u200d\ufeff]','',label).strip().split('\n')[0].strip()
        if not first or re.match(r'^\d+(?:\s|$)',first):continue
        if re.fullmatch(r'Details|More|Close|Open in Ads Manager|Deactivate|Assign people|Assign partner|Opportunity score',first,re.I):continue
        if first not in candidates:candidates.append(first)
    return candidates[0] if len(candidates)==1 else ''


async def select_settings_payment_tab(browser: Any) -> bool:
    """Open the observed Payment methods tab in the selected RK pane."""
    page=browser.page
    try:
        await page.wait_for_function("""() => Array.from(document.querySelectorAll('[role="tab"],button,[role="button"],span'))
          .some(el=>el.getClientRects().length && /^(Payment methods|Способы оплаты|Способи оплати)$/i.test(
            (el.getAttribute('aria-label')||el.textContent||'').replace(/[\\u200b-\\u200d\\ufeff]/g,'').trim()))""",timeout=6000)
        pattern=re.compile(r'^(Payment methods|Способы оплаты|Способи оплати)[\s\u200b-\u200d\ufeff]*$',re.I)
        tab=None
        for role in ('tab','button'):
            match=page.get_by_role(role,name=pattern).filter(visible=True)
            if await match.count()==1:tab=match;break
        if tab is None:
            match=page.get_by_text(pattern).filter(visible=True)
            if await match.count()==1:tab=match
        if tab is None:return False
        await tab.click(timeout=3000)
        # Meta loads tab content after the account identity is already visible.
        try:
            await page.get_by_role('button',name=re.compile(r'^(Add payment method|Добавить способ оплаты|Додати спосіб оплати)$',re.I)).wait_for(state='visible',timeout=7000)
        except Exception:pass
        await browser._assert_authenticated()
        return True
    except BrowserBusinessError:raise
    except Exception:return False


async def selected_payment_pane_text(browser: Any, name: str) -> str:
    """Read only the right-hand RK pane, excluding sibling account rows."""
    return str(await browser.page.evaluate("""expected => {
      const visible=e=>e.getClientRects().length;
      const clean=s=>(s||'').replace(/[\\u200b-\\u200d\\ufeff]/g,'').replace(/\\s+/g,' ').trim();
      const candidates=Array.from(document.querySelectorAll('div,section,[role="dialog"]')).filter(visible)
        .map(el=>({text:clean(el.innerText),box:el.getBoundingClientRect()}))
        .filter(r=>r.box.x>=500 && r.box.width<=800 && r.text.includes(expected) && /Payment methods|Способы оплаты|Способи оплати/i.test(r.text))
        .sort((a,b)=>a.box.width*a.box.height-b.box.width*b.box.height);
      return candidates[0]?.text||'';
    }""",name) or '')


def settings_payment_summary(target: str, url: str, text: str, *, asset: dict[str,str], identity: dict[str,Any]) -> dict[str,Any]:
    target=account_id(target);parsed=urlsplit(url);query=parse_qs(parsed.query,keep_blank_values=True)
    aliases={target,asset.get('business_asset_id','')}-{''}
    exact=(parsed.scheme=='https' and parsed.hostname=='business.facebook.com'
           and parsed.path.rstrip('/')=='/latest/settings/ad_accounts'
           and query.get('business_id')==[asset.get('business_id')]
           and len(query.get('selected_asset_id',[]))==1 and query['selected_asset_id'][0] in aliases
           and all(query[key]==[target] for key in ('act','ad_account_id','asset_id') if key in query)
           and identity.get('confirmed') is True
           and re.sub(r'^act_','',str(identity.get('ad_account_id') or ''))==target
           and identity.get('business_id',asset.get('business_id'))==asset.get('business_id')
           and bool(asset.get('name')) and asset['name'] in text
           and bool(re.search(r'Payment methods|Способы оплаты|Способи оплати',text,re.I)))
    # Reuse the proven mask parser; the actual Settings scope above supplies
    # the identity evidence rather than manufacturing a Billing URL.
    methods=masked_payment_methods(text) if exact else []
    empty=exact and bool(re.search(r'no payment methods?\b|haven.t added (?:(?:a|any) )?payment|нет (?:добавленных )?способов оплаты|немає (?:доданих )?способів оплати',text,re.I))
    return {'account_id':target,'account_scope_verified':exact,'verification_status':'LINKED' if methods else 'NONE' if empty else 'UNVERIFIED',
            'card_linked':True if methods else False if empty else None,'payment_methods':methods,'funding_verified':False,
            'checked_live':True,'source':'private_facebook_selected_rk_payment_tab'}


async def inspect_payment_methods(browser: Any, target: str, *, business_id: str = '', asset: dict[str,str] | None = None, fresh_billing_context: bool = False) -> dict[str, Any]:
    """Discover Billing from the authenticated Ads Manager UI; never guess it."""
    target = account_id(target)
    start_url = browser.ADS_MANAGER_URL + '?act=' + target
    if re.fullmatch(r'\d{5,30}', business_id):
        start_url = browser.SETTINGS_AD_ACCOUNTS_URLS[0].format(business_id=business_id)
        alias=(asset or {}).get('business_asset_id','')
        if re.fullmatch(r'\d{5,30}',alias):start_url+='&selected_asset_id='+alias+'&selected_asset_type=ad-account'
    page=browser.page
    parsed=urlsplit(str(getattr(page,'url','')))
    query=parse_qs(parsed.query,keep_blank_values=True)
    aliases={target,(asset or {}).get('business_asset_id','')}-{''}
    current_settings=(bool(asset and asset.get('name') and business_id)
        and parsed.scheme=='https' and parsed.hostname=='business.facebook.com'
        and parsed.path.rstrip('/')=='/latest/settings/ad_accounts'
        and query.get('business_id')==[business_id]
        and len(query.get('selected_asset_id',[]))==1 and query['selected_asset_id'][0] in aliases
        and all(query[key]==[target] for key in ('act','ad_account_id','asset_id') if key in query)
        and ('selected_asset_type' not in query or query['selected_asset_type']==['ad-account']))
    identity=None
    if current_settings:
        identity=await browser._read_selected_ad_account_identity(business_id=business_id,account_name=asset['name'])
        current_settings=(identity.get('confirmed') is True
            and re.sub(r'^act_','',str(identity.get('ad_account_id') or ''))==target
            and identity.get('business_id',business_id)==business_id)
    if not current_settings:
        # A card preparation already proved this exact Settings pane. Reloading
        # it before Billing loads the large Meta SPA twice in the 1 GB worker.
        await browser._goto(start_url,timeout_ms=25000,settle_ms=700,attempts=1)
        identity=None
    page = browser.page
    if page is None:
        raise BrowserBusinessError("BROWSER_NOT_READY", "Payment browser is not open.", retryable=False)

    if asset and business_id and (not asset.get('name') or re.fullmatch(r'(?:act_)?\d{5,30}',asset['name'])):
        try:
            await page.get_by_role('link',name=re.compile(r'^(Details|Подробнее|Деталі)$',re.I)).filter(visible=True).wait_for(state='visible',timeout=6000)
            name=await _resolve_payment_account_name(page,asset.get('name') or target)
            if name:asset={**asset,'name':name};identity=None
        except Exception:pass
    if asset and asset.get('name') and business_id:
        identity=identity or await browser._read_selected_ad_account_identity(business_id=business_id,account_name=asset['name'])
        if identity.get('confirmed') and re.sub(r'^act_','',str(identity.get('ad_account_id') or ''))==target:
            if await select_settings_payment_tab(browser):
                text=await selected_payment_pane_text(browser,asset['name'])
                result=settings_payment_summary(target,str(page.url),text,asset=asset,identity=identity)
                result['profile_id']=browser.profile_id
                if result['verification_status'] in {'LINKED','NONE'}:return result

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
      .some(a => a.getClientRects().length && /billing|payment|платеж|платіж|оплат|^all tools(?: menu)?$|^все инструменты$|^усі інструменти$/i.test(
        (a.innerText||a.getAttribute('aria-label')||'').trim()))"""
    try:
        await page.wait_for_function(navigation_ready, timeout=5000)
    except Exception:
        pass
    links = await page.evaluate(read_links)
    if not links:
        # Meta may keep Billing inside the rendered All tools drawer.
        # Open an exact observed menu control once; no guessed Billing URL.
        menu_name = re.compile(r"^(All tools(?: menu)?|Все инструменты|Усі інструменти)$", re.I)
        for role in ("button", "link"):
            menu = page.get_by_role(role, name=menu_name)
            if await menu.count() == 1 and await menu.is_visible():
                await menu.click(timeout=4000)
                await browser._assert_authenticated()
                try:
                    await page.wait_for_function("""() => Array.from(document.querySelectorAll('a[href]'))
                      .some(a => a.getClientRects().length && /billing|payment|платеж|платіж|оплат/i.test(
                        (a.innerText||a.getAttribute('aria-label')||'').trim()))""", timeout=15000)
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
    if fresh_billing_context:
        # Preserve only the validated rendered navigation destination and RK
        # identity. Release the large Settings SPA before opening Billing.
        # No card fields or payment submissions exist at this boundary.
        await browser.close()
        await browser.open()
        page=browser.page
        if page is None:
            raise BrowserBusinessError("BROWSER_NOT_READY","Payment browser is not open.",retryable=False)
    await browser._goto(billing_url, timeout_ms=25000, settle_ms=700, attempts=1)
    # Billing is a lazy SPA: DOMContentLoaded can still show only its skeleton.
    # Wait for rendered account evidence, then keep the same strict scope check.
    try:
        await page.wait_for_function("""target => {
          const text=document.body?.innerText||'';
          return new RegExp('(^|[^0-9])'+target+'([^0-9]|$)').test(text) &&
            /payment methods|payment settings|billing.{0,12}payments|способ[ыа] оплаты|настройки платеж|платіжн[іи] метод|способи оплати/i.test(text);
        }""", arg=target, timeout=15000)
    except Exception:
        await browser._assert_authenticated()
    # Body text remains in memory only. Return explicitly whitelisted masked data.
    text = await page.locator("body").inner_text(timeout=5000)
    result = payment_summary(target, str(page.url), text)
    result["profile_id"] = browser.profile_id
    return result


async def inspect_profile_payment_methods(resolver: Any, profile_id: str, target: str, *, state: Any = None,
                                          asset_hint: dict[str,Any] | None = None) -> dict[str, Any]:
    from .session import ProfileSession

    target = account_id(target)
    context = await resolver.resolve(profile_id)
    asset=await resolve_payment_asset(profile_id,target,state,asset_hint)
    if asset.get('business_id') == str(context.cookies.get('c_user') or ''):
        raise ValueError('PERSONAL_AD_ACCOUNT_EXCLUDED')
    async with ProfileSession(context) as session:
        browser = await session.facebook_business_browser()
        # Existing browser profile lock/global semaphore limits concurrent load.
        try:
            result=await asyncio.wait_for(inspect_payment_methods(browser, target,
                business_id=asset.get('business_id') or saved_payment_business(profile_id,target),asset=asset,fresh_billing_context=True), timeout=65)
            result['diagnostic']={'stage':'payment_methods_observed' if result['account_scope_verified'] else 'payment_account_scope_unverified',
                'path':urlsplit(str(browser.page.url)).path,'masked_method_count':len(result['payment_methods'])}
            try:
                import base64
                masks=[frame.locator('input,textarea') for frame in browser.page.frames]
                result['ui_preview']=base64.b64encode(await browser.page.screenshot(type='jpeg',quality=65,mask=masks,timeout=4000)).decode('ascii')
            except Exception as exc:result['ui_preview_unavailable']=type(exc).__name__
            logging.getLogger('remask.payment_inspection').info('payment inspection profile=%s status=%s scope_verified=%s diagnostic=%s',profile_id,result['verification_status'],result['account_scope_verified'],result['diagnostic'])
            return result
        except BrowserBusinessError:
            raise
        except Exception as exc:
            if 'Target crashed' not in str(exc):
                raise
            from .facebook_business_browser import _cgroup_memory_snapshot_mb
            logging.getLogger('remask.payment_inspection').warning('payment browser crashed profile=%s account=%s memory=%s',profile_id,target,_cgroup_memory_snapshot_mb())
            raise BrowserBusinessError('PAYMENT_BROWSER_CRASHED',
                'Meta payment browser crashed before payment methods were read.',retryable=False) from exc
