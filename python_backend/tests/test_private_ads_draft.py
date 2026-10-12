from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from app.private_ads_draft import fragment_params, flatten_params, publication_status, publish_params
from app.private_ads_http import context_from_document
from app.private_ads_state import AdsManagerSubmitter
from app.private_ads_publication import AdsManagerDraftPublisher
from app.private_launch import PrivateLaunchStateStore
from app.provisioning.models import ProvisioningError


def document(actor="123456", token="synthetic-session-value"):
    return json.dumps({"define": [["GraphAPIConfig", [], {"adsApiVersion": "v22.0"}],
        ["UrlMapConfig", [], {"graph": "adsmanager-graph.facebook.com"}],
        ["CurrentUserInitialData", [], {"USER_ID": actor}]], "require": [
        ["ApiClient", "setAccessToken", [], [token]],
        ["ApiClient", "setClientID", [], ["654321"]]]})


def completed():
    return {"data": [{"async_request_set_id": "789012", "status": "SUCCEEDED",
        "total_count": 3, "success_count": 3, "error_count": 0, "canceled_count": 0,
        "initial_count": 0, "in_progress_count": 0, "fragment_statuses": {"data": [
            {"fragment_id": value, "status": "SUCCESS"} for value in ["111111", "222222", "333333"]]}}]}


