import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_page_confirmation import exact_main_scope
from app.facebook_business_browser import BrowserBusinessError
from app.provisioning.fan_pages_handler import fan_pages_handler
from app.provisioning.models import ProvisioningStep, ProvisioningError
from app.provisioning.state import ProvisioningStateStore


class PageConfirmTests(unittest.IsolatedAsyncioTestCase):
    def test_main_scope_rejects_ignored_route_and_mixed_live_scope(self):
        url='https://adsmanager.facebook.com/adsmanager/manage/accounts?business_id=111111111'
        self.assertFalse(exact_main_scope(url,'111111111',set()))
        self.assertFalse(exact_main_scope(url,'111111111',{'999999999'}))
        self.assertFalse(exact_main_scope(url,'111111111',{'111111111','999999999'}))
        self.assertTrue(exact_main_scope(url,'111111111',{'111111111'}))

    async def test_confirm_retries_saved_page_without_creating_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=ProvisioningStateStore(tmp+'/state.sqlite3'); await state.init()
            await state.complete('created','9','created',ProvisioningStep.FAN_PAGES,
                {'pages':[{'id':'222222222','name':'ReMask Page'}],'page_ids':['222222222']})
            session=SimpleNamespace(context=SimpleNamespace(cookies={'c_user':'111111111'},profile_id='9',pages=[]))
            params={'mode':'confirm_existing','existing_page_id':'222222222','page_name':'ReMask Page'}
            kwargs=dict(provisioning_state=state,item_id='confirm',profile_id='9',scope_key='confirm')
            await state.set_running('confirm','9','confirm',ProvisioningStep.FAN_PAGES)
            async def uncertain(browser, **options):
                await options['before_submit']({'phase':'PAGE_CONFIRM_CLICK_INTENT','confirm_page_id':'222222222'})
                raise BrowserBusinessError('PAGE_CONFIRM_RESULT_UNKNOWN','lost reply',retryable=True)
            browser=AsyncMock(); browser.__aenter__.return_value=browser
            with patch('app.provisioning.fan_pages_handler.FacebookBusinessBrowser',return_value=browser), \
                 patch('app.facebook_page_confirmation.confirm_main_page',new=AsyncMock(side_effect=uncertain)), \
                 patch('app.provisioning.fan_pages_handler._fresh_page_inventory',new=AsyncMock()) as inventory:
                with self.assertRaises(ProvisioningError): await fan_pages_handler(session,params,{},**kwargs)
                inventory.assert_not_awaited()
            saved=await state.step('confirm',ProvisioningStep.FAN_PAGES)
            self.assertEqual(saved['result']['created_pages'][0]['id'],'222222222')
            self.assertEqual(saved['result']['phase'],'PAGE_CONFIRM_CLICK_INTENT')
            confirm=AsyncMock(return_value={'confirmed':True,'page_id':'222222222','main_business_id':'111111111'})
            with patch('app.provisioning.fan_pages_handler.FacebookBusinessBrowser',return_value=browser), \
                 patch('app.facebook_page_confirmation.confirm_main_page',new=confirm), \
                 patch('app.provisioning.fan_pages_handler._fresh_page_inventory',new=AsyncMock()) as inventory:
                result=await fan_pages_handler(session,params,{},**kwargs)
                inventory.assert_not_awaited()
            self.assertTrue(confirm.await_args.kwargs['verification_only'])
            self.assertTrue(result['main_business_confirmed'])
            self.assertFalse(result['page_business_attached'])
            self.assertFalse(result['ad_account_page_access_verified'])
            self.assertEqual(result['created_count'],0)

    async def test_unknown_profile_page_cannot_reach_confirm_or_create(self):
        state=SimpleNamespace(step=AsyncMock(return_value={}),latest_profile_fan_pages=AsyncMock(return_value=[]))
        session=SimpleNamespace(context=SimpleNamespace(profile_id='9'))
        with patch('app.provisioning.fan_pages_handler.FacebookBusinessBrowser') as browser:
            with self.assertRaises(ProvisioningError) as raised:
                await fan_pages_handler(session,{'mode':'confirm_existing','existing_page_id':'222222222'}, {},
                    provisioning_state=state,item_id='c',profile_id='9',scope_key='c')
            self.assertEqual(raised.exception.code,'CONFIRMED_PAGE_REQUIRED'); browser.assert_not_called()
