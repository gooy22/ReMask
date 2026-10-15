import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
import main as api
from fb_worker import ProxyError


class PrivateAssetRefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context=SimpleNamespace(cookies={'c_user':'999999'},ad_accounts=[
            {'ad_account_id':'111111','business_id':'222222'}])
        self.web=SimpleNamespace(private_only=False)
        session=SimpleNamespace(facebook_web=AsyncMock(return_value=self.web))
        self.factory=MagicMock()
        self.factory.return_value.__aenter__=AsyncMock(return_value=session)
        self.factory.return_value.__aexit__=AsyncMock(return_value=False)
        self.resources=SimpleNamespace(get=AsyncMock())
        self.cached={'data':[{'id':'333333','name':'Last confirmed'}], 'account_id':'111111',
            'checked_live':False,'account_scope_verified':False,'_cache':{'hit':True,'stale':False}}
        self.live={**self.cached,'checked_live':True,'account_scope_verified':True,'_cache':{'stale':False}}
        for patcher in (patch.object(api.pool.resolver,'resolve',AsyncMock(return_value=self.context)),
                patch.object(api.pool,'private_launch',SimpleNamespace(_id=lambda value,name:value,resources=self.resources)),
                patch.object(api,'ProfileSession',self.factory)):
            patcher.start();self.addCleanup(patcher.stop)

    async def test_fresh_cache_does_not_open_transport_or_claim_live_permissions(self):
        self.resources.get.return_value=self.cached
        result=await api.private_advertising_resources('20','custom_audiences','111111')
        self.assertFalse(result['checked_live']);self.factory.assert_not_called()
        self.resources.get.assert_awaited_once()

    async def test_cache_miss_performs_first_private_read_automatically(self):
        self.resources.get.side_effect=[{'availability':'NOT_LOADED','data':[]},self.live]
        result=await api.private_advertising_resources('20','custom_audiences','111111')
        self.assertTrue(result['checked_live']);self.assertTrue(self.web.private_only)
        args=self.resources.get.await_args.kwargs
        self.assertTrue(args['force']);self.assertEqual(args['actor_id'],'999999')
        self.assertEqual(args['values']['business_id'],'222222')
        self.assertEqual(args['values']['ad_account_id'],'111111')

    async def test_stale_metadata_is_refreshed_automatically(self):
        self.cached['_cache']['stale']=True
        self.resources.get.side_effect=[self.cached,self.live]
        result=await api.private_advertising_resources('20','pages','111111')
        self.assertTrue(result['checked_live']);self.assertEqual(self.resources.get.await_count,2)

    async def test_failed_automatic_refresh_retains_non_live_metadata(self):
        self.cached['_cache']['stale']=True
        self.resources.get.side_effect=[self.cached,ProxyError('SECRET')]
        result=await api.private_advertising_resources('20','pages','111111')
        self.assertEqual(result['data'],self.cached['data'])
        self.assertFalse(result['checked_live']);self.assertFalse(result['account_scope_verified'])
        self.assertTrue(result['_cache']['refresh_failed']);self.assertNotIn('SECRET',str(result))

    async def test_unavailable_first_read_is_an_error_not_an_empty_inventory(self):
        self.resources.get.side_effect=[{'availability':'NOT_LOADED','data':[]},ProxyError('SECRET')]
        with self.assertRaises(HTTPException) as raised:
            await api.private_advertising_resources('20','custom_audiences','111111')
        self.assertEqual(raised.exception.detail['code'],'PRIVATE_HTTP_PROXY_UNAVAILABLE')

    async def test_explicit_refresh_does_not_hide_failure_behind_old_metadata(self):
        self.resources.get.side_effect=ProxyError('SECRET')
        with self.assertRaises(HTTPException):
            await api.private_advertising_resources('20','pages','111111',force=True)
        self.resources.get.assert_awaited_once()

    async def test_foreign_account_is_rejected_before_cache_or_transport(self):
        with self.assertRaises(HTTPException):
            await api.private_advertising_resources('20','pages','444444')
        self.resources.get.assert_not_awaited();self.factory.assert_not_called()
