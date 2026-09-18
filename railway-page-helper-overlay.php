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
require_once __DIR__ . '/../classes/MetaEndpoint.php';

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
function rmx_page_helper_profile_name(string $hint): string {
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts=array_values(array_filter((array)$store->deserialize(),static fn($x)=>$x instanceof FbAccount));
    $hint=trim($hint);

    if($hint!==''){
        foreach($accounts as $account){
            if(trim((string)$account->name)===$hint)return (string)$account->name;
        }
        foreach($accounts as $account){
            if(rmx_page_helper_cookie_value($account,'c_user')===$hint)return (string)$account->name;
        }
        foreach($accounts as $account){
            $name=trim((string)$account->name);
            $cUser=rmx_page_helper_cookie_value($account,'c_user');
            if(($name!=='' && str_contains($hint,$name)) || ($cUser!=='' && str_contains($hint,$cUser))) {
                return (string)$account->name;
            }
        }
    }
    if(count($accounts)===1)return (string)$accounts[0]->name;
    throw new RuntimeException('Не удалось сопоставить выбранный FB-аккаунт с сохранённым профилем ReMask.');
}

try{
    $action=trim((string)($_POST['action']??$_GET['action']??'list_pages'));
    if($action==='csrf'){
        rmx_page_helper_out(['ok'=>true,'csrf'=>remask_csrf_token()]);
    }
    if($action!=='list_pages')throw new InvalidArgumentException('Unsupported action.');

    $hint=trim((string)($_POST['profile']??$_GET['profile']??''));
    $profile=rmx_page_helper_profile_name($hint);

    // One canonical Meta transport for Pages/BM/RK/Launch:
    // MetaEndpoint -> MetaAdsService -> MetaApiClient(profile token + profile proxy).
    $result=MetaEndpoint::cachedAsset($profile,'pages','',true);
    $pages=[];
    foreach((array)($result['data']??[]) as $row){
        if(!is_array($row))continue;
        $id=trim((string)($row['id']??''));
        if($id==='')continue;
        $pages[]=[
            'id'=>$id,
            'name'=>(string)($row['name']??$id),
            'category'=>(string)($row['category']??''),
            'instagram_business_account'=>is_array($row['instagram_business_account']??null)?$row['instagram_business_account']:null,
        ];
    }

    $account=MetaEndpoint::accountForName($profile);
    error_log('[page-helper] canonical list_pages profile_hash='.substr(hash('sha256',$profile),0,12).' count='.count($pages).' proxy='.(($account->proxy??null)!==null?'configured':'none'));

    rmx_page_helper_out([
        'ok'=>true,
        'profile'=>$profile,
        'profile_fb_id'=>rmx_page_helper_cookie_value($account,'c_user'),
        'pages'=>$pages,
        'transport'=>[
            'canonical'=>true,
            'proxy_configured'=>($account->proxy??null)!==null,
            'session_context'=>$account->isLegacyReady(),
        ],
    ]);
}catch(Throwable $e){
    $error=['message'=>$e->getMessage(),'type'=>get_class($e)];
    if(method_exists($e,'toArray')){
        try{$error=array_replace($error,(array)$e->toArray());}catch(Throwable){}
    }
    rmx_page_helper_out(['ok'=>false,'error'=>$error],400);
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
$tag='<script src="scripts/page-helper.js?v=20260918-page-helper-v12"></script>';
if(strpos($workspace,'scripts/page-helper.js')===false){
    if(stripos($workspace,'</body>')!==false)$workspace=str_ireplace('</body>',$tag."\n</body>",$workspace);
    else $workspace.="\n".$tag."\n";
}
file_put_contents($workspacePath,$workspace);
fwrite(STDERR,"[page-helper] existing Facebook Pages sync installed; Add FP removed\n");
