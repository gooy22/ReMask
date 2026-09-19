<?php
/**
 * v102 Meta SDK passthrough backend.
 * Uses official SDK field whitelist, keeps ReMask-generated IDs/media authoritative.
 */
$root='/var/www/html';
$schemaSrc='/tmp/remask-v102-MetaSdkSchema.php';
$schemaDst=$root.'/classes/MetaSdkSchema.php';
if(!is_file($schemaSrc)){fwrite(STDERR,"[meta-sdk-v102] missing schema source\n");exit(301);}
copy($schemaSrc,$schemaDst);

$servicePath=$root.'/classes/MetaAdsService.php';
$validatorPath=$root.'/classes/MetaLaunchValidator.php';
$jobPath=$root.'/ajax/metaJobCreate.php';
foreach([$servicePath,$validatorPath,$jobPath] as $p){
    if(!is_file($p)){fwrite(STDERR,"[meta-sdk-v102] missing $p\n");exit(302);}
}

/* ---------- MetaAdsService ---------- */
$service=file_get_contents($servicePath);
if($service===false){fwrite(STDERR,"[meta-sdk-v102] service read failed\n");exit(303);}
if(strpos($service,"require_once __DIR__ . '/MetaSdkSchema.php';")===false){
    $service=preg_replace(
        '/<\?php\s*/',
        "<?php\nrequire_once __DIR__ . '/MetaSdkSchema.php';\n",
        $service,
        1,
        $count
    ) ?? $service;
    if($count!==1){fwrite(STDERR,"[meta-sdk-v102] service require insertion failed\n");exit(304);}
}

$campaign=<<<'PHP_CODE'
    public function createCampaign(string $accountId, array $campaign): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = MetaSdkSchema::filter('campaign', $campaign);
        $params['name'] = $this->requiredString($campaign, 'name');
        $params['objective'] = strtoupper($this->requiredString($campaign, 'objective'));
        $params['status'] = strtoupper((string)($campaign['status'] ?? 'PAUSED'));
        if (!array_key_exists('special_ad_categories', $params)) {
            $params['special_ad_categories'] = $campaign['special_ad_categories'] ?? [];
        }

        $hasCampaignBudget =
            (array_key_exists('daily_budget', $params) && $params['daily_budget'] !== '' && $params['daily_budget'] !== null) ||
            (array_key_exists('lifetime_budget', $params) && $params['lifetime_budget'] !== '' && $params['lifetime_budget'] !== null);
        if (!$hasCampaignBudget && !array_key_exists('is_adset_budget_sharing_enabled', $params)) {
            $params['is_adset_budget_sharing_enabled'] = false;
        }
        return $this->client->post("{$accountId}/campaigns", $params);
    }
PHP_CODE;
$service=preg_replace(
    '/    public function createCampaign\(string \$accountId, array \$campaign\): array\s*\{[\s\S]*?\n    \}\n\n    public function createAdSet/',
    $campaign."\n\n    public function createAdSet",
    $service,1,$c1
) ?? $service;
if($c1!==1){fwrite(STDERR,"[meta-sdk-v102] createCampaign replace=$c1\n");exit(305);}

$adset=<<<'PHP_CODE'
    public function createAdSet(string $accountId, string $campaignId, array $adSet): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = MetaSdkSchema::filter('adset', $adSet);
        $params['name'] = $this->requiredString($adSet, 'name');
        $params['campaign_id'] = $campaignId;
        $params['optimization_goal'] = strtoupper((string)($adSet['optimization_goal'] ?? 'LINK_CLICKS'));
        $params['billing_event'] = strtoupper((string)($adSet['billing_event'] ?? 'IMPRESSIONS'));
        $params['targeting'] = MetaSdkSchema::filterTargeting($this->requiredArray($adSet, 'targeting'));
        $params['status'] = strtoupper((string)($adSet['status'] ?? 'PAUSED'));
        return $this->client->post("{$accountId}/adsets", $params);
    }
PHP_CODE;
$service=preg_replace(
    '/    public function createAdSet\(string \$accountId, string \$campaignId, array \$adSet\): array\s*\{[\s\S]*?\n    \}\n\n    public function uploadImage/',
    $adset."\n\n    public function uploadImage",
    $service,1,$c2
) ?? $service;
if($c2!==1){fwrite(STDERR,"[meta-sdk-v102] createAdSet replace=$c2\n");exit(306);}

