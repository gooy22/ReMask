import asyncio
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore, PrivateLaunchStep, _resumable_read
from app.action_result import ActionResult
from app.private_launch_verify import proof, validate_contract
from app.provisioning.models import ProvisioningError
from app.provisioning.state import ProvisioningStateStore
from tests.test_private_launch_service import contracts

ACCOUNT, BUSINESS, PAGE = "123456789", "987654321", "111222333"
IDS = [str(500000000000001 + index) for index in range(4)]


def responses():
    result = []
    for index, step in enumerate(("CAMPAIGN", "ADSET", "CREATIVE", "AD")):
        node = {"id": IDS[index], "account_id": ACCOUNT, "status": "PAUSED"}
        if step == "ADSET":
            node["campaign_id"] = IDS[0]
        if step == "CREATIVE":
            node["page_id"] = PAGE
        if step == "AD":
            node.update(campaign_id=IDS[0], adset_id=IDS[1], creative_id=IDS[2])
        result.extend([{"data": {"create": {"id": IDS[index]}}}, {"data": {"node": node}}])
    return result


class IndependentLaunchReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.docids = patch("app.facebook_docids.STORE_PATH", Path(self.tmp.name)/"docids.json")
        self.docids.start(); self.addCleanup(self.docids.stop)
        self.service = PrivateLaunchService(ProvisioningStateStore(str(Path(self.tmp.name)/"state.sqlite")),
            contracts=PrivateLaunchContractStore(json.dumps(contracts())))
        self.web = SimpleNamespace(bootstrap=AsyncMock(), graphql=AsyncMock(side_effect=responses()))
        self.session = SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.context = SimpleNamespace(profile_id="fixture")
        self.payload = {"business_id": BUSINESS, "ad_account_id": ACCOUNT, "page_id": PAGE,
            "launch_key": "verification-fixture", "launch": {key: {"name": key} for key in ("campaign", "adset", "creative", "ad")}}

    async def run_launch(self):
        with patch.object(self.service, "_live_preflight", AsyncMock(return_value={"checked_live": True})):
            return await self.service.run(item_id="fixture", profile_id="fixture", context=self.context,
                session=self.session, payload=self.payload)

    async def test_each_create_requires_a_separate_exact_account_paused_relation_read(self):
        result = await self.run_launch()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(self.web.graphql.await_count, 8)
        reads = self.web.graphql.await_args_list[1::2]
        self.assertEqual([call.args[0] for call in reads], [str(900001 + i) for i in range(4)])
        self.assertEqual([call.args[1] for call in reads], [{"id": value} for value in IDS])
        self.assertTrue(self.web.private_only)
        self.assertTrue(all(call.kwargs["business_context_id"] == BUSINESS for call in reads))
        for step in PrivateLaunchStep:
            saved = await self.service.state.step("verification-fixture", step)
            self.assertTrue(saved["result"]["evidence"]["verification"]["verified"])

    async def test_unconfirmed_read_retains_created_id_and_never_replays_create(self):
        values = responses(); values[1]["data"]["node"]["account_id"] = "999999999"
        self.web.graphql.side_effect = values
        with self.assertRaises(ProvisioningError) as caught:
            await self.run_launch()
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_CAMPAIGN_READ_UNCONFIRMED")
        self.assertEqual(self.web.graphql.await_count, 2)
        saved = await self.service.state.step("verification-fixture", PrivateLaunchStep.CAMPAIGN)
        self.assertEqual(saved["status"], "RECONCILE_REQUIRED")
        self.assertEqual(saved["entity_id"], IDS[0])
        self.assertFalse(saved["result"]["verified"])
        self.web.graphql.reset_mock()
        with self.assertRaises(ProvisioningError):
            await self.run_launch()
        self.web.graphql.assert_not_awaited()

    async def test_missing_read_contract_stops_entire_chain_before_any_meta_request(self):
        cfg = contracts(); cfg["AD"].pop("verification")
        self.service.contracts = PrivateLaunchContractStore(json.dumps(cfg))
        with self.assertRaises(ProvisioningError) as caught:
            await self.run_launch()
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_VERIFICATION_CONTRACT_REQUIRED")
        self.web.graphql.assert_not_awaited()

    async def test_temporary_read_failure_resumes_query_without_recreating_campaign(self):
        self.web.graphql.side_effect = [responses()[0], TimeoutError()]
        with self.assertRaises(ProvisioningError):
            await self.run_launch()
        saved = await self.service.state.step("verification-fixture", PrivateLaunchStep.CAMPAIGN)
        self.assertEqual(saved["result"]["entity_id"], IDS[0])
        self.web.graphql.reset_mock()
        self.web.graphql.side_effect = responses()[1:]
        result = await self.run_launch()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result["steps"][0]["read_resumed"])
        self.assertEqual(self.web.graphql.await_count, 7)
        self.assertEqual(self.web.graphql.await_args_list[0].args, ("900001", {"id": IDS[0]}))
        self.assertEqual(len([call for call in self.web.graphql.await_args_list
                              if call.args[0] == contracts()["CAMPAIGN"]["doc_id"]]), 0)

    async def test_changed_configuration_cannot_resume_known_created_id(self):
        self.web.graphql.side_effect = [responses()[0], TimeoutError()]
        with self.assertRaises(ProvisioningError):
            await self.run_launch()
        self.payload["launch"]["campaign"]["name"] = "changed"
        self.web.graphql.reset_mock()
        with self.assertRaises(ProvisioningError) as caught:
            await self.run_launch()
        self.assertEqual(caught.exception.code, "PRIVATE_LAUNCH_KEY_PAYLOAD_MISMATCH")
        self.web.graphql.assert_not_awaited()

    async def test_downstream_read_resume_preserves_confirmed_parent_and_skips_both_creates(self):
        self.web.graphql.side_effect = responses()[:3] + [TimeoutError()]
        with self.assertRaises(ProvisioningError):
            await self.run_launch()
        self.web.graphql.reset_mock()
        self.web.graphql.side_effect = responses()[3:]
        result = await self.run_launch()
        self.assertEqual(result["entities"]["campaign_id"], IDS[0])
        self.assertTrue(result["steps"][1]["read_resumed"])
        self.assertEqual(self.web.graphql.await_count, 5)
        self.assertEqual(self.web.graphql.await_args_list[0].args, ("900002", {"id": IDS[1]}))

    async def test_interrupted_pending_read_recovers_after_timeout_without_create(self):
        with patch.object(self.service, "_verify_entity", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_launch()
        prior = await self.service.state.step("verification-fixture", PrivateLaunchStep.CAMPAIGN)
        self.assertEqual(prior["error_code"], "PRIVATE_LAUNCH_CAMPAIGN_READ_PENDING")
        self.web.graphql.reset_mock()
        self.web.graphql.side_effect = responses()[1:]
        with patch("app.private_launch.time.time", return_value=prior["updated_at"] + 91):
            result = await self.run_launch()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertTrue(result["steps"][0]["read_resumed"])
        self.assertEqual(self.web.graphql.await_count, 7)

    async def test_late_failed_read_cannot_overwrite_another_workers_success(self):
        self.web.graphql.side_effect = [responses()[0], TimeoutError()]
        with self.assertRaises(ProvisioningError):
            await self.run_launch()
        prior = await self.service.state.step("verification-fixture", PrivateLaunchStep.CAMPAIGN)
        confirmed = ActionResult.success("CAMPAIGN", entity_id=IDS[0], evidence={
            **prior["result"]["evidence"], "verification": {"verified": True}})
        self.assertTrue(await self.service.state.finish_read(prior, confirmed))
        failed = ActionResult.reconcile("CAMPAIGN", code="READ_UNAVAILABLE", message="fixture", submitted=True)
        self.assertFalse(await self.service.state.finish_read(prior, failed))
        saved = await self.service.state.step("verification-fixture", PrivateLaunchStep.CAMPAIGN)
        self.assertEqual(saved["status"], "SUCCESS")
        self.assertTrue(saved["result"]["verified"])

    def test_pending_read_needs_returned_id_configuration_and_expired_read_timeout(self):
        prior = {"status": "RECONCILE_REQUIRED", "entity_id": IDS[0], "submitted": 1,
                 "error_code": "PRIVATE_LAUNCH_CAMPAIGN_READ_PENDING", "updated_at": 100}
        with patch("app.private_launch.time.time", return_value=189):
            self.assertFalse(_resumable_read(prior, PrivateLaunchStep.CAMPAIGN))
        with patch("app.private_launch.time.time", return_value=190):
            self.assertTrue(_resumable_read(prior, PrivateLaunchStep.CAMPAIGN))
            self.assertFalse(_resumable_read({**prior, "entity_id": ""}, PrivateLaunchStep.CAMPAIGN))
            self.assertFalse(_resumable_read({**prior, "status": "SUBMITTING"}, PrivateLaunchStep.CAMPAIGN))

    def test_active_foreign_partial_and_wrong_parent_responses_never_confirm(self):
        row = contracts()["AD"]["verification"]
        values = {"entity_id": IDS[3], "ad_account_id": ACCOUNT, "campaign_id": IDS[0], "adset_id": IDS[1], "creative_id": IDS[2]}
        base = responses()[-1]
        self.assertTrue(proof(base, row, values)["verified"])
        for field, value in (("id", IDS[0]), ("account_id", "999999999"), ("status", "ACTIVE"),
                             ("campaign_id", "999999999"), ("adset_id", "999999999"), ("creative_id", "999999999")):
            candidate = copy.deepcopy(base); candidate["data"]["node"][field] = value
            self.assertFalse(proof(candidate, row, values)["verified"])
        for marker in ({"errors": [{"message": "fixture"}]}, {"hasNext": True}, {"extensions": {"is_final": False}}):
            self.assertFalse(proof({**base, **marker}, row, values)["verified"])
        self.assertFalse(proof({"metadata": base["data"]}, row, values)["verified"])
        for bad in ({**row, "operation_kind": "mutation"}, {**row, "id_path": "metadata.node.id"},
                    {**row, "account_path": row["id_path"]}, {**row, "relations": {}},
                    {**row, "endpoint_url": "https://graph.facebook.com/api/graphql/"}):
            with self.assertRaises(ProvisioningError):
                validate_contract(bad, "AD")


if __name__ == "__main__":
    unittest.main()
