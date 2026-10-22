from __future__ import annotations

import copy
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.private_ads_draft import fragment_params, fragment_values_match, flatten_params, publication_receipt, publication_status, publish_params
from app.private_ads_http import context_from_document, AdsManagerResponseError
from app.private_ads_state import AdsManagerSubmitter
from app.action_result import ActionResult
from app.private_ads_publication import AdsManagerDraftPublisher
from app.private_ads_verify import _scope as object_read_scope
from app.private_launch_extensions import OperationKey
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
        self.assertEqual(entries["status"]["new_value"], "PAUSED")
        self.assertIsNone(entries["targeting"]["old_value"])
        self.assertEqual(entries["targeting"]["new_value"], values["targeting"])
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


    def test_raw_json_like_strings_are_not_reinterpreted(self):
        expected = {"name": "true", "description": "null", "status": "PAUSED", "budget": 100, "enabled": False}
        rows = [{"field": key, "old_value": None, "new_value": value} for key, value in expected.items()]
        self.assertTrue(fragment_values_match(rows, expected))
        rows[-1]["new_value"] = 0
        self.assertFalse(fragment_values_match(rows, expected))

    def test_saved_fragment_json_rejects_ambiguous_duplicate_keys(self):
        desired = {'targeting': {'age_min': 23, 'age_max': 33}}
        row = {'field': 'targeting', 'old_value': None,
               'new_value': '{"age_min":23,"age_max":33}'}
        self.assertTrue(fragment_values_match([row], desired))
        for bad in ('{"age_min":23,"age_min":23,"age_max":33}',
                    '{"age_min":23,"age_max":33,"extra":NaN}',
                    '{"age_min":23,"age_max":33,"extra":Infinity}'):
            with self.subTest(bad=bad):
                self.assertFalse(fragment_values_match([{**row, 'new_value': bad}], desired))

    def test_raw_fragment_status_array_is_verified_without_ui_cache_wrapper(self):
        row = completed()
        row['data'][0]['fragment_statuses'] = row['data'][0]['fragment_statuses']['data']
        self.assertTrue(publication_status(row, operation_id='789012',
            fragment_ids=['111111', '222222', '333333'])['completed'])

    def test_independent_object_scope_rejects_empty_error_envelopes(self):
        record = {'id': '777771', 'account_id': '111111'}
        object_read_scope(record, '111111', '777771')
        for envelope in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(envelope=envelope):
                with self.assertRaises(ProvisioningError) as caught:
                    object_read_scope({**record, **envelope}, '111111', '777771')
                self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_OBJECT_ID_MISMATCH')

    def test_async_handle_does_not_override_explicit_publication_rejection(self):
        for value in ({'success': False, 'async_request_set_id': '789012'}, {'async_request_set_id': '789012'}):
            with self.assertRaises(ProvisioningError):
                publication_receipt(value)



