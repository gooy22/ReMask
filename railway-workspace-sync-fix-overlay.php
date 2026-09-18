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
// REMASK_SYNC_STABILIZED_V1\nasync function syncSelection(){
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

fwrite(STDERR, "[workspace-sync-fix] clean sync stabilization ready; no diagnostic probe installed\n");
