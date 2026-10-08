import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_page_discovery import PageDiscoveryResult
from app.provisioning.fan_pages_handler import _fresh_page_inventory


class FanPagePrivatePreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_complete_private_inventory_needs_no_chromium(self):
        session = SimpleNamespace(context=SimpleNamespace(pages=[]), facebook_web=AsyncMock())
        result = PageDiscoveryResult(pages=[{"id": "123456789", "name": "PrgssTeam"}],
                                     source="facebook_web_graphql", inventory_complete=True)
        with patch("app.provisioning.fan_pages_handler.list_pages_via_private_graphql", new=AsyncMock(return_value=result)), \
             patch("app.provisioning.fan_pages_handler._browser_lease", side_effect=AssertionError("No browser")):
            rows = await _fresh_page_inventory(session)
        self.assertEqual(rows[0]["id"], "123456789")
        self.assertEqual(session.context.pages[0]["id"], "123456789")

    async def test_partial_or_failed_private_inventory_preserves_browser_fallback(self):
        for outcome in (PageDiscoveryResult(pages=[], source="private", inventory_complete=False), TimeoutError()):
            session = SimpleNamespace(context=SimpleNamespace(pages=[{"id": "saved", "name": "saved"}]),
                                      facebook_web=AsyncMock())
            browser = SimpleNamespace(discover_managed_pages=AsyncMock(return_value=[{"id": "123456789", "name": "PrgssTeam"}]))
            lease = AsyncMock()
            lease.__aenter__.return_value = browser
            discovery = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
            with patch("app.provisioning.fan_pages_handler.list_pages_via_private_graphql", new=discovery), \
                 patch("app.provisioning.fan_pages_handler._browser_lease", return_value=lease):
                rows = await _fresh_page_inventory(session)
            browser.discover_managed_pages.assert_awaited_once()
            self.assertEqual(rows[0]["id"], "123456789")
