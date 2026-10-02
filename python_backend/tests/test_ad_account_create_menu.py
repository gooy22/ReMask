import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.facebook_business_browser import FacebookBusinessBrowser


class AdAccountCreateMenuTests(unittest.IsolatedAsyncioTestCase):
    def browser(self):
        browser = FacebookBusinessBrowser(SimpleNamespace(profile_id='fixture'))
        browser.page = SimpleNamespace(wait_for_timeout=AsyncMock())
        browser._click_state_detected_ad_account_create_entry = AsyncMock(return_value={'clicked': True})
        browser._click_ad_account_form_action_by_visible_text = AsyncMock(return_value={'clicked': False})
        browser._wait_for_ad_account_ui_transition = AsyncMock(return_value={'state': 'FORM', 'name_input': True})
        return browser

    async def test_add_menu_classified_as_intro_clicks_its_exact_create_row(self):
        browser = self.browser()
        browser._ad_account_ui_state = AsyncMock(return_value={
            'state':'INTRO_DIALOG', 'signature':'menu', 'create_entry':True,
            'create_target':{'text':'Create a new ad account','role':'gridcell'},
        })
        self.assertTrue(await browser._wait_for_ad_account_create_entry(timeout_seconds=1))
        browser._click_state_detected_ad_account_create_entry.assert_awaited_once()
        browser._click_ad_account_form_action_by_visible_text.assert_not_awaited()

    async def test_real_intro_without_create_row_keeps_next_transition(self):
        browser = self.browser()
        browser._ad_account_ui_state = AsyncMock(return_value={'state':'INTRO_DIALOG','signature':'intro'})
        browser._click_ad_account_form_action_by_visible_text.return_value = {'clicked':True}
        self.assertTrue(await browser._advance_ad_account_intro_dialog())
        browser._click_state_detected_ad_account_create_entry.assert_not_awaited()
        browser._click_ad_account_form_action_by_visible_text.assert_awaited_once_with('next')

    async def test_click_without_opened_form_is_not_success_or_final_submission(self):
        browser = self.browser()
        state = {'state':'INTRO_DIALOG','signature':'menu','create_entry':True,'create_target':{'role':'gridcell'}}
        browser._ad_account_ui_state = AsyncMock(return_value=state)
        browser._wait_for_ad_account_ui_transition.return_value = state
        self.assertFalse(await browser._advance_ad_account_intro_dialog())
        browser._click_ad_account_form_action_by_visible_text.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
