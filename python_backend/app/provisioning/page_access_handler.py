"""Prepare one shared Page after the RK creation checkpoint, then prove Identity."""
from __future__ import annotations
import asyncio
import logging
import re
from typing import Any

from ..facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError, _cgroup_memory_snapshot_mb
from ..page_access_inspection import inspect_browser_pages
from .advertising_page import AdvertisingPageStore, _PAGE_LOCK, ensure_common_page
from .models import ProvisioningError, ProvisioningStep
from .ad_account_handler import _normalize_ad_account_id

log=logging.getLogger('remask.page_access')


async def _one(locator):
    return await locator.count()==1 and await locator.is_visible()


async def _select_page(browser, name: str) -> None:
    button=browser.page.get_by_role('button',name=name,exact=True)
    text=browser.page.get_by_text(name,exact=True)
    if await _one(button): await button.click(timeout=3000)
    elif await _one(text): await text.click(timeout=3000)
    else: raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Exact Page entry is not unique',retryable=True)


async def _ads_only(dialog) -> None:
    full=dialog.get_by_role('checkbox',name=re.compile(r'full control|manage everything',re.I))
    for control in await full.all():
        if await control.is_checked():
            raise BrowserBusinessError('PAGE_SHARE_PERMISSION_REVIEW_REQUIRED','Full-control permission is selected',retryable=False)
    ads=dialog.get_by_role('checkbox',name=re.compile(r'^(Ads|Manage ads|Create ads)$',re.I))
    if await ads.count()==1:
        await ads.check(timeout=2500); return
    ads=dialog.get_by_role('switch',name=re.compile(r'^(Ads|Manage ads|Create ads)$',re.I))
    if await ads.count()==1:
        if await ads.get_attribute('aria-checked')=='false': await ads.click(timeout=2500)
        if await ads.get_attribute('aria-checked')=='true': return
    raise BrowserBusinessError('PAGE_SHARE_PERMISSION_UI_CHANGED','Ads permission control is unavailable',retryable=True)


async def _request_target_page_access(browser, config: dict, business: str, checkpoint, prior: dict) -> bool:
    """Request Ads task access from the target BM; never claim Page ownership."""
    if await browser.verify_page_attached(business_id=business,page_id=config['page_id']):
        return True
    if prior.get('phase') in {
            'TARGET_PAGE_ACCESS_SUBMITTED',
            'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
            'TARGET_PAGE_ACCESS_OWNER_APPROVED',
            'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
        }:
        # Every owner-side phase belongs to the same already-submitted request.
        # A retry must reconcile/continue it and must never create a duplicate.
        return False
    if prior.get('phase') in {'TARGET_PAGE_ACCESS_CLICK_INTENT',
            'PARTNER_SHARE_CLICK_INTENT','PARTNER_SHARE_SUBMITTED'}:
        raise BrowserBusinessError('PAGE_SHARE_RESULT_UNKNOWN',
            'The saved target BM access request must be reconciled before another is sent',retryable=True)
    if not await browser._open_pages_add_action(business):
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Target BM Pages did not expose Add',retryable=True)
    # This action shares access. Add an existing Page transfers ownership and
    # must never be substituted when the request option is unavailable.
    if not await browser._click_named(('Request shared access to a Facebook Page','Request access to a Page')):
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Target BM did not expose Request shared access',retryable=True)
    if not await browser._fill_page_add_identifier(
        labels=('Facebook Page name or URL','Facebook Page URL or ID','Page URL or ID','Facebook Page'),
        value=config['page_id']):
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Shared-access Page field is unavailable',retryable=True)
    await browser.page.wait_for_timeout(900)
    if not await browser._click_exact_page_search_name(config['page_id'],config['name']):
        raise BrowserBusinessError('PAGE_SHARE_PAGE_UNVERIFIED','The exact shared Page search result is not unique',retryable=True)
    next_button=browser.page.get_by_role('button',name='Next',exact=True).filter(visible=True)
    if await _one(next_button):
        await next_button.click(timeout=3000)
        await browser.page.wait_for_timeout(500)
    dialog=browser.page.get_by_role('dialog').filter(visible=True)
    if await dialog.count()!=1:
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Shared-access review dialog is not unique',retryable=True)
    await _ads_only(dialog)
    # The current NorthStar access wizard uses Confirm on Choose access.
    # Scope this to the reviewed request dialog; never match ownership buttons.
    submit=dialog.get_by_role('button',name=re.compile(r'^(Confirm|Request access|Send request)$',re.I))
    if not await _one(submit) or not await submit.is_enabled():
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Shared-access final request action is unavailable',retryable=True)
    await checkpoint({'phase':'TARGET_PAGE_ACCESS_CLICK_INTENT','requested_tasks':['ADVERTISE'],
        'page_id':config['page_id'],'business_id':business})
    await submit.click(timeout=5000)
    await browser.page.wait_for_timeout(1000)
    await checkpoint({'phase':'TARGET_PAGE_ACCESS_SUBMITTED',
        'diagnostic':await browser._diagnostic('target_page_access_submit_response')})
    return await browser.verify_page_attached(business_id=business,page_id=config['page_id'])


