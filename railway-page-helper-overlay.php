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
function rmx_page_helper_cookie_value(FbAccount $account, string $wanted): string {
    foreach ((array)$account->cookies as $cookie) {
        if (!is_array($cookie)) continue;
        if ((string)($cookie['name']??'') !== $wanted) continue;
        return trim((string)($cookie['value']??''));
    }
    return '';
}
function rmx_page_helper_account(string $hint): FbAccount {
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts=array_values(array_filter((array)$store->deserialize(),static fn($x)=>$x instanceof FbAccount));
    $hint=trim($hint);

    if($hint!==''){
        foreach($accounts as $account){
            if(trim((string)$account->name)===$hint)return $account;
        }
        foreach($accounts as $account){
            if(rmx_page_helper_cookie_value($account,'c_user')===$hint)return $account;
        }
        foreach($accounts as $account){
            $name=trim((string)$account->name);
            if($name!=='' && str_contains($hint,$name))return $account;
            $cUser=rmx_page_helper_cookie_value($account,'c_user');
            if($cUser!=='' && str_contains($hint,$cUser))return $account;
        }
    }
    if(count($accounts)===1)return $accounts[0];
    throw new RuntimeException('Не удалось сопоставить выбранный FB-аккаунт с сохранённым профилем ReMask.');
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
function rmx_page_helper_graph(FbAccount $account, string $path, array $params, bool $useProxy): array {
    $version=getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/',$version)) $version='v26.0';
    $url='https://graph.facebook.com/'.$version.'/'.ltrim($path,'/');
    if($params!==[])$url.='?'.http_build_query($params);

    $token=trim((string)$account->token);
    if($token==='')throw new RuntimeException('У FB-профиля нет сохранённого access token.');

    $ch=curl_init($url);
    $opts=[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>12,
        CURLOPT_TIMEOUT=>40,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Accept: application/json','Authorization: Bearer '.$token],
        CURLOPT_USERAGENT=>'ReMask-PageSync/1.0',
    ];
    $cookies=rmx_page_helper_cookies($account);
    if($cookies!=='')$opts[CURLOPT_COOKIE]=$cookies;
    if($useProxy && $account->proxy!==null)$account->proxy->AddToCurlOptions($opts);

    curl_setopt_array($ch,$opts);
    $raw=curl_exec($ch);
    $curlError=curl_error($ch);
    $curlErrno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if($raw===false)throw new RuntimeException('TRANSPORT: '.($curlError ?: ('cURL errno '.$curlErrno)));
    $decoded=json_decode($raw,true);
    if(!is_array($decoded))throw new RuntimeException('Meta вернула не JSON, HTTP '.$http.'.');
    if(is_array($decoded['error']??null)){
        $e=$decoded['error'];
        $msg=trim((string)($e['message']??'Meta rejected request.'));
        $code=(int)($e['code']??0);
        $sub=(int)($e['error_subcode']??0);
        if($code)$msg.='; code '.$code;
        if($sub)$msg.='; subcode '.$sub;
        throw new RuntimeException($msg);
    }
    if($http<200||$http>=300)throw new RuntimeException('Meta API HTTP '.$http.'.');
    return $decoded;
}
function rmx_page_helper_call(FbAccount $account, string $path, array $params=[]): array {
    // Preserve one network identity per FB profile: never bypass a configured proxy.
    return rmx_page_helper_graph($account,$path,$params,$account->proxy!==null);
}
function rmx_page_helper_list_pages(FbAccount $account): array {
    $pages=[];
    $after='';
    $seenCursors=[];
    for($pageNo=0;$pageNo<30;$pageNo++){
        $params=[
            'fields'=>'id,name,category,access_token',
            'limit'=>200,
        ];
        if($after!=='')$params['after']=$after;
        $raw=rmx_page_helper_call($account,'me/accounts',$params);
        foreach((array)($raw['data']??[]) as $row){
            if(!is_array($row))continue;
            $id=trim((string)($row['id']??''));
            if($id==='')continue;
            $pages[$id]=[
                'id'=>$id,
                'name'=>(string)($row['name']??$id),
                'category'=>(string)($row['category']??''),
            ];
        }
        $next=trim((string)($raw['paging']['cursors']['after']??''));
        $hasNext=trim((string)($raw['paging']['next']??''))!=='';
        if(!$hasNext||$next==='')break;
        if(isset($seenCursors[$next]))break;
        $seenCursors[$next]=true;
        $after=$next;
    }
    return array_values($pages);
}

try{
    $action=trim((string)($_POST['action']??$_GET['action']??'list_pages'));
    if($action==='csrf'){
        rmx_page_helper_out(['ok'=>true,'csrf'=>remask_csrf_token()]);
    }
    if($action!=='list_pages')throw new InvalidArgumentException('Unsupported action.');

    $hint=trim((string)($_POST['profile']??$_GET['profile']??''));
    $account=rmx_page_helper_account($hint);
    $pages=rmx_page_helper_list_pages($account);
    error_log('[page-helper] list_pages profile_hash='.substr(hash('sha256',(string)$account->name),0,12).' count='.count($pages));
    rmx_page_helper_out([
        'ok'=>true,
        'profile'=>(string)$account->name,
        'profile_fb_id'=>rmx_page_helper_cookie_value($account,'c_user'),
        'pages'=>$pages,
    ]);
}catch(Throwable $e){
    rmx_page_helper_out(['ok'=>false,'error'=>$e->getMessage()],400);
}
PHP_ENDPOINT;

$script = <<<'JS'
(function(){
  'use strict';
  var endpoint='ajax/metaPageHelper.php';

  function parseResponse(response){
    return response.text().then(function(text){
      var data={};
      try{data=JSON.parse(text);}catch(e){}
      if(!response.ok || data.ok===false){
        var err=data.error;
        if(err && typeof err==='object')err=err.message||JSON.stringify(err);
        throw new Error(err||data.message||('HTTP '+response.status));
      }
      return data;
    });
  }
  var csrfPromise=null;
  function getCsrf(){
    if(csrfPromise)return csrfPromise;
    csrfPromise=fetch(endpoint+'?action=csrf',{
      method:'GET',
      credentials:'same-origin',
      cache:'no-store'
    }).then(parseResponse).then(function(data){
      if(!data.csrf)throw new Error('ReMask CSRF token missing.');
      return data.csrf;
    }).catch(function(e){csrfPromise=null;throw e;});
    return csrfPromise;
  }
  function api(payload){
    var body={};
    Object.keys(payload||{}).forEach(function(k){body[k]=String(payload[k]??'');});
    return getCsrf().then(function(csrf){
      body.remask_csrf=csrf;
      return fetch(endpoint,{
        method:'POST',
        credentials:'same-origin',
        headers:{
          'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8',
          'X-REMASK-CSRF':csrf
        },
        body:new URLSearchParams(body)
      }).then(parseResponse);
    });
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
  function setStatus(dialog,text,error){
    var el=dialog.querySelector('[data-remask-page-status]');
    if(!el){
      el=document.createElement('div');
      el.setAttribute('data-remask-page-status','1');
      el.style.marginTop='8px';
      el.style.fontSize='12px';
      var select=primarySelect(dialog);
      var anchor=(select&&select.parentElement)||dialog;
      anchor.appendChild(el);
    }
    el.style.color=error?'#ff7b7b':'#83dd99';
    el.textContent=text;
  }
  function setPageCount(dialog,count){
    var nodes=[].slice.call(dialog.querySelectorAll('*'));
    var countNode=nodes.find(function(el){
      return /^\d+\s+Pages$/i.test(String(el.textContent||'').trim());
    });
    if(countNode)countNode.textContent=String(count)+' Pages';
  }
  function replacePageOptions(select,pages){
    if(!select)return;
    var placeholder=[].slice.call(select.options).find(function(o){
      return /Primary Page|выберите|select/i.test(String(o.textContent||''));
    });
    select.innerHTML='';
    if(placeholder)select.appendChild(placeholder);
    else{
      var empty=document.createElement('option');
      empty.value='';
      empty.textContent='Primary Page';
      select.appendChild(empty);
    }
    pages.forEach(function(page){
      var option=document.createElement('option');
      option.value=String(page.id);
      option.textContent=(page.name||'Page')+' ('+page.id+')';
      select.appendChild(option);
    });
    if(pages.length){
      select.value=String(pages[0].id);
      select.dispatchEvent(new Event('input',{bubbles:true}));
      select.dispatchEvent(new Event('change',{bubbles:true}));
    }
  }
  function refresh(dialog){
    var profile=profileHint(dialog);
    var select=primarySelect(dialog);
    if(select)select.disabled=true;
    setStatus(dialog,'Загружаю Pages из Facebook…',false);

    api({action:'list_pages',profile:profile}).then(function(data){
      var pages=Array.isArray(data.pages)?data.pages:[];
      replacePageOptions(select,pages);
      setPageCount(dialog,pages.length);
      if(pages.length){
        setStatus(dialog,'Pages загружены: '+pages.length+'. Первая выбрана как Primary Page.',false);
      }else{
        setStatus(dialog,'У этого FB-аккаунта Meta вернула 0 Pages.',true);
      }
    }).catch(function(e){
      setStatus(dialog,'Не удалось загрузить Pages: '+e.message,true);
    }).finally(function(){
      if(select)select.disabled=false;
    });
  }
  function enhanceBmDialog(){
    var dialog=getDialog();
    if(!dialog||dialog.getAttribute('data-remask-page-sync')==='1')return;
    var select=primarySelect(dialog);
    if(!select)return;

    var reload=document.createElement('button');
    reload.type='button';
    reload.textContent='Обновить Pages';
    reload.className='btn btn-secondary';
    reload.style.marginTop='8px';
    reload.addEventListener('click',function(){refresh(dialog);});
    select.parentElement.appendChild(reload);

    dialog.setAttribute('data-remask-page-sync','1');
    refresh(dialog);
  }
  function enhance(){enhanceBmDialog();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhance);
  else enhance();
  new MutationObserver(enhance).observe(document.documentElement,{childList:true,subtree:true});
})();
JS;

file_put_contents($endpointPath,$endpoint);
file_put_contents($scriptPath,$script);

$workspace=file_get_contents($workspacePath);
if($workspace===false)throw new RuntimeException('workspace.php not found');
$tag='<script src="scripts/page-helper.js?v=20260918-page-helper-v11"></script>';
if(strpos($workspace,'scripts/page-helper.js')===false){
    if(stripos($workspace,'</body>')!==false)$workspace=str_ireplace('</body>',$tag."\n</body>",$workspace);
    else $workspace.="\n".$tag."\n";
}
file_put_contents($workspacePath,$workspace);
fwrite(STDERR,"[page-helper] existing Facebook Pages sync installed; Add FP removed\n");
