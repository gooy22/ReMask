<?php
/**
 * Official Meta App + OAuth auth layer for ReMask.
 * - Meta App credentials are stored server-side only (0600) on REMASK_DATA_DIR.
 * - OAuth exchanges a code for an official user access token and verifies /me.
 * - appsecret_proof is attached only to profiles actually connected through this app.
 * - Existing token-only profiles continue to work unchanged when their token is valid.
 */
$root = '/var/www/html';

$configStore = <<<'PHP_CODE'
<?php
final class MetaAppConfigStore
{
    private string $path;
    public function __construct(?string $path = null)
    {
        $base = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
        $this->path = $path ?: ($base . '/meta-app-config.json');
    }
    public function effective(): array
    {
        $envId = trim((string)(getenv('META_APP_ID') ?: ''));
        $envSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));
        if ($envId !== '' && $envSecret !== '') return ['app_id'=>$envId,'app_secret'=>$envSecret,'configured'=>true,'source'=>'environment'];
        $stored = $this->read();
        $id = trim((string)($stored['app_id'] ?? ''));
        $secret = trim((string)($stored['app_secret'] ?? ''));
        return ['app_id'=>$id,'app_secret'=>$secret,'configured'=>$id !== '' && $secret !== '','source'=>($id !== '' || $secret !== '') ? 'persistent' : 'none'];
    }
    public function save(string $appId, string $appSecret): void
    {
        $appId = trim($appId); $appSecret = trim($appSecret);
        if (!preg_match('/^[0-9]{5,30}$/', $appId)) throw new InvalidArgumentException('Meta App ID должен состоять из цифр.');
        if (strlen($appSecret) < 16 || strlen($appSecret) > 128 || preg_match('/\s/', $appSecret)) throw new InvalidArgumentException('Meta App Secret имеет неверный формат.');
        $dir = dirname($this->path);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) throw new RuntimeException('Cannot create Meta app config directory.');
        $json = json_encode(['app_id'=>$appId,'app_secret'=>$appSecret,'updated_at'=>gmdate('c')], JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT|JSON_THROW_ON_ERROR);
        $tmp = $this->path . '.tmp.' . getmypid() . '.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp, $json . "\n", LOCK_EX) === false) throw new RuntimeException('Cannot write Meta app config.');
        @chmod($tmp, 0600);
        if (!rename($tmp, $this->path)) { @unlink($tmp); throw new RuntimeException('Cannot replace Meta app config.'); }
        @chmod($this->path, 0600);
    }
    private function read(): array
    {
        if (!is_file($this->path)) return [];
        $raw = @file_get_contents($this->path);
        if ($raw === false || trim($raw) === '') return [];
        $decoded = json_decode($raw, true);
        return is_array($decoded) ? $decoded : [];
    }
}
PHP_CODE;
file_put_contents($root . '/classes/MetaAppConfigStore.php', $configStore);

$bindingStore = <<<'PHP_CODE'
<?php
final class MetaOAuthBindingStore
{
    private string $path;
    public function __construct(?string $path = null)
    {
        $base = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
        $this->path = $path ?: ($base . '/meta-oauth-bindings.json');
    }
    public function bind(string $profile, string $appId): void
    {
        $all = $this->read();
        $all[$profile] = ['app_id'=>$appId,'connected_at'=>gmdate('c')];
        $this->write($all);
    }
    public function isBound(string $profile, string $appId): bool
    {
        $all = $this->read();
        return isset($all[$profile]) && hash_equals((string)($all[$profile]['app_id'] ?? ''), $appId);
    }
    private function read(): array
    {
        if (!is_file($this->path)) return [];
        $raw = @file_get_contents($this->path);
        $decoded = $raw === false ? null : json_decode($raw, true);
        return is_array($decoded) ? $decoded : [];
    }
    private function write(array $data): void
    {
        $dir = dirname($this->path);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) throw new RuntimeException('Cannot create OAuth binding directory.');
        $json = json_encode($data, JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT|JSON_THROW_ON_ERROR);
        $tmp = $this->path . '.tmp.' . getmypid() . '.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp, $json . "\n", LOCK_EX) === false) throw new RuntimeException('Cannot write OAuth bindings.');
        @chmod($tmp, 0600);
        if (!rename($tmp, $this->path)) { @unlink($tmp); throw new RuntimeException('Cannot replace OAuth bindings.'); }
        @chmod($this->path, 0600);
    }
}
PHP_CODE;
file_put_contents($root . '/classes/MetaOAuthBindingStore.php', $bindingStore);

