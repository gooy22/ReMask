<?php
/**
 * Replaces ajax/metaProfileManager.php with a canonical profile store adapter.
 * Goal: save/list/delete FB profiles using the same Volume-backed accounts.json
 * that hierarchy sync and Workspace read from.
 */
$root = '/var/www/html';
$ajaxDir = $root . '/ajax';
if (!is_dir($ajaxDir)) mkdir($ajaxDir, 0775, true);
$target = $ajaxDir . '/metaProfileManager.php';

$php = <<<'PHP'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

ob_start();
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

header('Content-Type: application/json; charset=utf-8');

function remask_pm_data_dir(): string {
    $dir = getenv('REMASK_DATA_DIR') ?: getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask';
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    return $dir;
}

function remask_pm_accounts_file(): string {
    return getenv('REMASK_ACCOUNTS_FILE') ?: remask_pm_data_dir() . '/accounts.json';
}

function remask_pm_is_list(array $a): bool {
    return function_exists('array_is_list') ? array_is_list($a) : (array_keys($a) === range(0, count($a) - 1));
}

function remask_pm_read_root(string $file): array {
    if (!is_file($file)) return [];
    $raw = (string)@file_get_contents($file);
    if (trim($raw) === '') return [];
    $json = json_decode($raw, true);
    return is_array($json) ? $json : [];
}

function remask_pm_extract_accounts(array $root): array {
    if (remask_pm_is_list($root)) return [$root, 'list', $root];
    foreach (['accounts', 'profiles', 'items', 'data'] as $key) {
        if (isset($root[$key]) && is_array($root[$key])) return [$root[$key], $key, $root];
    }
    return [[], 'list', $root];
}

function remask_pm_apply_accounts(array $original, string $mode, array $accounts): array {
    if ($mode === 'list') return array_values($accounts);
    $original[$mode] = array_values($accounts);
    return $original;
}

