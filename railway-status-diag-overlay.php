<?php
$files = [
    '/var/www/html/scripts/workspace.js' => [
        'verification_status','NOT VERIFIED','NOT_VERIFIED','VERIFIED',
        'account_status','disable_reason','DISABLED','ACTIVE',
        'businesses','ad_accounts'
    ],
    '/var/www/html/classes/MetaAdsService.php' => [
        'function listBusinesses','verification_status',
        'function listAdAccounts','account_status','disable_reason',
        'function listBusinessAdAccounts'
    ],
    '/var/www/html/ajax/metaHierarchy.php' => [
        'hierarchy_profile_snapshot','verification_status',
        'account_status','disable_reason','business_id'
    ],
];
foreach ($files as $file => $needles) {
    $src = @file_get_contents($file);
    if (!is_string($src)) continue;
    foreach ($needles as $needle) {
        $offset = 0;
        $seen = 0;
        while (($pos = stripos($src, $needle, $offset)) !== false && $seen < 8) {
            $start = max(0, $pos - 700);
            $snippet = substr($src, $start, 1700);
            $snippet = str_replace(["\r","\n"], ['\\r','\\n'], $snippet);
            fwrite(STDERR, '[status-diag] ' . basename($file) . ' ' . $needle . '#' . ($seen + 1) . ' @' . $pos . ' ' . $snippet . "\n");
            $offset = $pos + strlen($needle);
            $seen++;
        }
    }
}
