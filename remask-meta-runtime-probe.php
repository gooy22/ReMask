<?php
/** Temporary runtime A/B probe. Never logs raw token/cookies/proxy credentials. */
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';
require_once __DIR__ . '/classes/FbAccount.php';
require_once __DIR__ . '/classes/RemaskProxy.php';

function probe_meta(FbAccount $acc, string $label, string $url, bool $bearer): void {
    $ch = curl_init($url);
    $headers = ['Accept: application/json'];
    if ($bearer) $headers[] = 'Authorization: Bearer ' . $acc->token;
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_TIMEOUT => 25,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_USERAGENT => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36',
    ];
    $cookies = method_exists($acc, 'getCurlCookies') ? trim((string)$acc->getCurlCookies()) : '';
    if ($cookies !== '') $opts[CURLOPT_COOKIE] = $cookies;
    if ($acc->proxy !== null) $acc->proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $curlError = curl_error($ch);
    $http = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $decoded = is_string($raw) ? json_decode($raw, true) : null;
    $err = is_array($decoded) && isset($decoded['error']) && is_array($decoded['error']) ? $decoded['error'] : [];
    $ok = $http >= 200 && $http < 300 && is_array($decoded) && !isset($decoded['error']) && isset($decoded['id']);
    $msg = trim((string)($err['message'] ?? ''));
    if (strlen($msg) > 120) $msg = substr($msg, 0, 120);
    $line = [
        'mode'=>$label,
        'ok'=>$ok,
        'http'=>$http,
        'curl_errno'=>$errno,
        'meta_type'=>(string)($err['type'] ?? ''),
        'meta_code'=>(int)($err['code'] ?? 0),
        'meta_subcode'=>(int)($err['error_subcode'] ?? 0),
        'fbtrace'=>(string)($err['fbtrace_id'] ?? ''),
        'message'=>$msg !== '' ? $msg : ($curlError !== '' ? substr($curlError,0,120) : ''),
    ];
    error_log('[meta-transport-probe] ' . json_encode($line, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE));
}

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts = (array)$store->deserialize();
    $acc = null;
    foreach ($accounts as $candidate) {
        if ($candidate instanceof FbAccount && trim((string)$candidate->token) !== '') { $acc = $candidate; break; }
    }
    if (!$acc instanceof FbAccount) {
        error_log('[meta-transport-probe] no saved account with token; skipped');
        exit(0);
    }
    $token = (string)$acc->token;
    $tokenHash = substr(hash('sha256', $token), 0, 12);
    $cookieHeader = method_exists($acc, 'getCurlCookies') ? trim((string)$acc->getCurlCookies()) : '';
    error_log('[meta-transport-probe] account=' . hash('sha256',(string)$acc->name) . ' token_sha=' . $tokenHash . ' cookies=' . ($cookieHeader !== '' ? 'yes' : 'no') . ' proxy=' . ($acc->proxy !== null ? 'yes' : 'no'));

    $encoded = rawurlencode($token);
    probe_meta($acc, 'v26-bearer', 'https://graph.facebook.com/v26.0/me?fields=id%2Cname', true);
    probe_meta($acc, 'v26-query', 'https://graph.facebook.com/v26.0/me?fields=id%2Cname&access_token=' . $encoded, false);
    probe_meta($acc, 'default-query', 'https://graph.facebook.com/me?fields=id%2Cname&access_token=' . $encoded, false);
    probe_meta($acc, 'v16-query', 'https://graph.facebook.com/v16.0/me?fields=id%2Cname&access_token=' . $encoded, false);
} catch (Throwable $e) {
    error_log('[meta-transport-probe] internal=' . get_class($e) . ':' . substr($e->getMessage(), 0, 160));
}
