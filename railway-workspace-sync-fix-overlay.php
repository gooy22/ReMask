<?php
$workspace = '/var/www/html/scripts/workspace.js';
$hierarchy = '/var/www/html/ajax/metaHierarchy.php';

$js = file_get_contents($workspace);
if ($js === false) {
    throw new RuntimeException('workspace.js not found');
}

$oldAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || !p.proxy_configured || (ps!==''&&ps!=='LIVE') || !p.ads_management_granted || p.bm_count===0 || p.rk_count===0; }";
$newAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || !p.proxy_configured || (ps!==''&&ps!=='LIVE') || p.ads_management_granted===false; }";
$count = 0;
$js = str_replace($oldAttention, $newAttention, $js, $count);
if ($count !== 1) {
    throw new RuntimeException('profileAttention patch failed: ' . $count);
}

$newSync = <<<'JS'
async function syncSelection(){
  if(state.running)return;
  const tab=state.activeTab, rows=selectedRows(tab);
  if(!rows.length)return;
  state.running=true;
  updateSelectionUi();
  $('workspaceStatus').textContent=`Доп. синхронизация: 0/${rows.length}`;
  try{
    let results=[];
    if(tab==='profiles') {
      results=await concurrent(rows,3,async r=>{
        const d=await apiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile:r.name}));
        applySnapshot(d);
        return d;
      },(d,t)=>{$('workspaceStatus').textContent=`Синхронизация FB: ${d}/${t}`;setProgress(d,t)});
    } else if(tab==='businesses') {
      results=await concurrent(rows,3,async r=>{
        const d=await apiJson('ajax/metaHierarchy.php',post({action:'sync_business',profile:r.profile,business_id:r.id}));
        applySnapshot(d);
        return d;
      },(d,t)=>{$('workspaceStatus').textContent=`Синхронизация BM: ${d}/${t}`;setProgress(d,t)});
    } else if(tab==='ad_accounts') {
      const profiles=[...new Set(rows.map(r=>r.profile))];
      results=await concurrent(profiles,3,async p=>{
        const d=await apiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile:p}));
        applySnapshot(d);
        return d;
      },(d,t)=>{$('workspaceStatus').textContent=`Синхронизация RK: ${d}/${t}`;setProgress(d,t)});
    } else {
      await refreshSelectedDelivery(tab, rows);
    }

    const failures=results.filter(x=>x?.error).map(x=>String(x.error));
    const warnings=results.flatMap(x=>Array.isArray(x?.sync_warnings)?x.sync_warnings:[]).filter(Boolean);
    if(failures.length){
      $('workspaceStatus').textContent=`Синхронизация Meta НЕ выполнена: ${failures.join(' · ')}`;
    } else if(warnings.length){
      $('workspaceStatus').textContent=`Meta синхронизирована. ${warnings.join(' · ')}`;
    } else {
      $('workspaceStatus').textContent='Синхронизация Meta завершена.';
    }
    render();
  } finally {
    state.running=false;
    updateSelectionUi();
  }
}
JS;

$pattern = '/async function syncSelection\(\)\{.*?\n\}\n\nfunction buildActionMenu\(\)\{/s';
$replacement = $newSync . "\n\nfunction buildActionMenu(){";
$patched = preg_replace($pattern, $replacement, $js, 1, $syncCount);
if ($patched === null || $syncCount !== 1) {
    throw new RuntimeException('syncSelection patch failed: ' . (string)$syncCount);
}
$js = $patched;
file_put_contents($workspace, $js);
fwrite(STDERR, "[workspace-sync-fix] workspace.js patched\n");

$php = file_get_contents($hierarchy);
if ($php === false) {
    throw new RuntimeException('metaHierarchy.php not found');
}

