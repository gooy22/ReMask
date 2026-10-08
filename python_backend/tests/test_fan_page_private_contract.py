import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fb_worker import FacebookBootstrap, FacebookWebSession, RemoteRequestError, WebProfile
from app.fan_page_contracts import FanPageContractStore
from app.facebook_business_browser import BrowserBusinessError, FacebookBusinessBrowser
from app.graphql_mutation_capture import GraphqlMutationCapture
from app.private_fan_page_create import create_fan_page_private
from app.provisioning.fan_pages_handler import _create_page_via_private_contract

UID = "999999999999"
PAGE = "777777777777"
NAME = "PrgssTeam"
CATEGORY = "Digital creator"


def capture():
    return {"doc_id": "1234567890123", "friendly_name": "PageCreateMutation",
            "endpoint_url": "https://www.facebook.com/api/graphql/",
            "source": "live_page_create_capture",
            "request_envelope": {"fb_dtsg": "OLD SECRET", "__hsi": "OLD SESSION"},
            "variables": {"input": {"name": NAME, "actor_id": "333333333333",
                                     "category_ids": ["123456789999"], "bio": "",
                                     "client_mutation_id": "old-mutation"}}}


class FanPagePrivateContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FanPageContractStore(Path(self.temp.name) / "fp.json")
        self.assertTrue(self.store.register_capture(capture(), name=NAME, category=CATEGORY,
                                                   bio="", actor_id="333333333333"))

    def rendered(self, **kwargs):
        return self.store.get(name=NAME, category=CATEGORY, bio="", actor_id=UID, **kwargs)

    def test_cache_rebinds_actor_name_bio_with_no_old_session_values(self):
        raw = self.store.path.read_text()
        for value in (UID, "333333333333", NAME, "OLD SECRET", "OLD SESSION", "old-mutation"):
            self.assertNotIn(value, raw)
        first = self.store.get(name="Another Page", category=CATEGORY, bio="new bio", actor_id=UID)
        data = first["variables"]["input"]
        self.assertEqual(data["name"], "Another Page")
        self.assertEqual(data["actor_id"], UID)
        self.assertEqual(data["bio"], "new bio")
        self.assertEqual(data["category_ids"], ["123456789999"])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertNotEqual(data["client_mutation_id"], self.rendered()["variables"]["input"]["client_mutation_id"])

    def test_category_change_staleness_expiry_and_unobserved_schema_are_cache_misses(self):
        self.assertIsNone(self.store.get(name=NAME, category="Other category", bio="", actor_id=UID))
        self.store.set_status(category=CATEGORY, doc_id=capture()["doc_id"], status="stale")
        self.assertIsNone(self.rendered())
        row = capture(); row["source"] = "guessed"
        self.assertFalse(self.store.register_capture(row, name=NAME, category=CATEGORY, bio="", actor_id="333333333333"))

    def test_wrong_actor_endpoint_or_opaque_identity_is_rejected(self):
        for key, value in (("fb_dtsg", "secret"), ("recipient_id", "55555555555"), ("callback_url", "https://foreign.test/")):
            row = capture(); row["variables"]["input"][key] = value
            self.assertFalse(self.store.register_capture(row, name=NAME, category=CATEGORY, bio="", actor_id="333333333333"))
        row = capture(); row["endpoint_url"] = "https://www.facebook.com:444/api/graphql/"
        self.assertFalse(self.store.register_capture(row, name=NAME, category=CATEGORY, bio="", actor_id="333333333333"))
        self.assertFalse(self.store.register_capture(capture(), name=NAME, category=CATEGORY, bio="", actor_id=UID))

    def web(self, *, timeout=False, partial=False, trace=None):
        calls = []
        trace = trace if trace is not None else []
        class Response:
            status = 200
            headers = {}
            async def text(self):
                if timeout:
                    raise __import__("asyncio").TimeoutError()
                data = {"data": {"page_create": {"page": {"__typename": "Page", "id": PAGE, "name": NAME}}}}
                if partial:
                    data["errors"] = [{"message": "partial execution"}]
                return json.dumps(data)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return False
        def post(url, **kwargs):
            trace.append("POST"); calls.append((url, kwargs)); return Response()
        web = FacebookWebSession(WebProfile("fixture", {"c_user": UID}, "http://127.0.0.1:8080", "FixtureUA"))
        web._ensure_session = AsyncMock(return_value=SimpleNamespace(post=post))
        web.bootstrap = AsyncMock(return_value=FacebookBootstrap(fb_dtsg="FRESH TOKEN", actor_id=UID,
            lsd="FRESH LSD", source_url="https://www.facebook.com/pages/creation/"))
        return web, calls

    async def execute(self, web, *, verify=None, before_submit=None):
        return await create_fan_page_private(web, self.rendered(), actor_id=UID,
            page_name=NAME, category=CATEGORY, before_ids=set(),
            before_submit=before_submit or AsyncMock(), verify=verify or AsyncMock(return_value=[{"id": PAGE, "name": NAME}]),
            store=self.store)

    async def test_real_http_posts_once_after_intent_and_verifies_independent_inventory(self):
        trace = []; web, calls = self.web(trace=trace)
        async def intent(patch): trace.append("INTENT")
        async def verify(): trace.append("VERIFY"); return [{"id": PAGE, "name": NAME}]
        result = await self.execute(web, before_submit=intent, verify=verify)
        self.assertEqual(trace, ["INTENT", "POST", "VERIFY"])
        self.assertEqual(result["page_id"], PAGE)
        self.assertEqual(len(calls), 1)
        request = calls[0][1]
        self.assertEqual(request["proxy"], "http://127.0.0.1:8080")
        self.assertEqual(request["data"]["fb_dtsg"], "FRESH TOKEN")
        self.assertEqual(request["data"]["av"], UID)
        self.assertEqual(json.loads(request["data"]["variables"])["input"]["actor_id"], UID)
        self.assertEqual(self.rendered()["contract_status"], "verified")

    async def test_mutation_id_alone_never_commits_when_inventory_is_inconclusive(self):
        web, calls = self.web()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.execute(web, verify=AsyncMock(return_value=[]))
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_RESULT_UNKNOWN")
        self.assertEqual(caught.exception.diagnostic["response_page_id"], PAGE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.rendered()["contract_status"], "captured")

    async def test_partial_response_reconciles_but_does_not_certify_contract(self):
        web, calls = self.web(partial=True)
        result = await self.execute(web)
        self.assertEqual(result["page_id"], PAGE)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.rendered()["contract_status"], "captured")

    async def test_explicit_stale_schema_invalidates_cache_without_blind_resubmission(self):
        web = SimpleNamespace(graphql=AsyncMock(return_value={"errors": [{
            "code": 1357054, "message": "Persisted query not found"}]}))
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.execute(web, verify=AsyncMock(return_value=[]))
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_RESULT_UNKNOWN")
        web.graphql.assert_awaited_once()
        self.assertIsNone(self.rendered())

    async def test_lost_response_preserves_uncertainty_without_a_second_post(self):
        web, calls = self.web(timeout=True)
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.execute(web)
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_RESULT_UNKNOWN")
        self.assertEqual(len(calls), 1)

    async def test_failed_durable_intent_forbids_http_submission(self):
        web, calls = self.web()
        with self.assertRaises(BrowserBusinessError) as caught:
            await self.execute(web, before_submit=AsyncMock(side_effect=OSError("db unavailable")))
        self.assertEqual(caught.exception.code, "FAN_PAGE_CREATE_PRE_SUBMIT_TRANSPORT")
        self.assertTrue(caught.exception.diagnostic["safe_before_submit"])
        self.assertEqual(calls, [])

    async def test_cache_hit_never_opens_browser_for_create(self):
        web, calls = self.web()
        session = SimpleNamespace(context=SimpleNamespace(profile_id="fixture", cookies={"c_user": UID}),
                                  facebook_web=AsyncMock(return_value=web))
        with patch("app.provisioning.fan_pages_handler.FanPageContractStore", return_value=self.store), \
             patch("app.provisioning.fan_pages_handler._browser_lease", side_effect=AssertionError("Chromium CREATE forbidden")), \
             patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(return_value=[{"id": PAGE, "name": NAME}])):
            result = await _create_page_via_private_contract(session, page_name=NAME, category=CATEGORY,
                bio="", before_pages=[], before_submit=AsyncMock(), params={})
        self.assertEqual(result["page_id"], PAGE)
        self.assertEqual(len(calls), 1)

    async def test_policy_gate_stops_cached_http_contract_before_post(self):
        web, calls = self.web()
        session = SimpleNamespace(context=SimpleNamespace(profile_id="fixture", cookies={"c_user": UID}),
                                  facebook_web=AsyncMock(return_value=web))
        with patch("app.provisioning.fan_pages_handler.FanPageContractStore", return_value=self.store):
            with self.assertRaises(BrowserBusinessError) as caught:
                await _create_page_via_private_contract(session, page_name=NAME, category=CATEGORY,
                    bio="", before_pages=[], before_submit=AsyncMock(), params={"require_policy_consent": True})
        self.assertEqual(caught.exception.code, "PAGE_POLICIES_CONFIRMATION_REQUIRED")
        self.assertEqual(calls, [])

    async def test_browser_captures_and_aborts_create_without_durable_submit_intent(self):
        handlers = []; route = SimpleNamespace(abort=AsyncMock(), continue_=AsyncMock())
        row = capture()
        request = SimpleNamespace(method="POST", url=row["endpoint_url"], headers={},
            post_data=__import__("urllib.parse", fromlist=["urlencode"]).urlencode({
                "doc_id": row["doc_id"], "fb_api_req_friendly_name": row["friendly_name"],
                "variables": json.dumps(row["variables"])}))
        async def install(pattern, handler): handlers.append(handler)
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id="fixture", cookies={"c_user": "333333333333"}))
        browser.page = SimpleNamespace(route=install, unroute=AsyncMock(), wait_for_timeout=AsyncMock())
        browser._goto = AsyncMock(); browser._fill_fan_page_name = AsyncMock(return_value=True)
        browser._fill_fan_page_category = AsyncMock(return_value=True)
        async def click(*args, before_click=None, **kwargs):
            await before_click(); await handlers[0](route, request)
            return {"found": True, "clicked": True}
        browser._click_named_single_attempt = AsyncMock(side_effect=click)
        intent = AsyncMock()
        observed = await browser.create_fan_page(page_name=NAME, category=CATEGORY,
                                                before_pages=[], before_submit=intent, capture_only=True)
        self.assertEqual(observed["doc_id"], row["doc_id"])
        route.abort.assert_awaited_once(); route.continue_.assert_not_awaited()
        intent.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
