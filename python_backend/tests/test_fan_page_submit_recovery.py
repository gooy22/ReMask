from __future__ import annotations

import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.provisioning.fan_pages_handler import _fresh_page_inventory


class FanPageSubmitRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def browser(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="13", pages=[]))
        browser.page = SimpleNamespace(
            url="https://www.facebook.com/pages/creation/",
            wait_for_timeout=AsyncMock(),
        )
        browser._goto = AsyncMock()
        browser._fill_fan_page_name = AsyncMock(return_value=True)
        browser._fill_fan_page_category = AsyncMock(return_value=True)
        browser._assert_authenticated = AsyncMock()
        browser._body_text = AsyncMock(return_value="")
        browser._diagnostic = AsyncMock(return_value={
            "stage": "fan_page_final_click_unknown", "url": browser.page.url,
            "visible_controls": [{"text": "Create Page"}],
        })

        async def click(*args, before_click=None, **kwargs):
            await before_click()
            return {"found": True, "attempted": True, "clicked": False,
                    "error": "TimeoutError: final navigation did not settle"}

        browser._click_named_single_attempt = AsyncMock(side_effect=click)
        return browser

    async def create(self, browser, submit=None):
        with patch("app.facebook_business_browser.asyncio.sleep", new=AsyncMock()):
            return await browser.create_fan_page(
                page_name="Fixture Page", category="Digital creator",
                before_pages=[{"id": "1111111111", "name": "Old Page"}],
                before_submit=submit,
            )

    async def test_click_timeout_recovers_confirmed_page_without_second_create(self):
        browser = self.browser()
        rows = [{"id": "2222222222", "name": "Fixture Page"}]
        browser.discover_managed_pages = AsyncMock(side_effect=[
            BrowserBusinessError("FAN_PAGES_NOT_DISCOVERED", "cold shell"), rows,
        ])
        submit = AsyncMock()
        result = await self.create(browser, submit)
        self.assertEqual(result["page_id"], "2222222222")
        self.assertEqual(result["transport"], "facebook_pages_ui_click_error_inventory_diff")
        self.assertEqual(browser.context.pages, rows)
        browser._click_named_single_attempt.assert_awaited_once()
        submit.assert_awaited_once()
        self.assertEqual(submit.await_args.args[0]["phase"], "PAGE_CREATE_CLICK_INTENT")
        self.assertEqual(browser.discover_managed_pages.await_count, 2)

    async def test_closed_document_preserves_unknown_and_original_click_diagnostic(self):
        browser = self.browser()
        browser._diagnostic.side_effect = RuntimeError("Target page closed")
        browser._assert_authenticated.side_effect = RuntimeError("Target page closed")
        browser._body_text.side_effect = RuntimeError("Target page closed")
        browser.discover_managed_pages = AsyncMock(side_effect=RuntimeError("Target page closed"))
        submit = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.create(browser, submit)
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_RESULT_UNKNOWN")
        diag = caught.exception.diagnostic
        self.assertEqual(diag["stage"], "fan_page_final_click_unknown")
        self.assertEqual(diag["before_ids"], ["1111111111"])
        self.assertTrue(diag["click_meta"]["attempted"])
        self.assertIn("TimeoutError", diag["click_meta"]["error"])
        self.assertEqual(diag["url"], browser.page.url)
        self.assertEqual(len(diag["confirmation_checks"]), 4)
        browser._click_named_single_attempt.assert_awaited_once()
        submit.assert_awaited_once()

    async def test_ambiguous_wrong_name_and_baseline_ids_do_not_confirm(self):
        cases = [
            [{"id": "2222222222", "name": "Fixture Page"},
             {"id": "3333333333", "name": "Fixture Page"}],
            [{"id": "2222222222", "name": "Other Page"}],
            [{"id": "1111111111", "name": "Fixture Page"}],
            [],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                browser = self.browser()
                browser.discover_managed_pages = AsyncMock(return_value=rows)
                with self.assertRaises(BrowserBusinessError) as caught:
                    await self.create(browser)
                self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_RESULT_UNKNOWN")
                browser._click_named_single_attempt.assert_awaited_once()

    async def test_auth_redirect_after_click_retains_actual_error_before_inventory(self):
        browser = self.browser()
        browser._assert_authenticated.side_effect = BrowserBusinessError(
            "CHECKPOINT_REQUIRED", "real auth redirect", retryable=False)
        browser.discover_managed_pages = AsyncMock()
        submit = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.create(browser, submit)
        self.assertEqual(caught.exception.code, "CHECKPOINT_REQUIRED")
        submit.assert_awaited_once()
        browser.discover_managed_pages.assert_not_awaited()
        browser._click_named_single_attempt.assert_awaited_once()

    async def test_explicit_rejection_after_click_timeout_is_not_success(self):
        browser = self.browser()
        browser._body_text.return_value = "We couldn't create page"
        browser.discover_managed_pages = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.create(browser)
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_REJECTED")
        browser.discover_managed_pages.assert_not_awaited()
        browser._click_named_single_attempt.assert_awaited_once()

    async def test_auth_failure_during_confirmation_stops_reads(self):
        browser = self.browser()
        browser.discover_managed_pages = AsyncMock(side_effect=BrowserBusinessError(
            "SESSION_EXPIRED", "login redirect", retryable=False))
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.create(browser)
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")
        browser.discover_managed_pages.assert_awaited_once()
        browser._click_named_single_attempt.assert_awaited_once()

    async def test_unactionable_submit_never_writes_intent_or_checks_created_inventory(self):
        browser = self.browser()
        browser._click_named_single_attempt = AsyncMock(return_value={
            "found": True, "attempted": False, "clicked": False,
            "actionability_failed": True, "error": "TimeoutError: overlay intercepts pointer events",
        })
        browser._diagnostic.return_value = {"stage": "fan_page_create_submit_not_actionable"}
        browser.discover_managed_pages = AsyncMock()
        submit = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.create(browser, submit)
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_UI_CHANGED")
        self.assertTrue(caught.exception.diagnostic["safe_before_submit"])
        self.assertEqual(browser._click_named_single_attempt.await_count, 9)
        submit.assert_not_awaited()
        browser.discover_managed_pages.assert_not_awaited()

    async def test_real_chromium_actionability_probe_sends_no_click_and_no_intent(self):
        executable = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                           if (path := shutil.which(name))), None)
        if not executable:
            self.skipTest("No local Chromium installed")
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(
                executable_path=executable, headless=True, args=["--no-sandbox"])
            try:
                page = await chromium.new_page()
                await page.set_content('''<button onclick="window.submits++">Create Page</button>
                    <div id="overlay" style="position:fixed;inset:0;background:white"></div>
                    <script>window.submits=0;</script>''')
                browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="13"))
                browser.page = page
                submit = AsyncMock()
                meta = await browser._click_named_single_attempt(
                    ("Create Page",), roles=("button",), before_click=submit, trial_timeout_ms=300)
                self.assertTrue(meta['actionability_failed'])
                self.assertFalse(meta['attempted'])
                self.assertEqual(await page.evaluate('window.submits'), 0)
                submit.assert_not_awaited()
                await page.locator('#overlay').evaluate('(el) => el.remove()')
                meta = await browser._click_named_single_attempt(
                    ("Create Page",), roles=("button",), before_click=submit, trial_timeout_ms=750)
                self.assertTrue(meta['clicked'])
                self.assertEqual(await page.evaluate('window.submits'), 1)
                submit.assert_awaited_once()
            finally:
                await chromium.close()

    async def test_saved_attempt_inventory_warms_same_lease(self):
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False
        rows = [{"id": "2222222222", "name": "Fixture Page"}]
        browser.discover_managed_pages.side_effect = [
            BrowserBusinessError("FAN_PAGES_NOT_DISCOVERED", "cold shell"), rows,
        ]
        session = SimpleNamespace(context=SimpleNamespace(profile_id="13", pages=[]))
        with patch("app.provisioning.fan_pages_handler.FacebookBusinessBrowser",
                   return_value=browser) as factory:
            found = await _fresh_page_inventory(session)
        self.assertEqual(found[0]["id"], "2222222222")
        self.assertEqual(session.context.pages, rows)
        factory.assert_called_once()
        browser.__aenter__.assert_awaited_once()
        browser.page.wait_for_timeout.assert_awaited_once_with(750)
        self.assertEqual(browser.discover_managed_pages.await_count, 2)

    async def test_saved_attempt_auth_failure_never_retries_read(self):
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False
        browser.discover_managed_pages.side_effect = BrowserBusinessError(
            "CHECKPOINT_REQUIRED", "auth required", retryable=False)
        with patch("app.provisioning.fan_pages_handler.FacebookBusinessBrowser", return_value=browser):
            with self.assertRaises(BrowserBusinessError):
                await _fresh_page_inventory(SimpleNamespace(context=SimpleNamespace(profile_id="13")))
        browser.discover_managed_pages.assert_awaited_once()
        browser.page.wait_for_timeout.assert_not_awaited()

    async def test_real_chromium_click_changes_document_then_confirmation_recovers(self):
        executable = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                           if (path := shutil.which(name))), None)
        if not executable:
            self.skipTest("No local Chromium installed")
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(
                executable_path=executable, headless=True, args=["--no-sandbox"])
            try:
                page = await chromium.new_page()
                await page.set_content('''
                    <button>Create Page</button><script>
                    window.submits = 0;
                    document.querySelector('button').onclick = () => {
                        window.submits++;
                        document.body.innerHTML = '<p>Setting up your Page</p>';
                        setTimeout(() => {
                            window.pages = [{id:'2222222222', name:'Fixture Page'}];
                        }, 100);
                    };
                    </script>''')
                browser = self.browser()
                browser.page = page
                single_click = FacebookBusinessBrowser._click_named_single_attempt.__get__(browser)

                async def uncertain_click(*args, **kwargs):
                    meta = await single_click(*args, **kwargs)
                    self.assertTrue(meta['clicked'])
                    meta.update(clicked=False, error="TimeoutError: result context changed")
                    return meta

                async def inventory(**kwargs):
                    await page.wait_for_function("() => window.pages && window.pages.length")
                    return await page.evaluate("window.pages")

                browser._click_named_single_attempt = AsyncMock(side_effect=uncertain_click)
                browser.discover_managed_pages = AsyncMock(side_effect=inventory)
                submit = AsyncMock()
                result = await self.create(browser, submit)
                self.assertEqual(result['page_id'], '2222222222')
                self.assertEqual(await page.evaluate('window.submits'), 1)
                submit.assert_awaited_once()
                browser._click_named_single_attempt.assert_awaited_once()
            finally:
                await chromium.close()