async def _resolve_owner_page_actor(browser, config: dict) -> tuple[str, dict]:
    """Resolve the switchable Page actor from exact managed-Page evidence."""
    page_id=str(config.get('page_id') or '')
    if not page_id.isdigit():
        raise BrowserBusinessError(
            'PAGE_OWNER_SWITCH_UNAVAILABLE',
            'The advertising Page ID is unavailable for owner switching',
            retryable=True,
            diagnostic={'page_id':page_id},
        )

    def exact(rows):
        return next(
            (
                row for row in (rows or [])
                if isinstance(row,dict)
                and str(row.get('id') or row.get('page_id') or '')==page_id
            ),
            None,
        )

    saved=exact(getattr(getattr(browser,'context',None),'pages',None) or [])
    if saved is not None:
        actor=str(saved.get('profile_id') or '')
        if actor.isdigit():
            return actor,{'source':'session_context','page':saved}
        # A saved exact Page row proves the asset belongs to this FB profile.
        return page_id,{'source':'session_context_page_asset','page':saved}

    try:
        rows=await asyncio.wait_for(
            browser.discover_managed_pages(
                fast=True,
                navigation_timeout_ms=9000,
            ),
            timeout=24,
        )
    except Exception as exc:
        diagnostic=dict(
            getattr(browser,'_last_page_inventory_diagnostic',{})
            if isinstance(getattr(browser,'_last_page_inventory_diagnostic',{}),dict)
            else {}
        )
        diagnostic.update({
            'page_id':page_id,
            'actor_resolution':'managed_pages_discovery_failed',
            'discovery_error':f'{exc.__class__.__name__}: {exc}'[:1200],
        })
        raise BrowserBusinessError(
            'PAGE_OWNER_SWITCH_UNAVAILABLE',
            'The exact Page actor could not be discovered from the authenticated managed-Pages surface',
            retryable=True,
            diagnostic=diagnostic,
        ) from exc

    live=exact(rows)
    if live is None:
        diagnostic=dict(
            getattr(browser,'_last_page_inventory_diagnostic',{})
            if isinstance(getattr(browser,'_last_page_inventory_diagnostic',{}),dict)
            else {}
        )
        diagnostic.update({
            'page_id':page_id,
            'actor_resolution':'exact_page_not_in_live_inventory',
            'discovered_page_ids':[
                str(row.get('id') or row.get('page_id') or '')
                for row in rows if isinstance(row,dict)
            ][:20],
        })
        raise BrowserBusinessError(
            'PAGE_OWNER_SWITCH_UNAVAILABLE',
            'The exact advertising Page is absent from the live managed-Pages inventory',
            retryable=True,
            diagnostic=diagnostic,
        )

    actor=str(live.get('profile_id') or '')
    if actor.isdigit():
        return actor,{'source':'live_managed_pages_profile_id','page':live}
    # Some New Pages Experience accounts expose no separate profile_id and
    # switch using the exact managed Page asset ID itself.
    return page_id,{'source':'live_managed_pages_page_asset','page':live}


def _saved_i_user_cookie(browser) -> dict | None:
    raw=getattr(getattr(browser,'context',None),'cookies',{}) or {}
    if isinstance(raw,dict):
        value=str(raw.get('i_user') or '')
        return ({'name':'i_user','value':value,'domain':'.facebook.com','path':'/',
            'secure':True,'sameSite':'Lax'} if value else None)
    if isinstance(raw,list):
        for row in raw:
            if not isinstance(row,dict) or str(row.get('name') or '')!='i_user' or not row.get('value'):
                continue
            cookie={'name':'i_user','value':str(row['value']),
                'domain':str(row.get('domain') or '.facebook.com'),
                'path':str(row.get('path') or '/')}
            for key in ('expires','httpOnly','secure','sameSite'):
                if key in row and row.get(key) is not None:
                    cookie[key]=row.get(key)
            return cookie
    return None


