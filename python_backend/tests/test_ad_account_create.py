from __future__ import annotations

import unittest

from app.facebook_ad_account_create import (
    _extract_ad_account_id,
    _normalize_ad_account_id,
)
from app.provisioning.ad_account_handler import (
    _known_pre_submit_navigation_failure,
)


class AdAccountCreateResponseTests(unittest.TestCase):
    def test_numeric_id_is_canonicalized(self) -> None:
        self.assertEqual(
            _normalize_ad_account_id("123456789"),
            "act_123456789",
        )

    def test_act_id_is_preserved(self) -> None:
        self.assertEqual(
            _normalize_ad_account_id("act_123456789"),
            "act_123456789",
        )

    def test_current_nested_shape(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "ad_account_create": {
                        "ad_account": {"id": "act_123456789"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_123456789")
        self.assertEqual(path, "data.ad_account_create.ad_account.id")

    def test_business_nested_shape(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "business_ad_account_create": {
                        "ad_account": {"id": "987654321"}
                    }
                }
            }
        )
        self.assertEqual(account_id, "act_987654321")
        self.assertEqual(path, "data.business_ad_account_create.ad_account.id")

    def test_ambiguous_ids_are_rejected(self) -> None:
        account_id, path = _extract_ad_account_id(
            {
                "data": {
                    "ad_account_create": {
                        "id": "111111",
                        "ad_account": {"id": "222222"},
                    }
                }
            }
        )
        self.assertEqual(account_id, "")
        self.assertEqual(path, "")

    def test_page_goto_timeout_is_known_pre_submit_failure(self) -> None:
        self.assertTrue(
            _known_pre_submit_navigation_failure(
                {
                    "last_error": (
                        "Facebook browser GraphQL transport failure: "
                        "TimeoutError: Page.goto: Timeout 15000ms exceeded. "
                        "Call log: navigating to "
                        "https://business.facebook.com/latest/home"
                    )
                }
            )
        )

    def test_post_submit_timeout_is_not_migrated(self) -> None:
        self.assertFalse(
            _known_pre_submit_navigation_failure(
                {
                    "last_error": (
                        "Facebook browser GraphQL transport failure: "
                        "TimeoutError during page.evaluate"
                    )
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
