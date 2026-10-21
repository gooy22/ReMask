"""Explicit retained-DRAFT publication through the real Launch journal."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixtures


class SavedDraftPublicationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch

    async def draft(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        result = await self.launch()
        self.assertTrue(result['draft_verified'])
        self.assertEqual(len(self.client.posts), 4)
        return result

    async def test_explicit_publication_reuses_all_draft_and_media_ids(self):
        original = await self.draft()
        intent = copy.deepcopy(self.payload['launch'])
        self.payload['publish_saved_draft'] = True
        with patch('app.static_payment_read.inspect_methods', new=AsyncMock(return_value=self.funding)) as payment:
            result = await self.launch()
        self.assertTrue(result['published'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['execution_mode'], 'PUBLISH')
        self.assertEqual(result['draft_id'], original['draft_id'])
        self.assertEqual(result['entities']['campaign_id'], original['draft_objects']['campaign'])
        self.assertEqual(result['entities']['adset_id'], original['draft_objects']['ad_set'])
        self.assertEqual(result['entities']['ad_id'], original['draft_objects']['ad'])
        self.assertEqual(result['entities']['creative_id'], '777774')
        self.assertEqual(self.payload['launch'], intent)
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)
        payment.assert_awaited_once()
        await self.launch()
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)

    async def test_missing_saved_draft_cannot_create_assets_under_publish_action(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        self.payload['publish_saved_draft'] = True
        self.client.get = AsyncMock(side_effect=AssertionError('No live read'))
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        self.client.get.assert_not_awaited()
        self.assertEqual(self.client.posts, [])
        self.assertEqual(self.client.uploads, 0)

    async def test_rejected_ad_cannot_be_promoted_even_with_accepted_receipt(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        original = self.client.get
        async def rejected(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad':
                row['validation_status'] = 'HAS_ERRORS'
            return row
        self.client.get = rejected
        with self.assertRaises(ProvisioningError):
            await self.launch()
        self.payload['publish_saved_draft'] = True
        with patch('app.static_payment_read.inspect_methods', new=AsyncMock()) as payment:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_SAVED_OPERATION_MISMATCH')
        payment.assert_not_awaited()
        self.assertEqual(len(self.client.posts), 4)

    async def test_payment_confirmation_blocks_promotion_without_recreating_draft(self):
        await self.draft()
        self.payload['publish_saved_draft'] = True
        self.funding['payment_methods'][0]['needs_verification'] = True
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_PAYMENT_CONFIRMATION_REQUIRED')
        self.assertEqual(len(self.client.posts), 4)
        self.funding['payment_methods'][0]['needs_verification'] = False
        self.assertTrue((await self.launch())['published'])
        self.assertEqual(len(self.client.posts), 5)

    async def test_unknown_publication_reply_never_replays_retained_draft(self):
        await self.draft()
        self.payload['publish_saved_draft'] = True
        original = self.client.post
        async def lost(path, params, **options):
            result = await original(path, params, **options)
            if path.endswith('/publish'):
                raise TimeoutError('publication accepted; reply lost')
            return result
        self.client.post = lost
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_RECONCILE_REQUIRED')
        self.assertEqual(len(self.client.posts), 5)
        self.assertEqual(self.client.uploads, 1)

    async def test_fresh_ad_error_blocks_publication_after_prior_draft_validation(self):
        await self.draft()
        self.payload['publish_saved_draft'] = True
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad':
                row['validation_status'] = 'HAS_ERRORS'
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        self.assertEqual(len(self.client.posts), 4)

    async def test_promotion_flag_requires_true_and_original_draft_mode(self):
        for value in (False, 'true', 1, None):
            self.payload['publish_saved_draft'] = value
            with self.subTest(value=value), self.assertRaises(ProvisioningError):
                await self.launch()
        self.assertEqual(self.client.posts, [])