$configEndpoint = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaAppConfigStore.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
function remask_meta_config_public(array $cfg): array {
    $id = trim((string)($cfg['app_id'] ?? ''));
    $masked = $id === '' ? '' : (strlen($id) <= 6 ? str_repeat('*', strlen($id)) : substr($id,0,3) . str_repeat('*', max(3,strlen($id)-6)) . substr($id,-3));
    $domain = trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN') ?: ''));
    $redirect = trim((string)(getenv('META_OAUTH_REDIRECT_URI') ?: ''));
    if ($redirect === '' && $domain !== '') $redirect = 'https://' . $domain . '/meta-oauth-callback.php';
    return ['configured'=>(bool)($cfg['configured'] ?? false),'source'=>(string)($cfg['source'] ?? 'none'),'app_id_masked'=>$masked,'app_secret_configured'=>trim((string)($cfg['app_secret'] ?? '')) !== '','redirect_uri'=>$redirect];
}
try {
    $store = new MetaAppConfigStore();
    if ($_SERVER['REQUEST_METHOD'] === 'GET') { echo json_encode(['ok'=>true] + remask_meta_config_public($store->effective()), JSON_UNESCAPED_SLASHES); exit; }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); echo json_encode(['ok'=>false,'error'=>'Method not allowed.']); exit; }
    if (($_SERVER['HTTP_X_REQUESTED_WITH'] ?? '') !== 'ReMask') { http_response_code(403); echo json_encode(['ok'=>false,'error'=>'Missing ReMask request header.']); exit; }
    $origin = trim((string)($_SERVER['HTTP_ORIGIN'] ?? '')); $host = preg_replace('/:\d+$/','',trim((string)($_SERVER['HTTP_HOST'] ?? '')));
    if ($origin !== '' && $host !== '') { $originHost = (string)(parse_url($origin, PHP_URL_HOST) ?: ''); if ($originHost === '' || strcasecmp($originHost,$host) !== 0) { http_response_code(403); echo json_encode(['ok'=>false,'error'=>'Origin rejected.']); exit; } }
    $effective = $store->effective();
    if (($effective['source'] ?? '') === 'environment' && ($effective['configured'] ?? false)) { http_response_code(409); echo json_encode(['ok'=>false,'error'=>'Meta App уже настроен через Railway environment.']); exit; }
    $body = json_decode((string)file_get_contents('php://input'), true); if (!is_array($body)) $body = $_POST;
    $store->save((string)($body['app_id'] ?? ''),(string)($body['app_secret'] ?? ''));
    echo json_encode(['ok'=>true] + remask_meta_config_public($store->effective()), JSON_UNESCAPED_SLASHES);
} catch (Throwable $e) { http_response_code($e instanceof InvalidArgumentException ? 400 : 500); echo json_encode(['ok'=>false,'error'=>$e->getMessage()], JSON_UNESCAPED_SLASHES); }
PHP_CODE;
file_put_contents($root . '/ajax/metaAppConfig.php', $configEndpoint);

