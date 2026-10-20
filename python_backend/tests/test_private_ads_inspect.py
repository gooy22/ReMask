import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture


class SavedDraftInspectionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LaunchTests.asyncSetUp
    launch = fixture.LaunchTests.launch

    async def saved(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        await self.launch()
        self.payload['reconcile_only'] = True

    async def test_fresh_reads_of_saved_hierarchy_never_upload_publish_or_activate(self):
        await self.saved()
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post, \
                patch('app.static_payment_read.inspect_methods', new=AsyncMock(side_effect=AssertionError('No billing'))) as payment:
            result = await self.launch()
        post.assert_not_called(); payment.assert_not_called()
        self.assertEqual(result['mutation_posts'], 0)
        self.assertEqual(result['execution_mode'], 'DRAFT_INSPECT')
        self.assertEqual(result['draft_objects'], {'campaign':'777771', 'ad_set':'777772', 'ad':'777773'})
        self.assertTrue(result['fragment_storage_verified'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(result['fragment_reads'][0]['submitted_values']['objective'], 'OUTCOME_TRAFFIC')
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)

    async def test_invalid_scope_actor_configuration_or_missing_key_fails_before_open(self):
        await self.saved()
        original = copy.deepcopy(self.payload)
        for change in ({'launch_key':'unknown'}, {'page_id':'555556'}, {'business_id':'222223'},
                       {'ad_account_id':'111112'}, {'reconcile_only':'true'}, {'reconcile_only':False}):
            self.payload = {**copy.deepcopy(original), **change}
            with self.subTest(change=change), patch('app.private_ads_inspect.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
                with self.assertRaises(ProvisioningError):
                    await self.launch()
                opened.assert_not_called()
        self.payload = original
        self.context.cookies['c_user'] = '123457'
        with patch('app.private_ads_inspect.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
            with self.assertRaises(ProvisioningError):
                await self.launch()
            opened.assert_not_called()

    async def test_historical_draft_inspection_is_read_only_and_cannot_enable_old_mutations(self):
        from app.private_ads_inspect import READ_ONLY_DRAFT_PREDECESSORS
        current = self.owner.ads_manager.source_digest
        self.owner.ads_manager.source_digest = next(iter(READ_ONLY_DRAFT_PREDECESSORS))
        await self.saved()
        self.owner.ads_manager.source_digest = current
        result = await self.launch()
        self.assertEqual(result['recovered_source_digest'], next(iter(READ_ONLY_DRAFT_PREDECESSORS)))
        self.payload.pop('reconcile_only')
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 4)

    async def test_changed_or_foreign_fresh_fragment_cannot_pass(self):
        await self.saved()
        self.client.foreign = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SCOPE_MISMATCH')
        self.client.foreign = False
        self.client.fragments['222222']['values'][0]['new_value'] = 'wrong-parent'
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.assertEqual(len(self.client.posts), 4)

    async def test_meta_validation_error_is_recorded_without_claiming_editability(self):
        await self.saved()
        self.client.fragments['222222']['active_errors'] = [{'code':100}]
        self.client.fragments['222222']['validation_status'] = 'unrecognized-status'
        result = await self.launch()
        self.assertTrue(result['fragment_reads'][1]['active_errors_present'])
        self.assertFalse(result['editor_verified'])
        self.assertIsNone(result['published'])
        self.assertEqual(len(self.client.posts), 4)

    async def test_changed_ad_identity_cannot_replace_previously_confirmed_object(self):
        await self.saved()
        self.client.fragments['333333']['ad_object_id'] = '777779'
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_RELATION_MISMATCH')
        self.assertEqual(len(self.client.posts), 4)
