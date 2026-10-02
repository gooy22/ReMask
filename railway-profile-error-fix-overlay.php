<?php
/**
 * Final profile-input hardening for the Railway runtime.
 * - Cookies are optional on profile creation.
 * - Add/Edit surfaces the backend JSON error message instead of generic HTTP 400.
 * - Backend logs a correlation id + exception class/message only; secrets are never logged.
 */
$root = '/var/www/html';

$workspacePath = $root . '/scripts/workspace.js';
$workspace = file_get_contents($workspacePath);
if ($workspace === false) {
    fwrite(STDERR, "[profile-error-fix] workspace.js missing\n");
    exit(71);
}

$helper = <<<'JS'
async function profileSaveJson(payload){
  const response=await fetch('ajax/metaProfileManager.php',post(payload));
  const text=await response.text();
  let data={};
  try{data=JSON.parse(text);}catch{}
  let merged=data&&typeof data==='object'?data:{};
  if(merged&&typeof merged.res==='string'){
    try{merged={...merged,...JSON.parse(merged.res)};}catch{}
  }
  if(!response.ok||merged.ok===false||merged.success===false){
    const detail=merged.message||merged.error||`HTTP ${response.status}`;
    const suffix=merged.error_id?` [${merged.error_id}]`:'';
    throw new Error(`${detail}${suffix}`);
  }
  return merged;
}
JS;

if (strpos($workspace, 'async function profileSaveJson(payload)') === false) {
    $marker = 'function prepareAddProfile(){';
    $pos = strpos($workspace, $marker);
    if ($pos === false) {
        fwrite(STDERR, "[profile-error-fix] prepareAddProfile marker missing\n");
        exit(72);
    }
    $workspace = substr($workspace, 0, $pos) . $helper . "\n\n" . substr($workspace, $pos);
}

if (strpos($workspace, 'REMASK_SESSION_REFRESH_UI_V1') === false) {
    $sessionRefreshUi = <<<'JS'

// REMASK_SESSION_REFRESH_UI_V1
function remaskSelectedProfileNameForSessionRefresh(){
  try{
    const rows=(typeof selectedRows==='function')?selectedRows('profiles'):[];
    if(!Array.isArray(rows)||rows.length!==1)return '';
    const row=rows[0]||{};
    return String(
      row.profile || row.profile_name || row.name ||
      row.profile_id || row.id || ''
    ).trim();
  }catch(_){
    return '';
  }
}

async function remaskSessionRefreshRun(){
  const name=remaskSelectedProfileNameForSessionRefresh();
  if(!name){
    if(typeof $==='function'&&$('workspaceStatus')){
      $('workspaceStatus').textContent='Выбери ровно один FB-профиль.';
    }
    return;
  }

  const raw=window.prompt(
    'Профиль '+name+'\nВставь свежий Cookies JSON текущей авторизованной Facebook-сессии. Token и proxy не изменяются.'
  );
  if(raw===null)return;

  const cookies=String(raw||'').trim();
  try{
    if(!cookies)throw new Error('Cookies JSON пустой.');
    let parsed;
    try{
      parsed=JSON.parse(cookies);
    }catch(_){
      throw new Error('Cookies JSON имеет неверный формат.');
    }
    const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
    const names=new Set(
      list
        .filter(function(item){
          return item&&typeof item==='object'&&String(item.value||'').trim()!=='';
        })
        .map(function(item){return String(item.name||'').trim();})
    );
    if(!names.has('c_user')||!names.has('xs')){
      throw new Error('В cookies должны присутствовать c_user и xs.');
    }

    if(typeof $==='function'&&$('workspaceStatus')){
      $('workspaceStatus').textContent='Обновляю FB-сессию профиля '+name+'…';
    }

    const updated=await profileSaveJson({
      action:'session_update',
      name:name,
      cookies:cookies
    });

    if(typeof $==='function'&&$('workspaceStatus')){
      $('workspaceStatus').textContent='FB-сессия сохранена. Синхронизирую '+name+'…';
    }

    const snapshot=await apiJson(
      'ajax/metaHierarchy.php',
      post({action:'sync_profile',profile:name})
    );
    if(typeof applySnapshot==='function')applySnapshot(snapshot);

    if(typeof $==='function'&&$('workspaceStatus')){
      $('workspaceStatus').textContent=
        'FB-сессия '+name+' обновлена'+
        (updated&&updated.token_refreshed?' · Ads Manager token обновлён':'')+
        ' · Meta синхронизирована';
    }
    if(typeof updateSelectionUi==='function')updateSelectionUi();
  }catch(error){
    const message=String((error&&error.message)||error);
    if(typeof $==='function'&&$('workspaceStatus')){
      $('workspaceStatus').textContent='FB session: '+message;
    }
    throw error;
  }
}

function remaskSessionRefreshUpdateButton(){
  const actions=document.getElementById('workspaceActions');
  if(!actions)return;

  let button=document.getElementById('remaskSessionRefreshBtn');
  if(!button){
    button=document.createElement('button');
    button.type='button';
    button.id='remaskSessionRefreshBtn';
    button.className='btn btn-secondary';
    button.textContent='Обновить FB-сессию';
    button.style.display='none';
    button.addEventListener('click',function(event){
      event.preventDefault();
      event.stopPropagation();
      remaskSessionRefreshRun().catch(function(){});
    });
    actions.insertAdjacentElement('afterend',button);
  }

  const profileName=remaskSelectedProfileNameForSessionRefresh();
  const onProfiles=typeof state==='undefined'||String(state.activeTab||'')==='profiles';
  button.style.display=(onProfiles&&profileName)?'inline-flex':'none';
}

(function(){
  const update=function(){remaskSessionRefreshUpdateButton();};
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',update,{once:true});
  }else{
    update();
  }
  document.addEventListener('click',function(){setTimeout(update,0);},true);
  document.addEventListener('change',function(){setTimeout(update,0);},true);
})();
JS;
    $workspace .= "\n" . $sessionRefreshUi . "\n";
}

