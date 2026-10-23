"""Exact account-owned resource preflight through the production Launch service."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixtures


class NestedAudienceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch

    def configure(self):
        target = self.payload['launch']['adset']['targeting']
        target.update(custom_audiences=[{'id': '123451'}],
            flexible_spec=[{'custom_audiences': [{'id': '123452'}],
                            'interests': [{'id': '123456'}]},
                           {'behaviors': [{'id': '654321'}]}],
            exclusions={'custom_audiences': [{'id': '123453'}, {'id': '123452'}]})
        return copy.deepcopy(target)

    async def test_every_nested_audience_requires_exact_account_inventory_before_upload(self):
        expected = self.configure()
        inventory = {'data': [{'id': '123451'}], 'checked_live': True, 'account_scope_verified': True}
        with patch.object(self.owner.reads, 'read', new=AsyncMock(return_value=inventory)) as read:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_ASSET_ACCESS_UNCONFIRMED')
        self.assertEqual(read.await_count, 1)
        self.assertEqual(read.await_args.args[1], 'custom_audiences')
        self.assertEqual(read.await_args.args[2]['ad_account_id'], '111111')
        self.assertEqual(read.await_args.args[2]['business_id'], '222222')
        self.assertEqual(self.payload['launch']['adset']['targeting'], expected)
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

    async def test_confirmed_nested_resources_keep_logic_and_are_read_once(self):
        expected = self.configure()
        from time import time
        inventory = {'data': [{'id': value, 'name': 'Selected audience ' + value}
                              for value in ('123451', '123452', '123453')],
                     'account_id': '111111', 'source': 'ads_manager_private_http',
                     'checked_at': int(time()), 'checked_live': True,
                     'account_scope_verified': True, 'inventory_complete': True}
        selected = self.owner._asset_selections(self.payload['launch'], True)
        self.assertEqual(set(selected['custom_audiences']), {'123451', '123452', '123453'})
        self.assertEqual(len(selected['custom_audiences']), 3)
        with patch.object(self.owner.reads, 'read', new=AsyncMock(return_value=inventory)) as read:
            self.assertTrue((await self.launch())['verified'])
        read.assert_awaited_once()
        adset = next(row for row in self.client.fragments.values() if row['ad_object_type'] == 'ad_set')
        actual = next(row['new_value'] for row in adset['values'] if row['field'] == 'targeting')
        self.assertEqual(actual, expected)
        self.assertEqual(self.payload['launch']['adset']['targeting'], expected)

    async def test_invalid_nested_resource_type_fails_before_any_live_read(self):
        self.payload['launch']['adset']['targeting']['exclusions'] = {'custom_audiences': {'id': '123453'}}
        self.client.get = AsyncMock(side_effect=AssertionError('No Meta read'))
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_LAUNCH_ASSET_INVALID')
        self.client.get.assert_not_awaited()
        self.assertEqual(self.client.posts, [])
