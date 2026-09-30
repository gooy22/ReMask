import unittest

from app.live_inventory_contract import (
    BUSINESS_SCOPED,
    PROFILE_FULL,
    compute_inventory_readiness,
    page_auto_selectable,
    page_business_ownership,
)


class LiveInventoryReadinessTests(unittest.TestCase):
    def test_profile_with_confirmed_zero_businesses_is_complete(self):
        result = compute_inventory_readiness(
            scope=PROFILE_FULL,
            session_ready=True,
            business_inventory_ready=True,
            business_ids=[],
            rk_ready_by_business={},
            pages_inventory_ready=True,
        )
        self.assertTrue(result["businesses_confirmed_empty"])
        self.assertTrue(result["rk_inventory_ready"])
        self.assertTrue(result["sync_complete"])

    def test_profile_is_not_complete_when_only_one_of_three_bm_is_ready(self):
        result = compute_inventory_readiness(
            scope=PROFILE_FULL,
            session_ready=True,
            business_inventory_ready=True,
            business_ids=["11", "22", "33"],
            rk_ready_by_business={
                "11": True,
                "22": False,
                "33": False,
            },
            pages_inventory_ready=True,
        )
        self.assertFalse(result["rk_inventory_ready"])
        self.assertFalse(result["sync_complete"])
        self.assertEqual(
            result["rk_unconfirmed_business_ids"],
            ["22", "33"],
        )

    def test_business_scoped_requires_requested_business(self):
        result = compute_inventory_readiness(
            scope=BUSINESS_SCOPED,
            session_ready=True,
            business_inventory_ready=True,
            business_ids=["11"],
            requested_business_ids=["11"],
            rk_ready_by_business={"11": True},
            pages_inventory_ready=True,
        )
        self.assertTrue(result["sync_complete"])

    def test_pages_are_required_for_scoped_sync_too(self):
        result = compute_inventory_readiness(
            scope=BUSINESS_SCOPED,
            session_ready=True,
            business_inventory_ready=True,
            business_ids=["11"],
            requested_business_ids=["11"],
            rk_ready_by_business={"11": True},
            pages_inventory_ready=False,
        )
        self.assertFalse(result["sync_complete"])


class PageOwnershipContractTests(unittest.TestCase):
    def test_missing_business_id_is_unknown_not_free(self):
        page = {"id": "123", "name": "Page"}
        self.assertEqual(page_business_ownership(page), "unknown")
        self.assertFalse(page_auto_selectable(page))

    def test_confirmed_unowned_page_can_be_auto_selected(self):
        page = {
            "id": "123",
            "name": "Page",
            "business_ownership": "unowned_confirmed",
        }
        self.assertTrue(page_auto_selectable(page))

    def test_restricted_unowned_page_is_not_auto_selected(self):
        page = {
            "id": "123",
            "name": "Page",
            "business_ownership": "unowned_confirmed",
            "advertising_restriction_info": {
                "is_restricted": True,
            },
        }
        self.assertFalse(page_auto_selectable(page))

    def test_business_owned_page_is_not_auto_selected(self):
        page = {
            "id": "123",
            "name": "Page",
            "business_id": "999",
        }
        self.assertEqual(
            page_business_ownership(page),
            "owned_by_business",
        )
        self.assertFalse(page_auto_selectable(page))


if __name__ == "__main__":
    unittest.main()
