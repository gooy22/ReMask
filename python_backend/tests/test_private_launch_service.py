import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fb_worker import RemoteRequestError

from app.private_launch import (
    PrivateLaunchContractStore,
    PrivateLaunchService,
)
from app.provisioning.models import ProvisioningError
from app.provisioning.state import ProvisioningStateStore


def contracts():
    return {
        "CAMPAIGN": {
            "doc_id": "111111",
            "friendly_name": "CampaignCreateMutation",
            "variables": {
                "input": {
                    "account_id": "{{ad_account_id}}",
                    "name": "{{payload.campaign.name}}",
                }
            },
            "result_id_paths": ["data.create.id"],
        },
        "ADSET": {
            "doc_id": "222222",
            "friendly_name": "AdSetCreateMutation",
            "variables": {
                "input": {
                    "campaign_id": "{{campaign_id}}",
                    "name": "{{payload.adset.name}}",
                }
            },
            "result_id_paths": ["data.create.id"],
        },
        "CREATIVE": {
            "doc_id": "333333",
            "friendly_name": "CreativeCreateMutation",
            "variables": {
                "input": {
                    "account_id": "{{ad_account_id}}",
                    "page_id": "{{page_id}}",
                    "name": "{{payload.creative.name}}",
                }
            },
            "result_id_paths": ["data.create.id"],
        },
        "AD": {
            "doc_id": "444444",
            "friendly_name": "AdCreateMutation",
            "variables": {
                "input": {
                    "adset_id": "{{adset_id}}",
                    "creative_id": "{{creative_id}}",
                    "name": "{{payload.ad.name}}",
                }
            },
            "result_id_paths": ["data.create.id"],
        },
    }


class PrivateLaunchContractRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "contracts.json"

    def test_registry_persists_valid_contract_and_reloads_it(self):
        store = PrivateLaunchContractStore("{}", path=self.path)
        contract = store.register(
            __import__("app.private_launch", fromlist=["PrivateLaunchStep"]).PrivateLaunchStep.CAMPAIGN,
            contracts()["CAMPAIGN"],
        )
        self.assertEqual(contract.friendly_name, "CampaignCreateMutation")
        self.assertTrue(self.path.is_file())

        reloaded = PrivateLaunchContractStore(path=self.path)
        status = reloaded.status()
        self.assertTrue(status["CAMPAIGN"]["configured"])
        self.assertFalse(status["ADSET"]["configured"])

    def test_registry_rejects_missing_target_placeholder_and_auth_material(self):
        step = __import__("app.private_launch", fromlist=["PrivateLaunchStep"]).PrivateLaunchStep.CAMPAIGN
        store = PrivateLaunchContractStore("{}", path=self.path)

        missing = {**contracts()["CAMPAIGN"], "variables": {"input": {"name": "fixture"}}}
        with self.assertRaises(ProvisioningError) as caught:
            store.register(step, missing)
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR")
        self.assertFalse(self.path.exists())

        auth = {
            **contracts()["CAMPAIGN"],
            "variables": {
                "input": {
                    "account_id": "{{ad_account_id}}",
                    "fb_dtsg": "must-never-persist",
                }
            },
        }
        with self.assertRaises(ProvisioningError) as caught:
            store.register(step, auth)
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR")
        self.assertFalse(self.path.exists())

    def test_registry_rejects_auth_fields_in_request_envelope(self):
        step = __import__("app.private_launch", fromlist=["PrivateLaunchStep"]).PrivateLaunchStep.CAMPAIGN
        store = PrivateLaunchContractStore("{}", path=self.path)
        row = {
            **contracts()["CAMPAIGN"],
            "request_envelope": {"__req": "1", "fb_dtsg": "secret"},
        }
        with self.assertRaises(ProvisioningError):
            store.register(step, row)
        self.assertFalse(self.path.exists())


class PrivateLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = ProvisioningStateStore(str(Path(self.tmp.name) / "jobs.sqlite"))
        await self.state.init()
        self.service = PrivateLaunchService(
            self.state,
            contracts=PrivateLaunchContractStore(__import__("json").dumps(contracts())),
        )
        self.context = SimpleNamespace(profile_id="7")
        self.web = SimpleNamespace(
            bootstrap=AsyncMock(return_value=SimpleNamespace(actor_id="61594993341059")),
            graphql=AsyncMock(),
        )
        self.session = SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.payload = {
            "business_id": "1760742031708754",
            "ad_account_id": "1569487661117197",
            "page_id": "1289628847574478",
            "launch_key": "launch-fixture",
            "launch": {
                "campaign": {"name": "Campaign fixture"},
                "adset": {"name": "AdSet fixture"},
                "creative": {"name": "Creative fixture"},
                "ad": {"name": "Ad fixture"},
            },
        }
        self.preflight = {
            "profile_id": "7",
            "business_id": self.payload["business_id"],
            "ad_account_id": self.payload["ad_account_id"],
            "page_id": self.payload["page_id"],
            "account_status": 1,
            "page_access_checked_live": True,
            "payment_checked_live": True,
        }

    async def test_official_graph_endpoint_is_rejected(self):
        cfg = contracts()
        cfg["CAMPAIGN"]["endpoint_url"] = "https://graph.facebook.com/v26.0/"
        store = PrivateLaunchContractStore(__import__("json").dumps(cfg))
        with self.assertRaises(ProvisioningError) as caught:
            store.get(__import__("app.private_launch", fromlist=["PrivateLaunchStep"]).PrivateLaunchStep.CAMPAIGN)
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CONTRACT_CONFIG_ERROR")

    async def test_success_persists_each_entity_and_rerun_submits_nothing(self):
        self.web.graphql.side_effect = [
            {"data": {"create": {"id": "500000000000001"}}},
            {"data": {"create": {"id": "500000000000002"}}},
            {"data": {"create": {"id": "500000000000003"}}},
            {"data": {"create": {"id": "500000000000004"}}},
        ]
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            result = await self.service.run(
                item_id="item",
                profile_id="7",
                context=self.context,
                session=self.session,
                payload=self.payload,
                task_idempotency_key="launch-fixture",
            )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(
            result["entities"],
            {
                "campaign_id": "500000000000001",
                "adset_id": "500000000000002",
                "creative_id": "500000000000003",
                "ad_id": "500000000000004",
            },
        )
        self.assertEqual(self.web.graphql.await_count, 4)
        campaign_vars = self.web.graphql.await_args_list[0].args[1]
        adset_vars = self.web.graphql.await_args_list[1].args[1]
        ad_vars = self.web.graphql.await_args_list[3].args[1]
        self.assertEqual(campaign_vars["input"]["account_id"], self.payload["ad_account_id"])
        self.assertEqual(adset_vars["input"]["campaign_id"], "500000000000001")
        self.assertEqual(ad_vars["input"]["adset_id"], "500000000000002")
        self.assertEqual(ad_vars["input"]["creative_id"], "500000000000003")

        self.web.graphql.reset_mock()
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            rerun = await self.service.run(
                item_id="item-2",
                profile_id="7",
                context=self.context,
                session=self.session,
                payload=self.payload,
                task_idempotency_key="launch-fixture",
            )
        self.web.graphql.assert_not_awaited()
        self.assertTrue(all(step["skipped"] for step in rerun["steps"]))

    async def test_per_rk_override_is_merged_before_contract_render(self):
        payload = {
            **self.payload,
            "launch_key": "launch-override",
            "launch": {
                "base": {
                    "campaign": {"name": "Base campaign"},
                    "adset": {"name": "Base adset", "targeting": {"age_min": 18, "age_max": 55}},
                    "creative": {"name": "Base creative", "message": "base"},
                    "ad": {"name": "Base ad"},
                },
                "override": {
                    "adset": {"targeting": {"age_max": 35}},
                    "creative": {"message": "RK override"},
                },
            },
        }
        self.web.graphql.side_effect = [
            {"data": {"create": {"id": "510000000000001"}}},
            {"data": {"create": {"id": "510000000000002"}}},
            {"data": {"create": {"id": "510000000000003"}}},
            {"data": {"create": {"id": "510000000000004"}}},
        ]
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            result = await self.service.run(
                item_id="item-override",
                profile_id="7",
                context=self.context,
                session=self.session,
                payload=payload,
            )
        self.assertEqual(result["status"], "SUCCESS")
        # Contract placeholders see one effective per-RK payload; nested base
        # values survive while the selected RK override replaces only its leaf.
        effective = __import__("app.private_launch", fromlist=["_effective_launch_payload"])._effective_launch_payload(payload["launch"])
        self.assertEqual(effective["adset"]["targeting"], {"age_min": 18, "age_max": 35})
        self.assertEqual(effective["creative"]["message"], "RK override")
        self.assertEqual(effective["creative"]["name"], "Base creative")

    async def test_crash_after_submit_checkpoint_never_replays_mutation(self):
        self.web.graphql.side_effect = RuntimeError("process-crash-fixture")
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(RuntimeError):
                await self.service.run(
                    item_id="item-crash",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={**self.payload, "launch_key": "crash-launch"},
                )
        step = await self.service.state.step(
            "crash-launch",
            __import__("app.private_launch", fromlist=["PrivateLaunchStep"]).PrivateLaunchStep.CAMPAIGN,
        )
        self.assertEqual(step["status"], "SUBMITTING")
        self.assertIsNone(step["submitted"])

        self.web.graphql.reset_mock()
        self.web.graphql.return_value = {"data": {"create": {"id": "599999999999999"}}}
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await self.service.run(
                    item_id="item-crash-retry",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={**self.payload, "launch_key": "crash-launch"},
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_RECONCILE_REQUIRED")
        self.web.graphql.assert_not_awaited()

    async def test_unknown_submitted_result_blocks_automatic_resubmit(self):
        self.web.graphql.return_value = {"data": {"create": {}}}
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await self.service.run(
                    item_id="item",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload=self.payload,
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CAMPAIGN_RESULT_UNKNOWN")
        self.assertEqual(self.web.graphql.await_count, 1)

        self.web.graphql.reset_mock()
        self.web.graphql.return_value = {"data": {"create": {"id": "599999999999999"}}}
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await self.service.run(
                    item_id="item-retry",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload=self.payload,
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_RECONCILE_REQUIRED")
        self.web.graphql.assert_not_awaited()

    async def test_review_cache_reuses_exact_recent_live_proof(self):
        self.context.inventory_updated_at = 123456789
        probe = AsyncMock(return_value=self.preflight)
        with patch.object(self.service, "_live_preflight", probe):
            first = await self.service.review(
                profile_id="7",
                context=self.context,
                payload=self.payload,
            )
            second = await self.service.review(
                profile_id="7",
                context=self.context,
                payload=self.payload,
            )
        self.assertTrue(first["ready"])
        self.assertTrue(second["ready"])
        self.assertIs(second["preflight"]["review_cache_reused"], True)
        self.assertEqual(probe.await_count, 1)

    async def test_explicit_meta_rejection_is_durable_and_never_replayed(self):
        self.web.graphql.side_effect = RemoteRequestError(
            "rejected",
            http_status=400,
        )
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await self.service.run(
                    item_id="item-blocked",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={**self.payload, "launch_key": "blocked-launch"},
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CAMPAIGN_REJECTED")
        self.assertEqual(self.web.graphql.await_count, 1)

        self.web.graphql.reset_mock()
        self.web.graphql.return_value = {"data": {"create": {"id": "500000000009999"}}}
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await self.service.run(
                    item_id="item-blocked-retry",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={**self.payload, "launch_key": "blocked-launch"},
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_BLOCKED_REPLAY")
        self.web.graphql.assert_not_awaited()

    async def test_missing_contract_stops_before_private_submit(self):
        only_campaign = {"CAMPAIGN": contracts()["CAMPAIGN"]}
        service = PrivateLaunchService(
            self.state,
            contracts=PrivateLaunchContractStore(__import__("json").dumps(only_campaign)),
        )
        self.web.graphql.side_effect = [
            {"data": {"create": {"id": "500000000000001"}}},
        ]
        with patch.object(service, "_live_preflight", AsyncMock(return_value=self.preflight)):
            with self.assertRaises(ProvisioningError) as caught:
                await service.run(
                    item_id="item",
                    profile_id="7",
                    context=self.context,
                    session=self.session,
                    payload={**self.payload, "launch_key": "missing-contract"},
                )
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CONTRACT_REQUIRED")
        self.web.graphql.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
