<?php
/**
 * Keep Workspace profile session context intact across add/edit/proxy operations.
 * This overlay intentionally never logs token/cookie/proxy secret values.
 */
$root = '/var/www/html';

function rmx_replace_block(string $source, string $startNeedle, string $endNeedle, string $replacement, string $label): string
{
    $start = strpos($source, $startNeedle);
    if ($start === false) {
        fwrite(STDERR, "[workspace-session-integrity] {$label}: start marker missing\n");
        exit(61);
    }
    $end = strpos($source, $endNeedle, $start + strlen($startNeedle));
    if ($end === false) {
        fwrite(STDERR, "[workspace-session-integrity] {$label}: end marker missing\n");
        exit(62);
    }
    return substr($source, 0, $start) . $replacement . "\n\n    " . substr($source, $end);
}

// 1) Route Workspace add/edit through the canonical session-safe profile manager.
$workspacePath = $root . '/scripts/workspace.js';
$workspace = file_get_contents($workspacePath);
if ($workspace === false) {
    fwrite(STDERR, "[workspace-session-integrity] workspace.js missing\n");
    exit(63);
}

$workspace = str_replace(
    'Cookies JSON (legacy only, optional)',
    'Cookies JSON / FB session',
    $workspace,
    $labelCount
);

$workspace = str_replace(
    '<textarea id="newProfileCookies" rows="3" placeholder="[]"></textarea>',
    '<textarea id="newProfileCookies" rows="5" placeholder="[{&quot;name&quot;:&quot;c_user&quot;,...},{&quot;name&quot;:&quot;xs&quot;,...}]"></textarea>',
    $workspace,
    $placeholderCount
);

$addStart = 'function prepareAddProfile(){';
$addEnd = 'function launchTargets(targets){';
$addReplacement = <<<'JS'
function prepareAddProfile(){
  openModal('Добавить FB аккаунт',`<div class="ws-form"><div><label>Название в ReMask</label><input id="newProfileName"></div><div><label>Access token</label><input id="newProfileToken" type="password" autocomplete="off"></div><div class="full"><label>Proxy (optional)</label><input id="newProfileProxy" placeholder="http:ip:port:login:password"></div><div class="full"><label>Cookies JSON / FB session</label><textarea id="newProfileCookies" rows="5" placeholder="Вставь JSON cookies с c_user и xs"></textarea></div></div><div class="ws-muted mt-2">Для импортируемого FB-профиля сохрани token + session cookies вместе. ReMask не возвращает token/cookies обратно в браузер после сохранения.</div>`,'Добавить',async()=>{
    const name=$('newProfileName').value.trim();
    const token=$('newProfileToken').value.trim();
    const cookies=$('newProfileCookies').value.trim();
    if(!name||!token)throw new Error('Название и token обязательны.');
    if(!cookies||cookies==='[]'||cookies==='{}')throw new Error('Добавь Cookies JSON текущей FB-сессии. Нужны как минимум c_user и xs.');
    let parsed;
    try{parsed=JSON.parse(cookies);}catch{throw new Error('Cookies должны быть валидным JSON.');}
    const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
    const names=new Set(list.filter(x=>x&&typeof x==='object').map(x=>String(x.name||'')));
    if(!names.has('c_user')||!names.has('xs'))throw new Error('В Cookies JSON не найдены обязательные c_user и xs.');
    await apiJson('ajax/metaProfileManager.php',post({action:'create',name,token,proxy:$('newProfileProxy').value.trim(),cookies}));
    await loadInventory('FB аккаунт сохранён вместе с session cookies. Теперь можно выполнить доп. синхронизацию BM/RK.');
    closeModal();
  });
}

