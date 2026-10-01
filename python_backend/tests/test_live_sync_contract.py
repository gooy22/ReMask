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
            source.index("revalidate_known_business_pages"),
            source.index("discover_promotable_pages_from_ads_manager"),
        )

        browser_source = inspect.getsource(
            FacebookBusinessBrowser.revalidate_known_business_pages
        )
        self.assertIn("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1", browser_source)
        self.assertIn("REMASK_KNOWN_PAGE_RESPONSE_SCOPE_V2", browser_source)
        self.assertIn("REMASK_KNOWN_PAGE_VISIBLE_DOM_PROOF_V2", browser_source)
        self.assertIn("page_inventory_operation", browser_source)
        self.assertNotIn("content = await self.page.content()", browser_source)
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
        self.assertIn("REMASK_USABLE_PAGE_STATE_V3", source)
        self.assertIn("$syncComplete = $liveReady;", source)
        self.assertIn("$snapshot['pages_live_verified'] = $pagesLiveVerified;", source)
        self.assertIn("REMASK_PAGE_VERIFICATION_TIMESTAMP_V3", source)
        self.assertIn("'pages_live_verified_at' => $pagesLiveVerified", source)
        self.assertIn("$pagesReady = true;", source)
        self.assertIn("REMASK_PAGE_LIVE_VERIFICATION_WARNING_V1", source)
        self.assertIn(
            "BM/РК проверены. Fan Page показаны из сохранённого состояния; текущая проверка FP в Meta не завершена",
            source,
        )
        self.assertIn(
            "Live Fan Page inventory was not confirmed in this sync",
            source,
        )
        self.assertNotIn(
            "$syncWarnings[] = 'Page inventory inconclusive; previous confirmed Pages preserved';",
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

    def test_live_inventory_startup_canary_is_opt_in_and_read_only(self) -> None:
        source=inspect.getsource(api.run_live_inventory_readonly_canary)
        self.assertIn("REMASK_LIVE_INVENTORY_READONLY_CANARY_V2", source)
        self.assertIn("REMASK_LIVE_INVENTORY_CANARY_PROFILE", source)
        self.assertIn("confirmed_ad_account_bindings_for_profile", source)
        self.assertIn("workspace-provisioning-bindings.json", source)
        self.assertIn("REMASK_CANARY_LAST_LIVE_HINT_V1", source)
        self.assertIn("workspace-live-meta-snapshots.json", source)
        self.assertIn("business_ids=business_id or None", source)
        self.assertIn("ad_account_hints=", source)
        self.assertIn("profile_live_inventory(", source)
        self.assertNotIn("create_job(", source)
        self.assertNotIn("enqueue_job(", source)
        self.assertNotIn("pool.provisioning.run(", source)
        self.assertNotIn("create_business", source.lower())
        self.assertNotIn("create_ad_account", source.lower())


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

    def test_live_endpoint_keeps_page_state_separate_from_bm_rk_truth(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)

        # BM/RK still require live confirmation; durable rows are not injected
        # into the live Business/RK payload as fake current inventory.
        self.assertNotIn("worker_confirmed_fallback", source)
        self.assertIn("REMASK_LIVE_PAYLOAD_EXCLUDES_DURABLE_FALLBACK_V1", source)
        self.assertIn("REMASK_LIVE_TARGET_SET_REQUIRED_V1", source)

        # Page inventory intentionally returns to the Sep-27 model:
        # successful FAN_PAGES worker state is usable profile Page state,
        # while fresh Meta enumeration is a separate enrichment dimension.
        self.assertIn("REMASK_STABLE_PAGE_STATE_V3", source)
        self.assertIn("latest_profile_fan_pages", source)
        self.assertIn("latest_profile_fan_page_batch", source)
        self.assertIn("REMASK_CURRENT_FAN_PAGE_BASELINE_V2", source)
        self.assertIn("REMASK_PROFILE_CONTEXT_PAGE_BASELINE_V1", source)
        self.assertIn("profile_context_pages=[", source)
        self.assertIn("REMASK_PROFILE_CONTEXT_PAGE_HINTS_V1", source)
        self.assertIn("context_baseline_pages=normalize_page_rows(", source)
        self.assertIn(
            "durable_pages=merge_page_rows(",
            source,
        )
        self.assertIn("'profile_context_page_hint',", source)
        self.assertIn(
            "str(row.get('source') or '') not in {",
            source,
        )
        self.assertIn("REMASK_LATEST_PAGE_BINDING_PER_BUSINESS_V1", source)
        self.assertIn("current_binding_pages=[", source)
        self.assertIn("workspace_last_live_page_hint", source)
        self.assertIn("*(current_fan_page_batch or [])", source)
        self.assertIn("*current_binding_pages", source)
        self.assertIn("REMASK_PAGE_HINT_PRECEDENCE_V1", source)
        self.assertIn("workspace_page_hint_businesses:set[str]=set()", source)
        self.assertIn("hinted_business_id in known_pages_by_business", source)
        self.assertIn(
            "if hinted_business_id in seen_page_businesses:",
            source,
        )
        self.assertNotIn(
            "known_business_ids == {hinted_business_id}",
            source,
        )
        self.assertIn("pages_live_verified=False", source)
        self.assertIn("'pages_live_verified':pages_live_verified", source)

        self.assertIn("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1", source)
        self.assertIn("REMASK_ADS_MANAGER_PAGES_FIRST_V1", source)
        self.assertIn("discover_promotable_pages_from_ads_manager", source)
        self.assertIn("REMASK_SYNC_PRIVATE_LIST_PAGES_FIRST_V1", source)
        self.assertIn("list_pages_via_private_graphql", source)

        # Account-level Your-Pages discovery is bounded and runs only
        # after BM/RK live state is resolved. The heavy Ads Manager document is
        # unloaded before the cold->warm Page handoff to keep Chromium stable.
        self.assertIn("REMASK_ISOLATED_PAGE_PRIMARY_V2", source)
        self.assertIn("discover_managed_pages_isolated(fast=True)", source)
        self.assertNotIn("REMASK_ENABLE_GLOBAL_PAGE_DISCOVERY", source)
        self.assertNotIn("discover_managed_pages(fast=False)", source)
        self.assertNotIn("REMASK_PAGE_INVENTORY_WARM_RETRY_V2", source)
        self.assertNotIn(
            "Fan Page inventory was not confirmed by Ads Manager",
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


    def test_profile_context_page_sources_are_distinguished(self) -> None:
        root = Path(__file__).resolve().parents[2]
        bridge = root / "railway-python-worker-bridge-overlay.php"
        if not bridge.exists():
            self.skipTest("profile resolver overlay is not copied into python runtime image")
        source = bridge.read_text(encoding="utf-8")
        self.assertIn("'profile_saved'", source)
        self.assertIn("'source' => 'profile_cache'", source)
        self.assertIn("'pages' => rmx_py_profile_pages($account, $profile)", source)

    def test_page_sync_uses_isolated_authenticated_tab(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)
        browser_source=inspect.getsource(
            FacebookBusinessBrowser.discover_managed_pages_isolated
        )
        discovery_source=inspect.getsource(
            FacebookBusinessBrowser.discover_managed_pages
        )
        goto_source=inspect.getsource(FacebookBusinessBrowser._goto)

        self.assertIn("REMASK_ISOLATED_PAGE_PRIMARY_V2", source)
        self.assertIn("discover_managed_pages_isolated(fast=True)", source)
        self.assertNotIn("REMASK_ENABLE_GLOBAL_PAGE_DISCOVERY", source)

        self.assertIn("REMASK_ISOLATED_PAGE_INVENTORY_V1", browser_source)
        self.assertIn("REMASK_LOW_MEMORY_PAGE_HANDOFF_V1", browser_source)
        self.assertIn("REMASK_PAGE_HANDOFF_UNLOAD_ADS_V1", browser_source)
        self.assertIn('page.goto(\n                "about:blank"', browser_source)
        self.assertIn("attempts=2 if fast else 1", browser_source)
        self.assertIn("navigation_timeout_ms=9000", browser_source)
        self.assertIn("handoff_history", browser_source)
        self.assertNotIn("self._browser_context.new_page()", browser_source)
        self.assertNotIn("self.page=primary_page", browser_source)
        self.assertNotIn("probe_page.close", browser_source)
        self.assertIn("navigation_timeout_ms: int | None = None", discovery_source)
        self.assertIn("auth_body_timeout_ms=500 if fast else 1500", discovery_source)
        self.assertIn("2.0 if fast else 2.4", discovery_source)
        self.assertIn("0.35 if fast else 0.5", discovery_source)

        self.assertIn("auth_body_timeout_ms: int = 1500", goto_source)
        self.assertIn("body_timeout_ms=max(", goto_source)
        self.assertIn("attempts=1", discovery_source)
        self.assertIn("navigation_attempts=", goto_source)

        auth_source=inspect.getsource(
            FacebookBusinessBrowser._assert_authenticated
        )
        self.assertIn("REMASK_AUTH_URL_BEFORE_BODY_V1", auth_source)
        self.assertLess(
            auth_source.index('if "/login" in lower_url'),
            auth_source.index("body = ("),
        )

    def test_isolated_page_probe_runs_before_lower_confidence_fallbacks(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)
        start=source.index("REMASK_FULL_PROFILE_SYNC_PAGES_V2")
        end=source.index("REMASK_LIVE_TARGET_SET_REQUIRED_V1",start)
        page_phase=source[start:end]

        isolated=page_phase.index("REMASK_ISOLATED_PAGE_PRIMARY_V2")
        known=page_phase.index("REMASK_KNOWN_PAGE_FAST_REVALIDATION_V1")
        ads=page_phase.index("REMASK_ADS_MANAGER_PAGES_FIRST_V1")
        private=page_phase.index("REMASK_SYNC_PRIVATE_LIST_PAGES_FIRST_V1")

        self.assertLess(isolated,known)
        self.assertLess(isolated,ads)
        self.assertLess(isolated,private)
        self.assertIn("isolated_pages_timeout=optional_page_budget(20.0)",page_phase)
        self.assertIn("if isolated_pages_timeout < 17.5:",page_phase)
        self.assertEqual(
            page_phase.count("discover_managed_pages_isolated(fast=True)"),
            1,
        )
        self.assertIn(
            "REMASK_LIVE_PAGE_LIST_REPLACES_BASELINE_V1",
            page_phase,
        )
        self.assertIn("pages=live_pages",page_phase)
        self.assertIn(
            "if not pages_live_verified and known_pages_by_business:",
            page_phase,
        )
        self.assertIn(
            "if not pages_live_verified:\n                try:\n                    ads_pages_timeout",
            page_phase,
        )


    def test_optional_page_enrichment_cannot_exhaust_whole_sync(self) -> None:
        source=inspect.getsource(api.profile_live_inventory)
        start=source.index("REMASK_FULL_PROFILE_SYNC_PAGES_V2")
        end=source.index("REMASK_LIVE_TARGET_SET_REQUIRED_V1",start)
        page_phase=source[start:end]

        self.assertIn("REMASK_OPTIONAL_PAGE_BUDGET_V1", page_phase)
        self.assertIn("def optional_page_budget(", page_phase)
        self.assertIn("fast_pages_timeout=optional_page_budget(5.5)", page_phase)
        self.assertIn("ads_pages_timeout=optional_page_budget(4.0)", page_phase)
        self.assertIn("private_pages_timeout=optional_page_budget(3.5)", page_phase)
        self.assertIn("isolated_pages_timeout=optional_page_budget(20.0)", page_phase)
        self.assertIn("if isolated_pages_timeout < 17.5:", page_phase)
        self.assertNotIn("=budget(5.5)", page_phase)
        self.assertNotIn("=budget(4.0)", page_phase)
        self.assertNotIn("=budget(3.5)", page_phase)
        self.assertNotIn("=budget(20.0)", page_phase)
        self.assertNotIn("except HTTPException:\n                    raise", page_phase)

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
