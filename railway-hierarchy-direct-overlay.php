<?php
/**
 * Direct replacement for ajax/metaHierarchy.php.
 * Safe JSON-only endpoint: never prints PHP notices into ajax response, preserves
 * saved profiles, uses official Graph API reads, and returns legacy `res` too.
 */
$root = '/var/www/html';
$target = $root . '/ajax/metaHierarchy.php';

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

function remask_hierarchy_redact(string $s): string {
    $s = preg_replace('/EA[A-Za-z0-9_\-]{15,}/', 'EA***REDACTED***', $s) ?? $s;
    $s = preg_replace('/access_token=([^&\s]+)/', 'access_token=***REDACTED***', $s) ?? $s;
    return $s;
}

function remask_hierarchy_out(array $payload): void {
    $payload['legacy'] = true;
    $legacy = $payload;
    unset($legacy['res']);
    $payload['res'] = json_encode($legacy, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    while (ob_get_level() > 0) { @ob_end_clean(); }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function remask_hierarchy_data_dir(): string {
    $dir = getenv('REMASK_DATA_DIR') ?: getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask';
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    return $dir;
}

function remask_hierarchy_accounts_file(): string {
    return getenv('REMASK_ACCOUNTS_FILE') ?: remask_hierarchy_data_dir() . '/accounts.json';
}

function remask_hierarchy_read_json_raw(string $file) {
    if (!is_file($file)) return [];
    $raw = (string)@file_get_contents($file);
    if (trim($raw) === '') return [];
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function remask_hierarchy_is_list(array $a): bool {
    return function_exists('array_is_list') ? array_is_list($a) : (array_keys($a) === range(0, count($a) - 1));
}

function remask_hierarchy_extract_accounts(array $root): array {
    if (remask_hierarchy_is_list($root)) return [$root, 'list', $root];
    foreach (['accounts', 'profiles', 'items', 'data'] as $key) {
        if (isset($root[$key]) && is_array($root[$key])) return [$root[$key], $key, $root];
    }
    return [[], 'list', $root];
}

function remask_hierarchy_apply_accounts(array $originalRoot, string $mode, array $accounts): array {
    if ($mode === 'list') return $accounts;
    $originalRoot[$mode] = $accounts;
    return $originalRoot;
}

function remask_hierarchy_recover_accounts(string $file): array {
    $root = remask_hierarchy_read_json_raw($file);
    [$accounts, $mode, $original] = remask_hierarchy_extract_accounts(is_array($root) ? $root : []);
    if (count($accounts) > 0) return [$accounts, $mode, $original, false];

    $candidates = glob($file . '.bak*') ?: [];
    rsort($candidates, SORT_STRING);
    foreach ($candidates as $candidate) {
        $bakRoot = remask_hierarchy_read_json_raw($candidate);
        [$bakAccounts, $bakMode, $bakOriginal] = remask_hierarchy_extract_accounts(is_array($bakRoot) ? $bakRoot : []);
        if (count($bakAccounts) > 0) return [$bakAccounts, $bakMode, $bakOriginal, true];
    }
    return [[], $mode, $original, false];
}

function remask_hierarchy_write_json(string $file, array $root, int $accountCount): void {
    if ($accountCount <= 0) {
        throw new RuntimeException('Refusing to write empty account store');
    }
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    if (is_file($file) && filesize($file) > 2) {
        @copy($file, $file . '.bak.' . gmdate('YmdHis'));
    }
    $tmp = $file . '.tmp.' . getmypid();
    file_put_contents($tmp, json_encode($root, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
    @rename($tmp, $file);
}

function remask_hierarchy_get_any(array $a, array $keys): string {
    foreach ($keys as $k) {
        if (array_key_exists($k, $a) && is_scalar($a[$k]) && trim((string)$a[$k]) !== '') return trim((string)$a[$k]);
    }
    foreach ($a as $v) {
        if (is_array($v)) {
            $hit = remask_hierarchy_get_any($v, $keys);
            if ($hit !== '') return $hit;
        }
    }
    return '';
}

function remask_hierarchy_proxy_from_account(array $account): ?RemaskProxy {
    foreach (['proxy','proxy_raw','proxyString','proxy_string'] as $k) {
        if (isset($account[$k]) && is_scalar($account[$k]) && trim((string)$account[$k]) !== '') {
            return RemaskProxy::fromSemicolonString(trim((string)$account[$k]));
        }
    }
    foreach (['proxy_data','proxyData'] as $k) {
        if (isset($account[$k]) && is_array($account[$k])) return RemaskProxy::fromArray($account[$k]);
    }
    if (isset($account['proxy']) && is_array($account['proxy'])) return RemaskProxy::fromArray($account['proxy']);
    return null;
}

function remask_hierarchy_graph_url(string $path, array $params): string {
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/' . ltrim($path, '/');
    if ($params) $url .= '?' . http_build_query($params);
    return $url;
}

function remask_hierarchy_curl_json(string $url, string $bearer, ?RemaskProxy $proxy, bool $authHeader = true): array {
    $ch = curl_init($url);
    $headers = ['Accept: application/json'];
    if ($authHeader) $headers[] = 'Authorization: Bearer ' . $bearer;
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 45,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_USERAGENT => 'ReMask-DirectHierarchySync/1.2',
    ];
    if ($proxy) $proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    $err = curl_error($ch);
    $errno = curl_errno($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if ($raw === false) throw new RuntimeException('Proxy/transport failed: ' . ($err ?: ('cURL errno ' . $errno)));
    $json = json_decode((string)$raw, true);
    if (!is_array($json)) throw new RuntimeException('Meta returned non-JSON HTTP ' . $status);
    if (isset($json['error']) && is_array($json['error'])) {
        $e = $json['error'];
        throw new RuntimeException(trim((string)($e['message'] ?? 'Meta error')) . ' type=' . (string)($e['type'] ?? '') . ' code=' . (string)($e['code'] ?? '') . ' fbtrace=' . (string)($e['fbtrace_id'] ?? ''));
    }
    if ($status < 200 || $status >= 300) throw new RuntimeException('Meta HTTP ' . $status);
    return $json;
}

function remask_hierarchy_graph(string $path, array $params, string $bearer, ?RemaskProxy $proxy): array {
    return remask_hierarchy_curl_json(remask_hierarchy_graph_url($path, $params), $bearer, $proxy, true);
}

function remask_hierarchy_graph_all(string $path, array $params, string $bearer, ?RemaskProxy $proxy, int $maxPages = 20): array {
    $out = [];
    $nextUrl = null;
    for ($page = 0; $page < $maxPages; $page++) {
        $json = $nextUrl
            ? remask_hierarchy_curl_json($nextUrl, $bearer, $proxy, false)
            : remask_hierarchy_graph($path, $params, $bearer, $proxy);
        foreach ((array)($json['data'] ?? []) as $row) if (is_array($row)) $out[] = $row;
        $nextUrl = is_string($json['paging']['next'] ?? null) ? $json['paging']['next'] : null;
        if (!$nextUrl) break;
    }
    return $out;
}

function remask_hierarchy_try_all(array $attempts, string $bearer, ?RemaskProxy $proxy, array &$errors): array {
    foreach ($attempts as $attempt) {
        try {
            return remask_hierarchy_graph_all($attempt[0], $attempt[1], $bearer, $proxy, (int)($attempt[2] ?? 20));
        } catch (Throwable $e) {
            $errors[] = remask_hierarchy_redact($attempt[0] . ': ' . $e->getMessage());
        }
    }
    return [];
}

try {
    $file = remask_hierarchy_accounts_file();
    [$accounts, $mode, $originalRoot, $recovered] = remask_hierarchy_recover_accounts($file);
    $profilesFound = count($accounts);

    if ($profilesFound <= 0) {
        remask_hierarchy_out([
            'ok' => false,
            'synced' => false,
            'profiles_found' => 0,
            'profiles_synced' => 0,
            'businesses_count' => 0,
            'ad_accounts_count' => 0,
            'error' => 'NO_SAVED_PROFILES',
            'message' => 'No saved FB profiles found. Add the FB account again.',
            'results' => [],
        ]);
    }

    $changed = $recovered;
    $results = [];
    $now = gmdate('c');

    foreach ($accounts as $i => $acc0) {
        if (!is_array($acc0)) continue;
        $acc = $acc0;
        $bearer = remask_hierarchy_get_any($acc, ['token','accessToken','access_token','fb_token','meta_token']);
        if ($bearer === '') {
            $acc['sync_status'] = 'ATTENTION';
            $acc['last_sync_error'] = 'No saved token found in profile';
            $acc['last_sync_at'] = $now;
            $accounts[$i] = $acc;
            $changed = true;
            $results[] = ['index'=>$i, 'ok'=>false, 'message'=>'No saved token found in profile'];
            continue;
        }

        try {
            $proxy = remask_hierarchy_proxy_from_account($acc);
            $errors = [];
            $me = remask_hierarchy_graph('me', ['fields'=>'id,name'], $bearer, $proxy);

            $permissions = remask_hierarchy_try_all([
                ['me/permissions', ['limit'=>200], 5],
            ], $bearer, $proxy, $errors);

            $adsGranted = false;
            foreach ($permissions as $p) {
                if (($p['permission'] ?? '') === 'ads_management' && ($p['status'] ?? '') === 'granted') $adsGranted = true;
            }

            $businesses = remask_hierarchy_try_all([
                ['me/businesses', ['fields'=>'id,name,verification_status','limit'=>100], 20],
                ['me/businesses', ['fields'=>'id,name','limit'=>100], 20],
            ], $bearer, $proxy, $errors);

            $adAccounts = remask_hierarchy_try_all([
                ['me/adaccounts', ['fields'=>'id,account_id,name,account_status,currency,disable_reason,business,amount_spent,balance,spend_cap,timezone_name','limit'=>100], 20],
                ['me/adaccounts', ['fields'=>'id,account_id,name,account_status,currency,disable_reason,business','limit'=>100], 20],
                ['me/adaccounts', ['fields'=>'id,account_id,name,account_status,currency','limit'=>100], 20],
            ], $bearer, $proxy, $errors);

            $pages = remask_hierarchy_try_all([
                ['me/accounts', ['fields'=>'id,name,access_token,category,tasks','limit'=>100], 20],
                ['me/accounts', ['fields'=>'id,name,category','limit'=>100], 20],
            ], $bearer, $proxy, $errors);
            foreach ($pages as $pi => $page) {
                if (isset($page['access_token'])) $pages[$pi]['access_token'] = '***REDACTED_STORED_BY_META***';
            }

            $acc['fb_id'] = (string)($me['id'] ?? ($acc['fb_id'] ?? ''));
            $acc['id'] = $acc['id'] ?? ($acc['fb_id'] ?: ('profile_' . $i));
            $acc['name'] = (string)($me['name'] ?? ($acc['name'] ?? $acc['id']));
            $acc['profile_name'] = $acc['name'];
            $acc['permissions'] = $permissions;
            $acc['ads_management_granted'] = $adsGranted;
            $acc['businesses'] = $businesses;
            $acc['business_managers'] = $businesses;
            $acc['bms'] = $businesses;
            $acc['ad_accounts'] = $adAccounts;
            $acc['adAccounts'] = $adAccounts;
            $acc['rk'] = $adAccounts;
            $acc['pages'] = $pages;
            $acc['sync_status'] = $adsGranted ? 'SYNCED' : 'ATTENTION';
            $acc['status'] = $adsGranted ? 'SYNCED' : 'ATTENTION';
            $acc['last_sync_at'] = $now;
            if ($adsGranted || count($adAccounts) > 0 || count($businesses) > 0) {
                $acc['lastSuccessfulSyncAt'] = $now;
                $acc['last_successful_sync_at'] = $now;
            }
            $acc['last_sync_error'] = $adsGranted ? '' : 'ads_management is not granted or not visible for this token';
            $acc['meta_hierarchy'] = [
                'profile' => $me,
                'permissions' => $permissions,
                'businesses' => $businesses,
                'ad_accounts' => $adAccounts,
                'pages' => $pages,
                'synced_at' => $now,
                'warnings' => $errors,
            ];
            $accounts[$i] = $acc;
            $changed = true;
            $results[] = [
                'index' => $i,
                'ok' => true,
                'profile' => $me,
                'ads_management_granted' => $adsGranted,
                'businesses_count' => count($businesses),
                'ad_accounts_count' => count($adAccounts),
                'pages_count' => count($pages),
                'warnings' => $errors,
            ];
        } catch (Throwable $e) {
            $acc['sync_status'] = 'ATTENTION';
            $acc['status'] = 'ATTENTION';
            $acc['last_sync_error'] = remask_hierarchy_redact($e->getMessage());
            $acc['last_sync_at'] = $now;
            $accounts[$i] = $acc;
            $changed = true;
            $results[] = ['index'=>$i, 'ok'=>false, 'message'=>remask_hierarchy_redact($e->getMessage())];
        }
    }

    if ($changed) {
        $newRoot = remask_hierarchy_apply_accounts($originalRoot, $mode, $accounts);
        remask_hierarchy_write_json($file, $newRoot, count($accounts));
    }

    $okCount = 0; $rkCount = 0; $bmCount = 0; $pageCount = 0;
    foreach ($results as $r) {
        if (!empty($r['ok'])) {
            $okCount++;
            $rkCount += (int)($r['ad_accounts_count'] ?? 0);
            $bmCount += (int)($r['businesses_count'] ?? 0);
            $pageCount += (int)($r['pages_count'] ?? 0);
        }
    }

    $message = $okCount > 0
        ? 'Meta hierarchy sync completed'
        : 'Meta hierarchy sync did not complete';
    remask_hierarchy_out([
        'ok' => $okCount > 0,
        'synced' => $okCount > 0,
        'profiles_found' => $profilesFound,
        'profiles_synced' => $okCount,
        'businesses_count' => $bmCount,
        'ad_accounts_count' => $rkCount,
        'pages_count' => $pageCount,
        'message' => $message,
        'results' => $results,
    ]);
} catch (Throwable $e) {
    remask_hierarchy_out([
        'ok' => false,
        'synced' => false,
        'profiles_found' => 0,
        'profiles_synced' => 0,
        'businesses_count' => 0,
        'ad_accounts_count' => 0,
        'error' => 'DIRECT_HIERARCHY_SYNC_FAILED',
        'message' => remask_hierarchy_redact($e->getMessage()),
        'results' => [],
    ]);
}
PHP;

file_put_contents($target, $php);
fwrite(STDERR, "[remask hierarchy direct overlay] ajax/metaHierarchy.php replaced with clean JSON safe sync\n");
