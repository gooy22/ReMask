import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlencode

from app.facebook_business_browser import FacebookBusinessBrowser


class AdAccountLiveInventoryReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def lookup(self, *, request_business='1632909278268870', friendly='BizKitBusinessAssetsQuery', expected_id='', asset_only=False):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        listeners = {}
        page = SimpleNamespace(
            url='https://business.facebook.com/latest/settings/ad_accounts?business_id=1632909278268870',
            on=lambda name, fn: listeners.__setitem__(name, fn),
            remove_listener=lambda name, fn: listeners.pop(name, None),
        )
        browser.page = page
        browser.SETTINGS_AD_ACCOUNTS_URLS = (page.url,)
        payload = {'data': {'business_assets': {'nodes': [{
            'asset_type': 'AD_ACCOUNT', 'asset_id': '120251669477430356',
            'account_id': '1758104775449075', 'name': 'ReMask RK 8 20261002',
            'business_id': request_business,
        }]}}}
        if asset_only:
            payload['data']['business_assets']['nodes'][0].pop('account_id')
            page.evaluate = AsyncMock(return_value=[{
                'text':'ReMask RK 8 20261002 1758104775449075',
                'account_ids':['1758104775449075'], 'href':'',
            }])
        request = SimpleNamespace(method='POST', url='https://business.facebook.com/api/graphql/', headers={},
            post_data=urlencode({'fb_api_req_friendly_name': friendly,
                                'variables': json.dumps({'business_id': request_business})}))
        response = SimpleNamespace(url=request.url, request=request, text=AsyncMock(return_value=json.dumps(payload)))
        async def navigate(url):
            page.url = url
            listeners['response'](response)
            await asyncio.sleep(0)
        browser._goto = AsyncMock(side_effect=navigate)
        browser._safe_graphql_request_summary = lambda request: {}
        result = await browser.find_ad_account_in_inventory(
            business_id='1632909278268870', account_name='ReMask RK 8 20261002',
            expected_ad_account_id=expected_id, timeout_seconds=2)
        self.assertEqual(listeners, {})
        return result

    async def test_generic_asset_inventory_recovers_canonical_account_id(self):
        result = await self.lookup()
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['ad_account_id'], 'act_1758104775449075')

    async def test_generic_inventory_requires_the_expected_canonical_id(self):
        self.assertTrue((await self.lookup(expected_id='act_1758104775449075'))['confirmed'])
        self.assertFalse((await self.lookup(expected_id='act_120251669477430356'))['confirmed'])

    async def test_other_business_inventory_is_not_reconciliation_proof(self):
        self.assertFalse((await self.lookup(request_business='999999999'))['confirmed'])

    async def test_create_response_is_not_independent_inventory_proof(self):
        self.assertFalse((await self.lookup(friendly='BizKitCreateAdAccountMutation'))['confirmed'])

    async def test_details_identity_corrects_a_business_asset_alias(self):
        result = await self.lookup(asset_only=True)
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['ad_account_id'],'act_1758104775449075')
        self.assertEqual(result['business_asset_id'],'120251669477430356')

    async def test_alias_checkpoint_cannot_verify_the_wrong_account_id(self):
        result = await self.lookup(asset_only=True,expected_id='act_120251669477430356')
        self.assertFalse(result['confirmed'])
        self.assertEqual(result['ad_account_id'],'act_1758104775449075')

    async def test_navigation_cannot_exceed_inventory_budget(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        browser.page = SimpleNamespace(url='', on=lambda *args: None, remove_listener=lambda *args: None)
        browser.SETTINGS_AD_ACCOUNTS_URLS = ('https://business.facebook.com/settings/ad-accounts/?business_id={business_id}',)
        cancelled = asyncio.Event()
        async def stalled(url):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        browser._goto = AsyncMock(side_effect=stalled)
        result = await asyncio.wait_for(browser.find_ad_account_in_inventory(
            business_id='1632909278268870', account_name='ReMask RK 8 20261002', timeout_seconds=2), timeout=3)
        self.assertTrue(cancelled.is_set())
        self.assertFalse(result['confirmed_empty'])

    async def test_quota_tooltip_is_a_terminal_meta_refusal(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        message = "You've reached the maximum number of ad accounts allowed for a new business portfolio."
        browser.page = SimpleNamespace(evaluate=AsyncMock(return_value={
            'state': 'INTRO_DIALOG', 'create_entry': True, 'errors': [], 'create_target': {'text': message},
        }))
        state = await browser._ad_account_ui_state()
        self.assertEqual(state['state'], 'BLOCKED')
        self.assertFalse(state['create_entry'])
        self.assertEqual(state['errors'][0], message)
