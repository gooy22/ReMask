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

/* -------- Launch cache-bust -------- */
$launchPhp=file_get_contents($launchPhpPath);
if($launchPhp===false){fwrite(STDERR,"[meta-builder-v102] read launch.php failed\n");exit(320);}
$launchPhp=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260919-meta-builder-v102" type="module"></script>',
    $launchPhp,1,$lc
) ?? $launchPhp;
if($lc!==1){fwrite(STDERR,"[meta-builder-v102] launch cache tag missing\n");exit(321);}
file_put_contents($launchPhpPath,$launchPhp);

fwrite(STDERR,"[meta-builder-v102] official Meta builder backend ready\n");
