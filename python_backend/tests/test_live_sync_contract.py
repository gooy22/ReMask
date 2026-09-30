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

    def test_empty_target_set_requires_explicit_empty_inventory_proof(self) -> None:
        self.assertFalse(api._live_inventory_targets_ready({}, set()))
        self.assertTrue(
            api._live_inventory_targets_ready(
                {},
                set(),
                confirmed_empty=True,
            )
        )

    def test_business_inventory_empty_detection_uses_live_selector_query(self) -> None:
        self.assertTrue(
            api._business_inventory_confirmed_empty({
                "stage": "complete",
                "queries": [{
                    "friendly_name": "NorthStarBusinessUnifiedScopingSelectorQuery",
                    "rows": 0,
                }],
            })
        )
        self.assertFalse(
            api._business_inventory_confirmed_empty({
                "stage": "complete",
                "queries": [{
                    "friendly_name": "CometNotificationsQuery",
                    "rows": 0,
                }],
            })
        )

    def test_live_endpoint_never_reinjects_durable_fallback_rows(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)
        self.assertNotIn("worker_confirmed_fallback", source)
        self.assertIn("REMASK_LIVE_INVENTORY_TOTAL_BUDGET_V1", source)
        self.assertIn("REMASK_SYNC_RESOLVER_BOUNDED_V1", source)
        self.assertIn("browser_open_timeout=budget(24.0)", source)
        self.assertIn("REMASK_LIVE_PAYLOAD_EXCLUDES_DURABLE_FALLBACK_V1", source)
        self.assertIn("REMASK_SYNC_PRIVATE_LIST_PAGES_FIRST_V1", source)
        self.assertIn("list_pages_via_private_graphql", source)
        self.assertIn("discover_managed_pages(fast=True)", source)
        self.assertIn("REMASK_SCOPED_HINT_FASTPATH_ONLY_V1", source)
        self.assertIn("REMASK_HISTORICAL_HINTS_ARE_FALLBACK_ONLY_V1", source)
        self.assertIn("REMASK_STALE_HINT_ROWS_EXCLUDED_V1", source)
        self.assertIn("REMASK_FULL_PROFILE_DISCOVERY_BUDGET_V1", source)
        self.assertIn("business_inventory_confirmed_empty", source)
        self.assertIn("invalidating browser session", source)

    def test_browser_open_cancellation_releases_owned_resources(self) -> None:
        source=inspect.getsource(FacebookBusinessBrowser.open)
        cancel_index=source.index("except asyncio.CancelledError:")
        generic_index=source.index("except Exception as exc:")
        cleanup_index=source.index("await self.close()", cancel_index)
        self.assertLess(cancel_index, generic_index)
        self.assertLess(cancel_index, cleanup_index)
        self.assertIn("REMASK_BROWSER_OPEN_CANCEL_CLEANUP_V1", source)
        self.assertIn("REMASK_BROWSER_OPEN_EARLY_CANCEL_CLEANUP_V1", source)
        self.assertGreaterEqual(source.count("except asyncio.CancelledError:"), 3)
        self.assertIn("await self.close()", source)


if __name__ == "__main__":
    unittest.main()
