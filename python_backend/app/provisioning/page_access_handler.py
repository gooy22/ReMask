"""Share one profile Page to target BMs for Ads access after the RK checkpoint."""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Any

from ..facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError, _cgroup_memory_snapshot_mb
from ..page_access_inspection import inspect_browser_pages
from ..facebook_page_search import page_lookup_url
from ..graphql_mutation_capture import GraphqlMutationCapture
from ..private_page_access import (
    PrivatePageShareContractStore,
    execute_private_page_share,
)
from ..session_auth_refresh import refresh_saved_auth_context
from .advertising_page import AdvertisingPageStore, _PAGE_LOCK, ensure_common_page
from .models import ProvisioningError, ProvisioningStep
from .ad_account_handler import _normalize_ad_account_id
from .page_full_control import ensure_existing_page_full_control
from .ad_account_full_control import ensure_ad_account_full_control

log=logging.getLogger('remask.page_access')


def _browser_lease(session: Any, **kwargs: Any):
    factory = getattr(session, "browser_lease", None)
    if callable(factory):
        return factory(**kwargs)
    return FacebookBusinessBrowser(
        session.context,
        **kwargs,
    )


def _context_rk_binding(
    context: Any,
    *,
    profile_id: str,
    business_id: str,
    ad_account_id: str,
) -> dict[str, Any] | None:
    """Return one exact last-confirmed BM -> RK binding from resolved context."""
    business=str(business_id or '').strip()
    account=_normalize_ad_account_id(ad_account_id).removeprefix('act_')
    profile=str(profile_id or '').strip()
    if not business.isdigit() or not account.isdigit():
        return None
    for row in (getattr(context,'ad_accounts',None) or []):
        if not isinstance(row,dict):
            continue
        row_business=str(row.get('business_id') or '').strip()
        row_account=_normalize_ad_account_id(
            row.get('ad_account_id')
            or row.get('account_id')
            or row.get('id')
        ).removeprefix('act_')
        row_profile=str(row.get('profile_id') or '').strip()
        if row_profile and profile and row_profile!=profile:
            continue
        if row_business!=business or row_account!=account:
            continue
        return {
            'source':str(
                row.get('source')
                or 'resolved_profile_last_confirmed_inventory'
            ).strip(),
            'business_id':business,
            'ad_account_id':account,
            'profile_id':profile,
            'inventory_updated_at':int(
                getattr(context,'inventory_updated_at',0) or 0
            ),
        }
    return None


def _rk_relation_negative_proof(
    evidence: list[dict[str, Any]] | Any,
    *,
    expected_ad_account_id: str,
) -> bool:
    """True only when exact BM inventory was observed and excludes the RK."""
    expected=_normalize_ad_account_id(expected_ad_account_id).removeprefix('act_')
    if not expected:
        return False
    for observation in evidence if isinstance(evidence,list) else []:
        if not isinstance(observation,dict):
            continue
        if observation.get('confirmed_empty') is True:
            return True
        candidates=[]
        nested=observation.get('evidence')
        if isinstance(nested,dict):
            candidates.append(nested)
        diagnostics=observation.get('diagnostics')
        if isinstance(diagnostics,list):
            candidates.extend(
                row for row in diagnostics if isinstance(row,dict)
            )
        for row in candidates:
            if (
                row.get('exact_business_context') is not True
                or row.get('inventory_observed') is not True
            ):
                continue
            ids={
                _normalize_ad_account_id(value).removeprefix('act_')
                for value in (row.get('inventory_ids') or [])
            }
            ids.discard('')
            if expected not in ids:
                return True
    return False


async def _one(locator):
    return await locator.count()==1 and await locator.is_visible()


async def _wait_business_pages_surface(browser, name: str, *, timeout_seconds: float=7.0) -> dict:
    """Wait for the target-BM Pages surface to hydrate before selecting an asset."""
    expected=' '.join(str(name or '').split()).strip()
    if browser.page is None or not callable(getattr(browser.page,'locator',None)):
        return {'ready':False,'polls':0,'body_excerpt':''}
    deadline=asyncio.get_running_loop().time()+max(0.5,float(timeout_seconds))
    polls=0
    last=''
    while asyncio.get_running_loop().time()<deadline and polls<20:
        polls+=1
        try:
            body=await browser.page.locator('body').inner_text(timeout=1600)
        except Exception:
            body=''
        normalized=' '.join(str(body or '').split())
        if normalized:
            last=normalized[:5000]
            if expected and expected in normalized:
                return {'ready':True,'polls':polls,'body_excerpt':last[:2200],'reason':'page_name'}
            if re.search(r'Business assets|Pages|Add|Partners|People',normalized,re.I) and polls>=3:
                return {'ready':True,'polls':polls,'body_excerpt':last[:2200],'reason':'settings_shell'}
        await asyncio.sleep(0.35)
    return {'ready':False,'polls':polls,'body_excerpt':last[:2200],'reason':'timeout'}


