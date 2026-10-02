<?php
declare(strict_types=1);
// REMASK_PRIVATE_LAUNCH_CATALOG_V1
$root='/var/www/html';
file_put_contents($root.'/classes/RemaskPrivateLaunchCatalog.php', <<<'CATALOG'
<?php
declare(strict_types=1);

/** Local private-worker catalog. Inventory identity is not live advertising permission. */
final class RemaskPrivateLaunchCatalog
{
    private static function id(mixed $value): string {
        $id = trim((string)$value);
        if (str_starts_with($id, 'act_')) $id = substr($id, 4);
        return preg_match('/^\d{5,30}$/', $id) ? $id : '';
    }

    public static function fromState(string $profile, array $snapshots, array $bindings = [], int $now = 0): array {
        $now = $now ?: time();
        $snapshot = is_array($snapshots[$profile] ?? null) ? $snapshots[$profile] : [];
        $accounts = [];
        foreach ((array)($snapshot['ad_accounts'] ?? []) as $row) {
            if (!is_array($row)) continue;
            if (isset($row['profile']) && (string)$row['profile'] !== $profile) continue;
            $id = self::id($row['id'] ?? $row['account_id'] ?? '');
            if ($id === '') continue;
            $accounts[$id] = [
                'id' => 'act_' . $id, 'account_id' => $id,
                'name' => trim((string)($row['name'] ?? $id)),
                'currency' => trim((string)($row['currency'] ?? '')),
                'account_status' => isset($row['account_status']) ? (int)$row['account_status'] : null,
                'business_id' => self::id($row['business_id'] ?? ''),
                'profile' => $profile, 'source' => 'last_confirmed_private_inventory',
                'advertising_access_verified' => false,
            ];
        }
        $binding = is_array($bindings[$profile] ?? null) ? $bindings[$profile] : [];
        $items = isset($binding['ad_account_id']) ? [$binding] : (array)($binding['ad_accounts'] ?? []);
        foreach ($items as $businessKey => $row) {
            if (!is_array($row)) continue;
            $id = self::id($row['ad_account_id'] ?? '');
            $business = self::id($row['business_id'] ?? $businessKey);
            // This file is emitted only after exact worker RK confirmation.
            // Merge identity only: it does not prove funding, ACTIVE or Page access.
            if ($id === '' || $business === '' || isset($accounts[$id])) continue;
            $accounts[$id] = [
                'id'=>'act_'.$id, 'account_id'=>$id,
                'name'=>trim((string)($row['account_name'] ?? $id)),
                'currency'=>'', 'account_status'=>null, 'business_id'=>$business,
                'profile'=>$profile, 'source'=>'python_worker_confirmed_create',
                'advertising_access_verified'=>false,
            ];
        }
        $pages = [];
        foreach ((array)($snapshot['pages'] ?? []) as $row) {
            if (!is_array($row)) continue;
            if (isset($row['profile']) && (string)$row['profile'] !== $profile) continue;
            $id = self::id($row['id'] ?? '');
            if ($id === '') continue;
            // Prefer the canonical Page ID when an alias mapping is present.
            $canonical = self::id($row['page_id'] ?? '') ?: $id;
            $pages[$canonical] = [
                'id'=>$canonical, 'name'=>trim((string)($row['name'] ?? $canonical)),
                'business_id'=>self::id($row['business_id'] ?? ''),
                'source'=>'last_confirmed_private_inventory',
                'ad_account_page_access_verified'=>false,
            ];
        }
        $updated = (int)($snapshot['updated_at'] ?? 0);
        $cache = ['hit'=>true, 'fetched_at'=>$updated, 'stale'=>$updated <= 0 || $now-$updated > 1800,
                  'cached_only'=>true, 'source'=>'private_worker_snapshot'];
        return [
            'profile'=>$profile, 'catalog_only'=>true, 'session_ready'=>null,
            'ads_management_granted'=>null, 'ads_read_granted'=>null,
            'identity'=>['id'=>'', 'name'=>$profile], 'api_version'=>'private_worker_snapshot',
            'ad_accounts'=>['data'=>array_values($accounts)],
            'pages'=>['data'=>array_values($pages)],
            '_cache'=>$cache,
        ];
    }

    private static function json(string $path): array {
        $raw = @file_get_contents($path);
        $data = is_string($raw) ? json_decode($raw, true) : null;
        return is_array($data) ? $data : [];
    }

    public static function load(string $profile): array {
        return self::fromState($profile,
            self::json('/var/lib/remask/workspace-live-meta-snapshots.json'),
            self::json('/var/lib/remask/workspace-provisioning-bindings.json'));
    }

    public static function asset(array $catalog, string $resource, string $accountId = ''): array {
        if ($resource === 'pages') return $catalog['pages'] + ['_cache'=>$catalog['_cache'], 'catalog_only'=>true];
        if ($resource === 'ad_accounts') return $catalog['ad_accounts'] + ['_cache'=>$catalog['_cache'], 'catalog_only'=>true];
        if ($resource === 'funding') {
            $id = self::id($accountId);
            foreach ($catalog['ad_accounts']['data'] as $account) {
                if ($id === '' || $account['account_id'] !== $id) continue;
                // No live payment proof is present in the identity mirror.
                return ['id'=>'act_'.$id, 'account_status'=>$account['account_status'],
                        'currency'=>$account['currency'], 'funding_verified'=>false,
                        'verification_status'=>'NOT_CHECKED', '_cache'=>$catalog['_cache']];
            }
            throw new InvalidArgumentException('Selected RK is absent from this profile catalog.');
        }
        throw new DomainException('PRIVATE_LAUNCH_RESOURCE_UNAVAILABLE: '.$resource);
    }
}
CATALOG
);
file_put_contents($root.'/ajax/metaPreflight.php', <<<'PREFLIGHT'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/RemaskPrivateLaunchCatalog.php';

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    // Validate profile existence locally; never consult its old Graph token.
    MetaEndpoint::accountForName($profile);
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    MetaEndpoint::ok($catalog);
} catch (Throwable $e) { MetaEndpoint::fail($e); }
PREFLIGHT
);
file_put_contents($root.'/ajax/metaAssets.php', <<<'ASSETS'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/RemaskPrivateLaunchCatalog.php';

try {
    $input = MetaEndpoint::input();
    $profile = trim((string)($input['profile'] ?? ''));
    if ($profile === '') throw new InvalidArgumentException('profile is required');
    // Validate profile existence locally; never consult its old Graph token.
    MetaEndpoint::accountForName($profile);
    $catalog = RemaskPrivateLaunchCatalog::load($profile);
    $resource = strtolower(trim((string)($input['resource'] ?? '')));
    $accountId = trim((string)($input['account_id'] ?? ''));
    MetaEndpoint::ok(RemaskPrivateLaunchCatalog::asset($catalog, $resource, $accountId));
} catch (DomainException $e) {
    http_response_code(501);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok'=>false, 'error'=>['code'=>'PRIVATE_LAUNCH_RESOURCE_UNAVAILABLE',
        'message'=>'Этот ресурс ещё не подключён к текущему режиму Launch.']], JSON_UNESCAPED_UNICODE);
} catch (Throwable $e) { MetaEndpoint::fail($e); }
ASSETS
);
// Review/submit still have only a legacy Graph implementation. Do not silently
// use that implementation when the user selected private-worker operation.
foreach (['metaLaunchReview.php','metaDryRun.php','metaJobCreate.php','metaLaunch.php'] as $endpoint) {
    file_put_contents($root.'/ajax/'.$endpoint, <<<'BLOCKED'
<?php
declare(strict_types=1);
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
http_response_code(409);
header('Content-Type: application/json; charset=utf-8');
echo json_encode(['ok'=>false,'error'=>[
    'code'=>'PRIVATE_LAUNCH_VERIFICATION_REQUIRED',
    'message'=>'Проверка рекламного доступа FP и оплаты в текущем режиме ещё не выполнена. Запуск рекламы недоступен.'
]], JSON_UNESCAPED_UNICODE);
BLOCKED
    );
}
$jsPath=$root.'/scripts/launch.js';
$js=file_get_contents($jsPath);
if (!is_string($js)) throw new RuntimeException('launch.js is missing');
if (strpos($js,'REMASK_PRIVATE_LAUNCH_CATALOG_V1')===false) {
    $js=str_replace("statusReady ? 'READY' :", "statusReady ? 'SELECTED / ACCESS NOT CHECKED' :", $js);
    $js=str_replace('Bindings loaded: ${ready}/${targets.length} RK READY.', 'Выбраны страницы: ${ready}/${targets.length}. Доступ FP для рекламы не проверен.', $js);
    // Render the confirmed Page catalog even when optional Pixel lookup fails.
    $js=str_replace("state.targetBindingAssets.pages[profile] = data.data || [];", "state.targetBindingAssets.pages[profile] = data.data || []; renderTargetBindings();", $js);
    $js.="\n".file_get_contents('/tmp/railway-launch-private-catalog.js');
}
file_put_contents($jsPath,$js);
$phpPath=$root.'/launch.php';
$php=file_get_contents($phpPath);
$php=preg_replace('#scripts/launch\\.js(?:\\?[^"\\\']*)?#','scripts/launch.js?v=20261002-private-catalog-v1',$php,1);
$php=str_replace('official API, read only','состояние оплаты, read only',$php);
$php=str_replace('Shows the current funding source/status Meta exposes for each selected RK. ReMask does not change payment methods here.',
    'Платёжная привязка требует отдельной проверки Meta. Сохранённые ID не подтверждают готовность карты.',$php);
file_put_contents($phpPath,$php);
fwrite(STDERR,"[launch-private-catalog] local RK/Page reads installed; live advertising/payment permissions remain unverified\n");
