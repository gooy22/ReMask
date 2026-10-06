from __future__ import annotations

import json
import gc
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.facebook_business_browser import BrowserBusinessError
from app.facebook_page_discovery import PageDiscoveryError, _private_page_inventory_complete
from app.provisioning.fan_pages_handler import _reconcile_uncertain_page, fan_pages_handler
from app.provisioning.models import ProvisioningError, ProvisioningStep
from app.provisioning.advertising_page import AdvertisingPageStore
from app.provisioning.service import ProvisioningService
from app.provisioning.state import ProvisioningStateStore


class InventoryCompletenessTests(unittest.TestCase):
    def test_explicit_empty_actor_connection_is_complete(self):
        self.assertTrue(_private_page_inventory_complete(
            {"data": {"userData": {"pages_can_administer": []}}}, "123"))

    def test_generic_data_null_errors_wrong_actor_and_bad_rows_are_inconclusive(self):
        for payload in (
            {"data": {"viewer": {"id": "123"}}},
            {"data": {"userData": {"pages_can_administer": None}}},
            {"data": {"userData": {"pages_can_administer": []}}, "errors": [{"message": "denied"}]},
            {"data": {"userData": {"id": "999", "pages_can_administer": []}}},
            {"data": {"userData": {"pages_can_administer": [{"id": "456"}]}}},
            {"data": {"userData": {"pages_can_administer": [{"id":"456","name":"Business","__typename":"Business"}]}}},
        ):
            with self.subTest(payload=payload):
                self.assertFalse(_private_page_inventory_complete(payload, "123"))

    def test_paginated_connection_requires_explicit_final_page(self):
        connection = {"edges": [], "page_info": {"has_next_page": True}}
        payload = {"data": {"userData": {"pages_can_administer": connection}}}
        self.assertFalse(_private_page_inventory_complete(payload, "123"))
        connection["page_info"]["has_next_page"] = False
        self.assertTrue(_private_page_inventory_complete(payload, "123"))
        connection.pop("page_info")
        self.assertFalse(_private_page_inventory_complete(payload, "123"))


class ReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def session(self, web=None):
        return SimpleNamespace(context=SimpleNamespace(profile_id="10"),
            facebook_web=AsyncMock(return_value=web or object()))

    async def test_bulk_error_copies_actual_uid_page_checkpoint_across_local_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = ProvisioningStateStore(tmp + '/state.sqlite3')
            await state.init()
            context = SimpleNamespace(profile_id='10', cookies={'c_user':'123456789'})
            common_item = 'workspace-common-page-facebook-123456789'
            await AdvertisingPageStore.for_context(state, context).patch(
                creation_item_id=common_item, creation_profile_id='6')
            await state.set_running(common_item,'6','workspace-common-page',ProvisioningStep.FAN_PAGES)
            await state.checkpoint(common_item,'6','workspace-common-page',ProvisioningStep.FAN_PAGES,
                {'phase':'PAGE_CREATE_RESULT_UNKNOWN','active_page_name':'PrgssTeam',
                 'reconciliation':[{'code':'PRIVATE_LIST_PAGES_UNAVAILABLE'}]})
            await state.set_running('bulk-item','10','bulk-scope',ProvisioningStep.FAN_PAGES)
            with patch('app.provisioning.advertising_page.ensure_common_page',
                       AsyncMock(side_effect=ProvisioningError('FAN_PAGE_CREATE_RESULT_UNKNOWN','pending'))):
                with self.assertRaises(ProvisioningError):
                    await ProvisioningService(state)._run_handler(None,ProvisioningStep.FAN_PAGES,
                        SimpleNamespace(context=context),{'common_page':True},{},
                        item_id='bulk-item',profile_id='10',scope_key='bulk-scope')
            result = (await state.step('bulk-item',ProvisioningStep.FAN_PAGES))['result']
            self.assertEqual(result['phase'],'PAGE_CREATE_RESULT_UNKNOWN')
            self.assertEqual(result['common_page_creation_item_id'],common_item)
            self.assertEqual(result['reconciliation'][0]['code'],'PRIVATE_LIST_PAGES_UNAVAILABLE')
            gc.collect()

    async def test_initial_cookie_html_recovers_page_without_spa_or_docid(self):
        document = '<script type="application/json">' + json.dumps({"data": {"viewer": {
            "additional_profiles_with_biz_tools": {"edges": [{"node": {
                "id": "99887766", "name": "PrgssTeam", "delegate_page": {
                    "__typename": "Page", "id": "123456789", "name": "PrgssTeam"}}}]}}}}) + '</script>'
        web = SimpleNamespace(fetch_text=AsyncMock(return_value=(200, document, "https://www.facebook.com/")))
        with patch('app.provisioning.fan_pages_handler._fresh_page_inventory',
                   AsyncMock(side_effect=BrowserBusinessError('FAN_PAGES_NOT_DISCOVERED', 'SPA did not hydrate'))), \
             patch('app.provisioning.fan_pages_handler.list_pages_via_private_graphql',
                   AsyncMock(side_effect=PageDiscoveryError('No current doc_id'))):
            found, absent, diagnostics = await _reconcile_uncertain_page(
                self.session(web), page_name="PrgssTeam", before_ids=set())
        self.assertEqual(found['id'], '123456789')
        self.assertFalse(absent)
        self.assertEqual(diagnostics[-1]['source'], 'facebook_browser_pages_html')
        web.fetch_text.assert_awaited_once()

    async def test_partial_private_inventory_never_releases_guard(self):
        result = SimpleNamespace(pages=[], source='facebook_web_graphql', diagnostics=[], inventory_complete=False)
        with patch('app.provisioning.fan_pages_handler._fresh_page_inventory', AsyncMock(return_value=[])), \
             patch('app.provisioning.fan_pages_handler.list_pages_via_private_graphql', AsyncMock(return_value=result)), \
             patch('app.provisioning.fan_pages_handler.discover_pages_from_browser_html',
                   AsyncMock(side_effect=PageDiscoveryError('unavailable'))) as html, \
             patch('app.provisioning.fan_pages_handler.asyncio.sleep', AsyncMock()):
            found, absent, _ = await _reconcile_uncertain_page(self.session(), page_name='PrgssTeam', before_ids=set())
        self.assertIsNone(found)
        self.assertFalse(absent)
        html.assert_awaited_once()

    async def test_ambiguous_same_name_blocks_absence_even_when_private_list_is_empty(self):
        result = SimpleNamespace(pages=[], source='facebook_web_graphql', diagnostics=[], inventory_complete=True)
        with patch('app.provisioning.fan_pages_handler._fresh_page_inventory', AsyncMock(return_value=[
                    {'id':'123456', 'name':'PrgssTeam'}, {'id':'654321', 'name':'PrgssTeam'}])), \
             patch('app.provisioning.fan_pages_handler.list_pages_via_private_graphql', AsyncMock(return_value=result)), \
             patch('app.provisioning.fan_pages_handler.discover_pages_from_browser_html',
                   AsyncMock(side_effect=PageDiscoveryError('unavailable'))), \
             patch('app.provisioning.fan_pages_handler.asyncio.sleep', AsyncMock()):
            found, absent, _ = await _reconcile_uncertain_page(self.session(), page_name='PrgssTeam', before_ids=set())
        self.assertIsNone(found)
        self.assertFalse(absent)

    async def test_checkpoint_is_reported_and_pending_create_is_preserved(self):
        state = SimpleNamespace(checkpoint=AsyncMock())
        session = self.session()
        with patch('app.provisioning.fan_pages_handler._fresh_page_inventory',
                   AsyncMock(side_effect=BrowserBusinessError('CHECKPOINT_REQUIRED', 'challenge'))) as inventory:
            with self.assertRaises(ProvisioningError) as caught:
                await fan_pages_handler(session, {'names':['PrgssTeam']}, {}, provisioning_state=state,
                    item_id='workspace-common-page-facebook-123', profile_id='10', scope_key='workspace-common-page',
                    step_state={'result': {'phase':'PAGE_CREATE_RESULT_UNKNOWN',
                        'active_page_name':'PrgssTeam','active_before_ids':[],'target_names':['PrgssTeam']}})
        self.assertEqual(caught.exception.code, 'CHECKPOINT_REQUIRED')
        self.assertTrue(caught.exception.retryable)
        inventory.assert_awaited_once()
        session.facebook_web.assert_not_awaited()
        saved = state.checkpoint.await_args.args[-1]
        self.assertEqual(saved['reconciliation'][0]['code'], 'CHECKPOINT_REQUIRED')
        self.assertNotIn('phase', saved)

    async def test_failed_reconciliation_is_saved_under_common_page_item(self):
        state = SimpleNamespace(checkpoint=AsyncMock())
        diagnostics = [{'attempt':1,'source':'your_pages','result':'unavailable','code':'FAN_PAGES_NOT_DISCOVERED'}]
        with patch('app.provisioning.fan_pages_handler._reconcile_uncertain_page',
                   AsyncMock(return_value=(None,False,diagnostics))):
            with self.assertRaises(ProvisioningError) as caught:
                await fan_pages_handler(self.session(), {'names':['PrgssTeam']}, {}, provisioning_state=state,
                    item_id='workspace-common-page-facebook-123', profile_id='10', scope_key='workspace-common-page',
                    step_state={'result':{'phase':'PAGE_CREATE_RESULT_UNKNOWN','active_page_name':'PrgssTeam'}})
        self.assertEqual(caught.exception.code, 'FAN_PAGE_CREATE_RESULT_UNKNOWN')
        self.assertEqual(state.checkpoint.await_args.args[0], 'workspace-common-page-facebook-123')
        self.assertEqual(state.checkpoint.await_args.args[-1]['reconciliation'], diagnostics)


if __name__ == '__main__':
    unittest.main()
