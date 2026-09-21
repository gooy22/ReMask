<?php
/**
 * v102: Full official Meta Marketing API builder handoff.
 * - Copies MetaOfficialFields whitelist into runtime.
 * - Allows saved Creative Library meta_builder to merge into launch payload.
 * - Uses the official-builder validation path instead of the older restrictive capability matrix.
 * - Forwards whitelisted Campaign / Ad Set / Creative / Ad parameters to Graph API.
 */
$root='/var/www/html';
$classSource='/tmp/railway-meta-official-fields.php';
$classPath=$root.'/classes/MetaOfficialFields.php';
$validatorPath=$root.'/classes/MetaLaunchValidator.php';
$servicePath=$root.'/classes/MetaAdsService.php';
$jobPath=$root.'/ajax/metaJobCreate.php';
$launchPath=$root.'/scripts/launch.js';
$launchPhpPath=$root.'/launch.php';

foreach([$classSource,$validatorPath,$servicePath,$jobPath,$launchPath,$launchPhpPath] as $p){
    if(!is_file($p)){fwrite(STDERR,"[meta-builder-v102] missing $p\n");exit(301);}
}
if(!copy($classSource,$classPath)){fwrite(STDERR,"[meta-builder-v102] could not copy MetaOfficialFields\n");exit(302);}

/* -------- MetaLaunchValidator official path -------- */
$validator=file_get_contents($validatorPath);
if($validator===false){fwrite(STDERR,"[meta-builder-v102] read validator failed\n");exit(303);}
if(strpos($validator,"MetaOfficialFields.php")===false){
    $anchor="require_once __DIR__ . '/MetaCapabilities.php';";
    if(strpos($validator,$anchor)===false){fwrite(STDERR,"[meta-builder-v102] validator require anchor missing\n");exit(304);}
    $validator=str_replace($anchor,$anchor."\nrequire_once __DIR__ . '/MetaOfficialFields.php';",$validator,$n);
}
if(strpos($validator,'REMASK_META_OFFICIAL_VALIDATOR_V1')===false){
    $anchor=<<<'PHP_CODE'
    public static function validateAndNormalize(array $payload): array
    {
PHP_CODE;
    $replace=<<<'PHP_CODE'
    public static function validateAndNormalize(array $payload): array
    {
        /* REMASK_META_OFFICIAL_VALIDATOR_V1 */
        if (!empty($payload['_meta_official_v1'])) {
            return MetaOfficialFields::validateAndNormalizePayload($payload);
        }
PHP_CODE;
    if(strpos($validator,$anchor)===false){fwrite(STDERR,"[meta-builder-v102] validator function anchor missing\n");exit(305);}
    $validator=str_replace($anchor,$replace,$validator,$n);
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] validator replacement count=$n\n");exit(306);}
}
file_put_contents($validatorPath,$validator);

/* -------- metaJobCreate applies saved builder before review/job persistence -------- */
$job=file_get_contents($jobPath);
if($job===false){fwrite(STDERR,"[meta-builder-v102] read job endpoint failed\n");exit(307);}
if(strpos($job,"MetaOfficialFields.php")===false){
    $anchor="require_once __DIR__ . '/../classes/MetaLaunchMediaValidator.php';";
    if(strpos($job,$anchor)===false){fwrite(STDERR,"[meta-builder-v102] job require anchor missing\n");exit(308);}
    $job=str_replace($anchor,$anchor."\nrequire_once __DIR__ . '/../classes/MetaOfficialFields.php';",$job,$n);
}
if(strpos($job,'REMASK_META_BUILDER_JOB_V1')===false){
    $anchor=<<<'PHP_CODE'
    $payload = json_decode($payloadRaw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($payload)) throw new InvalidArgumentException('payload must be a JSON object');
PHP_CODE;
    $replace=<<<'PHP_CODE'
    $payload = json_decode($payloadRaw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($payload)) throw new InvalidArgumentException('payload must be a JSON object');

    /* REMASK_META_BUILDER_JOB_V1 */
    $metaBuilderRaw = trim((string)($_POST['meta_builder'] ?? ''));
    if ($metaBuilderRaw !== '') {
        $metaBuilder = json_decode($metaBuilderRaw, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($metaBuilder)) throw new InvalidArgumentException('meta_builder must be a JSON object.');
        $payload = MetaOfficialFields::applyBuilderToPayload($payload, $metaBuilder);
    }
PHP_CODE;
    if(strpos($job,$anchor)===false){fwrite(STDERR,"[meta-builder-v102] job payload anchor missing\n");exit(309);}
    $job=str_replace($anchor,$replace,$job,$n);
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] job payload replacement count=$n\n");exit(310);}
}
file_put_contents($jobPath,$job);

