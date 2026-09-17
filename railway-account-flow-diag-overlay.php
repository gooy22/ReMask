<?php
$root = '/var/www/html';

function dump_file(string $path, string $label): void {
    if (!is_file($path)) {
        fwrite(STDERR, "[account-flow-diag] MISSING {$label} {$path}\n");
        return;
    }
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) return;
    fwrite(STDERR, "[account-flow-diag] BEGIN {$label}\n");
    foreach ($lines as $i => $line) {
        $safe = preg_replace('/([A-Za-z0-9_\-]{60,})/', '[LONG_VALUE_REDACTED]', $line);
        fwrite(STDERR, sprintf("[account-flow-diag] %s:%d %s\n", $label, $i + 1, $safe));
    }
    fwrite(STDERR, "[account-flow-diag] END {$label}\n");
}

function dump_matching_context(string $path, string $label, array $needles): void {
    if (!is_file($path)) return;
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) return;
    $printed = [];
    fwrite(STDERR, "[account-flow-diag] MATCHES {$label}\n");
    foreach ($lines as $i => $line) {
        $hit = false;
        foreach ($needles as $needle) if (stripos($line, $needle) !== false) { $hit = true; break; }
        if (!$hit) continue;
        for ($j=max(0,$i-8); $j<=min(count($lines)-1,$i+14); $j++) {
            if (isset($printed[$j])) continue;
            $safe = preg_replace('/([A-Za-z0-9_\-]{60,})/', '[LONG_VALUE_REDACTED]', $lines[$j]);
            fwrite(STDERR, sprintf("[account-flow-diag] %s:%d %s\n", $label, $j+1, $safe));
            $printed[$j]=true;
        }
    }
}

dump_file($root . '/ajax/addAccount.php', 'addAccount.php');
dump_file($root . '/classes/AccountStoreFactory.php', 'AccountStoreFactory.php');
dump_file($root . '/classes/FbAccount.php', 'FbAccount.php');
foreach (glob($root . '/classes/*Account*Store*.php') ?: [] as $path) {
    if (basename($path) === 'AccountStoreFactory.php') continue;
    dump_file($path, basename($path));
}
foreach (glob($root . '/classes/*Serializer*.php') ?: [] as $path) dump_file($path, basename($path));

foreach (glob($root . '/scripts/*.js') ?: [] as $path) {
    dump_matching_context($path, basename($path), ['metaProfileManager.php','Добавить FB аккаунт','add profile','profileManager','profile_name','access_token']);
}

if (is_file($root . '/settings.php')) {
    $lines = file($root . '/settings.php', FILE_IGNORE_NEW_LINES);
    if (is_array($lines)) {
        fwrite(STDERR, "[account-flow-diag] BEGIN settings excerpts\n");
        foreach ($lines as $i => $line) {
            if (stripos($line, 'ACCOUNTSFILENAME') !== false || stripos($line, 'REMASK_ACCOUNTS_FILE') !== false || stripos($line, 'accounts.json') !== false) {
                fwrite(STDERR, sprintf("[account-flow-diag] settings.php:%d %s\n", $i + 1, $line));
            }
        }
        fwrite(STDERR, "[account-flow-diag] END settings excerpts\n");
    }
}
