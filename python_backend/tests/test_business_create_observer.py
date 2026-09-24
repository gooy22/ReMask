import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import FacebookBusinessBrowser


class _BufferedRequest:
    def __init__(self, body: bytes, *, url: str = "https://business.facebook.com/api/graphql/"):
        self.method = "POST"
        self.url = url
        self.post_data_buffer = body
        self.post_data = None


class _TextRequest:
    def __init__(self, body: str, *, url: str = "https://business.facebook.com/api/graphql/"):
        self.method = "POST"
        self.url = url
        self.post_data_buffer = None
        self.post_data = body


class BusinessCreateObserverTests(unittest.TestCase):
    def test_matches_live_business_creation_friendly_name_from_buffer(self):
        request = _BufferedRequest(
            (
                "fb_api_req_friendly_name=useBusinessCreationMutationMutation"
                "&doc_id=28057338880523368"
                "&variables=%7B%22input%22%3A%7B%22business_name%22%3A"
                "%22Lucky%20Joker%20BM%22%2C%22user_email%22%3A"
                "%22owner%40example.com%22%7D%7D"
            ).encode("utf-8")
        )

        self.assertTrue(
            FacebookBusinessBrowser._request_matches_create(
                request,
                "Lucky Joker BM",
            )
        )

    def test_matches_business_name_from_structured_input(self):
        request = _TextRequest(
            "fb_api_req_friendly_name=useBusinessCreationMutationMutation"
            "&variables=%7B%22input%22%3A%7B%22business_name%22%3A"
            "%22Test%20Business%22%7D%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_create(
                request,
                "Test Business",
            )
        )

    def test_rejects_unrelated_graphql_with_same_text(self):
        request = _TextRequest(
            "fb_api_req_friendly_name=BusinessSearchQuery"
            "&variables=%7B%22input%22%3A%7B%22business_name%22%3A"
            "%22Test%20Business%22%7D%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_create(
                request,
                "Test Business",
            )
        )


    def test_matches_structured_page_add_mutation(self):
        request = _TextRequest(
            "fb_api_req_friendly_name=BizKitSettingsAddPageMutation"
            "&variables=%7B%22business_id%22%3A%22555666777888999%22%2C"
            "%22page_id%22%3A%22123456789%22%7D"
        )
        self.assertTrue(
            FacebookBusinessBrowser._request_matches_page_add(
                request,
                business_id="555666777888999",
                page_id="123456789",
            )
        )

    def test_rejects_page_search_query_with_same_ids(self):
        request = _TextRequest(
            "fb_api_req_friendly_name=BusinessPageSearchQuery"
            "&variables=%7B%22business_id%22%3A%22555666777888999%22%2C"
            "%22page_id%22%3A%22123456789%22%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_page_add(
                request,
                business_id="555666777888999",
                page_id="123456789",
            )
        )

    def test_rejects_creation_mutation_for_different_business_name(self):
        request = _TextRequest(
            "fb_api_req_friendly_name=useBusinessCreationMutationMutation"
            "&variables=%7B%22input%22%3A%7B%22business_name%22%3A"
            "%22Another%20Business%22%7D%7D"
        )
        self.assertFalse(
            FacebookBusinessBrowser._request_matches_create(
                request,
                "Test Business",
            )
        )


class BusinessInventoryProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_uses_lightweight_sidebar_probe(self):
        class _Links:
            async def evaluate_all(self, script):
                return []

        class _Keyboard:
            async def press(self, key):
                return None

        class _Page:
            def __init__(self):
                self.script = ""
                self.keyboard = _Keyboard()

            async def evaluate(self, script):
                self.script = script
                return {"clicked": False, "candidates": []}

            async def wait_for_timeout(self, ms):
                return None

            def locator(self, selector):
                return _Links()

            async def content(self):
                return "<html><body>Meta Business Suite</body></html>"

        browser = FacebookBusinessBrowser(
            SimpleNamespace(profile_id="profile-inventory-probe")
        )
        browser.page = _Page()
        browser._goto = AsyncMock(
            return_value="https://business.facebook.com/latest/home"
        )

        result = await browser.snapshot_businesses()

        self.assertEqual(result, {})
        self.assertIn("elementsFromPoint", browser.page.script)
        self.assertNotIn("querySelectorAll('*')", browser.page.script)
        self.assertNotIn('querySelectorAll("*")', browser.page.script)


if __name__ == "__main__":
    unittest.main()
