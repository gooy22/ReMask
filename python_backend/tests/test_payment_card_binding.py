import json
import tempfile
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.payment_card_binding import _open_card_form, _payment_surface, _unique_visible, card_values, field_kind, form_action_guard, missing_card_fields, payment_card_flow, selected_payment_asset
from app.payment_inspection import settings_payment_summary

ID='123456789'
CARD={'number':'4111111111111111','month':12,'year':2099,'holder':'Fixture'}


class CardFieldTests(unittest.TestCase):
    def test_selected_payment_pane_proves_only_exact_rk_business_and_masked_card(self):
        asset={'name':'Fixture RK','business_id':'987654321','business_asset_id':'555555555'}
        identity={'confirmed':True,'ad_account_id':'act_'+ID,'business_id':asset['business_id']}
        url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321&selected_asset_id=555555555'
        text='Fixture RK Payment methods Visa •••• 1111'
        result=settings_payment_summary(ID,url,text,asset=asset,identity=identity)
        self.assertEqual(result['verification_status'],'LINKED');self.assertFalse(result['funding_verified'])
        for bad_url,bad_identity,bad_text in [(url.replace('555555555','999999999'),identity,text),(url.replace('987654321','999999999'),identity,text),(url,{**identity,'ad_account_id':'999999999'},text),(url,identity,text.replace('Fixture RK','Other RK')),(url,identity,'Fixture RK Payment methods Visa 4111111111111111')]:
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
    async def test_optional_diagnostics_failure_never_breaks_card_flow_or_logs_secret(self):
        browser=SimpleNamespace(profile_id='Fixture',page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/',evaluate=AsyncMock(side_effect=RuntimeError(CARD['number']))))
        with self.assertLogs('remask.payment_card',level='INFO') as logs:
            await _payment_surface(browser,'fixture')
        self.assertNotIn(CARD['number'],' '.join(logs.output))

    async def test_billing_fallback_never_adds_to_unverified_account(self):
        rows=SimpleNamespace(wait_for=AsyncMock())
        page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321',
            get_by_role=lambda *a,**kw:SimpleNamespace(filter=lambda **kw:rows))
        browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=AsyncMock(),
            SETTINGS_AD_ACCOUNTS_URLS=['https://business.facebook.com/latest/settings/ad_accounts/?business_id={business_id}'],
            _read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
        with patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=None)) as controls,patch('app.payment_card_binding._payment_surface',AsyncMock()),patch('app.payment_card_binding.inspect_payment_methods',AsyncMock(return_value={'account_scope_verified':False})) as inspect:
            result=await _open_card_form(browser,ID,{'business_id':'987654321','name':'Fixture RK'})
        self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED')
        self.assertEqual(controls.await_count,3);inspect.assert_awaited_once()

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
