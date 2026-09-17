<?php
$needles = ['proxy_health', 'proxyHealth', 'proxy health', 'checkProxy', 'probeProxy', 'RemaskProxy', 'CURLOPT_PROXY', 'CURLPROXY_', 'proxy_status', 'proxy_status_url'];
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
    fwrite(STDERR, "[proxy-diag] FILE {$file}\n");
    $printed = [];
    foreach ($hits as $hit) {
        $start = max(0, $hit - 14);
        $end = min(count($lines) - 1, $hit + 24);
        for ($i = $start; $i <= $end; $i++) {
            if (isset($printed[$i])) continue;
            $printed[$i] = true;
            fwrite(STDERR, sprintf("[proxy-diag] %s:%d %s\n", basename($file), $i + 1, $lines[$i]));
        }
    }
}