$oauthStart = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/checkpassword.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';
require_once __DIR__ . '/classes/MetaAppConfigStore.php';
if (session_status() !== PHP_SESSION_ACTIVE) session_start();
$cfg = (new MetaAppConfigStore())->effective();
$appId = trim((string)($cfg['app_id'] ?? ''));
if ($appId === '') { http_response_code(503); exit('Meta App is not configured.'); }
$profile = trim((string)($_GET['profile'] ?? ''));
if ($profile === '') { http_response_code(400); exit('profile is required'); }
$store = AccountStoreFactory::create(ACCOUNTSFILENAME);
if ($store->getAccountByName($profile) === null) { http_response_code(404); exit('ReMask profile not found.'); }
$domain = trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN') ?: ''));
$redirect = trim((string)(getenv('META_OAUTH_REDIRECT_URI') ?: ''));
if ($redirect === '' && $domain !== '') $redirect = 'https://' . $domain . '/meta-oauth-callback.php';
if ($redirect === '') { http_response_code(503); exit('OAuth redirect URI cannot be determined.'); }
$state = bin2hex(random_bytes(32));
$_SESSION['remask_meta_oauth'] = ['state'=>$state,'profile'=>$profile,'redirect_uri'=>$redirect,'created_at'=>time()];
$scopes = trim((string)(getenv('META_OAUTH_SCOPES') ?: 'ads_management,ads_read,business_management,pages_show_list,pages_read_engagement,read_insights'));
$version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0'; if (!preg_match('/^v\d+\.\d+$/',$version)) $version='v26.0';
header('Location: https://www.facebook.com/' . rawurlencode($version) . '/dialog/oauth?' . http_build_query(['client_id'=>$appId,'redirect_uri'=>$redirect,'state'=>$state,'scope'=>$scopes,'response_type'=>'code']));
exit;
PHP_CODE;
file_put_contents($root . '/meta-oauth-start.php', $oauthStart);

