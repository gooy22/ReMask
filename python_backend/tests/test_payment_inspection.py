import asyncio
import json
import unittest
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.payment_inspection import account_id, inspect_payment_methods, inspect_profile_payment_methods, payment_summary, saved_payment_business

ID = "123456789"
URL = "https://business.facebook.com/billing_hub/payment_settings?asset_id=" + ID


class PaymentSummaryTests(unittest.TestCase):
    def test_masked_card_is_linkage_not_verified_funding(self):
        result = payment_summary(ID, URL, ID + "\nPayment methods\nVisa •••• 1234")
        self.assertEqual(result["verification_status"], "LINKED")
        self.assertTrue(result["card_linked"])
        self.assertFalse(result["funding_verified"])
        self.assertEqual(result["payment_methods"], [{"type":"Visa","last4":"1234","linkage_status":"OBSERVED"}])

    def test_requested_url_without_visible_exact_account_cannot_prove_linkage(self):
        result=payment_summary(ID,URL,"Payment methods\nVisa •••• 1234")
        self.assertEqual(result["verification_status"],"UNVERIFIED")
        self.assertEqual(result["payment_methods"],[])

    def test_wrong_account_host_or_missing_billing_scope_cannot_prove_linkage(self):
        for url in [URL.replace(ID,"999999999"),URL.replace("business.facebook.com","example.test"),URL.split("?")[0]]:
            result=payment_summary(ID,url,ID+"\nPayment methods\nVisa •••• 1234")
            self.assertFalse(result["account_scope_verified"])
            self.assertIsNone(result["card_linked"])

    def test_no_card_requires_explicit_empty_state(self):
        result=payment_summary(ID,URL,ID+"\nPayment methods\nNo payment methods")
        self.assertFalse(result["card_linked"])
        self.assertEqual(result["verification_status"],"NONE")
        result=payment_summary(ID,URL,ID+"\nPayment methods")
        self.assertIsNone(result["card_linked"])

    def test_live_billing_accounts_singular_empty_state_is_recognized(self):
        result=payment_summary(ID,URL,ID+' Billing & payments Accounts No payment method')
        self.assertEqual(result['verification_status'],'NONE');self.assertFalse(result['card_linked']);self.assertFalse(result['funding_verified'])
        wrong=payment_summary(ID,URL.replace(ID,'987654321'),ID+' Billing & payments No payment method')
        self.assertEqual(wrong['verification_status'],'UNVERIFIED')

    def test_conflicting_duplicate_or_empty_account_scope_cannot_prove_linkage(self):
        for suffix in ["&act=999999999", "&asset_id=999999999", "&ad_account_id=", "&act=" + ID + "&act=" + ID]:
            with self.subTest(suffix=suffix):
                result = payment_summary(ID, URL + suffix, ID + "\nPayment methods\nVisa •••• 1234")
                self.assertFalse(result["account_scope_verified"])
                self.assertEqual(result["payment_methods"], [])
                self.assertIsNone(result["card_linked"])

    def test_non_billing_route_and_action_route_cannot_prove_linkage(self):
        for path in ["/adsmanager/manage/campaigns", "/payment/submit", "/payments/checkout", "/billing_hub/payment_settings/submit"]:
            with self.subTest(path=path):
                result = payment_summary(ID, "https://business.facebook.com" + path + "?act=" + ID, ID + "\nPayment methods\nVisa •••• 1234")
                self.assertFalse(result["account_scope_verified"])
                self.assertEqual(result["payment_methods"], [])

    def test_consistent_account_scope_keys_are_accepted(self):
        result = payment_summary(ID, URL + "&act=" + ID, ID + "\nPayment methods\nVisa •••• 1234")
        self.assertTrue(result["account_scope_verified"])

    def test_raw_card_number_and_unrelated_body_never_escape(self):
        raw="4111111111111111"
        result=payment_summary(ID,URL,ID+"\nPayment methods\nVisa "+raw+"\nSecurity code fixture\nAddress fixture")
        self.assertEqual(result["payment_methods"],[])
        for value in [raw,"Security code","Address fixture"]:
            self.assertNotIn(value,json.dumps(result))

    def test_invalid_account_never_reaches_browser(self):
        for value in ["","fixture","123?act=9","https://example.test"]:
            with self.assertRaises(ValueError): account_id(value)
        self.assertEqual(account_id("act_"+ID),ID)


class PaymentBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_payment_inspection_receives_workspace_bm_hint(self):
        asset={'business_id':'987654321','business_asset_id':'','name':'Fixture RK'}
        browser=SimpleNamespace(page=SimpleNamespace(url=URL,frames=[],screenshot=AsyncMock(return_value=b'preview')))
        resolver=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(cookies={})))
        class Session:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return None
            async def facebook_business_browser(self):return browser
        state=object()
        observed={'account_id':ID,'account_scope_verified':False,'verification_status':'UNVERIFIED','payment_methods':[]}
        with patch('app.session.ProfileSession',return_value=Session()), \
             patch('app.payment_inspection.resolve_payment_asset',AsyncMock(return_value=asset)) as resolve, \
             patch('app.payment_inspection.inspect_payment_methods',AsyncMock(return_value=observed)) as inspect:
            result=await inspect_profile_payment_methods(resolver,'Fixture',ID,state=state,
                asset_hint={'business_id':'987654321','business_asset_id':'','name':'Fixture RK'})
        resolve.assert_awaited_once_with('Fixture',ID,state,{'business_id':'987654321','business_asset_id':'','name':'Fixture RK'})
        inspect.assert_awaited_once()
        self.assertEqual(inspect.await_args.kwargs['business_id'],'987654321')
        self.assertEqual(result['account_id'],ID)

    def browser(self, links):
        menu=SimpleNamespace(count=AsyncMock(return_value=0),is_visible=AsyncMock(return_value=False),click=AsyncMock())
        page=SimpleNamespace(url=URL,evaluate=AsyncMock(return_value=links),wait_for_function=AsyncMock(),
            get_by_role=lambda role,**kwargs:menu,
            locator=lambda selector:SimpleNamespace(inner_text=AsyncMock(return_value=ID+"\nPayment methods\nVisa •••• 1234")))
        return SimpleNamespace(page=page,profile_id="Fixture",ADS_MANAGER_URL="https://adsmanager.facebook.com/adsmanager/manage/campaigns",SETTINGS_AD_ACCOUNTS_URLS=('https://business.facebook.com/latest/settings/ad_accounts?business_id={business_id}',),_goto=AsyncMock(),_assert_authenticated=AsyncMock())


    async def test_reuses_only_exact_settings_scope_with_fresh_canonical_identity(self):
        asset={'name':'Fixture RK','business_id':'987654321','business_asset_id':'555555555'}
        settings='https://business.facebook.com/latest/settings/ad_accounts/?business_id=987654321&selected_asset_id=555555555&selected_asset_type=ad-account'
        variants=[(settings,ID,1),(settings, '999999999',2),
            (settings.replace('987654321','999999999'),ID,2),
            (settings.replace('555555555','999999999'),ID,2),
            (settings+'&selected_asset_id=555555555',ID,2),
            (settings+'&act=999999999',ID,2),
            (settings.replace('ad-account','page'),ID,2),
            (settings.replace('business.facebook.com','example.test'),ID,2)]
        for current,canonical,navigations in variants:
            with self.subTest(url=current,canonical=canonical):
                browser=self.browser([{'href':URL,'label':'Billing & payments'}])
                browser.page.url=current
                async def goto(url,**kwargs):browser.page.url=url
                browser._goto.side_effect=goto
                browser._read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':canonical,'business_id':'987654321'})
                with patch('app.payment_inspection.select_settings_payment_tab',AsyncMock(return_value=False)):
                    await inspect_payment_methods(browser,ID,business_id=asset['business_id'],asset=asset)
                self.assertEqual(browser._goto.await_count,navigations)
                self.assertEqual(browser._goto.await_args_list[-1].args[0],URL)
                if navigations==1:browser._read_selected_ad_account_identity.assert_awaited_once()
                else:self.assertIn('/settings/ad_accounts',browser._goto.await_args_list[0].args[0])
    async def test_follows_only_rendered_meta_billing_link_and_returns_masked_data(self):
        browser=self.browser([{"href":URL,"label":"Billing & payments"}])
        result=await inspect_payment_methods(browser,ID)
        self.assertEqual(browser._goto.await_count,2)
        self.assertEqual(browser._goto.await_args_list[1].args[0],URL)
        self.assertEqual(result["profile_id"],"Fixture")
        self.assertFalse(result["funding_verified"])

    async def test_uninformative_settings_tab_continues_to_observed_billing(self):
        asset={'name':'Fixture RK','business_id':'987654321','business_asset_id':'555555555'}
        browser=self.browser([{'href':URL,'label':'Billing & payments'}])
        async def goto(url,**kwargs):browser.page.url=url
        browser._goto.side_effect=goto
        browser._read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID,'business_id':asset['business_id']})
        with patch('app.payment_inspection.select_settings_payment_tab',AsyncMock(return_value=True)),patch('app.payment_inspection.selected_payment_pane_text',AsyncMock(return_value='Fixture RK Payment methods Loading')):
            result=await inspect_payment_methods(browser,ID,business_id=asset['business_id'],asset=asset)
        self.assertEqual(browser._goto.await_args_list[-1].args[0],URL)
        self.assertEqual(result['verification_status'],'LINKED');self.assertFalse(result['funding_verified'])

    async def test_placeholder_is_resolved_before_exact_identity_check(self):
        asset={'name':'act_'+ID,'business_id':'987654321','business_asset_id':'555555555'}
        browser=self.browser([{'href':URL,'label':'Billing & payments'}])
        details=SimpleNamespace(filter=lambda **kw:SimpleNamespace(wait_for=AsyncMock()))
        browser.page.get_by_role=lambda role,**kwargs:details
        async def goto(url,**kwargs):browser.page.url=url
        browser._goto.side_effect=goto
        browser._read_selected_ad_account_identity=AsyncMock(return_value={'confirmed':True,'ad_account_id':ID,'business_id':asset['business_id']})
        with patch('app.payment_inspection._resolve_payment_account_name',AsyncMock(return_value='Actual RK')),patch('app.payment_inspection.select_settings_payment_tab',AsyncMock(return_value=True)),patch('app.payment_inspection.selected_payment_pane_text',AsyncMock(return_value='Actual RK Payment methods Visa •••• 1234')):
            result=await inspect_payment_methods(browser,ID,business_id=asset['business_id'],asset=asset)
        browser._read_selected_ad_account_identity.assert_awaited_once_with(business_id=asset['business_id'],account_name='Actual RK')
        self.assertEqual(result['source'],'private_facebook_selected_rk_payment_tab');self.assertEqual(result['verification_status'],'LINKED')

    async def test_missing_other_account_or_untrusted_link_stops_without_guessed_route(self):
        for links in [[],[{"href":URL.replace(ID,"999999999")}],[{"href":URL.replace("business.facebook.com","example.test")}]]:
            browser=self.browser(links)
            with self.assertRaises(BrowserBusinessError) as exc:
                await inspect_payment_methods(browser,ID)
            self.assertEqual(exc.exception.code,"PAYMENT_UI_UNAVAILABLE")
            self.assertEqual(browser._goto.await_count,1)

    async def test_checkpoint_stops_immediately_without_billing_navigation(self):
        browser=self.browser([{"href":URL}])
        browser._goto.side_effect=BrowserBusinessError("CHECKPOINT_REQUIRED","Account verification required",retryable=False)
        with self.assertRaises(BrowserBusinessError):
            await inspect_payment_methods(browser,ID)
        browser.page.evaluate.assert_not_called()
        self.assertEqual(browser._goto.await_count,1)

    async def test_submission_and_ambiguous_scope_links_are_not_followed(self):
        for url in [URL.replace("/billing_hub/payment_settings", "/payment/submit"), URL + "&act=", URL + "&act=" + ID + "&act=" + ID]:
            with self.subTest(url=url):
                browser = self.browser([{"href": url, "label": "Payments"}])
                with self.assertRaises(BrowserBusinessError) as exc:
                    await inspect_payment_methods(browser, ID)
                self.assertEqual(exc.exception.code, "PAYMENT_UI_UNAVAILABLE")
                self.assertEqual(browser._goto.await_count, 1)

    async def test_collapsed_all_tools_is_opened_once_before_reading_billing_link(self):
        browser=self.browser([])
        menu=SimpleNamespace(count=AsyncMock(return_value=1),is_visible=AsyncMock(return_value=True),click=AsyncMock())
        browser.page.get_by_role=lambda role,**kwargs:menu
        browser.page.evaluate.side_effect=[[],[{"href":URL,"label":"Billing & payments"}]]
        result=await inspect_payment_methods(browser,ID)
        menu.click.assert_awaited_once()
        browser._assert_authenticated.assert_awaited_once()
        self.assertEqual(result["verification_status"],"LINKED")

    async def test_auth_gate_after_menu_open_stops_before_billing_navigation(self):
        browser=self.browser([])
        menu=SimpleNamespace(count=AsyncMock(return_value=1),is_visible=AsyncMock(return_value=True),click=AsyncMock())
        browser.page.get_by_role=lambda role,**kwargs:menu
        browser._assert_authenticated.side_effect=BrowserBusinessError("CHECKPOINT_REQUIRED","Verification required",retryable=False)
        with self.assertRaises(BrowserBusinessError): await inspect_payment_methods(browser,ID)
        self.assertEqual(browser._goto.await_count,1)


    async def test_navigation_hydration_is_awaited_before_missing_link_is_declared(self):
        browser=self.browser([])
        async def hydrate(*args, **kwargs):
            browser.page.evaluate.return_value=[{"href":URL,"label":"Billing & payments"}]
        browser.page.wait_for_function.side_effect=hydrate
        result=await inspect_payment_methods(browser,ID)
        self.assertEqual(browser.page.wait_for_function.await_count,2)
        self.assertEqual(browser.page.wait_for_function.await_args_list[0].kwargs['timeout'],5000)
        self.assertEqual(result['verification_status'],'LINKED')

    async def test_slow_all_tools_drawer_waits_for_rendered_billing_link_once(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        start='https://adsmanager.facebook.com/adsmanager/manage/campaigns?act='+ID
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                html="""<button id="menu" aria-label="All Tools menu" onclick="this.dataset.clicks=Number(this.dataset.clicks||0)+1;window.menuClicks=Number(this.dataset.clicks);document.getElementById('drawer').textContent='Loading';setTimeout(()=>document.getElementById('drawer').innerHTML='<a href=&quot;"""+URL+"""&quot;>Billing & payments</a>',4500)">All Tools</button><div id="drawer"></div>"""
                await page.route(start,lambda route:route.fulfill(status=200,content_type='text/html',body=html))
                await page.route(URL,lambda route:route.fulfill(status=200,content_type='text/html',body=ID+' Payment methods No payment methods'))
                navigations=[]
                async def goto(url,**kwargs):
                    if navigations:
                        self.assertEqual(await page.locator('#menu').get_attribute('data-clicks'),'1')
                    navigations.append(url)
                    await page.goto(url,wait_until='domcontentloaded')
                browser=SimpleNamespace(page=page,profile_id='Fixture',ADS_MANAGER_URL=start.split('?')[0],_goto=goto,_assert_authenticated=AsyncMock())
                result=await inspect_payment_methods(browser,ID)
                self.assertEqual(navigations,[start,URL])
                self.assertTrue(result['account_scope_verified']);self.assertEqual(result['verification_status'],'NONE')
                self.assertFalse(result['funding_verified'])
            finally:await chromium.close()

    async def test_fresh_billing_process_uses_validated_link_and_new_page_scope(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        start='https://adsmanager.facebook.com/adsmanager/manage/campaigns?act='+ID
        async with async_playwright() as playwright:
            for body,expected in [(ID+' Payment methods No payment methods','NONE'),('999999999 Payment methods Visa •••• 1234','UNVERIFIED')]:
                with self.subTest(expected=expected):
                    chromium=None;pages=[];closes=[];navigations=[]
                    browser=SimpleNamespace(page=None,profile_id='Fixture',ADS_MANAGER_URL=start.split('?')[0],_assert_authenticated=AsyncMock())
                    async def open_browser():
                        nonlocal chromium
                        chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
                        page=await chromium.new_page();pages.append(page);browser.page=page
                        await page.route(start,lambda route:route.fulfill(status=200,content_type='text/html',body='<a href="'+URL+'">Billing & payments</a>'))
                        await page.route(URL,lambda route:route.fulfill(status=200,content_type='text/html',body=body))
                    async def close_browser():
                        closes.append(True);await chromium.close();browser.page=None
                    async def goto(url,**kwargs):
                        navigations.append(url);await browser.page.goto(url,wait_until='domcontentloaded')
                    browser.open=open_browser;browser.close=close_browser;browser._goto=goto
                    try:
                        await open_browser()
                        result=await inspect_payment_methods(browser,ID,fresh_billing_context=True)
                        self.assertEqual(navigations,[start,URL]);self.assertEqual(len(closes),1);self.assertEqual(len(pages),2)
                        self.assertTrue(pages[0].is_closed());self.assertIs(browser.page,pages[1])
                        self.assertEqual(result['verification_status'],expected);self.assertFalse(result['funding_verified'])
                        if expected=='UNVERIFIED':self.assertFalse(result['account_scope_verified']);self.assertEqual(result['payment_methods'],[])
                    finally:
                        if chromium:await chromium.close()

    async def test_untrusted_billing_link_never_restarts_browser_or_navigates_it(self):
        browser=self.browser([{'href':URL.replace('business.facebook.com','example.test'),'label':'Billing & payments'}])
        browser.close=AsyncMock();browser.open=AsyncMock()
        with self.assertRaises(BrowserBusinessError):
            await inspect_payment_methods(browser,ID,fresh_billing_context=True)
        browser.close.assert_not_awaited();browser.open.assert_not_awaited()
        self.assertEqual(browser._goto.await_count,1)
    async def test_billing_skeleton_waits_for_exact_rendered_account_before_summary(self):
        browser=self.browser([{'href':URL,'label':'Billing & payments'}])
        body=SimpleNamespace(inner_text=AsyncMock(return_value='Loading'))
        browser.page.locator=lambda _:body
        async def hydrate(script, **kwargs):
            if kwargs.get('arg')==ID:
                self.assertEqual(kwargs['timeout'],15000)
                body.inner_text.return_value=ID+'\nPayment methods\nNo payment methods'
        browser.page.wait_for_function.side_effect=hydrate
        result=await inspect_payment_methods(browser,ID)
        self.assertTrue(result['account_scope_verified'])
        self.assertEqual(result['verification_status'],'NONE')

    async def test_real_chromium_waits_for_lazy_billing_account(self):
        executable=next((path for name in ('google-chrome','chromium','chromium-browser') if (path:=shutil.which(name))),None)
        if not executable:self.skipTest('No local Chromium installed')
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium=await playwright.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox'])
            try:
                page=await chromium.new_page()
                async def route(request):
                    html='<body>Loading<script>setTimeout(()=>document.body.innerText="'+ID+' Payment methods No payment methods",250)</script></body>'
                    await request.fulfill(status=200,content_type='text/html',body=html)
                await page.route(URL,route)
                async def goto(url, **kwargs):
                    if url==URL:await page.goto(url,wait_until='domcontentloaded')
                    else:await page.set_content('<a href="'+URL+'">Billing & payments</a>')
                browser=SimpleNamespace(page=page,profile_id='Fixture',ADS_MANAGER_URL='https://adsmanager.facebook.com/adsmanager/manage/campaigns',_goto=goto,_assert_authenticated=AsyncMock())
                result=await inspect_payment_methods(browser,ID)
                self.assertTrue(result['account_scope_verified'])
                self.assertEqual(result['verification_status'],'NONE')
            finally:await chromium.close()

    async def test_billing_wait_timeout_keeps_wrong_account_unverified(self):
        browser=self.browser([{'href':URL,'label':'Billing & payments'}])
        browser.page.locator=lambda _:SimpleNamespace(inner_text=AsyncMock(return_value='999999999\nPayment methods\nVisa •••• 1234'))
        async def timeout(script, **kwargs):
            if kwargs.get('arg')==ID:raise asyncio.TimeoutError()
        browser.page.wait_for_function.side_effect=timeout
        result=await inspect_payment_methods(browser,ID)
        self.assertFalse(result['account_scope_verified'])
        self.assertEqual(result['payment_methods'],[])
        browser._assert_authenticated.assert_awaited_once()

    async def test_hydration_timeout_does_not_invent_billing_destination(self):
        browser=self.browser([])
        browser.page.wait_for_function.side_effect=asyncio.TimeoutError()
        with self.assertRaises(BrowserBusinessError) as exc:
            await inspect_payment_methods(browser,ID)
        self.assertEqual(exc.exception.code,'PAYMENT_UI_UNAVAILABLE')
        self.assertEqual(browser._goto.await_count,1)


    async def test_exact_saved_business_uses_settings_before_billing_without_loading_ads_manager(self):
        browser=self.browser([{"href":URL,"label":"Billing & payments"}])
        result=await inspect_payment_methods(browser,ID,business_id="987654321")
        self.assertIn('/settings/ad_accounts?business_id=987654321',browser._goto.await_args_list[0].args[0])
        self.assertEqual(browser._goto.await_args_list[1].args[0],URL)
        self.assertEqual(result['verification_status'],'LINKED')


class SavedPaymentBusinessTests(unittest.TestCase):
    def test_requires_exact_profile_account_and_one_business(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'snapshot.json'
            rows=[{'id':'act_'+ID,'business_id':'987654321'}]
            path.write_text(json.dumps({'7':{'ad_accounts':rows}}))
            self.assertEqual(saved_payment_business('7',ID,path=path),'987654321')
            self.assertEqual(saved_payment_business('8',ID,path=path),'')
            self.assertEqual(saved_payment_business('7','111111111',path=path),'')
            rows.append({'id':ID,'business_id':'555555555'})
            path.write_text(json.dumps({'7':{'ad_accounts':rows}}))
            self.assertEqual(saved_payment_business('7',ID,path=path),'')
            path.write_text('broken')
            self.assertEqual(saved_payment_business('7',ID,path=path),'')

if __name__ == "__main__":
    unittest.main()
