<?php
/**
 * v106: Ads Manager-like placement capabilities.
 * - Live account/objective-aware placement discovery via Meta targetingbrowse.
 * - SDK-compatible fallback when the live call is unavailable.
 */
$root = '/var/www/html';
$servicePath = $root . '/classes/MetaAdsService.php';
$service = file_get_contents($servicePath);
if ($service === false) {
    fwrite(STDERR, "[placement-capabilities] MetaAdsService.php missing\n");
    exit(361);
}

if (strpos($service, 'REMASK_PLACEMENT_CAPABILITIES_V1') === false) {
    $anchor = "    public function preflight(?string \$accountId = null): array\n";
    if (strpos($service, $anchor) === false) {
        fwrite(STDERR, "[placement-capabilities] service anchor missing\n");
        exit(362);
    }

    $methods = <<<'PHP_CODE'
    /* REMASK_PLACEMENT_CAPABILITIES_V1 */
    public function getPlacementCapabilities(
        string $accountId,
        string $objective = '',
        string $optimizationGoal = ''
    ): array {
        $types = [
            'publisher_platforms',
            'facebook_positions',
            'instagram_positions',
            'messenger_positions',
            'audience_network_positions',
            'threads_positions',
            'whatsapp_positions',
            'device_platforms',
        ];
        $params = [
            'whitelisted_types' => json_encode($types, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR),
            'limit' => 500,
        ];
        $objective = strtoupper(trim($objective));
        if ($objective !== '') $params['objective'] = $objective;
        $optimizationGoal = strtoupper(trim($optimizationGoal));
        if ($optimizationGoal !== '') $params['optimization_goal'] = $optimizationGoal;

        return $this->client->get(
            $this->remaskCreativeAccountNode($accountId) . '/targetingbrowse',
            $params
        );
    }

PHP_CODE;

    $service = str_replace($anchor, $methods . $anchor, $service, $count);
    if ($count !== 1) {
        fwrite(STDERR, "[placement-capabilities] service patch count=$count\n");
        exit(363);
    }
    file_put_contents($servicePath, $service);
}

$endpoint = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaSdkSchema.php';

function remask_placement_rows(mixed $value): array
{
    if (!is_array($value)) return [];
    $rows = $value['data'] ?? $value;
    return is_array($rows) ? array_values(array_filter($rows, 'is_array')) : [];
}

function remask_placement_group(string $type): string
{
    $type = strtolower(trim($type));
    if (str_starts_with($type, 'effective_')) $type = substr($type, 10);
    return $type;
}

function remask_placement_value(string $group, array $row): string
{
    $raw = '';
    foreach (['key','id','raw_name','name'] as $key) {
        if (!isset($row[$key]) || !is_scalar($row[$key])) continue;
        $candidate = trim((string)$row[$key]);
        if ($candidate !== '') { $raw = $candidate; break; }
    }
    if ($raw === '') return '';

    $value = strtolower($raw);
    $value = preg_replace('/[^a-z0-9_]+/', '_', $value) ?? $value;
    $value = trim($value, '_');

    $aliases = [
        'fb_reels' => 'facebook_reels',
        'fb_reels_overlay' => 'facebook_reels_overlay',
        'rhc' => 'right_hand_column',
        'messenger_inbox' => 'messenger_home',
        'messenger_marketing_messages' => 'sponsored_messages',
        'messenger_story' => 'story',
        'messenger_thread' => 'messenger_thread',
    ];
    if (isset($aliases[$value])) $value = $aliases[$value];

    return $value;
}

function remask_placement_unique(array $values): array
{
    $out = [];
    foreach ($values as $value) {
        $value = trim((string)$value);
        if ($value === '' || in_array($value, $out, true)) continue;
        $out[] = $value;
    }
    return $out;
}

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    $accountId = trim((string)($input['account_id'] ?? ''));
    $objective = strtoupper(trim((string)($input['objective'] ?? '')));
    $optimizationGoal = strtoupper(trim((string)($input['optimization_goal'] ?? '')));

    $fallback = MetaSdkSchema::placementOptions();
    $result = [
        'source' => 'meta_sdk_fallback',
        'profile' => $profile,
        'account_id' => preg_replace('/^act_/i', '', $accountId),
        'objective' => $objective,
        'optimization_goal' => $optimizationGoal,
        'options' => $fallback,
        'live_groups' => [],
        'warning' => null,
    ];

    if ($profile === '' || $accountId === '') {
        MetaEndpoint::ok($result);
    }

    $fingerprint = hash('sha256', json_encode([
        'profile' => $profile,
        'account_id' => preg_replace('/^act_/i', '', $accountId),
        'objective' => $objective,
        'optimization_goal' => $optimizationGoal,
        'graph_version' => (string)(getenv('META_GRAPH_API_VERSION') ?: 'v26.0'),
    ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR));

    $dataRoot = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
    $cacheDir = $dataRoot . '/meta-cache/placement-capabilities';
    if (!is_dir($cacheDir)) @mkdir($cacheDir, 0770, true);
    $cacheFile = $cacheDir . '/' . $fingerprint . '.json';
    $ttl = 600;

    if (is_file($cacheFile) && (time() - (int)@filemtime($cacheFile)) < $ttl) {
        $cached = json_decode((string)@file_get_contents($cacheFile), true);
        if (is_array($cached)) {
            $cached['_cache'] = ['state' => 'HIT', 'ttl' => $ttl];
            MetaEndpoint::ok($cached);
        }
    }

    try {
        $service = MetaEndpoint::serviceForAccountName($profile);
        $raw = $service->getPlacementCapabilities($accountId, $objective, $optimizationGoal);
        $rows = remask_placement_rows($raw);

        $live = [];
        foreach ($rows as $row) {
            $group = remask_placement_group((string)($row['type'] ?? ''));
            if (!array_key_exists($group, $fallback)) continue;
            $value = remask_placement_value($group, $row);
            if ($value === '') continue;
            $live[$group][] = $value;
        }

        foreach ($live as $group => $values) {
            $values = remask_placement_unique($values);
            if ($values === []) continue;
            $result['options'][$group] = $values;
            $result['live_groups'][] = $group;
        }

        if ($result['live_groups'] !== []) {
            $result['source'] = 'meta_targetingbrowse';
        } else {
            $result['warning'] = 'Meta targetingbrowse returned no placement rows; SDK fallback is shown.';
        }
    } catch (Throwable $e) {
        $result['warning'] = $e->getMessage();
    }

    $result['_cache'] = ['state' => 'MISS', 'ttl' => $ttl];
    @file_put_contents($cacheFile, json_encode(
        $result,
        JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT
    ));
    MetaEndpoint::ok($result);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP_CODE;

if (file_put_contents($root . '/ajax/metaPlacementCapabilities.php', $endpoint) === false) {
    fwrite(STDERR, "[placement-capabilities] endpoint write failed\n");
    exit(364);
}

fwrite(STDERR, "[placement-capabilities] live targetingbrowse placement layer ready\n");
