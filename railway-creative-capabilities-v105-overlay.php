<?php
/**
 * Creative capabilities + audience estimate layer.
 *
 * Goals:
 *  - one source of truth for visible Meta builder selects (MetaSdkSchema)
 *  - live account context for profile/RK assets
 *  - real Meta delivery_estimate for potential audience
 *  - no raw Graph transport outside MetaAdsService/MetaApiClient
 */
$root = '/var/www/html';
$servicePath = $root . '/classes/MetaAdsService.php';

$service = file_get_contents($servicePath);
if ($service === false) {
    fwrite(STDERR, "[creative-capabilities] MetaAdsService.php missing\n");
    exit(351);
}

if (strpos($service, 'REMASK_CREATIVE_CAPABILITIES_V1') === false) {
    $anchor = "    public function preflight(?string \$accountId = null): array\n";
    if (strpos($service, $anchor) === false) {
        fwrite(STDERR, "[creative-capabilities] MetaAdsService preflight anchor missing\n");
        exit(352);
    }

    $methods = <<<'PHP_CODE'
    /* REMASK_CREATIVE_CAPABILITIES_V1 */
    private function remaskCreativeAccountNode(string $accountId): string
    {
        $id = preg_replace('/^act_/i', '', trim($accountId)) ?? '';
        if ($id === '' || !preg_match('/^\d+$/', $id)) {
            throw new InvalidArgumentException('Valid Meta ad account ID is required.');
        }
        return 'act_' . $id;
    }

    public function getDeliveryEstimate(
        string $accountId,
        array $targetingSpec,
        string $optimizationGoal = '',
        array $promotedObject = []
    ): array {
        if ($targetingSpec === []) {
            throw new InvalidArgumentException('targeting_spec is required for audience estimate.');
        }
        $params = [
            'targeting_spec' => json_encode(
                $targetingSpec,
                JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
            ),
        ];
        $optimizationGoal = strtoupper(trim($optimizationGoal));
        if ($optimizationGoal !== '') {
            $params['optimization_goal'] = $optimizationGoal;
        }
        if ($promotedObject !== []) {
            $params['promoted_object'] = json_encode(
                $promotedObject,
                JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
            );
        }
        return $this->client->get($this->remaskCreativeAccountNode($accountId) . '/delivery_estimate', $params);
    }

    public function getReachEstimate(string $accountId, array $targetingSpec): array
    {
        if ($targetingSpec === []) {
            throw new InvalidArgumentException('targeting_spec is required for reach estimate.');
        }
        return $this->client->get(
            $this->remaskCreativeAccountNode($accountId) . '/reachestimate',
            [
                'targeting_spec' => json_encode(
                    $targetingSpec,
                    JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR
                ),
            ]
        );
    }

    public function listCreativePixels(string $accountId): array
    {
        return $this->client->get(
            $this->remaskCreativeAccountNode($accountId) . '/adspixels',
            ['fields' => 'id,name,last_fired_time', 'limit' => 200]
        );
    }

    public function listCreativeCustomAudiences(string $accountId): array
    {
        return $this->client->get(
            $this->remaskCreativeAccountNode($accountId) . '/customaudiences',
            ['fields' => 'id,name,subtype,description', 'limit' => 200]
        );
    }

PHP_CODE;

    $service = str_replace($anchor, $methods . $anchor, $service, $count);
    if ($count !== 1) {
        fwrite(STDERR, "[creative-capabilities] MetaAdsService patch count=$count\n");
        exit(353);
    }
    file_put_contents($servicePath, $service);
}

$capabilities = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaSdkSchema.php';

function remask_creative_rows(mixed $value): array
{
    if (!is_array($value)) return [];
    $rows = $value['data'] ?? $value;
    return is_array($rows) ? array_values(array_filter($rows, 'is_array')) : [];
}

function remask_creative_error(Throwable $e): array
{
    $out = ['message' => $e->getMessage(), 'class' => get_class($e), 'code' => (int)$e->getCode()];
    if (method_exists($e, 'toArray')) {
        try {
            $meta = (array)$e->toArray();
            foreach (['http_status','type','code','subcode','error_subcode'] as $key) {
                if (array_key_exists($key, $meta)) $out[$key] = $meta[$key];
            }
        } catch (Throwable) {}
    }
    return $out;
}

