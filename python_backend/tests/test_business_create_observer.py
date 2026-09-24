import unittest

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


if __name__ == "__main__":
    unittest.main()