function remask_pm_write(string $file, array $root): void {
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    if (is_file($file) && filesize($file) > 2) @copy($file, $file . '.bak.' . gmdate('YmdHis'));
    $tmp = $file . '.tmp.' . getmypid();
    file_put_contents($tmp, json_encode($root, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
    @rename($tmp, $file);
}

function remask_pm_body(): array {
    $in = $_POST;
    $raw = (string)file_get_contents('php://input');
    if ($raw !== '') {
        $json = json_decode($raw, true);
        if (is_array($json)) $in = array_replace_recursive($in, $json);
        else {
            parse_str($raw, $parsed);
            if (is_array($parsed) && $parsed) $in = array_replace_recursive($in, $parsed);
        }
    }
    foreach (['profile','account','data','payload'] as $key) {
        if (isset($in[$key]) && is_string($in[$key])) {
            $j = json_decode($in[$key], true);
            if (is_array($j)) $in[$key] = $j;
        }
    }
    return $in;
}

function remask_pm_get_any(array $a, array $keys): string {
    foreach ($keys as $k) {
        if (array_key_exists($k, $a) && is_scalar($a[$k]) && trim((string)$a[$k]) !== '') return trim((string)$a[$k]);
    }
    foreach ($a as $v) {
        if (is_array($v)) {
            $hit = remask_pm_get_any($v, $keys);
            if ($hit !== '') return $hit;
        }
    }
    return '';
}

function remask_pm_proxy_raw(array $in): string {
    foreach (['proxy','proxy_raw','proxyString','proxy_string'] as $k) {
        if (isset($in[$k]) && is_scalar($in[$k])) return trim((string)$in[$k]);
    }
    foreach (['profile','account','data','payload'] as $k) {
        if (isset($in[$k]) && is_array($in[$k])) {
            $hit = remask_pm_proxy_raw($in[$k]);
            if ($hit !== '') return $hit;
        }
    }
    return '';
}

function remask_pm_cookie_raw(array $in): string {
    foreach (['cookies','cookie','cookies_json','cookie_json'] as $k) {
        if (isset($in[$k]) && is_scalar($in[$k])) return (string)$in[$k];
        if (isset($in[$k]) && is_array($in[$k])) return json_encode($in[$k], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
    return '';
}

function remask_pm_redact_account(array $acc): array {
    foreach (['token','access_token','accessToken','fb_token','meta_token'] as $k) {
        if (isset($acc[$k]) && is_scalar($acc[$k]) && (string)$acc[$k] !== '') $acc[$k] = '***SAVED***';
    }
    return $acc;
}

function remask_pm_out(array $payload): void {
    $legacy = $payload;
    unset($legacy['res']);
    $payload['res'] = json_encode($legacy, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    while (ob_get_level() > 0) { @ob_end_clean(); }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function remask_pm_graph_me(string $token, string $proxyRaw): array {
    if ($token === '') return [];
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/me?fields=id,name';
    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 25,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'Authorization: Bearer ' . $token],
        CURLOPT_USERAGENT => 'ReMask-ProfileSave/1.0',
    ];
    if ($proxyRaw !== '') {
        try { RemaskProxy::fromSemicolonString($proxyRaw)->AddToCurlOptions($opts); } catch (Throwable $e) {}
    }
    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    curl_close($ch);
    $json = is_string($raw) ? json_decode($raw, true) : null;
    return is_array($json) && !isset($json['error']) ? $json : [];
}

try {
    $file = remask_pm_accounts_file();
    $root = remask_pm_read_root($file);
    [$accounts, $mode, $originalRoot] = remask_pm_extract_accounts($root);
    $input = remask_pm_body();
    $action = strtolower(remask_pm_get_any($input, ['action','cmd','op','mode']));
    if ($action === '') $action = remask_pm_get_any($input, ['token','access_token','accessToken','fb_token','meta_token']) !== '' ? 'save' : 'list';

    if (in_array($action, ['list','get','load','all'], true)) {
        remask_pm_out(['ok'=>true, 'success'=>true, 'count'=>count($accounts), 'profiles'=>array_map('remask_pm_redact_account', array_values($accounts)), 'accounts'=>array_map('remask_pm_redact_account', array_values($accounts))]);
    }

    if (in_array($action, ['delete','remove'], true)) {
        $id = remask_pm_get_any($input, ['id','profile_id','fb_id','account_id']);
        $before = count($accounts);
        $accounts = array_values(array_filter($accounts, function($a) use ($id) {
            if (!is_array($a)) return false;
            foreach (['id','profile_id','fb_id','account_id','name','profile_name'] as $k) {
                if (isset($a[$k]) && (string)$a[$k] === $id) return false;
            }
            return true;
        }));
        remask_pm_write($file, remask_pm_apply_accounts($originalRoot, $mode, $accounts));
        remask_pm_out(['ok'=>true, 'success'=>true, 'deleted'=>($before-count($accounts)), 'count'=>count($accounts)]);
    }

    // Save/upsert profile.
    $source = $input;
    foreach (['profile','account','data','payload'] as $k) {
        if (isset($input[$k]) && is_array($input[$k])) $source = array_replace_recursive($source, $input[$k]);
    }

    $token = remask_pm_get_any($source, ['token','access_token','accessToken','fb_token','meta_token']);
    $proxyRaw = remask_pm_proxy_raw($source);
    $cookiesRaw = remask_pm_cookie_raw($source);
    $givenId = remask_pm_get_any($source, ['fb_id','profile_id','id','account_id']);
    $givenName = remask_pm_get_any($source, ['name','profile_name','label','title']);

    if ($token === '') {
        remask_pm_out(['ok'=>false, 'success'=>false, 'error'=>'NO_TOKEN', 'message'=>'No token supplied', 'count'=>count($accounts), 'profiles'=>array_map('remask_pm_redact_account', array_values($accounts))]);
    }

    $me = remask_pm_graph_me($token, $proxyRaw);
    $fbId = (string)($me['id'] ?? $givenId ?: ('fb_' . substr(sha1($token), 0, 12)));
    $name = (string)($me['name'] ?? $givenName ?: $fbId);
    $now = gmdate('c');

    $new = [
        'id' => $fbId,
        'profile_id' => $fbId,
        'fb_id' => $fbId,
        'name' => $name,
        'profile_name' => $name,
        'token' => $token,
        'access_token' => $token,
        'accessToken' => $token,
        'proxy' => $proxyRaw,
        'proxy_raw' => $proxyRaw,
        'proxy_string' => $proxyRaw,
        'cookies' => $cookiesRaw,
        'cookie_json' => $cookiesRaw,
        'status' => 'ATTENTION',
        'sync_status' => 'PENDING',
        'ads_management_granted' => false,
        'permissions' => [],
        'businesses' => [],
        'business_managers' => [],
        'bms' => [],
        'ad_accounts' => [],
        'adAccounts' => [],
        'rk' => [],
        'pages' => [],
        'created_at' => $now,
        'updated_at' => $now,
        'last_sync_at' => '',
        'last_sync_error' => '',
        'source' => 'metaProfileManager.overlay',
    ];

    $found = false;
    foreach ($accounts as $idx => $old) {
        if (!is_array($old)) continue;
        $oldId = remask_pm_get_any($old, ['fb_id','profile_id','id']);
        $oldToken = remask_pm_get_any($old, ['token','access_token','accessToken']);
        if ($oldId === $fbId || ($oldToken !== '' && $oldToken === $token)) {
            $new['created_at'] = (string)($old['created_at'] ?? $now);
            $accounts[$idx] = array_replace($old, $new);
            $found = true;
            break;
        }
    }
    if (!$found) $accounts[] = $new;

    remask_pm_write($file, remask_pm_apply_accounts($originalRoot, $mode, array_values($accounts)));
    $safe = remask_pm_redact_account($new);
    remask_pm_out(['ok'=>true, 'success'=>true, 'saved'=>true, 'profile'=>$safe, 'account'=>$safe, 'count'=>count($accounts), 'profiles'=>array_map('remask_pm_redact_account', array_values($accounts)), 'accounts'=>array_map('remask_pm_redact_account', array_values($accounts))]);
} catch (Throwable $e) {
    remask_pm_out(['ok'=>false, 'success'=>false, 'error'=>'PROFILE_MANAGER_FAILED', 'message'=>$e->getMessage()]);
}
PHP;

file_put_contents($target, $php);
fwrite(STDERR, "[remask account manager overlay] ajax/metaProfileManager.php replaced\n");
