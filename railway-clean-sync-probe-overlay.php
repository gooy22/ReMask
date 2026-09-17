<?php
$probePath = '/var/www/html/ajax/metaSyncProbe.php';
$code = <<<'PHP'
<?php
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
if (!hash_equals('rmx_probe_9fb2e8d1c43a6f057d18', (string)($_GET['k'] ?? ''))) {
    http_response_code(404);
    echo json_encode(['ok' => false]);
    exit;
}
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';

function rmx_clean_probe_error(Throwable $e): array {
    $message = (string)$e->getMessage();
    $message = preg_replace('#(https?://)([^/@:\s]+):([^/@\s]+)@#i', '$1***:***@', $message) ?? $message;
    $message = preg_replace('/(access[_-]?token|authorization|bearer)(\s*[:=]\s*|\s+)[A-Za-z0-9._\-]+/i', '$1$2***', $message) ?? $message;
    return [
        'ok' => false,
        'class' => get_class($e),
        'code' => (int)$e->getCode(),
        'message' => substr($message, 0, 500),
    ];
}

function rmx_clean_probe_step(callable $fn, ?callable $summarize = null): array {
    try {
        $value = $fn();
        return [
            'ok' => true,
            'result' => $summarize ? $summarize($value) : true,
        ];
    } catch (Throwable $e) {
        return rmx_clean_probe_error($e);
    }
}

$profile = '61594319066772';
$out = [
    'ok' => false,
    'profile' => $profile,
    'store_found' => false,
    'token_shape' => null,
    'identity' => null,
    'permissions' => null,
    'ad_accounts' => null,
];

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account = $store->getAccountByName($profile);
    if ($account === null) {
        throw new RuntimeException('Configured profile not found in AccountStore.');
    }
    $out['store_found'] = true;
    $rawToken = (string)$account->token;
    $token = trim($rawToken);
    $out['token_shape'] = [
        'length' => strlen($token),
        'starts_eaa' => str_starts_with($token, 'EAA'),
        'trimmed' => $rawToken !== $token,
    ];

    $service = MetaEndpoint::serviceForAccountName($profile);
    $out['identity'] = rmx_clean_probe_step(
        fn() => $service->getIdentity(),
        fn($v) => ['id' => (string)($v['id'] ?? ''), 'name_present' => isset($v['name'])]
    );
    $out['permissions'] = rmx_clean_probe_step(
        fn() => $service->getPermissions(),
        function ($v) {
            $granted = [];
            foreach ((array)($v['data'] ?? []) as $row) {
                if (is_array($row) && ($row['status'] ?? '') === 'granted' && isset($row['permission'])) {
                    $granted[] = (string)$row['permission'];
                }
            }
            return [
                'granted_count' => count($granted),
                'ads_management' => in_array('ads_management', $granted, true),
                'ads_read' => in_array('ads_read', $granted, true),
                'business_management' => in_array('business_management', $granted, true),
            ];
        }
    );
    $out['ad_accounts'] = rmx_clean_probe_step(
        fn() => $service->listAdAccounts(),
        fn($v) => ['count' => count((array)($v['data'] ?? []))]
    );
    $out['ok'] = ($out['identity']['ok'] ?? false) && ($out['ad_accounts']['ok'] ?? false);
} catch (Throwable $e) {
    $out['fatal'] = rmx_clean_probe_error($e);
}

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
PHP;
file_put_contents($probePath, $code);
fwrite(STDERR, "[clean-sync-probe] clean Meta sync probe installed\n");
