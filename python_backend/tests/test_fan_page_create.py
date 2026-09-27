from __future__ import annotations

import inspect
import tempfile
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
from app.provisioning.state import ProvisioningStateStore


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
        handler_source = inspect.getsource(fan_pages_handler)
        self.assertIn("_attach_page_to_business", handler_source)
        self.assertIn("attach_existing", handler_source)


    def test_create_page_rejection_markers_cover_supported_geos(self) -> None:
        source = inspect.getsource(FacebookBusinessBrowser.create_fan_page)
        for marker in (
            "seite konnte nicht erstellt werden",
            "impossible de créer la page",
            "không thể tạo trang",
            "पेज नहीं बनाया जा सका",
            "পেজ তৈরি করা যায়নি",
        ):
            self.assertIn(marker, source)


class FanPageProvisioningRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_recovers_cross_scope_rk_and_confirmed_fan_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ProvisioningStateStore(tmp + "/state.sqlite3")
            await store.init()

            await store.set_running(
                "rk-item",
                "4",
                "add-rk-bm-7777777777",
                ProvisioningStep.AD_ACCOUNT,
            )
            await store.complete(
                "rk-item",
                "4",
                "add-rk-bm-7777777777",
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "DONE",
                    "business_id": "7777777777",
                    "ad_account_id": "8888888888",
                },
            )

            await store.set_running(
                "fp-item",
                "4",
                "add-fp-test",
                ProvisioningStep.FAN_PAGES,
            )
            await store.complete(
                "fp-item",
                "4",
                "add-fp-test",
                ProvisioningStep.FAN_PAGES,
                {
                    "phase": "DONE",
                    "category": "Digital creator",
                    "page_ids": ["9999999999"],
                    "pages": [
                        {
                            "id": "9999999999",
                            "name": "Brand Page",
                            "category": "Digital creator",
                            "reused": False,
                        }
                    ],
                },
            )

            entities = await store.latest_profile_entities("4")
            pages = await store.latest_profile_fan_pages("4")

            await store.set_running(
                "legacy-rk-item",
                "5",
                "add-rk-bm-1578458920690597",
                ProvisioningStep.AD_ACCOUNT,
            )
            await store.checkpoint(
                "legacy-rk-item",
                "5",
                "add-rk-bm-1578458920690597",
                ProvisioningStep.AD_ACCOUNT,
                {
                    "phase": "CREATE_RESULT_UNKNOWN",
                    "business_id": "1578458920690597",
                    "account_name": "ReMask RK 1",
                    "browser_diagnostic": {
                        "ui_state": {
                            "dialogs": [
                                "Ad account created successfully. "
                                "The ReMask RK 1 ad account has been created "
                                "and added to the Polr Dwol business portfolio."
                            ],
                            "controls": [
                                "ReMask RK 1",
                                "2490929708095829",
                            ],
                        }
                    },
                },
            )
            await store.fail(
                "legacy-rk-item",
                "5",
                "add-rk-bm-1578458920690597",
                ProvisioningStep.AD_ACCOUNT,
                "AD_ACCOUNT_CREATE_RESULT_UNKNOWN",
                "legacy capture missed",
            )

            bindings = await store.confirmed_ad_account_bindings()


        self.assertEqual(entities["business_id"], "7777777777")
        self.assertEqual(entities["ad_account_id"], "8888888888")
        self.assertEqual(
            bindings["4"]["ad_account_id"],
            "8888888888",
        )
        self.assertEqual(
            bindings["4"]["business_id"],
            "7777777777",
        )
        self.assertEqual(
            bindings["5"]["business_id"],
            "1578458920690597",
        )
        self.assertEqual(
            bindings["5"]["ad_account_id"],
            "2490929708095829",
        )
        self.assertEqual(
            bindings["5"]["source"],
            "python_worker_capture_ui_history",
        )
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "9999999999")
        self.assertEqual(pages[0]["source"], "python_worker_confirmed")

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

    async def test_handler_reuses_worker_confirmed_page_before_browser_create(self) -> None:
        state = SimpleNamespace(
            checkpoint=AsyncMock(return_value={}),
            step=AsyncMock(return_value=None),
            latest_profile_fan_pages=AsyncMock(
                return_value=[
                    {
                        "id": "9999999999",
                        "name": "Brand Page",
                        "category": "Digital creator",
                        "source": "python_worker_confirmed",
                    }
                ]
            ),
        )
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False

        with patch(
            "app.provisioning.fan_pages_handler.FacebookBusinessBrowser",
            return_value=browser,
        ):
            result = await fan_pages_handler(
                SimpleNamespace(context=SimpleNamespace(profile_id="4")),
                {
                    "base_name": "Brand Page",
                    "count": 1,
                    "category": "Digital creator",
                },
                {},
                item_id="item-reuse",
                profile_id="4",
                scope_key="fan-pages-reuse",
                provisioning_state=state,
                step_state={"result": {}},
            )

        self.assertEqual(result["page_ids"], ["9999999999"])
        self.assertEqual(result["created_count"], 1)
        self.assertTrue(result["pages"][0]["reused"])
        state.latest_profile_fan_pages.assert_awaited()
        browser.create_fan_page.assert_not_awaited()

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

    async def test_create_page_can_attach_to_selected_rk_business(self) -> None:
        state = SimpleNamespace(
            checkpoint=AsyncMock(return_value={}),
            step=AsyncMock(return_value=None),
            latest_profile_fan_pages=AsyncMock(return_value=[]),
        )
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False
        browser.discover_managed_pages.return_value = []
        browser.create_fan_page.return_value = {
            "page_id": "4444444444",
            "name": "RK Page",
            "category": "Digital creator",
            "reused": False,
        }
        browser.add_existing_page.return_value = SimpleNamespace(
            already_attached=False
        )

        with patch(
            "app.provisioning.fan_pages_handler.FacebookBusinessBrowser",
            return_value=browser,
        ):
            result = await fan_pages_handler(
                SimpleNamespace(context=SimpleNamespace(profile_id="6")),
                {
                    "mode": "create",
                    "base_name": "RK Page",
                    "count": 1,
                    "category": "Digital creator",
                    "business_id": "1619103589770310",
                    "ad_account_id": "29459808963612032",
                },
                {},
                item_id="item-rk-page",
                profile_id="6",
                scope_key="rk-page-create",
                provisioning_state=state,
                step_state={"result": {}},
            )

        self.assertEqual(result["attached_count"], 1)
        self.assertEqual(result["business_id"], "1619103589770310")
        self.assertEqual(result["ad_account_id"], "29459808963612032")
        self.assertTrue(result["pages"][0]["attached"])
        self.assertEqual(
            result["pages"][0]["business_id"],
            "1619103589770310",
        )
        self.assertEqual(browser.create_fan_page.await_count, 1)
        self.assertEqual(browser.add_existing_page.await_count, 1)

    async def test_existing_page_can_attach_without_create(self) -> None:
        state = SimpleNamespace(
            checkpoint=AsyncMock(return_value={}),
            step=AsyncMock(return_value=None),
            latest_profile_fan_pages=AsyncMock(return_value=[]),
        )
        browser = AsyncMock()
        browser.__aenter__.return_value = browser
        browser.__aexit__.return_value = False
        browser.add_existing_page.return_value = SimpleNamespace(
            already_attached=False
        )

        with patch(
            "app.provisioning.fan_pages_handler.FacebookBusinessBrowser",
            return_value=browser,
        ):
            result = await fan_pages_handler(
                SimpleNamespace(context=SimpleNamespace(profile_id="6")),
                {
                    "mode": "attach_existing",
                    "existing_page_id": "5555555555",
                    "page_name": "Existing FP",
                    "business_id": "1619103589770310",
                    "ad_account_id": "29459808963612032",
                },
                {},
                item_id="item-rk-page-attach",
                profile_id="6",
                scope_key="rk-page-attach",
                provisioning_state=state,
                step_state={"result": {}},
            )

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["attached_count"], 1)
        self.assertEqual(result["page_ids"], ["5555555555"])
        self.assertTrue(result["pages"][0]["attached"])
        self.assertEqual(browser.create_fan_page.await_count, 0)
        self.assertEqual(browser.add_existing_page.await_count, 1)
