from __future__ import annotations

import inspect
import unittest

import main as api
from app.facebook_business_browser import FacebookBusinessBrowser


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

    def test_browser_open_cancellation_releases_owned_resources(self) -> None:
        source=inspect.getsource(FacebookBusinessBrowser.open)
        cancel_index=source.index("except asyncio.CancelledError:")
        generic_index=source.index("except Exception as exc:")
        cleanup_index=source.index("await self.close()", cancel_index)
        self.assertLess(cancel_index, generic_index)
        self.assertLess(cancel_index, cleanup_index)
        self.assertIn("REMASK_BROWSER_OPEN_CANCEL_CLEANUP_V1", source)


if __name__ == "__main__":
    unittest.main()
