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
require_once __DIR__ . '/../classes/RemaskProxy.php';

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
        return ['ok' => true, 'result' => $summarize ? $summarize($value) : true];
    } catch (Throwable $e) {
        return rmx_clean_probe_error($e);
    }
}

function rmx_proxy_shape(RemaskProxy $p): array {
    return [
        'configured' => $p->ip !== '' && $p->port > 0,
        'type' => $p->type,
        'has_login' => $p->login !== '',
        'has_password' => $p->password !== '',
        'auth_complete' => $p->login !== '' && $p->password !== '',
    ];
}

function rmx_find_proxy(object $object, int $depth = 0, array &$seen = []): ?RemaskProxy {
    if ($object instanceof RemaskProxy) return $object;
    if ($depth > 2) return null;
    $oid = spl_object_id($object);
    if (isset($seen[$oid])) return null;
    $seen[$oid] = true;
    try {
        $ref = new ReflectionObject($object);
        foreach ($ref->getProperties() as $prop) {
            try {
                $prop->setAccessible(true);
                $value = $prop->getValue($object);
            } catch (Throwable) {
                continue;
            }
            if ($value instanceof RemaskProxy) return $value;
            if (is_object($value)) {
                $found = rmx_find_proxy($value, $depth + 1, $seen);
                if ($found) return $found;
            }
        }
    } catch (Throwable) {
    }
    return null;
}

function rmx_account_proxy_shape(object $account): array {
    $vars = get_object_vars($account);
    foreach ($vars as $key => $value) {
        if (stripos((string)$key, 'proxy') === false) continue;
        if ($value instanceof RemaskProxy) return rmx_proxy_shape($value);
        if (is_array($value)) {
            return [
                'configured' => !empty($value),
                'type' => (string)($value['type'] ?? 'unknown'),
                'has_login' => trim((string)($value['login'] ?? $value['user'] ?? '')) !== '',
                'has_password' => trim((string)($value['password'] ?? $value['pass'] ?? '')) !== '',
                'auth_complete' => trim((string)($value['login'] ?? $value['user'] ?? '')) !== '' && trim((string)($value['password'] ?? $value['pass'] ?? '')) !== '',
            ];
        }
        if (is_string($value) && trim($value) !== '') {
            try { return rmx_proxy_shape(RemaskProxy::parse($value)); }
            catch (Throwable) { return ['configured'=>true,'type'=>'unparsed','has_login'=>false,'has_password'=>false,'auth_complete'=>false]; }
        }
    }
    $seen = [];
    $found = rmx_find_proxy($account, 0, $seen);
    return $found ? rmx_proxy_shape($found) : ['configured'=>false,'type'=>null,'has_login'=>false,'has_password'=>false,'auth_complete'=>false];
}

$profile = '61594319066772';
$out = [
    'ok' => false,
    'profile' => $profile,
    'store_found' => false,
    'token_shape' => null,
    'account_proxy' => null,
    'service_proxy' => null,
    'identity' => null,
    'permissions' => null,
    'ad_accounts' => null,
];

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account = $store->getAccountByName($profile);
    if ($account === null) throw new RuntimeException('Configured profile not found in AccountStore.');
    $out['store_found'] = true;
    $rawToken = (string)$account->token;
    $token = trim($rawToken);
    $out['token_shape'] = ['length'=>strlen($token),'starts_eaa'=>str_starts_with($token,'EAA'),'trimmed'=>$rawToken !== $token];
    $out['account_proxy'] = rmx_account_proxy_shape($account);

    $service = MetaEndpoint::serviceForAccountName($profile);
    $seen = [];
    $serviceProxy = rmx_find_proxy($service, 0, $seen);
    $out['service_proxy'] = $serviceProxy ? rmx_proxy_shape($serviceProxy) : ['configured'=>false,'type'=>null,'has_login'=>false,'has_password'=>false,'auth_complete'=>false];

    $out['identity'] = rmx_clean_probe_step(
        fn() => $service->getIdentity(),
        fn($v) => ['id'=>(string)($v['id'] ?? ''),'name_present'=>isset($v['name'])]
    );
    $out['permissions'] = rmx_clean_probe_step(
        fn() => $service->getPermissions(),
        function ($v) {
            $granted = [];
            foreach ((array)($v['data'] ?? []) as $row) {
                if (is_array($row) && ($row['status'] ?? '') === 'granted' && isset($row['permission'])) $granted[] = (string)$row['permission'];
            }
            return [
                'granted_count'=>count($granted),
                'ads_management'=>in_array('ads_management',$granted,true),
                'ads_read'=>in_array('ads_read',$granted,true),
                'business_management'=>in_array('business_management',$granted,true),
            ];
        }
    );
    $out['ad_accounts'] = rmx_clean_probe_step(
        fn() => $service->listAdAccounts(),
        fn($v) => ['count'=>count((array)($v['data'] ?? []))]
    );
    $out['ok'] = ($out['identity']['ok'] ?? false) && ($out['ad_accounts']['ok'] ?? false);
} catch (Throwable $e) {
    $out['fatal'] = rmx_clean_probe_error($e);
}

echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
PHP;
file_put_contents($probePath, $code);
fwrite(STDERR, "[clean-sync-probe] clean Meta sync probe installed\n");
