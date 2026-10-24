"""Exercise the real Sync entry point without contacting Meta or a renderer."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import main as api
from app.session import ProfileContext

BM='111111111111'
RK='222222222222'


class PrivateOnlySyncTests(unittest.IsolatedAsyncioTestCase):
    async def run_sync(self, snapshot):
        context=ProfileContext(profile_id='fixture',cookies={'c_user':'999999999999'},proxy='http://fixture:8080',user_agent='fixture')
        state=SimpleNamespace(**{name:AsyncMock(return_value=default) for name,default in (
            ('confirmed_ad_account_bindings_for_profile',[]),('confirmed_business_page_bindings_for_profile',[]),
            ('latest_profile_fan_pages',[]),('latest_profile_fan_page_batch',{}),('latest_profile_entities',{}))})
        pool=SimpleNamespace(resolver=SimpleNamespace(resolve=AsyncMock(return_value=context)),provisioning_state=state)
        web=SimpleNamespace()
        session=SimpleNamespace(facebook_web=AsyncMock(return_value=web),facebook_business_browser=AsyncMock())
        lease=AsyncMock()
        lease.__aenter__.return_value=session
        read=AsyncMock(return_value=snapshot)
        with patch.object(api,'pool',pool),patch.object(api,'ProfileSession',return_value=lease), \
                patch.object(api,'private_inventory_snapshot',read):
            result=await api.profile_live_inventory('fixture',business_ids=BM,ad_account_hints=BM+':'+RK)
        session.facebook_business_browser.assert_not_awaited()
        self.assertIs(read.call_args.args[0],web)
        self.assertTrue(read.call_args.kwargs['execute_read_queries'])
        self.assertTrue(read.call_args.kwargs['fast_sync'])
        self.assertTrue(web.defer_module_refresh)
        self.assertFalse(read.call_args.kwargs['discover_businesses'])
        self.assertEqual(read.call_args.kwargs['known_business_ids'],{BM})
        self.assertEqual(read.call_args.kwargs['known_accounts_by_business'],{BM:{RK}})
        return result

    async def test_inconclusive_private_inventory_preserves_failure_without_browser(self):
        result=await self.run_sync({'ready':False,'businesses':[],'diagnostics':[{'usable':True}]})
        self.assertFalse(result['live_ready'])
        self.assertFalse(result['browser_started'])
        self.assertFalse(result['business_inventory_confirmed_empty'])
        self.assertEqual(result['warnings'],['PRIVATE_INCONCLUSIVE'])
        self.assertNotIn('page_enrichment',result['timings_ms'])
        for phase in ('profile_context','durable_state','http_session','inventory','total'):
            self.assertIs(type(result['timings_ms'][phase]),int)
            self.assertGreaterEqual(result['timings_ms'][phase],0)

    async def test_auth_wall_remains_auth_failure_without_other_transport(self):
        result=await self.run_sync({'ready':False,'businesses':[{'id':BM,'diagnostics':[{'auth_gate':'CHECKPOINT_REQUIRED'}]}]})
        self.assertEqual(result['auth_blocked_business_codes'],{BM:'CHECKPOINT_REQUIRED'})
        self.assertFalse(result['session_ready'])
        self.assertFalse(result['live_ready'])

    async def test_proxy_failure_stays_a_connection_error(self):
        result=await self.run_sync({'ready':False,'businesses':[],
            'diagnostics':[{'code':'PRIVATE_HTTP_PROXY_UNAVAILABLE'}]})
        self.assertEqual(result['sync_error_kind'],'PRIVATE_HTTP_PROXY_UNAVAILABLE')
        self.assertEqual(result['auth_blocked_business_codes'],{})
        self.assertFalse(result['live_ready'])

    async def test_confirmed_private_inventory_does_not_depend_on_page_enrichment(self):
        snapshot={'ready':True,'discovery_complete':True,'confirmed_business_ids':[BM],
            'businesses':[{'id':BM,'ad_accounts_ready':True,'ad_accounts':[{'id':RK,'business_id':BM}]}]}
        with patch.object(api,'list_pages_via_private_graphql',AsyncMock(side_effect=TimeoutError())):
            result=await self.run_sync(snapshot)
        self.assertTrue(result['live_ready'])
        self.assertFalse(result['pages_live_verified'])
        self.assertEqual(result['businesses'][0]['ad_accounts'][0]['id'],RK)
        self.assertGreaterEqual(result['timings_ms']['page_enrichment'],0)
        self.assertGreaterEqual(result['timings_ms']['total'],result['timings_ms']['inventory'])