async def _select_page(browser, name: str, page_id: str, business: str) -> dict:
    """Select the exact Page asset in target Business Settings without name guessing."""
    page=str(page_id or '').strip()
    bm=str(business or '').strip()
    expected=' '.join(str(name or '').split()).strip()
    if not page.isdigit() or not bm.isdigit() or not expected:
        raise BrowserBusinessError(
            'PAGE_OPERATOR_PAGE_SELECTION_UNAVAILABLE',
            'Exact Page/Business identity is unavailable for operator assignment',
            retryable=True,
            diagnostic={'page_id':page,'business_id':bm,'page_name':expected},
        )

    surface=await _wait_business_pages_surface(browser,expected)

    async def scan_rows() -> tuple[Any | None,str,list[dict],int,int]:
        rows=browser.page.get_by_role('row').filter(has_text=expected)
        candidates=[]; exact=[]; visible=[]
        for index in range(min(await rows.count(),20)):
            row=rows.nth(index)
            if not await row.is_visible():
                continue
            visible.append(row)
            try:
                evidence=await row.evaluate("""(el, pageId) => {
                    const clean = value => String(value || '').replace(/\\s+/g,' ').trim();
                    const links=[...el.querySelectorAll('a[href]')].map(a=>a.getAttribute('href')||'');
                    const text=clean(el.innerText || el.textContent || '');
                    const attrs=[
                        el.getAttribute('data-key')||'',
                        el.getAttribute('data-testid')||'',
                        el.getAttribute('aria-label')||'',
                    ];
                    const hay=[text,...links,...attrs].join(' | ');
                    const rx=new RegExp('(^|\\\\D)'+pageId+'(\\\\D|$)');
                    return {text:text.slice(0,1200),links:links.slice(0,12),id_match:rx.test(hay)};
                }""",page)
            except Exception:
                evidence={'text':'','links':[],'id_match':False}
            info={
                'index':index,
                'text':str((evidence or {}).get('text') or '')[:1200],
                'links':list((evidence or {}).get('links') or [])[:12],
                'id_match':bool((evidence or {}).get('id_match')),
            }
            candidates.append(info)
            if info['id_match']:
                exact.append(row)
        if len(exact)==1:
            return exact[0],'row_exact_page_id',candidates,len(visible),len(exact)
        if len(exact)>1:
            return None,'ambiguous_page_id_rows',candidates,len(visible),len(exact)
        if len(visible)==1:
            return visible[0],'single_exact_name_row',candidates,len(visible),len(exact)
        return None,'',candidates,len(visible),len(exact)

    async def click_target(target, source: str, candidates: list[dict]) -> dict | None:
        if target is None:
            return None
        for role in ('button','link'):
            locator=target.get_by_role(role,name=expected,exact=True)
            if await _one(locator):
                await locator.click(timeout=3000)
                return {'source':source,'page_id':page,'business_id':bm,
                    'page_name':expected,'rows':candidates[:10],'surface':surface}
        named=target.get_by_text(expected,exact=True)
        if await _one(named):
            await named.click(timeout=3000)
            return {'source':source,'page_id':page,'business_id':bm,
                'page_name':expected,'rows':candidates[:10],'surface':surface}
        try:
            await target.click(timeout=3000)
            return {'source':source+'_row_click','page_id':page,'business_id':bm,
                'page_name':expected,'rows':candidates[:10],'surface':surface}
        except Exception:
            return None

    target,source,candidates,visible_count,exact_count=await scan_rows()
    selected=await click_target(target,source,candidates)
    if selected is not None:
        return selected
    if source=='ambiguous_page_id_rows':
        raise BrowserBusinessError(
            'PAGE_OPERATOR_PAGE_SELECTION_UNAVAILABLE',
            'Multiple Business Settings rows match the exact Page ID',
            retryable=True,
            diagnostic={'page_id':page,'business_id':bm,'page_name':expected,
                'matching_rows':candidates[:10],'surface':surface},
        )

    # Do not navigate directly with selected_asset_id. This route caused a
    # real Facebook checkpoint in production on profile 9. Stay on the already
    # loaded Business Settings surface and fail safely if the exact Page row is
    # not selectable.

    # Last safe fallback: one unique interactive control with the exact name.
    for role in ('button','link'):
        locator=browser.page.get_by_role(role,name=expected,exact=True)
        if await _one(locator):
            await locator.click(timeout=3000)
            return {'source':'unique_global_'+role,'page_id':page,'business_id':bm,
                'page_name':expected,'rows':candidates[:10],'surface':surface}

    diagnostic=await browser._diagnostic('operator_exact_page_entry_missing')
    diagnostic.update({
        'page_id':page,'business_id':bm,'page_name':expected,
        'matching_rows':candidates[:10],
        'visible_name_rows':visible_count,'exact_id_rows':exact_count,
        'business_pages_surface':surface,
    })
    log.warning(
        'PAGE_OPERATOR exact Page unavailable page=%s business=%s url=%s rows=%s surface=%s',
        page,bm,str(diagnostic.get('url') or '')[:700],
        str(candidates[:6])[:3000],str(surface)[:2600],
    )
    raise BrowserBusinessError(
        'PAGE_OPERATOR_PAGE_SELECTION_UNAVAILABLE',
        'The exact Page row/detail is unavailable for operator assignment',
        retryable=True,diagnostic=diagnostic,
    )


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


