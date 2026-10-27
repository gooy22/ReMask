"""Regression checks for the interactive read-only Sync path."""
import unittest
from unittest.mock import AsyncMock, patch

from app.private_inventory import private_inventory_snapshot
from app.facebook_page_discovery import PageDiscoveryError, list_pages_via_private_graphql


class FastPrivateSyncTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.web = type("FakeWeb", (), {})()
        self.web.graphql = AsyncMock()
        self.web.fetch_text = AsyncMock()
        self.web.refresh_reviewed_modules = AsyncMock()

    async def test_exact_pages_candidate_uses_profile_bound_loginpage_probe(self):
        from types import SimpleNamespace
        from app.facebook_docids import DocIdCandidate
        candidate = DocIdCandidate(
            operation="LIST_PAGES", doc_id="123456789012345",
            friendly_name="AccountQualityUserPagesWrapper_UserPageQuery",
            endpoint_url="https://www.facebook.com/api/graphql/",
            variables_mode="account_quality_user_pages_v1",
            source="runtime_bundle", priority=1, enabled=True,
        )
        self.web.profile = SimpleNamespace(cookies={"c_user": "123456789"})
        self.web.graphql.return_value = {"data": {}}
        with patch("app.facebook_page_discovery.list_candidates", return_value=[candidate]), patch(
                "app.facebook_page_discovery.record_result") as record:
            result = await list_pages_via_private_graphql(
                self.web, allow_runtime_discovery=False,
            )
        self.assertEqual(result.pages, [])
        self.assertFalse(result.inventory_complete)
        self.web.graphql.assert_awaited_once()
        args, kwargs = self.web.graphql.await_args
        self.assertEqual(args[1], {"assetOwnerId": "123456789"})
        self.assertEqual(kwargs["business_loginpage_probe"], "LIST_PAGES")
        self.assertEqual(kwargs["read_timeout_seconds"], 30)
        record.assert_called_once()

    async def snapshot(self):
        return await private_inventory_snapshot(
            self.web,
            known_accounts_by_business={"111111": {"222222"}},
            known_business_ids={"111111"},
            discover_businesses=False,
            execute_read_queries=True,
            fast_sync=True,
        )

    async def test_confirmed_static_rk_skips_html_and_module_scan(self):
        proof = {"account_rows": [
            {"id": "222222", "business_id": "111111", "name": "Account"}
        ], "complete": True, "source": "static_http_exact_business_inventory"}
        with patch("app.static_meta_inventory.read_ad_account_inventory",
                   new=AsyncMock(return_value=proof)) as read:
            result = await self.snapshot()
        self.assertTrue(result["ready"])
        self.assertEqual(result["businesses"][0]["ad_accounts_source"], "static_graphql_inventory")
        self.assertEqual(result["businesses"][0]["ad_accounts"][0]["id"], "222222")
        self.assertEqual(read.await_count, 1)
        self.web.fetch_text.assert_not_awaited()
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_partial_static_rk_cannot_clear_known_account(self):
        async def html_without_inventory(url, **kwargs):
            return 200, "{}", url
        self.web.fetch_text.side_effect = html_without_inventory
        proof = {"account_rows": [], "complete": False,
                 "source": "static_http_exact_business_inventory"}
        with patch("app.static_meta_inventory.read_ad_account_inventory",
                   new=AsyncMock(return_value=proof)), patch(
                   "app.private_inventory_queries.read_private_inventory_queries",
                   new_callable=AsyncMock) as dynamic:
            result = await self.snapshot()
        self.assertFalse(result["ready"])
        self.assertEqual(result["inconclusive_business_ids"], ["111111"])
        self.assertFalse(result["businesses"][0]["confirmed_empty"])
        self.assertEqual(result["businesses"][0]["expected_account_ids"], ["222222"])
        dynamic.assert_not_awaited()
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_verified_expected_pair_can_be_used_without_complete_prefix(self):
        proof = {"account_rows": [
            {"id": "222222", "business_id": "111111", "name": "Account"}
        ], "complete": False, "source": "static_http_exact_business_inventory"}
        with patch("app.static_meta_inventory.read_ad_account_inventory",
                   new=AsyncMock(return_value=proof)):
            result = await self.snapshot()
        self.assertFalse(result["ready"])
        self.assertEqual(result["inconclusive_business_ids"], ["111111"])
        self.assertEqual(result["businesses"][0]["confirmed_expected_account_ids"], ["222222"])
        self.assertFalse(result["businesses"][0]["inventory_complete"])
        self.assertTrue(result["businesses"][0]["ad_accounts_partial"])
        self.web.fetch_text.assert_not_awaited()

    async def test_unknown_account_in_partial_collection_is_not_complete(self):
        async def empty_html(url, **kwargs):
            return 200, "{}", url
        self.web.fetch_text.side_effect = empty_html
        proof = {"account_rows": [
            {"id": "333333", "business_id": "111111", "name": "New RK"}
        ], "complete": False, "source": "static_http_exact_business_inventory"}
        with patch("app.static_meta_inventory.read_ad_account_inventory",
                   new=AsyncMock(return_value=proof)):
            result = await private_inventory_snapshot(
                self.web, known_accounts_by_business={},
                known_business_ids={"111111"},
                discover_businesses=False, execute_read_queries=True, fast_sync=True,
            )
        self.assertFalse(result["ready"])
        self.assertFalse(result["businesses"][0]["ad_accounts_ready"])
        self.assertFalse(result["businesses"][0]["inventory_complete"])
        self.assertEqual(result["businesses"][0]["ad_accounts"][0]["id"], "333333")
        self.web.refresh_reviewed_modules.assert_not_awaited()

    async def test_unknown_account_requires_complete_collection(self):
        proof = {"account_rows": [
            {"id": "333333", "business_id": "111111", "name": "New RK"}
        ], "complete": True, "source": "static_http_exact_business_inventory"}
        with patch("app.static_meta_inventory.read_ad_account_inventory",
                   new=AsyncMock(return_value=proof)):
            result = await private_inventory_snapshot(
                self.web, known_accounts_by_business={},
                known_business_ids={"111111"},
                discover_businesses=False, execute_read_queries=True, fast_sync=True,
            )
        self.assertTrue(result["ready"])
        self.assertTrue(result["businesses"][0]["inventory_complete"])
        self.web.fetch_text.assert_not_awaited()

    async def test_fast_pages_do_not_trigger_script_discovery_without_contract(self):
        with patch("app.facebook_page_discovery.list_candidates", return_value=[]), patch(
                "app.facebook_page_discovery.discover_current_list_pages_docid_by_marker",
                new_callable=AsyncMock) as marker, patch(
                "app.facebook_page_discovery.discover_current_list_pages_docid",
                new_callable=AsyncMock) as bundle:
            with self.assertRaises(PageDiscoveryError):
                await list_pages_via_private_graphql(
                    self.web, allow_runtime_discovery=False,
                )
        marker.assert_not_awaited()
        bundle.assert_not_awaited()

    async def test_rk_auth_gate_stops_all_followup_reads(self):
        from fb_worker import AuthenticationError
        error = AuthenticationError('Fixture authentication required')
        error.meta_payload = {'business_precheck': [{'auth_reason': 'checkpoint_redirect'}]}
        with patch('app.static_meta_inventory.read_ad_account_inventory',
                   new=AsyncMock(side_effect=error)):
            result = await self.snapshot()
        self.assertFalse(result['ready'])
        self.assertEqual(result['diagnostics'][-1]['auth_gate'], 'CHECKPOINT_REQUIRED')
        self.web.fetch_text.assert_not_awaited()

    async def test_partial_bm_prefix_does_not_complete_a_full_profile_sync(self):
        self.web.fetch_text.return_value = (200, '{}', 'https://business.facebook.com/latest/home')
        business = {'rows': {'111111': 'BM'}, 'complete': False}
        accounts = {'account_rows': [{'id': '222222', 'business_id': '111111'}], 'complete': True}
        with patch('app.static_meta_inventory.read_business_inventory',
                   new=AsyncMock(return_value=business)), patch(
                   'app.static_meta_inventory.read_ad_account_inventory',
                   new=AsyncMock(return_value=accounts)):
            result = await private_inventory_snapshot(self.web,
                known_accounts_by_business={'111111': {'222222'}},
                known_business_ids={'111111'}, execute_read_queries=True, fast_sync=True)
        self.assertFalse(result['ready'])
        self.assertFalse(result['discovery_complete'])
        self.assertEqual(result['confirmed_business_ids'], ['111111'])
        self.assertTrue(result['businesses'][0]['ad_accounts_ready'])
        self.web.fetch_text.assert_not_awaited()
        self.assertEqual(result['inventory_scope'],'profile')
        self.assertIs(type(result['timings_ms']['business_discovery']),int)
        self.assertEqual(result['timings_ms']['account_inventory'][0]['business_id'],'111111')

    async def test_authenticated_html_rows_do_not_prove_bm_enumeration_complete(self):
        from tests.test_private_inventory import inventory, account
        import json
        payload = inventory([account()])
        self.web.fetch_text.return_value = (200, json.dumps(payload),
            'https://business.facebook.com/latest/home')
        with patch('app.static_meta_inventory.read_business_inventory',
                   new=AsyncMock(return_value={'rows': {}, 'complete': False})), patch(
                   'app.static_meta_inventory.read_ad_account_inventory',
                   new=AsyncMock(return_value={'account_rows': [], 'complete': False})):
            result = await private_inventory_snapshot(self.web,
                known_accounts_by_business={}, known_business_ids={'111111'},
                execute_read_queries=True, fast_sync=True)
        self.assertFalse(result['ready'])
        self.assertFalse(result['discovery_complete'])
        self.assertEqual(result['businesses'][0]['ad_accounts'][0]['id'], '222222')
