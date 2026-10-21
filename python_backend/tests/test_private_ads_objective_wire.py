"""Offline private editor record regressions; live UI remains unverified."""
import copy
import hashlib
from pathlib import Path

from app.contract_maintenance.ads_sources import module_records
import unittest

from app.private_ads_objective_wire import ObjectiveWireContract
from app.provisioning.models import ProvisioningError


class ObjectiveWireTests(unittest.TestCase):
    def setUp(self):
        self.contract = ObjectiveWireContract()

    def test_traffic_campaign_record_is_distinct_from_adset_goal(self):
        selected = {'objective': 'OUTCOME_TRAFFIC', 'daily_budget': 500,
                    'bid_strategy': 'LOWEST_COST_WITHOUT_CAP', 'status': 'PAUSED'}
        before = copy.deepcopy(selected)
        wire = self.contract.campaign(selected)
        self.assertEqual(wire, {**selected, 'objective': 'LINK_CLICKS', 'is_odax_campaign_group': True})
        self.assertEqual(selected, before)
        self.assertFalse(self.contract.data['editor_verified'])
        self.assertFalse(self.contract.data['checked_live'])

    def test_other_objectives_and_nested_values_are_not_reinterpreted(self):
        selected = {'objective': 'OUTCOME_SALES', 'special_ad_categories': [], 'status': 'PAUSED'}
        wire = self.contract.campaign(selected)
        self.assertEqual(wire, selected)
        wire['special_ad_categories'].append('HOUSING')
        self.assertEqual(selected['special_ad_categories'], [])

    def test_conflicting_legacy_campaign_metadata_is_rejected(self):
        for value in (False, 0, 1, 'true', None):
            with self.subTest(value=value), self.assertRaises(ProvisioningError) as caught:
                self.contract.campaign({'objective': 'OUTCOME_TRAFFIC', 'is_odax_campaign_group': value})
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_OBJECTIVE_REPRESENTATION_CONFLICT')

    def test_reviewed_writable_record_and_sender_sources_are_pinned(self):
        names = {row['module'] for row in self.contract.data['sources']}
        self.assertTrue({'AdsCampaignGroupRecordAccessors', 'AdsAdObjectsAccessorUtils',
                         'AdsCampaignGroupDraftFragmentsApplyChangesReducerPlugin',
                         'AdsDraftFragmentDataManager', 'AdsODAXUtils',
                         'AdsODAXObjectiveStrings', 'AdsEditorUnsupportedReasonUtils'} <= names)
        for row in self.contract.data['sources']:
            self.assertRegex(row['module_sha256'], r'^[a-f0-9]{64}$')
            self.assertRegex(row.get('script_sha256', row.get('artifact_sha256', '')), r'^[a-f0-9]{64}$')
            if 'public_url' in row:
                self.assertTrue(row['public_url'].startswith('https://static.xx.fbcdn.net/'))

    def test_actual_campaign_create_caller_writes_both_objective_fields(self):
        source = next(row for row in self.contract.data['sources'] if row['module'] == 'AdsLiveCreationDataManager')
        body = Path(__file__).with_name('fixtures').joinpath('private_bidding_observed_20261021.js').read_text()
        rows = [row for row in module_records(body, selected=('AdsLiveCreationDataManager',)) if row['name'] == 'AdsLiveCreationDataManager']
        self.assertEqual(len(rows), 1)
        actual = rows[0]['source']
        self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), source['module_sha256'])
        begin = actual.index('a.__createCampaignGroup=function')
        end = actual.index('a.__createCampaign=function', begin)
        caller = actual[begin:end]
        self.assertIn('objective:n.objective', caller)
        self.assertIn('is_odax_campaign_group:n.isODAXCampaignGroup||null', caller)
        self.assertIn('createFragment("campaign",s,!1,t.source)', caller)
