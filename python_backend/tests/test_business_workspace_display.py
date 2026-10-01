import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.provisioning.models import ProvisioningStep
from app.provisioning.state import ProvisioningStateStore
from app.runner import WorkerPool
from app.store import JobStore


class BusinessWorkspaceDisplayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = ProvisioningStateStore(str(self.root / "jobs.sqlite"))
        await self.state.init()

    async def checkpoint(self, item="bm", **changes):
        await self.state.set_running(item, "7", "scope-" + item, ProvisioningStep.BUSINESS)
        result = {
            "phase": "CREATE_CONFIRMED", "business_id": "2478360152656679",
            "business_name": "Confirmed Business",
            "create_response_business_id": "2478360152656679",
            "create_response_path": "data.bizkit_create_business.id",
            **changes,
        }
        await self.state.checkpoint(item, "7", "scope-" + item, ProvisioningStep.BUSINESS, result)

    async def test_exact_create_is_displayed_without_rk_or_page_attach(self):
        await self.checkpoint(phase="PAGE_ADD_NOT_SUBMITTED")
        groups = await self.state.confirmed_business_binding_groups()
        row = groups["7"]["businesses"]["2478360152656679"]
        self.assertEqual(row["business_name"], "Confirmed Business")
        self.assertTrue(row["create_confirmed"])
        self.assertNotIn("primary_page_id", row)
        self.assertEqual(await self.state.confirmed_ad_account_binding_groups(), {})

    async def test_mere_success_and_unrelated_response_are_not_create_proof(self):
        await self.checkpoint(create_response_path="data.viewer.actor.id")
        groups = await self.state.confirmed_business_binding_groups()
        self.assertEqual(groups, {})
        await self.state.complete("bm", "7", "scope-bm", ProvisioningStep.BUSINESS,
                                  {"business_id": "2478360152656679", "phase": "CREATE_CONFIRMED"})
        self.assertEqual(await self.state.confirmed_business_binding_groups(), {})

    async def test_legacy_native_create_then_attach_failure_is_creation_proof(self):
        await self.checkpoint(create_response_business_id="", create_response_path="",
            private_create_error_code="BUSINESS_CREATED_PAGE_ATTACH_FAILED",
            private_create_diagnostics=[{"stage":"set_primary_page",
                "result":"failed_after_business_created","business_id":"2478360152656679"}])
        groups = await self.state.confirmed_business_binding_groups()
        self.assertIn("2478360152656679", groups["7"]["businesses"])
        await self.checkpoint(private_create_diagnostics=[{"stage":"set_primary_page",
            "result":"failed_after_business_created","business_id":"999999999"}],
            create_response_business_id="", create_response_path="",
            private_create_error_code="BUSINESS_CREATED_PAGE_ATTACH_FAILED")
        self.assertEqual(await self.state.confirmed_business_binding_groups(), {})

    async def test_legacy_nested_exact_create_response_is_preserved(self):
        await self.checkpoint(create_response_business_id="", create_response_path="",
            create={"response_business_id":"2478360152656679",
                    "response_path":"data.bizkit_create_business.id"})
        groups = await self.state.confirmed_business_binding_groups()
        self.assertIn("2478360152656679", groups["7"]["businesses"])

    async def test_multiple_jobs_deduplicate_one_business(self):
        await self.checkpoint(item="old")
        await self.checkpoint(item="new", business_name="Current name")
        groups = await self.state.confirmed_business_binding_groups()
        self.assertEqual(len(groups["7"]["businesses"]), 1)

    async def test_worker_restart_persists_bm_before_any_rk_exists(self):
        await self.checkpoint()
        jobs = JobStore(str(self.root / "jobs.sqlite"))
        await jobs.init()
        pool = WorkerPool(jobs, concurrency=1)
        with patch.dict(os.environ, {"REMASK_DATA_DIR": str(self.root)}):
            await pool._restore_workspace_bindings()
            await pool._restore_workspace_bindings()
        saved = json.loads((self.root / "workspace-created-businesses.json").read_text())
        self.assertIn("2478360152656679", saved["7"]["businesses"])
        self.assertEqual(await pool.provisioning_state.confirmed_ad_account_binding_groups(), {})

    async def test_php_merges_confirmed_bm_after_live_snapshot_and_deduplicates(self):
        overlay = Path(__file__).resolve().parents[2] / "railway-workspace-sync-fix-overlay.php"
        source = overlay.read_text()
        start = source.index("function hierarchy_created_businesses_apply_display(")
        end = source.index("function hierarchy_binding_file(", start)
        helper = source[start:end]
        display_file = self.root / "workspace-created-businesses.json"
        display_file.write_text(json.dumps({"7": {"businesses": {
            "2478360152656679": {"business_id": "2478360152656679",
                "business_name": "Created BM", "create_confirmed": True},
            "999999999": {"business_id": "999999999", "create_confirmed": False}}}}))
        helper = helper.replace("/var/lib/remask/workspace-created-businesses.json", str(display_file))
        php = helper + "\n$input=json_decode(file_get_contents('php://stdin'),true); echo json_encode(hierarchy_created_businesses_apply_display('7', $input));"
        snapshot = {"profile": {"bm_count": 1}, "businesses": [{"id": "111111111", "name": "Old BM"}], "ad_accounts": []}
        first = subprocess.run(["php", "-r", php], input=json.dumps(snapshot), text=True,
                               capture_output=True, check=True, timeout=5)
        result = json.loads(first.stdout)
        self.assertEqual([row["id"] for row in result["businesses"]], ["111111111", "2478360152656679"])
        self.assertEqual(result["profile"]["bm_count"], 2)
        self.assertIsNone(result["businesses"][1]["primary_page"])
        self.assertEqual(result["businesses"][1]["ad_account_count"], 0)
        again = subprocess.run(["php", "-r", php], input=json.dumps(result), text=True,
                               capture_output=True, check=True, timeout=5)
        self.assertEqual(json.loads(again.stdout), result)
