import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fb_worker import FacebookBootstrap, FacebookWebSession, RemoteRequestError, WebProfile
from app.ad_account_contracts import AdAccountContractStore
from app.facebook_ad_account_create import AdAccountMutationError, create_ad_account_with_docids
from app.provisioning.ad_account_handler import ad_account_handler, _read_private_ad_account_inventory
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.state import ProvisioningStateStore

BM = "444444444444"
UID = "999999999999"
RK = "777777777777"
NAME = "ReMask RK 1"


def capture():
    return {"doc_id": "1234567890123", "friendly_name": "BizKitSettingsCreateAdAccountMutation",
            "endpoint_url": "https://business.facebook.com/api/graphql/",
            "source": "live_business_settings_capture", "business_id": "111111111111",
            "canary_name": "captured RK", "currency": "USD", "timezone_id": 137,
            "request_envelope": {"__hsi": "OLD SESSION", "fb_dtsg": "OLD TOKEN"},
            "variables": {"input": {"business_id": 111111111111, "actor_id": "333333333333",
                                     "name": "captured RK", "currency": "EUR", "timezone_id": 57,
                                     "client_mutation_id": "old-mutation"}}}


class ContractStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = AdAccountContractStore(Path(self.temp.name) / "rk.json")

    def render(self):
        return self.store.get(business_id=BM, account_name=NAME, currency="USD", timezone_id=137, actor_id=UID)

    def test_capture_becomes_typed_session_free_template_for_another_profile(self):
        original = capture()
        self.assertTrue(self.store.register_capture(original))
        raw = self.store.path.read_text()
        for value in ("111111111111", "333333333333", "captured RK", "OLD SESSION", "OLD TOKEN", "old-mutation"):
            self.assertNotIn(value, raw)
        rendered = self.render()["variables"]["input"]
        self.assertEqual(rendered["business_id"], int(BM))
        self.assertEqual(rendered["actor_id"], UID)
        self.assertEqual(rendered["timezone_id"], 137)
        self.assertIsInstance(rendered["timezone_id"], int)
        self.assertEqual(rendered["name"], NAME)
        self.assertEqual(rendered["currency"], "USD")
        self.assertEqual(original, capture())

    def test_each_render_has_a_new_mutation_identity(self):
        self.store.register_capture(capture())
        self.assertNotEqual(self.render()["variables"]["input"]["client_mutation_id"],
                            self.render()["variables"]["input"]["client_mutation_id"])

    def test_auth_or_opaque_identity_is_never_cached(self):
        for key, value in (("fb_dtsg", "secret"), ("cookie", "secret"),
                           ("unknown_recipient", "12345678999"), ("callback", "https://foreign.test/")):
            row = capture()
            row["variables"]["input"][key] = value
            self.assertFalse(self.store.register_capture(row))
        self.assertIsNone(self.render())

    def test_wrong_operation_endpoint_or_unobserved_capture_is_rejected(self):
        for key, value in (("friendly_name", "DeleteAdAccountMutation"), ("source", "manual"),
                           ("endpoint_url", "https://foreign.test/api/graphql/"),
                           ("endpoint_url", "https://business.facebook.com:invalid/api/graphql/"),
                           ("endpoint_url", "https://business.facebook.com:444/api/graphql/")):
            row = capture(); row[key] = value
            self.assertFalse(self.store.register_capture(row))

    def test_stale_expired_or_corrupt_contract_is_a_cache_miss(self):
        self.store.register_capture(capture())
        self.store.invalidate("not-current")
        self.assertIsNotNone(self.render())
        self.store.invalidate(capture()["doc_id"])
        self.assertIsNone(self.render())
        self.store.register_capture(capture())
        self.store.confirm(capture()["doc_id"])
        self.assertEqual(self.render()["contract_status"], "verified")
        row = json.loads(self.store.path.read_text()); row["observed_at"] = 1
        self.store.path.write_text(json.dumps(row))
        self.assertIsNone(self.render())
        self.store.path.write_text("not json")
        self.assertIsNone(self.render())


