"""Offline Launch/SQLite integration; Meta responses are simulated explicitly."""
import copy
import json
import unittest

from app.private_ads_draft import fragment_values_match
from app.private_launch import PrivateLaunchStateStore
from app.private_launch_extensions import OperationKey
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixtures
from tests import test_private_ads_delivery_lifecycle as delivery_fixtures


def selected_targeting():
    return {
        'geo_locations': {'countries': ['UA'], 'regions': [{'key': '123456'}],
                          'cities': [{'key': '234567', 'radius': 15, 'distance_unit': 'kilometer'}]},
        'excluded_geo_locations': {'countries': ['RU']},
        'age_min': 25, 'age_max': 55, 'genders': [1], 'locales': [52, 1000],
        'flexible_spec': [
            {'interests': [{'id': '123456', 'name': 'First'}, {'id': '234567', 'name': 'Second'}]},
            {'behaviors': [{'id': '654321', 'name': 'Behavior'}], 'education_statuses': [1, 2]}],
        'exclusions': {'interests': [{'id': '345678', 'name': 'Excluded'}]},
        'publisher_platforms': ['facebook', 'instagram'], 'facebook_positions': ['feed'],
        'instagram_positions': ['story'], 'device_platforms': ['mobile'],
        'user_os': ['Android'], 'user_device': ['Samsung Galaxy S23'],
    }


class ExactAdvertisingSettingsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch

    def configure(self):
        self.payload['launch']['adset'].update(
            targeting=selected_targeting(),
            attribution_spec=[{'event_type': 'CLICK_THROUGH', 'window_days': 1}],
            start_time='2026-11-01T09:00:00+00:00', end_time='2026-11-07T18:30:00+00:00')
        self.payload['launch']['creative'].update(
            message='Primary text ✓', headline='Headline ✓', description='Description ✓',
            link='https://example.com/selected?a=1', url_tags='utm_source=selected', call_to_action='LEARN_MORE')

    async def test_complete_selected_values_survive_draft_promotion_and_restart(self):
        self.configure()
        self.payload['launch']['execution_mode'] = 'DRAFT'
        intent = copy.deepcopy(self.payload)
        draft = await self.launch()
        self.assertTrue(draft['draft_verified'])
        self.assertFalse(draft['published'])
        self.assertEqual(self.payload, intent)
        self.payload['publish_saved_draft'] = True
        result = await self.launch()
        self.assertTrue(result['published'])
        self.assertTrue(result['verified'])
        self.assertFalse(result['delivery_running_verified'])
        adset = next(row for row in self.client.fragments.values() if row['ad_object_type'] == 'ad_set')
        values = {row['field']: row['new_value'] for row in adset['values']}
        for field, expected in intent['launch']['adset'].items():
            self.assertEqual(values[field], expected, field)
        creative = self.client.expected_creative
        link = creative['object_story_spec']['link_data']
        self.assertEqual((link['message'], link['name'], link['description']),
                         ('Primary text ✓', 'Headline ✓', 'Description ✓'))
        self.assertEqual(link['link'], intent['launch']['creative']['link'])
        self.assertEqual(link['call_to_action'], {'type': 'LEARN_MORE'})
        self.assertEqual(creative['url_tags'], 'utm_source=selected')
        self.assertEqual(creative['object_story_spec']['page_id'], self.payload['page_id'])
        before = len(self.client.posts)
        self.owner.state = PrivateLaunchStateStore(self.owner.state.path)
        self.assertTrue((await self.launch())['verified'])
        self.assertEqual(len(self.client.posts), before)
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(before, 5)

    async def test_per_account_override_replaces_nested_audience_conditions_in_real_draft(self):
        self.configure()
        self.payload['launch']['execution_mode'] = 'DRAFT'
        base = copy.deepcopy(self.payload['launch'])
        base['adset']['targeting']['exclusions'] = {
            'interests': [{'id': '111111'}], 'behaviors': [{'id': '222222'}]}
        override_targeting = {
            'exclusions': {'interests': [{'id': '333333'}]},
            'geo_locations': {'countries': ['PL']},
            'flexible_spec': [{'interests': [{'id': '444444'}]}],
        }
        self.payload['launch'] = {
            'base': base, 'override': {'adset': {'targeting': override_targeting}}}
        original = copy.deepcopy(self.payload)
        result = await self.launch()
        self.assertTrue(result['draft_verified'])
        self.assertEqual(self.payload, original)
        adset = next(row for row in self.client.fragments.values()
                     if row['ad_object_type'] == 'ad_set')
        target = next(row['new_value'] for row in adset['values']
                      if row['field'] == 'targeting')
        for key, selected in override_targeting.items():
            self.assertEqual(target[key], selected, key)
        # Independent fields not overridden stay inherited.
        self.assertEqual(target['excluded_geo_locations'], base['adset']['targeting']['excluded_geo_locations'])
        self.assertEqual(target['age_min'], 25)
        before = len(self.client.posts)
        self.owner.state = PrivateLaunchStateStore(self.owner.state.path)
        self.assertTrue((await self.launch())['draft_verified'])
        self.assertEqual(len(self.client.posts), before)

    async def test_added_nested_targeting_condition_blocks_fragment_publication(self):
        self.configure()
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad_set':
                target = next(item['new_value'] for item in row['values'] if item['field'] == 'targeting')
                target['flexible_spec'][0]['behaviors'] = [{'id': '999999'}]
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH')
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))
        self.assertFalse(any(row['ad_object_type'] == 'ad' for row in self.client.fragments.values()))

    async def test_added_inline_creative_destination_blocks_fragment_publication(self):
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if row.get('ad_object_type') == 'ad':
                creative = next(item['new_value'] for item in row['values'] if item['field'] == 'creative')
                creative['object_story_spec']['link_data']['call_to_action']['value'] = {'link': 'https://other.example/'}
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH')
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))

    async def test_changed_fresh_targeting_cannot_be_verified_and_recovery_never_republishes(self):
        self.configure()
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777772':
                row['targeting']['flexible_spec'][0]['behaviors'] = [{'id': '999999'}]
            return row
        self.client.get = changed
        for _ in range(2):
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_OBJECT_SPEC_MISMATCH')
            self.owner.state = PrivateLaunchStateStore(self.owner.state.path)
        self.assertEqual(len(self.client.posts), 5)
        saved = await self.owner.state.step('image-launch', OperationKey('ADS_MANAGER:PUBLISH'))
        self.assertNotEqual(saved['status'], 'SUCCESS')
        self.client.get = original
        self.assertTrue((await self.launch())['verified'])
        self.assertEqual(len(self.client.posts), 5)

    async def test_changed_fresh_creative_cannot_be_verified(self):
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777774':
                row['object_story_spec']['link_data']['call_to_action']['value'] = {'link': 'https://other.example/'}
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_CREATIVE_SPEC_MISMATCH')
        self.assertEqual(len(self.client.posts), 5)

    async def test_retained_inspection_marks_changed_nested_values_unmatched(self):
        self.configure()
        self.payload['launch']['execution_mode'] = 'DRAFT'
        await self.launch()
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777772':
                row['targeting']['geo_locations']['zips'] = [{'key': '999999'}]
            return row
        self.client.get = changed
        self.payload['reconcile_only'] = True
        result = await self.launch()
        adset = next(row for row in result['object_reads'] if row['level'] == 'ad_set')
        self.assertFalse(adset['settings_match'])
        self.assertEqual(len(self.client.posts), 4)


class ExactDeliverySettingsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LaunchTests.asyncSetUp
    launch = fixtures.LaunchTests.launch
    prepare_delivery = delivery_fixtures.DeliveryLifecycleTests.prepare_delivery
    deliver = delivery_fixtures.DeliveryLifecycleTests.deliver

    async def test_new_exclusion_blocks_activation_before_any_status_mutation(self):
        await self.prepare_delivery()
        original = self.client.get
        async def changed(path, params):
            row = await original(path, params)
            if path == '/777772':
                row['targeting']['exclusions'] = {'interests': [{'id': '999999'}]}
            return row
        self.client.get = changed
        with self.assertRaises(ProvisioningError) as caught:
            await self.deliver()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_DELIVERY_SPEC_MISMATCH')
        self.assertEqual(self.delivery_posts, [])


class ExactFragmentValueTests(unittest.TestCase):
    def test_encoded_nested_values_do_not_hide_extra_conditions(self):
        expected = {'targeting': selected_targeting()}
        actual = copy.deepcopy(expected)
        actual['targeting']['exclusions']['behaviors'] = [{'id': '999999'}]
        for encoded in (False, True):
            rows = [{'field': key, 'new_value': json.dumps(value) if encoded else value}
                    for key, value in actual.items()]
            with self.subTest(encoded=encoded):
                self.assertFalse(fragment_values_match(rows, expected))
