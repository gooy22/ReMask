<?php
/**
 * Whitelist derived from the current official Meta Business SDK create endpoints.
 * It keeps ReMask flexible without blindly forwarding arbitrary user keys.
 */
final class MetaOfficialFields
{
    private const CAMPAIGN = [
        'adlabels','bid_strategy','budget_schedule_specs','buying_type','daily_budget',
        'execution_options','frequency_control_specs','is_adset_budget_sharing_enabled',
        'is_budget_schedule_enabled','is_direct_send_campaign','is_message_campaign',
        'is_meta_moment_maker_enabled','is_reels_trending_ads_enabled','is_skadnetwork_attribution',
        'iterative_split_test_configs','lifetime_budget','name','objective','pacing_type',
        'promoted_object','smart_promotion_type','source_campaign_id','special_ad_categories',
        'special_ad_category_country','spend_cap','start_time','status','stop_time','topline_id'
    ];

    private const ADSET = [
        'ad_set_goal','adlabels','adset_schedule','attribution_count_type','attribution_spec',
        'automatic_manual_state','bid_adjustments','bid_amount','bid_constraints','bid_strategy',
        'billing_event','brand_safety_config','budget_schedule_specs','budget_source',
        'budget_split_set_id','campaign_attribution','creative_sequence',
        'creative_sequence_repetition_pattern','daily_budget','daily_imps','daily_min_spend_target',
        'daily_spend_cap','date_format','destination_type','dsa_beneficiary','dsa_payor','end_time',
        'execution_options','existing_customer_budget_percentage','frequency_control_specs',
        'full_funnel_exploration_mode','is_ba_skip_delayed_eligible','is_budget_schedule_enabled',
        'is_dc_follow_optimized','is_dynamic_creative','is_incremental_attribution_enabled',
        'is_sac_cfca_terms_certified','is_sequenced_conversion_creation','lifetime_budget',
        'lifetime_imps','lifetime_min_spend_target','lifetime_spend_cap','line_number',
        'live_video_ad_campaign_config','max_budget_spend_percentage','meta_moment_maker_spec',
        'min_budget_spend_percentage','multi_event_conversion_attribution_window_seconds',
        'multi_optimization_goal_weight','name','optimization_goal','optimization_sub_event',
        'pacing_type','placement_soft_opt_out','promoted_object','rb_prediction_id',
        'regional_regulated_categories','regional_regulation_identities','relative_value',
        'rf_prediction_id','source_adset_id','start_time','status','time_based_ad_rotation_id_blocks',
        'time_based_ad_rotation_intervals','time_start','time_stop','topline_id',
        'trending_topics_spec','tune_for_category','value_rule_set_id','value_rules_applied'
    ];

    private const TARGETING = [
        'age_max','age_min','age_range','alternate_auto_targeting_option','app_install_state',
        'audience_network_positions','behaviors','brand_safety_content_filter_levels',
        'catalog_based_targeting','cities','college_years','connections',
        'contextual_targeting_categories','countries','country','country_groups','custom_audiences',
        'device_platforms','direct_install_devices','dynamic_audience_ids','education_majors',
        'education_schools','education_statuses','engagement_specs','ethnic_affinity',
        'exclude_reached_since','excluded_brand_safety_content_types','excluded_connections',
        'excluded_custom_audiences','excluded_dynamic_audience_ids','excluded_engagement_specs',
        'excluded_geo_locations','excluded_mobile_device_model','excluded_product_audience_specs',
        'excluded_publisher_categories','excluded_publisher_list_ids','excluded_user_device',
        'exclusions','facebook_positions','family_statuses','fb_deal_id','flexible_spec',
        'friends_of_connections','genders','generation','geo_locations','home_ownership',
        'home_type','home_value','household_composition','income','industries',
        'instagram_positions','install_state_application','instream_video_skippable_excluded',
        'interested_in','interests','is_whatsapp_destination_ad','keywords','life_events',
        'locales','messenger_positions','moms','net_worth','office_type','place_page_set_ids',
        'political_views','politics','product_audience_specs','prospecting_audience',
        'publisher_platforms','radius','regions','relationship_statuses','site_category',
        'subscriber_universe','targeting_automation','targeting_optimization',
        'targeting_relaxation_types','threads_positions','user_adclusters','user_age_unknown',
        'user_device','user_event','user_os','whatsapp_positions','wireless_carrier',
        'work_employers','work_positions','zips'
    ];

    private const CREATIVE = [
        'actor_id','ad_disclaimer_spec','adlabels','applink_treatment','asset_feed_spec',
        'authorization_category','body','branded_content','branded_content_sponsor_page_id',
        'bundle_folder_id','call_to_action','categorization_criteria','category_media_source',
        'contextual_multi_ads','creative_sourcing_spec','degrees_of_freedom_spec',
        'destination_set_id','destination_spec','dynamic_ad_voice','enable_launch_instant_app',
        'execution_options','existing_post_title','facebook_branded_content',
        'format_transformation_spec','generative_asset_spec','image_crops','image_file',
        'image_hash','image_url','instagram_branded_content','instagram_permalink_url',
        'instagram_user_id','interactive_components_spec','is_dco_internal','link_og_id',
        'link_url','marketing_message_structured_spec','media_optimization_spec',
        'media_sourcing_spec','name','object_id','object_story_id','object_story_spec',
        'object_type','object_url','omnichannel_link_spec','page_welcome_message',
        'place_page_set_id','platform_customizations','playable_asset_id','portrait_customizations',
        'product_set_id','product_suggestion_settings','recommender_settings',
        'regional_regulation_disclaimer_spec','source_facebook_post_id',
        'source_instagram_media_id','template_url','template_url_spec','thumbnail_url',
        'title','url_tags','use_page_actor_override','wamo_whatsapp_identity_spec'
    ];

