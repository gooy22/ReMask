<?php
/**
 * Session-aware checkAccount endpoint.
 * Browser-session Meta tokens may need the same saved Facebook cookies that the
 * canonical MetaApiClient receives after import. Never echoes secret values.
 */
$target = '/var/www/html/ajax/checkAccount.php';
$php = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/ResponseFormatter.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/FbAccount.php';

function rmx_check_cookie_jar(mixed $raw): array
{
    if (is_array($raw)) $decoded = $raw;
    else {
        $text = trim((string)$raw);
        if ($text === '') return [];
        $decoded = json_decode($text, true);
        if (!is_array($decoded)) throw new InvalidArgumentException('Cookies must be valid JSON.');
    }
    $rows = array_is_list($decoded) ? $decoded : array_values($decoded);
    $out = [];
    foreach ($rows as $cookie) {
        if (!is_array($cookie)) continue;
        $name = trim((string)($cookie['name'] ?? ''));
        $value = (string)($cookie['value'] ?? '');
        if ($name === '' || $value === '') continue;
        $out[] = ['name'=>$name, 'value'=>$value];
    }
    return $out;
}

function rmx_check_cookie_header(array $cookies): string
{
    $parts = [];
    foreach ($cookies as $cookie) {
        $name = (string)($cookie['name'] ?? '');
        $value = (string)($cookie['value'] ?? '');
        if ($name === '' || $value === '') continue;
        $parts[] = $name . '=' . $value;
    }
    return implode('; ', $parts);
}

function rmx_check_saved_account(string $token): ?FbAccount
{
    try {
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        foreach ((array)$store->deserialize() as $account) {
            if (!$account instanceof FbAccount) continue;
            $saved = trim((string)$account->token);
            if ($saved !== '' && hash_equals(hash('sha256', $saved), hash('sha256', $token))) return $account;
        }
    } catch (Throwable) {
        // Preflight still works with explicit request context if storage is unavailable.
    }
    return null;
}

function rmx_check_graph_get(string $path, array $params, string $token, ?RemaskProxy $proxy, string $cookieHeader): array
{
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/' . ltrim($path, '/');
    if ($params !== []) $url .= '?' . http_build_query($params);

    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 35,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'Authorization: Bearer ' . $token],
        CURLOPT_USERAGENT => 'ReMask-MetaApiCheck/1.3',
    ];
    if ($cookieHeader !== '') $opts[CURLOPT_COOKIE] = $cookieHeader;
    if ($proxy !== null) $proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    $curlError = curl_error($ch);
    $curlErrno = curl_errno($ch);
    $httpStatus = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($raw === false) throw new RuntimeException('Proxy/transport failed before Meta response: ' . ($curlError ?: ('cURL errno ' . $curlErrno)));
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) throw new RuntimeException('Meta returned non-JSON response, HTTP ' . $httpStatus . '.');
    if (isset($decoded['error']) && is_array($decoded['error'])) {
        $err = $decoded['error'];
        $parts = [trim((string)($err['message'] ?? 'Meta rejected request.'))];
        $type = trim((string)($err['type'] ?? ''));
        $code = isset($err['code']) ? (int)$err['code'] : 0;
        $subcode = isset($err['error_subcode']) ? (int)$err['error_subcode'] : 0;
        if ($type !== '') $parts[] = 'type ' . $type;
        if ($code) $parts[] = 'code ' . $code;
        if ($subcode) $parts[] = 'subcode ' . $subcode;
        throw new RuntimeException('Meta API check failed: ' . implode(', ', $parts));
    }
    if ($httpStatus < 200 || $httpStatus >= 300) throw new RuntimeException('Meta API returned HTTP ' . $httpStatus . '.');
    return $decoded;
}

try {
    $token = trim((string)($_POST['token'] ?? $_POST['access_token'] ?? ''));
    if ($token === '') throw new InvalidArgumentException('Access token is required.');

    $savedAccount = rmx_check_saved_account($token);
    $proxyRaw = trim((string)($_POST['proxy'] ?? ''));
    $proxy = $proxyRaw !== '' ? RemaskProxy::fromSemicolonString($proxyRaw) : ($savedAccount?->proxy ?? null);

    $cookies = rmx_check_cookie_jar($_POST['cookies'] ?? $_POST['cookie'] ?? '');
    $usedSavedSession = false;
    if ($cookies === [] && $savedAccount instanceof FbAccount) {
        $cookies = rmx_check_cookie_jar((array)$savedAccount->cookies);
        $usedSavedSession = $cookies !== [];
    }
    $cookieHeader = rmx_check_cookie_header($cookies);

    $me = rmx_check_graph_get('me', ['fields'=>'id,name'], $token, $proxy, $cookieHeader);
    $permissions = rmx_check_graph_get('me/permissions', ['limit'=>200], $token, $proxy, $cookieHeader);
    $adsManagementGranted = false;
    foreach ((array)($permissions['data'] ?? []) as $permission) {
        if (is_array($permission) && ($permission['permission'] ?? '') === 'ads_management' && ($permission['status'] ?? '') === 'granted') {
            $adsManagementGranted = true;
            break;
        }
    }
    if (!$adsManagementGranted) throw new RuntimeException('ads_management permission is not granted for this token.');
    $adAccounts = rmx_check_graph_get('me/adaccounts', ['fields'=>'id,name,account_status,currency,disable_reason','limit'=>50], $token, $proxy, $cookieHeader);

    ResponseFormatter::Respond(['res'=>json_encode([
        'ok'=>true,
        'profile'=>['id'=>(string)($me['id'] ?? ''),'name'=>(string)($me['name'] ?? '')],
        'ads_management_granted'=>true,
        'ad_accounts_count'=>count((array)($adAccounts['data'] ?? [])),
        'proxy_used'=>$proxy !== null,
        'session_used'=>$cookieHeader !== '',
        'saved_session_used'=>$usedSavedSession,
        'cookie_count'=>count($cookies),
        'message'=>'Meta API profile is valid.',
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)]);
} catch (Throwable $e) {
    http_response_code(200);
    ResponseFormatter::Respond(['error'=>$e->getMessage()]);
}
PHP_CODE;
file_put_contents($target, $php);
fwrite(STDERR, "[check-account-session] checkAccount uses explicit or saved token session/proxy context\n");
