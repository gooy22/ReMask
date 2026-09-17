<?php
/**
 * Keep Workspace permission state honest when Meta preflight never completed.
 * An absent preflight means UNKNOWN, not "permission denied" and not "zero assets".
 */
$hierarchyPath = '/var/www/html/ajax/metaHierarchy.php';
$workspacePath = '/var/www/html/scripts/workspace.js';

$hierarchy = file_get_contents($hierarchyPath);
if ($hierarchy === false) throw new RuntimeException('metaHierarchy.php not found');

$replacements = [
    "            'ads_management_granted' => (bool)(\$preflight['ads_management_granted'] ?? false)," =>
        "            'ads_management_granted' => \$preflight === null ? null : (bool)(\$preflight['ads_management_granted'] ?? false),",
    "            'business_management_granted' => (bool)(\$preflight['business_management_granted'] ?? false)," =>
        "            'business_management_granted' => \$preflight === null ? null : (bool)(\$preflight['business_management_granted'] ?? false),",
];
foreach ($replacements as $old => $new) {
    $count = 0;
    $hierarchy = str_replace($old, $new, $hierarchy, $count);
    if ($count !== 1) throw new RuntimeException('Meta permission tri-state patch failed: ' . $count);
}
file_put_contents($hierarchyPath, $hierarchy);

$js = file_get_contents($workspacePath);
if ($js === false) throw new RuntimeException('workspace.js not found');

// Only call a permission missing after Meta actually returned a permission result.
$jsCounts = [];
$patterns = [
    "if(!p.ads_management_granted)" => "if(p.ads_management_granted===false)",
    "if (!p.ads_management_granted)" => "if (p.ads_management_granted===false)",
    "if(p.bm_count===0)" => "if(p.synced&&p.bm_count===0)",
    "if (p.bm_count===0)" => "if (p.synced&&p.bm_count===0)",
    "if(p.rk_count===0)" => "if(p.synced&&p.rk_count===0)",
    "if (p.rk_count===0)" => "if (p.synced&&p.rk_count===0)",
];
foreach ($patterns as $old => $new) {
    $count = 0;
    $js = str_replace($old, $new, $js, $count);
    if ($count > 0) $jsCounts[$old] = $count;
}
file_put_contents($workspacePath, $js);

fwrite(STDERR, '[meta-auth-state] tri-state permissions enabled; workspace replacements=' . json_encode($jsCounts, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES) . "\n");