    private const AD = [
        'ad_schedule_end_time','ad_schedule_start_time','adlabels','audience_id','bid_amount',
        'conversion_domain','creative_asset_groups_spec','creative_audience_pairing_persona',
        'creative_automation_spec','dataset_split_specs','date_format','display_sequence',
        'draft_adgroup_id','engagement_audience','execution_options','include_demolink_hashes',
        'name','priority','source_ad_id','status','tracking_specs'
    ];

    public static function sanitizeBuilder(array $builder): array
    {
        $out = [];
        foreach ([
            'campaign' => self::CAMPAIGN,
            'adset' => self::ADSET,
            'targeting' => self::TARGETING,
            'creative' => self::CREATIVE,
            'ad' => self::AD,
        ] as $section => $allowed) {
            $source = isset($builder[$section]) && is_array($builder[$section]) ? $builder[$section] : [];
            $out[$section] = self::pick($source, $allowed);
        }

        $identity = isset($builder['identity']) && is_array($builder['identity']) ? $builder['identity'] : [];
        $out['identity'] = self::pick($identity, ['page_id','instagram_actor_id']);

        return $out;
    }

    public static function applyBuilderToPayload(array $payload, array $builder): array
    {
        $builder = self::sanitizeBuilder($builder);

        $payload['campaign'] = array_replace(
            is_array($payload['campaign'] ?? null) ? $payload['campaign'] : [],
            $builder['campaign']
        );
        $payload['adset'] = array_replace(
            is_array($payload['adset'] ?? null) ? $payload['adset'] : [],
            $builder['adset']
        );

        $targeting = is_array($payload['adset']['targeting'] ?? null) ? $payload['adset']['targeting'] : [];
        $payload['adset']['targeting'] = array_replace_recursive($targeting, $builder['targeting']);

        $payload['creative'] = is_array($payload['creative'] ?? null) ? $payload['creative'] : [];
        foreach ($builder['identity'] as $key => $value) {
            $payload['creative'][$key] = $value;
        }
        $payload['creative']['official_params'] = $builder['creative'];

        $payload['ad'] = array_replace(
            is_array($payload['ad'] ?? null) ? $payload['ad'] : [],
            $builder['ad']
        );

        $payload['_meta_official_v1'] = true;
        return $payload;
    }

    public static function validateAndNormalizePayload(array $payload): array
    {
        foreach (['campaign','adset','creative','ad'] as $key) {
            if (!isset($payload[$key]) || !is_array($payload[$key])) {
                throw new InvalidArgumentException("$key must be an object.");
            }
        }

        foreach ([
            ['campaign','name'], ['campaign','objective'],
            ['adset','name'], ['adset','optimization_goal'], ['adset','billing_event'],
            ['creative','name'], ['ad','name']
        ] as [$section,$field]) {
            if (trim((string)($payload[$section][$field] ?? '')) === '') {
                throw new InvalidArgumentException("$section.$field is required.");
            }
        }

        if (!isset($payload['adset']['targeting']) || !is_array($payload['adset']['targeting']) || $payload['adset']['targeting'] === []) {
            throw new InvalidArgumentException('adset.targeting is required.');
        }

        $hasBudget = false;
        foreach ([['campaign','daily_budget'],['campaign','lifetime_budget'],['adset','daily_budget'],['adset','lifetime_budget']] as [$section,$field]) {
            $value = $payload[$section][$field] ?? null;
            if ($value === null || $value === '') continue;
            if (!is_numeric($value) || (int)$value <= 0) {
                throw new InvalidArgumentException("$section.$field must be a positive integer in minor currency units.");
            }
            $payload[$section][$field] = (int)$value;
            $hasBudget = true;
        }
        if (!$hasBudget) throw new InvalidArgumentException('Set a daily_budget or lifetime_budget on Campaign or Ad Set.');

        foreach (['campaign','adset','ad'] as $section) {
            if (isset($payload[$section]['status'])) {
                $payload[$section]['status'] = strtoupper((string)$payload[$section]['status']);
            }
        }
        $payload['campaign']['objective'] = strtoupper((string)$payload['campaign']['objective']);
        $payload['adset']['optimization_goal'] = strtoupper((string)$payload['adset']['optimization_goal']);
        $payload['adset']['billing_event'] = strtoupper((string)$payload['adset']['billing_event']);
        if (isset($payload['campaign']['bid_strategy'])) $payload['campaign']['bid_strategy'] = strtoupper((string)$payload['campaign']['bid_strategy']);
        if (isset($payload['adset']['bid_strategy'])) $payload['adset']['bid_strategy'] = strtoupper((string)$payload['adset']['bid_strategy']);

        $payload['_meta_official_v1'] = true;
        return $payload;
    }

    public static function officialParams(string $section, array $source): array
    {
        return match ($section) {
            'campaign' => self::pick($source, self::CAMPAIGN),
            'adset' => self::pick($source, self::ADSET),
            'creative' => self::pick(is_array($source['official_params'] ?? null) ? $source['official_params'] : [], self::CREATIVE),
            'ad' => self::pick($source, self::AD),
            default => [],
        };
    }

    private static function pick(array $source, array $allowed): array
    {
        $allowedMap = array_fill_keys($allowed, true);
        $out = [];
        foreach ($source as $key => $value) {
            if (!is_string($key) || !isset($allowedMap[$key])) continue;
            if ($value === '' || $value === null) continue;
            $out[$key] = $value;
        }
        return $out;
    }
}
