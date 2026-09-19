<?php
/**
 * ReMask Meta SDK schema snapshot.
 * Source: official facebook/facebook-python-business-sdk main branch,
 * AdAccount create_campaign/create_ad_set/create_ad_creative/create_ad
 * and Targeting fields, inspected 2026-09-19.
 */
final class MetaSdkSchema
{
    public static function schema(): array
    {
        return [
            'campaign' => [
                'fields' => [
                    'adlabels'=>'list<Object>',
                    'bid_strategy'=>'enum',
                    'budget_schedule_specs'=>'list<Object>',
                    'buying_type'=>'string',
                    'daily_budget'=>'unsigned int',
                    'execution_options'=>'list<enum>',
                    'frequency_control_specs'=>'list<Object>',
                    'is_adset_budget_sharing_enabled'=>'bool',
                    'is_budget_schedule_enabled'=>'bool',
                    'is_direct_send_campaign'=>'bool',
                    'is_message_campaign'=>'bool',
                    'is_meta_moment_maker_enabled'=>'bool',
                    'is_reels_trending_ads_enabled'=>'bool',
                    'is_skadnetwork_attribution'=>'bool',
                    'iterative_split_test_configs'=>'list<Object>',
                    'lifetime_budget'=>'unsigned int',
                    'name'=>'string',
                    'objective'=>'enum',
                    'pacing_type'=>'list<string>',
                    'promoted_object'=>'Object',
                    'smart_promotion_type'=>'enum',
                    'source_campaign_id'=>'string',
                    'special_ad_categories'=>'list<enum>',
                    'special_ad_category_country'=>'list<string>',
                    'spend_cap'=>'unsigned int',
                    'start_time'=>'datetime',
                    'status'=>'enum',
                    'stop_time'=>'datetime',
                    'topline_id'=>'string',
                ],
                'enums' => [
                    'bid_strategy'=>['COST_CAP','LOWEST_COST_WITHOUT_CAP','LOWEST_COST_WITH_BID_CAP','LOWEST_COST_WITH_MIN_ROAS'],
                    'objective'=>[
                        'APP_INSTALLS','BRAND_AWARENESS','CONVERSIONS','EVENT_RESPONSES','LEAD_GENERATION',
                        'LINK_CLICKS','LOCAL_AWARENESS','MESSAGES','OFFER_CLAIMS','OUTCOME_APP_PROMOTION',
                        'OUTCOME_AWARENESS','OUTCOME_ENGAGEMENT','OUTCOME_LEADS','OUTCOME_SALES','OUTCOME_TRAFFIC',
                        'PAGE_LIKES','POST_ENGAGEMENT','PRODUCT_CATALOG_SALES','REACH','STORE_VISITS','VIDEO_VIEWS'
                    ],
                    'special_ad_categories'=>[
                        'CREDIT','EMPLOYMENT','FINANCIAL_PRODUCTS_SERVICES','HOUSING',
                        'ISSUES_ELECTIONS_POLITICS','NONE','ONLINE_GAMBLING_AND_GAMING'
                    ],
                    'status'=>['ACTIVE','ARCHIVED','DELETED','PAUSED'],
                    'smart_promotion_type'=>['GUIDED_CREATION','SMART_APP_PROMOTION'],
                    'execution_options'=>['include_recommendations','validate_only'],
                ],
            ],
            'adset' => [
                'fields' => [
                    'ad_set_goal'=>'map',
                    'adlabels'=>'list<Object>',
                    'adset_schedule'=>'list<Object>',
                    'attribution_count_type'=>'enum',
                    'attribution_spec'=>'list<map>',
                    'automatic_manual_state'=>'enum',
                    'bid_adjustments'=>'Object',
                    'bid_amount'=>'int',
                    'bid_constraints'=>'map<string,Object>',
                    'bid_strategy'=>'enum',
                    'billing_event'=>'enum',
                    'brand_safety_config'=>'map',
                    'budget_schedule_specs'=>'list<Object>',
                    'budget_source'=>'enum',
                    'budget_split_set_id'=>'string',
                    'campaign_attribution'=>'Object',
                    'creative_sequence'=>'list<string>',
                    'creative_sequence_repetition_pattern'=>'enum',
                    'daily_budget'=>'unsigned int',
                    'daily_imps'=>'unsigned int',
                    'daily_min_spend_target'=>'unsigned int',
                    'daily_spend_cap'=>'unsigned int',
                    'date_format'=>'string',
                    'destination_type'=>'enum',
                    'dsa_beneficiary'=>'string',
                    'dsa_payor'=>'string',
                    'end_time'=>'datetime',
                    'execution_options'=>'list<enum>',
                    'existing_customer_budget_percentage'=>'unsigned int',
                    'frequency_control_specs'=>'list<Object>',
                    'full_funnel_exploration_mode'=>'enum',
                    'is_ba_skip_delayed_eligible'=>'bool',
                    'is_budget_schedule_enabled'=>'bool',
                    'is_dc_follow_optimized'=>'bool',
                    'is_dynamic_creative'=>'bool',
                    'is_incremental_attribution_enabled'=>'bool',
                    'is_sac_cfca_terms_certified'=>'bool',
                    'is_sequenced_conversion_creation'=>'bool',
                    'lifetime_budget'=>'unsigned int',
                    'lifetime_imps'=>'unsigned int',
                    'lifetime_min_spend_target'=>'unsigned int',
                    'lifetime_spend_cap'=>'unsigned int',
                    'line_number'=>'unsigned int',
                    'live_video_ad_campaign_config'=>'Object',
                    'max_budget_spend_percentage'=>'unsigned int',
                    'meta_moment_maker_spec'=>'map',
                    'min_budget_spend_percentage'=>'unsigned int',
                    'multi_event_conversion_attribution_window_seconds'=>'unsigned int',
                    'multi_optimization_goal_weight'=>'enum',
                    'name'=>'string',
                    'optimization_goal'=>'enum',
                    'optimization_sub_event'=>'enum',
                    'pacing_type'=>'list<string>',
                    'placement_soft_opt_out'=>'Object',
                    'promoted_object'=>'Object',
                    'rb_prediction_id'=>'string',
                    'regional_regulated_categories'=>'list<enum>',
                    'regional_regulation_identities'=>'map',
                    'relative_value'=>'float',
                    'rf_prediction_id'=>'string',
                    'source_adset_id'=>'string',
                    'start_time'=>'datetime',
                    'status'=>'enum',
                    'time_based_ad_rotation_id_blocks'=>'list<list<unsigned int>>',
                    'time_based_ad_rotation_intervals'=>'list<unsigned int>',
                    'time_start'=>'datetime',
                    'time_stop'=>'datetime',
                    'topline_id'=>'string',
                    'trending_topics_spec'=>'map',
                    'tune_for_category'=>'enum',
                    'value_rule_set_id'=>'string',
                    'value_rules_applied'=>'bool',
                ],
                'enums' => [
                    'attribution_count_type'=>['ALL_CONVERSIONS','FIRST_CONVERSION'],
                    'automatic_manual_state'=>['AUTOMATIC','MANUAL','UNSET'],
                    'bid_strategy'=>['COST_CAP','LOWEST_COST_WITHOUT_CAP','LOWEST_COST_WITH_BID_CAP','LOWEST_COST_WITH_MIN_ROAS'],
                    'billing_event'=>['APP_INSTALLS','CLICKS','IMPRESSIONS','LINK_CLICKS','LISTING_INTERACTION','NONE','OFFER_CLAIMS','PAGE_LIKES','POST_ENGAGEMENT','PURCHASE','THRUPLAY'],
                    'budget_source'=>['NONE','RMN'],
                    'creative_sequence_repetition_pattern'=>['FULL_SEQUENCE','LAST_AD'],
                    'destination_type'=>[
                        'APP','APPLINKS_AUTOMATIC','FACEBOOK','FACEBOOK_LIVE','FACEBOOK_PAGE','IMAGINE',
                        'INSTAGRAM_DIRECT','INSTAGRAM_LIVE','INSTAGRAM_PROFILE','INSTAGRAM_PROFILE_AND_FACEBOOK_PAGE',
                        'MESSAGING_INSTAGRAM_DIRECT_MESSENGER','MESSAGING_INSTAGRAM_DIRECT_MESSENGER_WHATSAPP',
                        'MESSAGING_INSTAGRAM_DIRECT_WHATSAPP','MESSAGING_MESSENGER_WHATSAPP','MESSENGER',
                        'ON_AD','ON_EVENT','ON_PAGE','ON_POST','ON_VIDEO','SHOP_AUTOMATIC','WEBSITE','WHATSAPP'
                    ],
                    'execution_options'=>['include_recommendations','validate_only'],
                    'full_funnel_exploration_mode'=>['EXTENDED_EXPLORATION','LIMITED_EXPLORATION','NONE_EXPLORATION'],
                    'multi_optimization_goal_weight'=>['BALANCED','PREFER_EVENT','PREFER_INSTALL','UNDEFINED'],
                    'optimization_goal'=>[
                        'ADVERTISER_SILOED_VALUE','AD_RECALL_LIFT','APP_INSTALLS','APP_INSTALLS_AND_OFFSITE_CONVERSIONS',
                        'AUTOMATIC_OBJECTIVE','CONVERSATIONS','DERIVED_EVENTS','ENGAGED_PAGE_VIEWS','ENGAGED_USERS',
                        'EVENT_RESPONSES','IMPRESSIONS','IN_APP_VALUE','LANDING_PAGE_VIEWS','LEAD_GENERATION','LINK_CLICKS',
                        'MEANINGFUL_CALL_ATTEMPT','MESSAGING_APPOINTMENT_CONVERSION','MESSAGING_DEEP_CONVERSATION_AND_FOLLOW',
                        'MESSAGING_PURCHASE_CONVERSION','NONE','OFFSITE_CONVERSIONS','PAGE_LIKES','POST_ENGAGEMENT',
                        'PROFILE_AND_PAGE_ENGAGEMENT','PROFILE_VISIT','QUALITY_CALL','QUALITY_LEAD','REACH','REMINDERS_SET',
                        'SUBSCRIBERS','THRUPLAY','VALUE','VISIT_INSTAGRAM_PROFILE'
                    ],
                    'optimization_sub_event'=>[
                        'NONE','POST_INTERACTION','TRAVEL_INTENT','TRAVEL_INTENT_BUCKET_01','TRAVEL_INTENT_BUCKET_02',
                        'TRAVEL_INTENT_BUCKET_03','TRAVEL_INTENT_BUCKET_04','TRAVEL_INTENT_BUCKET_05',
                        'TRAVEL_INTENT_NO_DESTINATION_INTENT','TRIP_CONSIDERATION','VIDEO_SOUND_ON'
                    ],
                    'status'=>['ACTIVE','ARCHIVED','DELETED','PAUSED'],
                    'tune_for_category'=>['CREDIT','EMPLOYMENT','FINANCIAL_PRODUCTS_SERVICES','HOUSING','ISSUES_ELECTIONS_POLITICS','NONE','ONLINE_GAMBLING_AND_GAMING'],
                ],
            ],
            'creative' => [
                'fields' => [
                    'actor_id'=>'unsigned int','ad_disclaimer_spec'=>'map','adlabels'=>'list<Object>',
                    'applink_treatment'=>'enum','asset_feed_spec'=>'Object','authorization_category'=>'enum',
                    'body'=>'string','branded_content'=>'map','branded_content_sponsor_page_id'=>'string',
                    'bundle_folder_id'=>'string','call_to_action'=>'Object','categorization_criteria'=>'enum',
                    'category_media_source'=>'enum','contextual_multi_ads'=>'map','creative_sourcing_spec'=>'map',
                    'degrees_of_freedom_spec'=>'map','destination_set_id'=>'string','destination_spec'=>'map',
                    'dynamic_ad_voice'=>'enum','enable_launch_instant_app'=>'bool','execution_options'=>'list<enum>',
                    'existing_post_title'=>'string','facebook_branded_content'=>'map','format_transformation_spec'=>'list<map>',
                    'generative_asset_spec'=>'map','image_crops'=>'map','image_file'=>'string','image_hash'=>'string',
                    'image_url'=>'string','instagram_branded_content'=>'map','instagram_permalink_url'=>'string',
                    'instagram_user_id'=>'string','interactive_components_spec'=>'map','is_dco_internal'=>'bool',
                    'link_og_id'=>'string','link_url'=>'string','marketing_message_structured_spec'=>'map',
                    'media_optimization_spec'=>'map','media_sourcing_spec'=>'map','name'=>'string',
                    'object_id'=>'unsigned int','object_story_id'=>'string','object_story_spec'=>'Object',
                    'object_type'=>'string','object_url'=>'string','omnichannel_link_spec'=>'map',
                    'page_welcome_message'=>'string','place_page_set_id'=>'string','platform_customizations'=>'Object',
                    'playable_asset_id'=>'string','portrait_customizations'=>'map','product_set_id'=>'string',
                    'product_suggestion_settings'=>'map','recommender_settings'=>'map','regional_regulation_disclaimer_spec'=>'map',
                    'source_facebook_post_id'=>'string','source_instagram_media_id'=>'string','template_url'=>'string',
                    'template_url_spec'=>'string','thumbnail_url'=>'string','title'=>'string','url_tags'=>'string',
                    'use_page_actor_override'=>'bool','wamo_whatsapp_identity_spec'=>'map',
                ],
                'enums'=>[],
            ],
            'ad' => [
                'fields' => [
                    'ad_schedule_end_time'=>'datetime','ad_schedule_start_time'=>'datetime','adlabels'=>'list<Object>','audience_id'=>'string','bid_amount'=>'int',
                    'conversion_domain'=>'string','creative_asset_groups_spec'=>'Object',
                    'creative_audience_pairing_persona'=>'map','creative_automation_spec'=>'Object',
                    'dataset_split_specs'=>'list<map>','date_format'=>'string','display_sequence'=>'unsigned int',
                    'draft_adgroup_id'=>'string','engagement_audience'=>'bool','execution_options'=>'list<enum>',
                    'include_demolink_hashes'=>'bool','name'=>'string','priority'=>'unsigned int',
                    'source_ad_id'=>'string','status'=>'enum','tracking_specs'=>'Object',
                ],
                'enums'=>[
                    'status'=>['ACTIVE','ARCHIVED','DELETED','PAUSED'],
                    'execution_options'=>['include_recommendations','validate_only'],
                ],
            ],
            'targeting' => [
                'fields' => [
'age_max'=>'unsigned int','age_min'=>'unsigned int','age_range'=>'list<unsigned int>',
                    'alternate_auto_targeting_option'=>'string','app_install_state'=>'string',
                    'audience_network_positions'=>'list<string>','behaviors'=>'list<IDName>',
                    'brand_safety_content_filter_levels'=>'list<string>','catalog_based_targeting'=>'Object',
                    'cities'=>'list<IDName>','college_years'=>'list<unsigned int>','connections'=>'list<Object>',
                    'contextual_targeting_categories'=>'list<IDName>','countries'=>'list<string>','country'=>'list<string>',
                    'country_groups'=>'list<string>','custom_audiences'=>'list<Object>','device_platforms'=>'list<enum>',
                    'direct_install_devices'=>'bool','dynamic_audience_ids'=>'list<string>','education_majors'=>'list<IDName>',
                    'education_schools'=>'list<IDName>','education_statuses'=>'list<unsigned int>','engagement_specs'=>'list<Object>',
                    'ethnic_affinity'=>'list<IDName>','exclude_reached_since'=>'list<string>',
                    'excluded_brand_safety_content_types'=>'list<string>','excluded_connections'=>'list<Object>',
                    'excluded_custom_audiences'=>'list<Object>','excluded_dynamic_audience_ids'=>'list<string>',
                    'excluded_engagement_specs'=>'list<Object>','excluded_geo_locations'=>'Object',
                    'excluded_mobile_device_model'=>'list<string>','excluded_product_audience_specs'=>'list<Object>',
                    'excluded_publisher_categories'=>'list<string>','excluded_publisher_list_ids'=>'list<string>',
                    'excluded_user_device'=>'list<string>','exclusions'=>'Object','facebook_positions'=>'list<string>',
                    'family_statuses'=>'list<IDName>','fb_deal_id'=>'string','flexible_spec'=>'list<Object>',
                    'friends_of_connections'=>'list<Object>','genders'=>'list<unsigned int>','generation'=>'list<IDName>',
                    'geo_locations'=>'Object','home_ownership'=>'list<IDName>','home_type'=>'list<IDName>',
                    'home_value'=>'list<IDName>','household_composition'=>'list<IDName>','income'=>'list<IDName>',
                    'industries'=>'list<IDName>','instagram_positions'=>'list<string>','install_state_application'=>'string',
                    'instream_video_skippable_excluded'=>'bool','interested_in'=>'list<unsigned int>','interests'=>'list<IDName>',
                    'is_whatsapp_destination_ad'=>'bool','keywords'=>'list<string>','life_events'=>'list<IDName>',
                    'locales'=>'list<unsigned int>','messenger_positions'=>'list<string>','moms'=>'list<IDName>',
                    'net_worth'=>'list<IDName>','office_type'=>'list<IDName>','place_page_set_ids'=>'list<string>',
                    'political_views'=>'list<unsigned int>','politics'=>'list<IDName>','product_audience_specs'=>'list<Object>',
                    'prospecting_audience'=>'Object','publisher_platforms'=>'list<string>','radius'=>'string',
                    'regions'=>'list<IDName>','relationship_statuses'=>'list<unsigned int>','site_category'=>'list<string>',
                    'subscriber_universe'=>'Object','targeting_automation'=>'Object','targeting_optimization'=>'string',
                    'targeting_relaxation_types'=>'Object','threads_positions'=>'list<string>','user_adclusters'=>'list<IDName>',
                    'user_age_unknown'=>'bool','user_device'=>'list<string>','user_event'=>'list<unsigned int>',
                    'user_os'=>'list<string>','whatsapp_positions'=>'list<string>','wireless_carrier'=>'list<string>',
                    'work_employers'=>'list<IDName>','work_positions'=>'list<IDName>','zips'=>'list<string>',
                ],
                'enums'=>[
                    'device_platforms'=>['connected_tv','desktop','mobile'],
                    'publisher_platforms'=>['facebook','instagram','messenger','audience_network','threads','whatsapp'],
                ],
            ],
        ];
    }

    public static function filter(string $group, array $input): array
    {
        $schema = self::schema();
        $allowed = array_keys((array)($schema[$group]['fields'] ?? []));
        if ($allowed === []) return [];
        $out = [];
        foreach ($allowed as $field) {
            if (!array_key_exists($field, $input)) continue;
            $value = $input[$field];
            if ($value === null || $value === '') continue;
            $out[$field] = $value;
        }
        return $out;
    }

    public static function filterTargeting(array $input): array
    {
        return self::filter('targeting', $input);
    }
}
