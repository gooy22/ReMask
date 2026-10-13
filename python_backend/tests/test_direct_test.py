import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.direct_test import direct_test_deadline, direct_test_result
from app.private_http import PrivateHttpClient
from app.session import ProfileResolver, ProfileSession, ProfileContextError


class DirectTestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '19',
                                         'REMASK_DIRECT_TEST_UNTIL': '1600'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.clock = patch('app.direct_test.time.time', return_value=1000)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    async def resolver(self, profile):
        resolver = ProfileResolver('http://127.0.0.1/fixture', 'fixture-key')
        resolver._fetch = AsyncMock(return_value={'cookies': {'c_user': '123456789', 'xs': 'fixture'},
            'proxy': 'http://127.0.0.1:1', 'user_agent': 'fixture'})
        return await resolver.resolve(profile)

    async def test_one_profile_overrides_context_without_modifying_saved_proxy(self):
        selected = await self.resolver('19')
        other = await self.resolver('20')
        self.assertIsNone(selected.proxy)
        self.assertEqual(selected.direct_test_until, 1600)
        self.assertEqual(other.proxy, 'http://127.0.0.1:1')
        self.assertEqual(other.direct_test_until, 0)
        async with ProfileSession(selected) as session:
            result = await session.proxy_check()
        self.assertEqual(result['mode'], 'DIRECT_TEST')
        self.assertFalse(result['proxy_verified'])

    async def test_expiry_restores_new_context_and_blocks_existing_direct_client(self):
        client = PrivateHttpClient(profile_name='19', cookies={}, proxy=None,
            user_agent='fixture', timeout_seconds=1, pool_size=1)
        self.addAsyncCleanup(client.close)
        client._client.stream = AsyncMock(side_effect=AssertionError('network must stay closed'))
        with patch('app.direct_test.time.time', return_value=1601):
            self.assertEqual((await self.resolver('19')).proxy, 'http://127.0.0.1:1')
            with self.assertRaises(httpx.ProxyError):
                async with client.get('https://www.facebook.com/'):
                    pass
        client._client.stream.assert_not_called()


    async def test_renewed_direct_lease_cannot_reauthorize_existing_profile_session(self):
        with patch.dict(os.environ, {'REMASK_DIRECT_TEST_PROFILE': '21',
                                      'REMASK_DIRECT_TEST_UNTIL': '1600'}):
            context = await self.resolver('21')
        self.assertEqual(context.direct_test_until, 1600)
        async with ProfileSession(context) as session:
            with patch.object(session, '_get_session', new_callable=AsyncMock) as get_session:
                for patch_values in (
                    {'REMASK_DIRECT_TEST_PROFILE': '21', 'REMASK_DIRECT_TEST_UNTIL': '1700'},
                    {'REMASK_DIRECT_TEST_PROFILE': '22', 'REMASK_DIRECT_TEST_UNTIL': '1600'},
                ):
                    with self.subTest(patch_values=patch_values), patch.dict(os.environ, patch_values):
                        with self.assertRaisesRegex(ProfileContextError, 'DIRECT_TEST_EXPIRED'):
                            await session.request('GET', 'https://www.facebook.com/')
                get_session.assert_not_called()

    async def test_unselected_profile_cannot_connect_directly(self):
        client = PrivateHttpClient(profile_name='20', cookies={}, proxy=None,
            user_agent='fixture', timeout_seconds=1, pool_size=1)
        self.addAsyncCleanup(client.close)
        with self.assertRaises(httpx.ProxyError):
            async with client.get('https://www.facebook.com/'):
                pass

    def test_missing_invalid_or_unbounded_deadline_never_allows_direct(self):
        for deadline in ('0', 'invalid', '99999999999', '999'):
            with patch.dict(os.environ, {'REMASK_DIRECT_TEST_UNTIL': deadline}):
                self.assertEqual(direct_test_deadline('19'), 0)
                self.assertIsNone(direct_test_result('19'))

