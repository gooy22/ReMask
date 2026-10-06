import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.provisioning.advertising_page import AdvertisingPageStore
from app.provisioning.models import ProvisioningStep
from app.provisioning.prepare import PrepareService
from app.provisioning.state import ProvisioningStateStore


class PreparePlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / "prepare.sqlite"))
        await self.state.init()
        self.profile_id = "7"
        self.business_id = "1760742031708754"
        self.context = SimpleNamespace(
            profile_id=self.profile_id,
            cookies={"c_user": "61594993341059"},
            pages=[],
        )
        self.session = SimpleNamespace(context=self.context)
        self.page_store = AdvertisingPageStore.for_context(
            self.state,
            self.context,
            self.profile_id,
        )
        await self.page_store.patch(
            page_id="1289628847574478",
            name="PrgssTeam",
        )

    async def _confirmed_business(self):
        await self.state.complete(
            "business-history",
            self.profile_id,
            "legacy-business",
            ProvisioningStep.BUSINESS,
            {
                "business_id": self.business_id,
                "business_name": "Existing BM",
                "phase": "PAGE_CONFIRMED",
            },
        )

    async def _confirmed_rk(self, item, scope, account):
        await self.state.complete(
            item,
            self.profile_id,
            scope,
            ProvisioningStep.AD_ACCOUNT,
            {
                "business_id": self.business_id,
                "ad_account_id": account,
                "account_name": f"RK {account[-2:]}",
            },
        )

    async def _confirmed_access(self, item, account):
        await self.state.complete(
            item,
            self.profile_id,
            f"access:{account}",
            ProvisioningStep.PAGE_ACCESS,
            {
                "page_id": "1289628847574478",
                "business_id": self.business_id,
                "ad_account_id": account,
                "page_shared_to_business": True,
                "operator_ads_access_assigned": True,
            },
        )

    async def _confirmed_funding(self, item, account):
        await self.state.complete(
            item,
            self.profile_id,
            f"funding:{account}",
            ProvisioningStep.FUNDING,
            {
                "business_id": self.business_id,
                "ad_account_id": account,
                "funding_source_id": f"fund-{account}",
                "funding_verified": True,
            },
        )

    async def test_multi_rk_inventory_preserves_two_accounts_under_one_business(self):
        await self._confirmed_business()
        await self._confirmed_rk("rk-one", "rk:1", "111111111111111")
        await self._confirmed_rk("rk-two", "rk:2", "222222222222222")

        rows = await self.state.confirmed_ad_accounts_for_profile(
            self.profile_id,
            self.business_id,
        )

        self.assertEqual(
            {row["ad_account_id"] for row in rows},
            {"111111111111111", "222222222222222"},
        )

    async def test_prepare_is_noop_when_desired_state_is_already_confirmed(self):
        await self._confirmed_business()
        accounts = ["111111111111111", "222222222222222"]
        for index, account in enumerate(accounts, 1):
            await self._confirmed_rk(f"rk-{index}", f"rk:{index}", account)
            await self._confirmed_access(f"access-{index}", account)
            await self._confirmed_funding(f"funding-{index}", account)

        provisioning = SimpleNamespace(run=AsyncMock())
        service = PrepareService(self.state, provisioning)

        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload={
                "desired": {
                    "ad_accounts": 2,
                    "payment": True,
                    "page_access": True,
                },
                "parameters": {
                    "AD_ACCOUNT": {
                        "currency": "USD",
                        "timezone_id": 1,
                    }
                },
            },
            task_idempotency_key="prepare-stable",
        )

        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "READY_TO_LAUNCH")
        self.assertTrue(result["ready_to_launch"])
        self.assertEqual(len(result["actual"]["ad_accounts"]), 2)

    async def test_prepare_creates_only_missing_second_rk(self):
        await self._confirmed_business()
        await self._confirmed_rk("rk-one", "rk:1", "111111111111111")
        await self._confirmed_access("access-one", "111111111111111")

        async def run_side_effect(**kwargs):
            self.assertEqual(kwargs["payload"]["steps"], ["AD_ACCOUNT"])
            params = kwargs["payload"]["parameters"]["AD_ACCOUNT"]
            self.assertEqual(params["business_id"], self.business_id)
            self.assertTrue(kwargs["item_id"].endswith(":prepare:rk:2"))
            account = "222222222222222"
            await self.state.complete(
                kwargs["item_id"],
                self.profile_id,
                kwargs["payload"]["scope_key"],
                ProvisioningStep.AD_ACCOUNT,
                {
                    "business_id": self.business_id,
                    "ad_account_id": account,
                    "account_name": params["name"],
                },
            )
            await self.state.complete(
                kwargs["item_id"],
                self.profile_id,
                kwargs["payload"]["scope_key"],
                ProvisioningStep.PAGE_ACCESS,
                {
                    "page_id": "1289628847574478",
                    "business_id": self.business_id,
                    "ad_account_id": account,
                    "page_shared_to_business": True,
                    "operator_ads_access_assigned": True,
                },
            )
            return {
                "state": {
                    "business_id": self.business_id,
                    "ad_account_id": account,
                }
            }

        provisioning = SimpleNamespace(run=AsyncMock(side_effect=run_side_effect))
        service = PrepareService(self.state, provisioning)

        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload={
                "desired": {
                    "ad_accounts": 2,
                    "payment": False,
                    "page_access": True,
                },
                "parameters": {
                    "AD_ACCOUNT": {
                        "currency": "USD",
                        "timezone_id": 1,
                    }
                },
            },
            task_idempotency_key="prepare-stable",
        )

        self.assertEqual(provisioning.run.await_count, 1)
        self.assertEqual(result["status"], "READY_TO_LAUNCH")
        self.assertEqual(
            {row["ad_account_id"] for row in result["actual"]["ad_accounts"]},
            {"111111111111111", "222222222222222"},
        )

    async def test_last_confirmed_workspace_inventory_suppresses_duplicate_create(self):
        self.context.businesses = [
            {
                "business_id": self.business_id,
                "name": "Live BM",
                "source": "workspace_last_confirmed_live",
            }
        ]
        self.context.ad_accounts = [
            {
                "business_id": self.business_id,
                "ad_account_id": "111111111111111",
                "name": "Live RK 1",
                "source": "workspace_last_confirmed_live",
            },
            {
                "business_id": self.business_id,
                "ad_account_id": "222222222222222",
                "name": "Live RK 2",
                "source": "workspace_last_confirmed_live",
            },
        ]
        self.context.inventory_updated_at = 123456789

        provisioning = SimpleNamespace(run=AsyncMock())
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload={
                "desired": {
                    "ad_accounts": 2,
                    "payment": False,
                    "page_access": False,
                },
                "parameters": {
                    "AD_ACCOUNT": {
                        "currency": "USD",
                        "timezone_id": 1,
                    }
                },
            },
        )

        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "READY_TO_LAUNCH")
        self.assertEqual(result["actual"]["business_id"], self.business_id)
        self.assertEqual(
            {row["ad_account_id"] for row in result["actual"]["ad_accounts"]},
            {"111111111111111", "222222222222222"},
        )

    async def test_payment_is_a_readiness_gate_not_a_duplicate_create_trigger(self):
        await self._confirmed_business()
        accounts = ["111111111111111", "222222222222222"]
        for index, account in enumerate(accounts, 1):
            await self._confirmed_rk(f"rk-{index}", f"rk:{index}", account)
            await self._confirmed_access(f"access-{index}", account)

        provisioning = SimpleNamespace(run=AsyncMock())
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload={
                "desired": {
                    "ad_accounts": 2,
                    "payment": True,
                    "page_access": True,
                },
                "parameters": {
                    "AD_ACCOUNT": {
                        "currency": "USD",
                        "timezone_id": 1,
                    }
                },
            },
        )

        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "PAYMENT_REQUIRED")
        self.assertEqual(result["reason"], "PAYMENT_UNCONFIRMED")
        self.assertFalse(result["ready_to_launch"])


if __name__ == "__main__":
    unittest.main()