class WireTests(unittest.TestCase):
    def test_source_encoding_preserves_null_boolean_unicode_and_input(self):
        values = {"status": "PAUSED", "name": "Реклама", "targeting": {"countries": ["UA"]}}
        old = copy.deepcopy(values)
        result = fragment_params("123456", level="campaign", values=values)
        self.assertEqual(values, old)
        entries = {row["field"]: row for row in result["values"]}
        self.assertEqual(entries["status"]["new_value"], '"PAUSED"')
        self.assertEqual(entries["targeting"]["old_value"], "null")
        flat = flatten_params({"omitted": None, "empty": "", "boolean": False, "list": [1, 2]})
        self.assertEqual(flat, {"empty": "", "boolean": "false", "list": "[1,2]"})

    def test_new_fragment_requires_explicit_paused(self):
        for values in ({"name": "x"}, {"status": "ACTIVE"}):
            with self.subTest(values=values), self.assertRaises(ProvisioningError):
                fragment_params("123456", level="ad", values=values)

    def test_credentials_and_nonfinite_values_cannot_enter_fragment(self):
        with self.assertRaises(ProvisioningError):
            fragment_params("123456", level="ad", values={"status": "PAUSED", "access_token": "not-allowed"})
        with self.assertRaises(ProvisioningError):
            flatten_params({"budget": float("nan")})

    def test_publication_requires_exact_distinct_numeric_ids(self):
        for values in (["111111", "111111"], ["111111", {}], ["act_111111"], []):
            with self.subTest(values=values), self.assertRaises(ProvisioningError):
                publish_params(values)

    def test_success_is_not_object_verification(self):
        result = publication_status(completed(), operation_id="789012",
                                    fragment_ids=["111111", "222222", "333333"])
        self.assertTrue(result["completed"])
        self.assertFalse(result["objects_verified"])

    def test_acceptance_pending_never_reports_complete(self):
        value = completed();value["data"][0]["status"] = "IN_PROGRESS"
        self.assertFalse(publication_status(value, operation_id="789012",
            fragment_ids=["111111", "222222", "333333"])["completed"])

    def test_mixed_errors_foreign_fragments_and_wrong_counters_reject(self):
        cases = []
        value = completed();value["data"][0]["fragment_statuses"]["data"][1]["status"] = "ERROR";cases.append(value)
        value = completed();value["data"][0]["fragment_statuses"]["data"][1]["fragment_id"] = "444444";cases.append(value)
        value = completed();value["data"][0]["success_count"] = True;cases.append(value)
        value = completed();value["data"].append(copy.deepcopy(value["data"][0]));cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ProvisioningError):
                publication_status(value, operation_id="789012", fragment_ids=["111111", "222222", "333333"])

    def test_session_configuration_not_repr_or_persisted(self):
        context = context_from_document(document(), account_id="111111", business_id="222222", actor_id="123456")
        self.assertNotIn("synthetic-session-value", repr(context))
        self.assertEqual(context.origin, "https://adsmanager-graph.facebook.com")

    def test_actor_route_and_conflicting_session_reject(self):
        docs = [document(actor="999999"), document().replace("v22.0", "v23.0"),
                document().replace("adsmanager-graph.facebook.com", "graph.facebook.com")]
        value = json.loads(document());value["require"].append(["ApiClient", "setAccessToken", [], ["another-synthetic-token"]]);docs.append(json.dumps(value))
        for value in docs:
            with self.subTest(value=value), self.assertRaises(ProvisioningError):
                context_from_document(value, account_id="111111", business_id="222222", actor_id="123456")

    def test_arbitrary_access_token_property_is_not_session_initialization(self):
        value = json.loads(document());value["require"] = []
        value["access_token"] = "synthetic-session-value"
        with self.assertRaises(ProvisioningError):
            context_from_document(json.dumps(value), account_id="111111", business_id="222222", actor_id="123456")


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state = PrivateLaunchStateStore(Path(self.tmp.name)/"state.sqlite")
        await self.state.init()
        self.sender = AdsManagerSubmitter(self.state, launch_key="launch", profile_id="19",
            business_id="222222", account_id="111111", actor_id="123456", launch_fingerprint="fixed-inputs", source_digest="source")

    async def test_lost_reply_never_replays(self):
        class Client:
            count = 0
            async def post(inner, path, params, *, before_submit, files):
                inner.count += 1;await before_submit();raise TimeoutError()
        client = Client()
        for _ in range(2):
            with self.assertRaises(ProvisioningError):
                await self.sender.submit(client, operation="DRAFT", path="/act_111111/addrafts",
                    params={"name": "test"}, receipt=lambda row: {"draft_id": row["id"]})
        self.assertEqual(client.count, 1)

    async def test_saved_receipt_survives_reopen_without_second_post(self):
        class Client:
            count = 0
            async def post(inner, path, params, *, before_submit, files):
                inner.count += 1;await before_submit();return {"id": "333333"}
        client = Client()
        kwargs = {"operation": "DRAFT", "path": "/act_111111/addrafts", "params": {"name": "test"},
                  "receipt": lambda row: {"draft_id": row["id"]}}
        one = await self.sender.submit(client, **kwargs)
        self.sender.state = PrivateLaunchStateStore(Path(self.tmp.name)/"state.sqlite")
        two = await self.sender.submit(client, **kwargs)
        self.assertEqual(one, two);self.assertEqual(client.count, 1)
        rows = await self.state.all_steps("launch")
        self.assertEqual(rows[0]["status"], "RECONCILE_REQUIRED")
        self.assertFalse(rows[0]["result"]["verified"])

    async def test_changed_inputs_cannot_reuse_receipt(self):
        class Client:
            async def post(inner, path, params, *, before_submit, files):
                await before_submit();return {"id": "333333"}
        await self.sender.submit(Client(), operation="DRAFT", path="/act_111111/addrafts",
            params={"name": "first"}, receipt=lambda row: {"draft_id": row["id"]})
        with self.assertRaises(ProvisioningError):
            await self.sender.submit(Client(), operation="DRAFT", path="/act_111111/addrafts",
                params={"name": "second"}, receipt=lambda row: {"draft_id": row["id"]})

    async def test_failure_before_claim_does_not_poison_retry(self):
        class Client:
            async def post(inner, path, params, *, before_submit, files):
                raise ConnectionError()
        with self.assertRaises(ConnectionError):
            await self.sender.submit(Client(), operation="DRAFT", path="/act_111111/addrafts",
                params={}, receipt=lambda row: {"draft_id": row["id"]})
        self.assertEqual(await self.state.all_steps("launch"), [])


