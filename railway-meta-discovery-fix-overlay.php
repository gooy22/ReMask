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

// Startup probe: isolate the exact failing Graph step and compare the transport
// used by ReMask against the conventional access_token transport used by SDKs.
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
require_once __DIR__ . '/../classes/AccountStoreFactory.php';

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

/** Direct, read-only official Graph request. Token is never returned or logged. */
function rmx_graph_transport_probe(string $version, string $token, string $authMode): array {
    $url = 'https://graph.facebook.com/' . $version . '/me?fields=id%2Cname';
    $headers = ['Accept: application/json', 'User-Agent: ReMask-AuthProbe/1.0'];
    if ($authMode === 'bearer') {
        $headers[] = 'Authorization: Bearer ' . $token;
    } elseif ($authMode === 'oauth') {
        $headers[] = 'Authorization: OAuth ' . $token;
    } elseif ($authMode === 'query') {
        $url .= '&access_token=' . rawurlencode($token);
    } else {
        return ['ok'=>false,'http'=>0,'message'=>'unsupported probe auth mode'];
    }

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_TIMEOUT => 25,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => $headers,
    ]);
    $raw = curl_exec($ch);
    $curlErr = curl_error($ch);
    $curlNo = curl_errno($ch);
    $http = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($raw === false) {
        return ['ok'=>false,'http'=>$http,'transport_error'=>rmx_probe_clean($curlErr ?: ('cURL errno ' . $curlNo))];
    }
    $decoded = json_decode((string)$raw, true);
    if (!is_array($decoded)) {
        return ['ok'=>false,'http'=>$http,'message'=>'non-json response'];
    }
    if (isset($decoded['error']) && is_array($decoded['error'])) {
        $e = $decoded['error'];
        return [
            'ok'=>false,
            'http'=>$http,
            'type'=>(string)($e['type'] ?? ''),
            'code'=>isset($e['code']) ? (int)$e['code'] : null,
            'subcode'=>isset($e['error_subcode']) ? (int)$e['error_subcode'] : null,
            'message'=>rmx_probe_clean((string)($e['message'] ?? 'Meta error')),
            'fbtrace_id'=>(string)($e['fbtrace_id'] ?? ''),
        ];
    }
    return [
        'ok'=>$http >= 200 && $http < 300 && isset($decoded['id']),
        'http'=>$http,
        'id_present'=>isset($decoded['id']),
        'name_present'=>isset($decoded['name']),
    ];
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
    'token_shape' => null,
    'auth_transport' => null,
];

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account = $store->getAccountByName($profile);
    if ($account === null) throw new RuntimeException('Configured profile not found in AccountStore.');
    $rawToken = (string)$account->token;
    $token = trim($rawToken);
    if ($token === '') throw new RuntimeException('Configured profile has an empty token.');
    $out['token_shape'] = [
        'raw_length'=>strlen($rawToken),
        'trimmed_length'=>strlen($token),
        'had_surrounding_whitespace'=>$rawToken !== $token,
        'starts_eaa'=>str_starts_with($token, 'EAA'),
        'contains_space'=>preg_match('/\s/', $token) === 1,
        'contains_pipe'=>str_contains($token, '|'),
        'contains_colon'=>str_contains($token, ':'),
    ];

    // Direct network only. Compare auth encoding on the current API version.
    $out['auth_transport'] = [
        'v26_bearer'=>rmx_graph_transport_probe('v26.0', $token, 'bearer'),
        'v26_query'=>rmx_graph_transport_probe('v26.0', $token, 'query'),
        'v26_oauth'=>rmx_graph_transport_probe('v26.0', $token, 'oauth'),
        'v25_query'=>rmx_graph_transport_probe('v25.0', $token, 'query'),
        'v24_query'=>rmx_graph_transport_probe('v24.0', $token, 'query'),
    ];

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
fwrite(STDERR, "[meta-discovery-fix] per-step + auth-mode Meta sync probe ready\n");
