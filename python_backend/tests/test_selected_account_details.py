import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from app.facebook_business_browser import FacebookBusinessBrowser


class SelectedAccountDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def read(self, row_count=1, details_count=1, initially_confirmed=False):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        details = SimpleNamespace(count=AsyncMock(return_value=details_count), is_visible=AsyncMock(return_value=True), click=AsyncMock())
        rows = SimpleNamespace(count=AsyncMock(return_value=row_count), get_by_role=MagicMock(return_value=details))
        row_locator = SimpleNamespace(filter=MagicMock(return_value=rows))
        browser.page = SimpleNamespace(get_by_role=MagicMock(return_value=row_locator))
        browser._reconcile_created_ad_account_from_ui = AsyncMock(side_effect=[
            {'confirmed':initially_confirmed}, {'confirmed':True,'ad_account_id':'1758104775449075'}])
        result = await browser._read_selected_ad_account_identity(business_id='1632909278268870',account_name='ReMask RK 8 20261002')
        return result, browser, details, rows

    async def test_missing_details_identity_opens_only_unique_named_row_details(self):
        result, browser, details, rows = await self.read()
        self.assertTrue(result['confirmed'])
        details.click.assert_awaited_once_with(timeout=1000)
        browser.page.get_by_role.assert_called_once_with('row')
        browser.page.get_by_role.return_value.filter.assert_called_once_with(has_text='ReMask RK 8 20261002')
        self.assertEqual(rows.get_by_role.call_args.args[0],'link')

    async def test_ambiguous_row_or_details_never_clicked(self):
        for args in ({'row_count':2},{'details_count':2}):
            result, _, details, _ = await self.read(**args)
            self.assertFalse(result['confirmed'])
            details.click.assert_not_awaited()

    async def test_confirmed_identity_needs_no_additional_click(self):
        result, browser, details, _ = await self.read(initially_confirmed=True)
        self.assertTrue(result['confirmed'])
        browser.page.get_by_role.assert_not_called()
        details.click.assert_not_awaited()
