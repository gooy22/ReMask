import copy
import unittest
from app.private_ads_estimate import audience, forecast, number


class EstimateTests(unittest.TestCase):
    def setUp(self):
        self.row={'estimate_ready':True,'estimate_mau':1200,'estimate_mau_lower_bound':1000,
            'estimate_mau_upper_bound':1500,'estimate_mau_advantage_audience_enabled':2000,
            'daily_outcomes_curve':[
                {'spend':100,'bid':10,'reach':1000,'actions':10,'impressions':2000,'reach_lower_bound':800,'reach_upper_bound':1200},
                {'spend':300,'bid':30,'reach':3000,'actions':20,'impressions':5000,'reach_lower_bound':2400,'reach_upper_bound':3600}]}
        self.input={'daily_budget':200,'optimization_goal':'LINK_CLICKS','bid_strategy':'LOWEST_COST_WITHOUT_CAP'}

    def test_interpolates_meta_curve_in_the_same_minor_currency_units(self):
        result=forecast(self.row,self.input)
        self.assertTrue(result['available'])
        self.assertEqual((result['reach'],result['actions'],result['spend']), (2000,15,200))
        self.assertEqual((result['reach_lower_bound'],result['reach_upper_bound']), (1600,2400))
        self.assertIsNone(result['actions_lower_bound'])
        self.assertEqual(audience(self.row,self.input)['estimate_mau_advantage_audience_enabled'],2000)

    def test_bid_cap_is_interpolated_and_budget_is_not_extrapolated(self):
        result=forecast(self.row,{**self.input,'bid_strategy':'LOWEST_COST_WITH_BID_CAP','bid_amount':15})
        self.assertEqual((result['reach'],result['spend']),(1500,150))
        result=forecast(self.row,{**self.input,'daily_budget':500})
        self.assertTrue(result['curve_budget_clamped']);self.assertEqual(result['spend'],300)

    def test_target_cost_scales_to_daily_budget_and_cost_cap_preserves_source_semantics(self):
        result=forecast(self.row,{**self.input,'daily_budget':100,'bid_strategy':'TARGET_COST','bid_amount':15})
        self.assertEqual(result['spend'],100);self.assertAlmostEqual(result['actions'],20/3)
        result=forecast(self.row,{**self.input,'bid_strategy':'COST_CAP','bid_amount':11})
        self.assertAlmostEqual(result['spend'],1100/9)

    def test_not_ready_never_displays_factory_or_stale_counts(self):
        result=audience({**self.row,'estimate_ready':False},self.input)
        self.assertIsNone(result['estimate_mau']);self.assertIsNone(result['lower_bound'])
        self.assertFalse(result['forecast']['available'])

    def test_missing_metrics_and_unknown_goal_do_not_become_zero_actions(self):
        row=copy.deepcopy(self.row);row['daily_outcomes_curve'][1].pop('reach')
        result=forecast(row,{**self.input,'optimization_goal':'REACH'})
        self.assertIsNone(result['reach']);self.assertIsNone(result['actions'])
        for value in [True,'123',-1,float('nan'),float('inf'),10**10000]:self.assertIsNone(number(value))

    def test_invalid_or_unordered_curves_are_unavailable(self):
        for curve in [list(reversed(self.row['daily_outcomes_curve'])),[{'spend':10,'reach_lower_bound':20,'reach_upper_bound':10}],
                      [{'spend':100},{'spend':100}],['invalid'],[{'spend':True}]]:
            self.assertEqual(forecast({**self.row,'daily_outcomes_curve':curve},self.input)['reason'],'META_CURVE_INVALID')

    def test_cbo_requires_campaign_curve_and_lifetime_budget_is_not_daily(self):
        result=forecast(self.row,{**self.input,'is_cbo_enabled':True})
        self.assertEqual(result['reason'],'CBO_CURVE_UNAVAILABLE')
        result=forecast({**self.row,'campaign_group_curve':self.row['daily_outcomes_curve']},{**self.input,'is_cbo_enabled':True})
        self.assertTrue(result['available']);self.assertEqual(result['curve_source'],'campaign_group_curve')
        self.assertEqual(forecast(self.row,{'lifetime_budget':200})['reason'],'DAILY_BUDGET_REQUIRED')

    def test_prediction_and_currency_have_exact_captured_module_proof(self):
        import json,hashlib
        from pathlib import Path
        manifest=json.loads((Path(__file__).parents[1]/'app/contracts/ads_delivery_estimate_20261014.json').read_text())
        for row in manifest['sources']:
            self.assertEqual(hashlib.sha256(row['source'].encode()).hexdigest(),row['source_sha256'])
            self.assertEqual(hashlib.sha256((row['module_prefix']+row['source']+row['module_suffix']).encode()).hexdigest(),row['sha256'])
        names={r['name'] for r in manifest['sources']}
        self.assertIn('AdsBisonUtils',names);self.assertNotIn('AdsDeliveryEstimateFactories',names)
