import asyncio
import json
import tempfile
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.payment_card_binding import _open_card_form, _resolve_payment_account_name, _selected_account_disabled, _payment_surface, _setup_control, _unique_visible, card_brand_aliases, card_values, configure_payment_account, field_kind, form_action_guard, missing_card_fields, payment_account_setup_required, payment_card_flow, profile_payment_card, selected_payment_asset
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

    def test_card_brand_aliases_match_meta_mask_labels(self):
        self.assertEqual(card_brand_aliases('4111111111111111'), {'visa'})
        self.assertEqual(card_brand_aliases('5555555555554444'), {'mastercard'})
        self.assertEqual(card_brand_aliases('378282246310005'), {'amex','americanexpress'})
        self.assertEqual(card_brand_aliases('6011111111111117'), {'discover'})
        self.assertEqual(card_brand_aliases('3530111333300000'), set())

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
    async def test_timezone_setup_resolves_current_city_button_by_accessible_name(self):
        empty=SimpleNamespace(count=AsyncMock(return_value=0))
        empty.filter=lambda **kwargs:empty
        current=SimpleNamespace(count=AsyncMock(return_value=1))
        current.filter=lambda **kwargs:current
        def get_by_role(role,name):
            if role=='button' and name.fullmatch('Los Angeles, America (GMT-07:00)'):
                return current
            return empty
        page=SimpleNamespace(get_by_role=get_by_role,
            get_by_text=lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('text fallback must not run')))
        control=await _setup_control(page,r'Time zone|Timezone|Часовой пояс|Часовий пояс','Los Angeles, America (GMT-07:00)')
        self.assertIs(control,current)

    async def test_new_created_rk_can_prepare_before_inventory_sync_but_conflicts_are_blocked(self):
        from app.payment_inspection import resolve_payment_asset
        state=SimpleNamespace(confirmed_ad_account_bindings_for_profile=AsyncMock(return_value=[
            {'business_id':'987654321','ad_account_id':'act_'+ID,'account_name':'Created RK'}]))
        with patch('app.payment_inspection.selected_payment_asset',return_value={}):
            asset=await resolve_payment_asset('Fixture',ID,state)
        self.assertEqual(asset,{'business_id':'987654321','business_asset_id':'','name':'Created RK'})
        state.confirmed_ad_account_bindings_for_profile.assert_awaited_once_with('Fixture')
        with patch('app.payment_inspection.selected_payment_asset',return_value={'business_id':'987654321','business_asset_id':'','name':''}):
            merged=await resolve_payment_asset('Fixture',ID,None,{'business_id':'987654321','name':'Workspace RK'})
        self.assertEqual(merged['name'],'Workspace RK')
        with patch('app.payment_inspection.selected_payment_asset',return_value={'business_id':'987654321','business_asset_id':'','name':''}):
            merged_confirmed=await resolve_payment_asset('Fixture',ID,state,{'business_id':'987654321','name':'Workspace RK'})
        self.assertEqual(merged_confirmed['name'],'Created RK')
        with patch('app.payment_inspection.selected_payment_asset',return_value={'business_id':'555555555'}):
            self.assertEqual(await resolve_payment_asset('Fixture',ID,state),{})
        state.confirmed_ad_account_bindings_for_profile=AsyncMock(return_value=[])
        with patch('app.payment_inspection.selected_payment_asset',return_value={}):
            hinted=await resolve_payment_asset('Fixture',ID,state,{'business_id':'222222222','name':'Workspace RK'})
            self.assertEqual(hinted,{'business_id':'222222222','business_asset_id':'','name':'Workspace RK'})
            self.assertEqual(await resolve_payment_asset('Fixture',ID,state,{'business_id':'bad'}),{})
        state.confirmed_ad_account_bindings_for_profile=AsyncMock(return_value=[
            {'business_id':'987654321','ad_account_id':'act_'+ID}])
        with patch('app.payment_inspection.selected_payment_asset',return_value={}):
            self.assertEqual(await resolve_payment_asset('Fixture',ID,state,{'business_id':'222222222'}),{})

    async def test_personal_rk_is_blocked_before_any_card_browser_opens(self):
        from app.payment_card_binding import _profile_payment_card_execute
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={'c_user':'987654321'})))
        with patch('app.payment_card_binding.resolve_payment_asset',AsyncMock(return_value={'business_id':'987654321'})), \
             patch('app.session.ProfileSession') as session:
            result=await _profile_payment_card_execute(resolver,'Fixture',{'account_id':ID,'operation':'prepare'})
        self.assertEqual(result['code'],'PERSONAL_AD_ACCOUNT_EXCLUDED')
        session.assert_not_called()

    async def test_unverified_account_diagnostics_exclude_identity_error_candidate_text_and_card_data(self):
        rows=SimpleNamespace(wait_for=AsyncMock(),count=AsyncMock(return_value=1))
        rows.filter=lambda **kw:rows
        page=SimpleNamespace(url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321',
            get_by_role=lambda *a,**kw:rows)
        browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=AsyncMock(),
            SETTINGS_AD_ACCOUNTS_URLS=[page.url],_read_selected_ad_account_identity=AsyncMock())
        for confirmed,observed in [(False,''),(True,'987654320')]:
            browser._read_selected_ad_account_identity.return_value={'confirmed':confirmed,'ad_account_id':observed,
                'error':CARD['number'],'candidates':[{'text':CARD['number'],'href':CARD['number']}],'unique_ids':['999999999']}
            with patch('app.payment_card_binding._payment_surface',AsyncMock()) as surface,patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()) as payment_tab:
                with self.assertLogs('remask.payment_card',level='INFO') as logs:
                    result=await _open_card_form(browser,ID,{'business_id':'987654321','name':'Fixture RK'})
            self.assertEqual(result['code'],'PAYMENT_ACCOUNT_SCOPE_UNVERIFIED')
            self.assertEqual(result['account_scope_diagnostic']['observed_account_id'],observed)
            self.assertEqual(result['account_scope_diagnostic']['candidate_count'],1)
            self.assertNotIn(CARD['number'],json.dumps(result)+' '.join(logs.output))
            surface.assert_awaited_once_with(browser,'selected_account_identity_unverified')
            payment_tab.assert_not_awaited()

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

    async def test_matching_custom_picker_value_is_not_reopened(self):
        control=SimpleNamespace(count=AsyncMock(return_value=1),evaluate=AsyncMock(return_value=False),
            inner_text=AsyncMock(return_value='Currency\nUS Dollars'),click=AsyncMock())
        control.filter=lambda **kw:control
        page=SimpleNamespace(get_by_role=lambda *a,**kw:control)
        from app.payment_card_binding import _setup_choice
        self.assertTrue(await _setup_choice(page,'Currency',r'^(US Dollars|USD)$','USD'))
        control.click.assert_not_awaited()

    async def test_matching_custom_picker_value_in_real_chromium_does_not_click(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        from app.payment_card_binding import _setup_choice
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content('<div role="combobox" aria-label="Currency" data-clicks="0" onclick="this.dataset.clicks=Number(this.dataset.clicks)+1">Currency<br>US Dollars</div>')
                self.assertTrue(await _setup_choice(page,'Currency',r'^(US Dollars|USD)$','USD'))
                self.assertEqual(await page.get_by_role('combobox').get_attribute('data-clicks'),'0')
            finally:await chromium.close()

    async def test_setup_advances_only_after_all_explicit_choices_and_no_charge_or_terms(self):
        next_button=SimpleNamespace(is_enabled=AsyncMock(return_value=True),click=AsyncMock())
        page=SimpleNamespace(locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='Select location and currency Set time zone')),wait_for_timeout=AsyncMock())
        dialogs=SimpleNamespace(count=AsyncMock(return_value=0));dialogs.filter=lambda **kw:dialogs
        page.get_by_role=lambda *a,**kw:dialogs
        browser=SimpleNamespace(page=page,_assert_authenticated=AsyncMock())
        setup={'country':'UA','currency':'USD','timezone':'Europe/Kyiv'}
        bd={'country_label':'Bangladesh','country_code':'BD','locked':False};ua={**bd,'country_label':'Ukraine','country_code':'UA'}
        with patch('app.payment_card_binding._country_setting',AsyncMock(side_effect=[bd,ua,ua])),patch('app.payment_card_binding._setup_selected',AsyncMock(return_value=True)),patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=True)) as choice,patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=next_button)):
            result=await configure_payment_account(browser,setup)
            self.assertEqual(result['status'],'SETUP_ADVANCED');self.assertFalse(result['billing_setup_observed']['saved'])
        self.assertEqual(choice.await_count,3);next_button.click.assert_awaited_once()
        next_button.click.reset_mock()
        with patch('app.payment_card_binding._country_setting',AsyncMock(return_value=bd)),patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=False)),patch('app.payment_card_binding._payment_surface',AsyncMock()):
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

    async def test_locked_country_is_preserved_but_selector_failure_is_not_a_restriction(self):
        next_button=SimpleNamespace(is_enabled=AsyncMock(return_value=True),click=AsyncMock())
        dialogs=SimpleNamespace(count=AsyncMock(return_value=0));dialogs.filter=lambda **kw:dialogs
        page=SimpleNamespace(locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='Select location and currency')),get_by_role=lambda *a,**kw:dialogs,wait_for_timeout=AsyncMock())
        browser=SimpleNamespace(page=page,profile_id='Fixture',_assert_authenticated=AsyncMock())
        setup={'country':'UA','currency':'USD','timezone':'Europe/Kyiv','country_mode':'prefer_ua'}
        locked={'country_label':'Bangladesh','country_code':'BD','locked':True}
        with patch('app.payment_card_binding._country_setting',AsyncMock(return_value=locked)),patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=True)) as choices,patch('app.payment_card_binding._setup_selected',AsyncMock(return_value=True)),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=next_button)):
            result=await configure_payment_account(browser,setup)
        self.assertEqual(result['status'],'SETUP_ADVANCED');self.assertTrue(result['billing_setup_observed']['country_preserved'])
        self.assertEqual(result['billing_setup_observed']['country_reason'],'meta_control_locked');self.assertFalse(result['billing_setup_observed']['saved'])
        self.assertEqual(choices.await_count,2);self.assertTrue(all(call.args[1] in {'Currency|Валюта',r'Time zone|Timezone|Часовой пояс|Часовий пояс'} for call in choices.await_args_list))
        next_button.click.reset_mock()
        for value in ({**locked,'locked':False},{}):
            with patch('app.payment_card_binding._country_setting',AsyncMock(return_value=value)),patch('app.payment_card_binding._setup_choice',AsyncMock(return_value=False)),patch('app.payment_card_binding._payment_surface',AsyncMock()),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=next_button)):
                result=await configure_payment_account(browser,setup)
            self.assertEqual(result['status'],'BLOCKED');self.assertNotEqual(result.get('status'),'SETUP_ADVANCED');next_button.click.assert_not_awaited()

    async def test_setup_evidence_survives_card_form_prepare_without_claiming_saved_or_funded(self):
        evidence={'country_label':'Bangladesh','country_preserved':True,'country_reason':'meta_control_locked','saved':False}
        browser=SimpleNamespace(profile_id='Fixture')
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','code':'CARD_FORM_READY','billing_setup_observed':evidence,'_fields':[]})):
            result=await payment_card_flow(browser,ID,{},operation='prepare')
        self.assertEqual(result['billing_setup_observed'],evidence);self.assertFalse(result['submitted']);self.assertFalse(result['funding_verified'])

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
            locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value=body)),wait_for_timeout=AsyncMock(),evaluate=AsyncMock(return_value=False),frames=[])
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

    async def test_auth_block_reports_only_verified_facebook_route(self):
        browser,save=self.browser()
        error=BrowserBusinessError('CHECKPOINT_REQUIRED','checkpoint',diagnostic={
            'auth_evidence':'checkpoint_url','checkpoint_path':'/checkpoint/123?next=secret'})
        with patch('app.payment_card_binding._open_card_form',AsyncMock(side_effect=error)):
            result=await payment_card_flow(browser,ID,{},operation='prepare')
        self.assertEqual(result['code'],'CHECKPOINT_REQUIRED')
        self.assertEqual(result['diagnostic'],{'auth_evidence':'checkpoint_url','facebook_route':'/checkpoint'})
        self.assertNotIn('secret',str(result))
        save.click.assert_not_awaited()

    async def test_financial_action_or_unaccepted_terms_stops_before_secret_entry(self):
        for body,checkbox in [('Verification charge',False),('Payment methods',True)]:
            browser,save=self.browser(body);fields=self.fields()
            if checkbox:fields.append({'kind':'','required':True,'type':'checkbox','tag':'input','checked':False})
            with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
                result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
            self.assertEqual(result['status'],'ACTION_REQUIRED');save.click.assert_not_awaited()
            for field in fields:
                if 'control' in field:field['control'].fill.assert_not_awaited()

    async def test_background_financial_help_does_not_block_card_form(self):
        browser,save=self.browser('Help: verification charge')
        fields=self.fields();fields[0]['control'].evaluate=AsyncMock(return_value='Payment methods\nSave')
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED');save.click.assert_awaited_once()
        fields[0]['control'].evaluate.return_value='Verification charge\nSave'
        browser,save=self.browser('Help: verification charge')
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['code'],'PAYMENT_FINANCIAL_ACTION_REQUIRED');save.click.assert_not_awaited()

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

    async def test_background_verification_text_is_not_a_bank_challenge(self):
        browser,save=self.browser(ID+' Payment methods Help: verification code and one-time password')
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':self.fields()})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED')
        self.assertEqual(result['code'],'CARD_LINK_NOT_VERIFIED');save.click.assert_awaited_once()

    async def test_visible_challenge_is_detected_before_settings_navigation(self):
        browser,save=self.browser('Verification code')
        browser.page.evaluate.return_value=True
        browser.page.url='https://business.facebook.com/latest/settings/ad_accounts/'
        browser._read_selected_ad_account_identity=AsyncMock()
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':self.fields()})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['code'],'CARD_BANK_CONFIRMATION_REQUIRED')
        browser._read_selected_ad_account_identity.assert_not_awaited();save.click.assert_awaited_once()

    async def test_billing_save_waits_for_mask_without_settings_fallback_or_replay(self):
        browser,save=self.browser(ID+' Payment methods Loading');fields=self.fields()
        current={'body':ID+' Payment methods Loading'}
        browser.page.locator=lambda _:SimpleNamespace(inner_text=AsyncMock(side_effect=lambda **kwargs:current['body']))
        async def hydrate(script,**kwargs):
            self.assertEqual(kwargs['arg'],'1111');self.assertEqual(kwargs['timeout'],12000)
            current['body']=ID+' Payment methods Visa •••• 1111'
        browser.page.wait_for_function=AsyncMock(side_effect=hydrate)
        browser._read_selected_ad_account_identity=AsyncMock()
        with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
            result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
        self.assertEqual(result['status'],'LINKED');save.click.assert_awaited_once();browser._read_selected_ad_account_identity.assert_not_awaited()
        self.assertFalse(result['funding_verified'])

    async def test_wrong_brand_and_explicit_validation_never_confirm_or_leak_secrets(self):
        for body,alert,code in [(ID+' Payment methods Mastercard •••• 1111','Help','CARD_LINK_NOT_VERIFIED'),
            (ID+' Payment methods No payment method','Your card was declined '+CARD['number']+' cvv fixture','CARD_META_REJECTED')]:
            browser,save=self.browser(body);fields=self.fields();browser.page.wait_for_function=AsyncMock()
            browser.page.get_by_role=lambda role:SimpleNamespace(filter=lambda **kwargs:SimpleNamespace(all_text_contents=AsyncMock(return_value=[alert])))
            browser._read_selected_ad_account_identity=AsyncMock()
            with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=save)):
                result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
            self.assertEqual(result['status'],'SUBMITTED_UNVERIFIED');self.assertEqual(result['code'],code)
            save.click.assert_awaited_once();browser._read_selected_ad_account_identity.assert_not_awaited()
            self.assertNotIn(CARD['number'],json.dumps(result));self.assertNotIn('cvv fixture',json.dumps(result))