async def _set_i_user(browser, value: str | None, *, restore: dict | None=None) -> None:
    context=getattr(browser,'_browser_context',None)
    if context is None:
        raise BrowserBusinessError('PAGE_OWNER_SWITCH_UNAVAILABLE',
            'Facebook browser context is unavailable for Page identity switching',retryable=True)
    await context.clear_cookies(name='i_user')
    cookie=restore if restore is not None else (
        {'name':'i_user','value':str(value),'domain':'.facebook.com','path':'/',
            'secure':True,'sameSite':'Lax'} if value else None)
    if cookie:
        await context.add_cookies([cookie])


async def _visible_owner_review_requests(page) -> list:
    pattern=re.compile(r'^(Review request|Respond to request|Review access request)$',re.I)
    items=[]
    for role in ('button','link'):
        locator=page.get_by_role(role,name=pattern)
        for index in range(min(await locator.count(),12)):
            item=locator.nth(index)
            if await item.is_visible() and await item.is_enabled():
                items.append(item)
    return items


async def _owner_request_context(item) -> str:
    try:
        return str(await item.evaluate("""el => {
            let node=el, parts=[];
            for (let i=0; node && i<9; i++, node=node.parentElement) {
                const text=(node.innerText || node.textContent || '').replace(/\\s+/g,' ').trim();
                // Stop before the surrounding list/settings surface: it can
                // contain another Business's name or an already-active partner.
                if (text.length>1600 || /Partners with access|Manage and view access/i.test(text)) break;
                const reviews=[...node.querySelectorAll('button,a,[role="button"],[role="link"]')]
                    .filter(item => /^(Review request|Respond to request|Review access request)$/i.test(
                        (item.innerText || item.textContent || '').trim()));
                if (reviews.length>1) break;
                const href=node.getAttribute ? (node.getAttribute('href') || '') : '';
                const aria=node.getAttribute ? (node.getAttribute('aria-label') || '') : '';
                const testid=node.getAttribute ? (node.getAttribute('data-testid') || '') : '';
                parts.push([text,href,aria,testid].filter(Boolean).join(' | '));
            }
            return parts.join(' || ').slice(0,10000);
        }""") or '')
    except Exception:
        return ''


