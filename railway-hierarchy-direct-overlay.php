<?php
/**
 * Direct replacement for ajax/metaHierarchy.php.
 * The packed runtime endpoint currently returns Meta OAuthException code 1 "Invalid request".
 * This overlay avoids the old request builder and performs simple official Graph API reads.
 */
$root = '/var/www/html';
$target = $root . '/ajax/metaHierarchy.php';

$php = <<<'PHP'
<?php
declare(strict_types=1);

require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';

header('Content-Type: application/json; charset=utf-8');

function remask_hierarchy_out(array $payload): void {
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function remask_hierarchy_redact(string $s): string {
    $s = preg_replace('/EA[A-Za-z0-9_\-]{20,}/', 'EA***REDACTED***', $s) ?? $s;
    return $s;
}

function remask_hierarchy_data_dir(): string {
    $dir = getenv('REMASK_DATA_DIR') ?: getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask';
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    return $dir;
}

function remask_hierarchy_accounts_file(): string {
    return getenv('REMASK_ACCOUNTS_FILE') ?: remask_hierarchy_data_dir() . '/accounts.json';
}

function remask_hierarchy_read_json(string $file): array {
    if (!is_file($file)) return [];
    $raw = (string)file_get_contents($file);
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function remask_hierarchy_write_json(string $file, array $data): void {
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    $tmp = $file . '.tmp.' . getmypid();
    file_put_contents($tmp, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
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
    $raw = '';
    foreach (['proxy','proxy_raw','proxyString','proxy_string'] as $k) {
        if (isset($account[$k]) && is_scalar($account[$k])) { $raw = trim((string)$account[$k]); break; }
    }
    if ($raw !== '') return RemaskProxy::fromSemicolonString($raw);
    foreach (['proxy','proxy_data','proxyData'] as $k) {
        if (isset($account[$k]) && is_array($account[$k])) return RemaskProxy::fromArray($account[$k]);
    }
    return null;
}

function remask_hierarchy_graph(string $path, array $params, string $bearer, ?RemaskProxy $proxy): array {
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/' . ltrim($path, '/');
    if ($params) $url .= '?' . http_build_query($params);
    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 45,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'Authorization: Bearer ' . $bearer],
        CURLOPT_USERAGENT => 'ReMask-DirectHierarchySync/1.0',
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

function remask_hierarchy_graph_all(string $path, array $params, string $bearer, ?RemaskProxy $proxy, int $maxPages = 20): array {
    $out = [];
    $nextUrl = null;
    for ($page = 0; $page < $maxPages; $page++) {
        if ($nextUrl) {
            $ch = curl_init($nextUrl);
            $opts = [CURLOPT_RETURNTRANSFER=>true, CURLOPT_HEADER=>false, CURLOPT_FOLLOWLOCATION=>false, CURLOPT_CONNECTTIMEOUT=>12, CURLOPT_TIMEOUT=>45, CURLOPT_SSL_VERIFYPEER=>true, CURLOPT_SSL_VERIFYHOST=>2, CURLOPT_HTTPHEADER=>['Accept: application/json'], CURLOPT_USERAGENT=>'ReMask-DirectHierarchySync/1.0'];
            if ($proxy) $proxy->AddToCurlOptions($opts);
            curl_setopt_array($ch, $opts);
            $raw = curl_exec($ch); $status=(int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE); $err=curl_error($ch); curl_close($ch);
            if ($raw === false) throw new RuntimeException('Proxy/transport failed on page: ' . $err);
            $json = json_decode((string)$raw, true);
            if (!is_array($json)) throw new RuntimeException('Meta returned non-JSON page HTTP ' . $status);
            if (isset($json['error'])) throw new RuntimeException((string)($json['error']['message'] ?? 'Meta page error'));
        } else {
            $json = remask_hierarchy_graph($path, $params, $bearer, $proxy);
        }
        foreach ((array)($json['data'] ?? []) as $row) if (is_array($row)) $out[] = $row;
        $nextUrl = is_string($json['paging']['next'] ?? null) ? $json['paging']['next'] : null;
        if (!$nextUrl) break;
    }
    return $out;
}

try {
    $file = remask_hierarchy_accounts_file();
    $accounts = remask_hierarchy_read_json($file);
    if (!is_array($accounts)) $accounts = [];
    $changed = false;
    $results = [];
    $now = gmdate('c');

    foreach ($accounts as $i => $acc) {
        if (!is_array($acc)) continue;
        $bearer = remask_hierarchy_get_any($acc, ['token','accessToken','access_token','fb_token','meta_token']);
        if ($bearer === '') {
            $results[] = ['index'=>$i, 'ok'=>false, 'message'=>'No saved token found in profile'];
            continue;
        }
        try {
            $proxy = remask_hierarchy_proxy_from_account($acc);
            $me = remask_hierarchy_graph('me', ['fields'=>'id,name'], $bearer, $proxy);
            $permissions = remask_hierarchy_graph_all('me/permissions', ['limit'=>200], $bearer, $proxy, 5);
            $adsGranted = false;
            foreach ($permissions as $p) {
                if (($p['permission'] ?? '') === 'ads_management' && ($p['status'] ?? '') === 'granted') $adsGranted = true;
            }
            $businesses = remask_hierarchy_graph_all('me/businesses', ['fields'=>'id,name,verification_status,created_time,updated_time','limit'=>100], $bearer, $proxy, 20);
            $adAccounts = remask_hierarchy_graph_all('me/adaccounts', ['fields'=>'id,account_id,name,account_status,currency,disable_reason,business,amount_spent,balance,spend_cap,timezone_name','limit'=>100], $bearer, $proxy, 20);
            $acc['fb_id'] = (string)($me['id'] ?? ($acc['fb_id'] ?? ''));
            $acc['name'] = (string)($me['name'] ?? ($acc['name'] ?? ''));
            $acc['profile_name'] = $acc['name'];
            $acc['permissions'] = $permissions;
            $acc['ads_management_granted'] = $adsGranted;
            $acc['businesses'] = $businesses;
            $acc['business_managers'] = $businesses;
            $acc['bms'] = $businesses;
            $acc['ad_accounts'] = $adAccounts;
            $acc['adAccounts'] = $adAccounts;
            $acc['rk'] = $adAccounts;
            $acc['sync_status'] = $adsGranted ? 'SYNCED' : 'ATTENTION';
            $acc['status'] = $adsGranted ? 'SYNCED' : 'ATTENTION';
            $acc['last_sync_at'] = $now;
            $acc['lastSuccessfulSyncAt'] = $now;
            $acc['last_successful_sync_at'] = $now;
            $acc['meta_hierarchy'] = ['profile'=>$me, 'permissions'=>$permissions, 'businesses'=>$businesses, 'ad_accounts'=>$adAccounts, 'synced_at'=>$now];
            $accounts[$i] = $acc;
            $changed = true;
            $results[] = ['index'=>$i, 'ok'=>true, 'profile'=>$me, 'ads_management_granted'=>$adsGranted, 'businesses_count'=>count($businesses), 'ad_accounts_count'=>count($adAccounts)];
        } catch (Throwable $e) {
            $acc['sync_status'] = 'ATTENTION';
            $acc['last_sync_error'] = remask_hierarchy_redact($e->getMessage());
            $acc['last_sync_at'] = $now;
            $accounts[$i] = $acc;
            $changed = true;
            $results[] = ['index'=>$i, 'ok'=>false, 'message'=>remask_hierarchy_redact($e->getMessage())];
        }
    }

    if ($changed) remask_hierarchy_write_json($file, $accounts);
    $okCount = 0; $rkCount = 0; $bmCount = 0;
    foreach ($results as $r) { if (!empty($r['ok'])) { $okCount++; $rkCount += (int)($r['ad_accounts_count'] ?? 0); $bmCount += (int)($r['businesses_count'] ?? 0); } }
    remask_hierarchy_out(['ok'=>$okCount > 0, 'synced'=>$okCount > 0, 'profiles_synced'=>$okCount, 'businesses_count'=>$bmCount, 'ad_accounts_count'=>$rkCount, 'results'=>$results]);
} catch (Throwable $e) {
    remask_hierarchy_out(['ok'=>false, 'synced'=>false, 'profiles_synced'=>0, 'error'=>'DIRECT_HIERARCHY_SYNC_FAILED', 'message'=>remask_hierarchy_redact($e->getMessage())]);
}
PHP;

file_put_contents($target, $php);
fwrite(STDERR, "[remask hierarchy direct overlay] ajax/metaHierarchy.php replaced\n");