$ctaTypes = [
    'ADD_TO_CART','APPLY_NOW','ASK_ABOUT_SERVICES','ASK_A_QUESTION','ASK_FOR_MORE_INFO','ASK_US',
    'AUDIO_CALL','BOOK_A_CONSULTATION','BOOK_NOW','BOOK_TRAVEL','BROWSE_SHOP','BUY','BUY_NOW',
    'BUY_TICKETS','BUY_VIA_MESSAGE','CALL','CALL_ME','CALL_NOW','CHAT_NOW','CHAT_WITH_US','CONFIRM',
    'CONTACT','CONTACT_US','DONATE','DONATE_NOW','DOWNLOAD','EVENT_RSVP','FIND_A_GROUP','FIND_OUT_MORE',
    'FIND_YOUR_GROUPS','FOLLOW_NEWS_STORYLINE','FOLLOW_PAGE','FOLLOW_USER','GET_A_QUOTE','GET_DETAILS',
    'GET_DIRECTIONS','GET_IN_TOUCH','GET_OFFER','GET_OFFER_VIEW','GET_PROMOTIONS','GET_QUOTE',
    'GET_SHOWTIMES','GET_STARTED','INQUIRE_NOW','INSTALL_APP','INSTALL_MOBILE_APP','JOIN_CHANNEL',
    'JOIN_LIVE_VIDEO','LEARN_MORE','LIKE_PAGE','LISTEN_MUSIC','LISTEN_NOW','MAKE_AN_APPOINTMENT',
    'MESSAGE_PAGE','MOBILE_DOWNLOAD','NO_BUTTON','OPEN_INSTANT_APP','OPEN_LINK','ORDER_NOW','PAY_TO_ACCESS',
    'PLAY_GAME','PLAY_GAME_ON_FACEBOOK','PURCHASE_GIFT_CARDS','RAISE_MONEY','RECORD_NOW','REFER_FRIENDS',
    'REQUEST_TIME','SAY_THANKS','SEE_MORE','SEE_SHOP','SELL_NOW','SEND_A_GIFT','SEND_GIFT_MONEY',
    'SEND_UPDATES','SHARE','SHOP_NOW','SHOP_WITH_AI','SIGN_UP','SOTTO_SUBSCRIBE','START_A_CHAT',
    'START_ORDER','SUBSCRIBE','SWIPE_UP_PRODUCT','SWIPE_UP_SHOP','TRY_DEMO','TRY_ON_WITH_AI','UPDATE_APP',
    'USE_APP','USE_MOBILE_APP','VIDEO_ANNOTATION','VIDEO_CALL','VIEW_CART','VIEW_CHANNEL','VIEW_IN_CART',
    'VIEW_PRODUCT','VISIT_PAGES_FEED','VISIT_WEBSITE','WATCH_LIVE_VIDEO','WATCH_MORE','WATCH_VIDEO',
    'WHATSAPP_MESSAGE','WOODHENGE_SUPPORT'
];

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    $accountId = trim((string)($input['account_id'] ?? ''));
    $refresh = filter_var($input['refresh'] ?? false, FILTER_VALIDATE_BOOLEAN);

    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $profiles = [];
    foreach ((array)$store->deserialize() as $account) {
        if (!$account instanceof FbAccount) continue;
        $profiles[] = [
            'name' => (string)$account->name,
            'user_id' => (string)($account->userId ?? ''),
            'proxy_configured' => $account->proxy !== null,
            'session_ready' => $account->isLegacyReady(),
        ];
    }

    $out = [
        'source' => 'facebook/facebook-python-business-sdk + live Meta account assets',
        'graph_version' => (string)(getenv('META_GRAPH_API_VERSION') ?: 'v26.0'),
        'schema' => MetaSdkSchema::schema(),
        'cta_types' => $ctaTypes,
        'profiles' => $profiles,
        'profile' => $profile,
        'account_id' => $accountId,
        'ad_accounts' => [],
        'pages' => [],
        'pixels' => [],
        'custom_audiences' => [],
        'warnings' => [],
    ];

    if ($profile === '') {
        MetaEndpoint::ok($out);
    }

    try {
        $preflight = MetaEndpoint::cachedPreflight($profile, $refresh);
        $out['ad_accounts'] = remask_creative_rows($preflight['ad_accounts'] ?? []);
        $out['transport'] = $preflight['transport'] ?? null;
        $out['cache'] = $preflight['_cache'] ?? null;
    } catch (Throwable $e) {
        $out['warnings'][] = ['resource' => 'profile', 'error' => remask_creative_error($e)];
    }

    try {
        $pages = MetaEndpoint::cachedAsset($profile, 'pages', '', $refresh);
        $out['pages'] = remask_creative_rows($pages);
    } catch (Throwable $e) {
        $out['warnings'][] = ['resource' => 'pages', 'error' => remask_creative_error($e)];
    }

    if ($accountId !== '') {
        $service = MetaEndpoint::serviceForAccountName($profile);
        try {
            $out['pixels'] = remask_creative_rows($service->listCreativePixels($accountId));
        } catch (Throwable $e) {
            $out['warnings'][] = ['resource' => 'pixels', 'error' => remask_creative_error($e)];
        }
        try {
            $out['custom_audiences'] = remask_creative_rows($service->listCreativeCustomAudiences($accountId));
        } catch (Throwable $e) {
            $out['warnings'][] = ['resource' => 'custom_audiences', 'error' => remask_creative_error($e)];
        }
    }

    MetaEndpoint::ok($out);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;

$estimate = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaSdkSchema.php';

function remask_estimate_compact(mixed $value): mixed
{
    if (!is_array($value)) return $value;
    $out = [];
    foreach ($value as $key => $item) {
        $item = remask_estimate_compact($item);
        if ($item === null || $item === '' || $item === []) continue;
        $out[$key] = $item;
    }
    return $out;
}

