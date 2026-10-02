import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import main as api


class InventoryProbeReached(BaseException):
    pass


class WrongInventorySurface(BaseException):
    pass


class SelectedBusinessInventoryTests(unittest.IsolatedAsyncioTestCase):
    def fixtures(self, bindings=()):
        store = SimpleNamespace(**{name:AsyncMock(return_value=[]) for name in (
            'confirmed_ad_account_bindings_for_profile',
            'confirmed_business_page_bindings_for_profile', 'latest_profile_fan_pages',
            'latest_profile_fan_page_batch',
        )})
        store.confirmed_ad_account_bindings_for_profile.return_value=list(bindings)
        store.latest_profile_entities=AsyncMock(return_value={})
        pool=SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=SimpleNamespace(pages=[]))),
                             provisioning_state=store)
        browser=SimpleNamespace(
            snapshot_businesses=AsyncMock(side_effect=WrongInventorySurface()),
            probe_ads_manager_inventory_context=AsyncMock(side_effect=InventoryProbeReached()),
        )
        session=SimpleNamespace(facebook_business_browser=AsyncMock(return_value=browser))
        factory=MagicMock()
        factory.return_value.__aenter__=AsyncMock(return_value=session)
        factory.return_value.__aexit__=AsyncMock(return_value=False)
        return pool,browser,factory

    async def test_selected_empty_business_is_probed_without_profile_discovery(self):
        pool,browser,factory=self.fixtures()
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(InventoryProbeReached):
                await api.profile_live_inventory('8',business_ids='1632909278268870')
        browser.snapshot_businesses.assert_not_awaited()
        self.assertEqual(browser.probe_ads_manager_inventory_context.await_args.kwargs['business_id'],
                         '1632909278268870')

    async def test_full_profile_discovers_businesses_even_with_old_account_binding(self):
        pool,browser,factory=self.fixtures([{'business_id':'111111111','ad_account_id':'222222222'}])
        browser.snapshot_businesses.side_effect=InventoryProbeReached()
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(InventoryProbeReached):
                await api.profile_live_inventory('8')
        browser.snapshot_businesses.assert_awaited_once()
        browser.probe_ads_manager_inventory_context.assert_not_awaited()

    async def test_unconfirmed_selected_account_hint_still_reads_exact_settings_inventory(self):
        pool,browser,factory=self.fixtures([{
            'business_id':'1632909278268870','ad_account_id':'120251669477430356',
        }])
        browser.probe_ads_manager_inventory_context.side_effect=None
        browser.probe_ads_manager_inventory_context.return_value={'confirmed':False}
        browser.snapshot_ad_accounts_for_business=AsyncMock(side_effect=InventoryProbeReached())
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(InventoryProbeReached):
                await api.profile_live_inventory('8',business_ids='1632909278268870')
        browser.snapshot_businesses.assert_not_awaited()
        browser.probe_ads_manager_inventory_context.assert_awaited_once()
        self.assertEqual(browser.snapshot_ad_accounts_for_business.await_args.kwargs['business_id'],
                         '1632909278268870')
