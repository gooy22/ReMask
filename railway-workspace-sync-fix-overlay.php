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

$listAdAccountsMethod = <<<'PHP_METHOD'
// REMASK_DIRECT_RK_FUNDING_V3
function listAdAccounts(int $limit = 0): array
    {
        $fieldSets = [
            ['name'=>'funding_full','fields'=>'id,account_id,name,account_status,disable_reason,currency,balance,amount_spent,spend_cap,business{id,name},business_name,timezone_name,is_prepay_account,funding_source,funding_source_details,expired_funding_source_details'],
            ['name'=>'baseline_full','fields'=>'id,account_id,name,account_status,disable_reason,currency,balance,amount_spent,spend_cap,business{id,name},business_name,timezone_name'],
            ['name'=>'baseline_compat','fields'=>'id,account_id,name,account_status,currency,balance,amount_spent,spend_cap,business{id,name},timezone_name'],
            ['name'=>'identity_minimal','fields'=>'id,account_id,name,account_status,currency'],
            ['name'=>'id_only','fields'=>'id,account_id,name'],
        ];

        $errors = [];
        foreach ($fieldSets as $index => $set) {
            try {
                $result = $this->listPagedEdge('me/adaccounts', ['fields'=>$set['fields']], $limit);
                foreach ((array)($result['data'] ?? []) as $i => $item) {
                    if (!is_array($item)) continue;
                    $result['data'][$i]['_funding_metadata_loaded'] = $index === 0;
                }
                if ($index > 0) {
                    $result['_adaccounts_fallback'] = [
                        'field_set'=>$set['name'],
                        'failed_attempts'=>$errors,
                    ];
                }
                return $result;
            } catch (Throwable $error) {
                $message = preg_replace(
                    '/access_token=[^&\\s]+/i',
                    'access_token=[redacted]',
                    (string)$error->getMessage()
                );
                $errors[] = [
                    'field_set'=>$set['name'],
                    'error_class'=>get_class($error),
                    'message'=>mb_substr((string)$message,0,500),
                ];
            }
        }

        $last = end($errors);
        throw new RuntimeException(
            'me/adaccounts failed after compatibility fallbacks: ' .
            (string)($last['message'] ?? 'unknown Meta error')
        );
    }
PHP_METHOD;