function prepareEditProfile(){
  const p=selectedRows('profiles')[0]; if(!p)return;
  openModal(`Редактировать ${p.name}`,`<div class="ws-form"><div><label>Новый access token (пусто = оставить)</label><input id="editToken" type="password" autocomplete="off"></div><div><label>Новый proxy (пусто = оставить)</label><input id="editProxy" placeholder="http:ip:port:login:password"></div><div class="full"><label>Cookies JSON (пусто = оставить текущую FB-сессию)</label><textarea id="editCookies" rows="5"></textarea></div><div class="full"><label><input id="editClearProxy" type="checkbox" style="width:auto"> удалить текущий proxy</label></div><div class="full"><label><input id="editClearSession" type="checkbox" style="width:auto"> явно удалить сохранённые session cookies</label></div></div><div class="ws-muted mt-2">Пустое поле cookies больше не стирает сохранённую сессию. Удаление возможно только отдельным чекбоксом.</div>`,'Сохранить',async()=>{
    const cookies=$('editCookies').value.trim();
    if(cookies){
      let parsed; try{parsed=JSON.parse(cookies);}catch{throw new Error('Cookies должны быть валидным JSON.');}
      const list=Array.isArray(parsed)?parsed:Object.values(parsed||{});
      const names=new Set(list.filter(x=>x&&typeof x==='object').map(x=>String(x.name||'')));
      if(!names.has('c_user')||!names.has('xs'))throw new Error('Новые cookies должны содержать c_user и xs.');
    }
    await apiJson('ajax/metaProfileManager.php',post({action:'save',name:p.name,token:$('editToken').value.trim(),cookies,proxy:$('editProxy').value.trim(),clear_proxy:$('editClearProxy').checked?'1':'0',clear_session:$('editClearSession').checked?'1':'0'}));
    await loadInventory('Настройки FB-профиля сохранены без потери session context.');
    closeModal();
  });
}
JS;

// Existing runtime has edit then add immediately before launchTargets. Replace both as one region.
$editStart = strpos($workspace, 'function prepareEditProfile(){');
$launchStart = strpos($workspace, $addEnd, $editStart === false ? 0 : $editStart);
if ($editStart === false || $launchStart === false) {
    fwrite(STDERR, "[workspace-session-integrity] profile form region not found\n");
    exit(64);
}
$workspace = substr($workspace, 0, $editStart) . $addReplacement . "\n" . substr($workspace, $launchStart);
file_put_contents($workspacePath, $workspace);
fwrite(STDERR, "[workspace-session-integrity] Workspace add/edit now uses canonical session-safe manager\n");

// 2) Harden any remaining server-side hierarchy callers that mutate profiles.
$hierarchyPath = $root . '/ajax/metaHierarchy.php';
$hierarchy = file_get_contents($hierarchyPath);
if ($hierarchy === false) {
    fwrite(STDERR, "[workspace-session-integrity] metaHierarchy.php missing\n");
    exit(65);
}