def _page_share_capture_match(
    request: Any,
    meta: dict[str, Any],
    *,
    business_id: str,
    page_id: str,
    require_named_access_marker: bool = True,
) -> bool:
    if str(meta.get('method') or '').upper()!='POST':
        return False
    if 'graphql' not in str(meta.get('url') or '').lower():
        return False
    business=str(business_id or '').strip()
    page=str(page_id or '').strip()
    if not business.isdigit() or not page.isdigit():
        return False
    variables=meta.get('variables') if isinstance(meta.get('variables'),dict) else {}
    try:
        encoded=json.dumps(variables,ensure_ascii=False,separators=(',',':'))
    except Exception:
        return False
    if business not in encoded or page not in encoded:
        return False

    friendly=str(meta.get('friendly_name') or '').lower()
    raw=str(meta.get('decoded_raw') or '').lower()
    evidence=friendly+' '+raw
    if 'mutation' not in evidence:
        return False

    ownership_markers=(
        'claimpage','pageclaim','takeownership','ownership',
        'addexistingpage','addpage',
    )
    if any(marker in evidence for marker in ownership_markers):
        return False

    if not require_named_access_marker:
        return True
    access_markers=(
        'requestaccess','requestpage','sharedaccess','sharepage',
        'pageaccess','partneraccess','permission','assetaccess',
    )
    return any(marker in evidence for marker in access_markers)


async def _request_target_page_access(
    browser,
    config: dict,
    business: str,
    checkpoint,
    prior: dict,
    *,
    capture_only: bool=False,
) -> bool | dict[str, Any]:
    """Request Ads task access from the target BM; never claim Page ownership."""
    phase=str(prior.get('phase') or '')
    if phase in {
            'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
            'TARGET_PAGE_ACCESS_RK_CONFIRMED',
            'TARGET_PAGE_ACCESS_RK_PROBE_BLOCKED',
            'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
            'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
            'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
        }:
        # All of these phases exist only after the exact Page -> target BM
        # relation was durably proven. Never re-open/re-request that relation
        # while continuing RK/operator reconciliation.
        return True
    if phase in {
            'TARGET_PAGE_ACCESS_SUBMITTED',
            'TARGET_PAGE_ACCESS_PRIVATE_EXECUTE_INTENT',
            'TARGET_PAGE_ACCESS_PRIVATE_RESULT_UNKNOWN',
            'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
            'TARGET_PAGE_ACCESS_OWNER_APPROVED',
        }:
        # These phases belong to the same already-submitted request. Continue
        # owner-side reconciliation and never create a duplicate request.
        return False
    if await browser.verify_page_attached(business_id=business,page_id=config['page_id']):
        return True
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
        value=config['page_id'],
        lookup_override=page_lookup_url(
            getattr(getattr(browser,'context',None),'pages',None) or [], config['page_id'])):
        raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Shared-access Page field is unavailable',retryable=True)
    await browser.page.wait_for_timeout(900)
    if not await browser._click_exact_page_search_name(config['page_id'],config['name']):
        selection=getattr(browser,'_last_page_search_diagnostic',{}) or {}
        log.warning('PAGE_SHARE Page selection page=%s business=%s selection=%s',
            config['page_id'],business,str(selection)[:3500])
        raise BrowserBusinessError('PAGE_SHARE_PAGE_UNVERIFIED',
            'The exact shared Page search result could not be selected: '+str(selection.get('reason') or 'unverified'),
            retryable=True,diagnostic={'stage':'shared_page_search','page_search':selection})
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
    if capture_only:
        capture=GraphqlMutationCapture(
            browser.page,
            matcher=lambda request,meta: _page_share_capture_match(
                request,meta,business_id=business,
                page_id=str(config['page_id']),
                require_named_access_marker=True,
            ),
            plausible_matcher=lambda request,meta: _page_share_capture_match(
                request,meta,business_id=business,
                page_id=str(config['page_id']),
                require_named_access_marker=False,
            ),
        )
        await capture.__aenter__()
        try:
            capture.arm()
            await checkpoint({
                'activity':'TARGET_PAGE_ACCESS_CAPTURE_CLICK_INTENT',
                'requested_tasks':['ADVERTISE'],
                'page_id':config['page_id'],
                'business_id':business,
            })
            await submit.click(timeout=5000)
            try:
                row=await capture.wait(6.0)
            except asyncio.TimeoutError as exc:
                diagnostic={
                    'stage':'page_share_private_capture',
                    'blocked_unclassified':capture.blocked_unclassified,
                    'graphql_candidates':capture.candidates[-12:],
                }
                code=(
                    'PRIVATE_PAGE_SHARE_CAPTURE_UNCLASSIFIED'
                    if capture.blocked_unclassified
                    else 'PRIVATE_PAGE_SHARE_CAPTURE_NOT_FOUND'
                )
                raise BrowserBusinessError(
                    code,
                    'Meta Page-share mutation could not be captured safely',
                    retryable=True,
                    diagnostic=diagnostic,
                ) from exc
            row={
                **row,
                'source':'browser_graphql_capture',
                'observed_at':str(int(__import__('time').time())),
            }
            await checkpoint({
                'activity':'TARGET_PAGE_ACCESS_CONTRACT_CAPTURED',
                'page_id':config['page_id'],
                'business_id':business,
                'captured_friendly_name':str(
                    row.get('friendly_name') or ''
                )[:180],
                'captured_doc_id':str(row.get('doc_id') or '')[:80],
            })
            return row
        finally:
            await capture.__aexit__(None,None,None)

    await checkpoint({'phase':'TARGET_PAGE_ACCESS_CLICK_INTENT','requested_tasks':['ADVERTISE'],
        'page_id':config['page_id'],'business_id':business})
    await submit.click(timeout=5000)
    await browser.page.wait_for_timeout(1000)
    await checkpoint({'phase':'TARGET_PAGE_ACCESS_SUBMITTED',
        'diagnostic':await browser._diagnostic('target_page_access_submit_response')})
    return await browser.verify_page_attached(business_id=business,page_id=config['page_id'])


