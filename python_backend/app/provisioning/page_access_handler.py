"""Prepare one shared Page after the RK creation checkpoint, then prove Identity."""
from __future__ import annotations
import asyncio
import re
from typing import Any

from ..facebook_business_browser import FacebookBusinessBrowser, BrowserBusinessError
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


async def _share_partner(browser, config: dict, business: str, checkpoint, prior: dict) -> None:
    page_id=config['page_id']
    if await browser.verify_page_attached(business_id=business,page_id=page_id): return
    if prior.get('phase') in {'PARTNER_SHARE_CLICK_INTENT','PARTNER_SHARE_SUBMITTED'}:
        raise BrowserBusinessError('PAGE_SHARE_RESULT_UNKNOWN','Saved Page-share submission must be reconciled before retry',retryable=True)
    owner=config['owner_business_id']
    if not await browser.verify_page_attached(business_id=owner,page_id=page_id):
        raise BrowserBusinessError('COMMON_PAGE_OWNER_RELATION_MISSING','Owner portfolio does not expose the advertising Page',retryable=True)
    await _select_page(browser,config['name'])
    assign=browser.page.get_by_role('button',name=re.compile(r'^Assign partners?$',re.I))
    if not await _one(assign): raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Assign partner is unavailable',retryable=True)
    await assign.click(timeout=3000)
    by_id=browser.page.get_by_text('Business ID',exact=True)
    if await _one(by_id): await by_id.click(timeout=2500)
    dialog=browser.page.get_by_role('dialog')
    if await dialog.count()!=1: raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Partner dialog is not unique',retryable=True)
    target=dialog.get_by_role('textbox',name=re.compile(r'business.*id',re.I))
    if await target.count()!=1: target=dialog.get_by_placeholder(re.compile(r'business.*id',re.I))
    if await target.count()!=1: raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Partner Business ID field is unavailable',retryable=True)
    await target.fill(business,timeout=3000)
    advance=dialog.get_by_role('button',name='Next',exact=True)
    if await _one(advance): await advance.click(timeout=3000)
    await _ads_only(dialog)
    submit=dialog.get_by_role('button',name=re.compile(r'^(Assign|Save|Confirm)$',re.I))
    if not await _one(submit): raise BrowserBusinessError('PAGE_SHARE_UI_UNAVAILABLE','Partner final action is unavailable',retryable=True)
    await checkpoint({'phase':'PARTNER_SHARE_CLICK_INTENT','page_id':page_id,'business_id':business,'requested_tasks':['ADVERTISE']})
    await submit.click(timeout=5000)
    await checkpoint({'phase':'PARTNER_SHARE_SUBMITTED'})
    if not await browser.verify_page_attached(business_id=business,page_id=page_id):
        raise BrowserBusinessError('PAGE_SHARE_RESULT_UNKNOWN','Target portfolio Page relation is not confirmed',retryable=True)
    await checkpoint({'phase':'PARTNER_SHARE_CONFIRMED','page_shared_to_business':True})


