import asyncio
import json
import tempfile
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.payment_card_binding import _open_card_form, _resolve_payment_account_name, _selected_account_disabled, _payment_surface, _unique_visible, card_values, configure_payment_account, field_kind, form_action_guard, missing_card_fields, payment_account_setup_required, payment_card_flow, profile_payment_card, selected_payment_asset
from app.payment_inspection import settings_payment_summary, select_settings_payment_tab

ID='123456789'
CARD={'number':'4111111111111111','month':12,'year':2099,'holder':'Fixture'}


class CardFieldTests(unittest.TestCase):
    def test_initial_account_payment_setup_is_detected_before_card_entry(self):
        self.assertTrue(payment_account_setup_required('Add payment information Select location and currency Set time zone Your location and currency cannot be changed once set.'))
        self.assertFalse(payment_account_setup_required('Payment methods Add credit or debit card'))
    def test_selected_payment_pane_proves_only_exact_rk_business_and_masked_card(self):
        asset={'name':'Fixture RK','business_id':'987654321','business_asset_id':'555555555'}
        identity={'confirmed':True,'ad_account_id':'act_'+ID,'business_id':asset['business_id']}
        url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321&selected_asset_id=555555555'
        text='Fixture RK Payment methods Visa •••• 1111'
        result=settings_payment_summary(ID,url,text,asset=asset,identity=identity)
        self.assertEqual(result['verification_status'],'LINKED');self.assertFalse(result['funding_verified'])
        for bad_url,bad_identity,bad_text in [(url.replace('555555555','999999999'),identity,text),(url+'&selected_asset_id=',identity,text),(url+'&act=999999999',identity,text),(url.replace('987654321','999999999'),identity,text),(url,{**identity,'ad_account_id':'999999999'},text),(url,identity,text.replace('Fixture RK','Other RK')),(url,identity,'Fixture RK Payment methods Visa 4111111111111111')]:
            result=settings_payment_summary(ID,bad_url,bad_text,asset=asset,identity=bad_identity)
            self.assertNotEqual(result['verification_status'],'LINKED');self.assertEqual(result['payment_methods'],[])

    def test_implicit_terms_or_temporary_charge_cannot_be_submitted_as_card_save(self):
        self.assertEqual(form_action_guard('By clicking Save you agree to Payments Terms',[]),'PAYMENT_TERMS_CONFIRMATION_REQUIRED')
        self.assertEqual(form_action_guard('A temporary authorization may apply',[]),'PAYMENT_FINANCIAL_ACTION_REQUIRED')
        self.assertEqual(form_action_guard('Privacy Terms Add payment method',[]),'')

    def test_fields_use_labels_or_standard_card_autocomplete(self):
        self.assertEqual(field_kind('Card number'),'number')
        self.assertEqual(field_kind('','cc-csc'),'cvv')
        self.assertEqual(field_kind('Name on card'),'holder')
        self.assertEqual(field_kind('MM/YY'),'expiry')
        self.assertEqual(field_kind('Account number'),'')

    def test_unknown_required_missing_billing_or_ambiguous_card_fields_block(self):
        fields=[{'kind':k,'required':True,'type':'text'} for k in ('number','expiry','cvv','holder')]
        values=card_values(CARD,'123')
        self.assertEqual(missing_card_fields(fields,values),[])
        self.assertIn('holder',missing_card_fields(fields,{**values,'holder':''}))
        self.assertIn('number',missing_card_fields(fields+[fields[0]],values))
        self.assertIn('unknown_required_field',missing_card_fields(fields+[{'kind':'','required':True,'type':'text'}],values))

    def test_saved_account_binding_requires_exact_profile_canonical_id_unique_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.json'
            row={'profile':'Fixture','id':'act_'+ID,'business_id':'987654321','business_asset_id':'555555555','name':'Fixture RK'}
            path.write_text(json.dumps({'Fixture':{'ad_accounts':[row]}}))
            self.assertEqual(selected_payment_asset('Fixture',ID,path)['business_id'],'987654321')
            self.assertEqual(selected_payment_asset('Other',ID,path),{})
            self.assertEqual(selected_payment_asset('Fixture','555555555',path),{})
            path.write_text(json.dumps({'Fixture':{'ad_accounts':[row,row]}}))
            self.assertEqual(selected_payment_asset('Fixture',ID,path),{})


class CardBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_placeholder_name_is_recovered_only_from_unique_rendered_rk_row(self):
        buttons=SimpleNamespace(all_text_contents=AsyncMock(return_value=['Fixture RK\n100','1 person','Details','Open in Ads Manager','More\n\u200b','Assign people']))
        rows=SimpleNamespace(count=AsyncMock(return_value=1),get_by_role=lambda *a,**kw:buttons)
        rows.filter=lambda **kw:rows
        page=SimpleNamespace(get_by_role=lambda *a,**kw:rows)
        self.assertEqual(await _resolve_payment_account_name(page,ID),'Fixture RK')
        self.assertEqual(await _resolve_payment_account_name(page,'Known RK'),'Known RK')
        rows.count.return_value=2
        self.assertEqual(await _resolve_payment_account_name(page,ID),'')
        rows.count.return_value=1
        buttons.all_text_contents.return_value=['Fixture RK','Other RK','Details']
        self.assertEqual(await _resolve_payment_account_name(page,ID),'')

    async def test_setup_advances_only_after_all_explicit_choices_and_no_charge_or_terms(self):
        next_button=SimpleNamespace(is_enabled=AsyncMock(return_value=True),click=AsyncMock())
        page=SimpleNamespace(locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='Select location and currency Set time zone')),wait_for_timeout=AsyncMock())
        browser=SimpleNamespace(page=page,_assert_authenticated=AsyncMock())
        setup={'country':'UA','currency':'USD','timezone':'Europe/Kyiv'}
        with patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=True)) as choice,patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=next_button)):
            self.assertEqual(await configure_payment_account(browser,setup),{})
        self.assertEqual(choice.await_count,3);next_button.click.assert_awaited_once()
        next_button.click.reset_mock()
        with patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=False)),patch('app.payment_card_binding._payment_surface',AsyncMock()):
            result=await configure_payment_account(browser,setup)
        self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING');next_button.click.assert_not_awaited()
        self.assertEqual((await configure_payment_account(browser,{**setup,'country':'US'}))['code'],'PAYMENT_SETUP_INVALID')
        page.locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='By clicking Next you agree to Payments Terms'))
        with patch('app.payment_card_binding._setup_choice',AsyncMock()) as choice:
            result=await configure_payment_account(browser,setup)
        self.assertEqual(result['code'],'PAYMENT_TERMS_CONFIRMATION_REQUIRED');choice.assert_not_awaited()

    async def test_initial_billing_setup_stops_before_fields_or_next_confirmation(self):
        rows=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=1),inner_text=AsyncMock(return_value='Fixture RK\nActive'))
        rows.filter=lambda **kw:rows
        add=SimpleNamespace(click=AsyncMock(),is_enabled=AsyncMock(return_value=True))
        page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321',
            get_by_role=lambda *a,**kw:SimpleNamespace(filter=lambda **kw:rows),wait_for_timeout=AsyncMock(),
            locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='Add payment information Select location and currency Set time zone')))
        browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=AsyncMock(),_assert_authenticated=AsyncMock(),
            SETTINGS_AD_ACCOUNTS_URLS=['https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}'],
            _read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
        with patch('app.payment_card_binding._resolve_payment_account_name',AsyncMock(return_value='Fixture RK')),patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()),patch('app.payment_card_binding._payment_surface',AsyncMock()),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=add)) as controls,patch('app.payment_card_binding._form_fields',AsyncMock()) as fields:
            result=await payment_card_flow(browser,ID,{'business_id':'987654321','name':''},operation='prepare')
        self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SETUP_REQUIRED');self.assertFalse(result['submitted'])
        self.assertEqual(result['required_settings'],['country','currency','timezone']);fields.assert_not_awaited()
        add.click.assert_awaited_once();self.assertEqual(controls.await_count,1)

    async def test_whole_operation_timeout_cancels_setup_and_never_claims_unsubmitted_bind(self):
        original_wait=asyncio.wait_for
        for operation,expected in [('prepare','BLOCKED'),('bind','SUBMITTED_UNVERIFIED')]:
            cancelled=[]
            async def setup(*args):
                try:await asyncio.Event().wait()
                finally:cancelled.append(True)
            async def short_wait(task,timeout):
                self.assertEqual(timeout,110)
                return await original_wait(task,0.01)
            with patch('app.payment_card_binding._profile_payment_card_execute',setup),patch('app.payment_card_binding.asyncio.wait_for',short_wait):
                result=await profile_payment_card(None,'Fixture',{'account_id':ID,'operation':operation})
            self.assertEqual(result['status'],expected);self.assertEqual(result['code'],'CARD_FLOW_TIMEOUT')
            self.assertEqual(cancelled,[True]);self.assertFalse(result['funding_verified'])
            if operation=='bind':self.assertIsNone(result['submitted'])

    async def test_optional_diagnostics_failure_never_breaks_card_flow_or_logs_secret(self):
        browser=SimpleNamespace(profile_id='Fixture',page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/',evaluate=AsyncMock(side_effect=RuntimeError(CARD['number']))))
        with self.assertLogs('remask.payment_card',level='INFO') as logs:
            await _payment_surface(browser,'fixture')
        self.assertNotIn(CARD['number'],' '.join(logs.output))

    async def test_billing_fallback_never_adds_to_unverified_account(self):
        rows=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=1),inner_text=AsyncMock(return_value='Fixture RK Active'))
        rows.filter=lambda **kw:rows
        page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321',
            get_by_role=lambda *a,**kw:SimpleNamespace(filter=lambda **kw:rows))
        browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=AsyncMock(),
            SETTINGS_AD_ACCOUNTS_URLS=['https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}'],
            _read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
        with patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=None)) as controls,patch('app.payment_card_binding._payment_surface',AsyncMock()),patch('app.payment_card_binding.inspect_payment_methods',AsyncMock(return_value={'account_scope_verified':False})) as inspect:
            result=await _open_card_form(browser,ID,{'business_id':'987654321','name':'Fixture RK'})
        self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED')
        self.assertEqual(controls.await_count,3);inspect.assert_awaited_once()

    async def test_disabled_exact_account_stops_before_payment_navigation_or_entry(self):
        rows=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=1),inner_text=AsyncMock(return_value='Fixture RK\nDisabled\nDisabled\n--'))
        rows.filter=lambda **kw:rows
        page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321',get_by_role=lambda *a,**kw:SimpleNamespace(filter=lambda **kw:rows))
        browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=AsyncMock(),SETTINGS_AD_ACCOUNTS_URLS=['https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}'],_read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
        with patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()) as tab,patch('app.payment_card_binding.inspect_payment_methods',AsyncMock()) as inspect:
            result=await _open_card_form(browser,ID,{'business_id':'987654321','name':'Fixture RK'})
        self.assertEqual(result['code'],'PAYMENT_AD_ACCOUNT_DISABLED');tab.assert_not_awaited();inspect.assert_not_awaited()

    async def test_nested_rows_use_unique_asset_button_and_reject_ambiguous_or_name_status(self):
        rows=SimpleNamespace(count=AsyncMock(return_value=2))
        rows.filter=lambda **kw:rows
        button=SimpleNamespace(count=AsyncMock(return_value=1),inner_text=AsyncMock(return_value='Fixture RK\nDisabled\nDisabled\n--'))
        button.filter=lambda **kw:button
        page=SimpleNamespace(get_by_role=lambda role,**kw:rows if role=='row' else button)
        self.assertTrue(await _selected_account_disabled(page,'Fixture RK'))
        button.count.return_value=2
        self.assertFalse(await _selected_account_disabled(page,'Fixture RK'))
        button.count.return_value=1
        for text in ('Fixture RK Disabled\nActive', 'Fixture RK sibling\nDisabled', 'Fixture RK\nActive'):
            button.inner_text.return_value=text
            self.assertFalse(await _selected_account_disabled(page,'Fixture RK'))

    def browser(self,body='Payment methods'):
        save=SimpleNamespace(is_enabled=AsyncMock(return_value=True),click=AsyncMock())
        page=SimpleNamespace(url='https://business.facebook.com/billing_hub/payment_settings?asset_id='+ID,
            locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value=body)),wait_for_timeout=AsyncMock())
        return SimpleNamespace(profile_id='Fixture',page=page,_assert_authenticated=AsyncMock()),save

    def fields(self):
        return [{'kind':k,'required':True,'type':'text','tag':'input','control':SimpleNamespace(fill=AsyncMock())} for k in ('number','holder','expiry','cvv')]

    async def test_preparation_does_not_fill_or_submit(self):
        browser,save=self.browser();fields=self.fields()
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','code':'CARD_FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='prepare')
        self.assertEqual(result['status'],'FORM_READY');save.click.assert_not_awaited()
        for field in fields:field['control'].fill.assert_not_awaited()
        self.assertNotIn('_fields',result)

    async def test_scope_failure_never_fills_or_submits(self):
        browser,save=self.browser()
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'BLOCKED','code':'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED'})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertFalse(result['submitted']);save.click.assert_not_awaited()

    async def test_financial_action_or_unaccepted_terms_stops_before_secret_entry(self):
        for body,checkbox in [('Verification charge',False),('Payment methods',True)]:
            browser,save=self.browser(body);fields=self.fields()
            if checkbox:fields.append({'kind':'','required':True,'type':'checkbox','tag':'input','checked':False})
            with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
                result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
            self.assertEqual(result['status'],'ACTION_REQUIRED');save.click.assert_not_awaited()
            for field in fields:
                if 'control' in field:field['control'].fill.assert_not_awaited()

    async def test_save_once_and_only_exact_masked_scope_can_confirm_linkage(self):
        for body,expected in [(ID+'\nPayment methods\nVisa •••• 1111','LINKED'),('Payment method added','SUBMITTED_UNVERIFIED')]:
            browser,save=self.browser(body);fields=self.fields()
            with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
                result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
            self.assertEqual(result['status'],expected);self.assertFalse(result['funding_verified']);save.click.assert_awaited_once()
            self.assertNotIn(CARD['number'],json.dumps(result));self.assertNotIn('Fixture',json.dumps(result.get('funding',{})))

    async def test_submit_exception_is_sanitized_and_never_retried(self):
        browser,save=self.browser();fields=self.fields();save.click.side_effect=RuntimeError(CARD['number']+' cvv fixture')
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED');self.assertTrue(result['submitted']);save.click.assert_awaited_once()
        self.assertNotIn(CARD['number'],json.dumps(result));self.assertNotIn('cvv fixture',json.dumps(result))


class RealCardSelectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_country_currency_timezone_choices_use_requested_values(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content('<label>Country/region<select id="country"><option value="BD">Bangladesh</option><option value="UA">Ukraine</option></select></label><label>Currency<select id="currency"><option value="BDT">BDT</option><option value="USD">US Dollars</option></select></label><label>Time zone<select id="zone"><option value="America/Los_Angeles">Los Angeles, America (GMT-07:00)</option><option value="Europe/Kyiv">Kyiv, Europe (GMT+03:00)</option></select></label><button onclick="this.dataset.advanced=\'yes\'">Next</button>')
                browser=SimpleNamespace(page=page,_assert_authenticated=AsyncMock())
                self.assertEqual(await configure_payment_account(browser,{'country':'UA','currency':'USD','timezone':'Europe/Kyiv'}),{})
                self.assertEqual(await page.locator('#country').input_value(),'UA')
                self.assertEqual(await page.locator('#currency').input_value(),'USD')
                self.assertEqual(await page.locator('#zone').input_value(),'Europe/Kyiv')
                self.assertEqual(await page.get_by_role('button',name='Next',exact=True).get_attribute('data-advanced'),'yes')
            finally:await chromium.close()

    async def test_nested_meta_rows_disabled_button_without_false_sibling_status(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await browser.new_page()
                await page.set_content('<div role="row"><div role="row"><button>Fixture RK<br>Disabled<br>Disabled<br>--</button></div></div><div role="row" style="display:none">Fixture RK Disabled</div>')
                self.assertTrue(await _selected_account_disabled(page,'Fixture RK'))
                await page.set_content('<div role="row"><button>Fixture RK<br>Active</button><button>Other RK<br>Disabled</button></div>')
                self.assertFalse(await _selected_account_disabled(page,'Fixture RK'))
            finally:await browser.close()

    async def test_payment_tab_waits_for_lazy_add_method_control(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content('<button role="tab" onclick="setTimeout(()=>document.getElementById(\'add\').style.display=\'block\',250)">Payment methods</button><button id="add" style="display:none">Add payment method</button>')
                browser=SimpleNamespace(page=page,_assert_authenticated=AsyncMock())
                self.assertTrue(await select_settings_payment_tab(browser))
                self.assertTrue(await page.get_by_role('button',name='Add payment method',exact=True).is_visible())
                browser._assert_authenticated.assert_awaited_once()
            finally:await chromium.close()

    async def test_credit_debit_label_is_resolved_by_real_playwright_selector_parser(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await browser.new_page()
                await page.set_content('<label><input type="radio" name="method">Credit/debit card</label><button>Add payment method</button>')
                radio=await _unique_visible(page,'radio',r'^(Credit or debit card|Credit/debit card)$')
                self.assertIsNotNone(radio)
                await radio.check()
                self.assertTrue(await radio.is_checked())
                await page.set_content('<label><input type="radio">Credit/debit card</label><label><input type="radio">Credit/debit card</label>')
                self.assertIsNone(await _unique_visible(page,'radio',r'^Credit/debit card$'))
            finally:await browser.close()