async def _execute_private_page_share_request(
    session: Any,
    *,
    store: PrivatePageShareContractStore,
    contract,
    config: dict[str, Any],
    business: str,
    profile_id: str,
    checkpoint,
) -> dict[str, Any]:
    actor_id=str(
        (getattr(session.context,'cookies',{}) or {}).get('c_user')
        or ''
    ).strip()
    try:
        web=await session.facebook_web()
        # Finish authentication/bootstrap before the irreversible checkpoint.
        await web.bootstrap()
    except Exception as exc:
        raise ProvisioningError(
            'PRIVATE_PAGE_SHARE_AUTH_PRECHECK',
            (
                'Private Page-share session is not ready before submit: '
                f'{exc.__class__.__name__}'
            ),
            retryable=True,
        ) from exc

    await checkpoint({
        'phase':'TARGET_PAGE_ACCESS_PRIVATE_EXECUTE_INTENT',
        'requested_tasks':['ADVERTISE'],
        'page_id':str(config.get('page_id') or ''),
        'business_id':business,
        'transport':'facebook_private_graphql',
        'doc_id':str(getattr(contract,'doc_id',''))[:80],
        'friendly_name':str(
            getattr(contract,'friendly_name','')
        )[:180],
    })
    try:
        payload=await execute_private_page_share(
            web,
            contract,
            business_id=business,
            page_id=str(config.get('page_id') or ''),
            actor_id=actor_id,
            profile_id=profile_id,
        )
    except ProvisioningError:
        raise
    except Exception as exc:
        # The checkpoint was written immediately before the POST. Never
        # replay automatically when transport fails after this point.
        await checkpoint({
            'phase':'TARGET_PAGE_ACCESS_PRIVATE_RESULT_UNKNOWN',
            'last_error_code':'PRIVATE_PAGE_SHARE_RESULT_UNKNOWN',
            'diagnostic':{
                'stage':'private_page_share_result_unknown',
                'error_type':exc.__class__.__name__,
            },
        })
        raise ProvisioningError(
            'PRIVATE_PAGE_SHARE_RESULT_UNKNOWN',
            (
                'Private Page-share request may have reached Meta; '
                'retry will reconcile before any new submit'
            ),
            retryable=True,
        ) from exc

    await checkpoint({
        'phase':'TARGET_PAGE_ACCESS_SUBMITTED',
        'requested_tasks':['ADVERTISE'],
        'page_id':str(config.get('page_id') or ''),
        'business_id':business,
        'transport':'facebook_private_graphql',
        'private_response_keys':sorted(
            str(key) for key in payload.keys()
        )[:30] if isinstance(payload,dict) else [],
    })
    return payload


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


