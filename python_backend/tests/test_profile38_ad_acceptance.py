"""Profile-38 request shape and rejected-Ad recovery, entirely offline.

1359188 is an observed code, not a locally assigned error meaning. The fixture
retains only the observed diagnostic vocabulary, not a fabricated Meta message.
"""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from app.private_launch import PrivateLaunchStateStore
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture
from tests.test_private_ads_objectives import configuration


class Profile38AdAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LaunchTests.asyncSetUp
    launch = fixture.LaunchTests.launch

    async def test_validated_parents_and_rejected_ad_survive_restart_without_new_hierarchy(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        selected = configuration()
        self.payload['launch']['campaign'].update(selected['campaign'])
        self.payload['launch']['adset'].update(selected['adset'])
        self.payload['launch']['creative'].update(description='Complete draft, no delivery.',
            call_to_action='LEARN_MORE', url_tags='source=retained-test')
        original_payload = copy.deepcopy(self.payload)
        original_get = self.client.get

        async def rejected_ad(path, params):
            row = await original_get(path, params)
            if row.get('ad_object_type') == 'ad':
                row.update(validation_status='HAS_ERRORS',
                    active_errors=[{'error_code': 1359188, 'message': 'billing'}])
            return row

        self.client.get = rejected_ad
        with patch('app.static_payment_read.inspect_methods',
                   new=AsyncMock(side_effect=AssertionError('DRAFT must not inspect payment'))) as payment:
            for _ in range(2):
                with self.assertRaises(ProvisioningError) as caught:
                    await self.launch()
                self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
                self.owner.state = PrivateLaunchStateStore(self.owner.state.path)
                await self.owner.state.init()
            payment.assert_not_called()
        self.assertEqual(self.payload, original_payload)
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(len(self.client.posts), 4)
        fragments = {row['ad_object_type']: row for row in self.client.fragments.values()}
        values = {level: {entry['field']: entry['new_value'] for entry in row['values']}
                  for level, row in fragments.items()}
        campaign, adset, ad = (values[level] for level in ('campaign', 'ad_set', 'ad'))
        self.assertEqual(campaign['bid_strategy'], 'LOWEST_COST_WITHOUT_CAP')
        self.assertNotIn('bid_amount', campaign)
        self.assertNotIn('bid_amount', adset)
        self.assertEqual(adset['campaign_id'], fragments['campaign']['ad_object_id'])
        self.assertEqual(ad['campaign_id'], fragments['campaign']['ad_object_id'])
        self.assertEqual(ad['adset_id'], fragments['ad_set']['ad_object_id'])
        self.assertEqual(adset['targeting'], selected['adset']['targeting'])
        self.assertEqual(adset['optimization_goal'], 'LINK_CLICKS')
        self.assertEqual(adset['billing_event'], 'IMPRESSIONS')
        creative = ad['creative']
        story = creative['object_story_spec']
        self.assertEqual(story['page_id'], self.payload['page_id'])
        link = story['link_data']
        selected_creative = self.payload['launch']['creative']
        self.assertEqual(link['image_hash'], 'fixture-image-hash')
        for field, source in [('message', 'message'), ('link', 'link'),
                              ('name', 'headline'), ('description', 'description')]:
            self.assertEqual(link[field], selected_creative[source])
        self.assertEqual(link['call_to_action']['type'], 'LEARN_MORE')
        self.assertEqual(creative['url_tags'], selected_creative['url_tags'])
        for level in values:
            self.assertEqual(values[level]['status'], 'PAUSED')
        before = copy.deepcopy(await self.owner.state.all_steps('image-launch'))
        self.payload['reconcile_only'] = True
        with patch.object(self.client, 'post', new=AsyncMock(side_effect=AssertionError('No POST'))) as post:
            inspected = await self.launch()
        post.assert_not_called()
        self.assertEqual([r['validation_status'] for r in inspected['fragment_reads']],
                         ['VALIDATED', 'VALIDATED', 'HAS_ERRORS'])
        diagnostic = inspected['fragment_reads'][2]['active_error_diagnostic']
        self.assertEqual(diagnostic['codes'], [{'field': 'error_code', 'value': 1359188}])
        self.assertEqual(diagnostic['terms'], ['billing'])
        self.assertEqual(inspected['mutation_posts'], 0)
        self.assertFalse(inspected['editor_verified'])
        self.assertIsNone(inspected['published'])
        self.assertEqual(await self.owner.state.all_steps('image-launch'), before)
