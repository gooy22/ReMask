<?php
$workspace = '/var/www/html/scripts/workspace.js';
$hierarchy = '/var/www/html/ajax/metaHierarchy.php';

$servicePath = '/var/www/html/classes/MetaAdsService.php';

function rmx_sync_replace_method(string $source, string $methodName, string $replacement): string
{
    $needle = 'function ' . $methodName . '(';
    $start = strpos($source, $needle);
    if ($start === false) throw new RuntimeException($methodName . ' method not found');
    $brace = strpos($source, '{', $start);
    if ($brace === false) throw new RuntimeException($methodName . ' opening brace not found');
    $depth = 0;
    $end = null;
    $len = strlen($source);
    for ($i = $brace; $i < $len; $i++) {
        if ($source[$i] === '{') $depth++;
        elseif ($source[$i] === '}') {
            $depth--;
            if ($depth === 0) { $end = $i + 1; break; }
        }
    }
    if ($end === null) throw new RuntimeException($methodName . ' closing brace not found');
    return substr($source, 0, $start) . $replacement . substr($source, $end);
}


$service = file_get_contents($servicePath);
if ($service === false) throw new RuntimeException('MetaAdsService.php not found');

$businessAccountsMethod = <<<'PHP_METHOD'
// REMASK_BM_OWNED_CLIENT_V1
function listBusinessAdAccounts(string $businessId, int $limit = 0, bool $includeClient = true): array
    {
        $businessId = trim($businessId);
        if ($businessId === '' || !preg_match('/^\\d+$/', $businessId)) {
            throw new InvalidArgumentException('A numeric Business Manager ID is required.');
        }

        $bounded = $limit > 0;
        $limit = $bounded ? max(1, $limit) : 0;
        $edges = $includeClient ? ['owned_ad_accounts', 'client_ad_accounts'] : ['owned_ad_accounts'];
        $items = [];
        $seen = [];
        $edgeWarnings = [];

        foreach ($edges as $edge) {
            $remaining = $bounded ? ($limit - count($items)) : 0;
            if ($bounded && $remaining <= 0) break;

            try {
                $page = $this->listPagedEdge("{$businessId}/{$edge}", [
                    'fields' => 'id,name,account_status,currency,amount_spent,balance,business{id,name},business_name,timezone_name,spend_cap,funding_source,funding_source_details',
                ], $remaining);
            } catch (Throwable $edgeError) {
                $edgeWarnings[] = [
                    'edge' => $edge,
                    'error_class' => get_class($edgeError),
                ];
                continue;
            }

            foreach ((array)($page['data'] ?? []) as $item) {
                if (!is_array($item)) continue;
                $id = (string)($item['id'] ?? '');
                if ($id === '' || isset($seen[$id])) continue;
                $item['_business_edge'] = $edge;
                $seen[$id] = true;
                $items[] = $item;
                if ($bounded && count($items) >= $limit) break 2;
            }
        }

        $result = ['data' => $items];
        if ($edgeWarnings !== []) $result['_edge_warnings'] = $edgeWarnings;
        return $result;
    }
PHP_METHOD;

$service = rmx_sync_replace_method($service, 'listBusinessAdAccounts', $businessAccountsMethod);
file_put_contents($servicePath, $service);
fwrite(STDERR, "[workspace-sync-fix] BM owned+client RK enrichment patched with per-edge fallback\n");

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
 // REMASK_SYNC_STABILIZED_V2
async function syncSelection(){
  if(state.running)return;
  const tab=state.activeTab, rows=selectedRows(tab);
  if(!rows.length)return;

  state.running=true;
  updateSelectionUi();
  $('workspaceStatus').textContent=`Доп. синхронизация: 0/${rows.length}`;

  const errorText=(e)=>{
    if(e&&typeof e==='object'){
      if(e.message)return String(e.message);
      if(e.error)return String(e.error);
    }
    return String(e||'Unknown sync error');
  };

  const syncProfileSafe=async(profile)=>{
    try{
      const d=await apiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile}));
      applySnapshot(d);
      return d;
    }catch(e){
      return {error:errorText(e),profile};
    }
  };

  const syncBusinessSafe=async(row)=>{
    try{
      const d=await apiJson('ajax/metaHierarchy.php',post({action:'sync_business',profile:row.profile,business_id:row.id}));
      applySnapshot(d);
      return d;
    }catch(e){
      return {error:errorText(e),profile:row.profile,business_id:row.id};
    }
  };

  try{
    let results=[];
    if(tab==='profiles'){
      results=await concurrent(
        rows,
        3,
        r=>syncProfileSafe(r.name),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация FB: ${done}/${total}`;setProgress(done,total)}
      );
    }else if(tab==='businesses'){
      results=await concurrent(
        rows,
        3,
        r=>syncBusinessSafe(r),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация BM: ${done}/${total}`;setProgress(done,total)}
      );
    }else if(tab==='ad_accounts'){
      const profiles=[...new Set(rows.map(r=>r.profile).filter(Boolean))];
      results=await concurrent(
        profiles,
        3,
        p=>syncProfileSafe(p),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация RK: ${done}/${total}`;setProgress(done,total)}
      );
    }else{
      try{
        await refreshSelectedDelivery(tab,rows);
      }catch(e){
        results=[{error:errorText(e)}];
      }
    }

    const failures=results
      .filter(x=>x&&x.error)
      .map(x=>[x.profile,x.business_id,x.error].filter(Boolean).join(': '));
    const warnings=results
      .flatMap(x=>Array.isArray(x?.sync_warnings)?x.sync_warnings:[])
      .filter(Boolean);

    if(failures.length){
      $('workspaceStatus').textContent=`Синхронизация Meta частично/полностью не выполнена: ${failures.join(' · ')}`;
    }else if(warnings.length){
      $('workspaceStatus').textContent=`Meta синхронизирована. ${warnings.join(' · ')}`;
    }else{
      $('workspaceStatus').textContent='Синхронизация Meta завершена.';
    }
    render();
  }finally{
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
                    $businessAccounts = MetaEndpoint::cachedAsset($profile, 'business_ad_accounts', $businessId, true);
                    foreach ((array)($businessAccounts['_edge_warnings'] ?? []) as $edgeWarning) {
                        if (!is_array($edgeWarning)) continue;
                        $edgeName = trim((string)($edgeWarning['edge'] ?? 'business_ad_accounts'));
                        $syncWarnings[] = 'BM ' . $businessId . ': ' . $edgeName . ' unavailable';
                    }
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
            'details'=>['sync_source'=>'direct_ad_accounts_with_optional_business_enrichment','warnings'=>$syncWarnings],
        ]);
        $snapshot = hierarchy_profile_snapshot($profile);
        $snapshot['sync_source'] = 'direct_ad_accounts_with_optional_business_enrichment';
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
