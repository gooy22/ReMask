<?php
$needles = [
    'proxy_health', 'proxyHealth', 'proxy health', 'checkProxy', 'probeProxy', 'RemaskProxy',
    'CURLOPT_PROXY', 'CURLPROXY_', 'proxy_status', 'proxy_status_url',
    'cachedPreflight', 'peekCachedPreflight', 'cachedAsset', 'peekCachedAsset', 'invalidateProfileCache',
    'function preflight', 'function request', 'function get(', 'function ApiGet', 'MetaApiClient',
    "'ad_accounts'", "'businesses'", 'me/adaccounts', 'me/businesses', 'me/permissions',
    'Authorization: Bearer', 'AddToCurlOptions', 'cache->', 'MetaCache',
    'profileAttention', 'profileReasons', 'attentionReasons', 'reason', 'statusText',
    'function render', 'renderProfiles', 'applySnapshot', 'ТРЕБУЕТ ВНИМАНИЯ', 'не синхронизирован'
];
$files = glob('/var/www/html/classes/*.php') ?: [];
$files = array_merge($files, glob('/var/www/html/ajax/*.php') ?: []);
$files = array_merge($files, glob('/var/www/html/scripts/*.js') ?: []);
foreach ($files as $file) {
    $text = @file_get_contents($file);
    if ($text === false) continue;
    $hits = [];
    $lines = preg_split('/\R/', $text) ?: [];
    foreach ($lines as $i => $line) {
        foreach ($needles as $needle) {
            if (stripos($line, $needle) !== false) { $hits[] = $i; break; }
        }
    }
    if (!$hits) continue;
    fwrite(STDERR, "[sync-diag] FILE {$file}\n");
    $printed = [];
    foreach ($hits as $hit) {
        $start = max(0, $hit - 18);
        $end = min(count($lines) - 1, $hit + 38);
        for ($i = $start; $i <= $end; $i++) {
            if (isset($printed[$i])) continue;
            $printed[$i] = true;
            fwrite(STDERR, sprintf("[sync-diag] %s:%d %s\n", basename($file), $i + 1, $lines[$i]));
        }
    }
}
