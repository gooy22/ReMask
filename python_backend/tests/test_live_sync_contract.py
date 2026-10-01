from __future__ import annotations

import inspect
import unittest
from pathlib import Path

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
    def test_known_page_hints_are_revalidated_before_global_discovery(self):
        signature = inspect.signature(api.profile_live_inventory)
        self.assertIn("page_hints", signature.parameters)

        source = inspect.getsource(api.profile_live_inventory)
        self.assertIn("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1", source)
        self.assertIn("confirmed_business_page_bindings_for_profile", source)
        self.assertIn("revalidate_known_business_pages", source)
        self.assertLess(
            source.index("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1"),
            source.index("REMASK_ADS_MANAGER_PAGES_FIRST_V1"),
        )

        browser_source = inspect.getsource(
            FacebookBusinessBrowser.revalidate_known_business_pages
        )
        self.assertIn("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1", browser_source)
        self.assertIn("SETTINGS_PAGES_URL", browser_source)
        self.assertIn('self.page.on("response"', browser_source)
        self.assertIn('"about:blank"', browser_source)


    def test_page_inventory_does_not_block_live_bm_rk_sync(self):
        # The Railway runtime image copies python_backend to /opt/remask-python
        # but applies the PHP overlay separately during the Docker build. Check
        # this source-level contract when running from the repository checkout;
        # Docker itself separately executes/lints the overlay before this suite.
        root = Path(__file__).resolve().parents[2]
        overlay = root / "railway-workspace-sync-fix-overlay.php"
        if not overlay.exists():
            self.skipTest("workspace sync overlay is not copied into python runtime image")
        source = overlay.read_text(encoding="utf-8")
        self.assertIn("REMASK_STABLE_SYNC_BOUNDARY_V2", source)
        self.assertIn("$syncComplete = $liveReady;", source)
        self.assertIn("$snapshot['sync_partial'] = ($syncComplete && !$pagesReady);", source)
        self.assertIn(
            "Fan Page inventory inconclusive; previous confirmed Pages preserved",
            source,
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
        self.assertIn("REMASK_ADS_MANAGER_PAGES_FIRST_V1", source)
        self.assertIn(
            "discover_promotable_pages_from_ads_manager",
            source,
        )
        self.assertIn("list_pages_via_private_graphql", source)
        self.assertIn("REMASK_PAGE_INVENTORY_WARM_RETRY_V2", source)
        self.assertIn("discover_managed_pages(fast=True)", source)
        self.assertIn("discover_managed_pages(fast=False)", source)
        self.assertIn(
            "facebook_business_browser_relay_warm_retry",
            source,
        )
        self.assertIn("REMASK_SCOPED_HINT_FASTPATH_ONLY_V1", source)
        self.assertIn("REMASK_HISTORICAL_HINTS_ARE_FALLBACK_ONLY_V1", source)
        self.assertIn("REMASK_STALE_HINT_ROWS_EXCLUDED_V1", source)
        self.assertIn("REMASK_FULL_PROFILE_DISCOVERY_BUDGET_V1", source)
        self.assertIn("business_inventory_confirmed_empty", source)
        self.assertIn("REMASK_DISCOVERY_TIMEOUT_HINT_FALLBACK_V1", source)
        self.assertIn("REMASK_RK_TIMEOUT_IS_ROW_FAILURE_V1", source)
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
