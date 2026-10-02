import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import FacebookBusinessBrowser


class AdAccountUiCandidateTests(unittest.IsolatedAsyncioTestCase):
    async def reconcile(self, text, ids, href=''):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture', cookies={'c_user':'61594897075733'}))
        browser.page = SimpleNamespace(evaluate=AsyncMock(return_value=[{
            'text':text, 'ids':ids, 'href':href, 'x':700, 'y':320,
        }]))
        return await browser._reconcile_created_ad_account_from_ui(
            business_id='1632909278268870', account_name='ReMask RK 8 20261002')

    async def test_date_in_entered_name_is_not_an_account_id(self):
        result = await self.reconcile('ReMask RK 8 20261002', ['20261002'])
        self.assertFalse(result['confirmed'])

    async def test_business_id_next_to_wizard_name_is_not_an_account_id(self):
        result = await self.reconcile('ReMask RK 8 20261002 ID: 1632909278268870', ['1632909278268870'])
        self.assertFalse(result['confirmed'])

    async def test_actor_id_is_not_an_account_id(self):
        result = await self.reconcile('ReMask RK 8 20261002 ID: 61594897075733', ['61594897075733'])
        self.assertFalse(result['confirmed'])

    async def test_explicit_account_label_ignores_date_in_name(self):
        result = await self.reconcile('ReMask RK 8 20261002 Ad account ID: 123456789012345', ['20261002','123456789012345'])
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['ad_account_id'], 'act_123456789012345')

    async def test_account_link_ignores_name_date_and_business_scope(self):
        result = await self.reconcile('ReMask RK 8 20261002', ['20261002','1632909278268870','123456789012345'],
            'https://business.facebook.com/latest/settings/ad_accounts/?business_id=1632909278268870&asset_id=123456789012345')
        self.assertTrue(result['confirmed'])
        self.assertEqual(result['ad_account_id'], 'act_123456789012345')


if __name__ == '__main__':
    unittest.main()
