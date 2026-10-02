import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.payment_card_binding import card_values, field_kind, missing_card_fields, payment_card_flow, selected_payment_asset

ID='123456789'
CARD={'number':'4111111111111111','month':12,'year':2099,'holder':'Fixture'}


class CardFieldTests(unittest.TestCase):
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
