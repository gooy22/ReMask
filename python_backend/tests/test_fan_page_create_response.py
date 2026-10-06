from __future__ import annotations

import copy
import json
import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from app.facebook_business_browser import FacebookBusinessBrowser, _decode_graphql_text, _request_graphql_meta
from app.facebook_fan_page_create import FanPageCreateCapture, confirmed_created_page


ACTOR = "61594596670000"
PAGE_ID = "2222222222"
NAME = "Fixture Page"


def meta():
    return {"method": "POST", "url": "https://www.facebook.com/api/graphql/",
            "friendly_name": "FixturePageCreateMutation", "body_decodable": True,
            "input": {"actor_id": ACTOR, "name": NAME}, "actor_ids": [ACTOR]}


def payload():
    return {"data": {"page_create": {"page": {"__typename": "Page", "id": PAGE_ID, "name": NAME}}}}


def confirm(request=None, response=None, **kwargs):
    return confirmed_created_page(request or meta(), response or payload(),
        actor_id=kwargs.get("actor_id", ACTOR), page_name=NAME, before_ids=kwargs.get("before_ids", set()))


class FanPageCreateResponseTests(unittest.TestCase):
    def test_exact_page_create_contract_confirms_id(self):
        self.assertEqual(confirm(), {"id": PAGE_ID, "name": NAME})

    def test_wrong_actor_name_host_read_query_and_undecodable_requests_are_rejected(self):
        mutations = [
            {"actor_ids": ["9999999999"]}, {"actor_ids": [] , "input": {"name": NAME}},
            {"input": {"actor_id": ACTOR, "name": "Other"}},
            {"input": {"actor_id": ACTOR, "name": NAME, "page_name": "Other"}},
            {"url": "https://facebook.com.example.org/api/graphql/"},
            {"friendly_name": "PagesAdminQuery"}, {"method": "GET"}, {"body_decodable": False},
        ]
        for change in mutations:
            with self.subTest(change=change):
                request = meta(); request.update(change)
                self.assertIsNone(confirm(request=request))
        self.assertIsNone(confirm(actor_id=""))

    def test_errors_partial_failure_and_non_creation_branches_are_rejected(self):
        responses = [
            {**payload(), "errors": [{"message": "error"}]},
            {"data": {"page_create": {"success": False, **payload()["data"]["page_create"]}}},
            {"data": {"viewer": {"managed_pages": [payload()["data"]["page_create"]["page"]]}}},
            {"data": {"page_create": None}},
            {"data": {"page_create": {"error": {"message": "rejected"},
                                      **payload()["data"]["page_create"]}}},
        ]
        for response in responses:
            with self.subTest(response=response):
                self.assertIsNone(confirm(response=response))

    def test_user_business_wrapper_ids_and_baseline_pages_are_rejected(self):
        for typename in ("User", "Business", "PageCreatePayload", ""):
            response = payload(); response["data"]["page_create"]["page"]["__typename"] = typename
            self.assertIsNone(confirm(response=response))
        self.assertIsNone(confirm(before_ids={PAGE_ID}))
        response = payload(); del response["data"]["page_create"]["page"]["name"]
        self.assertIsNone(confirm(response=response))

    def test_multiple_new_same_name_pages_and_batch_errors_are_rejected(self):
        response = payload()
        other = copy.deepcopy(response["data"]["page_create"]["page"]); other["id"] = "3333333333"
        response["data"]["page_create"]["other"] = other
        self.assertIsNone(confirm(response=response))
        self.assertIsNone(confirm(response=[payload(), {"errors": [{"message": "partial"}]}]))


