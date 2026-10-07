import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.private_page_access import (
    PageAccessContractStore,
    page_access_request_match,
)
from app.provisioning.models import ProvisioningError


class PrivatePageAccessContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.contract_path = Path(self.tmp.name) / "page-access.json"
        self.docids_path = Path(self.tmp.name) / "docids.json"
        self.docid_patch = patch(
            "app.facebook_docids.STORE_PATH",
            self.docids_path,
        )
        self.docid_patch.start()
        self.addCleanup(self.docid_patch.stop)

    def test_match_requires_exact_business_page_and_access_semantics(self):
        meta = {
            "doc_id": "123456789",
            "friendly_name": "BizKitRequestPageAccessMutation",
            "variables": {
                "input": {
                    "business_id": "1109354271784041",
                    "page_id": "1318717134669505",
                    "tasks": ["ADVERTISE"],
                }
            },
        }
        self.assertTrue(
            page_access_request_match(
                meta,
                business_id="1109354271784041",
                page_id="1318717134669505",
            )
        )
        self.assertFalse(
            page_access_request_match(
                meta,
                business_id="999999999999999",
                page_id="1318717134669505",
            )
        )
        self.assertFalse(
            page_access_request_match(
                {
                    "doc_id": "123456789",
                    "friendly_name": "InventoryQuery",
                    "variables": {
                        "input": {
                            "business_id": "1109354271784041",
                            "page_id": "1318717134669505",
                        }
                    },
                },
                business_id="1109354271784041",
                page_id="1318717134669505",
            )
        )

    def test_capture_persists_reusable_template_without_target_ids(self):
        store = PageAccessContractStore(self.contract_path)
        contract = store.register_capture(
            {
                "doc_id": "123456789",
                "friendly_name": "BizKitRequestPageAccessMutation",
                "endpoint_url": "https://business.facebook.com/api/graphql/",
                "variables": {
                    "input": {
                        "business_id": "1109354271784041",
                        "page_id": "1318717134669505",
                        "asset_ref": "page:1318717134669505",
                        "tasks": ["ADVERTISE"],
                    }
                },
                "request_envelope": {
                    "__req": "a",
                    "dpr": "1",
                },
            },
            business_id="1109354271784041",
            page_id="1318717134669505",
        )
        self.assertEqual(contract.doc_id, "123456789")
        raw = self.contract_path.read_text(encoding="utf-8")
        self.assertNotIn("1109354271784041", raw)
        self.assertNotIn("1318717134669505", raw)
        self.assertIn("{{business_id}}", raw)
        self.assertIn("{{page_id}}", raw)

    async def test_execute_renders_exact_target_through_private_web_session(self):
        store = PageAccessContractStore(self.contract_path)
        store.register_capture(
            {
                "doc_id": "123456789",
                "friendly_name": "BizKitRequestPageAccessMutation",
                "endpoint_url": "https://business.facebook.com/api/graphql/",
                "variables": {
                    "input": {
                        "business_id": "1109354271784041",
                        "page_id": "1318717134669505",
                        "tasks": ["ADVERTISE"],
                    }
                },
                "request_envelope": {
                    "__req": "capture",
                    "dpr": "1",
                },
            },
            business_id="1109354271784041",
            page_id="1318717134669505",
        )
        web = AsyncMock()
        web.graphql.return_value = {
            "data": {"requestPageAccess": {"ok": True}}
        }

        payload = await store.execute(
            web,
            business_id="991479610630943",
            page_id="1888888888888888",
            profile_id="14",
        )

        self.assertIn("data", payload)
        web.graphql.assert_awaited_once()
        call = web.graphql.await_args
        self.assertEqual(call.args[0], "123456789")
        self.assertEqual(
            call.args[1]["input"]["business_id"],
            "991479610630943",
        )
        self.assertEqual(
            call.args[1]["input"]["page_id"],
            "1888888888888888",
        )
        self.assertEqual(
            call.kwargs["friendly_name"],
            "BizKitRequestPageAccessMutation",
        )
        self.assertEqual(
            call.kwargs["request_envelope"]["__req"],
            "capture",
        )

    async def test_stale_graphql_contract_is_classified_before_retry(self):
        store = PageAccessContractStore(self.contract_path)
        store.register_capture(
            {
                "doc_id": "123456789",
                "friendly_name": "BizKitRequestPageAccessMutation",
                "endpoint_url": "https://business.facebook.com/api/graphql/",
                "variables": {
                    "input": {
                        "business_id": "1109354271784041",
                        "page_id": "1318717134669505",
                        "tasks": ["ADVERTISE"],
                    }
                },
                "request_envelope": {},
            },
            business_id="1109354271784041",
            page_id="1318717134669505",
        )
        web = AsyncMock()
        web.graphql.return_value = {
            "errors": [
                {
                    "message": "PersistedQueryNotFound: unknown document",
                    "code": 1357054,
                }
            ]
        }

        with self.assertRaises(ProvisioningError) as caught:
            await store.execute(
                web,
                business_id="1109354271784041",
                page_id="1318717134669505",
                profile_id="14",
            )
        self.assertEqual(
            caught.exception.code,
            "PRIVATE_PAGE_ACCESS_CONTRACT_STALE",
        )


if __name__ == "__main__":
    unittest.main()
