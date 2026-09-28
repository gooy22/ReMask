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
  }catch(_){return '';}
}

function remaskSessionRefreshEnsureModal(){
  let modal=document.getElementById('remaskSessionRefreshModal');
  if(modal)return modal;

  modal=document.createElement('div');
  modal.id='remaskSessionRefreshModal';
  modal.style.cssText='display:none;position:fixed;inset:0;z-index:100050;background:rgba(0,0,0,.58);align-items:center;justify-content:center;padding:18px;';
  modal.innerHTML=
    '<div style="width:min(680px,96vw);max-height:90vh;overflow:auto;background:#17191d;border:1px solid rgba(255,255,255,.14);border-radius:14px;padding:18px;box-shadow:0 20px 70px rgba(0,0,0,.45)">'+
      '<div style="font-size:18px;font-weight:700;margin-bottom:6px">Обновить FB-сессию</div>'+
      '<div id="remaskSessionRefreshProfile" style="font-size:13px;opacity:.72;margin-bottom:12px"></div>'+
      '<div style="font-size:13px;opacity:.8;margin-bottom:8px">Вставь свежий Cookies JSON из уже авторизованной Facebook-сессии. Token и proxy не изменяются.</div>'+
      '<textarea id="remaskSessionRefreshCookies" spellcheck="false" placeholder='[{"name":"c_user","value":"..."},{"name":"xs","value":"..."}]' style="box-sizing:border-box;width:100%;min-height:210px;resize:vertical;background:#0f1114;color:#fff;border:1px solid rgba(255,255,255,.14);border-radius:10px;padding:12px;font:12px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace"></textarea>'+
      '<div id="remaskSessionRefreshError" style="display:none;color:#ff6b6b;font-size:13px;margin-top:10px"></div>'+
      '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:14px">'+
        '<button type="button" id="remaskSessionRefreshCancel" style="padding:9px 14px;border-radius:9px;border:1px solid rgba(255,255,255,.15);background:transparent;color:inherit">Отмена</button>'+
        '<button type="button" id="remaskSessionRefreshSave" style="padding:9px 14px;border-radius:9px;border:0;background:#fff;color:#111;font-weight:700">Обновить и синхронизировать</button>'+
      '</div>'+
    '</div>';
  document.body.appendChild(modal);

  const close=()=>{modal.style.display='none';};
  modal.querySelector('#remaskSessionRefreshCancel').addEventListener('click',close);
  modal.addEventListener('click',e=>{if(e.target===modal)close();});

  modal.querySelector('#remaskSessionRefreshSave').addEventListener('click',async()=>{
    const name=String(modal.dataset.profile||'').trim();
    const raw=String(modal.querySelector('#remaskSessionRefreshCookies').value||'').trim();
    const err=modal.querySelector('#remaskSessionRefreshError');
    const save=modal.querySelector('#remaskSessionRefreshSave');
    err.style.display='none';
    err.textContent='';

    try{
      if(!name)throw new Error('Не выбран FB-профиль.');
      if(!raw)throw new Error('Вставь Cookies JSON.');

      let parsed;
      try{parsed=JSON.parse(raw);}catch(_){throw new Error('Cookies JSON имеет неверный формат.');}
      const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
      const names=new Set(
        list.filter(x=>x&&typeof x==='object'&&String(x.value||'').trim())
          .map(x=>String(x.name||'').trim())
      );
      if(!names.has('c_user')||!names.has('xs')){
        throw new Error('В cookies должны присутствовать c_user и xs.');
      }

      save.disabled=true;
      save.textContent='Обновляю…';
      if(typeof $==='function'&&$('workspaceStatus')){
        $('workspaceStatus').textContent='Обновляю FB-сессию профиля '+name+'…';
      }

      const updated=await profileSaveJson({
        action:'session_update',
        name,
        cookies:raw
      });

      save.textContent='Синхронизирую…';
      const snapshot=await apiJson(
        'ajax/metaHierarchy.php',
        post({action:'sync_profile',profile:name})
      );
      if(typeof applySnapshot==='function')applySnapshot(snapshot);

      close();
      modal.querySelector('#remaskSessionRefreshCookies').value='';
      if(typeof $==='function'&&$('workspaceStatus')){
        $('workspaceStatus').textContent=
          'FB-сессия '+name+' обновлена'+
          (updated&&updated.token_refreshed?' · Ads Manager token обновлён':'')+
          ' · Meta синхронизирована';
      }
      if(typeof updateSelectionUi==='function')updateSelectionUi();
    }catch(e){
      err.textContent=String((e&&e.message)||e);
      err.style.display='block';
      if(typeof $==='function'&&$('workspaceStatus')){
        $('workspaceStatus').textContent='FB session: '+err.textContent;
      }
    }finally{
      save.disabled=false;
      save.textContent='Обновить и синхронизировать';
    }
  });
  return modal;
}

function remaskSessionRefreshUpdateButton(){
  const actions=document.getElementById('workspaceActions');
  if(!actions)return;
  let btn=document.getElementById('remaskSessionRefreshBtn');
  if(!btn){
    btn=document.createElement('button');
    btn.type='button';
    btn.id='remaskSessionRefreshBtn';
    btn.className='btn btn-secondary';
    btn.textContent='Обновить FB-сессию';
    btn.style.display='none';
    btn.addEventListener('click',()=>{
      const name=remaskSelectedProfileNameForSessionRefresh();
      if(!name)return;
      const modal=remaskSessionRefreshEnsureModal();
      modal.dataset.profile=name;
      modal.querySelector('#remaskSessionRefreshProfile').textContent='Профиль: '+name;
      modal.querySelector('#remaskSessionRefreshError').style.display='none';
      modal.style.display='flex';
      setTimeout(()=>modal.querySelector('#remaskSessionRefreshCookies').focus(),0);
    });
    actions.appendChild(btn);
  }
  const profileName=remaskSelectedProfileNameForSessionRefresh();
  const profilesTab=!window.state||String(state.activeTab||'')==='profiles';
  btn.style.display=(profilesTab&&profileName)?'inline-flex':'none';
}

(function installRemaskSessionRefreshUi(){
  const run=()=>remaskSessionRefreshUpdateButton();
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',run,{once:true});
  else run();
  document.addEventListener('click',()=>setTimeout(run,0),true);
  document.addEventListener('change',()=>setTimeout(run,0),true);
  new MutationObserver(()=>run()).observe(document.documentElement,{childList:true,subtree:true});
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
