import asyncio
import json
import unittest
from types import SimpleNamespace
from urllib.parse import urlencode

from app.graphql_mutation_capture import (
    GraphqlMutationCapture,
    graphql_request_meta,
    safe_graphql_request_summary,
    safe_request_envelope,
)


class FakeRequest:
    def __init__(
        self,
        *,
        method="POST",
        url="https://business.facebook.com/api/graphql/",
        data="",
        headers=None,
    ):
        self.method = method
        self.url = url
        self.post_data = data
        self.post_data_buffer = data.encode()
        self.headers = headers or {}


class FakeRoute:
    def __init__(self):
        self.aborted = 0
        self.continued = 0

    async def abort(self):
        self.aborted += 1

    async def continue_(self):
        self.continued += 1


class FakePage:
    def __init__(self):
        self.handler = None
        self.pattern = None
        self.unrouted = False

    async def route(self, pattern, handler):
        self.pattern = pattern
        self.handler = handler

    async def unroute(self, pattern, handler):
        self.unrouted = True
        self.handler = None


def request_for(
    *,
    doc_id="123456789",
    friendly="FixtureCreateMutation",
    variables=None,
    extra=None,
):
    body = {
        "doc_id": doc_id,
        "fb_api_req_friendly_name": friendly,
        "variables": json.dumps(
            variables or {"input": {"name": "Fixture"}},
            separators=(",", ":"),
        ),
        "__req": "a",
        "dpr": "1",
        "fb_dtsg": "must-not-escape",
        "jazoest": "must-not-escape",
        "__user": "61594993341059",
    }
    body.update(extra or {})
    return FakeRequest(data=urlencode(body))


class GraphqlRequestParserTests(unittest.TestCase):
    def test_parser_supports_effective_post_and_safe_summary(self):
        query = urlencode({
            "method": "post",
            "doc_id": "777777",
            "variables": json.dumps({"input": {"account_id": "123"}}),
        })
        request = FakeRequest(
            method="GET",
            url="https://www.facebook.com/api/graphql/?" + query,
            headers={"x-fb-friendly-name": "HeaderMutation"},
        )
        meta = graphql_request_meta(request)
        self.assertEqual(meta["method"], "POST")
        self.assertEqual(meta["doc_id"], "777777")
        self.assertEqual(meta["friendly_name"], "HeaderMutation")
        self.assertEqual(meta["variables"]["input"]["account_id"], "123")

        summary = safe_graphql_request_summary(meta=meta)
        self.assertEqual(summary["doc_id"], "777777")
        self.assertNotIn("decoded_raw", summary)
        self.assertEqual(summary["input_keys"], ["account_id"])

    def test_replay_envelope_drops_all_auth_material(self):
        request = request_for(extra={
            "__hsi": "safe-hsi",
            "access_token": "must-not-escape",
            "cookie": "must-not-escape",
        })
        envelope = safe_request_envelope(request)
        self.assertEqual(envelope["__req"], "a")
        self.assertEqual(envelope["dpr"], "1")
        self.assertEqual(envelope["__hsi"], "safe-hsi")
        for forbidden in (
            "fb_dtsg", "jazoest", "__user", "access_token", "cookie",
        ):
            self.assertNotIn(forbidden, envelope)


class GraphqlMutationCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_definitive_match_is_aborted_and_returned(self):
        page = FakePage()
        request = request_for(
            doc_id="888888",
            friendly="CampaignCreateMutation",
            variables={"input": {"account_id": "123", "name": "Campaign"}},
        )
        route = FakeRoute()

        async with GraphqlMutationCapture(
            page,
            matcher=lambda req, meta: (
                meta["doc_id"] == "888888"
                and "Campaign" in meta["friendly_name"]
            ),
        ) as capture:
            await page.handler(route, request)
            row = await capture.wait(0.1)

        self.assertEqual(route.aborted, 1)
        self.assertEqual(route.continued, 0)
        self.assertEqual(row["doc_id"], "888888")
        self.assertEqual(row["friendly_name"], "CampaignCreateMutation")
        self.assertEqual(row["request_envelope"]["__req"], "a")
        self.assertNotIn("fb_dtsg", row["request_envelope"])
        self.assertTrue(page.unrouted)

    async def test_armed_plausible_unknown_is_aborted_but_not_replayable(self):
        page = FakePage()
        request = request_for(
            doc_id="999999",
            friendly="RenamedCreateMutation",
        )
        route = FakeRoute()

        async with GraphqlMutationCapture(
            page,
            matcher=lambda req, meta: False,
            plausible_matcher=lambda req, meta: "Create" in meta["friendly_name"],
        ) as capture:
            capture.arm()
            await page.handler(route, request)
            self.assertTrue(capture.blocked_unclassified)
            self.assertFalse(capture.done)
            self.assertEqual(route.aborted, 1)
            self.assertEqual(route.continued, 0)
            self.assertTrue(
                capture.candidates[-1]["plausible_unclassified_mutation"]
            )

    async def test_non_matching_request_continues(self):
        page = FakePage()
        route = FakeRoute()
        request = request_for(friendly="InventoryQuery")

        async with GraphqlMutationCapture(
            page,
            matcher=lambda req, meta: False,
            plausible_matcher=lambda req, meta: False,
        ) as capture:
            await page.handler(route, request)
            self.assertFalse(capture.done)

        self.assertEqual(route.aborted, 0)
        self.assertEqual(route.continued, 1)


if __name__ == "__main__":
    unittest.main()
