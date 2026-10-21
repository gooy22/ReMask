"""Offline accepted-publication recovery across reviewed sender upgrades."""
import copy
import json
import unittest

from app.private_launch import PrivateLaunchService, PrivateLaunchContractStore
from app.private_launch_extensions import OperationKey
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixtures

# Computed from exact main b23917a and PR #229 head 638d773 source trees.
REVIEWED_NATIVE_SENDERS = (
    'cb9b5fd76c6b9079d9584818a085848df404239437989499cd27f284f11e9353',
    '0ddf56b2f696365f7cad55886598a7dc95a1a89ba021839f0fb669df4add2c62',
)


class PublicationUpgradeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch

    async def accepted_native_publication(self, source):
        self.owner.ads_manager.source_digest = source
        original = self.client.get
        async def interrupted(path, params):
            if path == '/777771':
                raise ConnectionError('publication accepted before upgrade')
            return await original(path, params)
        self.client.get = interrupted
        with self.assertRaises(ConnectionError):
            await self.launch()
        contracts = PrivateLaunchContractStore('{}')
        contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        self.client.get = original

    async def verify_native_recovery(self, source):
        await self.accepted_native_publication(source)
        campaign = await self.owner.state.step('image-launch', OperationKey('ADS_MANAGER:FRAGMENT_CAMPAIGN'))
        saved = copy.deepcopy(campaign['result']['evidence']['binding']['values'])
        self.assertEqual(saved['objective'], 'LINK_CLICKS')
        self.assertIs(saved['is_odax_campaign_group'], True)
        original = self.client.get
        reads = []
        async def get(path, params):
            reads.append(path)
            if path == '/addraft_555555' or path.startswith('/addraftfragment_'):
                raise AssertionError('published draft fragments may have been consumed')
            return await original(path, params)
        self.client.get = get
        result = await self.launch()
        self.assertTrue(result['verified'])
        self.assertTrue(result['recovery_read_only'])
        self.assertTrue(self.client.read_only)
        self.assertTrue({'/777771', '/777772', '/777773', '/777774'} <= set(reads))
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)
        after = await self.owner.state.step('image-launch', OperationKey('ADS_MANAGER:FRAGMENT_CAMPAIGN'))
        self.assertEqual(after['result']['evidence']['binding']['values'], saved)

    async def test_main_228_native_publication_survives_upgrade(self):
        await self.verify_native_recovery(REVIEWED_NATIVE_SENDERS[0])

    async def test_main_229_native_publication_survives_upgrade(self):
        await self.verify_native_recovery(REVIEWED_NATIVE_SENDERS[1])

    async def test_changed_native_binding_is_not_trusted_as_an_old_receipt(self):
        await self.accepted_native_publication(REVIEWED_NATIVE_SENDERS[0])
        with self.owner.state._connect() as con:
            row = con.execute('SELECT result_json FROM private_launch_steps WHERE launch_key=? AND step=?',
                ('image-launch', 'ADS_MANAGER:FRAGMENT_CAMPAIGN')).fetchone()
            saved = json.loads(row['result_json'])
            saved['evidence']['binding']['values']['daily_budget'] += 1
            con.execute('UPDATE private_launch_steps SET result_json=? WHERE launch_key=? AND step=?',
                (json.dumps(saved), 'image-launch', 'ADS_MANAGER:FRAGMENT_CAMPAIGN'))
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 5)

    async def test_reviewed_source_cannot_resume_old_unpublished_draft_mutations(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        self.owner.ads_manager.source_digest = REVIEWED_NATIVE_SENDERS[0]
        await self.launch()
        contracts = PrivateLaunchContractStore('{}')
        contracts.protocol = 'private_ads_manager_versioned'
        self.owner = PrivateLaunchService(self.state, contracts=contracts)
        before = len(self.client.posts)
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), before)
        self.assertEqual(before, 4)
