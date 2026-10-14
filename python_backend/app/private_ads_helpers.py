"""Source-backed advertising form reads on the exact profile HTTP/2 pool."""
import json
import re
import time

from .private_ads_draft import fail
from .private_ads_reads import _ReadClient


async def read_form_helper(web, catalog, values, helper, inputs):
    from .private_launch import PrivateLaunchContractStore
    PrivateLaunchContractStore._reject_auth_material(inputs)
    if len(json.dumps(inputs, allow_nan=False)) > 65536:
        raise fail('INVALID_INPUT', 'Advertising helper input exceeds its budget')
    client = _ReadClient(web, catalog, values)
    account = await client.account_record(require_active=helper == 'preview',
                                          currency=helper == 'audience_estimate')
    common = {'profile': values['profile_id'], 'account_id': client.account,
              'business_id': client.business, 'account_scope_verified': True,
              'checked_live': True, 'checked_at': int(time.time()),
              'browser_started': False, 'transport': 'private_http_h2'}
    if helper == 'preview':
        from .private_ads_preview import read_preview
        return {**common, **await read_preview(client,catalog,account,inputs)}
    if helper == 'audience_estimate':
        targeting = inputs.get('targeting_spec')
        if (not isinstance(targeting, dict) or not targeting
                or set(targeting) - set(catalog.data['object_fields']['targeting'])):
            raise fail('TARGETING_FIELDS_UNAVAILABLE', 'Audience estimate requires reviewed targeting')
        params = {'targeting_spec': targeting, 'skip_bid_suggestion': True,
                  'send_long_term_prediction_shadow_request': True}
        currency = account.get('currency')
        if isinstance(currency, str) and re.fullmatch('[A-Z]{3}', currency):
            params['currency'] = currency
        else:
            currency = None
        for key in ('optimization_goal','promoted_object','bid_strategy','attribution_spec',
                    'special_ad_categories','campaign_destination_type','is_cbo_enabled','prediction_duration'):
            if key in inputs:
                params[key] = inputs[key]
        response = await client.request('DELIVERY_ESTIMATE', params=params)
        rows = response.get('data')
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise fail('RESOURCE_READ_INCOMPLETE', 'Meta did not return a complete audience estimate', True)
        row = rows[0]
        def bound(key):
            value = row.get(key)
            return value if type(value) is int and value >= 0 else None
        lower, upper = bound('estimate_mau_lower_bound'), bound('estimate_mau_upper_bound')
        if lower is not None and upper is not None and lower > upper:
            raise fail('RESOURCE_READ_INCOMPLETE', 'Meta audience bounds conflict')
        from .private_ads_estimate import audience
        return {**common, 'source':'private_ads_delivery_estimate', **audience(row, inputs),
                'currency': currency,
                'optimization_goal': inputs.get('optimization_goal', '')}
    if helper == 'placements':
        known = catalog.data['form_schema']['placement_options']
        params = {'fields':['raw_name','id','name','type','key','parent'],
                  'include_nodes':True, 'whitelisted_types':list(known),
                  'is_exclusion':False, 'is_reserved':False,
                  'regulated_categories':[], 'regulated_countries':[]}
        if inputs.get('optimization_goal'):
            params['optimization_goal'] = inputs['optimization_goal']
        response = await client.request('TARGETING_BROWSE', params=params)
        rows = response.get('data')
        if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(row, dict) for row in rows):
            raise fail('RESOURCE_READ_INCOMPLETE', 'Placement inventory is incomplete', True)
        # No partial page can establish an exhaustive capability set.
        if not isinstance(response.get('paging', {}), dict) or response.get('paging', {}).get('next'):
            raise fail('RESOURCE_READ_INCOMPLETE', 'Placement inventory requires another reviewed page', True)
        options = {}
        for row in rows:
            group = row.get('type')
            if group not in known:
                continue
            value = next((row.get(key) for key in ('key','id','raw_name')
                          if isinstance(row.get(key), str) and row[key] in known[group]), None)
            if value is not None and value not in options.setdefault(group, []):
                options[group].append(value)
        return {**common, 'source':'meta_targetingbrowse' if options else 'meta_unavailable',
                'private_source':'reviewed_private_ads_manager_modules',
                'options':options, 'live_groups':list(options), 'requires_context':not bool(options),
                'warning':'' if options else 'Meta не вернула места показа для выбранного РК.'}
    raise fail('RESOURCE_CONTRACT_REQUIRED', 'A reviewed private sender is required for this advertising helper')