function remask_estimate_first(array $response): array
{
    if (isset($response['data']) && is_array($response['data'])) {
        $first = reset($response['data']);
        return is_array($first) ? $first : [];
    }
    return $response;
}

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    $accountId = trim((string)($input['account_id'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    if ($accountId === '') throw new InvalidArgumentException('account_id is required');

    $targeting = $input['targeting_spec'] ?? [];
    if (is_string($targeting) && trim($targeting) !== '') {
        $targeting = json_decode($targeting, true, 512, JSON_THROW_ON_ERROR);
    }
    if (!is_array($targeting)) throw new InvalidArgumentException('targeting_spec must be an object');
    $targeting = MetaSdkSchema::filterTargeting(remask_estimate_compact($targeting));
    if ($targeting === []) throw new InvalidArgumentException('Targeting is empty.');

    $optimizationGoal = strtoupper(trim((string)($input['optimization_goal'] ?? '')));

    $promotedObject = $input['promoted_object'] ?? [];
    if (is_string($promotedObject) && trim($promotedObject) !== '') {
        $promotedObject = json_decode($promotedObject, true, 512, JSON_THROW_ON_ERROR);
    }
    if (!is_array($promotedObject)) $promotedObject = [];
    $promotedObject = remask_estimate_compact($promotedObject);

    $fingerprint = hash('sha256', json_encode([
        'profile' => $profile,
        'account_id' => preg_replace('/^act_/i', '', $accountId),
        'targeting' => $targeting,
        'optimization_goal' => $optimizationGoal,
        'promoted_object' => $promotedObject,
        'graph_version' => (string)(getenv('META_GRAPH_API_VERSION') ?: 'v26.0'),
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR));

    $dataRoot = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
    $cacheDir = $dataRoot . '/meta-cache/audience-estimates';
    if (!is_dir($cacheDir)) @mkdir($cacheDir, 0770, true);
    $cacheFile = $cacheDir . '/' . $fingerprint . '.json';
    $ttl = 300;

    if (is_file($cacheFile) && (time() - (int)@filemtime($cacheFile)) < $ttl) {
        $cached = json_decode((string)@file_get_contents($cacheFile), true);
        if (is_array($cached)) {
            $cached['_cache'] = ['state' => 'HIT', 'ttl' => $ttl];
            MetaEndpoint::ok($cached);
        }
    }

    $service = MetaEndpoint::serviceForAccountName($profile);
    $raw = $service->getDeliveryEstimate($accountId, $targeting, $optimizationGoal, $promotedObject);
    $row = remask_estimate_first($raw);

    $result = [
        'source' => 'meta_delivery_estimate',
        'estimate_ready' => (bool)($row['estimate_ready'] ?? false),
        'lower_bound' => isset($row['estimate_mau_lower_bound']) ? (int)$row['estimate_mau_lower_bound'] : null,
        'upper_bound' => isset($row['estimate_mau_upper_bound']) ? (int)$row['estimate_mau_upper_bound'] : null,
        'targeting_optimization_types' => is_array($row['targeting_optimization_types'] ?? null)
            ? $row['targeting_optimization_types']
            : [],
        'profile' => $profile,
        'account_id' => preg_replace('/^act_/i', '', $accountId),
        'optimization_goal' => $optimizationGoal,
        '_cache' => ['state' => 'MISS', 'ttl' => $ttl],
    ];

    // Some account/objective combinations return no delivery range. Use the
    // official reach estimate as a Meta-provided fallback, never a local formula.
    if ($result['lower_bound'] === null && $result['upper_bound'] === null) {
        try {
            $reachRaw = $service->getReachEstimate($accountId, $targeting);
            $reach = remask_estimate_first($reachRaw);
            if (isset($reach['users_lower_bound'])) $result['lower_bound'] = (int)$reach['users_lower_bound'];
            if (isset($reach['users_upper_bound'])) $result['upper_bound'] = (int)$reach['users_upper_bound'];
            if (array_key_exists('estimate_ready', $reach)) $result['estimate_ready'] = (bool)$reach['estimate_ready'];
            $result['source'] = 'meta_reach_estimate_fallback';
        } catch (Throwable $fallbackError) {
            $result['fallback_error'] = $fallbackError->getMessage();
        }
    }

    @file_put_contents(
        $cacheFile,
        json_encode($result, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT)
    );

    MetaEndpoint::ok($result);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;

if (file_put_contents($root . '/ajax/metaCreativeCapabilities.php', $capabilities) === false) {
    fwrite(STDERR, "[creative-capabilities] capabilities endpoint write failed\n");
    exit(354);
}
if (file_put_contents($root . '/ajax/metaAudienceEstimate.php', $estimate) === false) {
    fwrite(STDERR, "[creative-capabilities] audience endpoint write failed\n");
    exit(355);
}

fwrite(STDERR, "[creative-capabilities] live capabilities + delivery estimate ready\n");
