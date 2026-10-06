from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.facebook_page_discovery import PageDiscoveryError
from app.provisioning.fan_pages_handler import _reconcile_uncertain_page


class PendingPageQueryRefreshTests(unittest.IsolatedAsyncioTestCase):
    def session(self):
        return SimpleNamespace(context=SimpleNamespace(profile_id="fixture"),
                               facebook_web=AsyncMock(return_value=SimpleNamespace()))

    async def test_stale_read_query_refresh_recovers_pending_page_without_create(self):
        result = SimpleNamespace(pages=[{"id": "2222222222", "name": "Fixture Page"}],
                                 source="facebook_web_graphql", diagnostics=[], inventory_complete=False)
        with patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(
                side_effect=BrowserBusinessError("FAN_PAGES_NOT_DISCOVERED", "shell"))), \
             patch("app.provisioning.fan_pages_handler.list_pages_via_private_graphql", new=AsyncMock(
                side_effect=[PageDiscoveryError("stale query"), result])) as inventory, \
             patch("app.provisioning.fan_pages_handler.discover_current_list_pages_docid_by_marker", new=AsyncMock(
                return_value=SimpleNamespace(doc_id="987654321"))) as learn:
            found, absent, diagnostics = await _reconcile_uncertain_page(
                self.session(), page_name="Fixture Page", before_ids=set())
        self.assertEqual(found["id"], "2222222222")
        self.assertFalse(absent)
        self.assertEqual(inventory.await_count, 2)
        learn.assert_awaited_once()
        self.assertEqual(learn.await_args.kwargs, {"max_entries": 2, "per_entry_timeout": 2.2})

    async def test_no_query_or_failed_learning_keeps_guard_and_learns_only_once(self):
        for outcome in (None, RuntimeError("discovery unavailable")):
            with self.subTest(outcome=outcome):
                learner = AsyncMock(return_value=None) if outcome is None else AsyncMock(side_effect=outcome)
                with patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(
                        side_effect=BrowserBusinessError("FAN_PAGES_NOT_DISCOVERED", "shell"))), \
                     patch("app.provisioning.fan_pages_handler.list_pages_via_private_graphql", new=AsyncMock(
                        side_effect=PageDiscoveryError("stale query"))) as inventory, \
                     patch("app.provisioning.fan_pages_handler.discover_current_list_pages_docid_by_marker", new=learner), \
                     patch("app.provisioning.fan_pages_handler.discover_pages_from_browser_html", new=AsyncMock(
                        side_effect=PageDiscoveryError("no HTML proof"))), \
                     patch("app.provisioning.fan_pages_handler.asyncio.sleep", new=AsyncMock()):
                    found, absent, diagnostics = await _reconcile_uncertain_page(
                        self.session(), page_name="Fixture Page", before_ids=set())
                self.assertIsNone(found)
                self.assertFalse(absent)
                self.assertEqual(inventory.await_count, 3)
                learner.assert_awaited_once()

    async def test_refresh_does_not_authorize_absence_from_partial_empty_data(self):
        partial = SimpleNamespace(pages=[], source="facebook_web_graphql", diagnostics=[], inventory_complete=False)
        with patch("app.provisioning.fan_pages_handler._fresh_page_inventory", new=AsyncMock(return_value=[])), \
             patch("app.provisioning.fan_pages_handler.list_pages_via_private_graphql", new=AsyncMock(
                side_effect=[PageDiscoveryError("stale"), partial, partial, partial])), \
             patch("app.provisioning.fan_pages_handler.discover_current_list_pages_docid_by_marker", new=AsyncMock(
                return_value=SimpleNamespace(doc_id="987654321"))), \
             patch("app.provisioning.fan_pages_handler.discover_pages_from_browser_html", new=AsyncMock(
                side_effect=PageDiscoveryError("no HTML proof"))), \
             patch("app.provisioning.fan_pages_handler.asyncio.sleep", new=AsyncMock()):
            found, absent, diagnostics = await _reconcile_uncertain_page(
                self.session(), page_name="Fixture Page", before_ids=set())
        self.assertIsNone(found)
        self.assertFalse(absent)