/* -------- MetaAdsService forwards official whitelisted params -------- */
$service=file_get_contents($servicePath);
if($service===false){fwrite(STDERR,"[meta-builder-v102] read service failed\n");exit(311);}
if(strpos($service,"MetaOfficialFields.php")===false){
    $service=preg_replace('/^<\?php\s*/',"<?php\nrequire_once __DIR__ . '/MetaOfficialFields.php';\n",$service,1,$n) ?? $service;
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] service php anchor missing\n");exit(312);}
}
if(strpos($service,'REMASK_META_OFFICIAL_FORWARD_V1')===false){
    $campaignReturn=<<<'PHP_CODE'
        return $this->client->post("{$accountId}/campaigns", $params);
PHP_CODE;
    $campaignReplace=<<<'PHP_CODE'
        /* REMASK_META_OFFICIAL_FORWARD_V1 */
        $params = array_replace($params, MetaOfficialFields::officialParams('campaign', $campaign));
        return $this->client->post("{$accountId}/campaigns", $params);
PHP_CODE;
    if(strpos($service,$campaignReturn)===false){fwrite(STDERR,"[meta-builder-v102] campaign return missing\n");exit(313);}
    $service=str_replace($campaignReturn,$campaignReplace,$service,$n);
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] campaign return count=$n\n");exit(314);}

    $adsetReturn=<<<'PHP_CODE'
        return $this->client->post("{$accountId}/adsets", $params);
PHP_CODE;
    $adsetReplace=<<<'PHP_CODE'
        $params = array_replace($params, MetaOfficialFields::officialParams('adset', $adSet));
        $params['campaign_id'] = $campaignId;
        $params['targeting'] = $this->requiredArray($adSet, 'targeting');
        return $this->client->post("{$accountId}/adsets", $params);
PHP_CODE;
    if(strpos($service,$adsetReturn)===false){fwrite(STDERR,"[meta-builder-v102] adset return missing\n");exit(315);}
    $service=str_replace($adsetReturn,$adsetReplace,$service,$n);
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] adset return count=$n\n");exit(316);}

    $creativeTail=<<<'PHP_CODE'
        if (!empty($creative['url_tags'])) $params['url_tags'] = (string)$creative['url_tags'];
        return $this->client->post("{$accountId}/adcreatives", $params);
PHP_CODE;
    $creativeReplace=<<<'PHP_CODE'
        if (!empty($creative['url_tags'])) $params['url_tags'] = (string)$creative['url_tags'];
        $params = array_replace($params, MetaOfficialFields::officialParams('creative', $creative));
        return $this->client->post("{$accountId}/adcreatives", $params);
PHP_CODE;
    $service=str_replace($creativeTail,$creativeReplace,$service,$creativeCount);
    if($creativeCount < 2){fwrite(STDERR,"[meta-builder-v102] creative forward count=$creativeCount\n");exit(317);}

    $adOld=<<<'PHP_CODE'
    public function createAd(string $accountId, string $adSetId, string $creativeId, array $ad): array
    {
        $accountId = self::normalizeAccountId($accountId);
        return $this->client->post("{$accountId}/ads", [
            'name' => $this->requiredString($ad, 'name'),
            'adset_id' => $adSetId,
            'creative' => ['creative_id' => $creativeId],
            'status' => strtoupper((string)($ad['status'] ?? 'PAUSED')),
        ]);
    }
PHP_CODE;
    $adNew=<<<'PHP_CODE'
    public function createAd(string $accountId, string $adSetId, string $creativeId, array $ad): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = [
            'name' => $this->requiredString($ad, 'name'),
            'adset_id' => $adSetId,
            'creative' => ['creative_id' => $creativeId],
            'status' => strtoupper((string)($ad['status'] ?? 'PAUSED')),
        ];
        $params = array_replace($params, MetaOfficialFields::officialParams('ad', $ad));
        $params['adset_id'] = $adSetId;
        $params['creative'] = ['creative_id' => $creativeId];
        return $this->client->post("{$accountId}/ads", $params);
    }
