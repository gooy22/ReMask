import unittest
from unittest.mock import patch

from app.private_inventory import private_inventory_snapshot


class FakeWeb:
    async def fetch_text(self, url, max_bytes=0):
        body='<script type="application/json">{"data":{"ok":true}}</script>'
        return 200, body, url


class PrivateInventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_known_pair_is_confirmed_without_browser(self):
        def business_rows(payload):
            return [{"id":"111111","name":"BM One"}]

        def ad_rows(payload, request_scoped=False):
            return [{"id":"222222","name":"RK One","business_id":"111111"}]

        with patch("app.private_inventory._extract_business_inventory_rows", side_effect=business_rows), \
             patch("app.private_inventory._extract_inventory_ad_account_rows", side_effect=ad_rows), \
             patch("app.private_inventory._has_ad_account_inventory_container", return_value=True):
            result=await private_inventory_snapshot(
                FakeWeb(),
                known_accounts_by_business={"111111":{"222222"}},
                known_business_ids={"111111"},
                personal_scope_id="999999",
            )

        self.assertTrue(result["ready"])
        self.assertEqual(result["confirmed_business_ids"], ["111111"])
        self.assertEqual(result["businesses"][0]["ad_accounts"][0]["id"], "222222")
        self.assertEqual(result["source"], "private_http_relay_inventory")

    async def test_missing_expected_pair_is_inconclusive(self):
        with patch("app.private_inventory._extract_business_inventory_rows", return_value=[]), \
             patch("app.private_inventory._extract_inventory_ad_account_rows", return_value=[]), \
             patch("app.private_inventory._has_ad_account_inventory_container", return_value=False):
            result=await private_inventory_snapshot(
                FakeWeb(),
                known_accounts_by_business={"111111":{"222222"}},
                known_business_ids={"111111"},
            )

        self.assertFalse(result["ready"])
        self.assertEqual(result["inconclusive_business_ids"], ["111111"])


if __name__ == "__main__":
    unittest.main()
