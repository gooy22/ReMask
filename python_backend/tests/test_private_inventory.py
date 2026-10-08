import json
import unittest

from app.private_inventory import private_inventory_snapshot, inventory_diagnostic_summary


class FakeWeb:
    def __init__(self, payload, status=200, final_url=None):
        self.payload = payload
        self.status = status
        self.final_url = final_url
        self.calls = []

    async def fetch_text(self, url, max_bytes=0):
        self.calls.append(url)
        body = '<script type="application/json">' + json.dumps(self.payload) + '</script>'
        return self.status, body, self.final_url or url


def inventory(accounts, *, business="111111", paginated=False):
    return {"data": {"business": {
        "__typename": "Business", "id": business, "name": "BM One",
        "ad_accounts": {"edges": [{"node": account} for account in accounts],
                        "page_info": {"has_next_page": paginated}},
    }}}


def account(account_id="222222", business="111111"):
    row = {"__typename": "AdAccount", "id": account_id, "name": "RK One"}
    if business:
        row["business_id"] = business
    return row


class PrivateInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, web, expected=None, known=True):
        return await private_inventory_snapshot(
            web,
            known_accounts_by_business={"111111": set(expected or ["222222"])} if known else {},
            known_business_ids={"111111"} if known else set(),
            personal_scope_id="999999",
        )

    async def test_known_pair_is_confirmed_without_browser_and_stops_probing(self):
        web = FakeWeb(inventory([account()]))
        result = await self.snapshot(web)
        self.assertTrue(result["ready"])
        self.assertEqual(result["confirmed_business_ids"], ["111111"])
        self.assertEqual(result["businesses"][0]["ad_accounts"][0]["id"], "222222")
        self.assertEqual(result["source"], "private_http_relay_inventory")
        self.assertEqual(len(web.calls), 3)  # Two discovery reads, one exact inventory read.

    async def test_authenticated_act_url_is_not_inventory_evidence(self):
        web = FakeWeb({"CurrentUserInitialData": {"ACCOUNT_ID": "999999"},
                       "DTSGInitialData": {"token": "fixture"}})
        result = await self.snapshot(web)
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["confirmed_expected_account_ids"], [])
        self.assertTrue(any("act=222222" in url for url in web.calls))

    async def test_http_error_cannot_confirm_inventory(self):
        result = await self.snapshot(FakeWeb(inventory([account()]), status=500))
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_redirected_login_cannot_confirm_inventory(self):
        result = await self.snapshot(FakeWeb(inventory([account()]), final_url="https://www.facebook.com/login/"))
        self.assertFalse(result["ready"])

    async def test_all_expected_pairs_must_be_found(self):
        result = await self.snapshot(FakeWeb(inventory([account()])), expected=["222222", "333333"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["confirmed_expected_account_ids"], ["222222"])

    async def test_other_business_account_is_never_rebound_to_requested_business(self):
        result = await self.snapshot(FakeWeb(inventory([account(business="444444")])) )
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_unscoped_account_is_not_bound_from_url(self):
        result = await self.snapshot(FakeWeb({"data": {"ad_account": account(business="")}}))
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_exact_response_business_scopes_account_without_owner_field(self):
        result = await self.snapshot(FakeWeb(inventory([account(business="")])))
        self.assertTrue(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"][0]["business_id"], "111111")

    async def test_newly_discovered_business_can_be_confirmed_without_cached_targets(self):
        result = await self.snapshot(FakeWeb(inventory([account()])), known=False)
        self.assertTrue(result["ready"])

    async def test_empty_scoped_complete_inventory_without_expected_accounts_is_confirmed(self):
        result = await self.snapshot(FakeWeb(inventory([])), known=False)
        self.assertTrue(result["ready"])
        self.assertTrue(result["businesses"][0]["confirmed_empty"])

    async def test_empty_inventory_does_not_erase_expected_pair(self):
        result = await self.snapshot(FakeWeb(inventory([])))
        self.assertFalse(result["ready"])
        self.assertFalse(result["businesses"][0]["confirmed_empty"])

    async def test_paginated_empty_fragment_is_inconclusive(self):
        result = await self.snapshot(FakeWeb(inventory([], paginated=True)), known=False)
        self.assertFalse(result["ready"])
        self.assertFalse(result["businesses"][0]["confirmed_empty"])

    async def test_complete_owned_empty_does_not_hide_paginated_client_accounts(self):
        payload = inventory([])
        business = payload["data"]["business"]
        business["owned_ad_accounts"] = business.pop("ad_accounts")
        business["client_ad_accounts"] = {"edges": [], "page_info": {"has_next_page": True}}
        result = await self.snapshot(FakeWeb(payload), known=False)
        self.assertFalse(result["businesses"][0]["inventory_complete"])
        self.assertFalse(result["businesses"][0]["confirmed_empty"])

    async def test_request_variables_are_not_response_evidence(self):
        result = await self.snapshot(FakeWeb({"variables": inventory([account()])}))
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_packed_relay_response_is_decoded_without_losing_exact_scope(self):
        web = FakeWeb({"require": [["RelayPrefetchedStreamCache", "next", [],
            ["query", {"__bbox": {"result": json.dumps(inventory([account()]))}}]]]})
        result = await self.snapshot(web)
        self.assertTrue(result["ready"])
        self.assertEqual(result["businesses"][0]["confirmed_expected_account_ids"], ["222222"])

    async def test_packed_request_or_foreign_response_never_proves_exact_inventory(self):
        for payload in ({"request": json.dumps(inventory([account()]))},
                {"response": json.dumps(inventory([account(business="444444")], business="444444"))}):
            result = await self.snapshot(FakeWeb(payload))
            self.assertFalse(result["ready"])
            self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_plain_json_script_and_json_response_are_data_but_js_is_not(self):
        raw = json.dumps(inventory([account()]))
        for body, ready in (("<script>" + raw + "</script>", True),
                ("for (;;);" + raw, True),
                ("<script>var response=" + raw + ";</script>", False)):
            web = FakeWeb({})
            async def fetch(url, max_bytes=0):
                return 200, body, url
            web.fetch_text = fetch
            self.assertEqual((await self.snapshot(web))["ready"], ready)

    async def test_completeness_requires_all_same_business_response_fragments(self):
        owned = inventory([])
        owned["data"]["business"]["owned_ad_accounts"] = owned["data"]["business"].pop("ad_accounts")
        client = inventory([], paginated=True)
        client["data"]["business"]["client_ad_accounts"] = client["data"]["business"].pop("ad_accounts")
        result = await self.snapshot(FakeWeb([owned, client]), known=False)
        self.assertFalse(result["ready"])
        self.assertFalse(result["businesses"][0]["confirmed_empty"])

    async def test_diagnostic_shapes_do_not_expose_payload_values_or_url_tokens(self):
        payload = inventory([account()])
        payload.update(cookies={"xs": "SECRET"}, fb_dtsg="SECRET")
        web = FakeWeb(payload, final_url="https://business.facebook.com/latest/settings/ad_accounts/?fb_dtsg=SECRET")
        result = await self.snapshot(web)
        summary = inventory_diagnostic_summary(result)
        self.assertTrue(summary[-1]["payload_count"])
        self.assertTrue(summary[-1]["inventory_shape"])
        self.assertNotIn("SECRET", repr(summary))
        self.assertNotIn("fb_dtsg", repr(summary))

    async def test_foreign_redirect_cannot_supply_inventory_even_with_matching_response_ids(self):
        result = await self.snapshot(FakeWeb(inventory([account()]), final_url="https://other.example/"))
        self.assertFalse(result["ready"])

    async def test_json_looking_name_is_user_text_not_a_response_carrier(self):
        payload = {"data": {"viewer": {"name": json.dumps(inventory([account()]))}}}
        result = await self.snapshot(FakeWeb(payload))
        self.assertFalse(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts"], [])

    async def test_nested_foreign_business_does_not_scope_its_accounts_or_empty_collection(self):
        for accounts in ([], [account(business="")]):
            payload = {"data": {"business": {"__typename": "Business", "id": "111111", "name": "BM One",
                "related_business": inventory(accounts, business="444444")["data"]["business"]}}}
            result = await self.snapshot(FakeWeb(payload), known=False)
            target = next(row for row in result["businesses"] if row["id"] == "111111")
            self.assertFalse(target["ad_accounts_ready"])
            self.assertFalse(target["confirmed_empty"])
            self.assertEqual(target["ad_accounts"], [])


if __name__ == "__main__":
    unittest.main()