async def _operator_ads_proof(page: Any, uid: str) -> dict[str, Any] | None:
    """Prove the current operator has Ads task access, without accepting full control."""
    if not uid.isdigit():
        return None
    try:
        rows=await page.locator('[role="row"],[role="listitem"]').evaluate_all(r"""(rows, uid) => {
            const norm=value=>String(value||'').replace(/\s+/g,' ').trim();
            const visible=el=>{
                const r=el.getBoundingClientRect(),s=getComputedStyle(el);
                return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden';
            };
            const identity=text=>/(^|\W)You(\W|$)/i.test(text)
                || new RegExp('(^|\\D)'+uid+'(\\D|$)').test(text);
            return rows.filter(visible).filter(el=>!el.closest('[role="dialog"],[aria-modal="true"]'))
                .map(el=>norm(el.innerText||el.textContent||''))
                .filter(text=>text.length<=1200&&identity(text));
        }""",uid)
    except Exception:
        return None
    exact=[]
    for text in rows if isinstance(rows,list) else []:
        value=' '.join(str(text or '').split())
        has_ads=bool(re.search(r'(^|\W)(Ads|Manage ads|Create ads)(\W|$)',value,re.I))
        full=bool(re.search(r'full control|manage everything|ownership',value,re.I))
        if has_ads and not full:
            exact.append(value)
    if len(exact)!=1:
        return None
    return {
        'source':'exact_page_people_ads_access',
        'operator_uid':uid,
        'row':exact[0][:1200],
    }


async def _assign_operator(
    browser, config: dict, business: str, checkpoint, *, relation_preconfirmed: bool=False,
    prior: dict[str, Any] | None=None,
) -> dict[str, Any]:
    prior=prior if isinstance(prior,dict) else {}
    uid=str((getattr(browser.context,'cookies',{}) or {}).get('c_user') or '')
    if not uid.isdigit():
        raise BrowserBusinessError(
            'PAGE_OPERATOR_IDENTITY_UNAVAILABLE',
            'The current Facebook profile identity is unavailable',
            retryable=True,
        )

    if not relation_preconfirmed:
        verified=await browser.verify_page_attached(
            business_id=business,page_id=config['page_id'])
        if not verified:
            raise BrowserBusinessError(
                'PAGE_OPERATOR_ASSIGNMENT_REQUIRED',
                'Target BM Page access is unconfirmed',
                retryable=True,
            )

    selection=await _select_page(browser,config['name'],config['page_id'],business)
    log.info(
        'PAGE_OPERATOR exact Page selected page=%s business=%s source=%s',
        str(config.get('page_id') or ''),business,str(selection.get('source') or ''),
    )

    proof=await _operator_ads_proof(browser.page,uid)
    if proof is not None:
        await checkpoint({
            'phase':'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
            'requested_tasks':['ADVERTISE'],
            'page_id':str(config.get('page_id') or ''),
            'business_id':business,
            'operator_selection':selection,
            'operator_ads_access_proof':proof,
        })
        return proof

    phase=str(prior.get('phase') or '')
    if phase in {
        'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
        'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
    }:
        raise BrowserBusinessError(
            'PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN',
            'The saved operator Ads assignment may have reached Meta; retry only verifies it',
            retryable=True,
        )

    assign=browser.page.get_by_role(
        'button',name=re.compile(r'^(Assign people|Add people)$',re.I)
    ).filter(visible=True)
    if not await _one(assign):
        raise BrowserBusinessError(
            'PAGE_OPERATOR_ASSIGNMENT_REQUIRED',
            'The exact Page people-assignment action is unavailable',
            retryable=True,
        )
    await assign.click(timeout=3000)
    dialog=browser.page.get_by_role('dialog').filter(visible=True)
    if not await _one(dialog):
        raise BrowserBusinessError(
            'PAGE_OPERATOR_ASSIGNMENT_REQUIRED',
            'The people-assignment dialog is not unique',
            retryable=True,
        )
    person=dialog.get_by_role(
        'checkbox',name=re.compile(r'\bYou\b',re.I)
    ).filter(visible=True)
    if not await _one(person):
        person=dialog.get_by_role(
            'checkbox',
            name=re.compile(r'(?<!\d)'+re.escape(uid)+r'(?!\d)')
        ).filter(visible=True)
    if not await _one(person):
        raise BrowserBusinessError(
            'PAGE_OPERATOR_IDENTITY_UNAVAILABLE',
            'The current profile is not uniquely identified in the assignment dialog',
            retryable=True,
        )
    await person.check(timeout=3000)
    await _ads_only(dialog)
    submit=dialog.get_by_role(
        'button',name=re.compile(r'^(Assign|Save)$',re.I)
    ).filter(visible=True)
    if not await _one(submit) or not await submit.is_enabled():
        raise BrowserBusinessError(
            'PAGE_OPERATOR_ASSIGNMENT_REQUIRED',
            'Operator Ads assignment is unavailable',
            retryable=True,
        )
    await checkpoint({
        'phase':'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
        'requested_tasks':['ADVERTISE'],
        'page_id':str(config.get('page_id') or ''),
        'business_id':business,
        'operator_selection':selection,
    })
    try:
        await submit.click(timeout=5000)
    except Exception:
        pass
    await checkpoint({
        'phase':'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
        'requested_tasks':['ADVERTISE'],
        'page_id':str(config.get('page_id') or ''),
        'business_id':business,
        'operator_selection':selection,
        'diagnostic':await browser._diagnostic('page_operator_assign_submitted'),
    })
    deadline=asyncio.get_running_loop().time()+7
    while asyncio.get_running_loop().time()<deadline:
        proof=await _operator_ads_proof(browser.page,uid)
        if proof is not None:
            break
        await asyncio.sleep(0.35)
    if proof is None:
        raise BrowserBusinessError(
            'PAGE_OPERATOR_ASSIGN_RESULT_UNKNOWN',
            'Operator Ads assignment was submitted but the exact access row is not yet confirmed',
            retryable=True,
        )
    await checkpoint({
        'phase':'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
        'requested_tasks':['ADVERTISE'],
        'page_id':str(config.get('page_id') or ''),
        'business_id':business,
        'operator_selection':selection,
        'operator_ads_access_proof':proof,
    })
    return proof