$imageCreative=<<<'PHP_CODE'
    public function createImageCreative(string $accountId, array $creative, string $imageHash): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = MetaSdkSchema::filter('creative', $creative);

        $linkData = [
            'link' => $this->requiredString($creative, 'link'),
            'message' => $this->requiredString($creative, 'message'),
            'image_hash' => $imageHash,
            'call_to_action' => ['type' => strtoupper((string)($creative['call_to_action'] ?? 'LEARN_MORE'))],
        ];
        if (!empty($creative['headline'])) $linkData['name'] = (string)$creative['headline'];
        if (!empty($creative['description'])) $linkData['description'] = (string)$creative['description'];

        $customStory = isset($params['object_story_spec']) && is_array($params['object_story_spec'])
            ? $params['object_story_spec']
            : [];
        $customLink = isset($customStory['link_data']) && is_array($customStory['link_data'])
            ? $customStory['link_data']
            : [];
        $linkData = array_replace_recursive($customLink, $linkData);

        $story = array_replace_recursive($customStory, [
            'page_id' => $this->requiredString($creative, 'page_id'),
            'link_data' => $linkData,
        ]);
        if (!empty($creative['instagram_actor_id'])) {
            $story['instagram_actor_id'] = (string)$creative['instagram_actor_id'];
        }

        $params['name'] = $this->requiredString($creative, 'name');
        $params['object_story_spec'] = $story;
        if (!empty($creative['url_tags'])) $params['url_tags'] = (string)$creative['url_tags'];
        return $this->client->post("{$accountId}/adcreatives", $params);
    }
PHP_CODE;
$service=preg_replace(
    '/    public function createImageCreative\(string \$accountId, array \$creative, string \$imageHash\): array\s*\{[\s\S]*?\n    \}\n\n    public function createVideoCreative/',
    $imageCreative."\n\n    public function createVideoCreative",
    $service,1,$c3
) ?? $service;
if($c3!==1){fwrite(STDERR,"[meta-sdk-v102] image creative replace=$c3\n");exit(307);}

$videoCreative=<<<'PHP_CODE'
    public function createVideoCreative(string $accountId, array $creative, string $videoId): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = MetaSdkSchema::filter('creative', $creative);

        $videoData = [
            'video_id' => $videoId,
            'message' => $this->requiredString($creative, 'message'),
            'call_to_action' => [
                'type' => strtoupper((string)($creative['call_to_action'] ?? 'LEARN_MORE')),
                'value' => ['link' => $this->requiredString($creative, 'link')],
            ],
        ];
        if (!empty($creative['headline'])) $videoData['title'] = (string)$creative['headline'];
        if (!empty($creative['description'])) $videoData['link_description'] = (string)$creative['description'];

        $customStory = isset($params['object_story_spec']) && is_array($params['object_story_spec'])
            ? $params['object_story_spec']
            : [];
        $customVideo = isset($customStory['video_data']) && is_array($customStory['video_data'])
            ? $customStory['video_data']
            : [];
        $videoData = array_replace_recursive($customVideo, $videoData);

        $story = array_replace_recursive($customStory, [
            'page_id' => $this->requiredString($creative, 'page_id'),
            'video_data' => $videoData,
        ]);
        if (!empty($creative['instagram_actor_id'])) {
            $story['instagram_actor_id'] = (string)$creative['instagram_actor_id'];
        }

        $params['name'] = $this->requiredString($creative, 'name');
        $params['object_story_spec'] = $story;
        if (!empty($creative['url_tags'])) $params['url_tags'] = (string)$creative['url_tags'];
        return $this->client->post("{$accountId}/adcreatives", $params);
    }
PHP_CODE;
$service=preg_replace(
    '/    public function createVideoCreative\(string \$accountId, array \$creative, string \$videoId\): array\s*\{[\s\S]*?\n    \}\n\n    public function createAd/',
    $videoCreative."\n\n    public function createAd",
    $service,1,$c4
) ?? $service;
if($c4!==1){fwrite(STDERR,"[meta-sdk-v102] video creative replace=$c4\n");exit(308);}

$ad=<<<'PHP_CODE'
    public function createAd(string $accountId, string $adSetId, string $creativeId, array $ad): array
    {
        $accountId = self::normalizeAccountId($accountId);
        $params = MetaSdkSchema::filter('ad', $ad);
        $params['name'] = $this->requiredString($ad, 'name');
        $params['adset_id'] = $adSetId;
        $params['creative'] = ['creative_id' => $creativeId];
        $params['status'] = strtoupper((string)($ad['status'] ?? 'PAUSED'));
        return $this->client->post("{$accountId}/ads", $params);
    }
PHP_CODE;
$service=preg_replace(
    '/    public function createAd\(string \$accountId, string \$adSetId, string \$creativeId, array \$ad\): array\s*\{[\s\S]*?\n    \}\n\n    \/\*\*/',
    $ad."\n\n    /**",
    $service,1,$c5
) ?? $service;
if($c5!==1){fwrite(STDERR,"[meta-sdk-v102] createAd replace=$c5\n");exit(309);}

file_put_contents($servicePath,$service);