async def _assign_operator(browser, config: dict, business: str) -> None:
    # Partner administrators do not automatically receive the Ads task on a Page.
    if not await browser.verify_page_attached(business_id=business,page_id=config['page_id']): return
    await _select_page(browser,config['name'])
    assign=browser.page.get_by_role('button',name=re.compile(r'^(Assign people|Add people)$',re.I))
    if not await _one(assign): return
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
        verified,evidence=await _verify_expected_ad_account_in_business(session,
            business_id=business,account_name=str(params.get('ad_account_name') or ''),
            expected_ad_account_id=account,checks=1)
        if not verified:
            raise ProvisioningError('BUSINESS_RK_RELATION_UNVERIFIED','Meta did not confirm this exact RK in the requested Business Portfolio',retryable=True)
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'business_id':business,'ad_account_id':account,'inventory_binding_verified':True,'binding_evidence':evidence})
    else:
        rk_state=await state.step(item,ProvisioningStep.AD_ACCOUNT)
        result=(rk_state or {}).get('result') or {}
        if str(result.get('business_id') or '')!=business or _normalize_ad_account_id(result.get('ad_account_id')).removeprefix('act_')!=account:
            raise ProvisioningError('CREATED_BUSINESS_RK_REQUIRED','RK creation result does not match this portfolio')
    await ensure_common_page(session,{},state,resolver)
    store=AdvertisingPageStore(state); config=await store.get()
    prior=((await state.step(item,ProvisioningStep.PAGE_ACCESS)) or {}).get('result') or {}
    async def checkpoint(patch):
        phase=str(patch.get('phase') or '')
        if phase.startswith('PARTNER_SHARE_'):
            current=await store.get(); grants=current.get('grants') or {}
            await store.patch(grants={**grants,business:{**grants.get(business,{}),**patch}})
        await state.checkpoint(item,profile,scope,ProvisioningStep.PAGE_ACCESS,
            {'page_id':config['page_id'],'business_id':business,'ad_account_id':account,**patch})
    try:
        async with _PAGE_LOCK:
            config=await store.get()
            if not config.get('owner_business_id'):
                if profile!=config['owner_profile_id']:
                    raise ProvisioningError('COMMON_PAGE_OWNER_BUSINESS_REQUIRED','First create a BM and RK on the advertising Page owner profile',retryable=True)
                config=await store.patch(owner_business_id=business)
            owner_context=session.context if profile==config['owner_profile_id'] else await resolver.resolve(config['owner_profile_id'])
            async with FacebookBusinessBrowser(owner_context,v8_old_space_mb=256) as browser:
                try:
                    owner=config['owner_business_id']; page_id=config['page_id']
                    if not await browser.verify_page_attached(business_id=owner,page_id=page_id):
                        if config.get('ownership_phase') in {'PAGE_ADD_CLICK_INTENT','PAGE_ADD_SUBMITTED','PAGE_ADD_RESULT_UNKNOWN'}:
                            # Read the owner portfolio's Requests screen before
                            # deciding whether a saved claim is still pending.
                            # Never send another ownership request on this path.
                            requests=browser.page.get_by_text('Requests',exact=True).filter(visible=True)
                            if await requests.count()==1:
                                # The long settings sidebar can place this
                                # link beneath its sticky footer. Follow its
                                # observed href rather than force a click.
                                href=await requests.evaluate("e => (e.closest('a') || e.closest('[role=listitem]')?.querySelector('a'))?.href || ''")
                                if href.startswith('https://business.facebook.com/'):
                                    await browser._goto(href,timeout_ms=12000,wait_until='domcontentloaded',settle_ms=1800,attempts=1)
                                    sent=browser.page.get_by_role('tab',name='Sent',exact=True).filter(visible=True)
                                    if await sent.count()==1:
                                        await sent.click(timeout=3000)
                                        await browser.page.wait_for_timeout(1000)
                            diagnostic=await browser._diagnostic('owner_page_claim_reconciliation')
                            raise BrowserBusinessError('PAGE_ATTACH_RESULT_UNKNOWN','Owner Page claim needs reconciliation',retryable=True,diagnostic=diagnostic)
                        async def owner_checkpoint(patch):
                            if patch.get('phase'): await store.patch(ownership_phase=patch['phase'])
                            await checkpoint(patch)
                        await browser.add_existing_page(business_id=owner,page_id=page_id,page_name=config['name'],before_submit=owner_checkpoint)
                        await store.patch(ownership_phase='PAGE_ATTACHED')
                    await store.patch(owner_business_confirmed=True)
                    if business!=owner:
                        saved_grant=(config.get('grants') or {}).get(business) or {}
                        await _share_partner(browser,config,business,checkpoint,saved_grant or prior)
                except BrowserBusinessError as exc:
                    exc.diagnostic={**(exc.diagnostic or {}),'surface':str(await browser._body_text())[:1800]}
                    raise
        if profile!=config['owner_profile_id']:
            async with FacebookBusinessBrowser(session.context,v8_old_space_mb=256) as browser:
                await _assign_operator(browser,config,business)
        if params.get('verify_identity') is not True:
            return {'page_id':config['page_id'],'page_name':config['name'],'business_id':business,
                'ad_account_id':account,'page_shared_to_business':True,'ad_account_page_access_verified':False,
                'identity_verification':'not_requested','transport':'business_page_advertise_share_ui'}
        await checkpoint({'phase':'VERIFY_AD_IDENTITY','page_shared_to_business':True})
        async with FacebookBusinessBrowser(session.context,v8_old_space_mb=128) as browser:
            proof=await asyncio.wait_for(inspect_browser_pages(browser,account,business),timeout=55)
        exact=[p for p in proof.get('data',[]) if p.get('id')==config['page_id'] and p.get('ad_account_page_access_verified') is True]
        if not exact:
            await checkpoint({'phase':'PAGE_IDENTITY_UNVERIFIED','page_shared_to_business':True,'diagnostic':proof.get('diagnostic') or {}})
            raise ProvisioningError('PAGE_IDENTITY_UNVERIFIED','Existing Page could not be verified in this RK advertising Identity form',retryable=True)
        return {'page_id':config['page_id'],'page_name':config['name'],'business_id':business,
            'ad_account_id':account,'page_shared_to_business':True,'ad_account_page_access_verified':True,
            'verification':proof,'transport':'business_page_advertise_share_and_identity_ui'}
    except BrowserBusinessError as exc:
        await checkpoint({'last_error_code':exc.code,'diagnostic':{'stage':'page_access',**(exc.diagnostic or {})}})
        raise ProvisioningError(exc.code,str(exc),retryable=exc.retryable) from exc
