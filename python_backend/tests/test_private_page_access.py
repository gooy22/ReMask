import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fb_worker import RemoteRequestError
from unittest.mock import AsyncMock, patch

from app.private_page_access import (
    PageAccessContractStore,
    page_access_request_match,
    submit_page_access_request,
    PAGE_ACCESS_PENDING_PHASES,
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


    def _store(self, actor=False):
        store = PageAccessContractStore(self.contract_path)
        variables = {"input": {"business_id": "1109354271784041", "page_id": "1318717134669505", "tasks": ["ADVERTISE"]}}
        if actor:
            variables["input"]["actor_id"] = "61594946647826"
        store.register_capture({"doc_id": "123456789", "friendly_name": "BizKitRequestPageAccessMutation", "variables": variables, "request_envelope": {}},
            business_id="1109354271784041", page_id="1318717134669505", actor_id="61594946647826" if actor else "")
        return store

    async def test_captured_actor_is_rebound_to_current_profile(self):
        store = self._store(actor=True)
        self.assertNotIn("61594946647826", self.contract_path.read_text())
        web = SimpleNamespace(profile=SimpleNamespace(cookies={"c_user": "61500000000099"}),
                              graphql=AsyncMock(return_value={"data": {"request": {"ok": True}}}))
        await store.execute(web, business_id="991479610630943", page_id="1888888888888888", profile_id="15")
        self.assertEqual(web.graphql.await_args.args[1]["input"]["actor_id"], "61500000000099")

    def test_legacy_concrete_actor_contract_is_not_replayed(self):
        store = self._store(actor=True)
        row = json.loads(self.contract_path.read_text())
        row["variables"]["input"]["actor_id"] = "61594946647826"
        self.contract_path.write_text(json.dumps(row))
        self.assertIsNone(store.get())

    async def test_top_level_meta_error_is_rejection_without_recording_success(self):
        store = self._store()
        web = SimpleNamespace(graphql=AsyncMock(return_value={"error": 1357054, "errorSummary": "Your Request Couldn't be Processed", "payload": None}))
        with patch("app.private_page_access.record_result") as record:
            with self.assertRaises(ProvisioningError) as caught:
                await store.execute(web, business_id="1109354271784041", page_id="1318717134669505", profile_id="14")
        self.assertEqual(caught.exception.code, "PRIVATE_PAGE_ACCESS_META_REJECTED")
        self.assertTrue(caught.exception.request_rejected)
        self.assertFalse(record.call_args.kwargs["success"])

    async def test_empty_and_partial_error_responses_remain_unknown(self):
        store = self._store()
        for payload in ({}, {"data": {}}, {"data": {"request": {"ok": True}}, "errors": [{"message": "incomplete"}]}):
            with self.subTest(payload=payload):
                web = SimpleNamespace(graphql=AsyncMock(return_value=payload))
                with self.assertRaises(ProvisioningError) as caught:
                    await store.execute(web, business_id="1109354271784041", page_id="1318717134669505", profile_id="14")
                self.assertEqual(caught.exception.code, "PRIVATE_PAGE_ACCESS_RESULT_UNKNOWN")
                self.assertFalse(getattr(caught.exception, "request_rejected", False))

    async def test_submit_intent_is_durable_before_post_and_lost_response_remains_pending(self):
        store = self._store()
        phases = []
        async def checkpoint(value):
            phases.append(value["phase"])
        async def graphql(*args, before_submit, **kwargs):
            await before_submit()
            self.assertEqual(phases[-1], "TARGET_PAGE_ACCESS_PRIVATE_SUBMIT_INTENT")
            raise RemoteRequestError("response lost", request_may_have_been_sent=True)
        with self.assertRaises(ProvisioningError) as caught:
            await submit_page_access_request(store, SimpleNamespace(graphql=graphql), business_id="1109354271784041", page_id="1318717134669505", profile_id="14", checkpoint=checkpoint)
        self.assertEqual(caught.exception.code, "PRIVATE_PAGE_ACCESS_RESULT_UNKNOWN")
        self.assertEqual(phases, ["TARGET_PAGE_ACCESS_PRIVATE_SUBMIT_INTENT", "TARGET_PAGE_ACCESS_PRIVATE_RESULT_UNKNOWN"])
        self.assertIn(phases[-1], PAGE_ACCESS_PENDING_PHASES)

    async def test_checkpoint_failure_prevents_post(self):
        store = self._store()
        posted = []
        async def graphql(*args, before_submit, **kwargs):
            await before_submit()
            posted.append(True)
        with self.assertRaises(OSError):
            await submit_page_access_request(store, SimpleNamespace(graphql=graphql), business_id="1109354271784041", page_id="1318717134669505", profile_id="14", checkpoint=AsyncMock(side_effect=OSError("state unavailable")))
        self.assertEqual(posted, [])

    async def test_explicit_rejection_clears_pending_state_but_never_commits_rights(self):
        store = self._store()
        phases = []
        async def checkpoint(value):
            phases.append(value["phase"])
        async def graphql(*args, before_submit, **kwargs):
            await before_submit()
            return {"error": 1357054}
        with self.assertRaises(ProvisioningError):
            await submit_page_access_request(store, SimpleNamespace(graphql=graphql), business_id="1109354271784041", page_id="1318717134669505", profile_id="14", checkpoint=checkpoint)
        self.assertEqual(phases[-1], "TARGET_PAGE_ACCESS_PRIVATE_REJECTED")
        self.assertNotIn(phases[-1], PAGE_ACCESS_PENDING_PHASES)

    async def test_retry_pending_private_request_never_clicks_or_posts_again(self):
        from app.provisioning.page_access_handler import _request_target_page_access
        browser = SimpleNamespace(goto=AsyncMock())
        for phase in ("TARGET_PAGE_ACCESS_PRIVATE_SUBMIT_INTENT", "TARGET_PAGE_ACCESS_PRIVATE_RESULT_UNKNOWN"):
            result = await _request_target_page_access(browser, {"page_id": "1318717134669505"}, "1109354271784041", AsyncMock(), {"phase": phase})
            self.assertFalse(result)
        browser.goto.assert_not_awaited()

    def test_capture_match_rejects_query_get_and_ownership_mutations(self):
        meta = {"doc_id": "123456789", "variables": {"input": {"business_id": "1109354271784041", "page_id": "1318717134669505", "tasks": ["ADVERTISE"]}}}
        for friendly, method in (("BizKitPageAccessQuery", "POST"), ("BizKitRequestPageAccessMutation", "GET"), ("BizKitClaimPageOwnershipMutation", "POST")):
            self.assertFalse(page_access_request_match({**meta, "friendly_name": friendly, "method": method}, business_id="1109354271784041", page_id="1318717134669505"))


if __name__ == "__main__":
    unittest.main()
