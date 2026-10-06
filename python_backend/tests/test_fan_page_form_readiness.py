from __future__ import annotations

import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser


class FanPageFormReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_unbound_required_fields_fill_in_real_chromium(self):
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
                await page.set_content("""
                    <input aria-label="Page name" hidden>
                    <div id="form"></div>
                    <script>
                    window.submits = 0;
                    setTimeout(() => {
                      document.querySelector('#form').innerHTML = `
                        <div><span>Page name (required)</span><input id="pageName"></div>
                        <div><span>Category (required)</span><input id="category"></div>
                        <div id="choices"></div>
                        <button id="create" disabled>Create Page</button>`;
                      document.querySelector('#category').oninput = () => {
                        document.querySelector('#choices').innerHTML =
                          '<div role="option">Digital creator</div>';
                        document.querySelector('[role="option"]').onclick = () => {
                          window.categorySelected = true;
                          document.querySelector('#choices').innerHTML = '';
                          document.querySelector('#create').disabled = false;
                        };
                      };
                      document.querySelector('#create').onclick = () => window.submits++;
                    }, 650);
                    </script>
                """)
                browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="13"))
                browser.page = page
                browser._goto = AsyncMock()
                submit = AsyncMock()
                with self.assertRaises(BrowserBusinessError) as caught:
                    await browser.create_fan_page(
                        page_name="Fixture Page", category="Digital creator",
                        before_pages=[], before_submit=submit, require_policy_consent=True)
                self.assertEqual(caught.exception.code, "PAGE_POLICIES_CONFIRMATION_REQUIRED")
                self.assertEqual(await page.locator("#pageName").input_value(), "Fixture Page")
                self.assertEqual(await page.locator("#category").input_value(), "Digital creator")
                self.assertTrue(await page.evaluate("window.categorySelected"))
                self.assertEqual(await page.evaluate("window.submits"), 0)
                submit.assert_not_awaited()
            finally:
                await chromium.close()

    async def test_ambiguous_unbound_label_never_chooses_field_by_position(self):
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
                await page.set_content(
                    '<div><span>Category (required)</span><input id="first"><input id="second"></div>')
                browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="13"))
                browser.page = page
                self.assertIsNone(await browser._fan_page_input(("Category",)))
                self.assertEqual(await page.locator("#first").input_value(), "")
                self.assertEqual(await page.locator("#second").input_value(), "")
            finally:
                await chromium.close()

    async def test_auth_gate_during_hydration_stops_before_category_or_submit(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="12"))
        browser.page = SimpleNamespace(wait_for_timeout=AsyncMock())
        browser._goto = AsyncMock()
        browser._fan_page_input = AsyncMock(return_value=None)
        browser._assert_authenticated = AsyncMock(side_effect=BrowserBusinessError(
            "CHECKPOINT_REQUIRED", "Facebook checkpoint", retryable=False))
        browser._fill_fan_page_category = AsyncMock()
        browser._click_named_single_attempt = AsyncMock()
        submit = AsyncMock()
        with self.assertRaises(BrowserBusinessError) as caught:
            await browser.create_fan_page(
                page_name="Fixture Page", category="Digital creator",
                before_pages=[], before_submit=submit)
        self.assertEqual(caught.exception.code, "CHECKPOINT_REQUIRED")
        browser._goto.assert_awaited_once()
        browser._fill_fan_page_category.assert_not_awaited()
        browser._click_named_single_attempt.assert_not_awaited()
        submit.assert_not_awaited()

    async def test_missing_field_wait_is_bounded_without_reload_or_submit(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="13"))
        browser.page = SimpleNamespace(wait_for_timeout=AsyncMock())
        browser._fan_page_input = AsyncMock(return_value=None)
        browser._assert_authenticated = AsyncMock()
        browser._goto = AsyncMock()
        browser._click_named_single_attempt = AsyncMock()
        self.assertFalse(await browser._fill_fan_page_name("Fixture Page"))
        self.assertEqual(browser._fan_page_input.await_count, 21)
        self.assertEqual(browser.page.wait_for_timeout.await_count, 20)
        browser._goto.assert_not_awaited()
        browser._click_named_single_attempt.assert_not_awaited()