class RealCardSelectorTests(unittest.IsolatedAsyncioTestCase):

    async def test_delayed_mask_after_save_is_observed_in_real_chromium(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page();url='https://business.facebook.com/billing_hub/payment_settings?asset_id='+ID
                html=ID+' Payment methods No payment method'+''.join('<input id="'+key+'">' for key in ('number','holder','expiry','cvv'))+'''<button id="save" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;setTimeout(()=>document.getElementById('method').textContent='Visa •••• 1111',1800)">Save</button><div id="method"></div>'''
                await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html; charset=utf-8',body=html));await page.goto(url)
                fields=[{'kind':key,'required':True,'type':'text','tag':'input','control':page.locator('#'+key)} for key in ('number','holder','expiry','cvv')]
                browser=SimpleNamespace(page=page,profile_id='Fixture',_assert_authenticated=AsyncMock(),_read_selected_ad_account_identity=AsyncMock())
                with patch('app.payment_card_binding._open_card_form',AsyncMock(return_value={'status':'FORM_READY','_fields':fields})),patch('app.payment_card_binding._unique_visible',AsyncMock(return_value=page.locator('#save'))):
                    result=await payment_card_flow(browser,ID,{},operation='bind',card=CARD,cvv='123')
                self.assertEqual(result['status'],'LINKED');self.assertFalse(result['funding_verified']);self.assertEqual(await page.locator('#save').get_attribute('data-clicks'),'1')
                browser._read_selected_ad_account_identity.assert_not_awaited()
            finally:await chromium.close()

    async def test_card_scope_selects_only_account_and_rejects_ambiguous_or_broad_scope(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        from app.payment_card_binding import _set_card_account_availability
        only='<label><input id="only" type="radio" name="scope">Only this account</label>'
        broad='<label><input id="all" type="radio" name="scope" checked>All accounts in this business portfolio</label>'
        cases=[
            ('native radio',broad+only,'only_this_account'),
            ('disabled only',broad+only.replace('id="only"','id="only" disabled'),'BLOCKED'),
            ('duplicate only',broad+only+only.replace('id="only"','id="other"'),'BLOCKED'),
            ('only broad',broad,'BLOCKED'),
            ('independent checkboxes',(broad+only).replace('type="radio"','type="checkbox"'),'BLOCKED'),
            ('duplicate broad',broad+broad.replace('id="all"','id="other"')+only,'BLOCKED'),
            ('custom radio',"""<div id="all" role="radio" aria-checked="true" aria-label="All accounts in this business portfolio">All accounts in this business portfolio</div>
              <div id="only" role="radio" aria-checked="false" aria-label="Only this account" onclick="this.setAttribute('aria-checked','true');document.getElementById('all').setAttribute('aria-checked','false')">Only this account</div>""",'only_this_account'),
            ('unknown scope options','<h3>Which accounts can use this card?</h3><button>Different scope</button>','BLOCKED'),
            ('no chooser','<h3>Debit or credit card</h3>','not_exposed'),
        ]
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                for name,html,expected in cases:
                    with self.subTest(name=name):
                        await page.set_content(html)
                        result=await _set_card_account_availability(page)
                        if expected=='BLOCKED':
                            self.assertEqual(result['code'],'CARD_ACCOUNT_SCOPE_UNVERIFIED')
                            self.assertEqual(result['status'],'BLOCKED')
                        else:
                            self.assertEqual(result['card_availability'],expected)
                            if expected=='only_this_account':
                                self.assertTrue(await page.locator('#only').evaluate("e=>e.tagName==='INPUT'?e.checked:e.getAttribute('aria-checked')==='true'"))
                                self.assertFalse(await page.locator('#all').evaluate("e=>e.tagName==='INPUT'?e.checked:e.getAttribute('aria-checked')==='true'"))
            finally:await chromium.close()

    async def test_unselectable_only_account_scope_blocks_before_card_entry_or_save(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321'
                html="""<div role="row"><button>Fixture RK</button><a>Details</a></div>
                  <button id="add" onclick="document.getElementById('form').hidden=false">Add payment method</button><div id="form" hidden>
                  <label>Card number<input autocomplete="cc-number"></label><label>Expiry<input autocomplete="cc-exp"></label><label>CVV<input autocomplete="cc-csc"></label>
                  <h3>Which accounts can use this card?</h3>
                  <label><input id="all" type="radio" name="scope" checked>All accounts in this business portfolio</label>
                  <label><input type="radio" name="scope" disabled>Only this account</label>
                  <button id="save" onclick="this.dataset.saved='yes'">Save</button></div>"""
                await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                async def goto(url,**kwargs):await page.goto(url,wait_until='domcontentloaded')
                browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=goto,_assert_authenticated=AsyncMock(),SETTINGS_AD_ACCOUNTS_URLS=[url],_read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
                with patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()):
                    result=await payment_card_flow(browser,ID,{'business_id':'987654321','name':'Fixture RK'},operation='bind',card=CARD,cvv='123')
                self.assertEqual(result['code'],'CARD_ACCOUNT_SCOPE_UNVERIFIED')
                self.assertFalse(result['submitted']);self.assertFalse(result['funding_verified'])
                self.assertEqual(await page.locator('input[autocomplete]').evaluate_all('(es)=>es.map(e=>e.value)'),['','',''])
                self.assertIsNone(await page.locator('#save').get_attribute('data-saved'))
                self.assertTrue(await page.locator('#all').is_checked())
            finally:await chromium.close()

    async def test_unnamed_country_currency_controls_resolve_the_label_parent(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        import re
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content("""<style>.label,.value{display:block}</style>
                  <div role="dialog"><h2>Select location and currency</h2><div class="fields">
                    <div id="country" role="combobox" tabindex="0" onclick="document.getElementById('countryOptions').hidden=false">
                      <span class="label">Country/region</span><span id="countryValue" class="value">Bangladesh</span></div>
                    <div id="currency" role="combobox" tabindex="0" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">
                      <span class="label">Currency</span><span class="value">US Dollars</span></div>
                  </div><button id="zone" onclick="document.getElementById('zoneOptions').hidden=false">Los Angeles, America (GMT-07:00)</button>
                  <button id="next" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">Next</button></div>
                  <div id="countryOptions" role="listbox" hidden><button role="option" onclick="document.getElementById('countryValue').textContent='Ukraine';document.getElementById('countryOptions').hidden=true">Ukraine</button></div>
                  <div id="zoneOptions" role="listbox" hidden><button role="option" onclick="document.getElementById('zone').textContent='Kyiv, Europe (GMT+03:00)';document.getElementById('zoneOptions').hidden=true">Kyiv, Europe (GMT+03:00)</button></div>
                  <input id="background" placeholder="Search accounts">""")
                # Visible content does not provide a combobox accessible name.
                self.assertEqual(await page.get_by_role('combobox',name=re.compile('Country')).count(),0)
                self.assertEqual(await page.get_by_role('combobox').count(),2)
                browser=SimpleNamespace(page=page,profile_id='Fixture',_assert_authenticated=AsyncMock())
                result=await configure_payment_account(browser,{'country':'UA','country_mode':'prefer_ua','currency':'USD','timezone':'Europe/Kyiv'})
                self.assertEqual(result['status'],'SETUP_ADVANCED')
                self.assertEqual(result['billing_setup_observed']['country_label'],'Ukraine')
                self.assertFalse(result['billing_setup_observed']['saved'])
                self.assertEqual(await page.locator('#next').get_attribute('data-clicks'),'1')
                self.assertIsNone(await page.locator('#currency').get_attribute('data-clicks'))
                self.assertEqual(await page.locator('#background').input_value(),'')
                self.assertEqual(await page.locator('#zone').inner_text(),'Kyiv, Europe (GMT+03:00)')
            finally:await chromium.close()
    async def test_country_policy_and_dependent_settings_in_real_browser(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        setup={'country':'UA','currency':'USD','timezone':'Europe/Kyiv'}
        settings='<label>Currency<select id="currency"><option value="USD">US Dollars</option><option value="BDT">BDT</option></select></label><label>Time zone<select id="zone"><option value="Europe/Kyiv">Kyiv, Europe (GMT+03:00)</option><option value="America/Los_Angeles">Los Angeles, America (GMT-07:00)</option></select></label>'
        country='<label>Country/region<select id="country" {lock} onchange="this.dataset.changes=Number(this.dataset.changes||0)+1"><option value="BD">Bangladesh</option><option value="UA">Ukraine</option></select></label>'
        reset_settings='<label>Currency<select id="currency"><option value="USD">US Dollars</option></select></label><label>Time zone<select id="zone" onchange="document.getElementById(\'country\').value=\'UA\'"><option value="America/Los_Angeles">Los Angeles, America (GMT-07:00)</option><option value="Europe/Kyiv">Kyiv, Europe (GMT+03:00)</option></select></label>'
        scenarios=[
            ('prefer_ua',country.format(lock='disabled'),settings,'SETUP_ADVANCED','Bangladesh',True),
            ('prefer_ua',country.format(lock=''),settings,'SETUP_ADVANCED','Ukraine',False),
            ('current',country.format(lock=''),settings,'SETUP_ADVANCED','Bangladesh',True),
            ('strict',country.format(lock='disabled'),settings,'PAYMENT_COUNTRY_LOCKED','Bangladesh',False),
            ('prefer_ua','<div role="combobox" aria-label="Country/region" aria-disabled="true">Country/region<br>Bangladesh</div>',settings,'SETUP_ADVANCED','Bangladesh',True),
            ('current','<div role="combobox" aria-label="Country/region" aria-disabled="true">Loading</div>',settings,'PAYMENT_COUNTRY_UNVERIFIED','',False),
            ('prefer_ua','<div role="combobox" aria-label="Country/region">Country/region<br>Bangladesh</div>',settings,'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','',False),
            ('current','',settings,'PAYMENT_COUNTRY_UNVERIFIED','',False),
            ('prefer_ua',country.format(lock='disabled'),settings.replace('<option value="USD">US Dollars</option>',''),'PAYMENT_ACCOUNT_SETUP_CONTROL_MISSING','',False),
            ('current',country.format(lock=''),reset_settings,'PAYMENT_COUNTRY_UNVERIFIED','',False),
        ]
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                for mode,country_html,other_settings,expected,label,preserved in scenarios:
                    with self.subTest(mode=mode,expected=expected,country=country_html[:80]):
                        await page.set_content('<div role="dialog"><h2>Select location and currency</h2>'+country_html+other_settings+'<button onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1">Next</button></div><input id="background" placeholder="Search accounts">')
                        browser=SimpleNamespace(page=page,profile_id='Fixture',_assert_authenticated=AsyncMock())
                        result=await configure_payment_account(browser,{**setup,'country_mode':mode})
                        self.assertEqual(result.get('code',result.get('status')),expected)
                        next_button=page.get_by_role('button',name='Next',exact=True)
                        self.assertEqual(await next_button.get_attribute('data-clicks'),'1' if expected=='SETUP_ADVANCED' else None)
                        self.assertEqual(await page.locator('#background').input_value(),'')
                        if expected=='SETUP_ADVANCED':
                            observed=result['billing_setup_observed']
                            self.assertEqual(observed['country_label'],label);self.assertEqual(observed['country_preserved'],preserved);self.assertFalse(observed['saved'])
                            self.assertEqual(await page.locator('#currency').input_value(),'USD');self.assertEqual(await page.locator('#zone').input_value(),'Europe/Kyiv')
                        if mode=='current' and await page.locator('#country').count():self.assertIsNone(await page.locator('#country').get_attribute('data-changes'))
            finally:await chromium.close()

    async def test_picker_click_without_selected_value_change_does_not_advance(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        from app.payment_card_binding import _setup_choice
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content('<div role="combobox" aria-label="Country/region" onclick="document.getElementById(\'options\').hidden=false">Country/region<br>Bangladesh</div><div id="options" role="listbox" hidden><button role="option" onclick="this.dataset.clicked=\'yes\';document.getElementById(\'options\').hidden=true">Ukraine</button></div>')
                self.assertFalse(await _setup_choice(page,'Country/region',r'^Ukraine$','Ukraine'))
                self.assertEqual(await page.get_by_role('option',include_hidden=True).get_attribute('data-clicked'),'yes')
            finally:await chromium.close()

    async def test_timezone_name_change_and_picker_outside_dialog_remain_verifiable(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        from app.payment_card_binding import _setup_choice, _setup_selected
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content('<div role="dialog"><button id="zone" onclick="document.getElementById(\'popup\').hidden=false">Los Angeles, America (GMT-07:00)</button></div><div id="popup" role="listbox" hidden><button role="option" onclick="document.getElementById(\'zone\').textContent=\'Kyiv, Europe (GMT+03:00)\';document.getElementById(\'popup\').hidden=true">Kyiv, Europe (GMT+03:00)</button></div>')
                scope=page.get_by_role('dialog');pattern=r'^(Kyiv|Kiev)(?:\s*[,\(].*)?$';default='Los Angeles, America (GMT-07:00)'
                self.assertTrue(await _setup_choice(scope,'Time zone',pattern,'Kyiv',default,picker_scope=page))
                self.assertTrue(await _setup_selected(scope,'Time zone',pattern,'Kyiv',default))
            finally:await chromium.close()

    async def test_lazy_payment_dialog_is_awaited_without_reclicking_add(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321'
                html="""<div role="row"><button>Fixture RK</button><a>Details</a></div>
                  <button id="add" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;setTimeout(()=>document.getElementById('form').innerHTML='<label>Card number<input autocomplete=cc-number></label><label>Expiry<input autocomplete=cc-exp></label><label>CVV<input autocomplete=cc-csc></label>',2500)">Add payment method</button><div id="form">Loading</div>"""
                await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                async def goto(url, **kwargs):await page.goto(url,wait_until='domcontentloaded')
                browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=goto,_assert_authenticated=AsyncMock(),SETTINGS_AD_ACCOUNTS_URLS=[url],_read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
                with patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()):
                    result=await payment_card_flow(browser,ID,{'business_id':'987654321','name':'Fixture RK'},operation='prepare')
                self.assertEqual(result['status'],'FORM_READY');self.assertFalse(result['submitted'])
                self.assertEqual(await page.locator('#add').get_attribute('data-clicks'),'1')
                self.assertEqual(await page.locator('input').evaluate_all('(es)=>es.map(e=>e.value)'),['','',''])
            finally:await chromium.close()


    async def test_add_click_timeout_after_opening_dialog_observes_without_reclick(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321'
                html="""<div role="row"><button>Fixture RK</button><a>Details</a></div>
                  <button id="add" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;setTimeout(()=>document.getElementById('form').innerHTML='<label>Card number<input autocomplete=cc-number></label><label>Expiry<input autocomplete=cc-exp></label><label>CVV<input autocomplete=cc-csc></label>',1500)">Add payment method</button><div id="form">Loading</div>"""
                await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                async def goto(url,**kwargs):await page.goto(url,wait_until='domcontentloaded')
                async def click_then_timeout(**kwargs):
                    await page.locator('#add').click()
                    raise PlaywrightTimeoutError('Fixture click finished while the modal transition is pending')
                add=SimpleNamespace(click=AsyncMock(side_effect=click_then_timeout))
                browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=goto,_assert_authenticated=AsyncMock(),SETTINGS_AD_ACCOUNTS_URLS=[url],_read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
                unique=_unique_visible
                async def controls(scope,role,name):
                    if role=='button' and name.startswith('^(Add payment method'):return add
                    return await unique(scope,role,name)
                with patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()),patch('app.payment_card_binding._unique_visible',controls):
                    result=await payment_card_flow(browser,ID,{'business_id':'987654321','name':'Fixture RK'},operation='prepare')
                self.assertEqual(result['status'],'FORM_READY');self.assertFalse(result['submitted'])
                self.assertEqual(await page.locator('#add').get_attribute('data-clicks'),'1')
                add.click.assert_awaited_once()
                self.assertEqual(await page.locator('input').evaluate_all('(es)=>es.map(e=>e.value)'),['','',''])
            finally:await chromium.close()

    async def test_slow_picker_overlay_search_and_selection_are_observed_once(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        from app.payment_card_binding import _setup_choice
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content("""<div id="overlay" style="position:fixed;inset:0;z-index:999;background:rgba(0,0,0,.1)">Loading</div>
                  <div id="country" role="combobox" aria-label="Country" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;setTimeout(()=>document.getElementById('options').hidden=false,1600)">Country<br><span id="selected">Bangladesh</span></div>
                  <div id="options" role="listbox" hidden><input id="search" placeholder="Search" oninput="this.dataset.fills=Number(this.dataset.fills||0)+1;setTimeout(()=>document.getElementById('option').hidden=false,1800)">
                  <button id="option" role="option" hidden onclick="document.getElementById('options').hidden=true;setTimeout(()=>document.getElementById('selected').textContent='Ukraine',1100)">Ukraine</button></div>
                  <input id="background" placeholder="Search accounts"><script>setTimeout(()=>document.getElementById('overlay').remove(),4200)</script>""")
                self.assertTrue(await _setup_choice(page,'Country',r'^Ukraine$','Ukraine'))
                self.assertEqual(await page.locator('#country').get_attribute('data-clicks'),'1')
                self.assertEqual(await page.locator('#search').get_attribute('data-fills'),'1')
                self.assertEqual(await page.locator('#background').input_value(),'')
                self.assertEqual(await page.locator('#selected').inner_text(),'Ukraine')
            finally:await chromium.close()

    async def test_picker_click_timeout_after_opening_does_not_toggle_it_again(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
        from app.payment_card_binding import _setup_choice
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                await page.set_content("""<div id="country" role="combobox" aria-label="Country" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;document.getElementById('options').hidden=false">Country<br><span id="selected">Bangladesh</span></div>
                  <div id="options" role="listbox" hidden><button role="option" onclick="document.getElementById('selected').textContent='Ukraine';document.getElementById('options').hidden=true">Ukraine</button></div>""")
                control=page.locator('#country')
                async def click_then_timeout(**kwargs):
                    await control.click()
                    raise PlaywrightTimeoutError('Fixture picker transition after click')
                wrapper=SimpleNamespace(evaluate=control.evaluate,inner_text=control.inner_text,is_enabled=control.is_enabled,click=AsyncMock(side_effect=click_then_timeout))
                with patch('app.payment_card_binding._setup_control',AsyncMock(return_value=wrapper)):
                    self.assertTrue(await _setup_choice(page,'Country',r'^Ukraine$','Ukraine'))
                wrapper.click.assert_awaited_once()
                self.assertEqual(await control.get_attribute('data-clicks'),'1')
            finally:await chromium.close()

    async def test_slow_setup_gets_its_own_card_form_observation_window(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                url='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321'
                html="""<div role="row"><button>Fixture RK</button><a>Details</a></div>
                  <button id="add" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;document.getElementById('form').textContent='Select location and currency Set time zone'">Add payment method</button><div id="form"></div>"""
                await page.route(url,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                async def goto(url,**kwargs):await page.goto(url,wait_until='domcontentloaded')
                tick=[0.0]
                async def slow_setup(*args):
                    # Advance only this module's clock, leaving Chromium/event
                    # loop clocks real. The setup consumed the old 20s budget.
                    tick[0]+=25
                    await page.set_content('<label>Card number<input autocomplete="cc-number"></label><label>Expiry<input autocomplete="cc-exp"></label><label>CVV<input autocomplete="cc-csc"></label>')
                    return {'status':'SETUP_ADVANCED','billing_setup_observed':{'country_label':'Ukraine','saved':False}}
                setup=AsyncMock(side_effect=slow_setup)
                browser=SimpleNamespace(page=page,profile_id='Fixture',_goto=goto,_assert_authenticated=AsyncMock(),SETTINGS_AD_ACCOUNTS_URLS=[url],_read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID}))
                with patch('app.payment_card_binding.select_settings_payment_tab',AsyncMock()),patch('app.payment_card_binding.configure_payment_account',setup),patch('app.payment_card_binding.time',SimpleNamespace(monotonic=lambda:tick[0])):
                    result=await payment_card_flow(browser,ID,{'business_id':'987654321','name':'Fixture RK'},operation='prepare',billing_setup={'country':'UA','currency':'USD','timezone':'Europe/Kyiv'})
                self.assertEqual(result['status'],'FORM_READY');self.assertFalse(result['submitted'])
                self.assertFalse(result['billing_setup_observed']['saved']);setup.assert_awaited_once()
                self.assertEqual(await page.locator('input').evaluate_all('(es)=>es.map(e=>e.value)'),['','',''])
            finally:await chromium.close()
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
                result=await configure_payment_account(browser,{'country':'UA','currency':'USD','timezone':'Europe/Kyiv'})
                self.assertEqual(result['status'],'SETUP_ADVANCED')
                self.assertEqual(result['billing_setup_observed']['country_label'],'Ukraine')
                self.assertFalse(result['billing_setup_observed']['saved'])
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