/* ---------- MetaLaunchValidator SDK mode ---------- */
$validator=file_get_contents($validatorPath);
if($validator===false){fwrite(STDERR,"[meta-sdk-v102] validator read failed\n");exit(310);}
if(strpos($validator,'REMASK_META_SDK_MODE_V1')===false){
    $old=<<<'PHP_CODE'
        $campaign = self::object($payload, 'campaign');
        $adset = self::object($payload, 'adset');
        $creative = self::object($payload, 'creative');
        self::object($payload, 'ad');

        $objective = strtoupper(self::string($campaign, 'objective'));
PHP_CODE;
    $new=<<<'PHP_CODE'
        $campaign = self::object($payload, 'campaign');
        $adset = self::object($payload, 'adset');
        $creative = self::object($payload, 'creative');
        $ad = self::object($payload, 'ad');

        /* REMASK_META_SDK_MODE_V1
         * Full SDK builder mode deliberately delegates combination validation to Meta.
         * ReMask still requires the structural objects needed by its resumable worker.
         */
        if (!empty($payload['_meta_sdk_mode'])) {
            foreach (['name','objective'] as $field) self::string($campaign, $field);
            foreach (['name','optimization_goal','billing_event'] as $field) self::string($adset, $field);
            self::object($adset, 'targeting');
            self::string($creative, 'name');
            self::string($ad, 'name');

            $campaign['status'] = strtoupper((string)($campaign['status'] ?? 'PAUSED'));
            $adset['status'] = strtoupper((string)($adset['status'] ?? 'PAUSED'));
            $ad['status'] = strtoupper((string)($ad['status'] ?? 'PAUSED'));
            $campaign['objective'] = strtoupper((string)$campaign['objective']);
            $adset['optimization_goal'] = strtoupper((string)$adset['optimization_goal']);
            $adset['billing_event'] = strtoupper((string)$adset['billing_event']);

            $payload['campaign'] = $campaign;
            $payload['adset'] = $adset;
            $payload['creative'] = $creative;
            $payload['ad'] = $ad;
            return $payload;
        }

        $objective = strtoupper(self::string($campaign, 'objective'));
PHP_CODE;
    if(strpos($validator,$old)===false){fwrite(STDERR,"[meta-sdk-v102] validator anchor missing\n");exit(311);}
    $validator=str_replace($old,$new,$validator,$n);
    if($n!==1){fwrite(STDERR,"[meta-sdk-v102] validator replace=$n\n");exit(312);}
    file_put_contents($validatorPath,$validator);
}

/* ---------- metaJobCreate merges Builder preset ---------- */
$job=file_get_contents($jobPath);
if($job===false){fwrite(STDERR,"[meta-sdk-v102] job read failed\n");exit(313);}
if(strpos($job,'REMASK_META_BUILDER_MERGE_V1')===false){
    $old=<<<'PHP_CODE'
    $payload = json_decode($payloadRaw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($payload)) throw new InvalidArgumentException('payload must be a JSON object');

    $accountOverridesRaw = (string)($_POST['account_overrides'] ?? '{}');
PHP_CODE;
    $new=<<<'PHP_CODE'
    $payload = json_decode($payloadRaw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($payload)) throw new InvalidArgumentException('payload must be a JSON object');

    /* REMASK_META_BUILDER_MERGE_V1 */
    $metaBuilderRaw = trim((string)($_POST['meta_builder_payload'] ?? ''));
    if ($metaBuilderRaw !== '') {
        $metaBuilder = json_decode($metaBuilderRaw, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($metaBuilder)) throw new InvalidArgumentException('meta_builder_payload must be a JSON object.');

        foreach (['campaign','adset','creative','ad'] as $section) {
            $extra = $metaBuilder[$section] ?? null;
            if (!is_array($extra) || $extra === []) continue;
            $base = isset($payload[$section]) && is_array($payload[$section]) ? $payload[$section] : [];
            $payload[$section] = array_replace_recursive($base, $extra);
        }
        $targetingExtra = $metaBuilder['targeting'] ?? null;
        if (is_array($targetingExtra) && $targetingExtra !== []) {
            if (!isset($payload['adset']) || !is_array($payload['adset'])) $payload['adset'] = [];
            $targetingBase = isset($payload['adset']['targeting']) && is_array($payload['adset']['targeting'])
                ? $payload['adset']['targeting']
                : [];
            $payload['adset']['targeting'] = array_replace_recursive($targetingBase, $targetingExtra);
        }
        $payload['_meta_sdk_mode'] = true;
    }

    $accountOverridesRaw = (string)($_POST['account_overrides'] ?? '{}');
PHP_CODE;
    if(strpos($job,$old)===false){fwrite(STDERR,"[meta-sdk-v102] job payload anchor missing\n");exit(314);}
    $job=str_replace($old,$new,$job,$n);
    if($n!==1){fwrite(STDERR,"[meta-sdk-v102] job merge replace=$n\n");exit(315);}
    file_put_contents($jobPath,$job);
}

/* ---------- schema endpoint ---------- */
$endpoint=<<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaSdkSchema.php';
try {
    MetaEndpoint::ok([
        'source' => 'facebook/facebook-python-business-sdk',
        'captured_at' => '2026-09-19',
        'schema' => MetaSdkSchema::schema(),
    ]);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;
file_put_contents($root.'/ajax/metaSdkSchema.php',$endpoint);

fwrite(STDERR,"[meta-sdk-v102] official SDK whitelist + passthrough backend ready\n");
