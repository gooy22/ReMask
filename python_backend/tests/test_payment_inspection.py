import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import BrowserBusinessError
from app.payment_inspection import account_id, inspect_payment_methods, payment_summary

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
    def browser(self, links):
        menu=SimpleNamespace(count=AsyncMock(return_value=0),is_visible=AsyncMock(return_value=False),click=AsyncMock())
        page=SimpleNamespace(url=URL,evaluate=AsyncMock(return_value=links),wait_for_function=AsyncMock(),
            get_by_role=lambda role,**kwargs:menu,
            locator=lambda selector:SimpleNamespace(inner_text=AsyncMock(return_value=ID+"\nPayment methods\nVisa •••• 1234")))
        return SimpleNamespace(page=page,profile_id="Fixture",ADS_MANAGER_URL="https://adsmanager.facebook.com/adsmanager/manage/campaigns",_goto=AsyncMock(),_assert_authenticated=AsyncMock())

    async def test_follows_only_rendered_meta_billing_link_and_returns_masked_data(self):
        browser=self.browser([{"href":URL,"label":"Billing & payments"}])
        result=await inspect_payment_methods(browser,ID)
        self.assertEqual(browser._goto.await_count,2)
        self.assertEqual(browser._goto.await_args_list[1].args[0],URL)
        self.assertEqual(result["profile_id"],"Fixture")
        self.assertFalse(result["funding_verified"])

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
        browser.page.wait_for_function.assert_awaited_once()
        self.assertEqual(browser.page.wait_for_function.await_args.kwargs['timeout'],5000)
        self.assertEqual(result['verification_status'],'LINKED')

    async def test_hydration_timeout_does_not_invent_billing_destination(self):
        browser=self.browser([])
        browser.page.wait_for_function.side_effect=asyncio.TimeoutError()
        with self.assertRaises(BrowserBusinessError) as exc:
            await inspect_payment_methods(browser,ID)
        self.assertEqual(exc.exception.code,'PAYMENT_UI_UNAVAILABLE')
        self.assertEqual(browser._goto.await_count,1)


if __name__ == "__main__":
    unittest.main()
