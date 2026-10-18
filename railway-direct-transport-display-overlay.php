<?php
/** Display the explicitly selected transport without inventing a proxy. */
$hierarchyPath = '/var/www/html/ajax/metaHierarchy.php';
$workspacePath = '/var/www/html/scripts/workspace.js';
$hierarchy = file_get_contents($hierarchyPath);
// Successful private reads also support an explicitly approved direct profile.
// Always derive transport from the canonical account, never from success alone.
$transportHelper = <<<'DIRECT_TRANSPORT'
function hierarchy_cookie_transport(string $profile, array $row): array
{
    require_once __DIR__ . '/../classes/RemaskCookieProfile.php';
    $account = MetaEndpoint::accountForName($profile);
    $configured = ($account->proxy ?? null) !== null;
    $until = $configured ? 0 : RemaskCookieProfile::directTestUntil($profile, (string)($account->userId ?? ''));
    $row['proxy_configured'] = $configured;
    $row['direct_test_until'] = $until;
    $row['network_mode'] = $configured ? 'PROXY' : ($until ? 'DIRECT_TEST' : 'NOT_CONFIGURED');
    $row['transport'] = array_merge((array)($row['transport'] ?? []), [
        'proxy_configured' => $configured, 'network_mode' => $row['network_mode'],
        'direct_test_until' => $until, 'direct_fallback' => false,
    ]);
    if (!$configured) {
        $row['proxy'] = null;
        $row['proxy_health'] = null;
        $row['proxy_status'] = $until ? 'DIRECT_TEST' : 'NOT_CONFIGURED';
    }
    return $row;
}

DIRECT_TRANSPORT;
$signature = 'function hierarchy_profile_snapshot(string $profile, ?array $workspaceMeta = null): array';
$hierarchy = str_replace($signature, $transportHelper . $signature, $hierarchy, $transportHelperCount);
if ($transportHelperCount !== 1) throw new RuntimeException('Direct transport snapshot boundary missing');
$snapshotReturn = '    return hierarchy_asset_types_snapshot($snapshot);';
$snapshotTransport = <<<'DIRECT_SNAPSHOT'
    foreach ((array)($snapshot['profiles'] ?? []) as $i => $row) {
        if (is_array($row) && (string)($row['name'] ?? '') === $profile) {
            $snapshot['profiles'][$i] = hierarchy_cookie_transport($profile, $row);
        }
    }
    if (is_array($snapshot['profile'] ?? null)) $snapshot['profile'] = hierarchy_cookie_transport($profile, $snapshot['profile']);
DIRECT_SNAPSHOT;
$hierarchy = str_replace($snapshotReturn, $snapshotTransport . "\n" . $snapshotReturn, $hierarchy, $snapshotCount);
if ($snapshotCount !== 1) throw new RuntimeException('Direct transport snapshot return missing');
$responseAssignment = "        \$snapshot['profile'] = \$responseProfile;";
$hierarchy = str_replace($responseAssignment, "        \$responseProfile = hierarchy_cookie_transport(\$profile, \$responseProfile);\n        foreach ((array)(\$snapshot['profiles'] ?? []) as \$i => \$row) {\n            if (is_array(\$row) && (string)(\$row['name'] ?? '') === \$profile) \$snapshot['profiles'][\$i] = \$responseProfile;\n        }\n" . $responseAssignment, $hierarchy, $responseCount);
if ($responseCount !== 1) throw new RuntimeException('Direct transport sync response missing');
file_put_contents($hierarchyPath, $hierarchy);
$js = file_get_contents($workspacePath);
$js = str_replace("function proxyLabel(p){", "function proxyLabel(p){\n  if(remaskDirectProfile(p))return pill('БЕЗ ПРОКСИ','ok');", $js, $directLabelCount);
if ($directLabelCount !== 1) throw new RuntimeException('Direct transport proxy label missing');
$js = str_replace('p.proxy_configured===false ||', '(p.proxy_configured===false && !remaskDirectProfile(p)) ||', $js);
$js = str_replace("if(!row.proxy_configured)issues.push('нет proxy');", "if(!row.proxy_configured && !remaskDirectProfile(row))issues.push('нет proxy');", $js, $directReasonCount);
if ($directReasonCount !== 1) throw new RuntimeException('Direct transport attention reason missing');
file_put_contents($workspacePath, $js);
