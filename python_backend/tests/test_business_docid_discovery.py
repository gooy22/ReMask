import unittest

from app.facebook_business_create import (
    _candidate_is_stale_or_schema_mismatch,
    _extract_create_business_id,
    _extract_page_backed_create_docid,
)


class BusinessDocIdDiscoveryTests(unittest.TestCase):
    def test_extracts_escaped_page_backed_create_mutation(self) -> None:
        source = (
            r'{\"fb_api_req_friendly_name\":\"BusinessManagerCreateMutation\",'
            r'\"variables\":{\"input\":{\"name\":\"X\",'
            r'\"primary_page_id\":\"123456789\"}},'
            r'\"doc_id\":\"9988776655443322\"}'
        )
        doc_id, friendly = _extract_page_backed_create_docid(source)
        self.assertEqual(doc_id, "9988776655443322")
        self.assertIn("Business", friendly)

    def test_does_not_select_business_update_mutation(self) -> None:
        source = (
            '{"fb_api_req_friendly_name":"BizKitSettingsUpdateBusinessBasicInfoMutation",'
            '"variables":{"input":{"business_id":"111","primary_page_id":"222"}},'
            '"doc_id":"7893672220672612"}'
        )
        doc_id, _ = _extract_page_backed_create_docid(source)
        self.assertEqual(doc_id, "")


    def test_extracts_current_business_create_response_shape(self) -> None:
        business_id, path = _extract_create_business_id(
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

    def test_extracts_business_manager_create_response_shape(self) -> None:
        business_id, path = _extract_create_business_id(
            {
                "data": {
                    "business_manager_create": {
                        "business": {
                            "id": "555666777888999"
                        }
                    }
                }
            }
        )
        self.assertEqual(business_id, "555666777888999")
        self.assertEqual(path, "data.business_manager_create.business.id")

    def test_1357054_not_critical_is_stale_signal(self) -> None:
        payload = {
            "errors": [
                {
                    "code": 1357054,
                    "isNotCritical": 1,
                    "errorSummary": "request could not be processed",
                }
            ]
        }
        self.assertTrue(_candidate_is_stale_or_schema_mismatch(payload, ""))


if __name__ == "__main__":
    unittest.main()
