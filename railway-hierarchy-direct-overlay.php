<?php
/**
 * Direct replacement for ajax/metaHierarchy.php.
 * Keeps saved FB profiles intact, performs official Graph API reads, and returns
 * both the old legacy `res` string and plain JSON fields so existing Workspace JS
 * and the new autosync JS can both understand the response.
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

function remask_hierarchy_redact(string $s): string {
    return preg_replace('/EA[A-Za-z0-9_\-]{20,}/', 'EA***REDACTED***', $s) ?? $s;
}

function remask_hierarchy_respond(array $payload): void {
    $payload['legacy'] = true;
    if (!isset($payload['message'])) {
        $payload['message'] = !empty($payload['ok']) ? 'Meta hierarchy sync complete.' : 'Meta hierarchy sync did not complete.';
    }
    $res = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    echo json_encode($payload + ['res' => $res], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
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

function remask_hierarchy_read_raw_json(string $file): array {
    if (!is_file($file)) return [[], false, 'missing'];
    $raw = (string)file_get_contents($file);
    if (trim($raw) === '') return [[], false, 'empty'];
    $data = json_decode($raw, true);
    if (!is_array($data)) return [[], false, 'invalid_json'];
    return [$data, true, 'ok'];
}

function remask_hierarchy_count_profiles(array $data): int {
    if (array_is_list($data)) return count($data);
    foreach (['accounts','profiles','items','data'] as $k) {
        if (isset($data[$k]) && is_array($data[$k])) return count($data[$k]);
    }
    $count = 0;
    foreach ($data as $v) if (is_array($v)) $count++;
    return $count;
}

function remask_hierarchy_each_account_ref(array &$data): array {
    $refs = [];
    if (array_is_list($data)) {
        foreach (array_keys($data) as $k) if (is_array($data[$k])) $refs[] =& $data[$k];
        return $refs;
    }
    foreach (['accounts','profiles','items','data'] as $container) {
        if (isset($data[$container]) && is_array($data[$container])) {
            foreach (array_keys($data[$container]) as $k) if (is_array($data[$container][$k])) $refs[] =& $data[$container][$k];
            return $refs;
        }
    }
    foreach (array_keys($data) as $k) if (is_array($data[$k])) $refs[] =& $data[$k];
    return $refs;
}

function remask_hierarchy_write_json_safe(string $file, array $old, array $new): void {
    $oldCount = remask_hierarchy_count_profiles($old);
    $newCount = remask_hierarchy_count_profiles($new);
    if ($oldCount > 0 && $newCount === 0) {
        throw new RuntimeException('Refused to overwrite saved profiles with an empty accounts list.');
    }
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    if (is_file($file) && filesize($file) > 2) {
        @copy($file, $file . '.bak.' . gmdate('YmdHis'));
    }
    $tmp = $file . '.tmp.' . getmypid();
    file_put_contents($tmp, json_encode($new, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT));
    if (!@rename($tmp, $file)) {
        @unlink($tmp);
        throw new RuntimeException('Could not save accounts.json.');
    }
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
            return RemaskProxy::fromSemicolonString((string)$account[$k]);
        }
    }
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

function remask_hierarchy_graph_all(string $path, array $params, string $bearer, ?RemaskProxy $proxy, int $maxPages = 20): array {
    $out = [];
    $json = remask_hierarchy_graph($path, $params, $bearer, $proxy);
    for ($page = 0; $page < $maxPages; $page++) {
        foreach ((array)($json['data'] ?? []) as $row) if (is_array($row)) $out[] = $row;
        $next = is_string($json['paging']['next'] ?? null) ? $json['paging']['next'] : '';
        if ($next === '') break;
        $ch = curl_init($next);
        $opts = [CURLOPT_RETURNTRANSFER=>true, CURLOPT_HEADER=>false, CURLOPT_FOLLOWLOCATION=>false, CURLOPT_CONNECTTIMEOUT=>12, CURLOPT_TIMEOUT=>45, CURLOPT_SSL_VERIFYPEER=>true, CURLOPT_SSL_VERIFYHOST=>2, CURLOPT_HTTPHEADER=>['Accept: application/json'], CURLOPT_USERAGENT=>'ReMask-DirectHierarchySync/1.2'];
        if ($proxy) $proxy->AddToCurlOptions($opts);
        curl_setopt_array($ch, $opts);
        $raw = curl_exec($ch); $status=(int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE); $err=curl_error($ch); curl_close($ch);
        if ($raw === false) throw new RuntimeException('Proxy/transport failed on page: ' . $err);
        $json = json_decode((string)$raw, true);
        if (!is_array($json)) throw new RuntimeException('Meta returned non-JSON page HTTP ' . $status);
        if (isset($json['error'])) throw new RuntimeException((string)($json['error']['message'] ?? 'Meta page error'));
    }
    return $out;
}

try {
    $file = remask_hierarchy_accounts_file();
    [$accountsData, $valid, $readStatus] = remask_hierarchy_read_raw_json($file);
    if (!$valid) {
        remask_hierarchy_respond(['ok'=>false, 'synced'=>false, 'profiles_synced'=>0, 'error'=>'ACCOUNTS_FILE_' . strtoupper($readStatus), 'message'=>'Accounts storage is ' . $readStatus . '; no profiles were modified.']);
    }
    $profileCount = remask_hierarchy_count_profiles($accountsData);
    if ($profileCount === 0) {
        remask_hierarchy_respond(['ok'=>false, 'synced'=>false, 'profiles_synced'=>0, 'error'=>'NO_SAVED_PROFILES', 'message'=>'No saved FB profiles found. Re-add the FB account; sync will not overwrite storage.']);
    }

    $working = $accountsData;
    $refs =& remask_hierarchy_each_account_ref($working);
    $changed = false;
    $results = [];
    $now = gmdate('c');
    $idx = 0;

    foreach ($refs as &$acc) {
        if (!is_array($acc)) { $idx++; continue; }
        $bearer = remask_hierarchy_get_any($acc, ['token','accessToken','access_token','fb_token','meta_token']);
        if ($bearer === '') {
            $acc['sync_status'] = 'ATTENTION';
            $acc['last_sync_error'] = 'No saved token found in profile';
            $acc['last_sync_at'] = $now;
            $changed = true;
            $results[] = ['index'=>$idx, 'ok'=>false, 'message'=>'No saved token found in profile'];
            $idx++;
            continue;
        }
        try {
            $proxy = remask_hierarchy_proxy_from_account($acc);
            $me = remask_hierarchy_graph('me', ['fields'=>'id,name'], $bearer, $proxy);
            $permissions = remask_hierarchy_graph_all('me/permissions', ['limit'=>200], $bearer, $proxy, 5);
            $adsGranted = false;
            foreach ($permissions as $p) if (($p['permission'] ?? '') === 'ads_management' && ($p['status'] ?? '') === 'granted') $adsGranted = true;

            $businesses = [];
            $businessError = '';
            try { $businesses = remask_hierarchy_graph_all('me/businesses', ['fields'=>'id,name,verification_status,created_time,updated_time','limit'=>100], $bearer, $proxy, 20); }
            catch (Throwable $e) { $businessError = remask_hierarchy_redact($e->getMessage()); }

            $adAccounts = remask_hierarchy_graph_all('me/adaccounts', ['fields'=>'id,account_id,name,account_status,currency,disable_reason,business,amount_spent,balance,spend_cap,timezone_name','limit'=>100], $bearer, $proxy, 20);

            $acc['fb_id'] = (string)($me['id'] ?? ($acc['fb_id'] ?? ''));
            $acc['id'] = (string)($acc['id'] ?? $acc['fb_id']);
            $acc['name'] = (string)($me['name'] ?? ($acc['name'] ?? ''));
            $acc['profile_name'] = $acc['name'];
            $acc['permissions'] = $permissions;
            $acc['permissions_summary'] = array_values(array_filter(array_map(static fn($p) => (($p['status'] ?? '') === 'granted') ? (string)($p['permission'] ?? '') : '', $permissions)));
            $acc['ads_management_granted'] = $adsGranted;
            $acc['businesses'] = $businesses;
            $acc['business_managers'] = $businesses;
            $acc['businessManagers'] = $businesses;
            $acc['bms'] = $businesses;
            $acc['businesses_count'] = count($businesses);
            $acc['bm_count'] = count($businesses);
            $acc['ad_accounts'] = $adAccounts;
            $acc['adAccounts'] = $adAccounts;
            $acc['advertising_accounts'] = $adAccounts;
            $acc['rk'] = $adAccounts;
            $acc['ad_accounts_count'] = count($adAccounts);
            $acc['rk_count'] = count($adAccounts);
            $acc['sync_status'] = ($adsGranted && count($adAccounts) > 0) ? 'SYNCED' : 'ATTENTION';
            $acc['status'] = $acc['sync_status'];
            $acc['last_sync_at'] = $now;
            $acc['lastSuccessfulSyncAt'] = $now;
            $acc['last_successful_sync_at'] = $now;
            $acc['last_sync_error'] = $businessError !== '' ? ('Business sync warning: ' . $businessError) : '';
            $acc['meta_hierarchy'] = ['profile'=>$me, 'permissions'=>$permissions, 'businesses'=>$businesses, 'ad_accounts'=>$adAccounts, 'synced_at'=>$now];
            $changed = true;
            $results[] = ['index'=>$idx, 'ok'=>true, 'profile'=>['id'=>$acc['fb_id'], 'name'=>$acc['name']], 'ads_management_granted'=>$adsGranted, 'businesses_count'=>count($businesses), 'ad_accounts_count'=>count($adAccounts), 'business_warning'=>$businessError];
        } catch (Throwable $e) {
            $acc['sync_status'] = 'ATTENTION';
            $acc['status'] = 'ATTENTION';
            $acc['last_sync_error'] = remask_hierarchy_redact($e->getMessage());
            $acc['last_sync_at'] = $now;
            $changed = true;
            $results[] = ['index'=>$idx, 'ok'=>false, 'message'=>remask_hierarchy_redact($e->getMessage())];
        }
        $idx++;
    }
    unset($acc);

    if ($changed) remask_hierarchy_write_json_safe($file, $accountsData, $working);

    $okCount = 0; $rkCount = 0; $bmCount = 0;
    foreach ($results as $r) { if (!empty($r['ok'])) { $okCount++; $rkCount += (int)($r['ad_accounts_count'] ?? 0); $bmCount += (int)($r['businesses_count'] ?? 0); } }
    remask_hierarchy_respond(['ok'=>$okCount > 0, 'synced'=>$okCount > 0, 'profiles_found'=>$profileCount, 'profiles_synced'=>$okCount, 'businesses_count'=>$bmCount, 'ad_accounts_count'=>$rkCount, 'results'=>$results]);
} catch (Throwable $e) {
    remask_hierarchy_respond(['ok'=>false, 'synced'=>false, 'profiles_synced'=>0, 'error'=>'DIRECT_HIERARCHY_SYNC_FAILED', 'message'=>remask_hierarchy_redact($e->getMessage())]);
}
PHP;

file_put_contents($target, $php);
fwrite(STDERR, "[remask hierarchy direct overlay] ajax/metaHierarchy.php replaced with safe legacy-compatible sync\n");
