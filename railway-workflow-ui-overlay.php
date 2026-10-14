<?php
declare(strict_types=1);
// Presentation only: keep the existing editors, serializers and launch handlers.
$root = getenv('REMASK_UI_TEST_ROOT') ?: '/var/www/html';
$asset = __DIR__ . '/railway-workflow-ui.js';
if (!is_file($asset)) { throw new RuntimeException('Workflow UI asset missing'); }
if (!is_dir($root . '/scripts')) { mkdir($root . '/scripts', 0755, true); }
copy($asset, $root . '/scripts/workflow-ui.js');
$version = substr(hash_file('sha256', $asset), 0, 16);
foreach (['creatives.php', 'launch.php'] as $page) {
    $path = $root . '/' . $page;
    $html = file_get_contents($path);
    if ($html === false || substr_count($html, '</body>') !== 1) {
        throw new RuntimeException('Workflow UI body anchor missing: ' . $page);
    }
    $html = preg_replace('#<script[^>]+data-remask-workflow-ui[^>]*></script>\s*#', '', $html);
    $html = str_replace('</body>', '<script src="scripts/workflow-ui.js?v=' . $version . '" data-remask-workflow-ui defer></script>' . "\n</body>", $html);
    file_put_contents($path, $html);
}
fwrite(STDERR, "[workflow-ui] Creatives and Launch presentation installed\n");
