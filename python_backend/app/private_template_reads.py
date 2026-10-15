"""Discover a read context for shared templates; never create or bind assets."""
import asyncio
import hashlib
import json
import logging
import time

from .private_ads_catalog import identity
from .private_ads_draft import fail
from .private_ads_helpers import read_form_helper
from .private_ads_reads import AdsManagerReadCatalog
from .private_advertising_reads import TARGETING_TYPES
from .private_inventory import private_inventory_snapshot, inventory_diagnostic_summary
from .private_launch import PrivateLaunchContractStore
from .provisioning.models import ProvisioningError

REFERENCE_TYPES = TARGETING_TYPES | {'locations', 'locales'}
_sources = {}  # Private process memory. Hints expire and never prove permissions.
log = logging.getLogger(__name__)


def _key(web):
    profile = web.profile
    return hashlib.sha256(json.dumps([profile.name, profile.cookies, profile.proxy,
        profile.user_agent], sort_keys=True).encode()).hexdigest()


def _pairs(rows, actor):
    pairs = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            account = identity(str(row.get('ad_account_id') or row.get('account_id') or row.get('id') or '').removeprefix('act_'))
            business = identity(str(row.get('business_id') or ''))
        except (ValueError, TypeError, ProvisioningError):
            continue
        if business != actor and row.get('is_personal') is not True:
            pairs[(account, business)] = str(row.get('account_status', '')) == '1'
    return sorted(pairs, key=lambda pair: (not pairs[pair], pair))


async def read_template_reference(web, catalog, context, operation, inputs):
    # This endpoint cannot read account-owned assets or invoke a writer.
    if operation not in REFERENCE_TYPES | {'audience_estimate', 'placements'}:
        raise fail('TEMPLATE_HELPER_UNAVAILABLE', 'Only template reference reads are supported')
    if set(inputs) - {'q', 'limit', 'targeting_spec', 'optimization_goal', 'bid_strategy',
            'attribution_spec', 'special_ad_categories', 'campaign_destination_type',
            'is_cbo_enabled', 'prediction_duration', 'daily_budget', 'bid_amount', 'include_forecast',
            'promoted_object', 'ad_format'}:
        raise fail('INVALID_INPUT', 'Template reference input contains an owned resource or selector')
    PrivateLaunchContractStore._reject_auth_material(inputs)
    if len(json.dumps(inputs, allow_nan=False)) > 65536:
        raise fail('INVALID_INPUT', 'Template reference input exceeds its budget')
    owned = {'page_id', 'instagram_actor_id', 'instagram_user_id', 'image_hash', 'video_id',
             'creative_id', 'source_instagram_media_id', 'object_story_id', 'object_story_spec',
             'source_facebook_post_id', 'pixel_id', 'application_id', 'product_catalog_id',
             'product_set_id', 'custom_audiences', 'excluded_custom_audiences'}
    def reject_owned(value, depth=0):
        if depth > 24:
            raise fail('INVALID_INPUT', 'Template reference input is too deeply nested')
        if isinstance(value, dict):
            if set(value) & owned:
                raise fail('TEMPLATE_OWNED_RESOURCE_UNAVAILABLE', 'Owned assets must be selected in Launch')
            for child in value.values():
                reject_owned(child, depth+1)
        elif isinstance(value, list):
            for child in value:
                reject_owned(child, depth+1)
    reject_owned(inputs)
    actor = identity(str(web.profile.cookies.get('c_user') or ''))
    profile = str(context.profile_id)
    key, now = _key(web), time.monotonic()
    cached = _sources.get(key)
    pairs = ([cached[1]] if cached and now-cached[0] < 1800 else [])
    pairs += [pair for pair in _pairs(context.ad_accounts or [], actor) if pair not in pairs]
    if not pairs:
        snapshot = await asyncio.wait_for(private_inventory_snapshot(web,
            known_accounts_by_business={}, known_business_ids=set(), personal_scope_id=actor,
            execute_read_queries=True), timeout=18)
        rows = [row for business in snapshot.get('businesses', [])
                for row in business.get('ad_accounts', [])]
        pairs = _pairs(rows, actor)
        if not pairs:
            log.info('template reference discovery profile=%s account_contexts=0 diagnostic=%s',
                profile, json.dumps(inventory_diagnostic_summary(snapshot), ensure_ascii=True)[:12000])
            diagnostics = list(snapshot.get('diagnostics', []))
            for business in snapshot.get('businesses', []):
                diagnostics.extend(business.get('diagnostics', []))
            gates = {row.get('auth_gate') for row in diagnostics}
            for code in ('CHECKPOINT_REQUIRED', 'SESSION_EXPIRED', 'BUSINESS_LOGIN_GATE'):
                if code in gates:
                    raise fail(code, 'Meta session cannot supply template reference data')
            if any(row.get('error_type') == 'ProxyError' for row in diagnostics):
                raise fail('PRIVATE_HTTP_PROXY_UNAVAILABLE', 'The configured read transport is unavailable', True)
            raise fail('TEMPLATE_LOOKUP_NO_ACCOUNT', 'Meta did not confirm a template read account', True)
    # Exact fresh ACCOUNT_READ still confirms every selected pair. A stale or
    # foreign pair can fall through to another pair from this same profile.
    last = None
    for account, business in pairs[:2]:
        values = {'profile_id': profile, 'ad_account_id': account, 'business_id': business,
                  'query': str(inputs.get('q') or '').strip(), 'limit': min(500, max(1, int(inputs.get('limit', 100))))}
        try:
            if operation in REFERENCE_TYPES:
                if len(values['query']) > 200:
                    raise fail('INVALID_TARGETING_QUERY', 'Targeting query is too long')
                result = await AdsManagerReadCatalog(catalog).read(web, operation, values)
                result = {**result, 'profile': profile, 'business_id': business,
                          'transport': 'private_http_h2', 'browser_started': False}
            else:
                result = await read_form_helper(web, catalog, values, operation, inputs)
        except ProvisioningError as error:
            if not error.code.endswith('RESOURCE_SCOPE_MISMATCH'):
                raise
            last = error
            _sources.pop(key, None)
            continue
        if (result.get('checked_live') is not True or result.get('account_scope_verified') is not True
                or result.get('account_id') != account or result.get('business_id') != business
                or result.get('profile') != profile or result.get('transport') != 'private_http_h2'):
            raise fail('TEMPLATE_LOOKUP_ACCESS_UNVERIFIED', 'Meta did not confirm the reference read')
        _sources[key] = (now, (account, business))
        for old_key in list(_sources):
            if now-_sources[old_key][0] >= 1800:
                del _sources[old_key]
        while len(_sources) > 100:
            del _sources[next(iter(_sources))]
        return {**result, 'source_context_discovered': True, 'template_account_bound': False}
    raise last or fail('TEMPLATE_LOOKUP_NO_ACCOUNT', 'No verified template read context is available', True)