$oauthCallback = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/classes/FbAccount.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';
require_once __DIR__ . '/classes/MetaEndpoint.php';
require_once __DIR__ . '/classes/MetaAppConfigStore.php';
require_once __DIR__ . '/classes/MetaOAuthBindingStore.php';
if (session_status() !== PHP_SESSION_ACTIVE) session_start();
header('Cache-Control: no-store, max-age=0');
function remask_oauth_fail(string $m,int $s=400): never { http_response_code($s); header('Content-Type:text/html;charset=utf-8'); echo '<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;background:#111827;color:#e5e7eb;padding:28px"><h2>Meta OAuth</h2><p>'.htmlspecialchars($m,ENT_QUOTES|ENT_SUBSTITUTE,'UTF-8').'</p><p><a style="color:#93c5fd" href="/workspace.php">Workspace</a></p></body>'; exit; }
function remask_oauth_get(string $url): array { $ch=curl_init($url); curl_setopt_array($ch,[CURLOPT_RETURNTRANSFER=>true,CURLOPT_FOLLOWLOCATION=>false,CURLOPT_CONNECTTIMEOUT=>12,CURLOPT_TIMEOUT=>35,CURLOPT_SSL_VERIFYPEER=>true,CURLOPT_SSL_VERIFYHOST=>2,CURLOPT_HTTPHEADER=>['Accept: application/json','User-Agent: ReMask-MetaOAuth/2.0']]); $raw=curl_exec($ch); $err=curl_error($ch); $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE); curl_close($ch); if($raw===false)throw new RuntimeException('OAuth transport: '.($err?:'cURL error')); $d=json_decode((string)$raw,true); if(!is_array($d))throw new RuntimeException('Meta OAuth returned non-JSON.'); if(isset($d['error'])&&is_array($d['error']))throw new RuntimeException((string)($d['error']['message']??'Meta OAuth rejected request.')); if($http<200||$http>=300)throw new RuntimeException('Meta OAuth HTTP '.$http); return $d; }
$cfg=(new MetaAppConfigStore())->effective(); $appId=trim((string)($cfg['app_id']??'')); $secret=trim((string)($cfg['app_secret']??'')); if($appId===''||$secret==='')remask_oauth_fail('Meta App не настроен.',503);
$pending=$_SESSION['remask_meta_oauth']??null; unset($_SESSION['remask_meta_oauth']); if(!is_array($pending)||(int)($pending['created_at']??0)<time()-900)remask_oauth_fail('OAuth-сессия истекла.');
$expected=(string)($pending['state']??''); $state=(string)($_GET['state']??''); if($expected===''||$state===''||!hash_equals($expected,$state))remask_oauth_fail('OAuth state mismatch.');
if(isset($_GET['error']))remask_oauth_fail((string)($_GET['error_description']??$_GET['error'])); $code=trim((string)($_GET['code']??'')); if($code==='')remask_oauth_fail('Meta не вернула authorization code.');
$profile=trim((string)($pending['profile']??'')); $redirect=trim((string)($pending['redirect_uri']??'')); $version=getenv('META_GRAPH_API_VERSION')?:'v26.0'; if(!preg_match('/^v\d+\.\d+$/',$version))$version='v26.0';
try {
  $exchange=remask_oauth_get('https://graph.facebook.com/'.rawurlencode($version).'/oauth/access_token?'.http_build_query(['client_id'=>$appId,'client_secret'=>$secret,'redirect_uri'=>$redirect,'code'=>$code]));
  $token=trim((string)($exchange['access_token']??'')); if($token==='')throw new RuntimeException('Meta OAuth returned no access token.');
  try { $long=remask_oauth_get('https://graph.facebook.com/'.rawurlencode($version).'/oauth/access_token?'.http_build_query(['grant_type'=>'fb_exchange_token','client_id'=>$appId,'client_secret'=>$secret,'fb_exchange_token'=>$token])); if(trim((string)($long['access_token']??''))!=='')$token=trim((string)$long['access_token']); } catch(Throwable $ignored) {}
  $proof=hash_hmac('sha256',$token,$secret);
  $me=remask_oauth_get('https://graph.facebook.com/'.rawurlencode($version).'/me?'.http_build_query(['fields'=>'id,name','access_token'=>$token,'appsecret_proof'=>$proof]));
  if(trim((string)($me['id']??''))==='')throw new RuntimeException('Meta /me returned no user ID.');
  $store=AccountStoreFactory::create(ACCOUNTSFILENAME); $existing=$store->getAccountByName($profile); if(!$existing instanceof FbAccount)throw new RuntimeException('ReMask profile no longer exists.');
  $cookies=json_encode((array)$existing->cookies,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR); $store->addOrUpdateAccount(new FbAccount($existing->name,$token,$cookies,$existing->dtsg,$existing->proxy));
  (new MetaOAuthBindingStore())->bind($profile,$appId); try{MetaEndpoint::invalidateProfileCache($profile);}catch(Throwable $ignored){}
  header('Location: /workspace.php?meta_oauth=connected&profile='.rawurlencode($profile)); exit;
} catch(Throwable $e) { remask_oauth_fail($e->getMessage()); }
PHP_CODE;
file_put_contents($root . '/meta-oauth-callback.php', $oauthCallback);