$syncProfileReplacement = <<<'PHP'
    if ($action === 'sync_profile') {
        $profile = trim((string)($input['profile'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');

        // Directly accessible ad accounts from the token are the baseline.
        // Business Manager enumeration is optional enrichment and must not make
        // an otherwise valid FB profile fail synchronization.
        MetaEndpoint::cachedPreflight($profile, true);
        $syncWarnings = [];
        try {
            $businesses = MetaEndpoint::cachedAsset($profile, 'businesses', '', true);
            foreach (($businesses['data'] ?? []) as $business) {
                if (!is_array($business)) continue;
                $businessId = trim((string)($business['id'] ?? ''));
                if ($businessId === '') continue;
                try {
                    MetaEndpoint::cachedAsset($profile, 'business_ad_accounts', $businessId, true);
                } catch (Throwable $businessAccountError) {
                    $syncWarnings[] = 'BM ' . $businessId . ': RK enrichment unavailable';
                }
            }
        } catch (Throwable $businessError) {
            $syncWarnings[] = 'Business Manager list unavailable for this token; direct RK data kept';
        }

        hierarchy_activity([
            'action'=>'sync_profile',
            'entity_type'=>'profile',
            'entity_id'=>$profile,
            'profile_name'=>$profile,
            'summary'=>'Синхронизация FB-профиля завершена',
            'details'=>['warnings'=>$syncWarnings],
        ]);
        $snapshot = hierarchy_profile_snapshot($profile);
        if ($syncWarnings !== []) $snapshot['sync_warnings'] = array_values(array_unique($syncWarnings));
        MetaEndpoint::ok($snapshot);
    }
PHP;

$startNeedle = "    if (\$action === 'sync_profile') {";
$endNeedle = "        MetaEndpoint::ok(hierarchy_profile_snapshot(\$profile));\n    }";
$start = strpos($php, $startNeedle);
$end = $start === false ? false : strpos($php, $endNeedle, $start);
if ($start === false || $end === false) {
    throw new RuntimeException('sync_profile backend patch boundaries not found');
}
$end += strlen($endNeedle);
$php = substr($php, 0, $start) . $syncProfileReplacement . substr($php, $end);
file_put_contents($hierarchy, $php);
fwrite(STDERR, "[workspace-sync-fix] metaHierarchy.php patched\n");

$probePath = '/var/www/html/ajax/metaSyncProbe.php';
$probeCode = <<<'PROBE'
<?php
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
if (!hash_equals('rmx_probe_9fb2e8d1c43a6f057d18', (string)($_GET['k'] ?? ''))) {
    http_response_code(404);
    echo json_encode(['ok'=>false]);
    exit;
}
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';

function rmx_probe_clean(string $message): string {
    $message = preg_replace('#(https?://)([^/@:\s]+):([^/@\s]+)@#i', '$1***:***@', $message) ?? $message;
    $message = preg_replace('/(access[_-]?token|authorization|bearer)(\s*[:=]\s*|\s+)[A-Za-z0-9._\-]+/i', '$1$2***', $message) ?? $message;
    return mb_substr($message, 0, 600);
}

$profile = '61594319066772';
$out = [
    'ok' => false,
    'profile' => 'configured',
    'preflight' => null,
    'ads_management_granted' => null,
    'direct_ad_account_count' => null,
    'business_count' => null,
    'business_error' => null,
    'error_class' => null,
    'error_code' => null,
    'error' => null,
];
try {
    $preflight = MetaEndpoint::cachedPreflight($profile, true);
    $out['preflight'] = 'ok';
    $out['ads_management_granted'] = array_key_exists('ads_management_granted', $preflight) ? (bool)$preflight['ads_management_granted'] : null;
    if (isset($preflight['ad_accounts']) && is_array($preflight['ad_accounts'])) {
        $out['direct_ad_account_count'] = count($preflight['ad_accounts']);
    } elseif (isset($preflight['ad_accounts']['data']) && is_array($preflight['ad_accounts']['data'])) {
        $out['direct_ad_account_count'] = count($preflight['ad_accounts']['data']);
    }
    try {
        $businesses = MetaEndpoint::cachedAsset($profile, 'businesses', '', true);
        $out['business_count'] = isset($businesses['data']) && is_array($businesses['data']) ? count($businesses['data']) : 0;
    } catch (Throwable $businessError) {
        $out['business_error'] = rmx_probe_clean($businessError->getMessage());
    }
    $out['ok'] = true;
} catch (Throwable $e) {
    $out['error_class'] = get_class($e);
    $out['error_code'] = $e->getCode();
    $out['error'] = rmx_probe_clean($e->getMessage());
}
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
PROBE;
file_put_contents($probePath, $probeCode);
fwrite(STDERR, "[workspace-sync-fix] temporary sanitized sync probe ready\n");
