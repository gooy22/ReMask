<?php
$root = '/var/www/html';
$needles = ['metaProfileManager', 'addAccount.php', 'checkAccount.php', 'access_token', 'accessToken', 'token', 'proxy'];
foreach ([$root . '/scripts/accounts.js', $root . '/accounts.php', $root . '/workspace.php'] as $path) {
    if (!is_file($path)) continue;
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) continue;
    fwrite(STDERR, "[account-flow-diag] FILE " . basename($path) . "\n");
    $printed = [];
    foreach ($lines as $i => $line) {
        $hit = false;
        foreach ($needles as $needle) {
            if (stripos($line, $needle) !== false) { $hit = true; break; }
        }
        if (!$hit) continue;
        $from = max(0, $i - 3);
        $to = min(count($lines) - 1, $i + 5);
        for ($j = $from; $j <= $to; $j++) {
            if (isset($printed[$j])) continue;
            $safe = preg_replace('/([A-Za-z0-9_\-]{60,})/', '[LONG_VALUE_REDACTED]', $lines[$j]);
            fwrite(STDERR, sprintf("[account-flow-diag] %s:%d %s\n", basename($path), $j + 1, $safe));
            $printed[$j] = true;
        }
    }
}
