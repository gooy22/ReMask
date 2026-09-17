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

dump_file($root . '/ajax/addAccount.php', 'addAccount.php');
dump_file($root . '/classes/AccountStoreFactory.php', 'AccountStoreFactory.php');
dump_file($root . '/classes/FbAccount.php', 'FbAccount.php');

// Also inspect likely serializer/store implementations and the ACCOUNTSFILENAME definition.
foreach (glob($root . '/classes/*Account*Store*.php') ?: [] as $path) {
    if (basename($path) === 'AccountStoreFactory.php') continue;
    dump_file($path, basename($path));
}
foreach (glob($root . '/classes/*Serializer*.php') ?: [] as $path) dump_file($path, basename($path));

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
