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

$oldAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || !p.proxy_configured || (ps!==''&&ps!=='LIVE') || !p.ads_management_granted || p.bm_count===0 || p.rk_count===0; }";
$newAttention = "function profileAttention(p){ const ps=String(p.proxy_health?.status||'').toUpperCase(); return !p.synced || (p.proxy_configured && ps!==''&&ps!=='LIVE') || p.ads_management_granted===false; }";
$count = 0;
$js = str_replace($oldAttention, $newAttention, $js, $count);
if ($count !== 1) {
    throw new RuntimeException('profileAttention patch failed: ' . $count);
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
    let kind='META_API';
    if(/rate.?limit|too many|code[^0-9]*(4|17|32|613)\\b/.test(s))kind='RATE_LIMIT';
    else if(/\\b407\\b|proxy authentication|proxy auth/.test(s))kind='PROXY_AUTH';
    else if(/transport error|curl|could not resolve|connection timed out|connection refused|ssl connect/.test(s))kind='TRANSPORT';
    else if(/access token.*(invalid|expired)|token.*(invalid|expired)|session.*expired|code[^0-9]*190\\b|\\(#190\\)/.test(s))kind='TOKEN_INVALID';
    else if(/oauth.*code[^0-9]*1\\b|code=1\\b|meta graph .* http 400 code=1\\b/.test(s))kind='META_REQUEST';
    else if(/ads_management|ads_read|business_management|permission|permissions|not authorized|code[^0-9]*(10|200)\\b/.test(s))kind='PERMISSION';
    else if(/meta_sync_preflight_failed/.test(s))kind='META_PREFLIGHT';
    else if(/http 5\\d\\d|temporar|transient/.test(s))kind='META_TEMPORARY';
    return {kind,message};
  };

  const withTimeout=async(promise,ms=35000)=>{
    let timer=null;
    try{
      return await Promise.race([
        promise,
        new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Meta request timeout after '+ms+'ms')),ms);})
      ]);
    }finally{
      if(timer!==null)clearTimeout(timer);
    }
  };

  const syncProfileSafe=async(profile)=>{
    try{
      const d=await withTimeout(apiJson('ajax/metaHierarchy.php',post({action:'sync_profile',profile})));
      applySnapshot(d);
      return d;
    }catch(e){
      const x=classifySyncError(e);
      return {error:x.message,error_kind:x.kind,profile};
    }
  };

  const syncBusinessSafe=async(row)=>{
    try{
      const d=await withTimeout(apiJson('ajax/metaHierarchy.php',post({action:'sync_business',profile:row.profile,business_id:row.id})));
      applySnapshot(d);
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
$php = str_replace(
    $hierarchyHelperSignature,
    $hierarchyHelpers . "\n\n" . $hierarchyHelperSignature,
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

        // REMASK_SYNC_OPTIONAL_GRAPH_PREFLIGHT_V1
        // Workspace provisioning is browser/worker-backed. Official Graph /me
        // is useful enrichment when this token supports it, but it must not be
        // a hard sync gate. Some valid Ads Manager/session tokens return
        // OAuthException code=1 "Invalid request" for /me.
        $preflight = [
            'identity' => [],
            'permissions' => [],
            'ad_accounts' => ['data' => []],
        ];
        $graphPreflightAvailable = false;
        $graphPreflightWarning = '';
        try {
            $preflight = MetaEndpoint::cachedPreflight($profile, true);
            $graphPreflightAvailable = true;
        } catch (Throwable $preflightError) {
            $safeMessage = preg_replace(
                '/access_token=[^&\\s]+/i',
                'access_token=[redacted]',
                (string)$preflightError->getMessage()
            );
            error_log(
                '[remask-sync] profile=' . $profile .
                ' stage=optional_graph_preflight class=' . get_class($preflightError) .
                ' message=' . mb_substr((string)$safeMessage, 0, 1200)
            );

            $lowerPreflightError = strtolower((string)$safeMessage);
            $tokenActuallyInvalid = (
                preg_match('/(?:code=190\\b|\\(#190\\)|access token[^.]{0,80}(?:invalid|expired)|token[^.]{0,80}(?:invalid|expired)|session[^.]{0,80}expired)/i', (string)$safeMessage) === 1
            );
            if ($tokenActuallyInvalid) {
                throw new RuntimeException(
                    'META_TOKEN_INVALID: ' . (string)$safeMessage,
                    0,
                    $preflightError
                );
            }

            $graphPreflightWarning = 'Official Graph preflight unavailable; worker/browser-confirmed inventory retained';
        }

        // A saved token and saved browser cookies must represent the same
        // Facebook user. Otherwise me/adaccounts can silently inject RK from a
        // different account into this Workspace profile.
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        $savedAccount = $store->getAccountByName($profile);
        $cookieUserId = '';
        if ($savedAccount instanceof FbAccount) {
            foreach ((array)$savedAccount->cookies as $cookie) {
                if (!is_array($cookie)) continue;
                if ((string)($cookie['name'] ?? '') !== 'c_user') continue;
                $cookieUserId = trim((string)($cookie['value'] ?? ''));
                if ($cookieUserId !== '') break;
            }
        }
        $graphUserId = $graphPreflightAvailable
            ? trim((string)($preflight['identity']['id'] ?? ''))
            : '';
        if (
            $graphPreflightAvailable
            && $cookieUserId !== ''
            && $graphUserId !== ''
            && !hash_equals($cookieUserId, $graphUserId)
        ) {
            MetaEndpoint::invalidateProfileCache($profile);
            $snapshot = hierarchy_profile_snapshot($profile);
            $snapshot['sync_source'] = 'profile_identity_guard';
            $snapshot['identity_mismatch'] = true;
            $snapshot['sync_warnings'] = [
                'PROFILE_IDENTITY_MISMATCH: token and saved FB session belong to different users. Cached BM/RK data was cleared; update token or cookies before sync.',
            ];
            MetaEndpoint::ok($snapshot);
        }

        // REMASK_BM_BOUND_RK_SYNC_V1 REMASK_BM_BOUND_RK_SYNC_V2
        // me/adaccounts may update before owned_ad_accounts/client_ad_accounts
        // after a fresh CREATE. Keep an RK when Meta's direct object already
        // points at one of this profile's Business Managers; otherwise a real
        // newly-created RK disappears from Workspace during edge propagation.
        $verifiedBusinessAdAccounts = [];
        $knownBusinesses = [];
        $syncWarnings = [];

        $existingSnapshot = hierarchy_profile_snapshot($profile);
        foreach ((array)($existingSnapshot['businesses'] ?? []) as $existingBusiness) {
            if (!is_array($existingBusiness)) continue;
            $existingBusinessId = trim((string)($existingBusiness['id'] ?? ''));
            if ($existingBusinessId === '') continue;
            $knownBusinesses[$existingBusinessId] = [
                'id' => $existingBusinessId,
                'name' => trim((string)($existingBusiness['name'] ?? $existingBusinessId)),
                '_source' => 'existing_workspace_snapshot',
            ];
        }
        if ($graphPreflightWarning !== '') {
            $syncWarnings[] = $graphPreflightWarning;
        }
        foreach ((array)($preflight['_preflight_warnings'] ?? []) as $preflightWarning) {
            if (!is_array($preflightWarning)) continue;
            $stage = trim((string)($preflightWarning['stage'] ?? 'preflight'));
            $message = trim((string)($preflightWarning['message'] ?? 'unavailable'));
            $syncWarnings[] = 'Meta ' . $stage . ' unavailable: ' . $message;
        }
        if (!empty($preflight['ad_accounts']['_funding_enrichment_warning'])) {
            $syncWarnings[] = 'RK funding/payment metadata unavailable; RK list kept';
        }
        if (!empty($preflight['ad_accounts']['_adaccounts_fallback'])) {
            $fallbackName = trim((string)($preflight['ad_accounts']['_adaccounts_fallback']['field_set'] ?? 'compat'));
            $syncWarnings[] = 'RK sync used compatible Meta field set: ' . $fallbackName;
        }
        try {
            $businesses = MetaEndpoint::cachedAsset($profile, 'businesses', '', true);
            foreach (($businesses['data'] ?? []) as $business) {
                if (!is_array($business)) continue;
                $businessId = trim((string)($business['id'] ?? ''));
                if ($businessId === '') continue;
                $knownBusinesses[$businessId] = [
                    'id' => $businessId,
                    'name' => trim((string)($business['name'] ?? $businessId)),
                ];
                try {
                    $businessAccounts = MetaEndpoint::cachedAsset($profile, 'business_ad_accounts', $businessId, true);
                    foreach ((array)($businessAccounts['data'] ?? []) as $businessAccount) {
                        if (!is_array($businessAccount)) continue;
                        $rkId = trim((string)($businessAccount['id'] ?? ''));
                        if ($rkId === '') continue;
                        $row = $businessAccount;
                        $row['profile'] = $profile;
                        $row['business_id'] = $businessId;
                        if (trim((string)($row['business_name'] ?? '')) === '') {
                            $row['business_name'] = (string)($knownBusinesses[$businessId]['name'] ?? $businessId);
                        }
                        $verifiedBusinessAdAccounts[$rkId] = $row;
                    }
                    foreach ((array)($businessAccounts['_edge_warnings'] ?? []) as $edgeWarning) {
                        if (!is_array($edgeWarning)) continue;
                        $edgeName = trim((string)($edgeWarning['edge'] ?? 'business_ad_accounts'));
                        $edgeKind = trim((string)($edgeWarning['kind'] ?? 'edge_unavailable'));
                        if ($edgeKind === 'funding_metadata_unavailable') {
                            $syncWarnings[] = 'BM ' . $businessId . ': ' . $edgeName . ' funding metadata unavailable';
                        } else {
                            $syncWarnings[] = 'BM ' . $businessId . ': ' . $edgeName . ' unavailable';
                        }
                    }
                } catch (Throwable $businessAccountError) {
                    $syncWarnings[] = 'BM ' . $businessId . ': RK enrichment unavailable';
                }
            }
        } catch (Throwable $businessError) {
            $syncWarnings[] = 'Live Business Manager enrichment unavailable; existing/worker-confirmed BM inventory retained';
        }

        // Meta's direct ad-account object can carry the BM relation before the
        // BM-owned/client edge catches up. Treat that relation as a valid
        // read-only ownership proof when it points at a currently returned BM.
        foreach ((array)($preflight['ad_accounts']['data'] ?? []) as $directAccount) {
            if (!is_array($directAccount)) continue;
            $directId = trim((string)($directAccount['id'] ?? ''));
            if ($directId === '' || isset($verifiedBusinessAdAccounts[$directId])) continue;

            $directBusiness = $directAccount['business'] ?? null;
            $directBusinessId = '';
            $directBusinessName = '';
            if (is_array($directBusiness)) {
                $directBusinessId = trim((string)($directBusiness['id'] ?? ''));
                $directBusinessName = trim((string)($directBusiness['name'] ?? ''));
            }
            if ($directBusinessId === '') {
                $directBusinessId = trim((string)($directAccount['business_id'] ?? ''));
            }

            if ($directBusinessId === '' || !isset($knownBusinesses[$directBusinessId])) continue;

            $row = $directAccount;
            $row['profile'] = $profile;
            $row['business_id'] = $directBusinessId;
            $row['business_name'] = $directBusinessName !== ''
                ? $directBusinessName
                : (string)($knownBusinesses[$directBusinessId]['name'] ?? $directBusinessId);
            $row['_business_edge'] = 'direct_business_reference';
            $verifiedBusinessAdAccounts[$directId] = $row;
        }

        // Worker state is authoritative for every confirmed BM->RK relation,
        // not just the newest pair for the FB profile. This is required for
        // mass Add RK from the Businesses tab where one profile can own many BM.
        try {
            $workerState = hierarchy_worker_state($profile);
            $workerBindings = $workerState['ad_account_bindings'] ?? null;
            if (!is_array($workerBindings)) {
                $workerBindings = [];
            }

            // Backward-compatible fallback for workers that only expose the
            // newest relation at top level.
            if ($workerBindings === []) {
                $legacyBusinessId = trim((string)($workerState['business_id'] ?? ''));
                $legacyAdAccountId = trim((string)($workerState['ad_account_id'] ?? ''));
                if (
                    preg_match('/^\d{5,30}$/', $legacyBusinessId)
                    && preg_match('/^\d{5,30}$/', $legacyAdAccountId)
                ) {
                    $workerBindings[] = [
                        'business_id' => $legacyBusinessId,
                        'ad_account_id' => $legacyAdAccountId,
                        'account_name' => '',
                    ];
                }
            }

            foreach ($workerBindings as $workerBinding) {
                if (!is_array($workerBinding)) continue;
                $workerBusinessId = trim((string)($workerBinding['business_id'] ?? ''));
                $workerAdAccountId = trim((string)($workerBinding['ad_account_id'] ?? ''));
                $workerAccountName = trim((string)($workerBinding['account_name'] ?? ''));
                if (
                    !preg_match('/^\d{5,30}$/', $workerBusinessId)
                    || !preg_match('/^\d{5,30}$/', $workerAdAccountId)
                ) continue;

                if (!isset($knownBusinesses[$workerBusinessId])) {
                    $knownBusinesses[$workerBusinessId] = [
                        'id' => $workerBusinessId,
                        'name' => $workerBusinessId,
                        '_source' => 'python_worker_confirmed_binding',
                    ];
                }

                hierarchy_binding_put(
                    $profile,
                    $workerBusinessId,
                    $workerAdAccountId,
                    $workerAccountName
                );

                if (isset($verifiedBusinessAdAccounts[$workerAdAccountId])) {
                    continue;
                }

                $workerDirect = null;
                foreach ((array)($preflight['ad_accounts']['data'] ?? []) as $directAccount) {
                    if (!is_array($directAccount)) continue;
                    if (trim((string)($directAccount['id'] ?? '')) === $workerAdAccountId) {
                        $workerDirect = $directAccount;
                        break;
                    }
                }

                if (is_array($workerDirect)) {
                    $workerDirect['profile'] = $profile;
                    $workerDirect['business_id'] = $workerBusinessId;
                    $workerDirect['business_name'] = (string)($knownBusinesses[$workerBusinessId]['name'] ?? $workerBusinessId);
                    $workerDirect['_provisioned_only'] = true;
                    $workerDirect['_raw_account_status'] = $workerDirect['account_status'] ?? null;
                    $workerDirect['_raw_disable_reason'] = $workerDirect['disable_reason'] ?? null;
                    $workerDirect['_business_edge'] = 'python_worker_confirmed_binding';
                    $verifiedBusinessAdAccounts[$workerAdAccountId] = $workerDirect;
                } else {
                    $verifiedBusinessAdAccounts[$workerAdAccountId] = [
                        'profile' => $profile,
                        'id' => $workerAdAccountId,
                        'account_id' => $workerAdAccountId,
                        'name' => $workerAccountName !== '' ? $workerAccountName : ('RK ' . $workerAdAccountId),
                        'business_id' => $workerBusinessId,
                        'business_name' => (string)($knownBusinesses[$workerBusinessId]['name'] ?? $workerBusinessId),
                        'account_status' => null,
                        'disable_reason' => null,
                        'currency' => '',
                        'timezone_name' => '',
                        'funding' => null,
                        '_provisioned_only' => true,
                        '_business_edge' => 'python_worker_confirmed_binding',
                    ];
                }
            }
        } catch (Throwable $workerStateError) {
            $syncWarnings[] = 'Worker provisioning state unavailable; existing inventory retained';
        }

        if (!$graphPreflightAvailable) {
            foreach ((array)($existingSnapshot['ad_accounts'] ?? []) as $existingAdAccount) {
                if (!is_array($existingAdAccount)) continue;
                $existingAdAccountId = trim((string)($existingAdAccount['id'] ?? $existingAdAccount['account_id'] ?? ''));
                if ($existingAdAccountId === '' || isset($verifiedBusinessAdAccounts[$existingAdAccountId])) continue;
                $existingBusinessId = trim((string)($existingAdAccount['business_id'] ?? ''));
                if ($existingBusinessId === '') continue;
                $existingAdAccount['_sync_preserved'] = true;
                $existingAdAccount['_business_edge'] = (string)($existingAdAccount['_business_edge'] ?? 'existing_workspace_snapshot');
                $verifiedBusinessAdAccounts[$existingAdAccountId] = $existingAdAccount;
            }
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

        $directIds = [];
        foreach ((array)($preflight['ad_accounts']['data'] ?? []) as $directAccount) {
            if (!is_array($directAccount)) continue;
            $directId = trim((string)($directAccount['id'] ?? ''));
            if ($directId !== '') $directIds[$directId] = true;
        }
        $filteredDirect = array_values(array_diff(
            array_keys($directIds),
            array_keys($verifiedBusinessAdAccounts)
        ));
        // Token-level RK outside the currently returned BM inventory are not
        // an error. Keep only the count for diagnostics instead of alarming
        // the Workspace user with a sync warning.
        $snapshot['token_only_ad_accounts_count'] = count($filteredDirect);

        // Replace the broad me/adaccounts view with exact BM-bound inventory.
        // This is the data Workspace renders and therefore prevents phantom RK.
        $snapshot['ad_accounts'] = array_values($verifiedBusinessAdAccounts);
        $snapshot['ad_accounts_count'] = count($verifiedBusinessAdAccounts);

        $accountsByBusiness = [];
        foreach ($verifiedBusinessAdAccounts as $verifiedRow) {
            if (!is_array($verifiedRow)) continue;
            $verifiedBusinessId = trim((string)($verifiedRow['business_id'] ?? ''));
            if ($verifiedBusinessId === '') continue;
            $accountsByBusiness[$verifiedBusinessId][] = $verifiedRow;
        }
        if (is_array($snapshot['businesses'] ?? null)) {
            foreach ($snapshot['businesses'] as $i => $businessRow) {
                if (!is_array($businessRow)) continue;
                $businessRowId = trim((string)($businessRow['id'] ?? ''));
                if ($businessRowId === '') continue;
                $rowsForBusiness = $accountsByBusiness[$businessRowId] ?? [];
                $snapshot['businesses'][$i]['ad_account_count'] = count($rowsForBusiness);
                $snapshot['businesses'][$i]['accounts'] = $rowsForBusiness;
            }
        }

        if (is_array($snapshot['profiles'] ?? null)) {
            foreach ($snapshot['profiles'] as $i => $profileRow) {
                if (!is_array($profileRow)) continue;
                $rowName = trim((string)($profileRow['name'] ?? $profileRow['profile'] ?? ''));
                if ($rowName !== '' && $rowName !== $profile) continue;
                $snapshot['profiles'][$i]['rk_count'] = count($verifiedBusinessAdAccounts);
                $snapshot['profiles'][$i]['ad_accounts_count'] = count($verifiedBusinessAdAccounts);
            }
        }
        $snapshot['sync_source'] = $graphPreflightAvailable
            ? 'business_manager_bound_ad_accounts'
            : 'worker_browser_confirmed_inventory';
        $snapshot['graph_preflight_available'] = $graphPreflightAvailable;
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
            'proxy_status' => $proxy === null
                ? 'NOT_CONFIGURED'
                : strtoupper((string)(($profileMeta['proxy_health']['status'] ?? '') ?: 'NOT_CHECKED')),
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

fwrite(STDERR, "[workspace-sync-fix] clean sync stabilization ready; no diagnostic probe installed\n");