$mandatoryCookies = <<<'JS'
    if(!cookies||cookies==='[]'||cookies==='{}')throw new Error('Добавь Cookies JSON текущей FB-сессии. Нужны как минимум c_user и xs.');
    let parsed;
    try{parsed=JSON.parse(cookies);}catch{throw new Error('Cookies должны быть валидным JSON.');}
    const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
    const names=new Set(list.filter(x=>x&&typeof x==='object').map(x=>String(x.name||'')));
    if(!names.has('c_user')||!names.has('xs'))throw new Error('В Cookies JSON не найдены обязательные c_user и xs.');
JS;
$optionalCookies = <<<'JS'
    if(cookies){
      let parsed;
      try{parsed=JSON.parse(cookies);}catch{throw new Error('Cookies должны быть валидным JSON.');}
      const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
      if(!Array.isArray(list))throw new Error('Cookies должны быть JSON-массивом или объектом cookies.');
    }
JS;
$workspace = str_replace($mandatoryCookies, $optionalCookies, $workspace, $cookiePatchCount);

$addPattern = <<<'REGEX'
~await\s+apiJson\(\s*['"]ajax/metaProfileManager\.php['"]\s*,\s*post\(\{action\s*:\s*['"]create['"].*?\}\)\s*\);~s
REGEX;
$workspace = preg_replace(
    $addPattern,
    "await profileSaveJson({action:'create',name,token,proxy:$('newProfileProxy').value.trim(),cookies});",
    $workspace,
    -1,
    $addPatchCount
);

$editPattern = <<<'REGEX'
~await\s+apiJson\(\s*['"]ajax/metaProfileManager\.php['"]\s*,\s*post\(\{action\s*:\s*['"]save['"].*?\}\)\s*\);~s
REGEX;
$workspace = preg_replace(
    $editPattern,
    "await profileSaveJson({action:'save',name:p.name,token:$('editToken').value.trim(),cookies,proxy:$('editProxy').value.trim(),clear_proxy:$('editClearProxy').checked?'1':'0'});",
    $workspace,
    -1,
    $editPatchCount
);

$workspace = str_replace(
    'Для импортируемого FB-профиля сохрани token + session cookies вместе. ReMask не возвращает token/cookies обратно в браузер после сохранения.',
    'Access token обязателен. Cookies необязательны и используются только как дополнительный session context. ReMask не возвращает token/cookies обратно в браузер после сохранения.',
    $workspace
);

if ($cookiePatchCount < 1 || $addPatchCount < 1 || $editPatchCount < 1) {
    fwrite(STDERR, "[profile-error-fix] expected Workspace blocks were not patched: cookies={$cookiePatchCount} add={$addPatchCount} edit={$editPatchCount}\n");
    exit(73);
}
$invalidSelector = '$(' . chr(92) . "'";
if (strpos($workspace, $invalidSelector) !== false) {
    fwrite(STDERR, "[profile-error-fix] invalid escaped selector syntax detected in workspace.js\n");
    exit(76);
}
file_put_contents($workspacePath, $workspace);

$workspacePagePath = $root . '/workspace.php';
$workspacePage = file_get_contents($workspacePagePath);
if ($workspacePage === false) {
    fwrite(STDERR, "[profile-error-fix] workspace.php missing\n");
    exit(77);
}
$workspaceScriptPattern = <<<'REGEX'
#scripts/workspace\.js(?:\?[^"']*)?#
REGEX;
$workspacePage = preg_replace(
    $workspaceScriptPattern,
    'scripts/workspace.js?v=20260928-session-refresh-v180',
    $workspacePage,
    1,
    $workspaceScriptTagCount
);
if ($workspacePage === null || $workspaceScriptTagCount !== 1) {
    fwrite(STDERR, "[profile-error-fix] workspace.js cache-bust tag patch failed: " . (string)$workspaceScriptTagCount . "\n");
    exit(78);
}
file_put_contents($workspacePagePath, $workspacePage);
fwrite(STDERR, "[profile-error-fix] Workspace token-only add + detailed save errors + JS cache bust ready\n");

$managerPath = $root . '/ajax/metaProfileManager.php';
$manager = file_get_contents($managerPath);
if ($manager === false) {
    fwrite(STDERR, "[profile-error-fix] metaProfileManager.php missing\n");
    exit(74);
}
$oldCatch = <<<'PHP_CODE'
} catch (Throwable $e) {
    rmx_pm_out(['ok'=>false,'success'=>false,'error'=>'PROFILE_MANAGER_FAILED','message'=>$e->getMessage()], 400);
}
PHP_CODE;
$newCatch = <<<'PHP_CODE'
} catch (Throwable $e) {
    $errorId = 'PM-' . strtoupper(substr(hash('sha256', microtime(true) . '|' . random_int(1, PHP_INT_MAX)), 0, 10));
    $safeAction = isset($action) ? (string)$action : 'unknown';
    $safeInput = isset($input) && is_array($input) ? $input : [];
    $flags = [
        'name' => rmx_pm_find_scalar($safeInput, ['name','profile_name','label','fb_id','profile_id','account_id']) !== '',
        'token' => rmx_pm_find_scalar($safeInput, ['token','access_token','accessToken','fb_token','meta_token']) !== '',
        'proxy' => rmx_pm_find_scalar($safeInput, ['proxy','proxy_raw','proxyString','proxy_string']) !== '',
        'cookies' => array_key_exists('cookies', $safeInput) || array_key_exists('cookie', $safeInput) || array_key_exists('cookies_json', $safeInput),
    ];
    error_log('[profile-manager][' . $errorId . '] ' . get_class($e) . ': ' . $e->getMessage() . ' action=' . $safeAction . ' fields=' . json_encode($flags));
    rmx_pm_out(['ok'=>false,'success'=>false,'error'=>'PROFILE_MANAGER_FAILED','error_id'=>$errorId,'message'=>$e->getMessage()], 400);
}
PHP_CODE;
if (strpos($manager, $oldCatch) === false) {
    fwrite(STDERR, "[profile-error-fix] manager catch marker missing\n");
    exit(75);
}
$manager = str_replace($oldCatch, $newCatch, $manager, $catchPatchCount);
file_put_contents($managerPath, $manager);
fwrite(STDERR, "[profile-error-fix] profile manager correlation logging ready\n");
