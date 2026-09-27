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

        self.assertEqual(
            {
                (row["business_id"], row["ad_account_id"])
                for row in rows
            },
            {
                ("1578458920690597", "2490929708095829"),
                ("1619103589770310", "29459808963612032"),
            },
        )
        self.assertEqual(len(rows), 2)

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
