"""Offline regression scenarios, prepared for the deferred validation pass.

All doc_ids and source hashes in this file are synthetic test fixtures.
"""
import hashlib
from pathlib import Path
import tempfile
import unittest

from fb_worker import RemoteRequestError

from app.private_launch import PrivateLaunchStateStore, _effective_launch_payload
from app.private_launch_extensions import LaunchOperationRunner, OperationKey, extension_contract
from app.private_launch_media import LocalMedia, creative_with_media
from app.provisioning.models import ProvisioningError


def image_contract():
    return extension_contract("IMAGE_UPLOAD", {"evidence": {
        "artifact_sha256":"a"*64,"sender_sha256":"b"*64,"read_sha256":"c"*64,"observed_at":"2026-10-11"},
        "contract":{"doc_id":"50123","friendly_name":"FixtureImageMutation","file_field":"file",
            "variables":{"input":{"account":"{{ad_account_id}}","digest":"{{media.sha256}}",
                                  "nonce":"{{client_mutation_id}}"}},
            "output_paths":{"entity_id":"data.upload.hash"},
            "verification":{"operation_kind":"query","doc_id":"99123","friendly_name":"FixtureMediaQuery",
                "variables":{"id":"{{entity_id}}"},"id_path":"data.media.hash","account_path":"data.media.account_id"}}})


class UploadWeb:
    def __init__(self):
        self.posts = 0
        self.reads = 0
        self.read_error = False
        self.lost_response = False
        self.account = "123456789"

    async def graphql(self, doc_id, variables, **options):
        if doc_id == "50123":
            await options["before_submit"]()
            self.posts += 1
            if self.lost_response:
                raise RemoteRequestError("Fixture lost response",request_may_have_been_sent=True)
            return {"data":{"upload":{"hash":"fixture_image_hash"}}}
        self.reads += 1
        if self.read_error:
            raise RemoteRequestError("Fixture READ unavailable")
        return {"data":{"media":{"hash":"fixture_image_hash","account_id":self.account}}}


class AdvertisingOperationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.state = PrivateLaunchStateStore(root/"state.sqlite")
        await self.state.init()
        self.runner = LaunchOperationRunner(self.state)
        self.web = UploadWeb()
        path = root/"fixture.png"
        content = b"offline test bytes"
        path.write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        self.media = LocalMedia("m-"+sha[:24],sha,len(content),"image","image/png",path)
        self.values = {"profile_id":"fixture","business_id":"987654321","ad_account_id":"123456789","page_id":"555555555"}

    async def upload(self):
        return await self.runner.run(web=self.web,contract=image_contract(),values=self.values,
                                     launch_key="fixture-launch",launch_fingerprint="fixture-config",media=self.media)

    async def test_interrupted_media_read_retries_only_read_with_saved_hash(self):
        self.web.read_error = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.upload()
        self.assertTrue(caught.exception.retryable)
        self.web.read_error = False
        self.assertEqual(await self.upload(),{"entity_id":"fixture_image_hash"})
        self.assertEqual((self.web.posts,self.web.reads),(1,2))
        saved = await self.state.step("fixture-launch",OperationKey("IMAGE_UPLOAD:"+self.media.sha256))
        self.assertEqual(saved["status"],"SUCCESS")

    async def test_lost_upload_response_never_replays_post(self):
        self.web.lost_response = True
        for _ in range(2):
            with self.assertRaises(ProvisioningError):
                await self.upload()
        self.assertEqual(self.web.posts,1)
        self.assertEqual(self.web.reads,0)

    async def test_foreign_account_proof_cannot_promote_media(self):
        self.web.account = "999999999"
        with self.assertRaises(ProvisioningError) as caught:
            await self.upload()
        self.assertFalse(caught.exception.retryable)
        with self.assertRaises(ProvisioningError):
            await self.upload()
        self.assertEqual(self.web.posts,1)

    async def test_changed_file_stops_before_durable_intent_or_upload(self):
        self.media.path.write_bytes(b'changed bytes')
        with self.assertRaises(ProvisioningError) as caught:
            await self.upload()
        self.assertEqual(caught.exception.code,'PRIVATE_LAUNCH_MEDIA_INVALID')
        self.assertEqual(self.web.posts,0)
        self.assertIsNone(await self.state.step('fixture-launch',OperationKey('IMAGE_UPLOAD:'+self.media.sha256)))


class AdvertisingInputTests(unittest.TestCase):
    def test_desired_delivery_does_not_make_create_active(self):
        raw = {section:{"name":"fixture","status":"ACTIVE"} for section in ("campaign","adset","ad")}
        raw["creative"] = {"name":"fixture"}
        result = _effective_launch_payload(raw)
        self.assertTrue(all(result[section]["status"] == "PAUSED" for section in ("campaign","adset","ad")))
        self.assertEqual(result["delivery"]["ad"],"ACTIVE")
        self.assertEqual(raw["ad"]["status"],"ACTIVE")

    def test_carousel_cards_keep_order_and_receive_only_meta_references(self):
        creative = {"format":"CAROUSEL","carousel_media_library_ids":["local-a","local-b"],
                    "carousel_cards":[{"headline":"first"},{"headline":"second"}]}
        result = creative_with_media(creative,[{"image_hash":"hash_a"},{"video_id":"video_b"}])
        self.assertEqual(result["carousel_cards"],[{"headline":"first","image_hash":"hash_a"},
                                                  {"headline":"second","video_id":"video_b"}])
        self.assertNotIn("carousel_media_library_ids",result)
