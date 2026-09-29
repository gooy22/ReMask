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

  const businesses=(Array.isArray(s.businesses)?s.businesses:[])
    .filter(x=>x&&typeof x==='object')
    .map(x=>({...x,profile:String(x.profile||p)}));
  const accounts=(Array.isArray(s.ad_accounts)?s.ad_accounts:[])
    .filter(x=>x&&typeof x==='object')
    .map(x=>({...x,profile:String(x.profile||p)}));

  state.inventory.profiles=state.inventory.profiles
    .filter(x=>String(x.name||x.profile||'')!==p)
    .concat([normalizedProfile]);
  state.inventory.businesses=state.inventory.businesses
    .filter(x=>String(x.profile||'')!==p)
    .concat(businesses);
  state.inventory.ad_accounts=state.inventory.ad_accounts
    .filter(x=>String(x.profile||'')!==p)
    .concat(accounts);
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
    let raw='';
    if(e&&typeof e==='object'){
      if(e.message)raw=String(e.message);
      else if(e.error)raw=String(e.error);
    }
    if(!raw)raw=String(e||'Unknown sync error');
    return raw.replace(/access_token=[^&\\s]+/ig,'access_token=[redacted]');
  };
  const classifySyncError=(e)=>{
    const message=errorText(e);
    const s=message.toLowerCase();
    let kind='PRIVATE_SYNC';
    if(/rate.?limit|too many|code[^0-9]*(4|17|32|613)\\b/.test(s))kind='RATE_LIMIT';
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

  // REMASK_SYNC_DIRECT_FETCH_V1
  // The generic apiJson helper has its own short transport deadline in the
  // legacy Workspace runtime. A healthy private Meta sync can legitimately
  // take >10s, so sync requests must use a dedicated long-lived fetch instead
  // of being aborted by that helper while the server is still working.
  const syncApiJson=async(url,body)=>{
    const controller=new AbortController();
    const timer=setTimeout(()=>controller.abort(),65000);
    try{
      const response=await fetch(url,{
        method:'POST',
        body,
        credentials:'same-origin',
        cache:'no-store',
        signal:controller.signal,
        headers:{'X-Requested-With':'XMLHttpRequest'}
      });
      const raw=await response.text();
      let data=null;
      try{
        data=raw?JSON.parse(raw):{};
      }catch(_){
        throw new Error('Private Meta sync returned invalid JSON (HTTP '+response.status+')');
      }

      if(data&&typeof data==='object'&&Object.prototype.hasOwnProperty.call(data,'res')){
        const wrapped=data.res;
        if(typeof wrapped==='string'){
          try{data=JSON.parse(wrapped);}catch(_){data=wrapped;}
        }else if(wrapped&&typeof wrapped==='object'){
          data=wrapped;
        }
      }

      if(!response.ok){
        const detail=(data&&typeof data==='object'&&(data.error||data.detail||data.message))
          ? String(data.error||data.detail||data.message)
          : ('HTTP '+response.status);
        throw new Error(detail);
      }
      if(data&&typeof data==='object'&&data.error){
        throw new Error(String(data.error));
      }
      return data;
    }catch(e){
      if(e&&e.name==='AbortError'){
        throw new Error('Private Meta sync timeout after 65000ms');
      }
      throw e;
    }finally{
      clearTimeout(timer);
    }
  };

  const syncProfileSafe=async(profile)=>{
    try{
      const d=await withTimeout(syncApiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile})));
      const applied=applySnapshot(d);
      if(d && d.sync_complete===true && !applied){
        return {
          error:'Live Meta sync returned success but Workspace snapshot was not applied',
          error_kind:'SNAPSHOT_NOT_APPLIED',
          profile
        };
      }
      if(d && d.sync_complete===false){
        return {
          error:String(d.sync_error||'Private Business Suite inventory was inconclusive'),
          error_kind:String(d.sync_error_kind||'PRIVATE_INCONCLUSIVE'),
          profile
        };
      }
      return d;
    }catch(e){
      const x=classifySyncError(e);
      return {error:x.message,error_kind:x.kind,profile};
    }
  };

  const syncBusinessSafe=async(row)=>{
    try{
      const d=await withTimeout(syncApiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile:row.profile,business_id:row.id})));
      const applied=applySnapshot(d);
      if(d && d.sync_complete===true && !applied){
        return {
          error:'Live Meta sync returned success but Workspace snapshot was not applied',
          error_kind:'SNAPSHOT_NOT_APPLIED',
          profile:row.profile,
          business_id:row.id
        };
      }
      if(d && d.sync_complete===false){
        return {
          error:String(d.sync_error||'Private Business Suite inventory was inconclusive'),
          error_kind:String(d.sync_error_kind||'PRIVATE_INCONCLUSIVE'),
          profile:row.profile,
          business_id:row.id
        };
      }
      return d;
    }catch(e){
      const x=classifySyncError(e);
      return {error:x.message,error_kind:x.kind,profile:row.profile,business_id:row.id};
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
        const x=classifySyncError(e);
        results=[{error:x.message,error_kind:x.kind}];
      }
    }

    const failures=results
      .filter(x=>x&&x.error)
      .map(x=>[x.profile,x.business_id,`[${x.error_kind||'META_API'}]`,x.error].filter(Boolean).join(': '));
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
    array $knownAdAccountHints = []
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
    array $profileRow
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

        $all[$profile] = [
            'profile' => $persistedProfile,
            'businesses' => array_values($cleanBusinesses),
            'ad_accounts' => array_values($cleanAccounts),
            'updated_at' => time(),
            'source' => 'last_confirmed_live_meta_inventory',
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

    $snapshot['businesses'] = $businesses;
    $snapshot['businesses_count'] = count($businesses);
    $snapshot['ad_accounts'] = $adAccounts;
    $snapshot['ad_accounts_count'] = count($adAccounts);

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
        ];
        if (is_array($snapshot['profiles'] ?? null)) {
            $snapshot['profiles'][] = $freshProfile;
        }
    }

    $snapshot['profile'] = $freshProfile;
    $snapshot['last_confirmed_live_meta_at'] = (int)($saved['updated_at'] ?? 0);
    $snapshot['display_source'] = 'last_confirmed_live_meta_inventory';
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
    return hierarchy_live_snapshot_apply_display($profile, $snapshot);
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
    if ($action === 'sync_profile') {
        $profile = trim((string)($input['profile'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');

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

        try {
            $liveInventory = hierarchy_worker_live_inventory(
                $profile,
                array_keys($knownBusinessIds),
                array_map(
                    static fn($ids) => array_keys((array)$ids),
                    $knownAdAccountHints
                )
            );
        } catch (Throwable $liveInventoryError) {
            $message = trim((string)$liveInventoryError->getMessage());
            if (
                stripos($message, 'SESSION_EXPIRED') !== false
                || stripos($message, 'CHECKPOINT_REQUIRED') !== false
                || stripos($message, 'TWO_FACTOR_REQUIRED') !== false
            ) {
                throw new RuntimeException('FB_SESSION_EXPIRED: ' . $message, 0, $liveInventoryError);
            }
            throw new RuntimeException('PRIVATE_SYNC_FAILED: ' . $message, 0, $liveInventoryError);
        }

        $businessRows = [];
        $adAccountRows = [];
        $seenBusiness = [];
        $seenAccount = [];

        foreach ((array)($liveInventory['businesses'] ?? []) as $liveBusiness) {
            if (!is_array($liveBusiness)) continue;
            $businessId = trim((string)($liveBusiness['id'] ?? ''));
            if (!preg_match('/^\\d{5,30}$/', $businessId)) continue;

            $businessName = trim((string)($liveBusiness['name'] ?? $businessId));
            if ($businessName === '') $businessName = $businessId;
            $accountsForBusiness = [];

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

        foreach ((array)($liveInventory['warnings'] ?? []) as $warning) {
            if (is_scalar($warning) && trim((string)$warning) !== '') {
                $syncWarnings[] = trim((string)$warning);
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

        $snapshot = $existingSnapshot;
        $snapshot['businesses'] = array_values($businessRows);
        $snapshot['businesses_count'] = count($businessRows);
        $snapshot['ad_accounts'] = array_values($adAccountRows);
        $snapshot['ad_accounts_count'] = count($adAccountRows);
        $syncComplete = $liveReady;

        $snapshot['sync_source'] = 'private_business_suite_browser';
        $snapshot['live_inventory_available'] = $liveReady;
        $snapshot['confirmed_worker_bindings'] = $workerConfirmedCount;
        $snapshot['graph_preflight_available'] = false;
        $snapshot['sync_complete'] = $syncComplete;
        if (!$syncComplete) {
            $snapshot['sync_error_kind'] = 'PRIVATE_INCONCLUSIVE';
            $snapshot['sync_error'] = 'Private Business Suite inventory did not confirm live BM/RK state.';
        }

        $responseProfile = null;
        if (is_array($snapshot['profiles'] ?? null)) {
            foreach ($snapshot['profiles'] as $i => $profileRow) {
                if (!is_array($profileRow)) continue;
                $rowName = trim((string)($profileRow['name'] ?? $profileRow['profile'] ?? ''));
                if ($rowName !== '' && $rowName !== $profile) continue;
                $snapshot['profiles'][$i]['name'] = $rowName !== '' ? $rowName : $profile;
                $snapshot['profiles'][$i]['synced'] = true;
                $snapshot['profiles'][$i]['bm_count'] = count($businessRows);
                $snapshot['profiles'][$i]['rk_count'] = count($adAccountRows);
                $snapshot['profiles'][$i]['ad_accounts_count'] = count($adAccountRows);
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
                'synced' => true,
                'bm_count' => count($businessRows),
                'rk_count' => count($adAccountRows),
                'ad_accounts_count' => count($adAccountRows),
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

        if ($liveReady) {
            hierarchy_live_snapshot_put(
                $profile,
                $businessRows,
                $adAccountRows,
                $responseProfile
            );
            $snapshot['last_confirmed_live_meta_at'] = time();
            $snapshot['display_source'] = 'live_meta_inventory';
        }

        hierarchy_activity([
            'action' => 'sync_profile',
            'entity_type' => 'profile',
            'entity_id' => $profile,
            'profile_name' => $profile,
            'summary' => 'Приватная синхронизация FB-профиля завершена',
            'details' => [
                'sync_source' => 'private_business_suite_browser',
                'live_ready' => $liveReady,
                'worker_confirmed_bindings' => $workerConfirmedCount,
                'sync_complete' => $syncComplete,
                'businesses' => count($businessRows),
                'ad_accounts' => count($adAccountRows),
                'warnings' => $syncWarnings,
            ],
        ]);

        if ($syncWarnings !== []) {
            $snapshot['sync_warnings'] = array_values(array_unique($syncWarnings));
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
        if ((string)($bm['source'] ?? '') === 'python_worker_binding') {
            // Worker proved CREATE and the exact BM relation, but Meta's
            // owned/client edge has not propagated yet. Do not display a
            // transient token-level account_status=2 as a real disabled RK.
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
        $existingId = trim((string)($rkRow['id'] ?? ''));
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
fwrite(STDERR, "[workspace-sync-fix] live Meta sync is authoritative; no pre-live binding/state lookup\n");
fwrite(STDERR, "[workspace-sync-fix] last confirmed live snapshot persists for display only\n");
fwrite(STDERR, "[workspace-sync-fix] response/applySnapshot contract enforced\n");
fwrite(STDERR, "[workspace-sync-fix] clean sync stabilization ready; no diagnostic probe installed\n");