class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state = PrivateLaunchStateStore(Path(self.tmp.name)/"state.sqlite")
        await self.state.init()
        self.sender = AdsManagerSubmitter(self.state, launch_key="launch", profile_id="19",
            business_id="222222", account_id="111111", actor_id="123456", launch_fingerprint="fixed-inputs", source_digest="source")

    async def test_nonfinite_journal_evidence_is_rejected_before_sqlite_write(self):
        key = OperationKey("ADS_MANAGER:DRAFT")
        args = {"launch_key": "launch", "profile_id": "19",
                "business_id": "222222", "ad_account_id": "111111",
                "step": key, "status": "SUBMITTING"}
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=str(invalid)), self.assertRaises(ValueError):
                await self.state.save(**args, result={"evidence": {"value": invalid}})
            self.assertIsNone(await self.state.step("launch", key))

        await self.state.save(**args, result={"evidence": {"verified": False}})
        prior = await self.state.step("launch", key)
        malformed = ActionResult.reconcile(key.value, code="SYNTHETIC_INVALID_READ",
                                           message="Synthetic invalid journal result",
                                           evidence={"value": float("inf")})
        with self.assertRaises(ValueError):
            await self.state.finish_read(prior, malformed)
        self.assertEqual(await self.state.step("launch", key), prior)

    async def test_journal_update_cannot_cross_profile_business_or_account_scope(self):
        key = OperationKey("ADS_MANAGER:DRAFT")
        args = {"launch_key": "launch", "profile_id": "19",
                "business_id": "222222", "ad_account_id": "111111",
                "step": key, "status": "RECONCILE_REQUIRED",
                "result": {"evidence": {"owner": "19"}}}
        await self.state.save(**args)
        original = await self.state.step("launch", key)
        for field, foreign in (("profile_id", "20"), ("business_id", "999999"),
                               ("ad_account_id", "888888")):
            with self.subTest(field=field):
                with self.assertRaises(ProvisioningError) as rejected:
                    await self.state.save(**{**args, field: foreign,
                                            "status": "SUCCESS",
                                            "result": {"verified": True}})
                self.assertEqual(rejected.exception.code,
                                 "PRIVATE_LAUNCH_SAVED_OPERATION_MISMATCH")
                self.assertEqual(await self.state.step("launch", key), original)

        await self.state.save(**{**args, "status": "SUCCESS",
                                 "result": {"evidence": {"owner": "19", "verified": True}}})
        updated = await self.state.step("launch", key)
        self.assertEqual(updated["status"], "SUCCESS")
        self.assertEqual(updated["result"]["evidence"]["owner"], "19")

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

    async def test_concurrent_workers_claim_before_dispatch_and_only_one_post_wins(self):
        class Client:
            def __init__(inner):
                inner.dispatches = 0
                inner.barrier = asyncio.Event()
                inner.arrivals = 0

            async def post(inner, path, params, *, before_submit, files):
                inner.arrivals += 1
                if inner.arrivals == 2:
                    inner.barrier.set()
                await inner.barrier.wait()
                await before_submit()
                inner.dispatches += 1
                return {"id": "333333"}

        client = Client()
        senders = [AdsManagerSubmitter(self.state, launch_key="launch", profile_id="19",
            business_id="222222", account_id="111111", actor_id="123456",
            launch_fingerprint="fixed-inputs", source_digest="source") for _ in range(2)]
        async def submit(sender):
            try:
                return await sender.submit(client, operation="DRAFT", path="/act_111111/addrafts",
                    params={"name": "test"}, receipt=lambda row: {"draft_id": row["id"]})
            except ProvisioningError as exc:
                return exc.code
        results = await asyncio.gather(*(submit(sender) for sender in senders))
        self.assertEqual(client.dispatches, 1)
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual(sum(value == "PRIVATE_LAUNCH_RECONCILE_REQUIRED" for value in results), 1)
        rows = await self.state.all_steps("launch")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "RECONCILE_REQUIRED")

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

    async def test_corrupt_journal_never_restores_a_saved_receipt_or_replays(self):
        class Client:
            count = 0
            async def post(inner, path, params, *, before_submit, files):
                inner.count += 1
                await before_submit()
                return {"id": "333333"}

        client = Client()
        kwargs = {"operation": "DRAFT", "path": "/act_111111/addrafts",
                  "params": {"name": "test"},
                  "receipt": lambda row: {"draft_id": row["id"]}}
        self.assertEqual((await self.sender.submit(client, **kwargs))["draft_id"], "333333")
        with self.state._connect() as con:
            original = con.execute(
                "SELECT result_json FROM private_launch_steps WHERE launch_key=? AND step=?",
                ("launch", "ADS_MANAGER:DRAFT")).fetchone()[0]
        self.assertIn('"status":', original)
        for malformed in ('{"status":"SUCCESS",' + original[1:],
                          '{"untrusted_float":1e999,' + original[1:],
                          '{"untrusted_number":NaN,' + original[1:],
                          '[]', 'null', '1', '"scalar"',
                          '[' * 1100 + '0' + ']' * 1100):
            with self.subTest(malformed=malformed[:35]):
                with self.state._connect() as con:
                    con.execute("UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?",
                                (malformed, "launch", "ADS_MANAGER:DRAFT"))
                self.sender.state = PrivateLaunchStateStore(Path(self.tmp.name) / "state.sqlite")
                saved = await self.sender.state.step("launch", OperationKey("ADS_MANAGER:DRAFT"))
                self.assertEqual(saved["result"], {})
                with self.assertRaises(ProvisioningError):
                    await self.sender.submit(client, **kwargs)
                self.assertEqual(client.count, 1, "A corrupt journal cannot cause another submission")

    async def test_explicit_rejection_survives_restart_without_post_replay(self):
        class Client:
            count = 0
            async def post(inner, path, params, *, before_submit, files):
                await before_submit();inner.count += 1
                raise AdsManagerResponseError({'http_status':400, 'code':100,
                    'error_subcode':1815753, 'message':'synthetic-secret'})
        client = Client()
        for _ in range(2):
            self.sender.state = PrivateLaunchStateStore(Path(self.tmp.name)/'state.sqlite')
            with self.assertRaises(ProvisioningError) as caught:
                await self.sender.submit(client, operation='DRAFT', path='/act_111111/addrafts',
                    params={'name':'test'}, receipt=lambda row:{'draft_id':row['id']})
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_REQUEST_REJECTED')
            self.assertIn('code=100', str(caught.exception))
        self.assertEqual(client.count, 1)
        rows = await self.state.all_steps('launch')
        self.assertEqual(rows[0]['status'], 'RECONCILE_REQUIRED')
        self.assertEqual(rows[0]['submitted'], 1)
        self.assertFalse(rows[0]['result']['verified'])
        self.assertNotIn('synthetic-secret', json.dumps(rows))
        self.assertEqual(rows[0]['result']['evidence']['receipt'], {})

    async def test_rate_limited_claim_survives_restart_without_post_replay(self):
        class Client:
            count = 0
            async def post(inner, path, params, *, before_submit, files):
                await before_submit()
                inner.count += 1
                raise AdsManagerResponseError({"http_status": 429, "code": 4,
                    "message": "synthetic-rate-limit-private-response"})
        client = Client()
        for _ in range(2):
            self.sender.state = PrivateLaunchStateStore(Path(self.tmp.name)/"state.sqlite")
            with self.assertRaises(ProvisioningError) as caught:
                await self.sender.submit(client, operation="DRAFT", path="/act_111111/addrafts",
                    params={"name": "rate-limit-test"},
                    receipt=lambda row: {"draft_id": row["id"]})
            self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_RATE_LIMITED")
            self.assertTrue(caught.exception.retryable)
        self.assertEqual(client.count, 1, "A retryable rate-limit must not replay a claimed CREATE")
        rows = await self.state.all_steps("launch")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["evidence"]["receipt"], {})
        self.assertNotIn("synthetic-rate-limit", json.dumps(rows))

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

    async def test_saved_only_missing_receipt_never_creates_an_intent_or_dispatches(self):
        class NoPost:
            async def post(inner, *args, **kwargs):
                raise AssertionError('Recovery cannot create a new submission')
        with self.assertRaises(ProvisioningError) as caught:
            await self.sender.submit(NoPost(), operation='DRAFT', path='/act_111111/addrafts',
                params={'name':'test'}, receipt=lambda row: row, saved_only=True)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(await self.state.all_steps('launch'), [])


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
                "validation_status": "VALIDATED", "active_errors": [],
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

    async def test_draft_account_scope_rejects_empty_error_envelopes(self):
        good = {'id': '555555', 'account_id': '111111'}
        self.publisher._account(good, '111111')
        for envelope in ({'error': {}}, {'errors': []}, {'error': None}):
            with self.subTest(envelope=envelope):
                with self.assertRaises(ProvisioningError) as caught:
                    self.publisher._account({**good, **envelope}, '111111')
                self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SCOPE_MISMATCH')

    async def test_confirmation_refuses_contradictory_verification_metadata(self):
        await self.sender.submit(self.client, operation='DRAFT',
            path='/act_111111/addrafts', params={'name': 'source-flow'},
            receipt=lambda row: {'draft_id': row['id']})
        for proof in ({'verified': False}, {'source': 'untrusted'}, {'verified': None}):
            with self.subTest(proof=proof):
                with self.assertRaises(ProvisioningError) as caught:
                    await self.publisher._confirm('DRAFT', proof)
                self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_PENDING')
        step = await self.state.step('launch', OperationKey('ADS_MANAGER:DRAFT'))
        self.assertNotEqual(step['status'], 'SUCCESS')
        await self.publisher._confirm('DRAFT', {'verified': True})
        confirmed = await self.state.step('launch', OperationKey('ADS_MANAGER:DRAFT'))
        self.assertEqual(confirmed['status'], 'SUCCESS')
        self.assertEqual(confirmed['result']['evidence']['verification']['source'],
                         'independent_private_ads_http')

    async def test_campaign_fragment_with_foreign_parent_blocks_next_post(self):
        original = self.client.get
        async def read(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'campaign':
                row['parent_ad_object_id'] = '999999'
            return row
        self.client.get = read
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 2)

    async def test_unknown_post_reply_at_every_draft_stage_never_replays_creation(self):
        stages = ("draft", "campaign", "ad_set", "ad")
        for stage in stages:
            with self.subTest(stage=stage):
                state = PrivateLaunchStateStore(Path(self.tmp.name) / ("lost-" + stage + ".sqlite"))
                await state.init()
                sender = AdsManagerSubmitter(state, launch_key="lost-" + stage,
                    profile_id="19", business_id="222222", account_id="111111",
                    actor_id="123456", launch_fingerprint="fixed-inputs", source_digest="source")
                publisher = AdsManagerDraftPublisher(sender)
                client = DraftClient()
                original = client.post
                lost = [False]
                async def post(path, params, *, before_submit, files):
                    result = await original(path, params, before_submit=before_submit, files=files)
                    matches = ((stage == "draft" and path.endswith("/addrafts")) or
                        (path.endswith("/addraft_fragments") and params.get("ad_object_type") == stage))
                    if matches and not lost[0]:
                        lost[0] = True
                        raise TimeoutError("Synthetic response loss after server accepted the mutation")
                    return result
                client.post = post
                for _ in range(2):
                    with self.assertRaises(ProvisioningError) as caught:
                        await publisher.run(client, name="source-flow", levels=self.levels,
                                            verify_objects=self.verify, draft_only=True)
                    self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED")
                    self.assertTrue(lost[0])
                expected_posts = stages.index(stage) + 1
                self.assertEqual(len(client.posts), expected_posts)
                self.assertFalse(any(path.endswith("/publish") for path, _ in client.posts))

    async def test_interrupted_read_at_every_draft_stage_resumes_without_repeating_post(self):
        stages = ("draft", "campaign", "ad_set", "ad")
        for stage in stages:
            with self.subTest(stage=stage):
                state = PrivateLaunchStateStore(Path(self.tmp.name) / ("read-" + stage + ".sqlite"))
                await state.init()
                sender = AdsManagerSubmitter(state, launch_key="read-" + stage,
                    profile_id="19", business_id="222222", account_id="111111",
                    actor_id="123456", launch_fingerprint="fixed-inputs", source_digest="source")
                publisher = AdsManagerDraftPublisher(sender)
                client = DraftClient()
                original = client.get
                interrupted = [False]
                async def get(path, params):
                    is_target = ((stage == "draft" and path == "/addraft_555555") or
                        (stage != "draft" and path.startswith("/addraftfragment_") and
                         client.fragments.get(path.removeprefix("/addraftfragment_"), {}).get("ad_object_type") == stage))
                    if is_target and not interrupted[0]:
                        interrupted[0] = True
                        raise ConnectionError("Synthetic independent read was interrupted")
                    return await original(path, params)
                client.get = get
                with self.assertRaises(ConnectionError):
                    await publisher.run(client, name="source-flow", levels=self.levels,
                                        verify_objects=self.verify, draft_only=True)
                self.assertTrue(interrupted[0])
                result = await publisher.run(client, name="source-flow", levels=self.levels,
                                             verify_objects=self.verify, draft_only=True)
                self.assertTrue(result["draft_verified"])
                self.assertEqual(len(client.posts), 4)
                self.assertEqual(len(client.fragments), 3)
                self.assertFalse(any(path.endswith("/publish") for path, _ in client.posts))

    async def test_duplicate_fragment_receipt_cannot_advance_to_ad(self):
        original_post = self.client.post
        async def post(path, params, **options):
            result = await original_post(path, params, **options)
            if path.endswith("/addraft_fragments") and params.get("ad_object_type") == "ad_set":
                return {"id": "111111"}
            return result
        self.client.post = post
        with self.assertRaises(ProvisioningError) as caught:
            await self.publisher.run(self.client, name="source-flow", levels=self.levels,
                                     verify_objects=self.verify, draft_only=True)
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH")
        self.assertEqual(len(self.client.posts), 3)
        self.assertFalse(any(path.endswith("/publish") for path, _ in self.client.posts))

    async def test_explicit_active_fragment_readback_blocks_paused_draft(self):
        original_read = self.client.get
        async def read(path, params):
            row = await original_read(path, params)
            if row.get("ad_object_type") == "campaign":
                row["status"] = "ACTIVE"
            return row
        self.client.get = read
        with self.assertRaises(ProvisioningError) as caught:
            await self.publisher.run(self.client, name="source-flow", levels=self.levels,
                                     verify_objects=self.verify, draft_only=True)
        self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_DRAFT_DELIVERY_UNSAFE")
        self.assertEqual(len(self.client.posts), 2)
        self.assertFalse(any(path.endswith("/publish") for path, _ in self.client.posts))

    async def test_has_errors_without_error_details_stops_before_ad_or_publication(self):
        original = self.client.get
        async def read(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad_set':
                row['validation_status'] = 'HAS_ERRORS'
                row.pop('active_errors', None)
            return row
        self.client.get = read
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
            self.assertEqual(len(self.client.fragments), 2)
            self.assertEqual(len(self.client.posts), 3)
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))

    async def test_validated_fragment_without_complete_error_readback_never_advances(self):
        original = self.client.get
        shape = ["missing"]
        async def read(path, params):
            row = await original(path, params)
            if row.get("ad_object_type") == "ad_set":
                if shape[0] == "missing":
                    row.pop("active_errors", None)
                elif shape[0] == "null":
                    row["active_errors"] = None
                elif shape[0] == "malformed":
                    row["active_errors"] = {"error_code": 1815857}
            return row
        self.client.get = read
        for value in ("missing", "null", "malformed"):
            shape[0] = value
            with self.subTest(error_shape=value), self.assertRaises(ProvisioningError) as caught:
                await self.publisher.run(self.client, name="source-flow", levels=self.levels,
                                         verify_objects=self.verify, draft_only=True)
            self.assertEqual(caught.exception.code, "PRIVATE_ADS_DRAFT_VALIDATION_PENDING")
            self.assertEqual(len(self.client.posts), 3, "Incomplete proof cannot cause another POST")
            self.assertEqual(set(self.client.fragments), {"111111", "222222"})
        shape[0] = "complete"
        result = await self.publisher.run(self.client, name="source-flow", levels=self.levels,
                                          verify_objects=self.verify, draft_only=True)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(self.client.posts), 4, "Resume may only create the not-yet-submitted Ad")
        self.assertFalse(any(path.endswith("/publish") for path, _ in self.client.posts))

    async def test_profile38_bid_amount_error_is_retained_without_bid_or_objective_rewrite(self):
        self.levels['campaign'].update(objective='OUTCOME_TRAFFIC', daily_budget=500)
        self.levels['ad_set'].update(bid_strategy='LOWEST_COST_WITHOUT_CAP', optimization_goal='LINK_CLICKS')
        before = copy.deepcopy(self.levels)
        original = self.client.get
        async def read(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad_set':
                row.update(validation_status='HAS_ERRORS',
                    active_errors=[{'error_code':1815857, 'field':'bid_amount'}])
            return row
        self.client.get = read
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        self.assertEqual(self.levels, before)
        self.assertEqual(len(self.client.posts), 3)
        adset = self.client.fragments['222222']
        values = {r['field']:r['new_value'] for r in adset['values']}
        self.assertNotIn('bid_amount', values)
        self.assertEqual(values['bid_strategy'], 'LOWEST_COST_WITHOUT_CAP')

    async def test_unsettled_validation_resumes_exact_fragment_reads_without_replay(self):
        original = self.client.get
        status = ['IN_PROGRESS']
        async def read(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad_set':
                row['validation_status'] = status[0]
            return row
        self.client.get = read
        for pending in ('IN_PROGRESS', None, 'UNRECOGNIZED', True):
            status[0] = pending
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_PENDING')
            self.assertTrue(caught.exception.retryable)
            self.assertEqual(len(self.client.posts), 3)
            self.assertEqual(set(self.client.fragments), {'111111','222222'})
        status[0] = 'VALIDATED'
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(len(self.client.fragments), 3)
        await self.launch()
        self.assertEqual(len(self.client.posts), 5)

    async def test_draft_mode_does_not_call_unsettled_fragment_success(self):
        original = self.client.get
        async def read(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad':
                row.pop('validation_status', None)
            return row
        self.client.get = read
        with self.assertRaises(ProvisioningError) as caught:
            await self.publisher.run(self.client, name='source-flow', levels=self.levels,
                verify_objects=self.verify, draft_only=True)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_PENDING')
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))

    async def test_three_fragments_publish_and_verify_then_resume_without_post(self):
        result = await self.launch()
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual([row[1]["ad_object_type"] for row in self.client.posts[1:4]], ["campaign", "ad_set", "ad"])
        await self.launch();self.assertEqual(len(self.client.posts), 5)

    async def test_confirmed_publication_preserved_after_stale_status(self):
        await self.launch()
        key = OperationKey("ADS_MANAGER:PUBLISH")
        initial = await self.state.step("launch", key)
        self.assertEqual(initial["status"], "SUCCESS")
        self.assertTrue(initial["result"]["evidence"]["verification"]["verified"])
        await self.launch()
        repeated = await self.state.step("launch", key)
        self.assertEqual(repeated["status"], "SUCCESS")
        self.client.pending = True
        with self.assertRaises(ProvisioningError) as error:
            await self.launch()
        self.assertEqual(error.exception.code, "PRIVATE_ADS_DRAFT_PUBLISH_STATUS_REGRESSION")
        after = await self.state.step("launch", key)
        self.assertEqual(after["status"], "SUCCESS")
        self.assertEqual(after["result"], repeated["result"])
        self.assertEqual(len(self.client.posts), 5)

    async def test_pending_publication_resumes_status_without_new_post(self):
        self.client.pending = True
        with patch("app.private_ads_publication.asyncio.sleep", new=AsyncMock()):
            with self.assertRaises(ProvisioningError) as error:await self.launch()
        self.assertEqual(error.exception.code, "PRIVATE_ADS_DRAFT_PUBLISH_PENDING")
        self.assertEqual(len(self.client.posts), 5)
        self.client.pending = False
        await self.launch();self.assertEqual(len(self.client.posts), 5)

    async def test_publication_recovery_does_not_require_consumed_draft_rows(self):
        async def unavailable(*args):
            raise ConnectionError('independent object read interrupted')
        with self.assertRaises(ConnectionError):
            await self.launch(verify=unavailable)
        self.assertEqual(len(self.client.posts), 5)
        original = self.client.get
        reads = []
        async def consumed(path, params):
            reads.append(path)
            if path == '/addraft_555555' or path.startswith('/addraftfragment_'):
                raise AssertionError('Published draft rows are no longer an object-proof dependency')
            return await original(path, params)
        self.client.get = consumed
        self.sender.state = PrivateLaunchStateStore(Path(self.tmp.name)/'state.sqlite')
        self.publisher = AdsManagerDraftPublisher(self.sender)
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertIn('/addraft_555555/addraft_publish_statuses', reads)
        self.assertEqual(len(self.client.posts), 5)

    async def test_recovery_with_changed_fragment_values_stops_before_any_read_or_post(self):
        await self.launch()
        self.levels['ad']['name'] = 'another ad'
        original = self.client.get
        reads = []
        async def observed(path, params):
            reads.append(path)
            return await original(path, params)
        self.client.get = observed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(reads, [])
        self.assertEqual(len(self.client.posts), 5)

    async def test_incomplete_saved_confirmation_cannot_fall_back_to_creation(self):
        await self.launch()
        with self.state._connect() as con:
            con.execute("UPDATE private_launch_steps SET status='RECONCILE_REQUIRED' "
                        "WHERE launch_key=? AND step=?", ('launch','ADS_MANAGER:FRAGMENT_AD'))
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 5)

    async def test_recovery_cannot_confirm_foreign_published_objects(self):
        async def unavailable(*args):
            return {'verified':False}
        with self.assertRaises(ProvisioningError):
            await self.launch(verify=unavailable)
        async def foreign(*args):
            row = await self.verify(*args)
            row['entities']['adset_id'] = '999999'
            return row
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch(verify=foreign)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 5)
        row = await self.state.step('launch', OperationKey('ADS_MANAGER:PUBLISH'))
        self.assertNotEqual(row['status'], 'SUCCESS')

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