async def page_access_handler(session: Any, params: dict, snapshot: dict, **kwargs) -> dict:
    meta_transport=kwargs.get('meta_transport')
    if meta_transport is not None:
        session=meta_transport
    state=kwargs['provisioning_state']; profile=kwargs['profile_id']; item=kwargs['item_id']; scope=kwargs['scope_key']
    resolver=kwargs.get('profile_resolver')
    business=str(snapshot.get('business_id') or '')
    account=_normalize_ad_account_id(snapshot.get('ad_account_id')).removeprefix('act_')
    existing=params.get('existing_target') is True
    rk_state=await state.step(item,ProvisioningStep.AD_ACCOUNT)
    rk_result=(rk_state or {}).get('result') or {}
    rk_name=str(params.get('ad_account_name') or rk_result.get('account_name') or rk_result.get('name') or '')
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
        context_binding=_context_rk_binding(
            session.context,
            profile_id=profile,
            business_id=business,
            ad_account_id=account,
        )
        durable_binding=bool(created_binding or context_binding)
        # Recovery must accept an exact last-confirmed BM->RK relation from
        # either worker CREATE history or the resolved Workspace inventory.
        # A transient Business login gate must not erase that relation.
        if created_binding:
            verified=True
            evidence=[{
                'source':'recorded_profile_rk_create',
                'business_id':business,
                'ad_account_id':account,
            }]
        elif context_binding:
            verified=True
            evidence=[context_binding]
        else:
            verified,evidence=await _verify_expected_ad_account_in_business(
                session,
                business_id=business,
                account_name=str(params.get('ad_account_name') or ''),
                expected_ad_account_id=account,
                checks=1,
            )
        if not verified:
            negative=_rk_relation_negative_proof(
                evidence,
                expected_ad_account_id=account,
            )
            stage=(
                'business_rk_relation_unverified'
                if negative
                else 'business_rk_relation_inconclusive'
            )
            code=(
                'BUSINESS_RK_RELATION_UNVERIFIED'
                if negative
                else 'BUSINESS_RK_RELATION_INCONCLUSIVE'
            )
            message=(
                'Meta exact Business inventory does not contain this RK'
                if negative
                else (
                    'Live Meta inventory could not re-confirm the exact BM/RK '
                    'relation; the relation was not disproved'
                )
            )
            await state.checkpoint(
                item,profile,scope,ProvisioningStep.PAGE_ACCESS,
                {
                    'business_id':business,
                    'ad_account_id':account,
                    'diagnostic':{
                        'stage':stage,
                        'binding_evidence':evidence,
                    },
                },
            )
            raise ProvisioningError(code,message,retryable=True)
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'business_id':business,'ad_account_id':account,
                'inventory_binding_verified':not durable_binding,
                'created_binding_confirmed':created_binding,
                'context_binding_confirmed':bool(context_binding),
                'binding_evidence':evidence})
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
        if phase.startswith('TARGET_PAGE_ACCESS_') or phase.startswith('TARGET_PAGE_OPERATOR_'):
            current=await store.get(); grants=current.get('grants') or {}
            await store.patch(grants={**grants,business:{**grants.get(business,{}),**patch}})
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'page_id':config['page_id'],'business_id':business,'ad_account_id':account,**patch})
    await checkpoint({'diagnostic':{'stage':'target_page_access_start','business_id':business,
        'ad_account_id':account,'owner_bm_required':False,'access_mode':'shared_ads_access'}})
    try:
        legacy_full_control=params.get('access_mode')=='existing_page_full_control'
        if legacy_full_control:
            async with _PAGE_LOCK:
                config=await store.get()
                config={**config,'target_business_identity':target_business_identity}
                saved_grant=(config.get('grants') or {}).get(business) or {}
                resume_state={**saved_grant,**prior}
                async with _browser_lease(session,v8_old_space_mb=256) as browser:
                    try:
                        full_access=await ensure_existing_page_full_control(
                            browser,config,business,checkpoint,resume_state)
                        rk_full_access=await ensure_ad_account_full_control(
                            browser,business,account,rk_name,checkpoint,resume_state)
                    except Exception as exc:
                        diagnostic={
                            'stage':'existing_page_full_control',
                            'url':str(getattr(browser.page,'url','')),
                            'memory':_cgroup_memory_snapshot_mb(),
                            'v8_old_space_mb':256,
                        }
                        try:
                            diagnostic.update(await asyncio.wait_for(
                                browser._diagnostic('existing_page_full_control'),
                                timeout=3,
                            ))
                        except Exception:
                            pass
                        if isinstance(exc,BrowserBusinessError):
                            exc.diagnostic={
                                **diagnostic,
                                **(exc.diagnostic or {}),
                            }
                        else:
                            await checkpoint({'diagnostic':diagnostic})
                        raise
                await store.patch(
                    owner_business_id=business,
                    owner_business_confirmed=True,
                    ownership_phase='PAGE_OWNERSHIP_CONFIRMED',
                    name=full_access.get('page_name') or config['name'],
                )
            result={
                'page_id':config['page_id'],'page_name':config['name'],
                'business_id':business,'ad_account_id':account,
                'page_shared_to_business':True,
                'operator_ads_access_assigned':True,
                'operator_assignment':'performed',
                'ad_account_page_access_verified':False,
                'identity_verification':'not_requested',
                'rk_access_proof':{},
                **full_access,**rk_full_access,
            }
        else:
            async with _PAGE_LOCK:
                config=await store.get()
                config={**config,'target_business_identity':target_business_identity}
                saved_grant=(config.get('grants') or {}).get(business) or {}
                resume_state={**saved_grant,**prior}
                relation_proof=False
                operator_proof={}
                auth_refresh_attempted=False
                contract_store=PrivatePageShareContractStore()
                phase=str(resume_state.get('phase') or '')
                request_already_submitted=phase in {
                    'TARGET_PAGE_ACCESS_PRIVATE_EXECUTE_INTENT',
                    'TARGET_PAGE_ACCESS_PRIVATE_RESULT_UNKNOWN',
                    'TARGET_PAGE_ACCESS_SUBMITTED',
                    'TARGET_PAGE_ACCESS_OWNER_APPROVE_CLICK_INTENT',
                    'TARGET_PAGE_ACCESS_OWNER_APPROVED',
                    'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
                    'TARGET_PAGE_ACCESS_RK_CONFIRMED',
                    'TARGET_PAGE_ACCESS_RK_PROBE_BLOCKED',
                    'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
                    'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
                    'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
                }
                relation_already_confirmed=phase in {
                    'TARGET_PAGE_ACCESS_OWNER_CONFIRMED',
                    'TARGET_PAGE_ACCESS_RK_CONFIRMED',
                    'TARGET_PAGE_ACCESS_RK_PROBE_BLOCKED',
                    'TARGET_PAGE_OPERATOR_ASSIGN_CLICK_INTENT',
                    'TARGET_PAGE_OPERATOR_ASSIGN_SUBMITTED',
                    'TARGET_PAGE_OPERATOR_ASSIGN_CONFIRMED',
                }
                if relation_already_confirmed:
                    relation_proof=True
                private_contract=None
                capture_required=False
                if not request_already_submitted:
                    try:
                        private_contract=contract_store.get()
                    except ProvisioningError as exc:
                        if exc.code in {
                            'PRIVATE_PAGE_SHARE_CONTRACT_REQUIRED',
                            'PRIVATE_PAGE_SHARE_CONTRACT_STALE',
                        }:
                            capture_required=True
                        else:
                            raise

                # Once a contract has been captured, normal requests leave
                # Chromium entirely and execute through FacebookWebSession.
                if private_contract is not None:
                    await _execute_private_page_share_request(
                        session,
                        store=contract_store,
                        contract=private_contract,
                        config=config,
                        business=business,
                        profile_id=profile,
                        checkpoint=checkpoint,
                    )
                    resume_state={
                        **resume_state,
                        'phase':'TARGET_PAGE_ACCESS_SUBMITTED',
                        'transport':'facebook_private_graphql',
                    }
                    request_already_submitted=True

                for browser_attempt in range(2):
                    try:
                        async with _browser_lease(session,v8_old_space_mb=256) as browser:
                            # Browser is now only capture/reconcile/fallback.
                            await browser._goto(
                                'https://www.facebook.com/',
                                timeout_ms=10000,
                                wait_until='commit',
                                settle_ms=450,
                                attempts=1,
                            )
                            if capture_required and not request_already_submitted:
                                captured=await _request_target_page_access(
                                    browser,config,business,checkpoint,resume_state,
                                    capture_only=True,
                                )
                                actor_id=str(
                                    (
                                        getattr(session.context,'cookies',{})
                                        or {}
                                    ).get('c_user') or ''
                                ).strip()
                                private_contract=contract_store.register_capture(
                                    captured,
                                    business_id=business,
                                    page_id=str(config.get('page_id') or ''),
                                    actor_id=actor_id,
                                )
                                await checkpoint({
                                    'activity':'TARGET_PAGE_ACCESS_PRIVATE_CONTRACT_REGISTERED',
                                    'transport':'browser_capture_then_private_graphql',
                                    'captured_doc_id':private_contract.doc_id,
                                    'captured_friendly_name':
                                        private_contract.friendly_name[:180],
                                })
                                await _execute_private_page_share_request(
                                    session,
                                    store=contract_store,
                                    contract=private_contract,
                                    config=config,
                                    business=business,
                                    profile_id=profile,
                                    checkpoint=checkpoint,
                                )
                                resume_state={
                                    **resume_state,
                                    'phase':'TARGET_PAGE_ACCESS_SUBMITTED',
                                    'transport':'facebook_private_graphql',
                                }
                                request_already_submitted=True

                            # Submitted private requests continue only with
                            # owner-side reconciliation; never re-submit.
                            if not relation_proof:
                                relation_proof=await _approve_owner_page_access(
                                    browser,config,business,checkpoint)
                            if not relation_proof:
                                raise BrowserBusinessError(
                                    'TARGET_PAGE_ACCESS_APPROVAL_REQUIRED',
                                    'Meta has not confirmed the exact Page Ads access for this Business',
                                    retryable=True,
                                )
                            operator_proof=await _assign_operator(
                                browser,config,business,checkpoint,
                                relation_preconfirmed=True,
                                prior=resume_state,
                            )
                        break
                    except BrowserBusinessError as exc:
                        if (
                            exc.code=='BUSINESS_LOGIN_GATE'
                            and browser_attempt==0
                            and resolver is not None
                        ):
                            try:
                                changed=await asyncio.wait_for(
                                    refresh_saved_auth_context(
                                        resolver,session.context
                                    ),
                                    timeout=5.0,
                                )
                            except Exception:
                                changed=False
                            auth_refresh_attempted=True
                            if changed:
                                await checkpoint({
                                    'activity':'PAGE_ACCESS_AUTH_CONTEXT_REFRESHED',
                                    'auth_refresh_retry':True,
                                })
                                current=await store.get()
                                resume_state={
                                    **((current.get('grants') or {}).get(business) or {}),
                                    **((await state.step(
                                        item,ProvisioningStep.PAGE_ACCESS
                                    )) or {}).get('result',{}),
                                }
                                continue
                        diagnostic={
                            'stage':'shared_page_access',
                            'auth_refresh_attempted':auth_refresh_attempted,
                            **(exc.diagnostic or {}),
                        }
                        exc.diagnostic=diagnostic
                        raise

            result={
                'page_id':config['page_id'],
                'page_name':config['name'],
                'business_id':business,
                'ad_account_id':account,
                'page_shared_to_business':True,
                'operator_ads_access_assigned':True,
                'operator_assignment':'performed',
                'operator_ads_access_proof':operator_proof,
                'ad_account_page_access_verified':False,
                'identity_verification':'not_requested',
                'access_mode':'shared_ads_access',
                'transport':'target_business_page_advertising_access',
            }
        # Ads Manager / ad-form identity verification is explicitly opt-in.
        # Normal provisioning must not enter that surface.
        if params.get('verify_identity') is not True:
            return result
        await checkpoint({'phase':'VERIFY_AD_IDENTITY','page_shared_to_business':True})
        progress={}
        async with _browser_lease(session, v8_old_space_mb=256) as browser:
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