$statusEndpoint = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php'; require_once __DIR__ . '/../checkpassword.php'; require_once __DIR__ . '/../classes/AccountStoreFactory.php'; require_once __DIR__ . '/../classes/MetaAppConfigStore.php'; require_once __DIR__ . '/../classes/MetaOAuthBindingStore.php';
header('Content-Type:application/json;charset=utf-8'); header('Cache-Control:no-store,max-age=0');
try { $cfg=(new MetaAppConfigStore())->effective(); $domain=trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN')?:'')); $redirect=trim((string)(getenv('META_OAUTH_REDIRECT_URI')?:'')); if($redirect===''&&$domain!=='')$redirect='https://'.$domain.'/meta-oauth-callback.php'; $profiles=[]; $bindings=new MetaOAuthBindingStore(); foreach(AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $a){ if($a instanceof FbAccount&&$a->name!=='')$profiles[]=['name'=>$a->name,'oauth_bound'=>$bindings->isBound($a->name,(string)($cfg['app_id']??''))]; } echo json_encode(['ok'=>true,'configured'=>(bool)($cfg['configured']??false),'config_source'=>(string)($cfg['source']??'none'),'redirect_uri'=>$redirect,'profiles'=>$profiles],JSON_UNESCAPED_SLASHES); } catch(Throwable $e){http_response_code(500);echo json_encode(['ok'=>false,'error'=>$e->getMessage()]);}
PHP_CODE;
file_put_contents($root . '/ajax/metaOAuthStatus.php', $statusEndpoint);

$js = <<<'JS'
(() => {
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function json(url,opt){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',...(opt||{})});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||`HTTP ${r.status}`);return d;}
  async function boot(){
    if(document.getElementById('remaskOfficialMetaAuth'))return;
    let cfg,status; try{cfg=await json('ajax/metaAppConfig.php');status=await json('ajax/metaOAuthStatus.php');}catch(_){return;}
    const anchor=document.getElementById('workspaceStatus')||document.querySelector('main')||document.querySelector('.container')||document.body;
    const box=document.createElement('div');box.id='remaskOfficialMetaAuth';box.style.cssText='margin:12px 0;padding:12px;border:1px solid rgba(96,165,250,.3);border-radius:12px;background:rgba(15,23,42,.48);font-size:13px';
    if(!cfg.configured){
      box.innerHTML='<div style="font-weight:700;margin-bottom:8px">Meta API</div><div style="display:flex;gap:8px;flex-wrap:wrap"><input id="remaskAppId" inputmode="numeric" placeholder="Meta App ID"><input id="remaskAppSecret" type="password" autocomplete="new-password" placeholder="Meta App Secret"><button type="button" id="remaskSaveApp">Сохранить</button></div><div style="margin-top:7px;opacity:.75">OAuth Redirect URI: <code>'+esc(cfg.redirect_uri||'')+'</code></div><div id="remaskAppMsg" style="margin-top:7px"></div>';
      anchor.insertAdjacentElement?anchor.insertAdjacentElement('afterend',box):document.body.prepend(box);
      document.getElementById('remaskSaveApp').onclick=async()=>{const b=document.getElementById('remaskSaveApp'),m=document.getElementById('remaskAppMsg');b.disabled=true;m.textContent='Сохраняю…';try{await json('ajax/metaAppConfig.php',{method:'POST',headers:{'Content-Type':'application/json','X-Requested-With':'ReMask'},body:JSON.stringify({app_id:document.getElementById('remaskAppId').value.trim(),app_secret:document.getElementById('remaskAppSecret').value.trim()})});location.reload();}catch(e){m.textContent=e.message;b.disabled=false;}};
      return;
    }
    const profiles=Array.isArray(status.profiles)?status.profiles:[]; const options=profiles.map(p=>'<option value="'+esc(p.name)+'">'+esc(p.name)+(p.oauth_bound?' ✓':'')+'</option>').join('');
    box.innerHTML='<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><strong>Meta API</strong><select id="remaskOAuthProfile">'+options+'</select><button type="button" id="remaskOAuthConnect">Подключить Meta</button><span style="opacity:.7">официальный Marketing API token</span></div>';
    anchor.insertAdjacentElement?anchor.insertAdjacentElement('afterend',box):document.body.prepend(box);
    const btn=document.getElementById('remaskOAuthConnect'); if(btn)btn.onclick=()=>{const p=document.getElementById('remaskOAuthProfile')?.value||'';if(p)location.href='/meta-oauth-start.php?profile='+encodeURIComponent(p);};
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>setTimeout(boot,100));else setTimeout(boot,100);
})();
JS;
file_put_contents($root . '/scripts/meta-official-auth.js', $js);
foreach ([$root.'/workspace.php',$root.'/accounts.php'] as $page) {
    if (!is_file($page)) continue; $html=file_get_contents($page); if($html===false||str_contains($html,'scripts/meta-official-auth.js'))continue; $tag='<script src="scripts/meta-official-auth.js?v=20260917"></script>'; $html=str_contains($html,'</body>')?str_replace('</body>',$tag."\n</body>",$html):$html."\n".$tag."\n"; file_put_contents($page,$html);
}

// appsecret_proof support in the shared Meta client. It remains OFF unless
// MetaEndpoint marks this specific profile as OAuth-bound to the configured app.
$clientPath=$root.'/classes/MetaApiClient.php'; $client=file_get_contents($clientPath); if($client===false)throw new RuntimeException('MetaApiClient missing');
if(!str_contains($client,'private string $appSecret')){
    $anchor="    /** Saved Facebook browser-session cookies for the same profile/token. */\n    private string \$sessionCookies = '';";
    $replacement=$anchor."\n    private string \$appSecret = '';\n\n    public function setAppSecret(string \$secret): self\n    {\n        \$this->appSecret = trim(\$secret);\n        return \$this;\n    }";
    if(!str_contains($client,$anchor))throw new RuntimeException('MetaApiClient app-secret property anchor missing'); $client=str_replace($anchor,$replacement,$client,$count); if($count!==1)throw new RuntimeException('MetaApiClient app-secret property patch failed');
}
if(!str_contains($client,"appsecret_proof'] = hash_hmac")){
    $pattern='/private function prepareParams\(array \$params\): array\s*\{/' ;
    $replacement="private function prepareParams(array \$params): array\n    {\n        if (\$this->appSecret !== '' && !isset(\$params['appsecret_proof'])) {\n            \$params['appsecret_proof'] = hash_hmac('sha256', \$this->accessToken, \$this->appSecret);\n        }";
    $client=preg_replace($pattern,$replacement,$client,1,$count); if($count!==1)throw new RuntimeException('MetaApiClient prepareParams patch failed');
}
file_put_contents($clientPath,$client);

$endpointPath=$root.'/classes/MetaEndpoint.php'; $endpoint=file_get_contents($endpointPath); if($endpoint===false)throw new RuntimeException('MetaEndpoint missing');
if(!str_contains($endpoint,"MetaAppConfigStore.php"))$endpoint=str_replace('<?php',"<?php\nrequire_once __DIR__ . '/MetaAppConfigStore.php';\nrequire_once __DIR__ . '/MetaOAuthBindingStore.php';",$endpoint,$c);
if(!str_contains($endpoint,'OAuth-bound appsecret_proof')){
    $anchor=<<<'PHP_CODE'
        if ($account->isLegacyReady()) {
            $client->setSessionCookies($account->getCurlCookies());
        }
        return new MetaAdsService($client);
PHP_CODE;
    $replacement=<<<'PHP_CODE'
        if ($account->isLegacyReady()) {
            $client->setSessionCookies($account->getCurlCookies());
        }
        // OAuth-bound appsecret_proof: only sign tokens issued through our configured Meta App.
        try {
            $cfg = (new MetaAppConfigStore())->effective();
            $appId = trim((string)($cfg['app_id'] ?? ''));
            $appSecret = trim((string)($cfg['app_secret'] ?? ''));
            if ($appId !== '' && $appSecret !== '' && (new MetaOAuthBindingStore())->isBound($accountName, $appId)) {
                $client->setAppSecret($appSecret);
            }
        } catch (Throwable $ignored) {}
        return new MetaAdsService($client);
PHP_CODE;
    if(!str_contains($endpoint,$anchor))throw new RuntimeException('MetaEndpoint OAuth proof anchor missing'); $endpoint=str_replace($anchor,$replacement,$endpoint,$count); if($count!==1)throw new RuntimeException('MetaEndpoint OAuth proof patch failed');
}
file_put_contents($endpointPath,$endpoint);

fwrite(STDERR,"[meta-official-auth] Meta App config + OAuth + bound appsecret_proof ready\n");
