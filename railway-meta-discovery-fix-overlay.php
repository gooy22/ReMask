<?php
/**
 * Keep Meta account discovery deliberately small and reference-compatible.
 * The working reference project enumerates accessible ad accounts from /me/adaccounts
 * using only stable list fields, then fetches richer per-account details separately.
 */
$servicePath = '/var/www/html/classes/MetaAdsService.php';
$service = file_get_contents($servicePath);
if ($service === false) {
    throw new RuntimeException('MetaAdsService.php not found');
}

$oldFields = "            'fields' => 'id,name,account_status,currency,amount_spent,balance,business_name,timezone_name,spend_cap',";
$newFields = "            'fields' => 'id,name,account_status,currency,amount_spent',";
$count = 0;
$service = str_replace($oldFields, $newFields, $service, $count);
if ($count !== 1) {
    throw new RuntimeException('MetaAdsService listAdAccounts field patch failed: ' . $count);
}
file_put_contents($servicePath, $service);
fwrite(STDERR, "[meta-discovery-fix] /me/adaccounts uses minimal stable fields\n");

// Replace the temporary probe with per-step diagnostics so one bad edge/field does
// not hide whether identity, permissions, or direct ad-account discovery actually works.
$probePath = '/var/www/html/ajax/metaSyncProbe.php';
$probeCode = <<<'PROBE'
<?php
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
if (!hash_equals('rmx_probe_9fb2e8d1c43a6f057d18', (string)($_GET['k'] ?? ''))) {
    http_response_code(404);
    echo json_encode(['ok'=>false]);
    exit;
}
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';

function rmx_probe_clean(string $message): string {
    $message = preg_replace('#(https?://)([^/@:\s]+):([^/@\s]+)@#i', '$1***:***@', $message) ?? $message;
    $message = preg_replace('/(access[_-]?token|authorization|bearer)(\s*[:=]\s*|\s+)[A-Za-z0-9._\-]+/i', '$1$2***', $message) ?? $message;
    return function_exists('mb_substr') ? mb_substr($message, 0, 600) : substr($message, 0, 600);
}

function rmx_probe_step(callable $fn): array {
    try {
        $value = $fn();
        return ['ok'=>true, 'value'=>$value, 'error'=>null, 'error_class'=>null, 'error_code'=>null];
    } catch (Throwable $e) {
        return [
            'ok'=>false,
            'value'=>null,
            'error'=>rmx_probe_clean($e->getMessage()),
            'error_class'=>get_class($e),
            'error_code'=>$e->getCode(),
        ];
    }
}

$profile = '61594319066772';
$out = [
    'ok' => false,
    'profile' => 'configured',
    'identity' => null,
    'permissions' => null,
    'ad_accounts' => null,
    'businesses' => null,
    'ads_management_granted' => null,
    'direct_ad_account_count' => null,
    'business_count' => null,
];

try {
    $service = MetaEndpoint::serviceForAccountName($profile);

    $identity = rmx_probe_step(fn() => $service->getIdentity());
    $out['identity'] = [
        'ok'=>$identity['ok'],
        'id'=>$identity['ok'] ? (string)($identity['value']['id'] ?? '') : null,
        'error'=>$identity['error'],
        'error_class'=>$identity['error_class'],
        'error_code'=>$identity['error_code'],
    ];

    $permissions = rmx_probe_step(fn() => $service->getPermissions());
    $granted = [];
    if ($permissions['ok']) {
        foreach ((array)($permissions['value']['data'] ?? []) as $permission) {
            if (is_array($permission) && ($permission['status'] ?? '') === 'granted' && isset($permission['permission'])) {
                $granted[] = (string)$permission['permission'];
            }
        }
    }
    $out['ads_management_granted'] = $permissions['ok'] ? in_array('ads_management', $granted, true) : null;
    $out['permissions'] = [
        'ok'=>$permissions['ok'],
        'granted_count'=>count($granted),
        'ads_management_granted'=>$out['ads_management_granted'],
        'error'=>$permissions['error'],
        'error_class'=>$permissions['error_class'],
        'error_code'=>$permissions['error_code'],
    ];

    $adAccounts = rmx_probe_step(fn() => $service->listAdAccounts());
    $out['direct_ad_account_count'] = $adAccounts['ok'] ? count((array)($adAccounts['value']['data'] ?? [])) : null;
    $out['ad_accounts'] = [
        'ok'=>$adAccounts['ok'],
        'count'=>$out['direct_ad_account_count'],
        'error'=>$adAccounts['error'],
        'error_class'=>$adAccounts['error_class'],
        'error_code'=>$adAccounts['error_code'],
    ];

    // BM discovery is enrichment only. Its failure must not hide direct RK access.
    $businesses = rmx_probe_step(fn() => $service->listBusinesses());
    $out['business_count'] = $businesses['ok'] ? count((array)($businesses['value']['data'] ?? [])) : null;
    $out['businesses'] = [
        'ok'=>$businesses['ok'],
        'count'=>$out['business_count'],
        'error'=>$businesses['error'],
        'error_class'=>$businesses['error_class'],
        'error_code'=>$businesses['error_code'],
    ];

    $out['ok'] = $identity['ok'] && $permissions['ok'] && $adAccounts['ok'];
} catch (Throwable $e) {
    $out['fatal'] = [
        'error'=>rmx_probe_clean($e->getMessage()),
        'error_class'=>get_class($e),
        'error_code'=>$e->getCode(),
    ];
}

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
PROBE;
file_put_contents($probePath, $probeCode);
fwrite(STDERR, "[meta-discovery-fix] per-step Meta sync probe ready\n");
