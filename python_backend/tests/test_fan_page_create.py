from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import FacebookBusinessBrowser
from app.provisioning.fan_pages_handler import (
    _reconcile_uncertain_page,
    _target_names,
    fan_pages_handler,
)
from app.provisioning.models import ProvisioningStep
from app.provisioning.registry import PROVISIONING_HANDLERS


class FanPageProvisioningStructureTests(unittest.TestCase):
    def test_step_is_registered(self) -> None:
        self.assertEqual(ProvisioningStep.FAN_PAGES.value, "FAN_PAGES")
        self.assertIs(PROVISIONING_HANDLERS["FAN_PAGES"], fan_pages_handler)

    def test_two_page_names_are_deterministic(self) -> None:
        self.assertEqual(
            _target_names({"base_name": "Brand Page", "count": 2}),
            ["Brand Page 1", "Brand Page 2"],
        )

    def test_browser_uses_page_create_ui_and_single_final_click(self) -> None:
        source = inspect.getsource(FacebookBusinessBrowser.create_fan_page)
        self.assertIn("FAN_PAGE_CREATE_URLS", source)
        self.assertIn("_click_named_single_attempt", source)
        self.assertIn("before_ids", source)
        self.assertIn("discover_managed_pages", source)
        self.assertIn("FAN_PAGE_CREATE_RESULT_UNKNOWN", source)


class FanPageProvisioningRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_create_can_prove_brand_new_account_still_empty(self) -> None:
        with patch(
            "app.provisioning.fan_pages_handler._fresh_page_inventory",
            new=AsyncMock(return_value=[]),
        ) as inventory:
            found, proven_absent, diagnostics = await _reconcile_uncertain_page(
                SimpleNamespace(context=SimpleNamespace(profile_id="4")),
                page_name="Brand Page 1",
                before_ids=set(),
                checks=3,
            )

        self.assertIsNone(found)
        self.assertTrue(proven_absent)
        self.assertEqual(inventory.await_count, 3)
        self.assertEqual(len(diagnostics), 3)

    async def test_handler_creates_two_pages_and_checkpoints_each(self) -> None:
        state = SimpleNamespace(
            checkpoint=AsyncMock(return_value={}),
            step=AsyncMock(return_value=None),
        )
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False
        browser.discover_managed_pages.return_value = [
            {"id": "1111111111", "name": "Existing Page"}
        ]

        async def create_page(*, page_name, category, bio, before_submit):
            await before_submit(
                {
                    "before_ids": ["1111111111"],
                    "page_name": page_name,
                }
            )
            page_id = "2222222222" if page_name.endswith("1") else "3333333333"
            return {
                "page_id": page_id,
                "name": page_name,
                "category": category,
                "reused": False,
            }

        browser.create_fan_page.side_effect = create_page

        with patch(
            "app.provisioning.fan_pages_handler.FacebookBusinessBrowser",
            return_value=browser,
        ):
            result = await fan_pages_handler(
                SimpleNamespace(context=SimpleNamespace(profile_id="4")),
                {
                    "base_name": "Brand Page",
                    "count": 2,
                    "category": "Digital creator",
                },
                {},
                item_id="item-1",
                profile_id="4",
                scope_key="fan-pages-test",
                provisioning_state=state,
                step_state={"result": {}},
            )

        self.assertEqual(result["page_ids"], ["2222222222", "3333333333"])
        self.assertEqual(result["created_count"], 2)
        self.assertGreaterEqual(state.checkpoint.await_count, 4)
        self.assertEqual(browser.create_fan_page.await_count, 2)
