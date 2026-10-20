"""Offline contract regressions; these do not prove live Meta editability."""
import copy
import hashlib
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from app.contract_maintenance.ads_sources import module_records
from app.private_ads_objectives import ObjectiveContract
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture


def configuration():
    return {"campaign": {"objective": "OUTCOME_TRAFFIC", "buying_type": "AUCTION",
                         "name": "ReMask 38 — проверка PAUSED", "daily_budget": 500, "status": "PAUSED"},
            "adset": {"optimization_goal": "LINK_CLICKS", "billing_event": "IMPRESSIONS",
                      "destination_type": "WEBSITE", "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                      "targeting": {"geo_locations": {"countries": ["UA"]}, "age_min": 23,
                                    "age_max": 33, "locales": [52], "flexible_spec": [
                                        {"interests": [{"id": "6003277229371", "name": "Physical fitness (fitness)"}],
                                         "behaviors": [{"id": "6015547900583", "name": "Facebook access (browser): Chrome"}]}]}}}


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.contract = ObjectiveContract()
        self.launch = configuration()

    def test_profile38_validation_does_not_reinterpret_campaign_objective_as_optimization(self):
        original = copy.deepcopy(self.launch)
        self.contract.validate(self.launch)
        self.assertEqual(self.launch, original)
        self.assertFalse(self.contract.validate(self.launch)["editor_verified"])

    def test_supplied_compatibility_module_sources_are_pinned_without_promoting_odax_hypothesis(self):
        sources = {r['module']: r for r in self.contract.data['sources'] if r.get('source_kind') == 'supplied_public_cdn_module_export'}
        body = Path(__file__).with_name('fixtures').joinpath('private_objectives_observed_20261021.js').read_text()
        modules = {r['name']: r for r in module_records(body, selected=tuple(sources)) if r['name'] in sources}
        self.assertEqual(set(modules), set(sources))
        for name, row in modules.items():
            self.assertEqual(hashlib.sha256(row['source'].encode()).hexdigest(), sources[name]['module_sha256'], name)
        for spec in self.contract.data['objectives'].values():
            self.assertNotIn('wire_objective', spec)
            self.assertNotIn('is_odax_campaign_group', spec)

    def test_every_supported_ui_combination(self):
        for objective, spec in self.contract.data['objectives'].items():
            for goal in spec['optimization_goals']:
                for billing in self.contract.data['billing_events'][goal]:
                    with self.subTest(objective=objective, goal=goal, billing=billing):
                        launch = configuration()
                        launch['campaign']['objective'] = objective
                        launch['adset'].update(optimization_goal=goal, billing_event=billing)
                        if spec['destination_type'] is None:
                            launch['adset'].pop('destination_type')
                        if goal in {'OFFSITE_CONVERSIONS', 'VALUE'}:
                            launch['adset']['promoted_object'] = {'pixel_id': '888881', 'custom_event_type': 'PURCHASE'}
                        self.assertTrue(self.contract.validate(launch)['compatible'])

    def test_invalid_objectives(self):
        for objective in (None, 'LINK_CLICKS', 'CONVERSIONS', 'DEPRECATED_CLICKS', 'OUTCOME_APP_PROMOTION', [], ''):
            launch = configuration(); launch['campaign']['objective'] = objective
            with self.subTest(objective=objective), self.assertRaises(ProvisioningError):
                self.contract.validate(launch)

    def test_incompatible_fields_are_rejected_without_silent_replacement(self):
        cases = [({'optimization_goal': 'OFFSITE_CONVERSIONS'}, 'OPTIMIZATION_INCOMPATIBLE'),
                 ({'billing_event': 'LINK_CLICKS', 'optimization_goal': 'REACH'}, 'BILLING_INCOMPATIBLE'),
                 ({'destination_type': 'MESSENGER'}, 'DESTINATION_INCOMPATIBLE'),
                 ({'destination_type': None}, 'DESTINATION_INCOMPATIBLE'),
                 ({'promoted_object': {'page_id': '555555'}}, 'PROMOTED_OBJECT_INCOMPATIBLE'),
                 ({'bid_strategy': 'COST_CAP'}, 'BID_AMOUNT_REQUIRED'),
                 ({'bid_amount': 50}, 'BID_AMOUNT_INCOMPATIBLE'),
                 ({'targeting': {'age_min': 23}}, 'GEO_REQUIRED'),
                 ({'targeting': {'geo_locations': {'countries': ['UA']}, 'age_min': True}}, 'AGE_INVALID')]
        for fields, code in cases:
            launch = configuration(); launch['adset'].update(fields); before = copy.deepcopy(launch)
            with self.subTest(fields=fields), self.assertRaises(ProvisioningError) as caught:
                self.contract.validate(launch)
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_' + code)
            self.assertEqual(launch, before)

    def test_pixel_event_value_and_age_boundaries(self):
        self.launch['campaign']['objective'] = 'OUTCOME_SALES'
        self.launch['adset']['optimization_goal'] = 'VALUE'
        for promoted in ({}, {'pixel_id': '888881'}, {'pixel_id': '888881', 'custom_event_type': 'LEAD'}):
            self.launch['adset']['promoted_object'] = promoted
            with self.subTest(promoted=promoted), self.assertRaises(ProvisioningError):
                self.contract.validate(self.launch)
        self.launch = configuration()
        for minimum, maximum in ((18, 18), (18, 65), (65, 65)):
            self.launch['adset']['targeting'].update(age_min=minimum, age_max=maximum)
            self.assertTrue(self.contract.validate(self.launch)['compatible'])
        for minimum, maximum in ((17, 65), (18, 66), (33, 23), ('23', 33)):
            self.launch['adset']['targeting'].update(age_min=minimum, age_max=maximum)
            with self.assertRaises(ProvisioningError):
                self.contract.validate(self.launch)


class ObjectiveLaunchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.LaunchTests.asyncSetUp(self)

    async def launch(self):
        return await fixture.LaunchTests.launch(self)

    async def test_actual_wire_and_journal_keep_traffic_configuration_and_no_editor_claim(self):
        self.payload['launch']['execution_mode'] = 'DRAFT'
        selected = configuration()
        self.payload['launch']['campaign'].update(selected['campaign'])
        self.payload['launch']['adset'].update(selected['adset'])
        before = copy.deepcopy(self.payload)
        result = await self.launch()
        campaign = next(row for row in self.client.fragments.values() if row['ad_object_type'] == 'campaign')
        values = {row['field']: row['new_value'] for row in campaign['values']}
        self.assertEqual(values['objective'], 'OUTCOME_TRAFFIC')
        self.assertNotIn('is_odax_campaign_group', values)
        adset = next(row for row in self.client.fragments.values() if row['ad_object_type'] == 'ad_set')
        actual = {row['field']: row['new_value'] for row in adset['values']}
        self.assertEqual(actual['targeting'], selected['adset']['targeting'])
        self.assertEqual(self.payload, before)
        self.assertTrue(result['fragment_storage_verified'])
        self.assertFalse(result['editor_verified'])
        self.assertFalse(result['configuration']['editor_verified'])
        for path, params in self.client.posts:
            if path.endswith('/addraft_fragments'):
                self.assertIs(params['validate'], True)
        await self.launch()
        self.assertEqual(self.client.uploads, 1)
        self.assertEqual(len(self.client.posts), 4)

    async def test_incompatible_effective_override_cannot_bootstrap_upload_or_submit(self):
        base = copy.deepcopy(self.payload['launch'])
        self.payload['launch'] = {'base': base, 'override': {'adset': {'optimization_goal': 'OFFSITE_CONVERSIONS'}}}
        with patch('app.private_ads_launch.AdsManagerPrivateHttp.open', new=AsyncMock()) as opened:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_OPTIMIZATION_INCOMPATIBLE')
            opened.assert_not_called()
        self.assertEqual(self.client.uploads, 0)
        self.assertEqual(self.client.posts, [])

    async def test_meta_validation_errors_stop_before_next_fragment_or_publish(self):
        original = self.client.get
        async def read(path, params):
            row = await original(path, params)
            if path.startswith('/addraftfragment_'):
                row['active_errors'] = [{'error_code': 100}]
            return row
        self.client.get = read
        with self.assertRaises(ProvisioningError) as caught:
            await self.launch()
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_VALIDATION_ERRORS')
        self.assertEqual(len(self.client.fragments), 1)
        self.assertFalse(any(path.endswith('/publish') for path, _ in self.client.posts))
