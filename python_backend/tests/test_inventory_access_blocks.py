from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.facebook_business_browser import (
    BROWSER_TERMINAL_ACCESS_CODES,
    BrowserBusinessError,
    FacebookBusinessBrowser,
)
from app.provisioning.ad_account_handler import (
    _reconcile_existing_browser_inventory,
    _verify_expected_ad_account_in_business,
    ad_account_handler,
)
from app.provisioning.models import ProvisioningError


class InventoryAccessBlockTests(unittest.IsolatedAsyncioTestCase):
    def browser(self, code: str) -> FacebookBusinessBrowser:
        browser = object.__new__(FacebookBusinessBrowser)
        browser.page = SimpleNamespace(
            url="", on=MagicMock(), remove_listener=MagicMock(),
        )
        browser._goto = AsyncMock(side_effect=BrowserBusinessError(
            code, "Facebook access blocked", retryable=False,
            diagnostic={"auth_evidence": "checkpoint_url"},
        ))
        return browser

    async def test_graphql_inventory_stops_on_first_access_block(self) -> None:
        for code in BROWSER_TERMINAL_ACCESS_CODES:
            with self.subTest(code=code):
                browser = self.browser(code)
                with self.assertRaises(BrowserBusinessError) as caught:
                    await browser.find_ad_account_in_inventory(
                        business_id="2478360152656679", account_name="Cedar Ads",
                    )
                self.assertEqual(caught.exception.code, code)
                browser._goto.assert_awaited_once()
                browser.page.remove_listener.assert_called_once()

    async def test_ui_inventory_stops_on_first_access_block(self) -> None:
        for code in BROWSER_TERMINAL_ACCESS_CODES:
            with self.subTest(code=code):
                browser = self.browser(code)
                with self.assertRaises(BrowserBusinessError) as caught:
                    await browser.verify_ad_account_inventory_empty(
                        business_id="2478360152656679",
                    )
                self.assertEqual(caught.exception.code, code)
                browser._goto.assert_awaited_once()

    async def test_navigation_error_remains_inconclusive(self) -> None:
        browser = self.browser("FACEBOOK_NAVIGATION_FAILED")
        result = await browser.verify_ad_account_inventory_empty(
            business_id="2478360152656679",
        )
        self.assertFalse(result["confirmed_empty"])
        self.assertGreater(browser._goto.await_count, 1)

    async def test_reconciliation_preserves_checkpoint_and_skips_ui_fallback(self) -> None:
        instance = SimpleNamespace(
            find_ad_account_in_inventory=AsyncMock(side_effect=BrowserBusinessError(
                "CHECKPOINT_REQUIRED", "Facebook requires a checkpoint", retryable=False,
            )),
            verify_ad_account_inventory_empty=AsyncMock(),
        )
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=instance)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("app.provisioning.ad_account_handler.FacebookBusinessBrowser", factory):
            with self.assertRaises(ProvisioningError) as caught:
                await _reconcile_existing_browser_inventory(
                    SimpleNamespace(context=SimpleNamespace()),
                    business_id="2478360152656679", account_name="Cedar Ads",
                )
        self.assertEqual(caught.exception.code, "CHECKPOINT_REQUIRED")
        self.assertFalse(caught.exception.retryable)
        instance.find_ad_account_in_inventory.assert_awaited_once()
        instance.verify_ad_account_inventory_empty.assert_not_awaited()
        factory.return_value.__aexit__.assert_awaited_once()

    async def test_ui_fallback_access_block_is_not_generic_inventory_error(self) -> None:
        instance = SimpleNamespace(
            find_ad_account_in_inventory=AsyncMock(return_value={"confirmed_empty": False}),
            verify_ad_account_inventory_empty=AsyncMock(side_effect=BrowserBusinessError(
                "SESSION_EXPIRED", "Facebook redirected to login", retryable=False,
            )),
        )
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=instance)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("app.provisioning.ad_account_handler.FacebookBusinessBrowser", factory):
            with self.assertRaises(ProvisioningError) as caught:
                await _reconcile_existing_browser_inventory(
                    SimpleNamespace(context=SimpleNamespace()),
                    business_id="2478360152656679", account_name="Cedar Ads",
                )
        self.assertEqual(caught.exception.code, "SESSION_EXPIRED")
        self.assertFalse(caught.exception.retryable)

    async def test_expected_id_verification_does_not_repeat_access_block(self) -> None:
        with patch(
            "app.provisioning.ad_account_handler._reconcile_existing_browser_inventory",
            new=AsyncMock(side_effect=ProvisioningError(
                "CHECKPOINT_REQUIRED", "Facebook checkpoint", retryable=False,
            )),
        ) as reconcile:
            with self.assertRaises(ProvisioningError):
                await _verify_expected_ad_account_in_business(
                    SimpleNamespace(), business_id="2478360152656679",
                    account_name="Cedar Ads", expected_ad_account_id="123456789",
                    checks=3, delay_seconds=0,
                )
        reconcile.assert_awaited_once()

    def state(self):
        return SimpleNamespace(
            remember_entity=AsyncMock(),
            step=AsyncMock(return_value={}),
            latest_ad_account_resume_for_business=AsyncMock(return_value=None),
            checkpoint=AsyncMock(return_value={}),
        )

    async def test_fresh_create_saves_auth_block_before_any_submit(self) -> None:
        store = self.state()
        with patch(
            "app.provisioning.ad_account_handler._reconcile_existing_browser_inventory",
            new=AsyncMock(side_effect=ProvisioningError(
                "CHECKPOINT_REQUIRED", "Facebook checkpoint", retryable=False,
            )),
        ) as reconcile, patch(
            "app.provisioning.ad_account_handler.create_ad_account_with_docids",
            new=AsyncMock(),
        ) as create:
            with self.assertRaises(ProvisioningError) as caught:
                await ad_account_handler(
                    SimpleNamespace(context=SimpleNamespace(profile_id="7")),
                    {"business_id":"2478360152656679", "name":"Cedar Ads",
                     "currency":"USD", "timezone_id":1},
                    {"profile_id":"7"}, item_id="new-rk-item",
                    provisioning_state=store,
                )
        self.assertEqual(caught.exception.code, "CHECKPOINT_REQUIRED")
        self.assertFalse(caught.exception.retryable)
        reconcile.assert_awaited_once()
        create.assert_not_awaited()
        saved = store.checkpoint.await_args.args[-1]
        self.assertEqual(saved["phase"], "CREATE_NOT_SUBMITTED")
        self.assertEqual(saved["last_error_code"], "CHECKPOINT_REQUIRED")
        self.assertTrue(saved["auth_blocked"])
        self.assertNotIn("ad_account_id", saved)

    async def test_access_block_preserves_post_submit_uncertainty(self) -> None:
        store = self.state()
        with patch(
            "app.provisioning.ad_account_handler._reconcile_existing_browser_inventory",
            new=AsyncMock(side_effect=ProvisioningError(
                "CHECKPOINT_REQUIRED", "Facebook checkpoint", retryable=False,
            )),
        ) as reconcile, patch(
            "app.provisioning.ad_account_handler.create_ad_account_with_docids",
            new=AsyncMock(),
        ) as create:
            with self.assertRaises(ProvisioningError):
                await ad_account_handler(
                    SimpleNamespace(context=SimpleNamespace(profile_id="7")),
                    {"business_id":"2478360152656679", "name":"Cedar Ads",
                     "currency":"USD", "timezone_id":1},
                    {"profile_id":"7"}, item_id="uncertain-rk-item",
                    provisioning_state=store,
                    step_state={"result":{"phase":"CREATE_RESULT_UNKNOWN",
                                          "business_id":"2478360152656679"}},
                )
        reconcile.assert_awaited_once()
        create.assert_not_awaited()
        for call in store.checkpoint.await_args_list:
            self.assertNotEqual(call.args[-1].get("phase"), "CREATE_NOT_SUBMITTED")
