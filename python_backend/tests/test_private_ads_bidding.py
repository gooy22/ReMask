"""Offline draft bidding contracts; no claim of live editor acceptance."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

from app.contract_maintenance.ads_sources import module_records
from app.private_ads_bidding import MANIFEST, native_bidding
from app.private_ads_objectives import ObjectiveContract
from app.provisioning.models import ProvisioningError
from tests import test_private_ads_launch as fixture
from tests.test_private_ads_objectives import configuration


class BiddingContractTests(unittest.TestCase):
    def test_budget_owner_and_legacy_flag_contract_sources_are_pinned(self):
        data = json.loads(MANIFEST.read_bytes())
        sources = {row['module']: row for row in data['sources']}
        body = Path(__file__).with_name('fixtures').joinpath('private_bidding_observed_20261021.js').read_text()
        modules = {row['name']: row for row in module_records(body, selected=tuple(sources)) if row['name'] in sources}
        self.assertEqual(set(modules), set(sources))
        for name, row in modules.items():
            self.assertEqual(hashlib.sha256(row['source'].encode()).hexdigest(), sources[name]['module_sha256'])
        # Pin the actual inheritance branch and both separate legacy mappings.
        self.assertIn('hasBudget_LEGACY(i)?', modules['AdsUEditorCampaignDeliverySelectors']['source'])
        group = modules['AdsCampaignGroupBudgetMutationUtils']['source']
        self.assertIn('l.setCampaignGroupBidStrategyAndLegacyFlags=N', group)
        self.assertIn('l.resetCampaignWhenCampaignGroupBudgetEnabled=P', group)
        self.assertIn('bid_strategy.delete', group)
        creator = modules['AdsLiveCreationDataManager']['source']
        self.assertIn('bid_strategy:n.bidStrategy', creator)
        self.assertIn('is_autobid:n.isAutobid', creator)
        self.assertIn('is_average_price_pacing:n.isAveragePricePacing', creator)
        self.assertIn('createFragment("campaign",s,!1,t.source)', creator)
        self.assertFalse(data['checked_live'])

    def test_profile38_configuration_keeps_exact_intent_but_serializes_cbo_strategy_on_campaign(self):
        selected = configuration()
        before = copy.deepcopy(selected)
        campaign, adset, proof = native_bidding(selected['campaign'], selected['adset'])
        self.assertEqual(selected, before)
        self.assertEqual(campaign, {**selected['campaign'], 'bid_strategy':'LOWEST_COST_WITHOUT_CAP',
            'is_autobid':True, 'is_average_price_pacing':False})
        self.assertEqual(adset, {key:value for key,value in selected['adset'].items() if key != 'bid_strategy'})
        self.assertNotIn('bid_amount', adset)
        self.assertEqual(proof['budget_level'], 'campaign')
        self.assertEqual(native_bidding(campaign, adset), (campaign, adset, proof))

    def test_campaign_strategy_is_validated_instead_of_the_unused_adset_default(self):
        selected = configuration()
        selected['campaign']['bid_strategy'] = 'COST_CAP'
        selected['adset'].pop('bid_strategy')
        with self.assertRaises(ProvisioningError) as caught:
            ObjectiveContract().validate(selected)
        self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_BID_AMOUNT_REQUIRED')
        selected['adset']['bid_amount'] = 200
        campaign, adset, _ = native_bidding(selected['campaign'], selected['adset'])
        self.assertFalse(campaign['is_autobid'])
        self.assertTrue(campaign['is_average_price_pacing'])
        self.assertEqual(adset['bid_amount'],200)
        self.assertTrue(ObjectiveContract().validate(selected)['compatible'])

    def test_abo_strategies_keep_bid_amount_on_adset_and_never_add_campaign_bidding(self):
        for strategy, amount in [('LOWEST_COST_WITHOUT_CAP', None), ('LOWEST_COST_WITH_BID_CAP', 200), ('COST_CAP', 200)]:
            selected = configuration()
            selected['campaign'].pop('daily_budget')
            selected['adset'].update(daily_budget=500, bid_strategy=strategy)
            if amount is not None:
                selected['adset']['bid_amount'] = amount
            with self.subTest(strategy=strategy):
                campaign, adset, proof = native_bidding(selected['campaign'],selected['adset'])
                self.assertEqual(campaign, selected['campaign'])
                self.assertEqual(adset['bid_strategy'], strategy)
                self.assertIs(adset['is_autobid'], amount is None)
                self.assertIs(adset['is_average_price_pacing'], False)
                self.assertEqual(adset.get('bid_amount'), amount)
                self.assertEqual(proof['budget_level'], 'adset')

    def test_conflicting_explicit_controls_never_silently_override_either_choice(self):
        for campaign_change, adset_change in [
            ({'bid_strategy':'COST_CAP'}, {}),
            ({'is_autobid':False}, {}),
            ({}, {'is_autobid':1}),
            ({}, {'is_average_price_pacing':True}),
            ({'pacing_type':['standard']}, {'pacing_type':['no_pacing']}),
        ]:
            selected = configuration()
            selected['campaign'].update(campaign_change)
            selected['adset'].update(adset_change)
            before = copy.deepcopy(selected)
            with self.subTest(campaign=campaign_change, adset=adset_change), self.assertRaises(ProvisioningError) as caught:
                native_bidding(selected['campaign'],selected['adset'])
            self.assertEqual(caught.exception.code, 'PRIVATE_ADS_DRAFT_BID_STRATEGY_CONFLICT')
            self.assertEqual(selected, before)

    def test_explicit_pacing_is_transferred_without_discarding_and_wrong_budget_scope_rejected(self):
        selected = configuration()
        selected['adset']['pacing_type'] = ['standard','day_parting']
        campaign, adset, _ = native_bidding(selected['campaign'], selected['adset'])
        self.assertEqual(campaign['pacing_type'],['standard','day_parting'])
        self.assertNotIn('pacing_type',adset)
        selected['campaign'].pop('daily_budget')
        selected['campaign']['bid_strategy'] = 'LOWEST_COST_WITHOUT_CAP'
        selected['adset']['daily_budget'] = 500
        with self.assertRaises(ProvisioningError) as caught:
            native_bidding(selected['campaign'],selected['adset'])
        self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_BID_STRATEGY_SCOPE')

    def test_malformed_strategies_and_caps_fail_with_typed_errors(self):
        for strategy in (None, [], {}, 'UNKNOWN'):
            with self.subTest(strategy=strategy), self.assertRaises(ProvisioningError):
                native_bidding({}, {'bid_strategy':strategy})
        for amount in (True, 0, -1, 1.5, '200', 9_007_199_254_740_992):
            with self.subTest(amount=amount), self.assertRaises(ProvisioningError):
                native_bidding({}, {'bid_strategy':'COST_CAP','bid_amount':amount})


class BiddingLaunchTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.LaunchTests.asyncSetUp
    launch = fixture.LaunchTests.launch

    async def test_cbo_wire_journal_verification_and_inspection_share_exact_native_binding(self):
        selected = configuration()
        self.payload['launch']['campaign'].update(selected['campaign'])
        self.payload['launch']['adset'].update(selected['adset'])
        self.payload['launch']['execution_mode'] = 'DRAFT'
        before = copy.deepcopy(self.payload)
        with patch('app.static_payment_read.inspect_methods',new=AsyncMock(side_effect=AssertionError('No payment'))) as payment:
            result = await self.launch()
            self.assertEqual(self.payload, before)
            wire = {row['ad_object_type']:{field['field']:field['new_value'] for field in row['values']}
                    for row in self.client.fragments.values()}
            self.assertEqual(wire['campaign']['objective'],'LINK_CLICKS')
            self.assertTrue(wire['campaign']['is_autobid'])
            self.assertEqual(wire['campaign']['daily_budget'],500)
            self.assertNotIn('bid_strategy',wire['ad_set'])
            self.assertNotIn('bid_amount',wire['ad_set'])
            self.assertEqual(wire['ad_set']['targeting'],selected['adset']['targeting'])
            self.assertEqual(result['configuration']['bidding']['budget_level'],'campaign')
            posts = len(self.client.posts)
            await self.launch()
            self.assertEqual(len(self.client.posts),posts)
            self.payload['reconcile_only'] = True
            inspected = await self.launch()
            self.assertTrue(all(row['settings_match'] for row in inspected['fragment_reads']))
            self.assertEqual(inspected['mutation_posts'],0)
            self.assertFalse(inspected['editor_verified'])
            self.assertEqual(len(self.client.posts),posts)
        payment.assert_not_awaited()

    async def test_bidding_conflict_fails_before_session_bootstrap_or_media_upload(self):
        self.payload['launch']['campaign']['bid_strategy'] = 'COST_CAP'
        self.payload['launch']['adset']['bid_strategy'] = 'LOWEST_COST_WITHOUT_CAP'
        with patch('app.private_ads_launch.AdsManagerPrivateHttp.open',new=AsyncMock()) as opened:
            with self.assertRaises(ProvisioningError) as caught:
                await self.launch()
        self.assertEqual(caught.exception.code,'PRIVATE_ADS_DRAFT_BID_STRATEGY_CONFLICT')
        opened.assert_not_awaited()
        self.assertEqual(self.client.uploads,0)
        self.assertEqual(self.client.posts,[])
