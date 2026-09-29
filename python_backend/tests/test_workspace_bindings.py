from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.provisioning.models import ProvisioningStep
from app.provisioning.state import ProvisioningStateStore


class WorkspaceProvisioningBindingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProvisioningStateStore(
            str(Path(self.tmp.name) / "provisioning-state.sqlite3")
        )
        await self.store.init()

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_one_profile_keeps_rk_binding_for_each_business(self) -> None:
        profile = "6"
        await self.store.complete(
            "item-a",
            profile,
            "add-rk-bm-1578458920690597",
            ProvisioningStep.AD_ACCOUNT,
            {
                "phase": "DONE",
                "business_id": "1578458920690597",
                "ad_account_id": "act_2490929708095829",
                "account_name": "Polr Dwol RK",
            },
        )
        await self.store.complete(
            "item-b",
            profile,
            "add-rk-bm-1619103589770310",
            ProvisioningStep.AD_ACCOUNT,
            {
                "phase": "DONE",
                "business_id": "1619103589770310",
                "ad_account_id": "act_29459808963612032",
                "account_name": "Foldf Golld RK",
            },
        )

        rows = await self.store.confirmed_ad_account_bindings_for_profile(
            profile
        )
        groups = await self.store.confirmed_ad_account_binding_groups()

        expected = {
            ("1578458920690597", "2490929708095829"),
            ("1619103589770310", "29459808963612032"),
        }
        self.assertEqual(
            {
                (row["business_id"], row["ad_account_id"])
                for row in rows
            },
            expected,
        )
        self.assertEqual(len(rows), 2)

        profile_accounts = groups[profile]["ad_accounts"]
        self.assertEqual(set(profile_accounts), {
            "1578458920690597",
            "1619103589770310",
        })
        self.assertEqual(
            {
                (row["business_id"], row["ad_account_id"])
                for row in profile_accounts.values()
            },
            expected,
        )

    async def test_confirmed_create_survives_page_attach_failure_state(self) -> None:
        profile = "7"
        await self.store.remember_entity(
            profile,
            "add-bm-page-333333333333333",
            ProvisioningStep.BUSINESS,
            {"business_id": "2487306152656679"},
        )

        rows = await self.store.confirmed_businesses_for_profile(profile)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["business_id"], "2487306152656679")
        self.assertEqual(rows[0]["source"], "python_worker_confirmed_entity")

    async def test_profile_keeps_all_confirmed_businesses_even_without_rk(self) -> None:
        profile = "7"
        await self.store.complete(
            "item-bm-a",
            profile,
            "add-bm-page-111111111111111",
            ProvisioningStep.BUSINESS,
            {
                "phase": "DONE",
                "business_id": "61594753560938",
                "business_name": "BM A",
                "primary_page_id": "111111111111111",
            },
        )
        await self.store.complete(
            "item-bm-b",
            profile,
            "add-bm-page-222222222222222",
            ProvisioningStep.BUSINESS,
            {
                "phase": "DONE",
                "business_id": "2487306152656679",
                "business_name": "BM B",
                "primary_page_id": "222222222222222",
            },
        )

        rows = await self.store.confirmed_businesses_for_profile(profile)

        self.assertEqual(
            {row["business_id"] for row in rows},
            {"61594753560938", "2487306152656679"},
        )

    async def test_confirmed_fan_page_survives_without_graph_inventory(self) -> None:
        profile = "6"
        await self.store.complete(
            "item-fp",
            profile,
            "add-fp-test",
            ProvisioningStep.FAN_PAGES,
            {
                "phase": "DONE",
                "requested_count": 1,
                "created_count": 1,
                "pages": [
                    {
                        "id": "123456789012345",
                        "name": "ReMask Page",
                        "category": "Digital creator",
                        "reused": False,
                    }
                ],
                "page_ids": ["123456789012345"],
                "target_names": ["ReMask Page"],
                "category": "Digital creator",
            },
        )

        pages = await self.store.latest_profile_fan_pages(profile)

        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["id"], "123456789012345")
        self.assertEqual(pages[0]["source"], "python_worker_confirmed")


if __name__ == "__main__":
    unittest.main()
