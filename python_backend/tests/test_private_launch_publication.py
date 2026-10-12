"""Prepared offline cases for the user's deferred validation pass.

Request IDs, schemas and hashes below are synthetic fixtures, not Meta evidence.
"""
from pathlib import Path
import tempfile
import unittest

from fb_worker import RemoteRequestError
from app.private_launch import PrivateLaunchStateStore
from app.private_launch_extensions import LaunchOperationRunner, OperationKey, extension_contract
from app.provisioning.models import ProvisioningError

IDS = {"campaign_id":"12345001", "adset_id":"12345002", "creative_id":"12345003", "ad_id":"12345004"}
RELATIONS = {"CAMPAIGN":(), "ADSET":("campaign_id",), "CREATIVE":("page_id",),
             "AD":("campaign_id","adset_id","creative_id")}


def family_contract():
    reads = {}
    for index, (step, relations) in enumerate(RELATIONS.items()):
        reads[step] = {"operation_kind":"query", "doc_id":str(99001+index),
            "friendly_name":"Fixture"+step+"Query", "variables":{"id":"{{entity_id}}"},
            "id_path":"data.object.id", "account_path":"data.object.account_id",
            "relations":{name:"data.object."+name for name in relations}}
        if step != "CREATIVE":
            reads[step]["status_path"] = "data.object.status"
    return extension_contract("PUBLISH_FAMILY", {"evidence":{
        "artifact_sha256":"a"*64, "sender_sha256":"b"*64, "read_sha256":"c"*64,
        "poll_sha256":"d"*64, "observed_at":"2026-10-12"}, "contract":{
        "doc_id":"50001", "friendly_name":"FixtureFamilyMutation", "variables":{"input":{
            "account":"{{ad_account_id}}", "page":"{{page_id}}", "settings":"{{payload}}"}},
        "output_paths":{}, "verification":reads, "async_publication":{
            "operation_id_path":"data.publish.operation_id", "pending_statuses":["IN_PROGRESS"],
            "failed_statuses":["FAILED"], "success_status":"SUCCEEDED", "max_polls":1,
            "status_query":{"operation_kind":"query", "doc_id":"90001", "friendly_name":"FixturePublishStatusQuery",
                "variables":{"operation":"{{operation_id}}", "account":"{{ad_account_id}}"},
                "operation_id_path":"data.publish.operation_id", "account_path":"data.publish.account_id",
                "status_path":"data.publish.status", "output_paths":{name:"data.publish."+name for name in IDS}}}}})


class PublicationWeb:
    def __init__(self):
        self.posts, self.polls, self.reads = 0, 0, 0
        self.status, self.account = "IN_PROGRESS", "123456789"
        self.lost_start, self.unavailable, self.partial_start = False, False, False

    async def graphql(self, doc_id, variables, **options):
        if doc_id == "50001":
            await options["before_submit"]()
            self.posts += 1
            if self.lost_start:
                raise RemoteRequestError("Fixture response lost",request_may_have_been_sent=True)
            response = {"data":{"publish":{"operation_id":"fixture-operation"}}}
            if self.partial_start:
                response["errors"] = [{"message":"Fixture partial start"}]
            return response
        if doc_id == "90001":
            self.polls += 1
            if self.unavailable:
                raise RemoteRequestError("Fixture status unavailable")
            return {"data":{"publish":{"operation_id":"fixture-operation", "account_id":self.account,
                                         "status":self.status, **IDS}}}
        self.reads += 1
        return {"data":{"object":{"id":variables["id"], "account_id":"123456789", "status":"PAUSED",
                                     "page_id":"555555555", **IDS}}}


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = PrivateLaunchStateStore(Path(self.temp.name)/"state.sqlite")
        await self.state.init()
        self.runner = LaunchOperationRunner(self.state)
        self.web = PublicationWeb()
        self.values = {"profile_id":"fixture", "business_id":"987654321", "ad_account_id":"123456789",
                       "page_id":"555555555", "payload":{step.lower():{} for step in RELATIONS}}

    async def publish(self):
        return await self.runner.run(web=self.web,contract=family_contract(),values=self.values,
                                     launch_key="fixture-launch",launch_fingerprint="fixture-config")

    async def test_pending_resume_keeps_one_post_and_verifies_all_four_objects(self):
        with self.assertRaises(ProvisioningError) as caught:
            await self.publish()
        self.assertTrue(caught.exception.retryable)
        self.assertEqual(self.web.reads,0)
        self.web.status = "SUCCEEDED"
        self.assertEqual(await self.publish(),IDS)
        self.assertEqual((self.web.posts,self.web.polls,self.web.reads),(1,2,4))
        saved = await self.state.step("fixture-launch",OperationKey("PUBLISH_FAMILY"))
        self.assertEqual(saved["entity_id"],IDS["ad_id"])
        self.assertEqual(saved["status"],"SUCCESS")

    async def test_unavailable_poll_retains_operation_for_read_only_retry(self):
        self.web.unavailable = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.publish()
        self.assertTrue(caught.exception.retryable)
        self.web.unavailable = False
        self.web.status = "SUCCEEDED"
        self.assertEqual(await self.publish(),IDS)
        self.assertEqual(self.web.posts,1)

    async def test_foreign_account_status_never_promotes_objects(self):
        self.web.account = "999999999"
        self.web.status = "SUCCEEDED"
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.publish()
            self.assertFalse(caught.exception.retryable)
        self.assertEqual((self.web.posts,self.web.polls,self.web.reads),(1,1,0))

    async def test_lost_start_response_never_replays_publication(self):
        self.web.lost_start = True
        for _ in range(2):
            with self.assertRaises(ProvisioningError):
                await self.publish()
        self.assertEqual((self.web.posts,self.web.polls,self.web.reads),(1,0,0))

    async def test_partial_start_can_only_be_confirmed_by_status_and_object_reads(self):
        self.web.partial_start = True
        self.web.status = "SUCCEEDED"
        self.assertEqual(await self.publish(),IDS)
        self.assertEqual((self.web.posts,self.web.polls,self.web.reads),(1,1,4))
