<?php
$root = '/var/www/html';

function dump_matching_context(string $path, string $label, array $needles, int $before = 8, int $after = 16): void {
    if (!is_file($path)) return;
    $lines = file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) return;
    $printed = [];
    fwrite(STDERR, "[account-flow-diag] MATCHES {$label}\n");
    foreach ($lines as $i => $line) {
        $hit = false;
        foreach ($needles as $needle) if (stripos($line, $needle) !== false) { $hit = true; break; }
        if (!$hit) continue;
        for ($j=max(0,$i-$before); $j<=min(count($lines)-1,$i+$after); $j++) {
            if (isset($printed[$j])) continue;
            $safe = preg_replace('/([A-Za-z0-9_\-]{60,})/', '[LONG_VALUE_REDACTED]', $lines[$j]);
            fwrite(STDERR, sprintf("[account-flow-diag] %s:%d %s\n", $label, $j+1, $safe));
            $printed[$j]=true;
        }
    }
}

dump_matching_context($root . '/scripts/workspace.js', 'workspace.js', [
    'function apiJson', 'async function apiJson', 'loadInventory', 'state.inventory',
    'function render', 'inventory.profiles', 'inventory.businesses', 'inventory.ad_accounts',
    'metaProfileManager.php', 'prepareAddProfile'
], 10, 22);

dump_matching_context($root . '/ajax/metaHierarchy.php', 'metaHierarchy.php.final', [
    "'inventory'", 'inventory', 'profiles_synced', 'ad_accounts_count', 'remask_hierarchy_out'
], 8, 16);