async def _pick_owner_review_request(page, business: str, business_name: str=''):
    candidates=await _visible_owner_review_requests(page)
    evidence=[]
    matched=[]
    for item in candidates:
        context=await _owner_request_context(item)
        id_hit=bool(re.search(r'(?<!\d)'+re.escape(str(business))+r'(?!\d)',context))
        name=' '.join(str(business_name or '').split())
        normalized=' '.join(context.split())
        name_hit=bool(name and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',normalized))
        hit=id_hit or name_hit
        evidence.append({'business_match':hit,'business_id_match':id_hit,
            'business_name_match':name_hit,'context':context[:1200]})
        if hit:
            matched.append(item)
    if len(matched)==1:
        return matched[0],evidence
    return None,evidence


async def _visible_dialog_or_page(page):
    dialogs=page.get_by_role('dialog').filter(visible=True)
    return dialogs if await dialogs.count()==1 else page


async def _reject_full_control(scope) -> None:
    pattern=re.compile(r'full control|manage everything|ownership',re.I)
    for role in ('checkbox','switch'):
        controls=scope.get_by_role(role,name=pattern)
        for index in range(min(await controls.count(),8)):
            control=controls.nth(index)
            if not await control.is_visible():
                continue
            checked=False
            try:
                checked=await control.is_checked()
            except Exception:
                checked=(await control.get_attribute('aria-checked'))=='true'
            if checked:
                raise BrowserBusinessError('PAGE_SHARE_PERMISSION_REVIEW_REQUIRED',
                    'Owner approval unexpectedly includes full-control/ownership permission',retryable=False)


def _facebook_password(browser) -> str:
    context=getattr(browser,'context',None)
    for key in ('password','facebook_password','login_password','pass'):
        value=getattr(context,key,None)
        if value:
            return str(value)
    return ''


async def _resolve_target_business_name(browser, business: str, recorded: dict | None=None) -> tuple[str, dict]:
    """Resolve the exact created BM identity without requiring an actor-wide selector."""
    business_id=str(business or '').strip()
    if not business_id.isdigit():
        return '',{'source':'invalid_business_id'}
    recorded=recorded if isinstance(recorded,dict) else {}
    expected=str(recorded.get('business_name') or '').strip()
    known=recorded.get('known_business_names') or {}
    if (str(recorded.get('business_id') or '')==business_id
            and recorded.get('create_confirmed') is True
            and recorded.get('source')=='python_worker_confirmed_create'
            and expected and expected!=business_id):
        duplicates=[str(key) for key,value in known.items()
            if str(key)!=business_id and ' '.join(str(value or '').split())==' '.join(expected.split())]
        if not duplicates:
            return expected,{'source':'recorded_profile_business_create',
                'business_id':business_id,'business_name':expected,
                'profile_id':str(recorded.get('profile_id') or ''),
                'create_confirmed':True}
    try:
        rows=await asyncio.wait_for(browser.snapshot_businesses(),timeout=24)
    except Exception as exc:
        return '',{
            'source':'snapshot_businesses_failed',
            'error':f'{exc.__class__.__name__}: {exc}'[:900],
        }
    name=str((rows or {}).get(business_id) or '').strip()
    if name and sum(' '.join(str(value or '').split())==' '.join(name.split())
            for value in (rows or {}).values())!=1:
        return '',{'source':'ambiguous_business_name','business_id':business_id,
            'business_name':name}
    return name,{
        'source':'snapshot_businesses',
        'business_id':business_id,
        'business_name':name,
        'known_business_ids':sorted(str(key) for key in (rows or {}).keys())[:20],
    }


async def _wait_owner_access_surface(page, business_name: str, *, timeout_seconds: float=8.0) -> dict:
    """Wait for the Page-access React surface, not just the committed document."""
    expected=' '.join(str(business_name or '').split()).strip()
    if not callable(getattr(page,'locator',None)):
        return {'ready':False,'polls':0,'body_excerpt':''}
    deadline=asyncio.get_running_loop().time()+max(0.5,float(timeout_seconds))
    last_text=''
    polls=0
    max_polls=24
    while asyncio.get_running_loop().time()<deadline and polls<max_polls:
        polls+=1
        try:
            text=await page.locator('body').inner_text(timeout=1800)
        except Exception:
            text=''
        normalized=' '.join(str(text or '').split())
        if normalized:
            last_text=normalized[:5000]
            has_access_shell=bool(re.search(
                r'Manage and view access|People with Facebook access|People with task access|Partners with access',
                normalized,re.I))
            has_business=bool(expected and expected in normalized)
            has_request=bool(re.search(
                r'Review request|Respond to request|Review access request|View request',
                normalized,re.I))
            if has_access_shell and (has_business or has_request):
                return {'ready':True,'polls':polls,'body_excerpt':last_text[:2400]}
            if has_access_shell and polls>=3:
                # The access shell itself is enough to run exact structured
                # locators even when the target request/card is absent.
                return {'ready':True,'polls':polls,'body_excerpt':last_text[:2400]}
        await asyncio.sleep(0.4)
    return {'ready':False,'polls':polls,'body_excerpt':last_text[:2400]}


async def _owner_active_partner_ads_access(page, business_name: str, *, diagnostic: dict | None=None) -> dict | None:
    """Prove an exact Business is already an Ads partner on this Page."""
    expected=' '.join(str(business_name or '').split()).strip()
    if not expected:
        return None
    trace=diagnostic if diagnostic is not None else {}
    locators=[('exact_name',page.get_by_text(expected,exact=True))]
    try:
        locators.append(('partner_menu',page.get_by_role('button',
            name='More options for '+expected,exact=True)))
    except AttributeError:
        pass
    for source,locator in locators:
        count=await locator.count()
        trace[source+'_count']=count
        if count!=1:
            continue
        item=locator.nth(0)
        if not await item.is_visible():
            continue
        try:
            evidence=await item.evaluate(r"""(el, expected) => {
                const norm = value => String(value || '').replace(/\s+/g,' ').trim();
                let node=el;
                const ancestors=[];
                for (let i=0; node && i<24; i++, node=node.parentElement) {
                    const text=norm(node.innerText || node.textContent || '');
                    const menus=[...node.querySelectorAll('button,[role="button"]')].filter(item =>
                        /^More options for /i.test(item.getAttribute('aria-label') || '')).length;
                    if (text) ancestors.push({text,menus});
                }
                let row='';
                for (const {text,menus} of ancestors) {
                    if (/Partners with access/i.test(text)) break;
                    if (text.includes(expected) && /(^|\W)Ads(\W|$)/i.test(text)
                        && text.length <= 1200 && menus <= 1) {
                        row=text;
                        break;
                    }
                }
                const section=ancestors.map(item => item.text).find(text =>
                    /Partners with access/i.test(text) && text.includes(expected)
                ) || '';
                return {row,section:section.slice(0,3000),ancestors:ancestors.slice(0,16)
                    .map(item => ({text:item.text.slice(0,350),menus:item.menus}))};
            }""",expected)
        except Exception as exc:
            trace[source+'_error']=f'{exc.__class__.__name__}: {exc}'[:600]
            continue
        if not isinstance(evidence,dict):
            continue
        row=' '.join(str(evidence.get('row') or '').split())
        section=' '.join(str(evidence.get('section') or '').split())
        trace[source]={'row':row[:1200],'section_found':bool(section),
            'ancestors':evidence.get('ancestors') or []}
        if row and section and expected in row and re.search(r'(^|\W)Ads(\W|$)',row,re.I):
            return {
                'business_name':expected,
                'row':row[:1200],
                'section':section[:3000],
                'source':'page_access_partners_with_ads',
                'anchor':source,
            }
    return None


async def _approve_owner_page_access(browser, config: dict, business: str, checkpoint) -> bool:
    """Approve one exact pending Ads-access request as the Page owner, then restore the user actor."""
    page_id=str(config['page_id'])
    await checkpoint({'activity':'APPROVE_TARGET_PAGE_ACCESS'})
    if await browser.verify_page_attached(business_id=business,page_id=page_id):
        return True

    business_name,business_evidence=await _resolve_target_business_name(
        browser,business,config.get('target_business_identity'))
    log.info(
        'PAGE_OWNER business resolved business=%s name=%s source=%s',
        business,business_name,str(business_evidence.get('source') or ''),
    )
    actor,actor_evidence=await _resolve_owner_page_actor(browser,config)
    log.info(
        'PAGE_OWNER actor resolved page=%s business=%s actor=%s source=%s',
        page_id,business,actor,str(actor_evidence.get('source') or ''),
    )
    saved_i_user=_saved_i_user_cookie(browser)
    approval_error=None
    owner_relation_proof=None
    partner_diagnostic={}
    try:
        await _set_i_user(browser,actor)
        await browser._goto('https://www.facebook.com/settings/?tab=profile_access',
            timeout_ms=12000,wait_until='commit',settle_ms=1100,attempts=1)
        await browser._assert_authenticated()
        surface=await _wait_owner_access_surface(browser.page,business_name)
        partner_diagnostic['surface_ready']=surface.get('ready') is True
        partner_diagnostic['surface_polls']=int(surface.get('polls') or 0)
        partner_diagnostic['surface_body_excerpt']=str(surface.get('body_excerpt') or '')[:2400]

        owner_relation_proof=await _owner_active_partner_ads_access(
            browser.page,business_name,diagnostic=partner_diagnostic)
        if owner_relation_proof is not None:
            log.info(
                'PAGE_OWNER active partner Ads access proven page=%s business=%s name=%s',
                page_id,business,business_name,
            )
            await checkpoint({
                'phase':'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
                'requested_tasks':['ADVERTISE'],
                'page_id':page_id,
                'business_id':business,
                'page_actor_id':actor,
                'owner_relation_proof':owner_relation_proof,
                'business_identity_proof':business_evidence,
            })
        else:
            review,evidence=await _pick_owner_review_request(browser.page,business,business_name)
            if review is None:
                diagnostic=await browser._diagnostic('page_owner_access_request_missing')
                diagnostic.update(page_id=page_id,business_id=business,page_actor_id=actor,
                    page_actor_source=str(actor_evidence.get('source') or ''),
                    page_actor_evidence=actor_evidence.get('page') or {},
                    target_business_name=business_name,
                    target_business_evidence=business_evidence,
                    partner_access_probe=partner_diagnostic,
                    pending_request_candidates=evidence[:8])
                log.warning(
                    'PAGE_OWNER pending request missing page=%s business=%s actor=%s source=%s '
                    'business_name=%s url=%s body=%s partner_probe=%s',
                    page_id,business,actor,str(actor_evidence.get('source') or ''),
                    business_name,
                    str(diagnostic.get('url') or '')[:700],
                    str(diagnostic.get('body_excerpt') or '')[:2400],
                    str(partner_diagnostic)[:3500],
                )
                code='PAGE_OWNER_REQUEST_AMBIGUOUS' if len(evidence)>1 else 'PAGE_OWNER_APPROVAL_UI_UNAVAILABLE'
                message=('Multiple Page access requests are visible and the target Business cannot be uniquely proven'
                    if code=='PAGE_OWNER_REQUEST_AMBIGUOUS'
                    else 'The exact pending Page access request is not visible on Page access settings')
                raise BrowserBusinessError(code,message,retryable=True,diagnostic=diagnostic)

            await review.click(timeout=4000)
            await browser.page.wait_for_timeout(700)
            scope=await _visible_dialog_or_page(browser.page)
            next_button=scope.get_by_role('button',name=re.compile(r'^Next$',re.I)).filter(visible=True)
            if await _one(next_button) and await next_button.is_enabled():
                await next_button.click(timeout=3500)
                await browser.page.wait_for_timeout(650)
                scope=await _visible_dialog_or_page(browser.page)

            await _reject_full_control(scope)
            approve=scope.get_by_role('button',
                name=re.compile(r'^(Accept|Approve|Confirm|Accept request|Approve request)$',re.I)).filter(visible=True)
            if not await _one(approve) or not await approve.is_enabled():
                diagnostic=await browser._diagnostic('page_owner_access_final_action_missing')
                diagnostic.update(page_id=page_id,business_id=business,page_actor_id=actor)
                raise BrowserBusinessError('PAGE_OWNER_APPROVAL_UI_UNAVAILABLE',
                    'Owner review opened, but the final access approval action is unavailable',
                    retryable=True,diagnostic=diagnostic)

            await checkpoint({'phase':'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
                'requested_tasks':['ADVERTISE'],'page_id':page_id,'business_id':business})
            await approve.click(timeout=5000)
            await browser.page.wait_for_timeout(800)

            password_inputs=browser.page.locator('input[type="password"]:visible')
            if await password_inputs.count():
                if await password_inputs.count()!=1:
                    raise BrowserBusinessError('PAGE_OWNER_PASSWORD_CONFIRM_REQUIRED',
                        'Meta opened an ambiguous password confirmation form',retryable=False)
                password=_facebook_password(browser)
                if not password:
                    diagnostic=await browser._diagnostic('page_owner_password_required')
                    diagnostic.update(page_id=page_id,business_id=business,page_actor_id=actor)
                    raise BrowserBusinessError('PAGE_OWNER_PASSWORD_CONFIRM_REQUIRED',
                        'Meta requires the Facebook password to approve this Page access request',
                        retryable=False,diagnostic=diagnostic)
                await password_inputs.first.fill(password,timeout=3000)
                scope=await _visible_dialog_or_page(browser.page)
                confirm=scope.get_by_role('button',
                    name=re.compile(r'^(Confirm|Continue|Submit)$',re.I)).filter(visible=True)
                if not await _one(confirm) or not await confirm.is_enabled():
                    raise BrowserBusinessError('PAGE_OWNER_PASSWORD_CONFIRM_REQUIRED',
                        'Facebook password was filled but its confirmation action is unavailable',
                        retryable=True,diagnostic=await browser._diagnostic('page_owner_password_confirm_missing'))
                await confirm.click(timeout=5000)
                await browser.page.wait_for_timeout(900)

            await checkpoint({'phase':'TARGET_PAGE_ACCESS_OWNER_APPROVED',
                'requested_tasks':['ADVERTISE'],'page_id':page_id,'business_id':business,
                'diagnostic':await browser._diagnostic('page_owner_access_approved')})
    except BrowserBusinessError as exc:
        approval_error=exc
    finally:
        await _set_i_user(browser,None,restore=saved_i_user)

    # Verification runs only after restoring the personal Facebook actor.
    # Resolve the exact BM's identity from confirmed CREATE or live inventory,
    # then prove its current access in this exact Page's owner-side Ads row.
    if owner_relation_proof is not None:
        return True
    if await browser.verify_page_attached(business_id=business,page_id=page_id):
        await checkpoint({'phase':'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
            'requested_tasks':['ADVERTISE'],'page_id':page_id,'business_id':business})
        return True
    if approval_error is not None:
        raise approval_error
    return False


async def _assign_operator(
    browser, config: dict, business: str, *, relation_preconfirmed: bool=False
) -> None:
    # Partner administrators do not automatically receive the Ads task on a Page.
    # verify_page_attached also navigates to the exact target-BM Pages route.
    verified=await browser.verify_page_attached(
        business_id=business,page_id=config['page_id'])
    if not verified and not relation_preconfirmed:
        raise BrowserBusinessError('PAGE_OPERATOR_ASSIGNMENT_REQUIRED','Target BM Page access is unconfirmed',retryable=True)
    await _select_page(browser,config['name'])
    assign=browser.page.get_by_role('button',name=re.compile(r'^(Assign people|Add people)$',re.I))
    if not await _one(assign):
        raise BrowserBusinessError('PAGE_OPERATOR_ASSIGNMENT_REQUIRED','Target BM people assignment is unavailable',retryable=True)
    await assign.click(timeout=3000)
    dialog=browser.page.get_by_role('dialog')
    if await dialog.count()!=1: raise BrowserBusinessError('PAGE_OPERATOR_ASSIGNMENT_REQUIRED','People assignment dialog is unavailable',retryable=True)
    person=dialog.get_by_role('checkbox',name=re.compile(r'\bYou\b',re.I))
    if await person.count()!=1:
        raise BrowserBusinessError('PAGE_OPERATOR_ASSIGNMENT_REQUIRED','Current operator is not uniquely identified',retryable=True)
    await person.check(timeout=3000)
    await _ads_only(dialog)
    submit=dialog.get_by_role('button',name=re.compile(r'^(Assign|Save)$',re.I))
    if not await _one(submit): raise BrowserBusinessError('PAGE_OPERATOR_ASSIGNMENT_REQUIRED','Operator Ads assignment is unavailable',retryable=True)
    await submit.click(timeout=5000)


async def page_access_handler(session: Any, params: dict, snapshot: dict, **kwargs) -> dict:
    state=kwargs['provisioning_state']; profile=kwargs['profile_id']; item=kwargs['item_id']; scope=kwargs['scope_key']
    resolver=kwargs.get('profile_resolver')
    business=str(snapshot.get('business_id') or '')
    account=_normalize_ad_account_id(snapshot.get('ad_account_id')).removeprefix('act_')
    existing=params.get('existing_target') is True
    if existing:
        business=str(params.get('business_id') or '')
        account=_normalize_ad_account_id(params.get('ad_account_id')).removeprefix('act_')
    if not business.isdigit() or not account.isdigit() or business==session.context.cookies.get('c_user'):
        raise ProvisioningError('CREATED_BUSINESS_RK_REQUIRED','Page access requires the RK of a created Business Portfolio')
    if existing:
        from .ad_account_handler import _verify_expected_ad_account_in_business
        recorded=await state.confirmed_ad_account_bindings_for_profile(profile)
        created_binding=(any(str(row.get('business_id') or '')==business
                and _normalize_ad_account_id(row.get('ad_account_id')).removeprefix('act_')==account
                for row in recorded))
        # The original creation flow uses this exact durable proof too. A
        # recovery action must not depend on rediscovery of the already-created
        # RK. Live advertising permission is still proved independently below.
        verified,evidence=(True,[{'source':'recorded_profile_rk_create','business_id':business,
            'ad_account_id':account}]) if created_binding else await _verify_expected_ad_account_in_business(session,
                business_id=business,account_name=str(params.get('ad_account_name') or ''),
                expected_ad_account_id=account,checks=1)
        if not verified:
            await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
                {'business_id':business,'ad_account_id':account,'diagnostic':{
                    'stage':'business_rk_relation_unverified','binding_evidence':evidence}})
            raise ProvisioningError('BUSINESS_RK_RELATION_UNVERIFIED','Meta did not confirm this exact RK in the requested Business Portfolio',retryable=True)
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'business_id':business,'ad_account_id':account,'inventory_binding_verified':not created_binding,
                'created_binding_confirmed':created_binding,'binding_evidence':evidence})
    else:
        rk_state=await state.step(item,ProvisioningStep.AD_ACCOUNT)
        result=(rk_state or {}).get('result') or {}
        if str(result.get('business_id') or '')!=business or _normalize_ad_account_id(result.get('ad_account_id')).removeprefix('act_')!=account:
            raise ProvisioningError('CREATED_BUSINESS_RK_REQUIRED','RK creation result does not match this portfolio')
    await ensure_common_page(session,{'page_id':params.get('page_id'),'reuse_only':params.get('reuse_only') is True,'policies_accepted':params.get('policies_accepted') is not False},state,resolver)
    store=AdvertisingPageStore.for_context(state,session.context,profile); config=await store.get()
    businesses=((await state.confirmed_business_binding_groups()).get(str(profile)) or {}).get('businesses') or {}
    target_business_identity={**(businesses.get(business) or {}),'profile_id':str(profile),
        'known_business_names':{key:row.get('business_name') for key,row in businesses.items()}}
    context_pages=[
        {
            'id':str(row.get('id') or ''),
            'profile_id':str(row.get('profile_id') or ''),
            'business_id':str(row.get('business_id') or ''),
            'is_owned':row.get('is_owned'),
            'source':str(row.get('source') or ''),
        }
        for row in (getattr(session.context,'pages',None) or [])
        if isinstance(row,dict) and str(row.get('id') or '')==str(config.get('page_id') or '')
    ]
    log.info(
        'PAGE_ACCESS actor context profile=%s page=%s owner_business=%s ownership_phase=%s context=%s',
        profile,
        str(config.get('page_id') or ''),
        str(config.get('owner_business_id') or ''),
        str(config.get('ownership_phase') or ''),
        context_pages,
    )
    prior=((await state.step(item,ProvisioningStep.PAGE_ACCESS)) or {}).get('result') or {}
    async def checkpoint(patch):
        phase=str(patch.get('phase') or '')
        if phase.startswith('TARGET_PAGE_ACCESS_'):
            current=await store.get(); grants=current.get('grants') or {}
            await store.patch(grants={**grants,business:{**grants.get(business,{}),**patch}})
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'page_id':config['page_id'],'business_id':business,'ad_account_id':account,**patch})
    await checkpoint({'diagnostic':{'stage':'target_page_access_start','business_id':business,
        'ad_account_id':account,'owner_bm_required':False}})
    try:
        # Prepare access in lightweight Business Settings first. Loading the
        # campaigns editor is an optional verification, not a sharing dependency.
        async with _PAGE_LOCK:
            config=await store.get()
            config={**config,'target_business_identity':target_business_identity}
            saved_grant=(config.get('grants') or {}).get(business) or {}
            async with FacebookBusinessBrowser(session.context,v8_old_space_mb=256) as browser:
                try:
                    target_relation=await _request_target_page_access(browser,config,business,checkpoint,saved_grant or prior)
                    if not target_relation:
                        target_relation=await _approve_owner_page_access(browser,config,business,checkpoint)
                    if not target_relation:
                        raise BrowserBusinessError('TARGET_PAGE_ACCESS_APPROVAL_REQUIRED',
                            'Meta has not confirmed the exact Page advertising access after owner approval',retryable=True)
                    await _assign_operator(
                        browser,config,business,
                        relation_preconfirmed=bool(target_relation))
                except Exception as exc:
                    diagnostic={'stage':'target_page_access','url':str(getattr(browser.page,'url','')),
                        'memory':_cgroup_memory_snapshot_mb(),'v8_old_space_mb':256}
                    try:
                        diagnostic.update(await asyncio.wait_for(browser._diagnostic('target_page_access'),timeout=3))
                    except Exception:
                        pass
                    if isinstance(exc,BrowserBusinessError):
                        exc.diagnostic={**diagnostic,**(exc.diagnostic or {})}
                    else:
                        await checkpoint({'diagnostic':diagnostic})
                    raise
        result={'page_id':config['page_id'],'page_name':config['name'],'business_id':business,
            'ad_account_id':account,'page_shared_to_business':True,'operator_ads_access_assigned':True,
            'ad_account_page_access_verified':False,'identity_verification':'not_requested',
            'transport':'target_business_page_advertising_access'}
        if params.get('verify_identity') is not True:
            return result
        await checkpoint({'phase':'VERIFY_AD_IDENTITY','page_shared_to_business':True})
        progress={}
        async with FacebookBusinessBrowser(session.context,v8_old_space_mb=256) as browser:
            try:
                proof=await asyncio.wait_for(inspect_browser_pages(browser,account,business,
                    timeout=45,open_identity=True,progress=progress),timeout=55)
            except Exception:
                await checkpoint({'diagnostic':{**progress,'stage':'target_rk_identity_probe',
                    'memory':_cgroup_memory_snapshot_mb(),'v8_old_space_mb':256}})
                raise
        has_access=proof.get('account_scope_verified') is True and any(
            row.get('id')==config['page_id'] and row.get('account_id')==account
            and row.get('ad_account_page_access_verified') is True for row in proof.get('data',[]))
        if not has_access or proof.get('identity_form_verified') is not True:
            await checkpoint({'phase':'PAGE_IDENTITY_UNVERIFIED','page_shared_to_business':True,
                'ad_account_page_access_verified':has_access,'diagnostic':proof.get('diagnostic') or {}})
            raise ProvisioningError('PAGE_IDENTITY_UNVERIFIED',
                'BM access preparation is separate from a verified Page selector in this RK ad form',retryable=True)
        return {**result,'ad_account_page_access_verified':True,'identity_verification':'verified','verification':proof}
    except BrowserBusinessError as exc:
        await checkpoint({'last_error_code':exc.code,'diagnostic':{'stage':'page_access',**(exc.diagnostic or {})}})
        raise ProvisioningError(exc.code,str(exc),retryable=exc.retryable) from exc
