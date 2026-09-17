<?php
$needles = ['cachedPreflight', 'cachedAsset', 'ads_management', 'permissions', 'ad_accounts', 'serviceForAccountName', 'preflight'];
$files = glob('/var/www/html/classes/*.php') ?: [];
$files = array_merge($files, glob('/var/www/html/ajax/*.php') ?: []);
foreach ($files as $file) {
    $text = @file_get_contents($file);
    if ($text === false) continue;
    $matched = false;
    foreach ($needles as $needle) {
        if (stripos($text, $needle) !== false) { $matched = true; break; }
    }
    if (!$matched) continue;
    $lines = preg_split('/\R/', $text) ?: [];
    $hits = [];
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
        $end = min(count($lines) - 1, $hit + 28);
        for ($i = $start; $i <= $end; $i++) {
            if (isset($printed[$i])) continue;
            $printed[$i] = true;
            fwrite(STDERR, sprintf("[sync-diag] %s:%d %s\n", basename($file), $i + 1, $lines[$i]));
        }
    }
}
