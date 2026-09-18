<?php
declare(strict_types=1);

$root = '/var/www/html';
$endpointPath = $root . '/ajax/metaPageHelper.php';
$scriptPath = $root . '/scripts/page-helper.js';
$workspacePath = $root . '/workspace.php';

$endpoint = <<<'PHP_ENDPOINT'
<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/ResponseFormatter.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/FbAccount.php';

function rmx_page_helper_out(array $payload, int $status = 200): never {
    http_response_code($status);
    ResponseFormatter::Respond(['res'=>json_encode($payload, JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR)]);
    exit;
}
function rmx_page_helper_account(string $profile): FbAccount {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $profile = trim($profile);
    if ($profile !== '') {
        $account = $store->getAccountByName($profile);
        if ($account instanceof FbAccount) return $account;
        foreach ((array)$store->deserialize() as $candidate) {
            if (!$candidate instanceof FbAccount) continue;
            if ((string)$candidate->name === $profile) return $candidate;
        }
    }
    $all = array_values(array_filter((array)$store->deserialize(), static fn($x)=>$x instanceof FbAccount));
    if (count($all) === 1) return $all[0];
    throw new RuntimeException('Не удалось определить FB-профиль для обновления Pages.');
}
function rmx_page_helper_cookies(FbAccount $account): string {
    if (method_exists($account,'isLegacyReady') && $account->isLegacyReady() && method_exists($account,'getCurlCookies')) {
        return trim((string)$account->getCurlCookies());
    }
    $parts=[];
    foreach ((array)$account->cookies as $cookie) {
        if (!is_array($cookie)) continue;
        $name=trim((string)($cookie['name']??''));
        $value=(string)($cookie['value']??'');
        if ($name!=='' && $value!=='') $parts[]=$name.'='.$value;
    }
    return implode('; ', $parts);
}
function rmx_page_helper_graph(FbAccount $account, bool $useProxy): array {
    $version=getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/',$version)) $version='v26.0';
    $url='https://graph.facebook.com/'.$version.'/me/accounts?'.http_build_query([
        'fields'=>'id,name,category',
        'limit'=>200,
    ]);
    $token=trim((string)$account->token);
    if ($token==='') throw new RuntimeException('У FB-профиля нет сохранённого access token.');
    $ch=curl_init($url);
    $opts=[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>12,
        CURLOPT_TIMEOUT=>35,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Accept: application/json','Authorization: Bearer '.$token],
        CURLOPT_USERAGENT=>'ReMask-PageHelper/1.0',
    ];
    $cookies=rmx_page_helper_cookies($account);
    if ($cookies!=='') $opts[CURLOPT_COOKIE]=$cookies;
    if ($useProxy && $account->proxy !== null) $account->proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch,$opts);
    $raw=curl_exec($ch);
    $curlError=curl_error($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if ($raw===false) throw new RuntimeException('TRANSPORT: '.($curlError ?: 'Meta connection failed'));
    $decoded=json_decode($raw,true);
    if (!is_array($decoded)) throw new RuntimeException('Meta вернула не JSON, HTTP '.$http.'.');
    if (is_array($decoded['error']??null)) {
        $e=$decoded['error'];
        $msg=trim((string)($e['message']??'Meta rejected request.'));
        $code=(int)($e['code']??0);
        if ($code) $msg.=' (code '.$code.')';
        throw new RuntimeException($msg);
    }
    if ($http<200 || $http>=300) throw new RuntimeException('Meta API HTTP '.$http.'.');
    return $decoded;
}

try {
    $profile=trim((string)($_POST['profile']??''));
    $account=rmx_page_helper_account($profile);
    try {
        $result=rmx_page_helper_graph($account, $account->proxy !== null);
        $proxyFallback=false;
    } catch (Throwable $first) {
        if ($account->proxy === null || !str_starts_with($first->getMessage(),'TRANSPORT:')) throw $first;
        $result=rmx_page_helper_graph($account,false);
        $proxyFallback=true;
    }
    rmx_page_helper_out([
        'ok'=>true,
        'profile'=>(string)$account->name,
        'pages'=>array_values((array)($result['data']??[])),
        'proxy_fallback'=>$proxyFallback,
    ]);
} catch (Throwable $e) {
    rmx_page_helper_out(['ok'=>false,'error'=>$e->getMessage()],400);
}
PHP_ENDPOINT;

