import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import FacebookBusinessBrowser


class AdAccountSectionNavigationTests(unittest.IsolatedAsyncioTestCase):
    def browser(self, current, href):
        browser=FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        browser.page=SimpleNamespace(url=current, evaluate=AsyncMock(return_value={
            'mode':'href','href':href,'text':'Ad accounts','candidates':[],
        }), wait_for_timeout=AsyncMock())
        browser._goto=AsyncMock(return_value=href)
        browser._assert_authenticated=AsyncMock()
        browser._click_named=AsyncMock(return_value=False)
        return browser

    async def test_already_open_exact_section_does_not_reload_heavy_settings_document(self):
        browser=self.browser(
            'https://business.facebook.com/latest/settings/ad_accounts/?business_id=1632909278268870&nav_ref=redirect',
            'https://business.facebook.com/latest/settings/ad_accounts?business_id=1632909278268870')
        self.assertTrue(await browser._activate_ad_account_settings_section(business_id='1632909278268870'))
        browser._goto.assert_not_awaited()
        browser._assert_authenticated.assert_awaited_once()

    async def test_different_section_still_uses_observed_link(self):
        href='https://business.facebook.com/latest/settings/ad_accounts?business_id=1632909278268870'
        browser=self.browser('https://business.facebook.com/latest/settings/people?business_id=1632909278268870',href)
        self.assertTrue(await browser._activate_ad_account_settings_section(business_id='1632909278268870'))
        browser._goto.assert_awaited_once_with(href)

    async def test_matching_path_cannot_accept_a_different_business(self):
        browser=self.browser(
            'https://business.facebook.com/latest/settings/ad_accounts/?business_id=999999999',
            'https://business.facebook.com/latest/settings/ad_accounts?business_id=999999999')
        self.assertFalse(await browser._activate_ad_account_settings_section(business_id='1632909278268870'))
        browser._goto.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
