from __future__ import annotations

import inspect
import unittest

import main as api


class LiveSyncContractTests(unittest.TestCase):
    def test_every_target_business_requires_live_confirmation(self) -> None:
        self.assertTrue(
            api._live_inventory_targets_ready(
                {"10001": "A", "10002": "B"},
                {"10001", "10002"},
            )
        )
        self.assertFalse(
            api._live_inventory_targets_ready(
                {"10001": "A", "10002": "B"},
                {"10001"},
            )
        )

    def test_empty_target_set_is_not_complete(self) -> None:
        self.assertFalse(api._live_inventory_targets_ready({}, set()))

    def test_live_endpoint_never_reinjects_durable_fallback_rows(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)
        self.assertNotIn("worker_confirmed_fallback", source)
        self.assertIn("REMASK_LIVE_INVENTORY_TOTAL_BUDGET_V1", source)
        self.assertIn("REMASK_LIVE_PAYLOAD_EXCLUDES_DURABLE_FALLBACK_V1", source)
        self.assertIn("invalidating browser session", source)


if __name__ == "__main__":
    unittest.main()
