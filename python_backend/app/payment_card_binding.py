"""Profile-bound Meta card form, with no secrets in jobs, logs or diagnostics."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import traceback
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, parse_qs
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .facebook_business_browser import BrowserBusinessError
from .payment_inspection import account_id, masked_payment_methods, payment_summary, inspect_payment_methods, select_settings_payment_tab, selected_payment_pane_text, settings_payment_summary, selected_payment_asset, _resolve_payment_account_name
from .payment_inspection import resolve_payment_asset

ALLOWED_HOSTS = {'business.facebook.com', 'www.facebook.com', 'adsmanager.facebook.com', 'secure.facebook.com'}
FIELD_PATTERNS = {
    'number': r'card number|номер карт|номер карти',
    'holder': r'name on card|cardholder|card holder|имя.*карт|власник карт',
    'expiry': r'expir|expiry|valid thru|срок действия|термін дії|mm\s*/\s*yy',
    'month': r'^month$|^месяц$|^місяць$',
    'year': r'^year$|^год$|^рік$',
    'cvv': r'cvv|cvc|security code|код безопасности|код безпеки',
    'country': r'country|страна|країна',
    'address': r'^address(?: line 1)?$|street address|billing address|адрес|адреса',
    'city': r'^city$|город|місто',
    'region': r'^state$|province|region|область',
    'postal_code': r'postal|zip|индекс|індекс',
}
AUTOCOMPLETE = {'cc-number':'number','cc-name':'holder','cc-exp':'expiry','cc-exp-month':'month',
                'cc-exp-year':'year','cc-csc':'cvv','country':'country','country-name':'country',
                'address-line1':'address','address-level2':'city','address-level1':'region','postal-code':'postal_code'}

FINANCIAL_ACTION = re.compile(r'verify card|verification charge|temporary (?:charge|hold|authorization)|pay now|make payment|top up|add funds|пополн|оплатить|проверочн.*списан',re.I)
TERMS_ACCEPTANCE = re.compile(r'by (?:clicking|continuing|saving|adding)[^\n]{0,180}(?:agree|accept)|нажимая[^\n]{0,180}(?:соглас|принима)|натискаючи[^\n]{0,180}(?:погодж|прийма)',re.I)


def form_action_guard(text: str, fields: list[dict[str,Any]]) -> str:
    if FINANCIAL_ACTION.search(text):return 'PAYMENT_FINANCIAL_ACTION_REQUIRED'
    if TERMS_ACCEPTANCE.search(text) or any(f['type']=='checkbox' and not f['checked'] and
        (f['required'] or re.search(r'agree|accept|terms|соглас|принима|погодж',f.get('label',''),re.I)) for f in fields):return 'PAYMENT_TERMS_CONFIRMATION_REQUIRED'
    return ''


async def active_card_form_text(page: Any, fields: list[dict[str,Any]]) -> str:
    """Read only visible payment/card scopes, never unrelated page-wide text."""
    field=next((f for f in fields if f.get('kind')=='number' and f.get('control') is not None),None)
    if field:
        try:
            text=await field['control'].evaluate("""el => {
              const scope=el.closest('[role="dialog"],[role="alertdialog"],form');
              return scope && scope.getClientRects().length ? scope.innerText : '';
            }""")
            if isinstance(text,str) and text.strip():return text
        except Exception:
            pass

    # Before the PAN field mounts Meta may show a payment-method or setup
    # dialog. Inspect only visible dialog/form containers. Text elsewhere on
    # Business Suite (help, navigation, account summaries) must never turn a
    # safe card flow into ACTION_REQUIRED.
    try:
        scopes=page.locator('[role="dialog"]:visible,[role="alertdialog"]:visible,form:visible')
        texts=[]
        count=min(await scopes.count(),12)
        for index in range(count):
            try:
                text=await scopes.nth(index).inner_text(timeout=1200)
            except Exception:
                continue
            if not isinstance(text,str) or not text.strip():
                continue
            if re.search(
                r'payment|billing|credit|debit|card|способ(?:ы)? оплаты|оплат|карт|платіж',
                text,
                re.I,
            ):
                texts.append(text.strip())
        if texts:
            return '\n'.join(texts[-3:])[:12000]
    except Exception:
        pass
    return ''


def field_kind(label: str, autocomplete: str = '') -> str:
    if autocomplete in AUTOCOMPLETE:
        return AUTOCOMPLETE[autocomplete]
    for kind, pattern in FIELD_PATTERNS.items():
        if re.search(pattern, label, re.I):
            return kind
    return ''




async def _unique_visible(scope: Any, role: str, name: str) -> Any:
    # Playwright role selectors serialize Python regexes as /pattern/flags.
    # A literal slash in Credit/debit must not terminate that serialized regex.
    candidates = scope.get_by_role(role, name=re.compile(name.replace('/', r'\/'), re.I)).filter(visible=True)
    if await candidates.count() == 1:
        return candidates
    return None


async def _payment_surface(browser: Any, stage: str) -> None:
    """Record control labels before card entry; never values or page body."""
    page=browser.page
    try:
        rows=await asyncio.wait_for(page.evaluate("""() => Array.from(document.querySelectorAll('button,[role="button"],[role="menuitem"],a[href],select,[role="combobox"],input[type="radio"],input[type="checkbox"],[role="radio"],[role="checkbox"],h1,h2,h3'))
          .filter(el=>el.getClientRects().length).slice(0,65)
          .map(el=>({role:el.getAttribute('role')||el.tagName.toLowerCase(),label:(el.getAttribute('aria-label')||Array.from(el.labels||[]).map(l=>l.innerText).join(' ')||el.innerText||'').trim()
            .replace(/(?:\\d[ -]?){12,19}/g,'[redacted]').replace(/\\d{6,}/g,'[id]').slice(0,100)})).filter(r=>r.label)"""),timeout=2)
    except Exception as exc:
        rows=[{'diagnostic_unavailable':type(exc).__name__}]
    logging.getLogger('remask.payment_card').info('payment surface profile=%s stage=%s path=%s controls=%s',
        browser.profile_id,stage,urlsplit(str(page.url)).path,rows)


async def _form_fields(page: Any) -> list[dict[str, Any]]:
    rows = []
    for frame in page.frames:
        if urlsplit(str(frame.url)).hostname not in ALLOWED_HOSTS:
            continue
        fields = frame.locator('input:visible:not([type="hidden"]),select:visible')
        for index in range(min(await fields.count(), 30)):
            control = fields.nth(index)
            info = await control.evaluate("""el => ({tag:el.tagName.toLowerCase(),type:el.type||'',
                label:(el.getAttribute('aria-label')||Array.from(el.labels||[]).map(l=>l.innerText).join(' ')||el.placeholder||'').trim(),
                autocomplete:el.autocomplete||'',required:el.required||el.getAttribute('aria-required')==='true',
                checked:el.type==='checkbox' ? el.checked : null})""")
            info['kind'] = field_kind(info['label'], info['autocomplete'])
            info['control'] = control
            rows.append(info)
    return rows


def _safe_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # No control values, HTML, body snippets, payment payloads or frame URLs.
    return [{**{k:row[k] for k in ('kind','tag','type','required')},
             'label':re.sub(r'\d{6,}', '[redacted]',row['label'])[:80]} for row in fields]


async def _open_card_form(browser: Any, target: str, asset: dict[str,str], billing_setup: dict[str,str] | None = None) -> dict[str,Any]:
    page=browser.page
    business=asset.get('business_id',''); name=asset.get('name','')
    if not business:
        return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_BINDING_MISSING'}
    name=name or target
    url=browser.SETTINGS_AD_ACCOUNTS_URLS[0].format(business_id=business)
    alias=asset.get('business_asset_id','')
    if re.fullmatch(r'\d{5,30}',alias):
        url+='&'+urlencode({'selected_asset_id':alias,'selected_asset_type':'ad-account'})
    await browser._goto(url,timeout_ms=25000,settle_ms=700,attempts=1)
    if re.fullmatch(r'(?:act_)?\d{5,30}',name):
        try:
            await page.get_by_role('link',name=re.compile(r'^(Details|Подробнее|Деталі)$',re.I)).filter(visible=True).wait_for(state='visible',timeout=6000)
            name=await _resolve_payment_account_name(page,name)
        except Exception:name=''
        if not name:return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_ROW_MISSING'}
        asset['name']=name
    try:
        # This is only a hydration signal, not account identity proof. Meta
        # renders nested rows and hidden copies of the same RK; waiting on the
        # whole collection raises a strict-mode error even when it is visible.
        # The selected pane below must still prove the exact canonical RK ID.
        await page.get_by_role('row').filter(has_text=name).filter(visible=True).first.wait_for(state='visible',timeout=6000)
    except Exception:
        return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_ROW_MISSING'}
    identity=await browser._read_selected_ad_account_identity(business_id=business,account_name=name)
    if not identity.get('confirmed') or re.sub(r'^act_','',str(identity.get('ad_account_id') or ''))!=target:
        await _payment_surface(browser,'selected_account_identity_unverified')
        observed=re.sub(r'^act_','',str(identity.get('ad_account_id') or ''))
        error_type=str(identity.get('error') or '').split(':',1)[0]
        error_type=error_type if re.fullmatch(r'[A-Za-z]{1,40}Error',error_type) else ''
        diagnostic={'stage':'selected_account_identity_unverified','identity_confirmed':identity.get('confirmed') is True,
            'observed_account_id':observed if re.fullmatch(r'\d{5,30}',observed) else '',
            'candidate_count':len(identity.get('candidates') or []),'unique_id_count':len(identity.get('unique_ids') or []),
            'identity_error_type':error_type}
        logging.getLogger('remask.payment_card').info('payment account identity profile=%s diagnostic=%s',browser.profile_id,diagnostic)
        return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED','account_scope_diagnostic':diagnostic}
    parsed=urlsplit(str(page.url))
    if parsed.hostname not in ALLOWED_HOSTS or parse_qs(parsed.query).get('business_id')!=[business]:
        await _payment_surface(browser,'selected_business_scope_unverified')
        return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED',
            'account_scope_diagnostic':{'stage':'selected_business_scope_unverified','path':parsed.path}}
    if await _selected_account_disabled(page, name):
        return {'status':'BLOCKED','code':'PAYMENT_AD_ACCOUNT_DISABLED'}
    await select_settings_payment_tab(browser)
    # Capture only masked methods visible before any card fields are entered.
    # If the new PAN has the same brand+last4, Meta cannot prove which physical
    # card exists after Save, so the financial submission must be blocked.
    preexisting_methods=[]
    try:
        preexisting_methods=masked_payment_methods(await selected_payment_pane_text(browser,name))
    except Exception:
        preexisting_methods=[]
    add=await _unique_visible(page,'button',r'^(Add payment method|Добавить способ оплаты|Додати спосіб оплати)$')
    if add is None:
        add=await _unique_visible(page,'link',r'^(Add payment method|Добавить способ оплаты|Додати спосіб оплати)$')
    if add is None:
        more=await _unique_visible(page,'button',r'^(More|Ещё|Еще|Більше)$')
        if more is not None:
            await more.click(timeout=3000)
            await page.wait_for_timeout(300)
            await _payment_surface(browser,'selected_account_more')
            for role in ('menuitem','button','link'):
                add=await _unique_visible(page,role,r'^(Add payment method|Добавить способ оплаты|Додати спосіб оплати)$')
                if add is not None:break
            if add is None:await page.keyboard.press('Escape')
    if add is None:
        await _payment_surface(browser,'selected_settings')
        # Use the existing rendered Billing navigation, with no guessed URL.
        funding=await inspect_payment_methods(browser,target,business_id=business,asset=asset,fresh_billing_context=True)
        page=browser.page
        await _payment_surface(browser,'billing_navigation')
        if not funding['account_scope_verified']:
            return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED'}
        preexisting_methods=list(funding.get('payment_methods') or [])
        add=await _unique_visible(page,'button',r'^(Add payment method|Добавить способ оплаты|Додати спосіб оплати)$')
        if add is None:
            return {'status':'BLOCKED','code':'PAYMENT_ADD_CONTROL_MISSING'}
    try:
        await add.click(timeout=4000)
    except PlaywrightTimeoutError:
        # Meta can open the modal before Playwright finishes the click.
        # Observe the same bounded dialog transition; never click Add twice.
        await _payment_surface(browser,'payment_add_click_pending')
    await browser._assert_authenticated()
    await _payment_surface(browser,'payment_method_dialog')
    form_deadline=time.monotonic()+20.0
    setup_advanced=False;method_advanced=False;setup_observed={}
    while time.monotonic()<form_deadline:
        await page.wait_for_timeout(500)
        text=await page.locator('body').inner_text(timeout=3000)
        if payment_account_setup_required(text):
            if setup_advanced:continue
            await _payment_surface(browser,'billing_setup')
            if not billing_setup:
                return {'status':'ACTION_REQUIRED','code':'PAYMENT_ACCOUNT_SETUP_REQUIRED','required_settings':['country','currency','timezone']}
            setup_result=await configure_payment_account(browser,billing_setup)
            if setup_result.get('status')!='SETUP_ADVANCED':return setup_result
            setup_observed={'billing_setup_observed':setup_result['billing_setup_observed']}
            setup_advanced=True
            form_deadline=time.monotonic()+20.0
            continue
        fields=await _form_fields(page)
        kinds={f['kind'] for f in fields}
        if 'number' in kinds:
            availability=await _set_card_account_availability(page)
            if availability.get('status')=='BLOCKED':
                await _payment_surface(browser,'card_account_availability_unverified')
                return {**setup_observed,**availability,'fields':_safe_fields(fields)}
            fields=await _form_fields(page)
            text=await active_card_form_text(page,fields)
            guard=form_action_guard(text,fields)
            if guard:return {**setup_observed,'status':'ACTION_REQUIRED','code':guard,'fields':_safe_fields(fields)}
            return {**setup_observed,'card_availability':availability['card_availability'],'status':'FORM_READY','code':'CARD_FORM_READY','fields':_safe_fields(fields),'_fields':fields,'_preexisting_methods':preexisting_methods}
        # Only advance a payment-method selection, never a funded/verification action.
        if method_advanced:continue
        radio=await _unique_visible(page,'radio',r'^(Credit or debit card|Debit or credit card|Credit/debit card|Кредитная или дебетовая карта)$')
        if radio is not None:
            await radio.check(timeout=3000)
        next_button=await _unique_visible(page,'button',r'^(Next|Далее|Далі)$')
        if next_button is None or not await next_button.is_enabled():
            # The Billing dialog mounts after the Add control has responded.
            # Continue the bounded observation, without another click.
            continue
        body=await active_card_form_text(page,fields)
        guard=form_action_guard(body,fields)
        if guard:return {**setup_observed,'status':'ACTION_REQUIRED','code':guard}
        await next_button.click(timeout=3000)
        method_advanced=True
        form_deadline=time.monotonic()+20.0
        await browser._assert_authenticated()
    await _payment_surface(browser,'card_form_not_exposed')
    return {**setup_observed,'status':'BLOCKED','code':'PAYMENT_FORM_NOT_EXPOSED'}


def payment_account_setup_required(text: str) -> bool:
    return bool(re.search(r'select location and currency|your location and currency cannot be changed once set|выберите (?:местоположение|страну) и валюту|оберіть (?:розташування|країну) і валюту', text, re.I))


async def _set_card_account_availability(page: Any) -> dict[str,str]:
    only_pattern=r'^(Only this account|Только этот аккаунт|Лише цей акаунт|Тільки цей обліковий запис)$'
    all_pattern=r'^(All accounts in this business portfolio|Все аккаунты в этом бизнес-портфолио|Усі акаунти в цьому бізнес-портфоліо)$'
    async def control(pattern: str) -> Any:
        for role in ('radio','checkbox'):
            candidate=await _unique_visible(page,role,pattern)
            if candidate is not None:return candidate
        candidate=page.get_by_label(re.compile(pattern,re.I)).filter(visible=True)
        return candidate if await candidate.count()==1 else None
    only=await control(only_pattern);all_accounts=await control(all_pattern)
    all_visible=page.get_by_text(re.compile(all_pattern,re.I)).filter(visible=True)
    if all_accounts is None and await all_visible.count():
        return {'status':'BLOCKED','code':'CARD_ACCOUNT_SCOPE_UNVERIFIED'}
    if only is None:
        visible=page.get_by_text(re.compile(only_pattern+'|'+all_pattern+r'|Which accounts can use this card\?|Какие аккаунты могут использовать эту карту|Які акаунти можуть використовувати цю картку',re.I)).filter(visible=True)
        if all_accounts is not None or await visible.count():
            return {'status':'BLOCKED','code':'CARD_ACCOUNT_SCOPE_UNVERIFIED'}
        return {'status':'SCOPE_READY','card_availability':'not_exposed'}
    state="""e => {
        if(e.matches('input[type="radio"],input[type="checkbox"]'))return e.checked;
        const checked=e.getAttribute('aria-checked');
        if(checked==='true'||checked==='false')return checked==='true';
        const inputs=e.querySelectorAll('input[type="radio"],input[type="checkbox"]');
        return inputs.length===1 ? inputs[0].checked : null;
    }"""
    if await only.evaluate(state) is not True:
        if not await only.is_enabled():return {'status':'BLOCKED','code':'CARD_ACCOUNT_SCOPE_UNVERIFIED'}
        native=await only.evaluate("e=>e.matches('input[type=\"radio\"],input[type=\"checkbox\"]')")
        try:
            if native:await only.check(timeout=9000)
            else:await only.click(timeout=9000)
        except PlaywrightTimeoutError:pass
    deadline=time.monotonic()+3.0
    while time.monotonic()<deadline:
        if await only.evaluate(state) is True and (all_accounts is None or await all_accounts.evaluate(state) is False):
            return {'status':'SCOPE_READY','card_availability':'only_this_account'}
        await asyncio.sleep(0.25)
    return {'status':'BLOCKED','code':'CARD_ACCOUNT_SCOPE_UNVERIFIED'}


async def _setup_control(page: Any, label: str, observed_default: str = '') -> Any:
    label=label.replace('/',r'\/')
    # The setup dialog is the only place where these country/currency/city
    # choices are valid. Background account search must never be used.
    for role in ('combobox','button'):
        controls=page.get_by_role(role,name=re.compile(label,re.I)).filter(visible=True)
        if await controls.count()==1:
            return controls
    # In Meta's current Billing wizard the timezone control is often a button
    # named only for its selected city (for example, "Los Angeles, America …"),
    # with no "Time zone" label in its accessible name. Resolve that exact
    # visible button before falling back to a text locator; clicking a text node
    # can miss the button handler and leave the old timezone selected.
    if observed_default:
        current=page.get_by_role('button',name=re.compile(r'^'+re.escape(observed_default)+r'$',re.I)).filter(visible=True)
        if await current.count()==1:
            return current
    label_node=page.get_by_text(re.compile(r'^(?:'+label+r')$',re.I)).filter(visible=True)
    control=None
    if await label_node.count()==1:
        for parent in ('..','../..'):
            container=label_node.locator(parent)
            # Meta's combobox has no accessible name: its visible label
            # is a child of the control itself. Descendant-only lookup
            # skips that parent and then finds both country and currency.
            if await container.evaluate("e=>e.matches('select,[role=\"combobox\"],[role=\"button\"],button')"):
                control=container;break
            candidates=container.locator('select,[role="combobox"],[role="button"],button').filter(visible=True)
            if await candidates.count()==1:control=candidates;break
    if control is None and observed_default:
        candidate=page.get_by_text(observed_default,exact=True).filter(visible=True)
        if await candidate.count()==1:control=candidate
    return control


async def _setup_choice(page: Any, label: str, choice: str, search: str, observed_default: str = '', *, picker_scope: Any = None) -> bool:
    picker_scope=picker_scope if picker_scope is not None else page
    if re.search(r'Time zone|Timezone|Часовой пояс|Часовий пояс',label,re.I):
        from .payment_timezone_picker import choose_payment_timezone
        selected, diagnostic = await choose_payment_timezone(page, picker_scope)
        picker_scope._remask_timezone_diagnostic = diagnostic
        logging.getLogger('remask.payment_card').info('billing timezone selection diagnostic=%s',diagnostic)
        return selected
    pattern=re.compile(choice,re.I)
    control=await _setup_control(page,label,observed_default)
    if control is None:
        # Meta's timezone button may expose only its selected city as its name.
        current_button=page.get_by_role('button',name=pattern).filter(visible=True)
        if await current_button.count()!=1:return False
        control=current_button
    if await control.evaluate("e=>e.tagName==='SELECT'"):
        options=await control.locator('option').evaluate_all('(es)=>es.map(e=>({value:e.value,label:e.textContent.trim()}))')
        selected=[o['value'] for o in options if pattern.search(o['label']) or o['value']==search]
        if len(set(selected))!=1:return False
        if await control.input_value()==selected[0]:return True
        if not await control.is_enabled():return False
        await control.select_option(selected[0],timeout=3000)
        return await control.input_value()==selected[0]
    # A custom picker exposes its selected value in the closed control.
    # Reopening an already matching USD/Kyiv menu can produce duplicate labels.
    if await _custom_setup_matches(control,pattern):return True
    if not await control.is_enabled():return False
    try:
        await control.click(timeout=9000)
    except PlaywrightTimeoutError as exc:
        # A click can open the picker before its transition times out.
        # Observe that one attempt; do not toggle the control a second time.
        message=str(exc)
        reason=next((code for marker,code in [('intercepts pointer events','POINTER_INTERCEPTED'),('not stable','CONTROL_MOVING'),('not enabled','CONTROL_DISABLED'),('not visible','CONTROL_HIDDEN'),('detached','CONTROL_REPLACED')] if marker in message),'TRANSITION_TIMEOUT')
        logging.getLogger('remask.payment_card').info('billing picker open pending field=%s reason=%s',label,reason)
    picker_deadline=time.monotonic()+8.0
    searched=False
    while time.monotonic()<picker_deadline:
        await asyncio.sleep(0.25)
        for role in ('option','menuitem','radio'):
            options=picker_scope.get_by_role(role,name=pattern).filter(visible=True)
            if await options.count()==1:
                try:await options.click(timeout=3000)
                except PlaywrightTimeoutError:pass
                return await _wait_setup_match(page,label,choice,search,observed_default)
        option=picker_scope.get_by_text(pattern).filter(visible=True)
        if await option.count()==1:
            try:await option.click(timeout=3000)
            except PlaywrightTimeoutError:pass
            return await _wait_setup_match(page,label,choice,search,observed_default)
        # Search only the picker, never the background Billing account search.
        popup=picker_scope.locator('[role="listbox"],[role="menu"]').filter(visible=True)
        if searched or await popup.count()!=1:continue
        for finder in (popup.get_by_placeholder(re.compile(r'search|поиск|пошук',re.I)),popup.get_by_role('textbox',name=re.compile(r'search|поиск|пошук',re.I))):
            inputs=finder.filter(visible=True)
            if await inputs.count()==1:
                await inputs.fill(search,timeout=2000);searched=True;break
    return False


async def _custom_setup_matches(control: Any, pattern: re.Pattern) -> bool:
    current=await control.inner_text(timeout=2000)
    return any(pattern.fullmatch(line.strip()) for line in re.sub(r'[\u200b-\u200d\ufeff]','',current).splitlines())


async def _wait_setup_match(page: Any, label: str, choice: str, search: str, observed_default: str) -> bool:
    selected_deadline=time.monotonic()+3.0
    while time.monotonic()<selected_deadline:
        await asyncio.sleep(0.25)
        # Re-resolve the control: timezone's accessible name changes after
        # selection, so a locator using its old city must not be reused.
        if await _setup_selected(page,label,choice,search,observed_default):return True
    return False


async def _setup_selected(scope: Any, label: str, choice: str, search: str, observed_default: str = '') -> bool:
    """Final read only proof after all dependent pickers have been changed."""
    if re.search(r'Time zone|Timezone|Часовой пояс|Часовий пояс',label,re.I):
        from .payment_timezone_picker import payment_timezone_selected
        return await payment_timezone_selected(scope)
    pattern=re.compile(choice,re.I)
    control=await _setup_control(scope,label,observed_default)
    if control is None:
        candidate=scope.get_by_role('button',name=pattern).filter(visible=True)
        if await candidate.count()!=1:return False
        control=candidate
    if await control.evaluate("e=>e.tagName==='SELECT'"):
        selected=await control.evaluate("e=>({value:e.value,label:e.selectedOptions[0]?.textContent.trim()||''})")
        return bool(selected['value'] and (selected['value']==search or pattern.fullmatch(selected['label'])))
    return await _custom_setup_matches(control,pattern)


async def _country_setting(scope: Any) -> dict[str,Any]:
    """Only the rendered country control is evidence; locale/proxy are not."""
    label=r'Country/region|Country|Страна/регион|Країна/регіон'
    control=await _setup_control(scope,label)
    if control is None:
        logging.getLogger('remask.payment_card').info('billing country observation control=missing')
        return {}
    info=await control.evaluate("""el => ({
        label:el.tagName==='SELECT' ? (el.selectedOptions[0]?.textContent||'') : (el.innerText||''),
        code:el.tagName==='SELECT' ? el.value : '',
        locked:el.disabled===true || el.matches(':disabled') || el.getAttribute('aria-disabled')==='true' || el.readOnly===true || el.getAttribute('aria-readonly')==='true'
    })""")
    lines=[line.strip() for line in re.sub(r'[\u200b-\u200d\ufeff]','',str(info.get('label') or '')).splitlines() if line.strip()]
    lines=[line for line in lines if not re.fullmatch(label,line,re.I)]
    if len(lines)!=1:return {}
    country=re.sub(r'^(?:'+label+r')\s*:\s*','',lines[0],flags=re.I).strip()
    if not country or len(country)>80 or not re.fullmatch(r"[^\W\d_][^\d<>:]{0,79}",country,re.UNICODE):return {}
    if re.search(r'select|choose|loading|search|unavailable|failed|error|try again|please wait|выберите|загрузка|ошибка|недоступ|оберіть|пошук|помилка',country,re.I):return {}
    # Native empty values are placeholders, even when they have a country-like label.
    if await control.evaluate("e=>e.tagName==='SELECT'") and not info.get('code'):return {}
    code=str(info.get('code') or '')
    return {'country_label':country,'country_code':code if re.fullmatch(r'[A-Z]{2}',code) else '', 'locked':info.get('locked') is True}


async def configure_payment_account(browser: Any, setup: dict[str,str]) -> dict[str,Any]:
    mode=setup.get('country_mode','strict')
    if any(setup.get(key)!=value for key,value in {'country':'UA','currency':'USD','timezone':'Europe/Kyiv'}.items()) or mode not in {'strict','prefer_ua','current'} or set(setup)-{'country','currency','timezone','country_mode'}:
        return {'status':'BLOCKED','code':'PAYMENT_SETUP_INVALID'}
    page=browser.page
    body=await page.locator('body').inner_text(timeout=3000)
    guard=form_action_guard(body,[])
    if guard:return {'status':'ACTION_REQUIRED','code':guard}
    dialogs=page.get_by_role('dialog').filter(visible=True).filter(has_text=re.compile(r'select location and currency|выберите (?:местоположение|страну) и валюту|оберіть (?:розташування|країну) і валюту',re.I))
    count=await dialogs.count()
    if count>1:return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','missing_fields':['country']}
    scope=dialogs if count==1 else page
    country=await _country_setting(scope)
    if not country:
        try:
            observation=await page.evaluate("""() => Array.from(document.querySelectorAll('[role="combobox"]'))
              .filter(e=>e.getClientRects().length && /Country|Страна|Країна/i.test(e.getAttribute('aria-label')||e.innerText||''))
              .map(e=>({tag:e.tagName,role:e.getAttribute('role'),label:(e.getAttribute('aria-label')||e.innerText||'').replace(/\\d{6,}/g,'[redacted]').slice(0,120),
                aria_hidden:e.closest('[aria-hidden]')?.getAttribute('aria-hidden')||'',inert:!!e.closest('[inert]'),
                inside_dialog:!!e.closest('[role="dialog"]')}))""")
            counts={role:await page.get_by_role(role,name=re.compile(r'Country|Страна|Країна',re.I)).filter(visible=True).count() for role in ('combobox','button')}
            logging.getLogger('remask.payment_card').info('billing country scope dialogs=%s page_roles=%s dom=%s',count,counts,observation)
        except Exception as exc:
            logging.getLogger('remask.payment_card').info('billing country scope diagnostic unavailable=%s',type(exc).__name__)
        return {'status':'BLOCKED','code':'PAYMENT_COUNTRY_UNVERIFIED','missing_fields':['country']}
    preferred=bool(re.fullmatch(r'Ukraine|Украина|Україна',country['country_label'],re.I))
    reason='already_selected' if preferred else 'current_requested' if mode=='current' else 'meta_control_locked' if country['locked'] else 'requested_ua'
    preserved=not preferred and (mode=='current' or (mode=='prefer_ua' and country['locked']))
    if not preferred and country['locked'] and mode=='strict':
        return {'status':'BLOCKED','code':'PAYMENT_COUNTRY_LOCKED','missing_fields':['country'],'billing_setup_observed':{**country,'country_preserved':False,'saved':False}}
    if not preferred and not preserved:
        if not await _setup_choice(scope,r'Country/region|Country|Страна/регион|Країна/регіон',r'^(Ukraine|Украина|Україна)$','Ukraine',picker_scope=page):
            await _payment_surface(browser,'billing_setup_control_missing')
            return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','missing_fields':['country']}
        country=await _country_setting(scope)
        if not country or not re.fullmatch(r'Ukraine|Украина|Україна',country['country_label'],re.I):
            return {'status':'BLOCKED','code':'PAYMENT_COUNTRY_UNVERIFIED','missing_fields':['country']}
    observed={**country,'country_preserved':preserved,'country_reason':reason,'currency':'USD','timezone':'Europe/Kyiv','saved':False}
    choices=[('currency',r'Currency|Валюта',r'^(US Dollars|USD|Доллар США|Долари США)$','USD','US Dollars'),
             ('timezone',r'Time zone|Timezone|Часовой пояс|Часовий пояс',r'^(Kyiv|Kiev|Киев|Київ)(?:\s*[,\(].*)?$','Kyiv','Los Angeles, America (GMT-07:00)')]
    for key,label,choice,search,default in choices:
        if not await _setup_choice(scope,label,choice,search,default,picker_scope=page):
            await _payment_surface(browser,'billing_setup_control_missing')
            return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','missing_fields':[key],
                'setup_diagnostic':getattr(page,'_remask_timezone_diagnostic',{}) if key=='timezone' else {}}
    for key,label,choice,search,default in choices:
        if not await _setup_selected(scope,label,choice,search,default):
            return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','missing_fields':[key]}
    # Re-read country after changing other fields; dependent pickers may reset it.
    final_country=await _country_setting(scope)
    if not final_country or (final_country['country_label'],final_country['country_code'])!=(country['country_label'],country['country_code']):
        return {'status':'BLOCKED','code':'PAYMENT_COUNTRY_UNVERIFIED','missing_fields':['country']}
    # Advance the explicitly selected settings to the card form. Never accept
    # terms, a charge, verification or card submission at this stage.
    body=await page.locator('body').inner_text(timeout=3000)
    guard=form_action_guard(body,[])
    if guard:return {'status':'ACTION_REQUIRED','code':guard}
    next_button=await _unique_visible(scope,'button',r'^(Next|Далее|Далі)$')
    if next_button is None or not await next_button.is_enabled():
        return {'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING'}
    await next_button.click(timeout=3000)
    await browser._assert_authenticated()
    await page.wait_for_timeout(500)
    # These are observed selections. Opening the next pane does not prove the
    # billing profile was persisted, and never proves a card or payment.
    return {'status':'SETUP_ADVANCED','billing_setup_observed':observed}


async def _selected_account_disabled(page: Any, name: str) -> bool:
    # Meta can expose nested/hidden table rows for one asset. Its named asset
    # button remains unique. Call only after canonical RK and BM proof.
    asset_button = page.get_by_role('button', name=re.compile(r'^'+re.escape(name)+r'(?:\s|$)')).filter(visible=True)
    button_count = await asset_button.count()
    if button_count > 1:
        return False
    candidates = [asset_button] if button_count == 1 else [page.get_by_role('row').filter(has_text=name).filter(visible=True)]
    for candidate in candidates:
        if await candidate.count() != 1:
            continue
        text = await candidate.inner_text(timeout=2000)
        lines = [line.replace('\u200b','').strip().casefold() for line in text.splitlines() if line.replace('\u200b','').strip()]
        # Never interpret Disabled in an account name or a sibling's status.
        if lines and lines[0] == name.casefold() and any(line in {
            'disabled', 'отключен', 'отключён', 'вимкнено', 'вимкнений',
        } for line in lines[1:]):
            return True
    return False


async def bank_challenge_visible(page: Any) -> bool:
    """Observe an actionable challenge, never infer one from background help text.

    Return only a boolean; challenge text, OTPs and card fields stay in the browser.
    """
    script = r"""() => {
      const visible=e=>!!e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
      const challenge=/3d[ -]?secure|verify (?:your )?card|verification code|one[ -]time (?:code|password)|bank.{0,40}authentication|подтверд.{0,40}банк|код подтверждения|код підтвердження/i;
      const action=/verify|authenticate|confirm|submit|continue|подтверд|підтверд|продолж|продовж/i;
      return Array.from(document.querySelectorAll('[role="dialog"],[role="alertdialog"],form')).some(scope=>{
        if(!visible(scope)||!challenge.test(scope.innerText||''))return false;
        return Array.from(scope.querySelectorAll('input,button,[role="button"]')).some(e=>{
          if(!visible(e)||e.disabled)return false;
          if(e.tagName==='INPUT')return !['hidden','button','submit','checkbox','radio'].includes(e.type)
            && !/^cc-/.test(e.autocomplete||'')
            && !/cvv|cvc|card number|security code/i.test([e.name,e.id,e.getAttribute('aria-label')].join(' '));
          return action.test(e.innerText||e.getAttribute('aria-label')||'');
        });
      });
    }"""
    if await page.evaluate(script) is True:
        return True
    # Issuer challenges may be hosted in a visible cross-origin iframe.
    for frame in getattr(page, 'frames', []):
        if frame == getattr(page, 'main_frame', None):continue
        try:
            element=await frame.frame_element()
            if await element.is_visible() and await frame.evaluate(script) is True:return True
        except Exception:continue
    return False


def card_brand_aliases(number: str) -> set[str]:
    """Canonical masked-brand aliases used only to prove the submitted card."""
    digits=re.sub(r'\D','',number)
    if digits.startswith('4'):return {'visa'}
    if re.match(r'^(?:5[1-5]|2(?:2[2-9]|[3-6]\d|7[01]))',digits):return {'mastercard'}
    if re.match(r'^3[47]',digits):return {'amex','americanexpress'}
    if re.match(r'^(?:6011|65|64[4-9]|622(?:12[6-9]|1[3-9]\d|[2-8]\d{2}|9[01]\d|92[0-5]))',digits):return {'discover'}
    return set()


def card_values(card: dict[str,Any],cvv: str) -> dict[str,str]:
    number=re.sub(r'[\s-]','',str(card.get('number') or ''))
    if not re.fullmatch(r'\d{12,19}',number) or not re.fullmatch(r'\d{3,4}',cvv):
        raise ValueError('CARD_DATA_INVALID')
    month=int(card.get('month') or 0);year=int(card.get('year') or 0)
    if not 1<=month<=12 or not 2000<=year<=2100:
        raise ValueError('CARD_DATA_INVALID')
    values={k:str(card.get(k) or '') for k in ('holder','country','address','city','region','postal_code')}
    values.update(number=number,month=f'{month:02d}',year=str(year),expiry=f'{month:02d}/{year%100:02d}',cvv=cvv)
    return values


def missing_card_fields(fields: list[dict[str,Any]], values: dict[str,str]) -> list[str]:
    kinds=[f['kind'] for f in fields]
    missing=[]
    for kind in ('number','cvv'):
        if kinds.count(kind)!=1:missing.append(kind)
    if kinds.count('expiry')!=1 and not (kinds.count('month')==1 and kinds.count('year')==1):missing.append('expiry')
    for field in fields:
        if field['type'] in ('checkbox','radio','submit','button'):continue
        kind=field['kind']
        if kind and kinds.count(kind)>1:missing.append(kind)
        if kind and not values.get(kind) and (field['required'] or kind=='holder'):missing.append(kind)
        if not kind and field['required']:missing.append('unknown_required_field')
    return sorted(set(missing))


async def payment_card_flow(browser:Any,target:str,asset:dict[str,str],*,operation:str,card:dict[str,Any]|None=None,cvv:str='',billing_setup:dict[str,str]|None=None) -> dict[str,Any]:
    target=account_id(target); submitted=False
    base={'profile_id':browser.profile_id,'account_id':target,'submitted':False,'funding_verified':False}
    try:
        form=await _open_card_form(browser,target,asset,billing_setup)
        fields=form.pop('_fields',[])
        preexisting_methods=form.pop('_preexisting_methods',[])
        if 'billing_setup_observed' in form:base['billing_setup_observed']=form['billing_setup_observed']
        if operation=='prepare' or form['status']!='FORM_READY':return {**base,**form}
        values=card_values(card or {},cvv)
        missing=missing_card_fields(fields,values)
        if missing:return {**base,'status':'BLOCKED','code':'CARD_BILLING_FIELDS_REQUIRED','missing_fields':missing}
        brands=card_brand_aliases(values['number'])
        mask_collision=any(
            str(method.get('last4') or '')==values['number'][-4:]
            and re.sub(r'[^a-z]','',str(method.get('type') or '').casefold()) in brands
            for method in preexisting_methods if isinstance(method,dict)
        )
        if mask_collision:
            return {**base,'status':'BLOCKED','code':'CARD_MASK_COLLISION_PREEXISTING'}
        page=browser.page
        body=await active_card_form_text(page,fields)
        guard=form_action_guard(body,fields)
        if guard:return {**base,'status':'ACTION_REQUIRED','code':guard}
        for field in fields:
            kind=field['kind'];value=values.get(kind,'')
            if not value:continue
            control=field['control']
            if field['tag']=='select':
                options=await control.locator('option').evaluate_all('(els)=>els.map(e=>({value:e.value,label:e.textContent.trim()}))')
                aliases={value.casefold()}
                if kind=='month':aliases.add(str(int(value)))
                if kind=='year':aliases.add(value[-2:])
                exact=[o['value'] for o in options if o['value'].casefold() in aliases or o['label'].casefold() in aliases]
                if len(set(exact))!=1:return {**base,'status':'BLOCKED','code':'CARD_BILLING_OPTION_UNAVAILABLE','missing_fields':[kind]}
                await control.select_option(exact[0],timeout=3000)
            else:await control.fill(value,timeout=3000)
        # Save is the only supported final control. Never click Pay/Verify/Confirm.
        save=await _unique_visible(page,'button',r'^(Save|Add card|Сохранить|Добавить карту|Зберегти)$')
        if save is None or not await save.is_enabled():return {**base,'status':'BLOCKED','code':'CARD_SAVE_CONTROL_UNAVAILABLE'}
        submitted=True
        await save.click(timeout=4000)
        # Save can finish before Meta hydrates the masked method. Observe the
        # same page once; never replay Save or enter card fields again.
        try:
            await page.wait_for_function("""last4 => {
              const text=document.body?.innerText||'';
              const masked=new RegExp('(?:[•*·●xX]{2,}|ending\\\\s+in|ends\\\\s+in)\\\\s*'+last4+'(?![0-9])','i').test(text);
              const alert=Array.from(document.querySelectorAll('[role="alert"]')).some(e=>e.getClientRects().length&&e.innerText.trim());
              return masked || alert;
            }""",arg=values['number'][-4:],timeout=12000)
        except Exception:pass
        await browser._assert_authenticated()
        if await bank_challenge_visible(page):
            return {**base,'submitted':True,'status':'ACTION_REQUIRED','code':'CARD_BANK_CONFIRMATION_REQUIRED'}
        body=await page.locator('body').inner_text(timeout=3000)
        funding=payment_summary(target,str(page.url),body)
        parsed=urlsplit(str(page.url))
        if not funding['account_scope_verified'] and parsed.hostname in ALLOWED_HOSTS and parsed.path.rstrip('/')=='/latest/settings/ad_accounts':
            identity=await browser._read_selected_ad_account_identity(business_id=asset['business_id'],account_name=asset['name'])
            await select_settings_payment_tab(browser)
            pane=await selected_payment_pane_text(browser,asset['name'])
            funding=settings_payment_summary(target,str(page.url),pane,asset=asset,identity=identity)
        number=values['number'];brands=card_brand_aliases(number)
        linked=funding['account_scope_verified'] and any(m['last4']==number[-4:] and re.sub(r'[^a-z]','',m['type'].casefold()) in brands for m in funding['payment_methods'])
        if linked:return {**base,'submitted':True,'status':'LINKED','code':'CARD_LINK_OBSERVED','funding':funding}
        # A success toast alone is not proof of linkage to this RK. No automatic resubmission.
        alerts=[]
        try:alerts=await page.get_by_role('alert').filter(visible=True).all_text_contents()
        except Exception:pass
        rejected=any(re.search(r'card (?:was |is )?declined|card (?:could not|couldn.t) be (?:added|saved)|unable to (?:add|save) (?:this |your |the )?card|карта отклонена|картку відхилено',text,re.I) for text in alerts)
        return {**base,'submitted':True,'status':'SUBMITTED_UNVERIFIED','code':'CARD_META_REJECTED' if rejected else 'CARD_LINK_NOT_VERIFIED','funding':funding,
            'diagnostic':{'stage':'card_save_observed','path':parsed.path,'account_scope_verified':funding['account_scope_verified'],'masked_method_count':len(funding['payment_methods']),'validation_error_observed':rejected}}
    except BrowserBusinessError as exc:
        result={**base,'submitted':submitted,'status':'SUBMITTED_UNVERIFIED' if submitted else 'BLOCKED','code':exc.code}
        evidence=str((exc.diagnostic or {}).get('auth_evidence') or '')
        if evidence=='checkpoint_url' and exc.code=='CHECKPOINT_REQUIRED':
            result['diagnostic']={'auth_evidence':evidence,'facebook_route':'/checkpoint'}
        elif evidence=='login_url' and exc.code=='SESSION_EXPIRED':
            result['diagnostic']={'auth_evidence':evidence,'facebook_route':'/login'}
        return result
    except Exception as exc:
        # Playwright exception messages can contain filled secrets. Never stringify them.
        # Only classify the error in memory; stack locations contain no values.
        message=str(exc)
        reason=next((code for marker,code in [('Target crashed','TARGET_CRASHED'),('Execution context was destroyed','CONTEXT_REPLACED'),('SyntaxError','EVALUATION_SYNTAX'),('strict mode violation','AMBIGUOUS_CONTROL'),('Timeout','CONTROL_TIMEOUT')] if marker in message),'BROWSER_ERROR')
        locations=[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(exc.__traceback__) if Path(frame.filename).name=='payment_card_binding.py']
        logging.getLogger('remask.payment_card').info('card flow interrupted profile=%s submitted=%s exception_type=%s reason=%s locations=%s',browser.profile_id,submitted,type(exc).__name__,reason,locations)
        return {**base,'submitted':submitted,'status':'SUBMITTED_UNVERIFIED' if submitted else 'BLOCKED','code':'CARD_BROWSER_INTERRUPTED'}


async def _profile_payment_card_execute(resolver:Any,profile:str,payload:dict[str,Any],state:Any=None) -> dict[str,Any]:
    from .session import ProfileSession
    target=account_id(payload.get('account_id',''));operation=payload.get('operation','')
    if operation not in {'prepare','bind','reconcile'}:raise ValueError('CARD_OPERATION_INVALID')
    if operation=='prepare':
        from .static_payment_card import prepare_profile_card_form
        return await prepare_profile_card_form(resolver,profile,target,state=state,
                                               asset_hint=payload.get('asset_hint'))
    asset=await resolve_payment_asset(profile,target,state,payload.get('asset_hint'))
    base={'profile_id':profile,'account_id':target,'submitted':False,'funding_verified':False}
    if not asset:return {**base,'status':'BLOCKED','code':'PAYMENT_ACCOUNT_BINDING_MISSING'}
    context=await resolver.resolve(profile)
    if asset.get('business_id')==str(context.cookies.get('c_user') or ''):
        return {**base,'status':'BLOCKED','code':'PERSONAL_AD_ACCOUNT_EXCLUDED'}
    async with ProfileSession(context) as session:
        browser=await session.facebook_business_browser()
        try:
            result=await asyncio.wait_for(payment_card_flow(browser,target,asset,operation=operation,
                card=payload.get('card'),cvv=str(payload.get('cvv') or ''),billing_setup=payload.get('billing_setup')),timeout=95)
            # A review of Meta before any card entry. Images never enter the vault
            # or jobs; text inputs are masked, while non-sensitive choice states
            # remain visible for account availability verification.
            if operation=='prepare' and result.get('code') not in {'SESSION_EXPIRED','CHECKPOINT_REQUIRED','TWO_FACTOR_REQUIRED'}:
                try:
                    if urlsplit(str(browser.page.url)).hostname in ALLOWED_HOSTS:
                        screenshot=await browser.page.screenshot(type='jpeg',quality=65,mask=[browser.page.locator('input:not([type="radio"]):not([type="checkbox"]),textarea')],timeout=4000)
                        result['ui_preview']=base64.b64encode(screenshot).decode('ascii')
                except Exception as exc:
                    result['ui_preview_unavailable']=type(exc).__name__
                    logging.getLogger('remask.payment_card').info('payment preview unavailable profile=%s exception_type=%s',profile,type(exc).__name__)
            return result
        except asyncio.TimeoutError:
            # Timeout may happen after Save; never permit blind retry.
            return {**base,'submitted':None if operation=='bind' else False,'status':'SUBMITTED_UNVERIFIED' if operation=='bind' else 'BLOCKED','code':'CARD_FLOW_TIMEOUT'}


async def profile_payment_card(resolver:Any,profile:str,payload:dict[str,Any],*,state:Any=None) -> dict[str,Any]:
    target=account_id(payload.get('account_id',''));operation=payload.get('operation','')
    if operation not in {'prepare','bind','reconcile'}:raise ValueError('CARD_OPERATION_INVALID')
    try:
        from .payment_card_service import profile_payment_card_http
        result=await asyncio.wait_for(profile_payment_card_http(resolver,profile,payload,state=state),timeout=110)
        funding=result.get('funding') if isinstance(result.get('funding'),dict) else {}
        if (
            state is not None
            and operation=='bind'
            and result.get('status')=='LINKED'
            and funding.get('account_scope_verified') is True
            and hasattr(state,'set_payment_link_state')
        ):
            await state.set_payment_link_state(
                profile,target,True,source='card_link_observed'
            )
        logging.getLogger('remask.payment_card').info(
            'payment card result profile=%s account=%s operation=%s status=%s code=%s submitted=%s',
            profile,target,operation,str(result.get('status') or '')[:40],
            str(result.get('code') or '')[:80],result.get('submitted'),
        )
        return result
    except asyncio.TimeoutError:
        result={'profile_id':profile,'account_id':target,'submitted':None if operation=='bind' else False,'funding_verified':False,
            'status':'SUBMITTED_UNVERIFIED' if operation=='bind' else 'BLOCKED','code':'CARD_FLOW_TIMEOUT'}
        logging.getLogger('remask.payment_card').info(
            'payment card result profile=%s account=%s operation=%s status=%s code=%s submitted=%s',
            profile,target,operation,result['status'],result['code'],result['submitted'],
        )
        return result

