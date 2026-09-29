<?php
/**
 * Final Workspace readiness pass.
 *
 * Private BM/RK sync does not prove official Graph permissions. Therefore
 * missing/failed permission preflight is UNKNOWN, never a false negative.
 * This overlay intentionally runs after the Python Worker UI overlay so the
 * final shipped workspace.js keeps tri-state semantics.
 */
$hierarchyPath = '/var/www/html/ajax/metaHierarchy.php';
$workspacePath = '/var/www/html/scripts/workspace.js';

$hierarchy = file_get_contents($hierarchyPath);
if ($hierarchy === false) throw new RuntimeException('metaHierarchy.php not found');

$legacyBackendReplacements = [
    "            'ads_management_granted' => (bool)(\$preflight['ads_management_granted'] ?? false)," =>
        "            'ads_management_granted' => \$preflight === null ? null : (bool)(\$preflight['ads_management_granted'] ?? false),",
    "            'business_management_granted' => (bool)(\$preflight['business_management_granted'] ?? false)," =>
        "            'business_management_granted' => \$preflight === null ? null : (bool)(\$preflight['business_management_granted'] ?? false),",
];
$backendCounts = [];
foreach ($legacyBackendReplacements as $old => $new) {
    $count = 0;
    $hierarchy = str_replace($old, $new, $hierarchy, $count);
    if ($count > 0) $backendCounts[$old] = $count;
}

// The main Workspace sync overlay must have installed the canonical fields.
// Fail the build rather than silently shipping another false "нет proxy" state.
if (
    strpos($hierarchy, "'proxy_configured' => \$proxy !== null") === false
    || strpos($hierarchy, "'permissions_available' => !is_array(\$preflight)") === false
    || strpos($hierarchy, "'ads_management_granted' => !is_array(\$preflight)") === false
) {
    throw new RuntimeException('canonical Workspace readiness fields are missing');
}
file_put_contents($hierarchyPath, $hierarchy);

$js = file_get_contents($workspacePath);
if ($js === false) throw new RuntimeException('workspace.js not found');

// Only call ads_management missing after Meta explicitly returned a negative
// permission result. null/undefined means UNKNOWN and must not create a warning.
$jsCounts = [];
$patterns = [
    "!p.ads_management_granted" => "p.ads_management_granted===false",
    "!p.business_management_granted" => "p.business_management_granted===false",
];
foreach ($patterns as $old => $new) {
    $count = 0;
    $js = str_replace($old, $new, $js, $count);
    if ($count > 0) $jsCounts[$old] = $count;
}

if (
    strpos($js, "!p.ads_management_granted") !== false
    || strpos($js, "!p.business_management_granted") !== false
) {
    throw new RuntimeException('falsey permission warning remains in final workspace.js');
}

if (strpos($js, 'REMASK_META_PERMISSION_TRISTATE_FINAL_V2') === false) {
    $js .= "\n/* REMASK_META_PERMISSION_TRISTATE_FINAL_V2 */\n";
}

file_put_contents($workspacePath, $js);

fwrite(
    STDERR,
    '[meta-auth-state] final tri-state readiness enabled; backend='
    . json_encode($backendCounts, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
    . ' workspace='
    . json_encode($jsCounts, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES)
    . "\n"
);
