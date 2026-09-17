<?php
$needles = [
    'proxy_health', 'proxyHealth', 'proxy health', 'checkProxy', 'probeProxy', 'RemaskProxy',
    'CURLOPT_PROXY', 'CURLPROXY_', 'proxy_status', 'proxy_status_url',
    'cachedPreflight', 'peekCachedPreflight', 'cachedAsset', 'peekCachedAsset', 'invalidateProfileCache',
    'function preflight', 'function request', 'function get(', 'function ApiGet', 'MetaApiClient',
    "'ad_accounts'", "'businesses'", 'me/adaccounts', 'me/businesses', 'me/permissions',
    'Authorization: Bearer', 'AddToCurlOptions', 'cache->', 'MetaCache',
];
$files = glob('/var/www/html/classes/*.php') ?: [];
$files = array_merge($files, glob('/var/www/html/ajax/*.php') ?: []);
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

// workspace.js is intentionally compact/minified enough that logging a whole matched
// line can exceed Railway's log record size. Emit short bounded snippets instead.
$workspace = '/var/www/html/scripts/workspace.js';
$js = @file_get_contents($workspace);
if ($js !== false) {
    $jsNeedles = [
        'profileAttention',
        'profileReasons',
        'attentionReasons',
        'applySnapshot',
        'ТРЕБУЕТ ВНИМАНИЯ',
        'не синхронизирован',
        'ads_management_granted',
        'function render',
        'renderProfiles',
    ];
    foreach ($jsNeedles as $needle) {
        $offset = 0;
        $seen = 0;
        while (($pos = stripos($js, $needle, $offset)) !== false && $seen < 4) {
            $start = max(0, $pos - 450);
            $snippet = substr($js, $start, 1000);
            $snippet = str_replace(["\r", "\n"], ['\\r', '\\n'], $snippet);
            fwrite(STDERR, '[workspace-diag] ' . $needle . '#' . ($seen + 1) . ' @' . $pos . ' ' . $snippet . "\n");
            $offset = $pos + strlen($needle);
            $seen++;
        }
    }
}
