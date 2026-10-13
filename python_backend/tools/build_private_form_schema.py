"""Review an extracted public module index; emit metadata, never vendor JavaScript.

Run explicitly with an index produced by the existing source-audit tool. This is
an offline review step, not an automatic promotion of changed schemas.
"""
import hashlib
import json
from pathlib import Path
import re
import sys


def build(index, manifest):
    rows = {row['name']: row for row in index}
    evidence = {}
    def literals(name):
        row = rows[name]
        wrapped = row['module_prefix'] + row['source'] + row['module_suffix']
        if hashlib.sha256(wrapped.encode()).hexdigest() != row['sha256']:
            raise ValueError('Module hash mismatch: ' + name)
        values = re.findall(r'\b[A-Z][A-Z0-9_]*:"([^"\\]+)"', row['source'])
        if not values:
            raise ValueError('Missing literal values: ' + name)
        evidence[name] = {'module': name, 'module_sha256': row['sha256'],
            'script_sha256': row['script_sha256'], 'observed_public_url': row['observed_url']}
        return list(dict.fromkeys(values))

    manifest['object_fields']['targeting'] = sorted(set(literals('AdsAPITargetFields')))
    # A name constant does not establish a vendor type. JSON inputs retain the
    # exact submitted value for fields without a reviewed primitive binding.
    editable = {
        'campaign': 'name objective buying_type special_ad_categories special_ad_category_country bid_strategy daily_budget lifetime_budget spend_cap start_time stop_time status adlabels budget_schedule_specs execution_options frequency_control_specs is_adset_budget_sharing_enabled is_budget_schedule_enabled is_message_campaign is_skadnetwork_attribution pacing_type promoted_object smart_promotion_type',
        'adset': 'name optimization_goal billing_event bid_strategy bid_amount destination_type daily_budget lifetime_budget start_time end_time attribution_spec promoted_object is_dynamic_creative status adlabels bid_adjustments bid_constraints daily_min_spend_target daily_spend_cap lifetime_min_spend_target lifetime_spend_cap frequency_control_specs pacing_type targeting adset_schedule optimization_sub_event time_based_ad_rotation',
        'targeting': 'age_min age_max genders locales geo_locations excluded_geo_locations interests behaviors custom_audiences excluded_custom_audiences flexible_spec exclusions publisher_platforms facebook_positions instagram_positions messenger_positions audience_network_positions threads_positions whatsapp_positions device_platforms user_os user_device connections excluded_connections friends_of_connections education_schools education_majors education_statuses college_years work_positions work_employers relationship_statuses life_events family_statuses income industries home_ownership home_type home_value wireless_carrier contextual_targeting_categories brand_safety_content_filter_levels excluded_publisher_categories excluded_publisher_list_ids targeting_automation targeting_relaxation_types',
        'creative': 'name body title url_tags degrees_of_freedom_spec asset_feed_spec platform_customizations authorization_category applink_treatment branded_content_sponsor_page_id branded_content branded_content_boosting_type contextual_multi_ads creative_sourcing_spec destination_set_id enable_direct_install object_store_url product_set_id template_url_spec use_page_actor_override',
        'ad': 'name status conversion_domain priority tracking_specs adlabels ad_schedule_start_time ad_schedule_end_time creative_sequence rotation_config',
    }
    text = set('name body title buying_type start_time stop_time end_time destination_type optimization_sub_event conversion_domain authorization_category branded_content_sponsor_page_id branded_content_boosting_type applink_treatment destination_set_id object_store_url product_set_id ad_schedule_start_time ad_schedule_end_time url_tags'.split())
    number = set('daily_budget lifetime_budget spend_cap bid_amount age_min age_max priority daily_min_spend_target daily_spend_cap lifetime_min_spend_target lifetime_spend_cap'.split())
    boolean = set('is_adset_budget_sharing_enabled is_budget_schedule_enabled is_message_campaign is_skadnetwork_attribution is_dynamic_creative enable_direct_install use_page_actor_override'.split())
    schema = {}
    for group, names in editable.items():
        known = manifest['object_fields'][group]
        schema[group] = {'fields': {name: ('string' if name in text else
            'unsigned int' if name in number else 'bool' if name in boolean else 'JSON')
            for name in names.split() if name in known}, 'enums': {}}
    bindings = [('campaign', 'objective', 'AdsAPIObjectives'),
        ('campaign', 'bid_strategy', 'AdsAPIBidStrategies'),
        ('campaign', 'special_ad_categories', 'AdsRegulatedCategory'),
        ('adset', 'bid_strategy', 'AdsAPIBidStrategies'),
        ('adset', 'optimization_goal', 'AdsAPIOptimizationGoals'),
        ('adset', 'billing_event', 'AdsAPIBillingEvents'),
        ('adset', 'destination_type', 'AdCampaignDestination')]
    for group, field, module in bindings:
        if field not in manifest['object_fields'][group]:
            raise ValueError('Missing field: ' + field)
        schema[group]['enums'][field] = literals(module)
        schema[group]['fields'][field] = 'list<enum>' if field == 'special_ad_categories' else 'enum'
    for group in ('campaign', 'adset', 'ad'):
        schema[group]['enums']['status'] = [v for v in literals('AdsRunStatus') if v in {'PAUSED', 'ACTIVE'}]
        schema[group]['fields']['status'] = 'enum'
    for field, module in [('publisher_platforms', 'AdsAPIPublisherPlatform'),
        ('facebook_positions', 'AdsAPIFacebookPosition'), ('instagram_positions', 'AdsAPIInstagramPosition'),
        ('messenger_positions', 'AdsAPIMessengerPosition'), ('audience_network_positions', 'AdsAPIAudienceNetworkPosition'),
        ('threads_positions', 'AdsAPIThreadsPosition'), ('whatsapp_positions', 'AdsAPIWhatsAppPosition'),
        ('device_platforms', 'AdsAPIDevicePlatform')]:
        schema['targeting']['enums'][field] = literals(module)
        schema['targeting']['fields'][field] = 'list<enum>'
    events = literals('AdsCustomConversionCustomEventAPICreatableType')
    for field in ('name', 'body', 'title', 'url_tags'):
        schema['creative']['fields'].pop(field, None)
    story_fields = literals('ApiAdCreativeStorySpecFields')
    if 'instagram_actor_id' not in story_fields:
        raise ValueError('Missing private Instagram story identity field')
    manifest['object_fields']['story'] = sorted(set(story_fields))
    for route, edge, sender, parameters in [
        ('DELIVERY_ESTIMATE', 'delivery_estimate', 'AdsDeliveryEstimateDataLoader',
         ['targeting_spec','optimization_goal','promoted_object','currency','bid_strategy','attribution_spec',
          'skip_bid_suggestion','send_long_term_prediction_shadow_request','prediction_duration',
          'special_ad_categories','is_cbo_enabled','campaign_destination_type']),
        ('TARGETING_BROWSE', 'targetingbrowse', 'AdsUnifiedBrowseTreeDataSource',
         ['fields','include_nodes','whitelisted_types','optimization_goal','regulated_categories',
          'regulated_countries','is_exclusion','is_reserved','countries','excluded_category']),
    ]:
        names = [sender] + (['adsDeliveryEstimateDataLoaderGetParams','AdsUEditorCampaignPotentialReachSelector']
                            if route == 'DELIVERY_ESTIMATE' else [])
        sources = []
        for name in names:
            row = rows[name]
            if hashlib.sha256((row['module_prefix']+row['source']+row['module_suffix']).encode()).hexdigest() != row['sha256']:
                raise ValueError('Sender hash mismatch: ' + name)
            sources.append({'module': name, 'module_sha256':row['sha256'],
                'script_sha256':row['script_sha256'], 'observed_public_url':row['observed_url']})
        if 'edge("'+edge+'")' not in rows[sender]['source']:
            raise ValueError('Unobserved edge: ' + edge)
        manifest['routes'][route] = {'method':'GET','path':'/{account_node}/'+edge,
            'params':parameters, 'request_name':sender, 'source_module':sender, 'sources':sources}
    manifest['form_schema'] = {'source': 'reviewed_private_ads_manager_modules',
        'captured_at': '2026-10-13', 'schema': schema, 'standard_conversion_events': events,
        'placement_options': schema['targeting']['enums'], 'source_evidence': list(evidence.values()),
        'input_representation': 'JSON for values without a reviewed primitive binding',
        'checked_live': False, 'browser_started': False, 'transport': 'private_http_h2'}
    preview_modules = ['AdsLWIAdPreviewDataLoader','XAdsLWIAdPreviewControllerRouteBuilder',
                       'AsyncRequest','AdsPreviewApiFormatPositionsWithVideoFeedsVersionChange']
    preview_sources = []
    for name in preview_modules:
        row = rows[name]
        if hashlib.sha256((row['module_prefix']+row['source']+row['module_suffix']).encode()).hexdigest() != row['sha256']:
            raise ValueError('Preview source hash mismatch: '+name)
        preview_sources.append({'module':name,'module_sha256':row['sha256'],
            'script_sha256':row['script_sha256'],'observed_public_url':row['observed_url']})
    if '/ads/lwi/ad_preview_generator/' not in rows[preview_modules[1]]['source']:
        raise ValueError('Unreviewed preview path')
    if 'this.method="POST"' not in rows['AsyncRequest']['source']:
        raise ValueError('Unreviewed AsyncRequest method')
    formats = list(dict.fromkeys(re.findall(r'\b([A-Z][A-Z0-9_]+):\{platform:', rows[preview_modules[3]]['source'])))
    if 'MOBILE_FEED_STANDARD' not in formats:
        raise ValueError('Private preview formats unavailable')
    manifest['form_schema']['preview_formats'] = formats
    manifest['native_reads'] = {'CREATIVE_PREVIEW': {
        'method':'POST', 'path':'/ads/lwi/ad_preview_generator/',
        'query_params':['ad_account_id','ad_preview_format','creative_spec','is_sponsored'],
        'operation_kind':'read', 'request_name':'AdsLWIAdPreviewDataLoader.fetchAdPreview',
        'response_path':'payload.markup', 'sources':preview_sources}}
    return manifest


if __name__ == '__main__':
    index_path, manifest_path = map(Path, sys.argv[1:])
    result = build(json.loads(index_path.read_text()), json.loads(manifest_path.read_text()))
    manifest_path.write_text(json.dumps(result, indent=2) + '\n')
    print({group: len(row['fields']) for group, row in result['form_schema']['schema'].items()})
