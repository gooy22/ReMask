import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.private_page_access import (
    PAGE_SHARE_OPERATION,
    PrivatePageShareContractStore,
    execute_private_page_share,
)
from app.facebook_docids import registry_view
from app.provisioning.models import ProvisioningError


class PrivatePageShareContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.contract_path = Path(self.tmp.name) / "page-share.json"
        self.docid_path = Path(self.tmp.name) / "docids.json"
        self.docid_patch = patch(
            "app.facebook_docids.STORE_PATH",
            self.docid_path,
        )
        self.docid_patch.start()
        self.addCleanup(self.docid_patch.stop)

    def _capture(self):
        return {
            "doc_id": "987654321",
            "friendly_name": "BusinessRequestPageAccessMutation",
            "endpoint_url": "https://business.facebook.com/api/graphql/",
            "variables": {
                "input": {
                    "business_id": "1109354271784041",
                    "page_id": "1318717134669505",
                    "actor_id": "61594882851656",
                    "tasks": ["ADVERTISE"],
                }
            },
            "request_envelope": {
                "__req": "a",
                "dpr": "1",
            },
            "source": "browser_graphql_capture",
            "observed_at": "1791400000",
        }

    def test_capture_is_templated_and_target_ids_are_not_persisted(self):
        store = PrivatePageShareContractStore(path=self.contract_path)
        contract = store.register_capture(
            self._capture(),
            business_id="1109354271784041",
            page_id="1318717134669505",
            actor_id="61594882851656",
        )
        self.assertEqual(contract.doc_id, "987654321")
        disk = self.contract_path.read_text(encoding="utf-8")
        self.assertNotIn("1109354271784041", disk)
        self.assertNotIn("1318717134669505", disk)
        self.assertNotIn("61594882851656", disk)
        self.assertIn("{{business_id}}", disk)
        self.assertIn("{{page_id}}", disk)
        self.assertIn("{{actor_id}}", disk)

        rendered = store.render(
            contract,
            business_id="222222222222222",
            page_id="333333333333333",
            actor_id="444444444444444",
        )
        self.assertEqual(
            rendered["input"]["business_id"],
            "222222222222222",
        )
        self.assertEqual(
            rendered["input"]["page_id"],
            "333333333333333",
        )
        self.assertEqual(
            rendered["input"]["actor_id"],
            "444444444444444",
        )

        registry = registry_view(PAGE_SHARE_OPERATION)
        rows = registry["operations"][PAGE_SHARE_OPERATION]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["doc_id"], "987654321")

    def test_capture_rejects_auth_material(self):
        capture = self._capture()
        capture["variables"]["fb_dtsg"] = "secret"
        store = PrivatePageShareContractStore(path=self.contract_path)
        with self.assertRaises(ProvisioningError) as caught:
            store.register_capture(
                capture,
                business_id="1109354271784041",
                page_id="1318717134669505",
                actor_id="61594882851656",
            )
        self.assertEqual(
            caught.exception.code,
            "PRIVATE_PAGE_SHARE_CONTRACT_INVALID",
        )

    async def test_private_executor_renders_target_and_records_success(self):
        store = PrivatePageShareContractStore(path=self.contract_path)
        contract = store.register_capture(
            self._capture(),
            business_id="1109354271784041",
            page_id="1318717134669505",
            actor_id="61594882851656",
        )
        web = type("Web", (), {})()
        web.graphql = AsyncMock(return_value={
            "data": {"request_page_access": {"success": True}}
        })

        result = await execute_private_page_share(
            web,
            contract,
            business_id="222222222222222",
            page_id="333333333333333",
            actor_id="444444444444444",
            profile_id="14",
        )

        self.assertIn("data", result)
        kwargs = web.graphql.await_args.kwargs
        args = web.graphql.await_args.args
        self.assertEqual(args[0], "987654321")
        self.assertEqual(
            args[1]["input"]["business_id"],
            "222222222222222",
        )
        self.assertEqual(
            args[1]["input"]["page_id"],
            "333333333333333",
        )
        self.assertEqual(
            args[1]["input"]["actor_id"],
            "444444444444444",
        )
        self.assertEqual(
            kwargs["friendly_name"],
            "BusinessRequestPageAccessMutation",
        )

        registry = registry_view(PAGE_SHARE_OPERATION)
        row = registry["operations"][PAGE_SHARE_OPERATION][0]
        self.assertEqual(row["stats"]["success_count"], 1)

    async def test_graphql_errors_are_not_committed_as_success(self):
        store = PrivatePageShareContractStore(path=self.contract_path)
        contract = store.register_capture(
            self._capture(),
            business_id="1109354271784041",
            page_id="1318717134669505",
            actor_id="61594882851656",
        )
        web = type("Web", (), {})()
        web.graphql = AsyncMock(return_value={
            "errors": [{"message": "permission denied"}]
        })

        with self.assertRaises(ProvisioningError) as caught:
            await execute_private_page_share(
                web,
                contract,
                business_id="222222222222222",
                page_id="333333333333333",
                actor_id="444444444444444",
                profile_id="14",
            )
        self.assertEqual(
            caught.exception.code,
            "PRIVATE_PAGE_SHARE_REJECTED",
        )


if __name__ == "__main__":
    unittest.main()
