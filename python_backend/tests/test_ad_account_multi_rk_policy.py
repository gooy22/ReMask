import unittest

from app.provisioning.ad_account_handler import (
    _browser_inventory_confirms_target_absent,
)


class MultiRkInventoryPolicyTests(unittest.TestCase):
    def test_other_rk_do_not_block_when_exact_target_name_is_absent(self):
        evidence = {
            "evidence": {
                "exact_business_context": True,
                "inventory_observed": True,
                "inventory_ids": ["111111111111111"],
                "exact_name_ids": [],
            }
        }
        self.assertTrue(_browser_inventory_confirms_target_absent(evidence))

    def test_exact_target_name_never_counts_as_absent(self):
        evidence = {
            "evidence": {
                "exact_business_context": True,
                "inventory_observed": True,
                "inventory_ids": ["111111111111111", "222222222222222"],
                "exact_name_ids": ["222222222222222"],
            }
        }
        self.assertFalse(_browser_inventory_confirms_target_absent(evidence))

    def test_unscoped_or_unobserved_inventory_cannot_unlock_create(self):
        self.assertFalse(_browser_inventory_confirms_target_absent({
            "evidence": {
                "exact_business_context": False,
                "inventory_observed": True,
                "exact_name_ids": [],
            }
        }))
        self.assertFalse(_browser_inventory_confirms_target_absent({
            "evidence": {
                "exact_business_context": True,
                "inventory_observed": False,
                "exact_name_ids": [],
            }
        }))


if __name__ == "__main__":
    unittest.main()