class DraftClient:
    def __init__(self):
        self.context = SimpleNamespace(account_id="111111", business_id="222222",
            actor_id="123456", application_id="654321")
        self.posts = [];self.fragments = {};self.pending = False;self.foreign = False

    async def post(self, path, params, *, before_submit, files):
        await before_submit();self.posts.append((path, copy.deepcopy(params)))
        if path.endswith("/addrafts"):
            return {"id": "555555"}
        if path.endswith("/addraft_fragments"):
            index = len(self.fragments)
            fragment_id = ["111111", "222222", "333333"][index]
            row = {"id": fragment_id, "account_id": "111111", "ad_draft_id": "555555",
                "ad_object_id": ["777771", "777772", "777773"][index],
                "ad_object_type": params["ad_object_type"], "values": params["values"],
                "parent_ad_object_id": params.get("parent_ad_object_id")}
            self.fragments[fragment_id] = row
            return {"id": fragment_id}
        return {"success": True, "async_request_set_id": "789012"}

    async def get(self, path, params):
        if path == "/addraft_555555":
            return {"id": "555555", "account_id": "111111", "author_id": "123456", "ownership_type": "USER"}
        if path.startswith("/addraftfragment_"):
            row = copy.deepcopy(self.fragments[path.removeprefix("/addraftfragment_")])
            if self.foreign:row["account_id"] = "999999"
            return row
        result = completed()
        if self.pending:result["data"][0]["status"] = "IN_PROGRESS"
        return result


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await JournalTests.asyncSetUp(self)
        self.publisher = AdsManagerDraftPublisher(self.sender)
        self.client = DraftClient()
        self.levels = {level: {"name": level, "status": "PAUSED"} for level in ("campaign", "ad_set", "ad")}

    async def verify(self, client, objects, levels):
        return {"verified": True, "entities": {"campaign_id": "777771", "adset_id": "777772",
                                               "ad_id": "777773", "creative_id": "777774"}}

    async def launch(self, verify=None):
        return await self.publisher.run(self.client, name="source-flow", levels=self.levels,
                                        verify_objects=verify or self.verify)

    async def test_three_fragments_publish_and_verify_then_resume_without_post(self):
        result = await self.launch()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual([row[1]["ad_object_type"] for row in self.client.posts[1:4]], ["campaign", "ad_set", "ad"])
        await self.launch();self.assertEqual(len(self.client.posts), 5)

    async def test_pending_publication_resumes_status_without_new_post(self):
        self.client.pending = True
        with self.assertRaises(ProvisioningError) as error:await self.launch()
        self.assertEqual(error.exception.code, "PRIVATE_ADS_DRAFT_PUBLISH_PENDING")
        self.assertEqual(len(self.client.posts), 5)
        self.client.pending = False
        await self.launch();self.assertEqual(len(self.client.posts), 5)

    async def test_foreign_fragment_stops_before_other_fragments_or_publish(self):
        self.client.foreign = True
        with self.assertRaises(ProvisioningError):await self.launch()
        self.assertEqual(len(self.client.posts), 2)

    async def test_unverified_objects_never_mark_publication_success(self):
        async def unavailable(*args):return {"verified": False}
        with self.assertRaises(ProvisioningError):await self.launch(verify=unavailable)
        rows = await self.state.all_steps("launch")
        publication = next(row for row in rows if row["step"] == "ADS_MANAGER:PUBLISH")
        self.assertEqual(publication["status"], "RECONCILE_REQUIRED")
        self.assertFalse(publication["result"]["verified"])
        await self.launch();self.assertEqual(len(self.client.posts), 5)

    async def test_wrong_verified_ids_do_not_complete(self):
        async def unrelated(*args):
            row = await self.verify(*args);row["entities"]["ad_id"] = "999999";return row
        with self.assertRaises(ProvisioningError):await self.launch(verify=unrelated)

    async def test_bad_downstream_input_is_rejected_before_first_post(self):
        self.levels["ad"]["status"] = "ACTIVE"
        with self.assertRaises(ProvisioningError):await self.launch()
        self.assertEqual(self.client.posts, [])