PHP_CODE;
    if(strpos($service,$adOld)===false){fwrite(STDERR,"[meta-builder-v102] createAd block missing\n");exit(318);}
    $service=str_replace($adOld,$adNew,$service,$n);
    if($n!==1){fwrite(STDERR,"[meta-builder-v102] createAd replacement count=$n\n");exit(319);}
}
file_put_contents($servicePath,$service);

/* -------- Launch uses the exact saved Meta builder before Review / Job -------- */
$launch=file_get_contents($launchPath);
if($launch===false){fwrite(STDERR,"[meta-builder-v102] read launch.js failed\n");exit(320);}

if(strpos($launch,'REMASK_EFFECTIVE_META_BUILDER_V2')===false){
    $helper=<<<'JS_CODE'
/* REMASK_EFFECTIVE_META_BUILDER_V2 */
function remaskMergeBuilderObject(base, extra) {
    const out = (base && typeof base === 'object' && !Array.isArray(base)) ? {...base} : {};
    if (!extra || typeof extra !== 'object' || Array.isArray(extra)) return out;
    for (const [key, value] of Object.entries(extra)) {
        if (value && typeof value === 'object' && !Array.isArray(value)
            && out[key] && typeof out[key] === 'object' && !Array.isArray(out[key])) {
            out[key] = remaskMergeBuilderObject(out[key], value);
        } else {
            out[key] = value;
        }
    }
    return out;
}

function remaskApplySavedMetaBuilderToPayload(payload) {
    const builder = remaskSelectedCreativePreset?.meta_builder;
    if (!builder || typeof builder !== 'object') return payload;

    const out = remaskMergeBuilderObject({}, payload || {});
    out.campaign = {...(out.campaign || {}), ...(builder.campaign || {})};
    out.adset = {...(out.adset || {}), ...(builder.adset || {})};
    out.adset.targeting = remaskMergeBuilderObject(
        out.adset.targeting || {},
        builder.targeting || {}
    );

    out.creative = {...(out.creative || {})};
    for (const [key, value] of Object.entries(builder.identity || {})) {
        if (value !== '' && value !== null && value !== undefined) out.creative[key] = value;
    }

    const existingMedia = builder.existing_media && typeof builder.existing_media === 'object'
        ? builder.existing_media
        : {};
    if (existingMedia.image_hash) out.creative.existing_image_hash = String(existingMedia.image_hash);
    if (existingMedia.video_id) out.creative.existing_video_id = String(existingMedia.video_id);
    if (existingMedia.creative_id) out.creative.existing_creative_id = String(existingMedia.creative_id);
    if (existingMedia.source_instagram_media_id) {
        out.creative.source_instagram_media_id = String(existingMedia.source_instagram_media_id);
    }

    const creativeOfficial = {...(builder.creative || {})};
    if (!Object.keys(existingMedia).length && creativeOfficial.image_hash) {
        out.creative.existing_image_hash = String(creativeOfficial.image_hash);
        delete creativeOfficial.image_hash;
    }
    if (!Object.keys(existingMedia).length && creativeOfficial.video_id) {
        out.creative.existing_video_id = String(creativeOfficial.video_id);
        delete creativeOfficial.video_id;
    }
    if (!Object.keys(existingMedia).length && creativeOfficial.source_instagram_media_id) {
        out.creative.source_instagram_media_id = String(creativeOfficial.source_instagram_media_id);
        delete creativeOfficial.source_instagram_media_id;
    }
    out.creative.official_params = remaskMergeBuilderObject(
        out.creative.official_params || {},
        creativeOfficial
    );

    out.ad = {...(out.ad || {}), ...(builder.ad || {})};
    out._meta_official_v1 = true;
    return out;
}

/* REMASK_PLACEMENT_PREFLIGHT_V1 */
function remaskExplicitPlacementGroups(payload) {
    const targeting = payload?.adset?.targeting || {};
    const groups = [
        'publisher_platforms',
        'facebook_positions',
        'instagram_positions',
        'messenger_positions',
        'audience_network_positions',
        'threads_positions',
        'whatsapp_positions',
        'device_platforms'
    ];
    const out = {};
    for (const group of groups) {
        const values = Array.isArray(targeting[group])
            ? [...new Set(targeting[group].map((v) => String(v).trim()).filter(Boolean))]
            : [];
        if (values.length) out[group] = values;
    }
    return out;
}

async function remaskValidatePlacementsForLaunch(config) {
    const explicit = remaskExplicitPlacementGroups(config?.payload || {});
    const groups = Object.keys(explicit);
    if (!groups.length) return {ok:true, verified:false, checked:0, warnings:[]};

    const accountIds = Array.isArray(config?.accountIds) ? config.accountIds : [];
    const objective = String(config?.payload?.campaign?.objective || '');
    const optimizationGoal = String(config?.payload?.adset?.optimization_goal || '');
    const rows = accountIds.map((accountId) => {
        const target = typeof targetForAccount === 'function' ? targetForAccount(accountId) : null;
        return {
            accountId:String(accountId || '').replace(/^act_/i,''),
            profile:String(target?.profile || state.profile || '')
        };
    });

    const failures = [];
    const warnings = [];
    let checked = 0;
    let cursor = 0;
    const workers = Array.from({length:Math.min(4, Math.max(1, rows.length))}, async () => {
        while (true) {
            const index = cursor++;
            if (index >= rows.length) return;
            const row = rows[index];
            if (!row.accountId || !row.profile) {
                failures.push('RK ' + (row.accountId || '?') + ': Facebook profile context is missing.');
                continue;
            }
            try {
                const data = await apiJson('ajax/metaPlacementCapabilities.php', formPost({
                    profile:row.profile,
                    account_id:row.accountId,
                    objective,
                    optimization_goal:optimizationGoal
                }));
                if (data?.source !== 'meta_targetingbrowse') {
                    warnings.push('RK ' + row.accountId + ': Meta did not expose live placement capabilities.');
                    continue;
                }
                checked++;
                const options = data.options || {};
                for (const group of groups) {
                    const live = Array.isArray(options[group]) ? options[group].map(String) : [];
                    if (!live.length) {
                        warnings.push('RK ' + row.accountId + ': ' + group + ' was not verifiable from Meta.');
                        continue;
                    }
                    const invalid = explicit[group].filter((value) => !live.includes(String(value)));
                    if (invalid.length) {
                        failures.push('RK ' + row.accountId + ': unavailable ' + group + ' = ' + invalid.join(', '));
                    }
                }
            } catch (e) {
                const message = e?.payload?.message || e?.message || String(e);
                warnings.push('RK ' + row.accountId + ': placement verification unavailable — ' + message);
            }
        }
    });
    await Promise.all(workers);

    return {
        ok:failures.length === 0,
        verified:checked > 0,
        checked,
        failures,
        warnings
    };
}
JS_CODE;

    $helperAnchor='function ensureLaunchNames() {';
    if(strpos($launch,$helperAnchor)===false){fwrite(STDERR,"[meta-builder-v102] effective payload helper anchor missing\n");exit(324);}
    $launch=str_replace($helperAnchor,$helper."\n\n".$helperAnchor,$launch,$hc);
    if($hc!==1){fwrite(STDERR,"[meta-builder-v102] effective payload helper count=$hc\n");exit(325);}

    $payloadNeedle=<<<'JS_CODE'
    ensureLaunchNames();
    const payload = buildPayload();
JS_CODE;
    $payloadReplace=<<<'JS_CODE'
    ensureLaunchNames();
    let payload = buildPayload();
    payload = remaskApplySavedMetaBuilderToPayload(payload);
JS_CODE;
    if(strpos($launch,$payloadNeedle)===false){fwrite(STDERR,"[meta-builder-v102] current payload anchor missing\n");exit(326);}
    $launch=str_replace($payloadNeedle,$payloadReplace,$launch,$pc);
    if($pc!==1){fwrite(STDERR,"[meta-builder-v102] current payload replacement count=$pc\n");exit(327);}

    $placementNeedle=<<<'JS_CODE'
    const payload = config.payload;
    const accountOverrides = config.accountOverrides;

    // One-click flow: validate the exact selection against Meta before any mutation.
JS_CODE;
    $placementReplace=<<<'JS_CODE'
    const payload = config.payload;
    const accountOverrides = config.accountOverrides;

    const placementCheck = await remaskValidatePlacementsForLaunch(config);
    if (!placementCheck.ok) {
        show(
            $('launchResult'),
            'Launch blocked by RK-specific placements: ' + placementCheck.failures.join(' | '),
            'failed'
        );
        return;
    }
    if (placementCheck.verified) {
        show(
            $('launchResult'),
            'Placements verified from Meta for ' + placementCheck.checked + ' RK.' +
                (placementCheck.warnings?.length ? ' Warnings: ' + placementCheck.warnings.join(' | ') : ''),
            'ready'
        );
    }

    // One-click flow: validate the exact selection against Meta before any mutation.
JS_CODE;
    if(strpos($launch,$placementNeedle)===false){fwrite(STDERR,"[meta-builder-v102] placement preflight anchor missing\n");exit(328);}
    $launch=str_replace($placementNeedle,$placementReplace,$launch,$vc);
    if($vc!==1){fwrite(STDERR,"[meta-builder-v102] placement preflight replacement count=$vc\n");exit(329);}

    file_put_contents($launchPath,$launch);
}