$updateBlock = <<<'PHP_BLOCK'
if ($action === 'update_profile') {
        $profile = trim((string)($input['profile'] ?? $input['name'] ?? ''));
        if ($profile === '') throw new InvalidArgumentException('profile is required');
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        $existing = $store->getAccountByName($profile);
        if (!$existing instanceof FbAccount) throw new InvalidArgumentException('Profile not found.');

        $tokenInput = trim((string)($input['token'] ?? $input['access_token'] ?? ''));
        $token = $tokenInput !== '' ? $tokenInput : (string)$existing->token;
        if ($token === '') throw new InvalidArgumentException('Token is required.');

        $clearSession = filter_var($input['clear_session'] ?? false, FILTER_VALIDATE_BOOLEAN);
        $cookiesInput = trim((string)($input['cookies'] ?? $input['cookies_json'] ?? ''));
        $cookies = (array)$existing->cookies;
        $dtsg = $existing->dtsg;
        if ($clearSession) {
            $cookies = [];
            $dtsg = null;
        } elseif ($cookiesInput !== '') {
            $decoded = json_decode($cookiesInput, true);
            if (!is_array($decoded)) throw new InvalidArgumentException('Cookies must be valid JSON.');
            // Empty [] / {} is treated as preserve, never as an accidental erase.
            if ($decoded !== []) {
                $cookies = $decoded;
                $dtsgInput = trim((string)($input['dtsg'] ?? $input['fb_dtsg'] ?? ''));
                $dtsg = $dtsgInput !== '' ? $dtsgInput : null;
            }
        }

        $clearProxy = filter_var($input['clear_proxy'] ?? false, FILTER_VALIDATE_BOOLEAN);
        $proxyInput = trim((string)($input['proxy'] ?? ''));
        $proxy = $clearProxy ? null : ($proxyInput !== '' ? RemaskProxy::fromSemicolonString($proxyInput) : $existing->proxy);

        $accountsPath = (string)ACCOUNTSFILENAME;
        if (is_file($accountsPath) && filesize($accountsPath) > 2) @copy($accountsPath, $accountsPath . '.bak.hierarchy-safe.' . gmdate('YmdHis'));
        $cookieJson = json_encode(array_values($cookies), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $replacement = new FbAccount($profile, $token, $cookieJson, $dtsg, $proxy);
        $store->addOrUpdateAccount($replacement);
        if ($clearProxy || $proxyInput !== '') hierarchy_workspace_meta_store()->setProxyHealth($profile, []);
        MetaEndpoint::invalidateProfileCache($profile);
        hierarchy_activity(['action'=>'update_profile','entity_type'=>'profile','entity_id'=>$profile,'profile_name'=>$profile,'summary'=>'Обновлены настройки FB-профиля без потери session context','details'=>['token_changed'=>$tokenInput !== '','cookies_changed'=>$cookiesInput !== '' && !$clearSession,'session_cleared'=>$clearSession,'proxy_changed'=>$clearProxy || $proxyInput !== '']]);
        MetaEndpoint::ok(['updated' => true, 'inventory' => hierarchy_inventory()]);
    }
PHP_BLOCK;

$assignBlock = <<<'PHP_BLOCK'
if ($action === 'assign_proxy') {
        $profiles = decode_profile_list($input['profiles'] ?? []);
        if ($profiles === []) throw new InvalidArgumentException('Select at least one Facebook profile.');
        $proxyText = trim((string)($input['proxy'] ?? ''));
        $sourceProfile = trim((string)($input['source_profile'] ?? ''));
        $clear = filter_var($input['clear'] ?? false, FILTER_VALIDATE_BOOLEAN);
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        if ($clear) {
            $proxy = null;
        } elseif ($sourceProfile !== '') {
            $source = $store->getAccountByName($sourceProfile);
            if ($source === null || $source->proxy === null) throw new InvalidArgumentException('Selected existing proxy is no longer available.');
            $proxy = $source->proxy;
        } else {
            if ($proxyText === '') throw new InvalidArgumentException('Proxy is required unless an existing proxy is selected.');
            $proxy = RemaskProxy::fromSemicolonString($proxyText);
        }

        $accountsPath = (string)ACCOUNTSFILENAME;
        if (is_file($accountsPath) && filesize($accountsPath) > 2) @copy($accountsPath, $accountsPath . '.bak.proxy-safe.' . gmdate('YmdHis'));
        $updated = [];
        foreach ($profiles as $profile) {
            $existing = $store->getAccountByName($profile);
            if (!$existing instanceof FbAccount) throw new InvalidArgumentException("Profile not found: {$profile}");
            $cookieJson = json_encode(array_values((array)$existing->cookies), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
            $replacement = new FbAccount($existing->name, (string)$existing->token, $cookieJson, $existing->dtsg, $proxy);
            $store->addOrUpdateAccount($replacement);
            hierarchy_workspace_meta_store()->setProxyHealth($profile, []);
            MetaEndpoint::invalidateProfileCache($profile);
            $updated[] = ['profile' => $profile, 'ok' => true, 'proxy_configured' => $proxy !== null];
        }
        hierarchy_activity(['action'=>'assign_proxy','entity_type'=>'profile','summary'=>($clear?'Proxy удалён у ':'Proxy назначен для ') . count($profiles) . ' FB-профилей без изменения session context','details'=>['profiles'=>count($profiles),'mode'=>$clear?'clear':($sourceProfile!==''?'reuse':'new')]]);
        MetaEndpoint::ok(['updated' => $updated, 'inventory' => hierarchy_inventory()]);
    }
PHP_BLOCK;

$hierarchy = rmx_replace_block($hierarchy, "if (\$action === 'update_profile') {", "if (\$action === 'assign_proxy') {", $updateBlock, 'update_profile');
$hierarchy = rmx_replace_block($hierarchy, "if (\$action === 'assign_proxy') {", "if (\$action === 'check_proxy') {", $assignBlock, 'assign_proxy');
file_put_contents($hierarchyPath, $hierarchy);
fwrite(STDERR, "[workspace-session-integrity] hierarchy update_profile/assign_proxy preserve cookies+dtsg\n");
