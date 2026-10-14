"""AdsBisonUtils curve interpolation; unknown Meta metrics remain unknown.

Source: module 9eda272d55d8db092c064b21642a510f37706da54926e6de4da6a9cb4771b9b0
in https://static.xx.fbcdn.net/rsrc.php/v4ieqh4/y6/l/en_US-j/lbrF6LM6frL.js.
No AdsDeliveryEstimateFactories sample or fallback estimate is used.
"""
import math

METRICS = ('spend', 'bid', 'reach', 'impressions', 'actions', 'reach_lower_bound',
           'reach_upper_bound', 'actions_lower_bound', 'actions_upper_bound')
ACTION_GOALS = frozenset(('APP_INSTALLS', 'EVENT_RESPONSES', 'LANDING_PAGE_VIEWS',
    'LEAD_GENERATION', 'LINK_CLICKS', 'OFFER_CLAIMS', 'OFFSITE_CONVERSIONS',
    'PAGE_ENGAGEMENT', 'PAGE_LIKES', 'POST_ENGAGEMENT', 'VIDEO_VIEWS', 'QUALITY_CALL',
    'CONVERSATIONS', 'REPLIES', 'THRUPLAY', 'TWO_SECOND_CONTINUOUS_VIDEO_VIEWS'))


def number(value):
    return value if type(value) in (int, float) and 0 <= value <= 2**53-1 and math.isfinite(value) else None


def interpolate(left, right, fraction):
    # Only the observed curve's zero-budget origin is synthetic. A missing
    # metric at either endpoint must not become a fabricated zero result.
    return {key: (left[key]+fraction*(right[key]-left[key])
                  if left.get(key) is not None and right.get(key) is not None else None)
            for key in METRICS}


def origin(point):
    return {key: 0 if point.get(key) is not None else None for key in METRICS}


def at_budget(curve, budget, bid=None):
    left = origin(curve[0])
    for right in curve:
        if right['spend'] > budget or (bid is not None and right['bid'] > bid):
            if right['spend'] == left['spend']:
                return dict(left)
            fraction = (budget-left['spend'])/(right['spend']-left['spend'])
            if bid is not None and right['bid'] != left['bid']:
                fraction = min(fraction, (bid-left['bid'])/(right['bid']-left['bid']))
            return interpolate(left, right, max(0, min(1, fraction)))
        left = right
    return dict(curve[-1])  # No unobserved gkx(9118) extrapolation.


def at_cpa(curve, target):
    left = origin(curve[0])
    for right in curve:
        if right['actions'] and right['spend']/right['actions'] >= target:
            numerator = target*left['actions']-left['spend']
            denominator = target*(left['actions']-right['actions'])-(left['spend']-right['spend'])
            fraction = numerator/denominator if denominator > 0 else 0
            return interpolate(left, right, max(0, min(1, fraction)))
        left = right
    return dict(curve[-1])


def forecast(row, inputs):
    result = {'available': False, 'reason': 'META_FORECAST_NOT_READY'}
    if row.get('estimate_ready') is not True:
        return result
    budget = number(inputs.get('daily_budget'))
    if budget is None or budget <= 0:
        return {**result, 'reason': 'DAILY_BUDGET_REQUIRED'}
    cbo = inputs.get('is_cbo_enabled') is True
    strategy = inputs.get('bid_strategy') or 'LOWEST_COST_WITHOUT_CAP'
    if strategy not in {'LOWEST_COST_WITHOUT_CAP', 'LOWEST_COST_WITH_BID_CAP', 'TARGET_COST', 'COST_CAP'}:
        return {**result, 'reason': 'BID_STRATEGY_FORECAST_UNAVAILABLE'}
    # A per-adset curve cannot predict campaign budget allocation.
    if cbo:
        if strategy != 'LOWEST_COST_WITHOUT_CAP':
            return {**result, 'reason': 'CBO_BID_FORECAST_UNAVAILABLE'}
        raw = row.get('campaign_group_curve')
    else:
        raw = row.get('daily_outcomes_curve')
    if not isinstance(raw, list) or not raw or len(raw) > 500:
        return {**result, 'reason': 'CBO_CURVE_UNAVAILABLE' if cbo else 'META_CURVE_UNAVAILABLE'}
    curve = []
    previous = -1
    for value in raw:
        if not isinstance(value, dict):
            return {**result, 'reason': 'META_CURVE_INVALID'}
        point = {key: number(value.get(key)) for key in METRICS}
        if point['spend'] is None or point['spend'] <= previous:
            return {**result, 'reason': 'META_CURVE_INVALID'}
        for key in ('reach', 'actions'):
            lo, hi = point[key+'_lower_bound'], point[key+'_upper_bound']
            if lo is not None and hi is not None and lo > hi:
                return {**result, 'reason': 'META_CURVE_INVALID'}
        previous = point['spend']
        curve.append(point)
    bid = number(inputs.get('bid_amount'))
    if strategy != 'LOWEST_COST_WITHOUT_CAP' and (bid is None or bid <= 0):
        return {**result, 'reason': 'BID_AMOUNT_REQUIRED'}
    if strategy == 'LOWEST_COST_WITH_BID_CAP':
        if any(p['bid'] is None for p in curve) or any(a['bid'] > b['bid'] for a, b in zip(curve, curve[1:])):
            return {**result, 'reason': 'META_BID_CURVE_UNAVAILABLE'}
        point = at_budget(curve, budget, bid)
    elif strategy in {'COST_CAP', 'TARGET_COST'}:
        if any(p['actions'] is None for p in curve):
            return {**result, 'reason': 'META_ACTION_CURVE_UNAVAILABLE'}
        point = at_budget(curve, budget)
        if strategy == 'TARGET_COST' or not point['actions'] or bid < point['spend']/point['actions']:
            point = at_cpa(curve, bid)
        if strategy == 'TARGET_COST' and point['spend'] > budget:
            factor = budget/point['spend']
            point = {k: (v*factor if k != 'bid' and v is not None else v) for k, v in point.items()}
    else:
        if cbo and budget < curve[0]['spend']:
            return {**result, 'reason': 'CBO_BUDGET_OUTSIDE_CURVE'}
        point = at_budget(curve, budget)
    if inputs.get('optimization_goal') not in ACTION_GOALS:
        for key in ('actions', 'actions_lower_bound', 'actions_upper_bound'):
            point[key] = None
    return {'available': any(point[k] is not None for k in ('reach','impressions','actions')),
            'reason': '', 'budget_minor_units': budget,
            'curve_source': 'campaign_group_curve' if cbo else 'daily_outcomes_curve',
            'curve_budget_clamped': budget > curve[-1]['spend'], **point}


def audience(row, inputs):
    ready = row.get('estimate_ready') is True
    counts = {key: (number(row.get(key)) if ready else None) for key in (
        'estimate_mau', 'estimate_dau', 'estimate_mau_lower_bound', 'estimate_mau_upper_bound',
        'estimate_mau_relaxation_aware', 'estimate_mau_advantage_audience_enabled')}
    return {'estimate_ready': ready, 'lower_bound': counts['estimate_mau_lower_bound'],
            'upper_bound': counts['estimate_mau_upper_bound'], **counts, 'forecast': forecast(row, inputs)}