class FakeMeta:
    def __init__(self, *, partial=False, timeout=False, trace=None):
        self.created = False
        self.posts = []
        self.partial = partial
        self.timeout = timeout
        self.trace = trace if trace is not None else []

    def post(self, url, **kwargs):
        self.trace.append("POST")
        self.posts.append((url, kwargs))
        self.created = True
        owner = self
        class Response:
            status = 200
            headers = {}
            async def text(self):
                if owner.timeout:
                    raise __import__("asyncio").TimeoutError()
                result = {"data": {"bizkit_create_ad_account": {"ad_account": {"id": RK}}}}
                if owner.partial:
                    result["errors"] = [{"message": "partial execution"}]
                return json.dumps(result)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        return Response()

    async def fetch_text(self, url, **kwargs):
        rows = [{"node": {"__typename": "AdAccount", "id": RK, "name": NAME,
                          "business_id": BM}}] if self.created else []
        body = {"data": {"business": {"__typename": "Business", "id": BM,
                 "ad_accounts": {"edges": rows, "page_info": {"has_next_page": False}}}}}
        return 200, '<script type="application/json">' + json.dumps(body) + '</script> ["DTSGInitialData",[],{"token":"CURRENT"}]', url


class PrivateTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = AdAccountContractStore(Path(self.temp.name) / "rk.json")
        self.cache.register_capture(capture())
        self.trace = []
        self.meta = FakeMeta(trace=self.trace)
        self.web = FacebookWebSession(WebProfile(name="15", cookies={"c_user": UID},
                                      proxy="http://profile-proxy:8080", user_agent="profile-UA"))
        self.web.bootstrap = AsyncMock(return_value=FacebookBootstrap(
            fb_dtsg="CURRENT", actor_id=UID,
            source_url="https://business.facebook.com/latest/settings/ad_accounts/?business_id=" + BM))
        self.web._ensure_session = AsyncMock(return_value=self.meta)
        self.web.fetch_text = self.meta.fetch_text
        self.web.graphql_browser_native = AsyncMock(side_effect=AssertionError("Browser POST forbidden"))
        self.context = SimpleNamespace(profile_id="15", cookies={"c_user": UID})
        self.session = SimpleNamespace(context=self.context, facebook_web=AsyncMock(return_value=self.web))
        self.state = ProvisioningStateStore(str(Path(self.temp.name) / "state.db"))
        await self.state.init()
        await self.state.set_running("job-1", "15", "bundle-1", ProvisioningStep.AD_ACCOUNT)
        self.addCleanup(patch.stopall)
        patch("app.facebook_ad_account_create.record_result").start()
        patch("app.facebook_ad_account_create.upsert_candidate", return_value=None).start()

    def request(self):
        return self.cache.get(business_id=BM, account_name=NAME, currency="USD", timezone_id=137, actor_id=UID)

    async def create(self, before_submit=None):
        return await create_ad_account_with_docids(self.web, business_id=BM, account_name=NAME,
            currency="USD", timezone_id=137, captured_request=self.request(), before_submit=before_submit)

    async def test_direct_http_preserves_current_actor_proxy_and_before_post_boundary(self):
        async def checkpoint(): self.trace.append("INTENT")
        result = await self.create(checkpoint)
        self.assertEqual(result.ad_account_id, "act_" + RK)
        self.assertEqual(self.trace, ["INTENT", "POST"])
        self.assertEqual(len(self.meta.posts), 1)
        args = self.meta.posts[0][1]
        self.assertEqual(args["proxy"], "http://profile-proxy:8080")
        self.assertEqual(args["data"]["fb_dtsg"], "CURRENT")
        self.assertEqual(args["data"]["av"], UID)
        self.assertEqual(json.loads(args["data"]["variables"])["input"]["actor_id"], UID)
        self.web.graphql_browser_native.assert_not_awaited()

    async def test_partial_mutation_data_does_not_become_success(self):
        self.meta.partial = True
        with self.assertRaises(AdAccountMutationError) as caught: await self.create()
        self.assertEqual(caught.exception.code, "CREATE_AD_ACCOUNT_RESULT_UNKNOWN")
        self.assertEqual(len(self.meta.posts), 1)

    async def test_timeout_after_post_never_switches_to_browser_or_reposts(self):
        self.meta.timeout = True
        with self.assertRaises(AdAccountMutationError) as caught: await self.create()
        self.assertEqual(caught.exception.code, "CREATE_AD_ACCOUNT_RESULT_UNKNOWN")
        self.assertEqual(len(self.meta.posts), 1)
        self.web.graphql_browser_native.assert_not_awaited()

    async def test_missing_http_transport_does_not_use_browser_fallback(self):
        self.web.graphql = None
        with self.assertRaises(AdAccountMutationError) as caught: await self.create()
        self.assertEqual(caught.exception.code, "CREATE_AD_ACCOUNT_PRIVATE_TRANSPORT_UNAVAILABLE")
        self.assertEqual(self.meta.posts, [])
        self.web.graphql_browser_native.assert_not_awaited()

    async def test_checkpoint_failure_prevents_post_and_is_proven_pre_submit(self):
        async def fail(): raise OSError("database unavailable")
        with self.assertRaises(AdAccountMutationError) as caught:
            await self.create(fail)
        self.assertEqual(caught.exception.code, "CREATE_AD_ACCOUNT_PRE_SUBMIT_TRANSPORT")
        self.assertEqual(self.meta.posts, [])

    async def test_another_bm_bootstrap_is_replaced_with_the_exact_target_context(self):
        self.web.bootstrap = AsyncMock(return_value=FacebookBootstrap(
            fb_dtsg="OLD", actor_id=UID,
            source_url="https://business.facebook.com/latest/home?business_id=111111111111"))
        await self.create()
        args = self.meta.posts[0][1]
        self.assertEqual(args["data"]["fb_dtsg"], "CURRENT")
        self.assertEqual(args["headers"]["Referer"],
                         "https://business.facebook.com/latest/settings/ad_accounts/?business_id=" + BM)

    async def test_exact_inventory_preflight_and_create_and_commit_need_no_browser(self):
        with patch("app.provisioning.ad_account_handler.AdAccountContractStore", return_value=self.cache), \
             patch("app.provisioning.ad_account_handler._browser_lease", side_effect=AssertionError("No Chromium lease permitted")):
            result = await ad_account_handler(self.session,
                {"business_id": BM, "name": NAME, "currency": "USD", "timezone_id": 137},
                {}, profile_id="15", scope_key="bundle-1", item_id="job-1", provisioning_state=self.state)
        self.assertTrue(result["post_create_verified"])
        self.assertEqual(result["transport"], "facebook_private_http_contract_verified")
        self.assertEqual(len(self.meta.posts), 1)
        checkpoint = await self.state.step("job-1", ProvisioningStep.AD_ACCOUNT)
        self.assertEqual(checkpoint["result"]["phase"], "CREATE_CONFIRMED")
        self.assertEqual(self.request()["contract_status"], "verified")

    async def test_existing_other_named_rk_blocks_create_without_browser(self):
        self.meta.created = True
        async def fetch(url, **kwargs):
            status, body, final = await self.meta.fetch_text(url, **kwargs)
            return status, body.replace(NAME, "Other RK"), final
        self.web.fetch_text = fetch
        with patch("app.provisioning.ad_account_handler._browser_lease", side_effect=AssertionError("No browser")):
            with self.assertRaises(ProvisioningError) as caught:
                await ad_account_handler(self.session,
                    {"business_id": BM, "name": NAME, "currency": "USD", "timezone_id": 137}, {},
                    profile_id="15", scope_key="bundle-1", item_id="job-1", provisioning_state=self.state)
        self.assertEqual(caught.exception.code, "AD_ACCOUNT_ALREADY_EXISTS")
        self.assertEqual(self.meta.posts, [])

    async def test_ambiguous_post_recovers_exact_inventory_without_second_create(self):
        self.meta.timeout = True
        with patch("app.provisioning.ad_account_handler.AdAccountContractStore", return_value=self.cache), \
             patch("app.provisioning.ad_account_handler._browser_lease", side_effect=AssertionError("No browser")):
            result = await ad_account_handler(self.session,
                {"business_id": BM, "name": NAME, "currency": "USD", "timezone_id": 137}, {},
                profile_id="15", scope_key="bundle-1", item_id="job-1", provisioning_state=self.state)
        self.assertEqual(result["ad_account_id"], "act_" + RK)
        self.assertTrue(result["recovered_after_uncertainty"])
        self.assertEqual(len(self.meta.posts), 1)

    async def test_wrong_business_and_paginated_empty_cannot_prove_absence(self):
        async def fetch(url, **kwargs):
            status, body, final = await self.meta.fetch_text(url, **kwargs)
            return status, body.replace('"has_next_page": false', '"has_next_page": true'), final
        self.web.fetch_text = fetch
        result = await _read_private_ad_account_inventory(self.session, business_id=BM, account_name=NAME)
        self.assertIsNone(result)
        async def wrong(url, **kwargs):
            status, body, final = await self.meta.fetch_text(url, **kwargs)
            return status, body.replace(BM, "888888888888"), final
        self.web.fetch_text = wrong
        result = await _read_private_ad_account_inventory(self.session, business_id=BM, account_name=NAME)
        self.assertIsNone(result)
