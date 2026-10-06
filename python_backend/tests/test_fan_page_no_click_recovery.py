from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.facebook_fan_page_create import fan_page_click_never_resolved
from app.provisioning.fan_pages_handler import fan_pages_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore


ERROR = '''TimeoutError: Locator.click: Timeout 5000ms exceeded.
Call log:
  - waiting for get_by_role("button", name=re.compile(r"^\s*Create\ Page\s*$", re.IGNORECASE)).first
'''
NAMES = FacebookBusinessBrowser.FAN_PAGE_CREATE_NAMES


def click_meta():
    return {"found": True, "attempted": True, "clicked": False, "name": "Create Page",
            "role": "button", "index": 0, "error": ERROR}


class FanPageNoClickTests(unittest.IsolatedAsyncioTestCase):
    def test_complete_unresolved_locator_trace_proves_no_dispatch(self):
        self.assertTrue(fan_page_click_never_resolved(click_meta(), allowed_names=NAMES))

    def test_ambiguous_truncated_post_click_and_untrusted_traces_keep_guard(self):
        variants = [
            {"clicked": True}, {"attempted": False}, {"found": False},
            {"role": "link"}, {"name": "Create Business"},
            {"error": ERROR + "  - locator resolved to button\n"},
            {"error": ERROR + "  - click action done\n  - waiting for scheduled navigations\n"},
            {"error": ERROR[:30]}, {"error": ERROR + " " * 500},
            {"error": "TimeoutError: navigation timeout"},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                meta = click_meta(); meta.update(variant)
                self.assertFalse(fan_page_click_never_resolved(meta, allowed_names=NAMES))

    async def test_legacy_pending_no_click_is_recovered_and_old_proof_cleared_before_new_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(tmp + "/state.sqlite")
            await state.init()
            await state.set_running("common", "13", "common-page", ProvisioningStep.FAN_PAGES)
            await state.checkpoint("common", "13", "common-page", ProvisioningStep.FAN_PAGES, {
                "phase": "PAGE_CREATE_RESULT_UNKNOWN", "active_page_name": "PrgssTeam",
                "active_before_ids": [], "target_names": ["PrgssTeam"],
                "browser_diagnostic": {"stage": "fan_page_final_click_unknown", "click_meta": click_meta()},
            })
            browser = AsyncMock()
            browser.__aenter__.return_value = browser
            browser.__aexit__.return_value = False

            async def create(**kwargs):
                await kwargs["before_submit"]({"before_ids": []})
                current = await state.step("common", ProvisioningStep.FAN_PAGES)
                self.assertEqual(current["result"]["phase"], "PAGE_CREATE_CLICK_INTENT")
                self.assertEqual(current["result"]["browser_diagnostic"], {})
                return {"page_id": "2222222222", "name": "PrgssTeam", "reused": False}
            browser.create_fan_page.side_effect = create
            session = SimpleNamespace(context=SimpleNamespace(profile_id="13", pages=[]))
            with patch("app.provisioning.fan_pages_handler.FacebookBusinessBrowser", return_value=browser) as factory, \
                 patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(return_value=[])), \
                 patch("app.provisioning.fan_pages_handler._reconcile_uncertain_page", new=AsyncMock()) as reconcile:
                factory.FAN_PAGE_CREATE_NAMES = NAMES
                result = await fan_pages_handler(session, {"names": ["PrgssTeam"]}, {},
                    provisioning_state=state, item_id="common", profile_id="13", scope_key="common-page")
            self.assertEqual(result["pages"][0]["id"], "2222222222")
            browser.create_fan_page.assert_awaited_once()
            reconcile.assert_not_awaited()
            current = await state.step("common", ProvisioningStep.FAN_PAGES)
            self.assertEqual(current["result"]["recovery_reason"], "LOCATOR_NEVER_RESOLVED")
            self.assertEqual(current["result"]["phase"], "PAGE_CREATED")

    async def test_fresh_proven_no_click_retries_once_and_never_becomes_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(tmp + "/state.sqlite")
            await state.init()
            browser = AsyncMock()
            browser.__aenter__.return_value = browser
            browser.__aexit__.return_value = False

            async def missing(**kwargs):
                await kwargs["before_submit"]({"before_ids": []})
                raise BrowserBusinessError("FAN_PAGE_CREATE_NOT_SUBMITTED", "not sent", retryable=True,
                    diagnostic={"click_meta": click_meta(), "safe_before_submit": True})
            browser.create_fan_page.side_effect = missing
            session = SimpleNamespace(context=SimpleNamespace(profile_id="fixture", pages=[]))
            with patch("app.provisioning.fan_pages_handler.FacebookBusinessBrowser", return_value=browser) as factory, \
                 patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(return_value=[])), \
                 patch("app.provisioning.fan_pages_handler._reconcile_uncertain_page", new=AsyncMock()) as reconcile:
                factory.FAN_PAGE_CREATE_NAMES = NAMES
                with self.assertRaises(ProvisioningError) as caught:
                    await fan_pages_handler(session, {"names": ["PrgssTeam"]}, {},
                        provisioning_state=state, item_id="new", profile_id="fixture", scope_key="default")
            self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_UI_CHANGED")
            self.assertEqual(browser.create_fan_page.await_count, 2)
            reconcile.assert_not_awaited()
            current = await state.step("new", ProvisioningStep.FAN_PAGES)
            self.assertEqual(current["result"]["phase"], "CREATE_NOT_SUBMITTED")
            self.assertEqual(current["result"]["browser_diagnostic"], {})

    async def test_real_chromium_button_removed_after_intent_never_dispatches_click(self):
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
                await page.set_content('<button onclick="window.submits++">Create Page</button>'
                                       '<script>window.submits=0;</script>')
                browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="fixture"))
                browser.page = page
                intents = []

                async def persist_intent():
                    intents.append("PAGE_CREATE_CLICK_INTENT")
                    await page.locator("button").evaluate("(el) => el.remove()")
                meta = await browser._click_named_single_attempt(
                    ("Create Page",), roles=("button",), before_click=persist_intent,
                    click_timeout_ms=350, trial_timeout_ms=750)
                self.assertTrue(fan_page_click_never_resolved(meta, allowed_names=NAMES), str(meta))
                self.assertEqual(intents, ["PAGE_CREATE_CLICK_INTENT"])
                self.assertEqual(await page.evaluate("window.submits"), 0)
            finally:
                await chromium.close()
