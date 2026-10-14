"""Creative/Launch private reads report transport failures without secrets or retries."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
import main as api
from fb_worker import AuthenticationError, RemoteRequestError, ProxyError


class PrivateAdvertisingErrorTests(unittest.IsolatedAsyncioTestCase):
    async def check_routes(self, error, status, code):
        context = SimpleNamespace(cookies={'c_user':'123456789'},
            ad_accounts=[{'ad_account_id':'123456789','business_id':'987654321'}])
        web = SimpleNamespace(private_only=False)
        session = SimpleNamespace(facebook_web=AsyncMock(return_value=web))
        factory = MagicMock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock(return_value=False)
        resources = SimpleNamespace(get=AsyncMock(side_effect=error))
        launch = SimpleNamespace(_id=lambda value, name:value, resources=resources,
                                 ads_manager=SimpleNamespace(catalog=object()))
        helper = AsyncMock(side_effect=error)
        with patch.object(api.pool.resolver,'resolve',AsyncMock(return_value=context)), \
             patch.object(api.pool,'private_launch',launch), \
             patch.object(api,'ProfileSession',factory), \
             patch('app.private_ads_helpers.read_form_helper',helper):
            for route in ('resource','helper'):
                with self.subTest(route=route), self.assertRaises(HTTPException) as caught:
                    if route == 'resource':
                        await api.private_advertising_resources('19','interests','123456789',force=True)
                    else:
                        await api.private_advertising_helper('19','placements',{'account_id':'123456789'})
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.detail['code'], code)
                self.assertNotIn('SECRET',str(caught.exception.detail))
                self.assertNotIn('http://',str(caught.exception.detail))
        resources.get.assert_awaited_once()
        helper.assert_awaited_once()
        self.assertTrue(web.private_only)

    async def test_proxy_failure_is_not_session_expiry_or_unhandled_500(self):
        await self.check_routes(RemoteRequestError('http://SECRET:SECRET@example.test',
            meta_payload={'transport_failure':{'kind':'ProxyError','proxy_url':'SECRET'}}),
            502,'PRIVATE_HTTP_PROXY_UNAVAILABLE')

    async def test_timeouts_have_safe_typed_response(self):
        await self.check_routes(RemoteRequestError('https://facebook.com/?token=SECRET',
            meta_payload={'transport_failure':{'kind':'ReadTimeout'}}),504,'PRIVATE_HTTP_READ_TIMEOUT')

    async def test_network_failure_is_not_cookie_failure(self):
        await self.check_routes(RemoteRequestError('SECRET',
            meta_payload={'transport_failure':{'kind':'ConnectError'}}),502,'PRIVATE_HTTP_CONNECTION_UNAVAILABLE')

    async def test_protocol_failure_keeps_http_two_requirement(self):
        await self.check_routes(RemoteRequestError('SECRET',
            meta_payload={'transport_failure':{'kind':'RemoteProtocolError'}}),502,'PRIVATE_HTTP_PROTOCOL_ERROR')

    async def test_unknown_remote_error_does_not_reflect_untrusted_message(self):
        await self.check_routes(RemoteRequestError('SECRET'),502,'PRIVATE_HTTP_READ_UNAVAILABLE')

    async def test_direct_proxy_error_is_typed(self):
        await self.check_routes(ProxyError('SECRET'),502,'PRIVATE_HTTP_PROXY_UNAVAILABLE')

    async def test_authentication_failure_retains_session_code(self):
        await self.check_routes(AuthenticationError('SECRET'),409,'SESSION_EXPIRED')