$businessAccountsMethod = <<<'PHP_METHOD'
// REMASK_BM_OWNED_CLIENT_V2
function listBusinessAdAccounts(string $businessId, int $limit = 0, bool $includeClient = true): array
    {
        $businessId = trim($businessId);
        if ($businessId === '' || !preg_match('/^\\d+$/', $businessId)) {
            throw new InvalidArgumentException('A numeric Business Manager ID is required.');
        }

        $bounded = $limit > 0;
        $limit = $bounded ? max(1, $limit) : 0;
        $edges = $includeClient ? ['owned_ad_accounts', 'client_ad_accounts'] : ['owned_ad_accounts'];
        $baseFields = 'id,account_id,name,account_status,disable_reason,currency,amount_spent,balance,business{id,name},business_name,timezone_name,spend_cap';
        $fundingFields = $baseFields . ',funding_source,funding_source_details';
        $items = [];
        $seen = [];
        $edgeWarnings = [];

        foreach ($edges as $edge) {
            $remaining = $bounded ? ($limit - count($items)) : 0;
            if ($bounded && $remaining <= 0) break;

            try {
                try {
                    $page = $this->listPagedEdge("{$businessId}/{$edge}", ['fields' => $fundingFields], $remaining);
                } catch (Throwable $fundingEdgeError) {
                    // Preserve BM -> RK mapping even when payment metadata is restricted.
                    $page = $this->listPagedEdge("{$businessId}/{$edge}", ['fields' => $baseFields], $remaining);
                    $edgeWarnings[] = [
                        'edge' => $edge,
                        'kind' => 'funding_metadata_unavailable',
                        'error_class' => get_class($fundingEdgeError),
                    ];
                }
            } catch (Throwable $edgeError) {
                $edgeWarnings[] = [
                    'edge' => $edge,
                    'kind' => 'edge_unavailable',
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

$service = rmx_sync_replace_method($service, 'listAdAccounts', $listAdAccountsMethod);
$service = rmx_sync_replace_method($service, 'listBusinessAdAccounts', $businessAccountsMethod);
file_put_contents($servicePath, $service);
fwrite(STDERR, "[workspace-sync-fix] BM owned+client RK enrichment patched with per-edge fallback\n");

$js = file_get_contents($workspace);
if ($js === false) {
    throw new RuntimeException('workspace.js not found');
}

$oldApplySnapshot = "function applySnapshot(s){ if(!s?.profile)return; const p=s.profile.name; state.inventory.profiles=state.inventory.profiles.filter(x=>x.name!==p).concat([s.profile]); state.inventory.businesses=state.inventory.businesses.filter(x=>x.profile!==p).concat(s.businesses||[]); state.inventory.ad_accounts=state.inventory.ad_accounts.filter(x=>x.profile!==p).concat(s.ad_accounts||[]); }";
$newApplySnapshot = <<<'JS'
function applySnapshot(s){
  if(!s || typeof s!=='object')return false;
  // Inconclusive live responses are diagnostics, never replacement inventory.
  if(s.sync_complete===false)return false;
  const profileRow=(s.profile && typeof s.profile==='object')
    ? s.profile
    : (Array.isArray(s.profiles)
      ? s.profiles.find(x=>x && String(x.name||x.profile||'').trim()===String(s.profile_name||'').trim())
        || (s.profiles.length===1?s.profiles[0]:null)
      : null);
  const p=String(
    profileRow?.name
    || profileRow?.profile
    || s.profile_name
    || ''
  ).trim();
  if(!p)return false;

  const existingProfile=state.inventory.profiles.find(
    x=>x && String(x.name||x.profile||'').trim()===p
  ) || null;

  // A sync response is partial by design. Never replace the whole profile row
  // with it: that used to drop the persisted proxy descriptor from client
  // state and render "proxy://" immediately after a successful sync.
  const normalizedProfile=profileRow && typeof profileRow==='object'
    ? {
        ...(existingProfile&&typeof existingProfile==='object'?existingProfile:{}),
        ...profileRow,
        name:String(profileRow.name||profileRow.profile||p),
      }
    : {
        ...(existingProfile&&typeof existingProfile==='object'?existingProfile:{}),
        name:p,
        synced:true,
      };

  // If the worker reached private inventory, proxy_configured=true is
  // authoritative for this sync, but keep the existing proxy descriptor and
  // proxy health payload unless the response explicitly supplied replacements.
  if(existingProfile && normalizedProfile.proxy_configured===true){
    if(normalizedProfile.proxy==null && existingProfile.proxy!=null){
      normalizedProfile.proxy=existingProfile.proxy;
    }
    if(normalizedProfile.proxy_health==null && existingProfile.proxy_health!=null){
      normalizedProfile.proxy_health=existingProfile.proxy_health;
    }
  }

  const personalScope=String(s.personal_scope_id||normalizedProfile.personal_scope_id||normalizedProfile.user_id||'');
  const businesses=(Array.isArray(s.businesses)?s.businesses:[])
    .filter(x=>x&&typeof x==='object')
    .filter(x=>String(x.id||'')!==personalScope&&x.is_personal!==true)
    .map(x=>({...x,profile:String(x.profile||p)}));
  const accounts=(Array.isArray(s.ad_accounts)?s.ad_accounts:[])
    .filter(x=>x&&typeof x==='object')
    .filter(x=>/^\d{5,30}$/.test(String(x.business_id||''))&&String(x.business_id)!==personalScope&&x.is_personal!==true)
    .map(x=>({...x,profile:String(x.profile||p)}));

  state.inventory.profiles=state.inventory.profiles
    .filter(x=>String(x.name||x.profile||'')!==p)
    .concat([normalizedProfile]);
  // Session updates contain a profile row only. Omitted collections are not
  // an authoritative empty Meta inventory.
  if(Array.isArray(s.businesses)){
    state.inventory.businesses=state.inventory.businesses
      .filter(x=>String(x.profile||'')!==p)
      .concat(businesses);
  }
  if(Array.isArray(s.ad_accounts)){
    state.inventory.ad_accounts=state.inventory.ad_accounts
      .filter(x=>String(x.profile||'')!==p)
      .concat(accounts);
  }
  return true;
}
JS;
$js = str_replace($oldApplySnapshot, $newApplySnapshot, $js, $applySnapshotPatchCount);
if ($applySnapshotPatchCount !== 1) {
    throw new RuntimeException('applySnapshot contract patch failed: ' . $applySnapshotPatchCount);
}

$oldAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || !p.proxy_configured || (ps!==''&&ps!=='LIVE') || !p.ads_management_granted || p.bm_count===0 || p.rk_count===0; }";
$newAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || p.proxy_configured===false || (p.proxy_configured===true && ps!==''&&ps!=='LIVE'); }";
$count = 0;
$js = str_replace($oldAttention, $newAttention, $js, $count);
if ($count !== 1) {
    throw new RuntimeException('profileAttention patch failed: ' . $count);
}

$permissionConditionPatterns = [];
foreach ($permissionConditionPatterns as $permissionOld => $permissionNew) {
    $permissionCount = 0;
    $js = str_replace($permissionOld, $permissionNew, $js, $permissionCount);
}

$oldAccountStatus = "function accountStatus(a){ const s=Number(a.account_status||0); return s===1?pill('ACTIVE','ok'):s===2?pill('DISABLED','bad'):s===3?pill('UNSETTLED','warn'):pill(String(a.account_status||'UNKNOWN'),'warn'); }";
$newAccountStatus = <<<'JS'
function accountStatus(a){
  /* REMASK_STATUS_TRUTH_V1 */
  if(a && a._provisioned_only){
    return pill('CREATED','ok')+'<div class="sub">Worker confirmed · awaiting Meta inventory sync</div>';
  }
  const s=Number(a.account_status||0);
  const reason=String(a.disable_reason==null?'':a.disable_reason).trim();
  if(s===1)return pill('ACTIVE','ok');
  if(s===2){
    const detail=reason&&reason!=='0'
      ? '<div class="sub">Meta disable_reason: '+esc(reason)+'</div>'
      : '<div class="sub">Meta account_status=2</div>';
    return pill('META: DISABLED','bad')+detail;
  }
  if(s===3)return pill('UNSETTLED','warn');
  return pill('META STATUS '+String(a.account_status||'UNKNOWN'),'warn');
}
JS;
$js = str_replace($oldAccountStatus, $newAccountStatus, $js, $accountStatusPatchCount);
if ($accountStatusPatchCount !== 1) {
    throw new RuntimeException('accountStatus patch failed: ' . $accountStatusPatchCount);
}

$oldBusinessRender = "const pp=row.primary_page||{}; const status=businessAttention(row)?pill('НЕТ RK','warn'):pill(row.verification_status||'READY', row.verification_status==='verified'?'ok':'blue');";
$newBusinessRender = <<<'JS'
const pp=row.primary_page||{};
    const verification=String(row.verification_status||'').trim().toLowerCase();
    const status=verification==='verified'
      ? pill('VERIFIED','ok')
      : verification==='not_verified'
        ? pill('NOT VERIFIED','blue')+'<div class="sub">Business Verification · не блокировка</div>'
        : verification
          ? pill(verification.toUpperCase(),'blue')
          : pill('VERIFICATION UNKNOWN','warn');
JS;
$js = str_replace($oldBusinessRender, $newBusinessRender, $js, $businessRenderPatchCount);
if ($businessRenderPatchCount !== 1) {
    throw new RuntimeException('business verification render patch failed: ' . $businessRenderPatchCount);
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

  // REMASK_SYNC_ERROR_CLASSIFIER_V1
  const errorText=(e)=>{
    const pick=(value,depth=0)=>{
      if(depth>4||value==null)return '';
      if(typeof value==='string'||typeof value==='number'||typeof value==='boolean'){
        return String(value);
      }
      if(typeof value==='object'){
        for(const key of ['message','detail','error_description','error','reason']){
          if(Object.prototype.hasOwnProperty.call(value,key)){
            const nested=pick(value[key],depth+1);
            if(nested)return nested;
          }
        }
        try{
          const encoded=JSON.stringify(value);
          if(encoded&&encoded!=='{}')return encoded;
        }catch(_){}
      }
      return '';
    };
    const raw=pick(e)||'Unknown sync error';
    return raw.replace(/access_token=[^&\\s]+/ig,'access_token=[redacted]');
  };
  const classifySyncError=(e)=>{
    const message=errorText(e);
    const s=message.toLowerCase();
    let kind='PRIVATE_SYNC';
    if(/authentication required|http 401|status 401/.test(s))kind='REMASK_AUTH_EXPIRED';
    else if(/checkpoint_required/.test(s))kind='CHECKPOINT_REQUIRED';
    else if(/two_factor_required/.test(s))kind='TWO_FACTOR_REQUIRED';
    else if(/session_expired/.test(s))kind='SESSION_EXPIRED';
    else if(/rate.?limit|too many|code[^0-9]*(4|17|32|613)\\b/.test(s))kind='RATE_LIMIT';
    else if(/\\b407\\b|proxy authentication|proxy auth/.test(s))kind='PROXY_AUTH';
    else if(/live_inventory_browser_open_timeout|browser slot waited|profile_browser_lock_timeout|browser_open timeout/.test(s))kind='BROWSER_BUSY';
    else if(/business discovery timed out|live_inventory_timeout:business_discovery/.test(s))kind='BM_DISCOVERY_TIMEOUT';
    else if(/load failed|failed to fetch|network request failed/.test(s))kind='CLIENT_TRANSPORT';
    else if(/transport error|curl|could not resolve|connection timed out|connection refused|ssl connect/.test(s))kind='TRANSPORT';
    else if(/access token.*(invalid|expired)|token.*(invalid|expired)|session.*expired|code[^0-9]*190\\b|\\(#190\\)/.test(s))kind='TOKEN_INVALID';
    else if(/oauth.*code[^0-9]*1\\b|code=1\\b|meta graph .* http 400 code=1\\b/.test(s))kind='META_REQUEST';
    else if(/ads_management|ads_read|business_management|permission|permissions|not authorized|code[^0-9]*(10|200)\\b/.test(s))kind='PERMISSION';
    else if(/meta_sync_preflight_failed/.test(s))kind='PRIVATE_PREFLIGHT';
    else if(/http 5\\d\\d|temporar|transient/.test(s))kind='META_TEMPORARY';
    return {kind,message};
  };

  const withTimeout=async(promise,ms=70000)=>{
    let timer=null;
    try{
      return await Promise.race([
        promise,
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Private Meta sync timeout after '+ms+'ms')),ms);})
      ]);
    }finally{
      if(timer!==null)clearTimeout(timer);
    }
  };

  // REMASK_SYNC_CSRF_SAFE_TRANSPORT_V1
  // Use the native Workspace request helper for every sync request. It carries
  // the current auth/CSRF contract. If the mobile browser drops the long
  // request, request_id reconciliation below reads the server-side result
  // instead of bypassing CSRF with a second custom fetch implementation.
  const syncApiJson=async(url,body)=>apiJson(url,body);

  // REMASK_SYNC_RESULT_RECONCILIATION_V1
  const syncRequestId=()=>{
    try{
      if(globalThis.crypto&&typeof globalThis.crypto.randomUUID==='function'){
        return globalThis.crypto.randomUUID().replace(/-/g,'_');
      }
    }catch(_){}
    return 'sync_'+Date.now()+'_'+Math.random().toString(36).slice(2,12);
  };

  const reconcileDroppedSync=async(profile,requestId)=>{
    for(let attempt=0;attempt<12;attempt++){
      if(attempt>0)await new Promise(resolve=>setTimeout(resolve,1250));
      try{
        const d=await syncApiJson(
          'ajax/metaHierarchy.php',
          post({action:'sync_result',profile,request_id:requestId})
        );
        if(!d||d.pending===true)continue;
        if(d.sync_complete===true){
          const applied=applySnapshot(d);
          if(!applied){
            return {
              error:'Live Meta sync finished but reconciled snapshot was not applied',
              error_kind:'SNAPSHOT_NOT_APPLIED',
              profile
            };
          }
          return d;
        }
        return {
          error:String(d.sync_error||'Private Meta sync failed after client reconnect'),
          error_kind:String(d.sync_error_kind||'PRIVATE_SYNC'),
          profile
        };
      }catch(_){
        // The status request itself is intentionally retried; it is read-only
        // and does not start another Meta sync.
      }
    }
    return null;
  };

  const finishSyncResponse=(d,profile,business_id='')=>{
    // REMASK_FAILED_SYNC_DOES_NOT_MUTATE_WORKSPACE_V1
    // A partial/failed browser result is diagnostic only. Keep the last
    // confirmed Workspace state untouched unless the full BM/RK/FP contract
    // completed successfully.
    if(d && d.sync_complete===false){
      return {
        error:String(d.sync_error||'Private Business Suite inventory was inconclusive'),
        error_kind:String(d.sync_error_kind||'PRIVATE_INCONCLUSIVE'),
        profile,
        business_id
      };
    }
    const applied=applySnapshot(d);
    if(d && d.sync_complete===true && !applied){
      return {
        error:'Live Meta sync returned success but Workspace snapshot was not applied',
        error_kind:'SNAPSHOT_NOT_APPLIED',
        profile,
        business_id
      };
    }
    return d;
  };

  const syncProfileSafe=async(profile)=>{
    const requestId=syncRequestId();
    try{
      const d=await withTimeout(syncApiJson(
        'ajax/metaHierarchy.php',
        post({action:'sync_profile',profile,request_id:requestId})
      ));
      return finishSyncResponse(d,profile);
    }catch(e){
      const x=classifySyncError(e);
      if(
        x.kind==='CLIENT_TRANSPORT'
        || /load failed|failed to fetch|network request failed|http 403|csrf/i.test(x.message)
      ){
        const reconciled=await reconcileDroppedSync(profile,requestId);
        if(reconciled)return reconciled;
      }
      return {error:x.message,error_kind:x.kind,profile};
    }
  };

  const syncBusinessSafe=async(row)=>{
    const requestId=syncRequestId();
    try{
      const d=await withTimeout(syncApiJson(
        'ajax/metaHierarchy.php',
        post({
          action:'sync_profile',
          profile:row.profile,
          business_id:row.id,
          request_id:requestId
        })
      ));
      return finishSyncResponse(d,row.profile,row.id);
    }catch(e){
      const x=classifySyncError(e);
      if(
        x.kind==='CLIENT_TRANSPORT'
        || /load failed|failed to fetch|network request failed|http 403|csrf/i.test(x.message)
      ){
        const reconciled=await reconcileDroppedSync(row.profile,requestId);
        if(reconciled)return reconciled;
      }
      return {
        error:x.message,
        error_kind:x.kind,
        profile:row.profile,
        business_id:row.id
      };
    }
  };

  // REMASK_SYNC_BROWSER_SERIAL_V1
  // Production is intentionally one Chromium lease in a 1 GB container.
  // Match client request concurrency to that capacity instead of queueing
  // three long HTTP requests behind one browser semaphore.
  const syncConcurrency=1;

  try{
    let results=[];
    if(tab==='profiles'){
      results=await concurrent(
        rows,
        syncConcurrency,
        r=>syncProfileSafe(r.name),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация FB: ${done}/${total}`;setProgress(done,total)}
      );
    }else if(tab==='businesses'){
      // REMASK_BUSINESS_TAB_SELECTED_SCOPE_V2
      // Selected BM rows are explicit targets. The server merges live
      // siblings from the confirmed snapshot after checking this exact BM.
      results=await concurrent(
        rows,
        syncConcurrency,
        row=>syncBusinessSafe(row),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация BM: ${done}/${total}`;setProgress(done,total)}
      );
    }else if(tab==='ad_accounts'){
      const businesses=[...new Map(rows.filter(r=>r.profile && r.business_id)
        .map(r=>[`${r.profile}:${r.business_id}`,{profile:r.profile,id:r.business_id}])).values()];
      results=await concurrent(
        businesses,
        syncConcurrency,
        row=>syncBusinessSafe(row),
        (done,total)=>{$('workspaceStatus').textContent=`Синхронизация RK: ${done}/${total}`;setProgress(done,total)}
      );
    }else{
      try{
        await refreshSelectedDelivery(tab,rows);
      }catch(e){
        const x=classifySyncError(e);
        results=[{error:x.message,error_kind:x.kind}];
      }
    }

    const failures=results
      .filter(x=>x&&x.error)
      .map(x=>[x.profile,x.business_id,`[${x.error_kind||'META_API'}]`,x.error].filter(Boolean).join(': '));
    const warnings=[...new Set(
      results
        .flatMap(x=>Array.isArray(x?.sync_warnings)?x.sync_warnings:[])
        .map(x=>String(x||'').trim())
        .filter(Boolean)
    )];

    if(failures.length){
      $('workspaceStatus').textContent=`Синхронизация Meta частично/полностью не выполнена: ${failures.join(' · ')}`;
    }else if(warnings.length){
      $('workspaceStatus').textContent=`Синхронизация выполнена частично. ${warnings.join(' · ')}`;
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

$oldSelectionUi = <<<'JS'
function updateSelectionUi(){
  const n=state.selected[state.activeTab].size; $('workspaceSelected').textContent=`Выбрано: ${n}`; $('workspaceActions').disabled=n===0||state.running; $('syncSelected').disabled=n===0||state.running; if($('loadDelivery'))$('loadDelivery').style.display=deliveryTabs.has(state.activeTab)?'inline-flex':'none'; buildActionMenu(); updateAssetsVisibility();
}
JS;
$newSelectionUi = <<<'JS'
function updateSelectionUi(){
  const n=state.selected[state.activeTab].size;
  $('workspaceSelected').textContent=`Выбрано: ${n}`;
  // A Meta sync must never freeze navigation/actions. Lock only the sync trigger.
  $('workspaceActions').disabled=n===0;
  $('syncSelected').disabled=n===0||state.running;
  if($('loadDelivery'))$('loadDelivery').style.display=deliveryTabs.has(state.activeTab)?'inline-flex':'none';
  buildActionMenu();
  updateAssetsVisibility();
}
JS;
$js = str_replace($oldSelectionUi, $newSelectionUi, $js, $selectionUiCount);
if ($selectionUiCount !== 1) {
    throw new RuntimeException('responsive selection UI patch failed: ' . $selectionUiCount);
}

file_put_contents($workspace, $js);
fwrite(STDERR, "[workspace-sync-fix] workspace.js patched; sync no longer blocks whole Workspace; timeout=35s\n");

$php = file_get_contents($hierarchy);
if ($php === false) {
    throw new RuntimeException('metaHierarchy.php not found');
}


$hierarchyHelperSignature = <<<'PHP_SIG'
function hierarchy_profile_snapshot(string $profile, ?array $workspaceMeta = null): array
{
PHP_SIG;
$hierarchyHelpers = <<<'PHP_HELPERS'
// REMASK_HONEST_SYNC_OUTCOME_V1
function hierarchy_asset_types_snapshot(array $snapshot): array
{
    $personalScope = trim((string)($snapshot['personal_scope_id'] ?? $snapshot['profile']['user_id'] ?? ''));
    $snapshot['businesses'] = array_values(array_filter((array)($snapshot['businesses'] ?? []),
        static fn($row): bool => is_array($row) && (string)($row['id'] ?? '') !== $personalScope
            && ($row['is_personal'] ?? false) !== true));
    // A generic Relay asset can be a portfolio or Page, not an ad account.
    // Known object IDs are global; exclude only explicit cross-type collisions.
    $otherIds = [];
    foreach (['businesses', 'pages'] as $kind) {
        foreach ((array)($snapshot[$kind] ?? []) as $row) {
            if (!is_array($row)) continue;
            $id = trim((string)($row['id'] ?? ''));
            if ($id !== '') $otherIds[$id] = true;
        }
    }
    $valid = static function ($row) use ($otherIds, $personalScope): bool {
        if (!is_array($row)) return false;
        $id = preg_replace('/^act_/', '', trim((string)($row['id'] ?? $row['account_id'] ?? '')));
        $business = trim((string)($row['business_id'] ?? ''));
        return $id !== '' && !isset($otherIds[$id]) && preg_match('/^\d{5,30}$/', $business)
            && $business !== $personalScope && ($row['is_personal'] ?? false) !== true;
    };
    $snapshot['ad_accounts'] = array_values(array_filter((array)($snapshot['ad_accounts'] ?? []), $valid));
    $snapshot['ad_accounts_count'] = count($snapshot['ad_accounts']);
    $snapshot['businesses_count'] = count($snapshot['businesses']);
    foreach ((array)($snapshot['businesses'] ?? []) as $i => $business) {
        if (!is_array($business)) continue;
        if (isset($business['accounts'])) $snapshot['businesses'][$i]['accounts'] = array_values(array_filter((array)$business['accounts'], $valid));
        $snapshot['businesses'][$i]['ad_account_count'] = count(array_filter($snapshot['ad_accounts'], static fn($row) => (string)($row['business_id'] ?? '') === (string)($business['id'] ?? '')));
    }
    foreach ((array)($snapshot['profiles'] ?? []) as $i => $row) {
        if (!is_array($row) || (string)($row['name'] ?? '') !== (string)($snapshot['profile']['name'] ?? '')) continue;
        $snapshot['profiles'][$i]['rk_count'] = $snapshot['ad_accounts_count'];
        $snapshot['profiles'][$i]['bm_count'] = $snapshot['businesses_count'];
        $snapshot['profiles'][$i]['ad_accounts_count'] = $snapshot['ad_accounts_count'];
    }
    if (is_array($snapshot['profile'] ?? null)) {
        $snapshot['profile']['rk_count'] = $snapshot['ad_accounts_count'];
        $snapshot['profile']['bm_count'] = $snapshot['businesses_count'];
        $snapshot['profile']['ad_accounts_count'] = $snapshot['ad_accounts_count'];
    }
    return $snapshot;
}

function hierarchy_private_sync_outcome(array $inventory): array
{
    foreach ((array)($inventory['businesses'] ?? []) as $business) {
        if (!is_array($business) || empty($business['auth_blocked'])) continue;
        $code = (string)($business['browser_error_code'] ?? '');
        if (in_array($code, ['CHECKPOINT_REQUIRED', 'TWO_FACTOR_REQUIRED', 'SESSION_EXPIRED'], true)) {
            return ['complete' => false, 'kind' => $code, 'error' => $code . ': Meta requires account authentication or verification.'];
        }
    }
    $blockedCodes = is_array($inventory['auth_blocked_business_codes'] ?? null)
        ? $inventory['auth_blocked_business_codes']
        : [];
    foreach ($blockedCodes as $businessId => $code) {
        $businessId = trim((string)$businessId);
        $code = trim((string)$code);
        if (!preg_match('/^\d{5,30}$/', $businessId)
            || !in_array($code, ['CHECKPOINT_REQUIRED', 'TWO_FACTOR_REQUIRED', 'SESSION_EXPIRED'], true)) continue;
        $targets = array_values(array_filter(array_map('strval', (array)($inventory['auth_blocked_businesses'] ?? [])),
            static fn($id) => preg_match('/^\d{5,30}$/', trim($id))));
        return [
            'complete' => false,
            'kind' => $code,
            'error' => $code . ': Facebook redirected this profile to login or verification while checking BM(s): ' . implode(', ', $targets) . '. Restore the Facebook session for this profile with fresh cookies, then repeat Sync.',
        ];
    }
    $complete = ($inventory['live_ready'] ?? false) === true;
    return ['complete' => $complete, 'kind' => $complete ? '' : 'PRIVATE_INCONCLUSIVE',
        'error' => $complete ? '' : 'Live private BM/RK inventory was not confirmed.'];
}
// REMASK_PERSISTENT_BM_RK_BINDING_V1
function hierarchy_worker_state(string $profile): array
{
    $profile = trim($profile);
    if ($profile === '') return [];

    $base = rtrim(trim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081')), '/');
    $url = $base . '/api/v1/profiles/' . rawurlencode($profile) . '/provisioning-state';
    $headers = ['Accept: application/json'];
    $key = trim((string)(getenv('REMASK_WORKER_API_KEY') ?: ''));
    if ($key !== '') $headers[] = 'X-Remask-Worker-Key: ' . $key;

    $ctx = stream_context_create(['http' => [
        'method' => 'GET',
        'header' => implode("\r\n", $headers) . "\r\n",
        'timeout' => 5,
        'ignore_errors' => true,
        'follow_location' => 0,
    ]]);

    $raw = @file_get_contents($url, false, $ctx);
    if (!is_string($raw) || trim($raw) === '') return [];
    $json = json_decode($raw, true);
    return is_array($json) ? $json : [];
}

function hierarchy_worker_live_inventory(
    string $profile,
    array $knownBusinessIds = [],
    array $knownAdAccountHints = [],
    array $knownPageHints = []
): array
{
    $profile = trim($profile);
    if ($profile === '') return [];

    $base = rtrim(trim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081')), '/');
    $url = $base . '/api/v1/profiles/' . rawurlencode($profile) . '/live-inventory';
    $businessIds = [];
    foreach ($knownBusinessIds as $businessId) {
        $businessId = trim((string)$businessId);
        if (preg_match('/^\d{5,30}$/', $businessId)) $businessIds[$businessId] = true;
    }
    $query = [];
    if ($businessIds !== []) {
        $query['business_ids'] = implode(',', array_keys($businessIds));
    }

    $hintPairs = [];
    foreach ($knownAdAccountHints as $businessId => $accountIds) {
        $businessId = trim((string)$businessId);
        if (!preg_match('/^\d{5,30}$/', $businessId)) continue;
        foreach ((array)$accountIds as $accountId) {
            $accountId = trim((string)$accountId);
            if (!preg_match('/^\d{5,30}$/', $accountId)) continue;
            $hintPairs[$businessId . ':' . $accountId] = true;
        }
    }
    if ($hintPairs !== []) {
        $query['ad_account_hints'] = implode(',', array_keys($hintPairs));
    }

    $pageHintPairs = [];
    foreach ($knownPageHints as $businessId => $pageIds) {
        $businessId = trim((string)$businessId);
        if (!preg_match('/^\d{5,30}$/', $businessId)) continue;
        foreach ((array)$pageIds as $pageId) {
            $pageId = trim((string)$pageId);
            if (!preg_match('/^\d{5,30}$/', $pageId)) continue;
            $pageHintPairs[$businessId . ':' . $pageId] = true;
        }
    }
    if ($pageHintPairs !== []) {
        $query['page_hints'] = implode(',', array_keys($pageHintPairs));
    }
    if ($query !== []) {
        $url .= '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986);
    }

    $headers = ['Accept: application/json'];
    $key = trim((string)(getenv('REMASK_WORKER_API_KEY') ?: ''));
    if ($key !== '') $headers[] = 'X-Remask-Worker-Key: ' . $key;

    $ctx = stream_context_create(['http' => [
        'method' => 'GET',
        'header' => implode("\r\n", $headers) . "\r\n",
        // Keep this below the 70s browser UI deadline, but long enough for
        // private BM discovery plus one BM's RK inventory.
        'timeout' => 64,
        'ignore_errors' => true,
        'follow_location' => 0,
    ]]);

    $raw = @file_get_contents($url, false, $ctx);
    if (!is_string($raw) || trim($raw) === '') {
        throw new RuntimeException('LIVE_INVENTORY_TRANSPORT_FAILED');
    }

    $status = 0;
    foreach ((array)($http_response_header ?? []) as $line) {
        if (preg_match('#^HTTP/\\S+\\s+(\\d{3})#i', (string)$line, $m)) {
            $status = (int)$m[1];
        }
    }

    $json = json_decode($raw, true);
    if (!is_array($json)) {
        throw new RuntimeException('LIVE_INVENTORY_INVALID_JSON');
    }
    if ($status < 200 || $status >= 300) {
        $detail = $json['detail'] ?? $json['error'] ?? ('HTTP ' . $status);
        throw new RuntimeException(
            'LIVE_INVENTORY_HTTP_' . $status . ': ' .
            (is_scalar($detail) ? trim((string)$detail) : 'worker error')
        );
    }
    return $json;
}

function hierarchy_sync_result_file(): string
{
    return '/var/lib/remask/workspace-sync-results.json';
}

function hierarchy_sync_result_put(
    string $requestId,
    string $profile,
    array $result
): void
{
    $requestId = trim($requestId);
    $profile = trim($profile);
    if (
        !preg_match('/^[A-Za-z0-9_-]{8,96}$/', $requestId)
        || $profile === ''
    ) return;

    $file = hierarchy_sync_result_file();
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0700, true);

    $fp = @fopen($file, 'c+');
    if (!$fp) return;
    try {
        if (!@flock($fp, LOCK_EX)) return;
        rewind($fp);
        $raw = stream_get_contents($fp);
        $all = [];
        if (is_string($raw) && trim($raw) !== '') {
            $decoded = json_decode($raw, true);
            if (is_array($decoded)) $all = $decoded;
        }

        $now = time();
        foreach ($all as $key => $row) {
            if (
                !is_array($row)
                || (int)($row['updated_at'] ?? 0) < ($now - 1800)
            ) {
                unset($all[$key]);
            }
        }

        // Store only status metadata. The actual snapshot is rebuilt from
        // current account state + last confirmed live inventory on read.
        $all[$requestId] = [
            'profile' => $profile,
            'updated_at' => $now,
            'status' => (string)($result['status'] ?? 'failed'),
            'sync_complete' => ($result['sync_complete'] ?? false) === true,
            'sync_error_kind' => trim((string)($result['sync_error_kind'] ?? '')),
            'sync_error' => trim((string)($result['sync_error'] ?? '')),
        ];

        $encoded = json_encode(
            $all,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if (!is_string($encoded)) return;
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, $encoded);
        fflush($fp);
    } finally {
        @flock($fp, LOCK_UN);
        @fclose($fp);
    }
}

function hierarchy_sync_result_get(
    string $requestId,
    string $profile
): array
{
    $requestId = trim($requestId);
    $profile = trim($profile);
    if (
        !preg_match('/^[A-Za-z0-9_-]{8,96}$/', $requestId)
        || $profile === ''
    ) return [];

    $raw = @file_get_contents(hierarchy_sync_result_file());
    if (!is_string($raw) || trim($raw) === '') return [];
    $all = json_decode($raw, true);
    if (!is_array($all)) return [];
    $row = $all[$requestId] ?? null;
    if (!is_array($row)) return [];
    if (trim((string)($row['profile'] ?? '')) !== $profile) return [];
    if ((int)($row['updated_at'] ?? 0) < (time() - 1800)) return [];
    return $row;
}

function hierarchy_created_businesses_apply_display(string $profile, array $snapshot): array
{
    // Exact worker CREATE proof is local historical state, not a live Meta inventory check.
    $raw = @file_get_contents('/var/lib/remask/workspace-created-businesses.json');
    $all = is_string($raw) ? json_decode($raw, true) : null;
    $incoming = is_array($all[$profile]['businesses'] ?? null) ? $all[$profile]['businesses'] : [];
    $rows = is_array($snapshot['businesses'] ?? null) ? $snapshot['businesses'] : [];
    $known = [];
    foreach ($rows as $row) {
        if (is_array($row)) $known[(string)($row['id'] ?? '')] = true;
    }
    foreach ($incoming as $binding) {
        if (!is_array($binding) || ($binding['create_confirmed'] ?? null) !== true) continue;
        $id = trim((string)($binding['business_id'] ?? ''));
        if (!preg_match('/^\\d{5,30}$/', $id) || isset($known[$id])) continue;
        $accounts = array_values(array_filter(
            is_array($snapshot['ad_accounts'] ?? null) ? $snapshot['ad_accounts'] : [],
            static fn($row): bool => is_array($row) && (string)($row['business_id'] ?? '') === $id
        ));
        $rows[] = [
            'id' => $id, 'name' => trim((string)($binding['business_name'] ?? $id)),
            'profile' => $profile, 'primary_page' => null,
            'ad_account_count' => count($accounts), 'accounts' => $accounts,
            '_provisioned_only' => true, '_source' => 'python_worker_confirmed_create',
            'create_confirmed' => true,
        ];
        $known[$id] = true;
    }
    $snapshot['businesses'] = $rows;
    if (is_array($snapshot['profile'] ?? null)) $snapshot['profile']['bm_count'] = count($rows);
    return $snapshot;
}

function hierarchy_created_accounts_apply_display(string $profile, array $snapshot): array
{
    // Restore successful CREATE identities after applying an older live
    // snapshot. A historical binding never establishes current ACTIVE status.
    $businessNames = [];
    foreach ((array)($snapshot['businesses'] ?? []) as $row) {
        if (is_array($row)) $businessNames[(string)($row['id'] ?? '')] = (string)($row['name'] ?? '');
    }
    $rows = (array)($snapshot['ad_accounts'] ?? []);
    $known = [];
    foreach ($rows as $row) {
        if (is_array($row)) $known[preg_replace('/^act_/', '', (string)($row['id'] ?? $row['account_id'] ?? ''))] = true;
    }
    foreach (hierarchy_binding_get($profile) as $binding) {
        $business = (string)($binding['business_id'] ?? '');
        $account = preg_replace('/^act_/', '', (string)($binding['ad_account_id'] ?? ''));
        if (!preg_match('/^\d{5,30}$/', $business) || !preg_match('/^\d{5,30}$/', $account)
            || !isset($businessNames[$business]) || isset($known[$account])) continue;
        $rows[] = ['profile' => $profile, 'id' => $account, 'account_id' => $account,
            'name' => (string)($binding['account_name'] ?? ('RK ' . $account)),
            'business_id' => $business, 'business_name' => $businessNames[$business],
            'account_status' => null, 'disable_reason' => null, 'currency' => '',
            'timezone_name' => '', 'funding' => null, '_provisioned_only' => true,
            '_source' => 'python_worker_binding'];
        $known[$account] = true;
    }
    $snapshot['ad_accounts'] = $rows;
    $snapshot['ad_accounts_count'] = count($rows);
    $snapshot['businesses_count'] = count($businessNames);
    foreach ((array)($snapshot['businesses'] ?? []) as $i => $business) {
        $accounts = array_values(array_filter($rows, static fn($row) =>
            is_array($row) && (string)($row['business_id'] ?? '') === (string)($business['id'] ?? '')));
        $snapshot['businesses'][$i]['accounts'] = $accounts;
        $snapshot['businesses'][$i]['ad_account_count'] = count($accounts);
    }
    if (is_array($snapshot['profile'] ?? null)) {
        $snapshot['profile']['bm_count'] = count($businessNames);
        $snapshot['profile']['rk_count'] = count($rows);
        $snapshot['profile']['ad_accounts_count'] = count($rows);
    }
    foreach ((array)($snapshot['profiles'] ?? []) as $i => $row) {
        if ((string)($row['name'] ?? '') !== $profile) continue;
        $snapshot['profiles'][$i]['bm_count'] = count($businessNames);
        $snapshot['profiles'][$i]['rk_count'] = count($rows);
        $snapshot['profiles'][$i]['ad_accounts_count'] = count($rows);
    }
    return $snapshot;
}

function hierarchy_binding_file(): string
{
    return '/var/lib/remask/workspace-provisioning-bindings.json';
}

function hierarchy_binding_all(): array
{
    $file = hierarchy_binding_file();
    $raw = @file_get_contents($file);
    if (!is_string($raw) || trim($raw) === '') return [];
    $json = json_decode($raw, true);
    return is_array($json) ? $json : [];
}

function hierarchy_binding_get(string $profile): array
{
    $all = hierarchy_binding_all();
    $row = $all[$profile] ?? null;
    if (!is_array($row)) return [];

    // V1 stored one BM->RK pair directly at profile level. Normalize it into
    // the V2 list shape so existing volume data remains usable.
    if (isset($row['business_id']) || isset($row['ad_account_id'])) {
        $businessId = trim((string)($row['business_id'] ?? ''));
        $adAccountId = trim((string)($row['ad_account_id'] ?? ''));
        if (
            preg_match('/^\d{5,30}$/', $businessId)
            && preg_match('/^\d{5,30}$/', $adAccountId)
        ) {
            return [[
                'business_id' => $businessId,
                'ad_account_id' => $adAccountId,
                'account_name' => trim((string)($row['account_name'] ?? '')),
                'updated_at' => (int)($row['updated_at'] ?? 0),
                'source' => (string)($row['source'] ?? 'legacy_binding_v1'),
            ]];
        }
        return [];
    }

    $items = $row['ad_accounts'] ?? $row;
    if (!is_array($items)) return [];

    $out = [];
    foreach ($items as $businessKey => $item) {
        if (!is_array($item)) continue;
        $businessId = trim((string)($item['business_id'] ?? $businessKey));
        $adAccountId = trim((string)($item['ad_account_id'] ?? ''));
        if (
            !preg_match('/^\d{5,30}$/', $businessId)
            || !preg_match('/^\d{5,30}$/', $adAccountId)
        ) continue;
        $out[] = [
            'business_id' => $businessId,
            'ad_account_id' => $adAccountId,
            'account_name' => trim((string)($item['account_name'] ?? '')),
            'updated_at' => (int)($item['updated_at'] ?? 0),
            'source' => (string)($item['source'] ?? 'python_worker_confirmed_entities'),
        ];
    }
    return $out;
}

function hierarchy_live_snapshot_file(): string
{
    return '/var/lib/remask/workspace-live-meta-snapshots.json';
}

function hierarchy_live_snapshot_all(): array
{
    $file = hierarchy_live_snapshot_file();
    $raw = @file_get_contents($file);
    if (!is_string($raw) || trim($raw) === '') return [];
    $json = json_decode($raw, true);
    return is_array($json) ? $json : [];
}

function hierarchy_live_snapshot_get(string $profile): array
{
    $profile = trim($profile);
    if ($profile === '') return [];
    $all = hierarchy_live_snapshot_all();
    $row = $all[$profile] ?? null;
    return is_array($row) ? $row : [];
}

function hierarchy_live_snapshot_put(
    string $profile,
    array $businesses,
    array $adAccounts,
    array $pages,
    array $profileRow,
    bool $pagesLiveVerified = false
): void
{
    $profile = trim($profile);
    if ($profile === '') return;

    $cleanBusinesses = [];
    foreach ($businesses as $row) {
        if (!is_array($row)) continue;
        $id = trim((string)($row['id'] ?? ''));
        if (!preg_match('/^\d{5,30}$/', $id)) continue;
        $row['profile'] = $profile;
        $cleanBusinesses[] = $row;
    }

    $cleanAccounts = [];
    foreach ($adAccounts as $row) {
        if (!is_array($row)) continue;
        $id = trim((string)($row['id'] ?? $row['account_id'] ?? ''));
        if (str_starts_with($id, 'act_')) $id = substr($id, 4);
        if (!preg_match('/^\d{5,30}$/', $id)) continue;
        $row['profile'] = $profile;
        $row['id'] = $id;
        $row['account_id'] = $id;
        $cleanAccounts[] = $row;
    }

    $cleanPages = [];
    foreach ($pages as $row) {
        if (!is_array($row)) continue;
        $id = trim((string)($row['id'] ?? ''));
        if (!preg_match('/^\d{5,30}$/', $id)) continue;
        $row['profile'] = $profile;
        $cleanPages[] = $row;
    }

    // Persist only live Meta inventory identity/counts. Profile readiness
    // (proxy/session/permissions/token health) must always come from the
    // current account store and current cached preflight, never from an old
    // live-inventory snapshot.
    $profileName = trim((string)($profileRow['name'] ?? $profile));
    if ($profileName === '') $profileName = $profile;
    $persistedProfile = [
        'name' => $profileName,
        'synced' => true,
        'bm_count' => count($cleanBusinesses),
        'rk_count' => count($cleanAccounts),
        'ad_accounts_count' => count($cleanAccounts),
        'pages_count' => count($cleanPages),
    ];

    $file = hierarchy_live_snapshot_file();
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0700, true);

    $fp = @fopen($file, 'c+');
    if (!$fp) return;
    try {
        if (!@flock($fp, LOCK_EX)) return;
        rewind($fp);
        $raw = stream_get_contents($fp);
        $all = [];
        if (is_string($raw) && trim($raw) !== '') {
            $decoded = json_decode($raw, true);
            if (is_array($decoded)) $all = $decoded;
        }

        $previous = is_array($all[$profile] ?? null)
            ? $all[$profile]
            : [];
        $now = time();
        $previousPageLiveAt = (int)($previous['pages_live_verified_at'] ?? 0);

        // REMASK_PAGE_VERIFICATION_TIMESTAMP_V3
        // BM/RK live truth is refreshed on every successful Sync. Page state
        // may be durable/preserved without being re-enumerated from Meta, so
        // advance the Page verification timestamp only on fresh live proof.
        $all[$profile] = [
            'profile' => $persistedProfile,
            'businesses' => array_values($cleanBusinesses),
            'ad_accounts' => array_values($cleanAccounts),
            'pages' => array_values($cleanPages),
            'updated_at' => $now,
            'pages_live_verified' => $pagesLiveVerified,
            'pages_live_verified_at' => $pagesLiveVerified
                ? $now
                : $previousPageLiveAt,
            'source' => $pagesLiveVerified
                ? 'live_meta_inventory'
                : (
                    $cleanPages !== []
                    ? 'live_bm_rk_with_confirmed_page_state'
                    : 'live_bm_rk_without_page_state'
                ),
        ];

        $encoded = json_encode(
            $all,
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES
        );
        if (!is_string($encoded)) return;
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, $encoded);
        fflush($fp);
    } finally {
        @flock($fp, LOCK_UN);
        @fclose($fp);
    }
}

function hierarchy_live_snapshot_apply_display(
    string $profile,
    array $snapshot
): array
{
    $saved = hierarchy_live_snapshot_get($profile);
    if ($saved === []) return $snapshot;

    $businesses = array_values(array_filter(
        (array)($saved['businesses'] ?? []),
        static fn($row) => is_array($row)
    ));
    $adAccounts = array_values(array_filter(
        (array)($saved['ad_accounts'] ?? []),
        static fn($row) => is_array($row)
    ));
    $pages = array_values(array_filter(
        (array)($saved['pages'] ?? []),
        static fn($row) => is_array($row)
    ));

    $snapshot['businesses'] = $businesses;
    $snapshot['businesses_count'] = count($businesses);
    $snapshot['ad_accounts'] = $adAccounts;
    $snapshot['ad_accounts_count'] = count($adAccounts);
    $snapshot['pages'] = $pages;
    $snapshot['pages_count'] = count($pages);

    // Keep the fresh profile row authoritative. The persisted live snapshot is
    // allowed to restore BM/RK and their counts only. This prevents an old
    // snapshot from erasing proxy_configured, proxy health or permission state.
    $freshProfile = null;
    if (is_array($snapshot['profiles'] ?? null)) {
        foreach ($snapshot['profiles'] as $i => $row) {
            if (!is_array($row)) continue;
            $name = trim((string)($row['name'] ?? $row['profile'] ?? ''));
            if ($name !== $profile) continue;
            $row['name'] = $name !== '' ? $name : $profile;
            $row['synced'] = true;
            $row['bm_count'] = count($businesses);
            $row['rk_count'] = count($adAccounts);
            $row['ad_accounts_count'] = count($adAccounts);
            $row['pages_count'] = count($pages);
            $snapshot['profiles'][$i] = $row;
            $freshProfile = $row;
            break;
        }
    }

    if ($freshProfile === null && is_array($snapshot['profile'] ?? null)) {
        $row = $snapshot['profile'];
        $name = trim((string)($row['name'] ?? $row['profile'] ?? ''));
        if ($name === '' || $name === $profile) {
            $row['name'] = $name !== '' ? $name : $profile;
            $row['synced'] = true;
            $row['bm_count'] = count($businesses);
            $row['rk_count'] = count($adAccounts);
            $row['ad_accounts_count'] = count($adAccounts);
            $row['pages_count'] = count($pages);
            $freshProfile = $row;
        }
    }

    if ($freshProfile === null) {
        $freshProfile = [
            'name' => $profile,
            'synced' => true,
            'bm_count' => count($businesses),
            'rk_count' => count($adAccounts),
            'ad_accounts_count' => count($adAccounts),
            'pages_count' => count($pages),
        ];
        if (is_array($snapshot['profiles'] ?? null)) {
            $snapshot['profiles'][] = $freshProfile;
        }
    }

    $snapshot['profile'] = $freshProfile;
    $snapshot['last_confirmed_live_meta_at'] = (int)($saved['updated_at'] ?? 0);
    $snapshot['pages_live_verified'] = ($saved['pages_live_verified'] ?? false) === true;
    $snapshot['pages_live_verified_at'] = (int)($saved['pages_live_verified_at'] ?? 0);
    $snapshot['display_source'] = (string)(
        $saved['source'] ?? 'last_confirmed_workspace_meta_state'
    );
    return $snapshot;
}

function hierarchy_binding_put(
    string $profile,
    string $businessId,
    string $adAccountId,
    string $accountName = ''
): void
{
    $profile = trim($profile);
    $businessId = trim($businessId);
    $adAccountId = trim($adAccountId);
    $accountName = trim($accountName);
    if (
        $profile === ''
        || !preg_match('/^\d{5,30}$/', $businessId)
        || !preg_match('/^\d{5,30}$/', $adAccountId)
        || $businessId === $adAccountId
    ) return;

    $file = hierarchy_binding_file();
    $dir = dirname($file);
    if (!is_dir($dir)) @mkdir($dir, 0700, true);

    $fp = @fopen($file, 'c+');
    if (!$fp) return;
    try {
        if (!@flock($fp, LOCK_EX)) return;
        rewind($fp);
        $raw = stream_get_contents($fp);
        $all = [];
        if (is_string($raw) && trim($raw) !== '') {
            $decoded = json_decode($raw, true);
            if (is_array($decoded)) $all = $decoded;
        }

        $profileRow = $all[$profile] ?? [];
        if (!is_array($profileRow)) $profileRow = [];
        $existing = hierarchy_binding_get($profile);
        $normalized = [];
        foreach ($existing as $item) {
            if (!is_array($item)) continue;
            $bid = trim((string)($item['business_id'] ?? ''));
            if (!preg_match('/^\d{5,30}$/', $bid)) continue;
            $normalized[$bid] = $item;
        }
        $normalized[$businessId] = [
            'business_id' => $businessId,
            'ad_account_id' => $adAccountId,
            'account_name' => $accountName,
            'updated_at' => time(),
            'source' => 'python_worker_confirmed_entities_v2',
        ];
        $all[$profile] = ['ad_accounts' => $normalized];

        $encoded = json_encode($all, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if (!is_string($encoded)) return;
        ftruncate($fp, 0);
        rewind($fp);
        fwrite($fp, $encoded);
        fflush($fp);
    } finally {
        @flock($fp, LOCK_UN);
        @fclose($fp);
    }
}
PHP_HELPERS;
$hierarchyBaseSignature = <<<'PHP_SIG'
function hierarchy_profile_snapshot_base(string $profile, ?array $workspaceMeta = null): array
{
PHP_SIG;

$hierarchySnapshotWrapper = <<<'PHP_WRAPPER'
function hierarchy_profile_snapshot(string $profile, ?array $workspaceMeta = null): array
{
    $snapshot = hierarchy_profile_snapshot_base($profile, $workspaceMeta);
    $snapshot = hierarchy_live_snapshot_apply_display($profile, $snapshot);
    $snapshot = hierarchy_created_accounts_apply_display($profile, hierarchy_created_businesses_apply_display($profile, $snapshot));
    $account = MetaEndpoint::accountForName($profile);
    $scope = trim((string)($account->userId ?? ''));
    foreach ((array)($account->cookies ?? []) as $cookie) {
        if (is_array($cookie) && ($cookie['name'] ?? '') === 'c_user') {
            $scope = trim((string)($cookie['value'] ?? '')); break;
        }
    }
    $snapshot['personal_scope_id'] = $scope;
    $snapshot['profile']['personal_scope_id'] = $scope;
    return hierarchy_asset_types_snapshot($snapshot);
}
PHP_WRAPPER;

$php = str_replace(
    $hierarchyHelperSignature,
    $hierarchyHelpers . "\n\n" .
        $hierarchySnapshotWrapper . "\n\n" .
        $hierarchyBaseSignature,
    $php,
    $hierarchyHelperCount
);
if ($hierarchyHelperCount !== 1) {
    throw new RuntimeException('worker binding helper injection failed: ' . $hierarchyHelperCount);
}

$fundingNeedle = "        \$rk['funding'] = MetaEndpoint::peekCachedAsset(\$profile, 'funding', \$id);";
$fundingReplacement = <<<'PHP_FUNDING'
        // REMASK_DIRECT_FUNDING_SNAPSHOT_V2
        $cachedFunding = MetaEndpoint::peekCachedAsset($profile, 'funding', $id);
        if (is_array($cachedFunding)) {
            $rk['funding'] = $cachedFunding;
        } elseif (($rk['_funding_metadata_loaded'] ?? false) === true) {
            $rk['funding'] = [
                'id' => $id,
                'name' => (string)($rk['name'] ?? ''),
                'account_status' => $rk['account_status'] ?? null,
                'disable_reason' => $rk['disable_reason'] ?? null,
                'currency' => (string)($rk['currency'] ?? ''),
                'balance' => $rk['balance'] ?? null,
                'amount_spent' => $rk['amount_spent'] ?? null,
                'spend_cap' => $rk['spend_cap'] ?? null,
                'is_prepay_account' => $rk['is_prepay_account'] ?? null,
                'funding_source' => $rk['funding_source'] ?? null,
                'funding_source_details' => is_array($rk['funding_source_details'] ?? null) ? $rk['funding_source_details'] : [],
                'expired_funding_source_details' => is_array($rk['expired_funding_source_details'] ?? null) ? $rk['expired_funding_source_details'] : [],
                '_source' => 'direct_ad_account_sync',
            ];
        } else {
            $rk['funding'] = null;
        }
PHP_FUNDING;
if (strpos($php, 'REMASK_DIRECT_FUNDING_SNAPSHOT_V1') === false) {
    $php = str_replace($fundingNeedle, $fundingReplacement, $php, $fundingPatchCount);
    if ($fundingPatchCount !== 1) {
        throw new RuntimeException('direct funding snapshot patch failed: ' . $fundingPatchCount);
    }
}

$syncProfileReplacement = <<<'PHP'
    if ($action === 'sync_result') {
        $profile = trim((string)($input['profile'] ?? ''));
        $requestId = trim((string)($input['request_id'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');
        if (!preg_match('/^[A-Za-z0-9_-]{8,96}$/', $requestId)) {
            throw new InvalidArgumentException('request_id is required');
        }

        $savedResult = hierarchy_sync_result_get($requestId, $profile);
        if ($savedResult === []) {
            MetaEndpoint::ok([
                'profile_name' => $profile,
                'request_id' => $requestId,
                'pending' => true,
            ]);
        }

        if (($savedResult['sync_complete'] ?? false) === true) {
            $reconciled = hierarchy_live_snapshot_apply_display(
                $profile,
                hierarchy_profile_snapshot($profile)
            );
            $reconciled['request_id'] = $requestId;
            $reconciled['pending'] = false;
            $reconciled['sync_complete'] = true;
            $reconciled['sync_reconciled'] = true;
            MetaEndpoint::ok(hierarchy_asset_types_snapshot($reconciled));
        }

        MetaEndpoint::ok([
            'profile_name' => $profile,
            'request_id' => $requestId,
            'pending' => false,
            'sync_complete' => false,
            'sync_error_kind' => (string)($savedResult['sync_error_kind'] ?? 'PRIVATE_SYNC'),
            'sync_error' => (string)($savedResult['sync_error'] ?? 'Private Meta sync failed'),
            'sync_reconciled' => true,
        ]);
    }

    if ($action === 'snapshot_profile') {
        $profile = trim((string)($input['profile'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');

        $snapshot = hierarchy_profile_snapshot($profile);
        $snapshot['snapshot_only'] = true;
        $snapshot['sync_source'] = 'local_confirmed_state';
        MetaEndpoint::ok($snapshot);
    }

    if ($action === 'sync_profile') {
        // Keep backend reconciliation alive even if a mobile browser/network
        // closes the long HTTP request before Meta finishes.
        @ignore_user_abort(true);

        $profile = trim((string)($input['profile'] ?? ''));
        $requestId = trim((string)($input['request_id'] ?? ''));
        $requestedBusinessId = trim((string)($input['business_id'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');
        if (
            $requestId !== ''
            && !preg_match('/^[A-Za-z0-9_-]{8,96}$/', $requestId)
        ) {
            throw new InvalidArgumentException('invalid request_id');
        }
        if (
            $requestedBusinessId !== ''
            && !preg_match('/^\d{5,30}$/', $requestedBusinessId)
        ) {
            throw new InvalidArgumentException('invalid business_id');
        }

        // REMASK_PRIVATE_BROWSER_SYNC_V1
        // Workspace sync is sourced only from the profile-bound Business Suite
        // browser worker. Official Graph /me, me/adaccounts and BM Graph edges
        // are deliberately not part of this path.
        $syncWarnings = [];
        $existingSnapshot = hierarchy_profile_snapshot($profile);

        // Live synchronization authority remains the current authenticated
        // Facebook private UI. The last *confirmed live* snapshot is used only
        // as a navigation hint when Meta's selector fails to enumerate BM/RK.
        // A hint can never create a success by itself; the worker must still
        // re-open Meta and prove the current account live.
        $confirmedLive = hierarchy_live_snapshot_get($profile);
        $knownBusinessIds = [];
        $knownAdAccountHints = [];
        $knownPageHints = [];
        $unscopedPageHints = [];

        foreach ((array)($confirmedLive['businesses'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $businessId = trim((string)($row['id'] ?? ''));
            if (preg_match('/^\d{5,30}$/', $businessId)) {
                $knownBusinessIds[$businessId] = true;
            }
        }
        foreach ((array)($confirmedLive['ad_accounts'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $businessId = trim((string)($row['business_id'] ?? ''));
            $accountId = trim((string)($row['id'] ?? $row['account_id'] ?? ''));
            if (str_starts_with($accountId, 'act_')) $accountId = substr($accountId, 4);
            if (
                preg_match('/^\d{5,30}$/', $businessId)
                && preg_match('/^\d{5,30}$/', $accountId)
            ) {
                $knownBusinessIds[$businessId] = true;
                $knownAdAccountHints[$businessId][$accountId] = true;
            }
        }

        // REMASK_WORKSPACE_PAGE_HINTS_V1
        // Last-live Page rows are navigation hints only. Current Python
        // Business Settings must revalidate them before pages_ready=true.
        foreach ((array)($confirmedLive['pages'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $pageId = trim((string)($row['id'] ?? $row['page_id'] ?? ''));
            $businessId = trim((string)($row['business_id'] ?? ''));
            if (!preg_match('/^\d{5,30}$/', $pageId)) continue;
            if (preg_match('/^\d{5,30}$/', $businessId)) {
                $knownPageHints[$businessId][$pageId] = true;
                $knownBusinessIds[$businessId] = true;
            } else {
                $unscopedPageHints[$pageId] = true;
            }
        }

        // REMASK_SYNC_HINTED_BM_ONLY_V1
        // The durable worker binding is never accepted as current inventory.
        // It only restores the last worker-confirmed BM->RK navigation hint
        // when the live display snapshot has lost the account row. The Python
        // worker still has to reopen Ads Manager and confirm that exact RK live.
        foreach (hierarchy_binding_get($profile) as $binding) {
            if (!is_array($binding)) continue;
            $businessId = trim((string)($binding['business_id'] ?? ''));
            $accountId = trim((string)($binding['ad_account_id'] ?? ''));
            if (
                !preg_match('/^\d{5,30}$/', $businessId)
                || !preg_match('/^\d{5,30}$/', $accountId)
            ) continue;
            $knownBusinessIds[$businessId] = true;
            $knownAdAccountHints[$businessId][$accountId] = true;
        }

        // REMASK_NO_UNSCOPED_PAGE_TO_BM_INFERENCE_V1
        // Never assign every unscoped preserved Page to the only known BM.
        // Old profile snapshots may contain historical standalone Pages; doing
        // so manufactured multiple fake BM->Page hints (profile 7 reached four).
        // Only rows with an explicit business_id become live revalidation hints.

        // REMASK_REQUESTED_BUSINESS_SYNC_SCOPE_V1
        // Workspace sends business_id when the user synchronizes one BM row.
        // Honor that scope instead of silently turning one-BM sync into a
        // full-profile scan across unrelated historical bindings.
        if ($requestedBusinessId !== '') {
            $requestedAccounts = (array)($knownAdAccountHints[$requestedBusinessId] ?? []);
            $requestedPages = (array)($knownPageHints[$requestedBusinessId] ?? []);
            $knownBusinessIds = [$requestedBusinessId => true];
            $knownAdAccountHints = $requestedAccounts !== []
                ? [$requestedBusinessId => $requestedAccounts]
                : [];
            $knownPageHints = $requestedPages !== []
                ? [$requestedBusinessId => $requestedPages]
                : [];
        }

        // Only an explicitly selected Business restricts live discovery.
        // Full profile Sync must discover new BMs even when an older BM has RK.
        $liveBusinessHints = $requestedBusinessId !== ''
            ? [$requestedBusinessId]
            : [];

        try {
            $liveInventory = hierarchy_worker_live_inventory(
                $profile,
                $liveBusinessHints,
                array_map(
                    static fn($ids) => array_keys((array)$ids),
                    $knownAdAccountHints
                ),
                array_map(
                    static fn($ids) => array_keys((array)$ids),
                    $knownPageHints
                )
            );
        } catch (Throwable $liveInventoryError) {
            $message = trim((string)$liveInventoryError->getMessage());
            $errorKind = 'PRIVATE_SYNC';
            foreach (['CHECKPOINT_REQUIRED', 'TWO_FACTOR_REQUIRED', 'SESSION_EXPIRED'] as $authCode) {
                if (stripos($message, $authCode) !== false) {
                    $errorKind = $authCode;
                    break;
                }
            }

            if ($requestId !== '') {
                hierarchy_sync_result_put(
                    $requestId,
                    $profile,
                    [
                        'status' => 'failed',
                        'sync_complete' => false,
                        'sync_error_kind' => $errorKind,
                        'sync_error' => $message,
                    ]
                );
            }

            if ($errorKind !== 'PRIVATE_SYNC') {
                throw new RuntimeException($errorKind . ': ' . $message, 0, $liveInventoryError);
            }
            throw new RuntimeException('PRIVATE_SYNC_FAILED: ' . $message, 0, $liveInventoryError);
        }

        $businessRows = [];
        $adAccountRows = [];
        $seenBusiness = [];
        $seenAccount = [];
        $rkPartial = false;

        foreach ((array)($liveInventory['businesses'] ?? []) as $liveBusiness) {
            if (!is_array($liveBusiness)) continue;
            $businessId = trim((string)($liveBusiness['id'] ?? ''));
            if (!preg_match('/^\\d{5,30}$/', $businessId)) continue;

            $businessName = trim((string)($liveBusiness['name'] ?? $businessId));
            if ($businessName === '') $businessName = $businessId;
            $accountsForBusiness = [];
            if (!empty($liveBusiness['ad_accounts_partial'])) {
                $rkPartial = true;
                $syncWarnings[] = 'Проверены отдельные РК. Полный список РК этого BM не подтверждён; остальные сохранённые строки сохранены без новой проверки.';
                foreach ((array)($existingSnapshot['ad_accounts'] ?? []) as $oldAccount) {
                    if (!is_array($oldAccount) || (string)($oldAccount['business_id'] ?? '') !== $businessId) continue;
                    $oldId = preg_replace('/^act_/', '', (string)($oldAccount['id'] ?? $oldAccount['account_id'] ?? ''));
                    $freshIds = array_map(static fn($row) => preg_replace('/^act_/', '', (string)($row['id'] ?? $row['account_id'] ?? '')), (array)($liveBusiness['ad_accounts'] ?? []));
                    if ($oldId === '' || in_array($oldId, $freshIds, true)) continue;
                    $oldAccount['_sync_preserved'] = true;
                    $adAccountRows[] = $oldAccount;
                    $seenAccount[$oldId] = true;
                }
            }

            foreach ((array)($liveBusiness['ad_accounts'] ?? []) as $liveAccount) {
                if (!is_array($liveAccount)) continue;
                $accountId = trim((string)($liveAccount['id'] ?? $liveAccount['account_id'] ?? ''));
                if (str_starts_with($accountId, 'act_')) $accountId = substr($accountId, 4);
                if (!preg_match('/^\\d{5,30}$/', $accountId) || isset($seenAccount[$accountId])) continue;

                $row = $liveAccount;
                $row['profile'] = $profile;
                $row['id'] = $accountId;
                $row['account_id'] = $accountId;
                $row['business_id'] = $businessId;
                $row['business_name'] = $businessName;
                // An ID-only fast revalidation cannot rename a known RK or
                // discard its Business asset alias. Keep identity metadata only
                // for the same canonical RK in this exact profile and BM.
                foreach ((array)($existingSnapshot['ad_accounts'] ?? []) as $knownAccount) {
                    if (!is_array($knownAccount) || (string)($knownAccount['business_id'] ?? '') !== $businessId) continue;
                    $knownId = preg_replace('/^act_/', '', (string)($knownAccount['id'] ?? $knownAccount['account_id'] ?? ''));
                    if ($knownId !== $accountId) continue;
                    $freshName = trim((string)($row['name'] ?? ''));
                    $knownName = trim((string)($knownAccount['name'] ?? ''));
                    if (($freshName === '' || preg_match('/^(?:act_)?\d{5,30}$/', $freshName)) && $knownName !== '' && !preg_match('/^(?:act_)?\d{5,30}$/', $knownName)) $row['name'] = $knownName;
                    if (empty($row['business_asset_id']) && !empty($knownAccount['business_asset_id'])) $row['business_asset_id'] = $knownAccount['business_asset_id'];
                    break;
                }
                $row['_business_edge'] = (string)($row['_source'] ?? 'business_suite_browser_live_inventory');
                if (!array_key_exists('funding', $row)) $row['funding'] = null;

                $seenAccount[$accountId] = true;
                $adAccountRows[] = $row;
                $accountsForBusiness[] = $row;

                hierarchy_binding_put(
                    $profile,
                    $businessId,
                    $accountId,
                    trim((string)($row['name'] ?? ''))
                );
            }

            $businessRows[] = [
                'profile' => $profile,
                'id' => $businessId,
                'name' => $businessName,
                'verification_status' => trim((string)($liveBusiness['verification_status'] ?? '')),
                'primary_page' => is_array($liveBusiness['primary_page'] ?? null) ? $liveBusiness['primary_page'] : null,
                'ad_account_count' => count($accountsForBusiness),
                'accounts' => $accountsForBusiness,
                '_source' => (string)($liveBusiness['source'] ?? $liveBusiness['ad_accounts_source'] ?? 'business_suite_browser_live_inventory'),
                '_live_ready' => ($liveBusiness['ad_accounts_ready'] ?? false) === true,
            ];
            $seenBusiness[$businessId] = true;
        }

        // Provisioning bindings are historical state only. They are never
        // merged into the result of a live Meta synchronization.
        $workerConfirmedCount = 0;

        $pageRows = [];
        $seenPages = [];
        foreach ((array)($liveInventory['pages'] ?? []) as $livePage) {
            if (!is_array($livePage)) continue;
            $pageId = trim((string)($livePage['id'] ?? ''));
            if (!preg_match('/^\d{5,30}$/', $pageId) || isset($seenPages[$pageId])) continue;
            $row = $livePage;
            $row['id'] = $pageId;
            $row['profile'] = $profile;
            $row['_source'] = (string)($liveInventory['pages_source'] ?? 'facebook_business_browser');
            $seenPages[$pageId] = true;
            $pageRows[] = $row;
        }

        foreach ((array)($liveInventory['warnings'] ?? []) as $warning) {
            if (is_scalar($warning) && trim((string)$warning) !== '') {
                $syncWarnings[] = trim((string)$warning);
            }
        }

        $sessionReady = (($liveInventory['session_ready'] ?? false) === true);
        $pagesReady = (($liveInventory['pages_ready'] ?? false) === true);
        $pagesLiveVerified = (($liveInventory['pages_live_verified'] ?? false) === true);

        // REMASK_PRESERVE_CONFIRMED_PAGES_ON_PARTIAL_V1
        // A failed Page probe must never erase a previously confirmed FP list.
        // Prefer the durable last-confirmed live snapshot over the transient
        // display snapshot, then fall back to the latter for legacy data.
        if (!$pagesReady && $pageRows === []) {
            $preservedPages = array_values(array_filter(
                (array)($confirmedLive['pages'] ?? []),
                static fn($row) => is_array($row)
            ));
            if ($preservedPages === []) {
                $preservedPages = array_values(array_filter(
                    (array)($existingSnapshot['pages'] ?? []),
                    static fn($row) => is_array($row)
                ));
            }
            if ($preservedPages !== []) {
                foreach ($preservedPages as &$preservedPage) {
                    if (is_array($preservedPage)) {
                        $preservedPage['_sync_preserved'] = true;
                    }
                }
                unset($preservedPage);
                $pageRows = $preservedPages;
                // REMASK_USABLE_PAGE_STATE_V3
                // Last-confirmed Pages remain usable inventory even when this
                // particular Sync did not re-enumerate account-level Pages.
                // Keep live verification as a separate truth dimension.
                $pagesReady = true;
                if (trim((string)($liveInventory['pages_source'] ?? '')) === '') {
                    $liveInventory['pages_source'] = 'last_confirmed_page_state';
                }
            }
        }

        // If browser inventory was inconclusive, preserve the previous private
        // snapshot rather than erasing working rows.
        $liveReady = (($liveInventory['live_ready'] ?? false) === true);
        if (!$liveReady && $adAccountRows === []) {
            foreach ((array)($existingSnapshot['ad_accounts'] ?? []) as $row) {
                if (!is_array($row)) continue;
                $id = trim((string)($row['id'] ?? $row['account_id'] ?? ''));
                if ($id === '') continue;
                $row['_sync_preserved'] = true;
                $adAccountRows[] = $row;
            }
            if ($businessRows === []) {
                $businessRows = array_values(array_filter(
                    (array)($existingSnapshot['businesses'] ?? []),
                    static fn($row) => is_array($row)
                ));
            }
            $syncWarnings[] = 'Live browser inventory was inconclusive; previous private snapshot preserved';
        }

        // REMASK_SCOPED_SYNC_MERGE_LIVE_SIBLINGS_V1
        // A row-scoped BM sync proves only the requested Business. Merge that
        // fresh result into the last confirmed live profile snapshot instead
        // of replacing the whole profile inventory and accidentally deleting
        // sibling BMs/RKs that were not part of this request.
        if ($liveReady && $requestedBusinessId !== '') {
            foreach ((array)($confirmedLive['businesses'] ?? []) as $row) {
                if (!is_array($row)) continue;
                $businessId = trim((string)($row['id'] ?? ''));
                if (
                    !preg_match('/^\d{5,30}$/', $businessId)
                    || $businessId === $requestedBusinessId
                    || isset($seenBusiness[$businessId])
                ) continue;
                $businessRows[] = $row;
                $seenBusiness[$businessId] = true;
            }

            foreach ((array)($confirmedLive['ad_accounts'] ?? []) as $row) {
                if (!is_array($row)) continue;
                $businessId = trim((string)($row['business_id'] ?? ''));
                $accountId = trim((string)($row['id'] ?? $row['account_id'] ?? ''));
                if (str_starts_with($accountId, 'act_')) $accountId = substr($accountId, 4);
                if (
                    !preg_match('/^\d{5,30}$/', $businessId)
                    || !preg_match('/^\d{5,30}$/', $accountId)
                    || $businessId === $requestedBusinessId
                    || isset($seenAccount[$accountId])
                ) continue;
                $row['_sync_preserved_sibling'] = true;
                $adAccountRows[] = $row;
                $seenAccount[$accountId] = true;
            }
        }

        $typedInventory = hierarchy_asset_types_snapshot(['businesses' => $businessRows, 'ad_accounts' => $adAccountRows, 'pages' => $pageRows,
            'personal_scope_id' => (string)($existingSnapshot['personal_scope_id'] ?? '')]);
        $businessRows = $typedInventory['businesses'];
        $adAccountRows = $typedInventory['ad_accounts'];
        $snapshot = $existingSnapshot;
        $snapshot['businesses'] = array_values($businessRows);
        $snapshot['businesses_count'] = count($businessRows);
        $snapshot['ad_accounts'] = array_values($adAccountRows);
        $snapshot['ad_accounts_count'] = count($adAccountRows);
        $snapshot['pages'] = array_values($pageRows);
        $snapshot['pages_count'] = count($pageRows);
        $snapshot['pages_ready'] = $pagesReady;
        $snapshot['pages_live_verified'] = $pagesLiveVerified;
        $snapshot['pages_source'] = (string)($liveInventory['pages_source'] ?? '');
        $snapshot['pages_diagnostic'] = trim((string)($liveInventory['pages_diagnostic'] ?? ''));

        // REMASK_FULL_SYNC_REQUIRES_PAGES_V1
        // LEGACY CONTRACT DISABLED by REMASK_STABLE_SYNC_BOUNDARY_V2.
        //
        // BM/RK and Fan Pages are independent Meta inventory surfaces. A live
        // BM/RK confirmation must not be turned into a whole-profile failure
        // merely because Facebook's separate Page SPA/Relay surface is cold,
        // delayed or temporarily inconclusive. Keep BM/RK strict/live, preserve
        // the last confirmed Page snapshot, and report Page uncertainty as a
        // warning/partial dimension instead of PRIVATE_INCONCLUSIVE.
        // REMASK_STABLE_SYNC_BOUNDARY_V2
        $syncOutcome = hierarchy_private_sync_outcome($liveInventory);
        $syncComplete = $syncOutcome['complete'];

        $snapshot['sync_source'] = 'private_business_suite_browser';
        $snapshot['live_inventory_available'] = $liveReady;
        $snapshot['confirmed_worker_bindings'] = $workerConfirmedCount;
        $snapshot['graph_preflight_available'] = false;
        $snapshot['sync_complete'] = $syncComplete;
        $snapshot['sync_partial'] = ($syncComplete && (!$pagesLiveVerified || $rkPartial));
        unset($snapshot['sync_error_kind'], $snapshot['sync_error']);

        if (!$syncComplete) {
            $snapshot['sync_error_kind'] = $syncOutcome['kind'];
            $snapshot['sync_error'] = $syncOutcome['error'];
        } elseif (!$pagesLiveVerified) {
            // REMASK_PAGE_LIVE_VERIFICATION_WARNING_V1
            // A usable Page baseline is not the same thing as fresh live Page
            // enumeration. Keep BM/RK sync successful, but never present stale
            // or durable Page state as if this Sync had just proved it live.
            $syncWarnings[] = $pagesReady
                ? 'BM/РК проверены. Fan Page показаны из сохранённого состояния; текущая проверка FP в Meta не завершена'
                : 'Live Fan Page inventory was not confirmed in this sync';
        }

        $responseProfile = null;
        if (is_array($snapshot['profiles'] ?? null)) {
            foreach ($snapshot['profiles'] as $i => $profileRow) {
                if (!is_array($profileRow)) continue;
                $rowName = trim((string)($profileRow['name'] ?? $profileRow['profile'] ?? ''));
                if ($rowName !== '' && $rowName !== $profile) continue;
                $snapshot['profiles'][$i]['name'] = $rowName !== '' ? $rowName : $profile;
                $snapshot['profiles'][$i]['synced'] = $syncComplete;
                $snapshot['profiles'][$i]['bm_count'] = count($businessRows);
                $snapshot['profiles'][$i]['rk_count'] = count($adAccountRows);
                $snapshot['profiles'][$i]['ad_accounts_count'] = count($adAccountRows);
                $snapshot['profiles'][$i]['pages_count'] = count($pageRows);
                $snapshot['profiles'][$i]['token_status'] = 'PRIVATE';
                $snapshot['profiles'][$i]['permissions_available'] = null;
                $snapshot['profiles'][$i]['ads_management_granted'] = null;
                $snapshot['profiles'][$i]['ads_read_granted'] = null;
                $snapshot['profiles'][$i]['business_management_granted'] = null;

                $currentTransport = is_array($snapshot['profiles'][$i]['transport'] ?? null)
                    ? $snapshot['profiles'][$i]['transport']
                    : [];
                // The private live-inventory endpoint cannot reach browser_open
                // unless the profile resolver supplied a configured proxy.
                // Therefore a completed worker call is authoritative for this
                // sync response: stale snapshot metadata must never clear proxy.
                $proxyConfigured = true;

                $snapshot['profiles'][$i]['proxy_configured'] = true;
                $snapshot['profiles'][$i]['proxy_status'] = (
                    strtoupper((string)($snapshot['profiles'][$i]['proxy_status'] ?? '')) === 'NOT_CONFIGURED'
                ) ? 'NOT_CHECKED' : ($snapshot['profiles'][$i]['proxy_status'] ?? 'NOT_CHECKED');
                $snapshot['profiles'][$i]['transport'] = array_merge(
                    $currentTransport,
                    [
                        'network_identity' => 'profile_bound',
                        'proxy_configured' => $proxyConfigured,
                        'session_context' => true,
                        'direct_fallback' => false,
                        'source' => 'business_suite_browser',
                    ]
                );
                $responseProfile = $snapshot['profiles'][$i];
                break;
            }
        }
        if (!is_array($responseProfile)) {
            // Reaching live inventory already proves that the internal resolver
            // supplied a configured proxy and a logged-in FB session. Permission
            // state is intentionally UNKNOWN here because private sync does not
            // query official Graph permissions.
            $responseProfile = [
                'name' => $profile,
                'synced' => $syncComplete,
                'bm_count' => count($businessRows),
                'rk_count' => count($adAccountRows),
                'ad_accounts_count' => count($adAccountRows),
                'pages_count' => count($pageRows),
                'token_status' => 'PRIVATE',
                'proxy_configured' => true,
                'ads_management_granted' => null,
                'business_management_granted' => null,
                'permissions_available' => null,
                'transport' => [
                    'network_identity' => 'profile_bound',
                    'proxy_configured' => true,
                    'session_context' => true,
                    'direct_fallback' => false,
                    'source' => 'business_suite_browser',
                ],
            ];
        }
        $snapshot['profile'] = $responseProfile;
        $snapshot['profile_name'] = $profile;

        // REMASK_PERSIST_ONLY_COMPLETE_META_SNAPSHOT_V1
        // LEGACY "all surfaces or nothing" persistence is intentionally
        // relaxed by REMASK_STABLE_SYNC_BOUNDARY_V2. When BM/RK are live
        // confirmed we persist them immediately; Page rows are either freshly
        // confirmed or the preserved last-confirmed rows assembled above.
        if ($syncComplete) {
            hierarchy_live_snapshot_put(
                $profile,
                $businessRows,
                $adAccountRows,
                $pageRows,
                $responseProfile,
                $pagesLiveVerified
            );
            $snapshot['last_confirmed_live_meta_at'] = time();
            $snapshot['display_source'] = $pagesLiveVerified
                ? 'live_meta_inventory'
                : (
                    $pagesReady
                    ? 'live_bm_rk_with_confirmed_page_state'
                    : 'live_bm_rk_without_page_state'
                );
        }

        hierarchy_activity([
            'action' => 'sync_profile',
            'entity_type' => 'profile',
            'entity_id' => $profile,
            'profile_name' => $profile,
            'status' => $syncComplete ? 'SUCCESS' : 'ERROR',
            'summary' => $syncComplete
                ? 'Приватная синхронизация FB-профиля завершена'
                : (string)$snapshot['sync_error'],
            'details' => [
                'sync_source' => 'private_business_suite_browser',
                'live_ready' => $liveReady,
                'worker_confirmed_bindings' => $workerConfirmedCount,
                'sync_complete' => $syncComplete,
                'businesses' => count($businessRows),
                'ad_accounts' => count($adAccountRows),
                'pages' => count($pageRows),
                'pages_ready' => $pagesReady,
                'pages_live_verified' => $pagesLiveVerified,
                'pages_source' => (string)($snapshot['pages_source'] ?? ''),
                'warnings' => array_values(array_unique($syncWarnings)),
            ],
        ]);

        if ($syncWarnings !== []) {
            $snapshot['sync_warnings'] = array_values(array_unique($syncWarnings));
        }

        if ($requestId !== '') {
            hierarchy_sync_result_put(
                $requestId,
                $profile,
                [
                    'status' => $syncComplete ? 'success' : 'failed',
                    'sync_complete' => $syncComplete,
                    'sync_error_kind' => (string)($snapshot['sync_error_kind'] ?? ''),
                    'sync_error' => (string)($snapshot['sync_error'] ?? ''),
                ]
            );
        }

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

// Mutations must validate the current profile transport and current asset access.
// Never rely on a potentially stale read cache immediately before creating BM/RK.
$php = str_replace(
    "\$preflight = MetaEndpoint::cachedPreflight(\$profile, false);",
    "\$preflight = MetaEndpoint::cachedPreflight(\$profile, true);",
    $php,
    $livePreflightCount
);
$php = str_replace(
    "\$pages = MetaEndpoint::cachedAsset(\$profile, 'pages', '', false);",
    "\$pages = MetaEndpoint::cachedAsset(\$profile, 'pages', '', true);",
    $php,
    $livePagesCount
);
$php = str_replace(
    "\$businesses = MetaEndpoint::cachedAsset(\$profile, 'businesses', '', false);",
    "\$businesses = MetaEndpoint::cachedAsset(\$profile, 'businesses', '', true);",
    $php,
    $liveBusinessesCount
);
if ($livePreflightCount < 2 || $livePagesCount < 1 || $liveBusinessesCount < 1) {
    throw new RuntimeException(
        'live mutation preflight patch failed: preflight=' . $livePreflightCount .
        ' pages=' . $livePagesCount . ' businesses=' . $liveBusinessesCount
    );
}

// Explicit readiness dimensions in the profile snapshot. Cache-only: opening
// Workspace itself does not create extra Graph traffic.

$bindingSnapshotNeedle = <<<'PHP_BINDING'
    $businessAccountMap = [];
    $bmRows = [];
PHP_BINDING;
$bindingSnapshotReplacement = <<<'PHP_BINDING'
    $bindings = hierarchy_binding_get($profile);

    $knownBusinessNames = [];
    foreach ($businessRows as $businessRow) {
        if (!is_array($businessRow)) continue;
        $businessRowId = trim((string)($businessRow['id'] ?? ''));
        if ($businessRowId === '') continue;
        $knownBusinessNames[$businessRowId] = trim((string)($businessRow['name'] ?? $businessRowId));
    }

    $businessAccountMap = [];
    $bmRows = [];
PHP_BINDING;
$php = str_replace(
    $bindingSnapshotNeedle,
    $bindingSnapshotReplacement,
    $php,
    $bindingSnapshotCount
);
if ($bindingSnapshotCount !== 1) {
    throw new RuntimeException('snapshot binding prelude patch failed: ' . $bindingSnapshotCount);
}

$rkRowsSnapshotNeedle = <<<'PHP_BINDING'
    $rkRows = [];
    foreach ($allAccounts as $rk) {
PHP_BINDING;
$rkRowsSnapshotReplacement = <<<'PHP_BINDING'
    foreach ($bindings as $binding) {
        if (!is_array($binding)) continue;
        $boundBusinessId = trim((string)($binding['business_id'] ?? ''));
        $boundAdAccountId = trim((string)($binding['ad_account_id'] ?? ''));
        if (
            !preg_match('/^\d{5,30}$/', $boundBusinessId)
            || !preg_match('/^\d{5,30}$/', $boundAdAccountId)
            || !isset($knownBusinessNames[$boundBusinessId])
            || isset($businessAccountMap[$boundAdAccountId])
        ) continue;

        $businessAccountMap[$boundAdAccountId] = [
            'id' => $boundBusinessId,
            'name' => $knownBusinessNames[$boundBusinessId],
            'source' => 'python_worker_binding',
            'account_name' => trim((string)($binding['account_name'] ?? '')),
        ];
    }

    $rkRows = [];
    foreach ($allAccounts as $rk) {
PHP_BINDING;
$php = str_replace(
    $rkRowsSnapshotNeedle,
    $rkRowsSnapshotReplacement,
    $php,
    $rkRowsSnapshotCount
);
if ($rkRowsSnapshotCount !== 1) {
    throw new RuntimeException('snapshot RK binding patch failed: ' . $rkRowsSnapshotCount);
}

$tokenOnlyNeedle = <<<'PHP_BINDING'
        $bm = $businessAccountMap[$id] ?? null;
        $rk['profile'] = $profile;
PHP_BINDING;
$tokenOnlyReplacement = <<<'PHP_BINDING'
        $bm = $businessAccountMap[$id] ?? null;
        if (!is_array($bm)) continue; // never render token-only/unmapped RK
        $rk['profile'] = $profile;
        if ((string)($bm['source'] ?? '') === 'python_worker_binding' && !isset($rk['account_status'])) {
            // Worker proved CREATE and the exact BM relation only. Mark an
            // unknown status as provisional; never hide an observed DISABLED.
            $rk['_provisioned_only'] = true;
            $rk['_raw_account_status'] = $rk['account_status'] ?? null;
            $rk['_raw_disable_reason'] = $rk['disable_reason'] ?? null;
        }
PHP_BINDING;
$php = str_replace($tokenOnlyNeedle, $tokenOnlyReplacement, $php, $tokenOnlyFilterCount);
if ($tokenOnlyFilterCount !== 1) {
    throw new RuntimeException('token-only RK snapshot filter failed: ' . $tokenOnlyFilterCount);
}

$provisionalNeedle = <<<'PHP_BINDING'
        $rkRows[] = $rk;
    }
PHP_BINDING;
$provisionalReplacement = <<<'PHP_BINDING'
        $rkRows[] = $rk;
    }

    $existingRkIds = [];
    foreach ($rkRows as $rkRow) {
        if (!is_array($rkRow)) continue;
        $existingId = preg_replace('/^act_/', '', trim((string)($rkRow['id'] ?? $rkRow['account_id'] ?? '')));
        if ($existingId !== '') $existingRkIds[$existingId] = true;
    }

    foreach ($bindings as $binding) {
        if (!is_array($binding)) continue;
        $boundBusinessId = trim((string)($binding['business_id'] ?? ''));
        $boundAdAccountId = trim((string)($binding['ad_account_id'] ?? ''));
        if (
            isset($existingRkIds[$boundAdAccountId])
            || !preg_match('/^\d{5,30}$/', $boundBusinessId)
            || !preg_match('/^\d{5,30}$/', $boundAdAccountId)
            || !isset($knownBusinessNames[$boundBusinessId])
        ) continue;

        $boundAccountName = trim((string)($binding['account_name'] ?? ''));
        $rkRows[] = [
            'profile' => $profile,
            'group' => (string)($profileMeta['group'] ?? ''),
            'id' => $boundAdAccountId,
            'account_id' => $boundAdAccountId,
            'name' => $boundAccountName !== '' ? $boundAccountName : ('RK ' . $boundAdAccountId),
            'business_id' => $boundBusinessId,
            'business_name' => $knownBusinessNames[$boundBusinessId],
            'account_status' => null,
            'disable_reason' => null,
            'currency' => '',
            'timezone_name' => '',
            'funding' => null,
            '_provisioned_only' => true,
            '_source' => 'python_worker_binding',
        ];
        $existingRkIds[$boundAdAccountId] = true;
    }

    $rkByBusiness = [];
    foreach ($rkRows as $rkRow) {
        if (!is_array($rkRow)) continue;
        $rkBusinessId = trim((string)($rkRow['business_id'] ?? ''));
        if ($rkBusinessId === '') continue;
        $rkByBusiness[$rkBusinessId][] = $rkRow;
    }
    foreach ($bmRows as $i => $bmRow) {
        if (!is_array($bmRow)) continue;
        $bmId = trim((string)($bmRow['id'] ?? ''));
        if ($bmId === '') continue;
        $rows = $rkByBusiness[$bmId] ?? [];
        $bmRows[$i]['ad_account_count'] = count($rows);
        $bmRows[$i]['accounts'] = $rows;
    }
PHP_BINDING;
$php = str_replace(
    $provisionalNeedle,
    $provisionalReplacement,
    $php,
    $provisionalCount
);
if ($provisionalCount !== 1) {
    throw new RuntimeException('provisional worker RK row patch failed: ' . $provisionalCount);
}

$pagesNeedle = "    \$businesses = MetaEndpoint::peekCachedAsset(\$profile, 'businesses', '');";
$pagesReplacement = "    \$pages = MetaEndpoint::peekCachedAsset(\$profile, 'pages', '');\n" . $pagesNeedle;
$php = str_replace($pagesNeedle, $pagesReplacement, $php, $pagesSnapshotCount);
if ($pagesSnapshotCount !== 1) {
    throw new RuntimeException('profile pages snapshot patch failed: ' . $pagesSnapshotCount);
}

$profileFieldsNeedle = <<<'PHP_CODE'
            'legacy_ready' => $account->isLegacyReady(),
            'bm_count' => count($bmRows),
            'rk_count' => count($rkRows),
            'cache' => $preflight['_cache'] ?? null,
PHP_CODE;
$profileFieldsReplacement = <<<'PHP_CODE'
            'legacy_ready' => $account->isLegacyReady(),
            'token_status' => ($preflight !== null && !empty($preflight['identity']['id'])) ? 'READY' : 'NOT_SYNCED',
            'proxy_configured' => $proxy !== null,
            'proxy_status' => $proxy === null
                ? 'NOT_CONFIGURED'
                : strtoupper((string)(($profileMeta['proxy_health']['status'] ?? '') ?: 'NOT_CHECKED')),
            'permissions_available' => !is_array($preflight)
                ? null
                : (
                    array_key_exists('permissions_available', $preflight)
                        ? (bool)$preflight['permissions_available']
                        : null
                ),
            'ads_management_granted' => (
                is_array($preflight)
                && ($preflight['permissions_available'] ?? null) === true
                && array_key_exists('ads_management_granted', $preflight)
            )
                ? (bool)$preflight['ads_management_granted']
                : null,
            'ads_read_granted' => (
                is_array($preflight)
                && ($preflight['permissions_available'] ?? null) === true
                && array_key_exists('ads_read_granted', $preflight)
            )
                ? (bool)$preflight['ads_read_granted']
                : null,
            'business_management_granted' => (
                is_array($preflight)
                && ($preflight['permissions_available'] ?? null) === true
                && array_key_exists('business_management_granted', $preflight)
            )
                ? (bool)$preflight['business_management_granted']
                : null,
            'pages_count' => count(is_array($pages['data'] ?? null) ? $pages['data'] : []),
            'bm_count' => count($bmRows),
            'rk_count' => count($rkRows),
            'transport' => [
                'network_identity' => 'profile_bound',
                'proxy_configured' => $proxy !== null,
                'session_context' => $account->isLegacyReady(),
                'direct_fallback' => false,
                'context_id' => substr(hash('sha256', $profile . '|' . $account->token), 0, 16),
            ],
            'cache' => $preflight['_cache'] ?? null,
PHP_CODE;
$php = str_replace($profileFieldsNeedle, $profileFieldsReplacement, $php, $profileStatusCount);
if ($profileStatusCount !== 1) {
    throw new RuntimeException('profile readiness snapshot patch failed: ' . $profileStatusCount);
}

file_put_contents($hierarchy, $php);
fwrite(STDERR, "[workspace-sync-fix] metaHierarchy.php patched; live writes + TOKEN/PROXY/PAGES/BM/RK readiness\n");

if (
    strpos($php, 'hierarchy_worker_live_inventory(') === false
    || strpos($php, 'hierarchy_live_snapshot_get($profile)') === false
    || strpos($php, "'ad_account_hints'") === false
) {
    throw new RuntimeException('live Meta sync confirmed-hint contract missing');
}
if (
    strpos($php, 'hierarchy_live_snapshot_put(') === false
    || strpos($php, 'hierarchy_live_snapshot_apply_display(') === false
    || strpos($php, 'hierarchy_profile_snapshot_base(') === false
    || strpos($php, "\$snapshot['profile'] = \$responseProfile;") === false
    || strpos($js, 'SNAPSHOT_NOT_APPLIED') === false
    || strpos($js, 'function applySnapshot(s){') === false
    || strpos($js, 'REMASK_SYNC_RESULT_RECONCILIATION_V1') === false
    || strpos($php, "hierarchy_sync_result_put(") === false
    || strpos($php, "\$action === 'sync_result'") === false
    || strpos($js, 'return true;') === false
    || strpos($php, "'proxy_configured' => \$proxy !== null") === false
    || strpos($php, "'permissions_available' => !is_array(\$preflight)") === false
    || strpos($php, "array_merge(\n                    \$currentTransport") === false
) {
    throw new RuntimeException('workspace sync snapshot contract invariant failed');
}
if (strpos($php, '[remask-private-sync]') !== false) {
    throw new RuntimeException('obsolete pre-live BM hint stage still present');
}
fwrite(STDERR, "[workspace-sync-fix] live Meta sync is authoritative; durable BM/RK state is navigation-hint only\n");
fwrite(STDERR, "[workspace-sync-fix] last confirmed live snapshot persists for display only\n");
fwrite(STDERR, "[workspace-sync-fix] response/applySnapshot contract enforced\n");
fwrite(STDERR, "[workspace-sync-fix] clean sync stabilization ready; no diagnostic probe installed\n");