$script = <<<'JS'
(function(){
  'use strict';
  var endpoint='ajax/metaPageHelper.php';
  var createUrl='https://www.facebook.com/pages/create';

  function parseResponse(response){
    return response.text().then(function(text){
      var data={};
      try{data=JSON.parse(text);}catch(e){}
      if(data && typeof data.res==='string'){
        try{var inner=JSON.parse(data.res); for(var k in inner)data[k]=inner[k];}catch(e){}
      }
      if(!response.ok || data.ok===false)throw new Error(data.error||data.message||('HTTP '+response.status));
      return data;
    });
  }
  function api(profile){
    return parseResponse(fetch(endpoint,{
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
      body:new URLSearchParams({profile:profile||''})
    }));
  }
  function getDialog(){
    var nodes=[].slice.call(document.querySelectorAll('[role="dialog"],.modal,.modal-content'));
    return nodes.find(function(d){return /Добавить Business Manager/i.test(String(d.textContent||''));})||null;
  }
  function profileHint(dialog){
    var m=String(dialog.textContent||'').match(/\b\d{8,20}\b/);
    return m?m[0]:'';
  }
  function primarySelect(dialog){
    var all=[].slice.call(dialog.querySelectorAll('select'));
    return all.find(function(s){
      var p=s.parentElement;
      return /Primary Page/i.test(String((p&&p.textContent)||'')) ||
        [].slice.call(s.options).some(function(o){return /Primary Page/i.test(String(o.textContent||''));});
    })||null;
  }
  function addPageOption(select,page){
    if(!select||!page||!page.id)return;
    var value=String(page.id);
    var option=[].slice.call(select.options).find(function(o){return String(o.value)===value;});
    if(!option){
      option=document.createElement('option');
      option.value=value;
      option.textContent=(page.name||'Page')+' ('+value+')';
      select.appendChild(option);
    }
    select.value=value;
    select.dispatchEvent(new Event('input',{bubbles:true}));
    select.dispatchEvent(new Event('change',{bubbles:true}));
  }
  function setStatus(dialog,text,error){
    var el=dialog.querySelector('[data-remask-page-status]');
    if(!el){
      el=document.createElement('div');
      el.setAttribute('data-remask-page-status','1');
      el.style.marginTop='8px';
      el.style.fontSize='12px';
      var footer=dialog.querySelector('.modal-footer');
      (footer?footer.parentElement:dialog).appendChild(el);
    }
    el.style.color=error?'#ff7b7b':'#83dd99';
    el.textContent=text;
  }
  function refresh(dialog){
    var profile=profileHint(dialog);
    setStatus(dialog,'Обновляю Pages из Meta…',false);
    api(profile).then(function(data){
      var pages=data.pages||[];
      var select=primarySelect(dialog);
      pages.forEach(function(page){addPageOption(select,page);});
      if(pages.length){
        addPageOption(select,pages[0]);
        var zero=[].slice.call(dialog.querySelectorAll('*')).find(function(el){
          return /^0 Pages$/i.test(String(el.textContent||'').trim());
        });
        if(zero)zero.textContent=String(pages.length)+' Pages';
        setStatus(dialog,'Pages обновлены: '+pages.length+'. Первая Page выбрана как Primary Page.',false);
      }else{
        setStatus(dialog,'Meta всё ещё возвращает 0 Pages для этого FB-профиля.',true);
      }
    }).catch(function(e){
      setStatus(dialog,'Не удалось обновить Pages: '+e.message,true);
    });
  }
  function enhanceBmDialog(){
    var dialog=getDialog();
    if(!dialog||dialog.getAttribute('data-remask-page-helper')==='1')return;
    var select=primarySelect(dialog);
    var zero=[].slice.call(dialog.querySelectorAll('*')).find(function(el){
      return /^0 Pages$/i.test(String(el.textContent||'').trim());
    });
    var anchor=(zero&&zero.parentElement)||(select&&select.parentElement);
    if(!anchor)return;

    var wrap=document.createElement('div');
    wrap.style.display='flex';
    wrap.style.gap='8px';
    wrap.style.marginTop='8px';

    var create=document.createElement('button');
    create.type='button';
    create.textContent='Создать Facebook Page';
    create.className='btn btn-primary';
    create.addEventListener('click',function(){
      window.open(createUrl,'_blank','noopener');
      setStatus(dialog,'Создай Page в открывшейся вкладке Facebook, затем вернись сюда и нажми «Обновить Pages».',false);
    });

    var reload=document.createElement('button');
    reload.type='button';
    reload.textContent='Обновить Pages';
    reload.className='btn btn-secondary';
    reload.addEventListener('click',function(){refresh(dialog);});

    wrap.appendChild(create);
    wrap.appendChild(reload);
    anchor.appendChild(wrap);
    dialog.setAttribute('data-remask-page-helper','1');
  }

  function actionNodes(root){
    return [].slice.call(root.querySelectorAll('button,a,[role="menuitem"],li,.dropdown-item,.menu-item'));
  }
  function enhanceActionMenus(){
    var roots=[].slice.call(document.querySelectorAll(
      '[role="menu"],.dropdown-menu,.context-menu,.popover,.menu,[class*="dropdown-menu"],[class*="context-menu"]'
    ));
    roots.forEach(function(menu){
      if(menu.querySelector('[data-remask-create-page-action="1"]'))return;
      var text=String(menu.textContent||'');
      if(!/(Добавить\s+(?:BM|Business Manager)|Business Manager)/i.test(text))return;
      if(!/(Проверить\s+прокси|Launch всех доступных RK|Редактировать аккаунт)/i.test(text))return;

      var nodes=actionNodes(menu);
      var reference=nodes.find(function(el){
        return /(Добавить\s+(?:BM|Business Manager)|Business Manager)/i.test(String(el.textContent||'').trim());
      }) || nodes.find(function(el){
        return /Проверить\s+прокси/i.test(String(el.textContent||'').trim());
      });

      var item;
      if(reference){
        item=reference.cloneNode(false);
        item.removeAttribute('href');
        item.removeAttribute('onclick');
        item.removeAttribute('id');
        item.removeAttribute('disabled');
      }else{
        item=document.createElement('button');
        item.type='button';
      }
      item.setAttribute('data-remask-create-page-action','1');
      item.textContent='Создать Facebook Page';
      item.style.cursor='pointer';
      item.addEventListener('click',function(e){
        e.preventDefault();
        e.stopPropagation();
        window.open(createUrl,'_blank','noopener');
      });

      if(reference && reference.parentNode) reference.parentNode.insertBefore(item,reference.nextSibling);
      else menu.appendChild(item);
    });
  }

  function enhance(){
    enhanceBmDialog();
    enhanceActionMenus();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhance);
  else enhance();
  new MutationObserver(enhance).observe(document.documentElement,{childList:true,subtree:true});
})();
JS;

file_put_contents($endpointPath,$endpoint);
file_put_contents($scriptPath,$script);

$workspace=file_get_contents($workspacePath);
if($workspace===false)throw new RuntimeException('workspace.php not found');
$tag='<script src="scripts/page-helper.js?v=20260918-page-helper-v3"></script>';
if(strpos($workspace,'scripts/page-helper.js')===false){
    if(stripos($workspace,'</body>')!==false)$workspace=str_ireplace('</body>',$tag."\n</body>",$workspace);
    else $workspace.="\n".$tag."\n";
}
file_put_contents($workspacePath,$workspace);
fwrite(STDERR,"[page-helper] BM modal + account action menu Page helper installed\n");