class FanPageCreateCaptureTests(unittest.IsolatedAsyncioTestCase):
    def capture(self, page=None):
        return FanPageCreateCapture(page or SimpleNamespace(), actor_id=ACTOR, page_name=NAME,
            before_ids=set(), request_meta=_request_graphql_meta, decode=_decode_graphql_text)

    def response(self, body=None):
        request = SimpleNamespace(method="POST", url=meta()["url"], headers={},
            post_data=urlencode({"av": ACTOR, "__user": ACTOR,
                "fb_api_req_friendly_name": meta()["friendly_name"],
                "variables": json.dumps({"input": meta()["input"]})}))
        return SimpleNamespace(request=request, status=200, text=AsyncMock(return_value=json.dumps(body or payload())))

    async def test_unarmed_responses_are_ignored_and_listener_removed(self):
        listeners = {}
        page = SimpleNamespace(on=lambda event, handler: listeners.update({event: handler}),
                               remove_listener=lambda event, handler: listeners.pop(event))
        capture = self.capture(page)
        async with capture:
            response = self.response()
            capture.on_response(response)
            self.assertIsNone(capture.page_result)
            response.text.assert_not_awaited()
            capture.armed = True
            capture.on_response(response)
            await capture.drain()
            self.assertEqual(capture.page_result["id"], PAGE_ID)
        self.assertEqual(listeners, {})

    async def test_conflicting_responses_and_http_failure_never_confirm(self):
        capture = self.capture(); capture.armed = True
        async with capture:
            one = self.response()
            response = payload(); response["data"]["page_create"]["page"]["id"] = "3333333333"
            capture.on_response(one); capture.on_response(self.response(response))
            await capture.drain()
            self.assertIsNone(capture.page_result)
        capture = self.capture(); response = self.response(); response.status = 500
        await capture.inspect(response)
        self.assertIsNone(capture.page_result)
        response.text.assert_not_awaited()

    async def test_response_body_exception_diagnostics_never_include_auth(self):
        capture = self.capture(); response = self.response()
        response.text.side_effect = RuntimeError("xs=secret-token")
        await capture.inspect(response)
        self.assertIsNone(capture.page_result)
        self.assertNotIn("secret-token", str(capture.diagnostics))

    async def test_real_chromium_create_response_confirms_before_any_pages_inventory(self):
        executable = next((path for name in ("google-chrome", "chromium", "chromium-browser")
                           if (path := shutil.which(name))), None)
        if not executable:
            self.skipTest("No local Chromium installed")
        from playwright.async_api import async_playwright
        async with async_playwright() as playwright:
            chromium = await playwright.chromium.launch(
                executable_path=executable, headless=True, args=["--no-sandbox"])
            try:
                page = await chromium.new_page()
                body = urlencode({"av": ACTOR, "__user": ACTOR,
                    "fb_api_req_friendly_name": "FixturePageCreateMutation",
                    "variables": json.dumps({"input": {"actor_id": ACTOR, "name": NAME}})})
                html = '<button>Create Page</button><script>window.submits=0;document.querySelector("button").onclick=()=>{window.submits++;fetch("/api/graphql/",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:'+json.dumps(body)+'});};</script>'

                async def fulfill(route):
                    if route.request.url.endswith('/api/graphql/'):
                        await route.fulfill(content_type='application/json', body=json.dumps(payload()))
                    else:
                        await route.fulfill(content_type='text/html', body=html)

                await page.route('**/*', fulfill)
                await page.goto('https://www.facebook.com/pages/creation/')
                context = SimpleNamespace(profile_id="fixture", cookies={"c_user": ACTOR}, pages=[])
                browser = FacebookBusinessBrowser(context); browser.page = page
                browser._goto = AsyncMock()
                browser._fill_fan_page_name = AsyncMock(return_value=True)
                browser._fill_fan_page_category = AsyncMock(return_value=True)
                browser._assert_authenticated = AsyncMock()
                browser.discover_managed_pages = AsyncMock(side_effect=AssertionError("inventory must not run"))
                submit = AsyncMock()
                result = await browser.create_fan_page(page_name=NAME, category="Digital creator",
                    before_pages=[], before_submit=submit)
                self.assertEqual(result['page_id'], PAGE_ID)
                self.assertEqual(result['transport'], 'facebook_pages_ui_create_response')
                self.assertEqual(await page.evaluate('window.submits'), 1)
                browser.discover_managed_pages.assert_not_awaited()
                submit.assert_awaited_once()
                self.assertEqual(context.pages[0]['ownership_source'], 'scoped_page_create_response')
            finally:
                await chromium.close()
