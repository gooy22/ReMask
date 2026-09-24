import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import (
    FacebookBusinessBrowser,
    _decode_graphql_text,
    _extract_created_business_id,
    _graphql_error_details,
    _meta_error_retryable,
    _walk_business_ids,
)


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


class ExactCreateResponseTests(unittest.TestCase):
    def test_extracts_business_create_nested_id(self):
        business_id, path = _extract_created_business_id(
            {
                "data": {
                    "business_create": {
                        "business": {
                            "id": "555666777888999"
                        }
                    }
                }
            }
        )
        self.assertEqual(business_id, "555666777888999")
        self.assertEqual(path, "data.business_create.business.id")

    def test_extracts_legacy_bizkit_create_id(self):
        business_id, path = _extract_created_business_id(
            {
                "data": {
                    "bizkit_create_business": {
                        "id": "555666777888999"
                    }
                }
            }
        )
        self.assertEqual(business_id, "555666777888999")
        self.assertEqual(path, "data.bizkit_create_business.id")

    def test_ignores_unrelated_business_id(self):
        business_id, path = _extract_created_business_id(
            {
                "data": {
                    "viewer": {
                        "business": {
                            "id": "111111111111111"
                        }
                    }
                }
            }
        )
        self.assertEqual(business_id, "")
        self.assertEqual(path, "")

    def test_rejects_ambiguous_create_ids(self):
        business_id, path = _extract_created_business_id(
            [
                {
                    "data": {
                        "business_create": {
                            "business": {"id": "111111111111111"}
                        }
                    }
                },
                {
                    "data": {
                        "bizkit_create_business": {
                            "id": "222222222222222"
                        }
                    }
                },
            ]
        )
        self.assertEqual(business_id, "")
        self.assertEqual(path, "")


class RelayResponseDecodeTests(unittest.TestCase):
    def test_decodes_xssi_prefixed_single_json(self):
        payload = _decode_graphql_text(
            'for (;;);{"data":{"business_create":{"business":{"id":"555666777888999"}}}}'
        )
        ids = _walk_business_ids(payload)
        self.assertTrue(any(value == "555666777888999" for value, _ in ids))

    def test_decodes_line_delimited_relay_payloads(self):
        payload = _decode_graphql_text(
            '{"extensions":{"is_final":false}}\n'
            '{"data":{"business_create":{"business":{"id":"555666777888999"}}}}'
        )
        self.assertIsInstance(payload, list)
        ids = _walk_business_ids(payload)
        self.assertTrue(any(value == "555666777888999" for value, _ in ids))

    def test_line_delimited_errors_remain_visible(self):
        payload = _decode_graphql_text(
            '{"extensions":{"is_final":false}}\n'
            '{"errors":[{"message":"Server error. Please try again.",'
            '"extensions":{"code":"INTERNAL"}}]}'
        )
        errors = _graphql_error_details(payload)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["code"], "INTERNAL")


class MetaCreateErrorTests(unittest.TestCase):
    def test_extracts_graphql_error_code_and_message(self):
        errors = _graphql_error_details(
            {
                "errors": [
                    {
                        "message": "You have reached the maximum number of business portfolios.",
                        "extensions": {
                            "code": "BUSINESS_LIMIT",
                            "error_subcode": "12345",
                        },
                    }
                ]
            }
        )
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["code"], "BUSINESS_LIMIT")
        self.assertEqual(errors[0]["subcode"], "12345")
        self.assertIn("maximum", errors[0]["message"].lower())
        self.assertFalse(_meta_error_retryable(errors))

    def test_extracts_summary_only_meta_error(self):
        errors = _graphql_error_details(
            {
                "errors": [
                    {
                        "summary": "You have reached the maximum number of business portfolios.",
                        "code": 200,
                    }
                ]
            }
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("maximum", errors[0]["message"].lower())
        self.assertEqual(errors[0]["code"], "200")
        self.assertFalse(_meta_error_retryable(errors))

    def test_transient_meta_error_is_retryable(self):
        errors = _graphql_error_details(
            {
                "errors": [
                    {
                        "message": "Server error. Please try again.",
                        "extensions": {"code": "INTERNAL"},
                    }
                ]
            }
        )
        self.assertTrue(_meta_error_retryable(errors))

    def test_temporary_feature_block_is_not_auto_retried(self):
        errors = _graphql_error_details(
            {
                "error": {
                    "code": "FEATURE_BLOCK",
                    "message": "You are temporarily blocked from using this feature.",
                }
            }
        )
        self.assertFalse(_meta_error_retryable(errors))


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
