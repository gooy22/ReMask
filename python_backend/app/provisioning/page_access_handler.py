"""Prepare one shared Page after the RK creation checkpoint, then prove Identity."""
from __future__ import annotations
import asyncio
import re
from typing import Any

from ..facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError, _cgroup_memory_snapshot_mb
from ..page_access_inspection import inspect_browser_pages
from .advertising_page import AdvertisingPageStore, _PAGE_LOCK, ensure_common_page
from .models import ProvisioningError, ProvisioningStep
from .ad_account_handler import _normalize_ad_account_id


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
    if prior.get('phase') in {'TARGET_PAGE_ACCESS_CLICK_INTENT','TARGET_PAGE_ACCESS_SUBMITTED',
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
    await checkpoint({'phase':'TARGET_PAGE_ACCESS_SUBMITTED'})
    return await browser.verify_page_attached(business_id=business,page_id=config['page_id'])


async def _assign_operator(browser, config: dict, business: str) -> None:
    # Partner administrators do not automatically receive the Ads task on a Page.
    if not await browser.verify_page_attached(business_id=business,page_id=config['page_id']):
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
    await ensure_common_page(session,{'page_id':params.get('page_id'),'reuse_only':params.get('reuse_only') is True,'policies_accepted':params.get('policies_accepted') is True},state,resolver)
    store=AdvertisingPageStore.for_context(state,session.context,profile); config=await store.get()
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
            saved_grant=(config.get('grants') or {}).get(business) or {}
            async with FacebookBusinessBrowser(session.context,v8_old_space_mb=256) as browser:
                try:
                    target_relation=await _request_target_page_access(browser,config,business,checkpoint,saved_grant or prior)
                    if not target_relation:
                        raise BrowserBusinessError('TARGET_PAGE_ACCESS_APPROVAL_REQUIRED',
                            'The target BM requested Ads access; approval belongs to the Facebook Page owner, not a main BM',
                            retryable=True,diagnostic=await browser._diagnostic('target_page_access_pending'))
                    await _assign_operator(browser,config,business)
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