/* -------- Launch budget validation supports saved daily/lifetime budgets -------- */
if(strpos($launch,'REMASK_META_BUILDER_BUDGET_V1')===false){
    $oldBudget=<<<'JS_CODE'
    /* REMASK_DAILY_BUDGET_GUARD_V1 */
    const campaignBudget = Number(payload.campaign.daily_budget || 0);
    const adsetBudget = Number(payload.adset.daily_budget || 0);
    if ((campaignBudget > 0) === (adsetBudget > 0)) {
        throw new Error('Set a positive Daily budget on exactly one level: Campaign or Ad Set.');
    }
    const activeDailyBudget = campaignBudget > 0 ? campaignBudget : adsetBudget;
    if (!Number.isInteger(activeDailyBudget)) {
        throw new Error('Daily budget must be an integer in minor currency units.');
    }
    if (activeDailyBudget <= 100) {
        throw new Error('Daily budget is too low. Enter more than 100 minor units (for USD: 101 = $1.01; recommended 200 = $2.00 or more).');
    }
JS_CODE;
    $newBudget=<<<'JS_CODE'
    /* REMASK_DAILY_BUDGET_GUARD_V1 */
    /* REMASK_META_BUILDER_BUDGET_V1 */
    const savedBuilder = remaskSelectedCreativePreset?.meta_builder || {};
    const campaignDaily = Number(savedBuilder.campaign?.daily_budget ?? payload.campaign.daily_budget ?? 0);
    const campaignLifetime = Number(savedBuilder.campaign?.lifetime_budget ?? payload.campaign.lifetime_budget ?? 0);
    const adsetDaily = Number(savedBuilder.adset?.daily_budget ?? payload.adset.daily_budget ?? 0);
    const adsetLifetime = Number(savedBuilder.adset?.lifetime_budget ?? payload.adset.lifetime_budget ?? 0);
    const budgetEntries = [
        ['Campaign daily', campaignDaily, true],
        ['Campaign lifetime', campaignLifetime, false],
        ['Ad Set daily', adsetDaily, true],
        ['Ad Set lifetime', adsetLifetime, false],
    ].filter(([, value]) => value > 0);
    if (budgetEntries.length !== 1) {
        throw new Error('Set exactly one budget: Campaign daily/lifetime OR Ad Set daily/lifetime.');
    }
    const [budgetName, activeBudget, isDailyBudget] = budgetEntries[0];
    if (!Number.isInteger(activeBudget)) {
        throw new Error(budgetName + ' budget must be an integer in minor currency units.');
    }
    if (isDailyBudget && activeBudget <= 100) {
        throw new Error('Daily budget is too low. Enter more than 100 minor units (for USD: 101 = $1.01; recommended 200 = $2.00 or more).');
    }
JS_CODE;
    if(strpos($launch,$oldBudget)===false){fwrite(STDERR,"[meta-builder-v102] legacy budget guard block missing\n");exit(322);}
    $launch=str_replace($oldBudget,$newBudget,$launch,$bc);
    if($bc!==1){fwrite(STDERR,"[meta-builder-v102] budget guard replacement count=$bc\n");exit(323);}
    file_put_contents($launchPath,$launch);
}

/* -------- Launch cache-bust -------- */
$launchPhp=file_get_contents($launchPhpPath);
if($launchPhp===false){fwrite(STDERR,"[meta-builder-v102] read launch.php failed\n");exit(320);}
$launchPhp=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260921-effective-builder-v110" type="module"></script>',
    $launchPhp,1,$lc
) ?? $launchPhp;
if($lc!==1){fwrite(STDERR,"[meta-builder-v102] launch cache tag missing\n");exit(321);}
file_put_contents($launchPhpPath,$launchPhp);

fwrite(STDERR,"[meta-builder-v102] official Meta builder + effective Review payload + RK placement preflight ready\n");
