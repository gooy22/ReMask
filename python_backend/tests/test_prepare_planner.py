import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.provisioning.advertising_page import AdvertisingPageStore
from app.provisioning.models import ProvisioningStep
from app.provisioning.models import ProvisioningError
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
        self.business_id_2 = "2478360152656679"
        self.context = SimpleNamespace(
            profile_id=self.profile_id,
            cookies={"c_user": "61594993341059"},
            pages=[],
            businesses=[],
            ad_accounts=[],
            inventory_updated_at=0,
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

    async def _confirmed_business(self, business_id=None, item=None):
        business_id = business_id or self.business_id
        await self.state.complete(
            item or f"business-{business_id}",
            self.profile_id,
            f"business:{business_id}",
            ProvisioningStep.BUSINESS,
            {
                "business_id": business_id,
                "business_name": f"BM {business_id[-4:]}",
                "phase": "PAGE_CONFIRMED",
            },
        )

    async def _confirmed_rk(
        self,
        account,
        business_id=None,
        item=None,
        scope=None,
        name=None,
    ):
        business_id = business_id or self.business_id
        await self.state.complete(
            item or f"rk-{account}",
            self.profile_id,
            scope or f"rk:{business_id}:{account}",
            ProvisioningStep.AD_ACCOUNT,
            {
                "business_id": business_id,
                "ad_account_id": account,
                "account_name": name or f"RK {account[-2:]}",
            },
        )

    async def _confirmed_access(self, account, business_id=None, item=None):
        business_id = business_id or self.business_id
        await self.state.complete(
            item or f"access-{account}",
            self.profile_id,
            f"access:{business_id}:{account}",
            ProvisioningStep.PAGE_ACCESS,
            {
                "page_id": "1289628847574478",
                "business_id": business_id,
                "ad_account_id": account,
                "page_shared_to_business": True,
                "operator_ads_access_assigned": True,
                "page_owned_by_business": True,
                "operator_full_control_verified": True,
                "rk_operator_full_control_verified": True,
            },
        )

    async def _confirmed_funding(self, account, business_id=None, item=None):
        business_id = business_id or self.business_id
        await self.state.complete(
            item or f"funding-{account}",
            self.profile_id,
            f"funding:{business_id}:{account}",
            ProvisioningStep.FUNDING,
            {
                "business_id": business_id,
                "ad_account_id": account,
                "funding_source_id": f"fund-{account}",
                "funding_verified": True,
            },
        )

    @staticmethod
    def _payload(*, bundles=2, payment=False, page_access=False):
        return {
            "desired": {
                "ad_accounts": bundles,
                "payment": payment,
                "page_access": page_access,
            },
            "parameters": {
                "AD_ACCOUNT": {
                    "currency": "USD",
                    "timezone_id": 1,
                }
            },
        }

    def test_prepare_default_topology_is_one_business_one_rk(self):
        desired = PrepareService._desired({})
        self.assertEqual(desired.ad_accounts, 1)

    async def test_legacy_multi_rk_inventory_is_preserved_but_not_counted_as_two_bundles(self):
        await self._confirmed_business()
        await self._confirmed_rk("111111111111111")
        await self._confirmed_rk("222222222222222")

        rows = await self.state.confirmed_ad_accounts_for_profile(
            self.profile_id,
            self.business_id,
        )
        self.assertEqual(
            {row["ad_account_id"] for row in rows},
            {"111111111111111", "222222222222222"},
        )

        calls = []

        async def run_side_effect(**kwargs):
            calls.append(kwargs)
            steps = kwargs["payload"]["steps"]
            if steps == ["BUSINESS"]:
                await self._confirmed_business(
                    self.business_id_2,
                    item=kwargs["item_id"],
                )
                return {"state": {"business_id": self.business_id_2}}
            if steps == ["AD_ACCOUNT"]:
                params = kwargs["payload"]["parameters"]["AD_ACCOUNT"]
                self.assertEqual(params["business_id"], self.business_id_2)
                self.assertNotIn("allow_multiple_in_business", params)
                account = "333333333333333"
                await self._confirmed_rk(
                    account,
                    self.business_id_2,
                    item=kwargs["item_id"],
                    scope=kwargs["payload"]["scope_key"],
                    name=params["name"],
                )
                return {
                    "state": {
                        "business_id": self.business_id_2,
                        "ad_account_id": account,
                    }
                }
            raise AssertionError(steps)

        provisioning = SimpleNamespace(run=AsyncMock(side_effect=run_side_effect))
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload=self._payload(bundles=2),
            task_idempotency_key="prepare-stable",
        )

        self.assertEqual(provisioning.run.await_count, 2)
        self.assertEqual(result["desired"]["topology"], "ONE_RK_PER_BUSINESS")
        self.assertEqual(
            {row["business_id"] for row in result["actual"]["bundles"]},
            {self.business_id, self.business_id_2},
        )
        self.assertEqual(len(result["actual"]["extra_ad_accounts"]), 1)
        self.assertEqual(
            result["actual"]["extra_ad_accounts"][0]["ad_account_id"],
            "111111111111111"
            if result["actual"]["bundles"][0]["ad_account_id"] == "222222222222222"
            else "222222222222222",
        )

    async def test_prepare_is_noop_when_two_distinct_bm_rk_bundles_are_confirmed(self):
        pairs = [
            (self.business_id, "111111111111111"),
            (self.business_id_2, "222222222222222"),
        ]
        for business_id, account in pairs:
            await self._confirmed_business(business_id)
            await self._confirmed_rk(account, business_id)
            await self._confirmed_access(account, business_id)
            await self._confirmed_funding(account, business_id)

        provisioning = SimpleNamespace(run=AsyncMock())
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload=self._payload(
                bundles=2,
                payment=True,
                page_access=True,
            ),
        )

        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "READY_TO_LAUNCH")
        self.assertEqual(len(result["actual"]["bundles"]), 2)
        self.assertEqual(
            len({row["business_id"] for row in result["actual"]["bundles"]}),
            2,
        )

    async def test_prepare_creates_new_business_before_second_rk(self):
        first_account = "111111111111111"
        second_account = "222222222222222"
        await self._confirmed_business(self.business_id)
        await self._confirmed_rk(first_account, self.business_id)

        sequence = []

        async def run_side_effect(**kwargs):
            steps = kwargs["payload"]["steps"]
            sequence.append(steps[0])
            if steps == ["BUSINESS"]:
                self.assertTrue(
                    kwargs["item_id"].endswith(":prepare:bundle:2:business")
                )
                await self._confirmed_business(
                    self.business_id_2,
                    item=kwargs["item_id"],
                )
                return {"state": {"business_id": self.business_id_2}}
            if steps == ["AD_ACCOUNT"]:
                params = kwargs["payload"]["parameters"]["AD_ACCOUNT"]
                self.assertEqual(params["business_id"], self.business_id_2)
                self.assertNotIn("allow_multiple_in_business", params)
                self.assertTrue(
                    kwargs["item_id"].endswith(":prepare:bundle:2:rk")
                )
                await self._confirmed_rk(
                    second_account,
                    self.business_id_2,
                    item=kwargs["item_id"],
                    scope=kwargs["payload"]["scope_key"],
                    name=params["name"],
                )
                return {
                    "state": {
                        "business_id": self.business_id_2,
                        "ad_account_id": second_account,
                    }
                }
            raise AssertionError(steps)

        provisioning = SimpleNamespace(run=AsyncMock(side_effect=run_side_effect))
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload=self._payload(bundles=2),
        )

        self.assertEqual(sequence, ["BUSINESS", "AD_ACCOUNT"])
        self.assertEqual(result["status"], "PREPARED")
        self.assertFalse(result["ready_to_launch"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["page_access_confirmed"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["payment_confirmed"])
        self.assertEqual(
            {row["business_id"] for row in result["actual"]["bundles"]},
            {self.business_id, self.business_id_2},
        )

    async def test_prepare_uses_existing_empty_business_for_missing_bundle(self):
        await self._confirmed_business(self.business_id)
        await self._confirmed_rk("111111111111111", self.business_id)
        await self._confirmed_business(self.business_id_2)

        async def run_side_effect(**kwargs):
            self.assertEqual(kwargs["payload"]["steps"], ["AD_ACCOUNT"])
            params = kwargs["payload"]["parameters"]["AD_ACCOUNT"]
            self.assertEqual(params["business_id"], self.business_id_2)
            self.assertNotIn("allow_multiple_in_business", params)
            account = "222222222222222"
            await self._confirmed_rk(
                account,
                self.business_id_2,
                item=kwargs["item_id"],
                scope=kwargs["payload"]["scope_key"],
                name=params["name"],
            )
            return {
                "state": {
                    "business_id": self.business_id_2,
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
            payload=self._payload(bundles=2),
        )

        self.assertEqual(provisioning.run.await_count, 1)
        self.assertEqual(result["status"], "PREPARED")
        self.assertFalse(result["ready_to_launch"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["page_access_confirmed"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["payment_confirmed"])
        self.assertEqual(
            len({row["business_id"] for row in result["actual"]["bundles"]}),
            2,
        )

    async def test_prepare_repairs_page_access_per_exact_bm_rk_pair(self):
        account = "111111111111111"
        await self._confirmed_business(self.business_id)
        await self._confirmed_rk(account, self.business_id)

        async def run_side_effect(**kwargs):
            self.assertEqual(kwargs["payload"]["steps"], ["PAGE_ACCESS"])
            params = kwargs["payload"]["parameters"]["PAGE_ACCESS"]
            self.assertEqual(params["business_id"], self.business_id)
            self.assertEqual(params["ad_account_id"], account)
            await self._confirmed_access(
                account,
                self.business_id,
                item=kwargs["item_id"],
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
            payload=self._payload(
                bundles=1,
                payment=False,
                page_access=True,
            ),
        )

        self.assertEqual(provisioning.run.await_count, 1)
        self.assertEqual(result["status"], "PREPARED")
        self.assertFalse(result["ready_to_launch"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["payment_confirmed"])
        self.assertTrue(result["actual"]["ad_accounts"][0]["page_access_confirmed"])

    async def test_existing_bundle_is_repaired_before_missing_bundle_create(self):
        account = "111111111111111"
        await self._confirmed_business()
        await self._confirmed_rk(account)
        sequence = []

        async def execute(**kwargs):
            action = kwargs["payload"]["steps"][0]
            sequence.append(action)
            if action == "PAGE_ACCESS":
                params = kwargs["payload"]["parameters"][action]
                self.assertEqual((params["business_id"], params["ad_account_id"]),
                    (self.business_id, account))
                await self._confirmed_access(account, item=kwargs["item_id"])
                return {"state": {}}
            self.assertEqual(action, "BUSINESS")
            raise ProvisioningError("PRIVATE_BM_INVENTORY_INCONCLUSIVE", "No CREATE was sent", retryable=True)

        provisioning = SimpleNamespace(run=AsyncMock(side_effect=execute))
        service = PrepareService(self.state, provisioning)
        for item in ("attempt-1", "attempt-2"):
            with self.assertRaises(ProvisioningError):
                await service.run(item_id=item, profile_id=self.profile_id,
                    context=self.context, session=self.session,
                    payload=self._payload(bundles=2, page_access=True))
        self.assertEqual(sequence, ["PAGE_ACCESS", "BUSINESS", "BUSINESS"])
        self.assertTrue(await self.state.page_access_confirmed(
            self.profile_id, self.business_id, account, full_control=True))
        self.assertEqual(len(await self.state.confirmed_ad_accounts_for_profile(self.profile_id)), 1)

    async def test_two_existing_incomplete_bundles_are_repaired_without_business_create(self):
        await self._confirmed_business()
        await self._confirmed_business(self.business_id_2)
        sequence = []

        async def execute(**kwargs):
            action = kwargs["payload"]["steps"][0]
            params = kwargs["payload"]["parameters"][action]
            bm = params["business_id"]
            sequence.append((action, bm))
            if action == "AD_ACCOUNT":
                account = "111111111111111" if bm == self.business_id else "222222222222222"
                await self._confirmed_rk(account, bm, item=kwargs["item_id"])
                return {"state": {"business_id": bm, "ad_account_id": account}}
            self.assertEqual(action, "PAGE_ACCESS")
            await self._confirmed_access(params["ad_account_id"], bm, item=kwargs["item_id"])
            return {"state": {}}

        service = PrepareService(self.state, SimpleNamespace(run=AsyncMock(side_effect=execute)))
        result = await service.run(item_id="repair-two", profile_id=self.profile_id,
            context=self.context, session=self.session,
            payload=self._payload(bundles=2, page_access=True))
        self.assertEqual(len(result["actual"]["bundles"]), 2)
        self.assertEqual([a for a, _ in sequence], ["AD_ACCOUNT", "PAGE_ACCESS", "AD_ACCOUNT", "PAGE_ACCESS"])
        self.assertEqual(sequence[0][1], sequence[1][1])
        self.assertEqual(sequence[2][1], sequence[3][1])
        self.assertNotEqual(sequence[0][1], sequence[2][1])

    async def test_missing_rights_do_not_trigger_replacement_business_create(self):
        await self._confirmed_business()
        await self._confirmed_rk("111111111111111")
        async def execute(**kwargs):
            self.assertEqual(kwargs["payload"]["steps"], ["PAGE_ACCESS"])
            raise ProvisioningError("PRIVATE_PERMISSION_UNVERIFIED", "rights incomplete", retryable=True)
        provisioning = SimpleNamespace(run=AsyncMock(side_effect=execute))
        service = PrepareService(self.state, provisioning)
        with self.assertRaises(ProvisioningError):
            await service.run(item_id="rights-incomplete", profile_id=self.profile_id,
                context=self.context, session=self.session,
                payload=self._payload(bundles=2, page_access=True))
        self.assertEqual(provisioning.run.await_count, 1)

    async def test_workspace_inventory_with_two_bm_rk_pairs_suppresses_duplicate_create(self):
        self.context.businesses = [
            {
                "business_id": self.business_id,
                "name": "Live BM 1",
                "source": "workspace_last_confirmed_live",
            },
            {
                "business_id": self.business_id_2,
                "name": "Live BM 2",
                "source": "workspace_last_confirmed_live",
            },
        ]
        self.context.ad_accounts = [
            {
                "business_id": self.business_id,
                "ad_account_id": "111111111111111",
                "name": "Live RK 1",
                "source": "workspace_last_confirmed_live",
            },
            {
                "business_id": self.business_id_2,
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
            payload=self._payload(bundles=2),
        )

        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "PREPARED")
        self.assertFalse(result["ready_to_launch"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["page_access_confirmed"])
        self.assertFalse(result["actual"]["ad_accounts"][0]["payment_confirmed"])
        self.assertEqual(
            result["actual"]["business_ids"],
            [row["business_id"] for row in result["actual"]["bundles"]],
        )
        self.assertEqual(len(set(result["actual"]["business_ids"])), 2)

    async def test_payment_readiness_is_evaluated_per_bundle(self):
        pairs = [
            (self.business_id, "111111111111111"),
            (self.business_id_2, "222222222222222"),
        ]
        for business_id, account in pairs:
            await self._confirmed_business(business_id)
            await self._confirmed_rk(account, business_id)
            await self._confirmed_access(account, business_id)

        provisioning = SimpleNamespace(run=AsyncMock())
        service = PrepareService(self.state, provisioning)
        result = await service.run(
            item_id="prepare-item",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload=self._payload(
                bundles=2,
                payment=True,
                page_access=True,
            ),
        )
        provisioning.run.assert_not_awaited()
        self.assertEqual(result["status"], "PAYMENT_REQUIRED")

        for business_id, account in pairs:
            await self.state.set_payment_link_state(
                self.profile_id,
                account,
                True,
                source=f"test:{business_id}",
            )

        ready = await service.run(
            item_id="prepare-item-ready",
            profile_id=self.profile_id,
            context=self.context,
            session=self.session,
            payload=self._payload(
                bundles=2,
                payment=True,
                page_access=True,
            ),
        )
        self.assertEqual(ready["status"], "READY_TO_LAUNCH")
        self.assertTrue(ready["ready_to_launch"])


if __name__ == "__main__":
    unittest.main()
