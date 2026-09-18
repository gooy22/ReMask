<?php
/**
 * v69 navigation freeze fix.
 *
 * The previous selection persistence helper used a document-wide MutationObserver
 * whose scan mutated DOM again (selection counters / bindings). On pages with
 * dynamic tables that could create a self-sustaining observer loop and pin the
 * browser main thread after navigation.
 *
 * Selection persistence is non-critical, so production now removes the helper
 * from all top-level pages. Keep an inert compatibility file only so stale browser
 * requests receive harmless JS instead of 404.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$scriptPath = $scriptsDir . '/selection-persistence.js';
$js = <<<'JS'
(() => {
  'use strict';
  // v69: intentionally disabled. Never install a global MutationObserver here.
  window.__remaskSelectionPersistenceLoaded = true;
  window.__remaskSelectionPersistenceDisabled = true;
})();
JS;
file_put_contents($scriptPath, $js);

$targets = ['index.php','workspace.php','launch.php','campaigns.php','adsets.php','accounts.php'];
$removed = 0;
foreach ($targets as $base) {
    $file = $root . '/' . $base;
    if (!is_file($file)) continue;
    $html = file_get_contents($file);
    if ($html === false) continue;

    $before = $html;
    $html = preg_replace(
        '#\s*<script\s+src=["\']scripts/selection-persistence\.js(?:\?[^"\']*)?["\']\s*></script>\s*#i',
        "\n",
        $html
    ) ?? $html;

    if ($html !== $before) {
        file_put_contents($file, $html);
        $removed++;
    }
}

// Force a fresh Accounts module as part of the navigation-stability release.
$accountsPage = $root . '/accounts.php';
if (is_file($accountsPage)) {
    $html = file_get_contents($accountsPage);
    if ($html !== false) {
        $html = preg_replace(
            '#scripts/accounts\.js(?:\?[^"\']*)?#',
            'scripts/accounts.js?v=20260918-accounts-v69',
            $html,
            1
        ) ?? $html;
        file_put_contents($accountsPage, $html);
    }
}

fwrite(STDERR, "[remask selection overlay] v69 global selection observer disabled; script tags removed={$removed}\n");
