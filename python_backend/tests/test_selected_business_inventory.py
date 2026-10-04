import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

import main as api


class InventoryProbeReached(BaseException):
    pass


class WrongInventorySurface(BaseException):
    pass


class SelectedBusinessInventoryTests(unittest.IsolatedAsyncioTestCase):
    def test_expected_name_uses_existing_exact_profile_binding_without_inventing_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bindings.json'
            path.write_text(json.dumps({'8':{'ad_accounts':{'1632909278268870':{'business_id':'1632909278268870','ad_account_id':'1758104775449075','account_name':'ReMask RK 8 20261002'}}},'Other':{'business_id':'999999999','ad_account_id':'888888888','account_name':'Other RK'}}))
            names=api._sync_expected_account_names('8',[],str(path))
            self.assertEqual(names,{'1632909278268870':'ReMask RK 8 20261002'})
            self.assertEqual(api._sync_expected_account_names('Missing',[],str(path)),{})
            path.write_text('{broken')
            self.assertEqual(api._sync_expected_account_names('8',[{'business_id':'123456789','account_name':'Worker RK'}],str(path)),{'123456789':'Worker RK'})

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

    async def test_full_sync_ads_timeout_reads_settings_on_fresh_same_profile_browser(self):
        pool,browser,factory=self.fixtures()
        browser.snapshot_businesses.side_effect=None
        browser.snapshot_businesses.return_value={'1632909278268870':'Existing BM'}
        browser.probe_ads_manager_inventory_context.side_effect=asyncio.TimeoutError()
        browser.close=AsyncMock()
        fresh=SimpleNamespace(
            probe_ads_manager_inventory_context=AsyncMock(side_effect=WrongInventorySurface()),
            snapshot_ad_accounts_for_business=AsyncMock(side_effect=InventoryProbeReached()),
        )
        session=factory.return_value.__aenter__.return_value
        session.facebook_business_browser.side_effect=[browser,fresh]
        session._business_browser=browser
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(InventoryProbeReached):
                await api.profile_live_inventory('8')
        browser.close.assert_awaited_once()
        self.assertIsNone(session._business_browser)
        self.assertEqual(session.facebook_business_browser.await_count,2)
        fresh.probe_ads_manager_inventory_context.assert_not_awaited()
        self.assertEqual(fresh.snapshot_ad_accounts_for_business.await_args.kwargs['business_id'],
                         '1632909278268870')

    async def test_selected_hint_timeout_uses_settings_without_repeating_ads_probe(self):
        pool,browser,factory=self.fixtures([{
            'business_id':'1632909278268870','ad_account_id':'120251669477430356',
        }])
        browser.probe_ads_manager_inventory_context.side_effect=asyncio.TimeoutError()
        browser.close=AsyncMock()
        fresh=SimpleNamespace(
            probe_ads_manager_inventory_context=AsyncMock(side_effect=WrongInventorySurface()),
            snapshot_ad_accounts_for_business=AsyncMock(side_effect=InventoryProbeReached()),
        )
        session=factory.return_value.__aenter__.return_value
        session.facebook_business_browser.side_effect=[browser,fresh]
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(InventoryProbeReached):
                await api.profile_live_inventory('8',business_ids='1632909278268870')
        browser.close.assert_awaited_once()
        fresh.probe_ads_manager_inventory_context.assert_not_awaited()
        fresh.snapshot_ad_accounts_for_business.assert_awaited_once()

    async def test_settings_timeout_after_ads_timeout_remains_failure(self):
        pool,browser,factory=self.fixtures()
        browser.snapshot_businesses.side_effect=None
        browser.snapshot_businesses.return_value={'1632909278268870':'Existing BM'}
        browser.probe_ads_manager_inventory_context.side_effect=asyncio.TimeoutError()
        browser.close=AsyncMock()
        fresh=SimpleNamespace(
            close=AsyncMock(),
            snapshot_ad_accounts_for_business=AsyncMock(side_effect=asyncio.TimeoutError()),
        )
        session=factory.return_value.__aenter__.return_value
        session.facebook_business_browser.side_effect=[browser,fresh]
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            with self.assertRaises(api.HTTPException) as failure:
                await api.profile_live_inventory('8')
        self.assertEqual(failure.exception.status_code,504)
        self.assertEqual(failure.exception.detail,'LIVE_INVENTORY_TIMEOUT:rk_inventory')
        self.assertEqual(session.facebook_business_browser.await_count,2)
        fresh.close.assert_awaited_once()

    async def test_settings_live_success_recovers_full_sync_after_ads_timeout(self):
        pool,browser,factory=self.fixtures()
        browser.snapshot_businesses.side_effect=None
        browser.snapshot_businesses.return_value={'1632909278268870':'Existing BM'}
        browser.probe_ads_manager_inventory_context.side_effect=asyncio.TimeoutError()
        browser.close=AsyncMock()
        fresh=SimpleNamespace(
            snapshot_ad_accounts_for_business=AsyncMock(return_value={
                'ready':True,'accounts':[{'id':'1758104775449075'}],
                'source':'business_settings_live_inventory',
            }),
            discover_managed_pages_isolated=AsyncMock(return_value=[]),
        )
        session=factory.return_value.__aenter__.return_value
        session.facebook_business_browser.side_effect=[browser,fresh]
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
            result=await api.profile_live_inventory('8')
        self.assertTrue(result['live_ready'])
        row=result['businesses'][0]
        self.assertEqual(row['ad_accounts'][0]['id'],'1758104775449075')
        self.assertEqual(row['ad_accounts_source'],'business_settings_live_inventory')
        self.assertFalse(row['ad_accounts_partial'])
        self.assertFalse(row['ads_manager_diagnostic']['confirmed'])

    async def test_single_ads_account_is_partial_in_full_and_selected_sync(self):
        for selected in (False,True):
            with self.subTest(selected=selected):
                pool,browser,factory=self.fixtures([{
                    'business_id':'1632909278268870','ad_account_id':'1758104775449075',
                }])
                browser.snapshot_businesses.side_effect=None
                browser.snapshot_businesses.return_value={'1632909278268870':'Existing BM'}
                browser.probe_ads_manager_inventory_context.side_effect=None
                browser.probe_ads_manager_inventory_context.return_value={
                    'confirmed':True,'confirmed_accounts':[{'id':'1758104775449075'}],
                    'confirmation_source':'ads_manager_generic_live_act_matches_confirmed_snapshot',
                }
                browser.discover_managed_pages_isolated=AsyncMock(return_value=[])
                with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',factory):
                    result=await api.profile_live_inventory('8',
                        business_ids='1632909278268870' if selected else '')
                self.assertTrue(result['live_ready'])
                self.assertTrue(result['businesses'][0]['ad_accounts_partial'])
                self.assertEqual(result['businesses'][0]['ad_accounts_count'],1)
