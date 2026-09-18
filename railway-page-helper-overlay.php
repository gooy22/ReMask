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
function rmx_page_helper_graph(FbAccount $account, string $method, string $path, array $params, bool $useProxy): array {
    $version=getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/',$version)) $version='v26.0';
    $url='https://graph.facebook.com/'.$version.'/'.ltrim($path,'/');
    $method=strtoupper($method);
    if ($method==='GET' && $params!==[]) $url.='?'.http_build_query($params);
    $token=trim((string)$account->token);
    if ($token==='') throw new RuntimeException('У FB-профиля нет сохранённого access token.');

    $ch=curl_init($url);
    $opts=[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>12,
        CURLOPT_TIMEOUT=>40,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Accept: application/json','Authorization: Bearer '.$token],
        CURLOPT_USERAGENT=>'ReMask-PageHelper/2.0',
    ];
    if ($method==='POST') {
        $opts[CURLOPT_POST]=true;
        $opts[CURLOPT_POSTFIELDS]=http_build_query($params);
        $opts[CURLOPT_HTTPHEADER][]='Content-Type: application/x-www-form-urlencoded';
    }
    $cookies=rmx_page_helper_cookies($account);
    if ($cookies!=='') $opts[CURLOPT_COOKIE]=$cookies;
    if ($useProxy && $account->proxy !== null) $account->proxy->AddToCurlOptions($opts);

    curl_setopt_array($ch,$opts);
    $raw=curl_exec($ch);
    $curlError=curl_error($ch);
    $curlErrno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($raw===false) {
        $e=new RuntimeException('TRANSPORT: '.($curlError ?: ('cURL errno '.$curlErrno)));
        throw $e;
    }
    $decoded=json_decode($raw,true);
    if (!is_array($decoded)) throw new RuntimeException('Meta вернула не JSON, HTTP '.$http.'.');
    if (is_array($decoded['error']??null)) {
        $e=$decoded['error'];
        $msg=trim((string)($e['message']??'Meta rejected request.'));
        $type=trim((string)($e['type']??''));
        $code=(int)($e['code']??0);
        $sub=(int)($e['error_subcode']??0);
        if($type!=='')$msg.='; type '.$type;
        if($code)$msg.='; code '.$code;
        if($sub)$msg.='; subcode '.$sub;
        throw new RuntimeException($msg);
    }
    if ($http<200 || $http>=300) throw new RuntimeException('Meta API HTTP '.$http.'.');
    return $decoded;
}

function rmx_page_helper_call(FbAccount $account, string $method, string $path, array $params=[]): array {
    try {
        return rmx_page_helper_graph($account,$method,$path,$params,$account->proxy!==null);
    } catch (Throwable $first) {
        if ($account->proxy===null || !str_starts_with($first->getMessage(),'TRANSPORT:')) throw $first;
        return rmx_page_helper_graph($account,$method,$path,$params,false);
    }
}
function rmx_page_helper_permissions(FbAccount $account): array {
    $raw=rmx_page_helper_call($account,'GET','me/permissions',['limit'=>200]);
    $granted=[];
    foreach((array)($raw['data']??[]) as $row){
        if(!is_array($row))continue;
        if(($row['status']??'')==='granted')$granted[(string)($row['permission']??'')]=true;
    }
    return $granted;
}
function rmx_page_helper_flatten_categories(array $rows, array &$out): void {
    foreach($rows as $row){
        if(!is_array($row))continue;
        $enum=trim((string)($row['api_enum']??''));
        $name=trim((string)($row['name']??''));
        if($enum!=='')$out[]=['api_enum'=>$enum,'name'=>$name!==''?$name:$enum];
        $children=$row['fb_page_categories']??[];
        if(is_array($children))rmx_page_helper_flatten_categories($children,$out);
    }
}
function rmx_page_helper_resolve_hints(array $hints): array {
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts=array_values(array_filter((array)$store->deserialize(),static fn($x)=>$x instanceof FbAccount));
    $resolved=[];
    foreach($hints as $hint){
        $hint=trim((string)$hint);
        if($hint==='')continue;
        foreach($accounts as $account){
            $name=trim((string)$account->name);
            if($name!=='' && ($hint===$name || str_contains($hint,$name))){
                $resolved[$name]=$account;
                break;
            }
        }
    }
    if($resolved===[] && count($accounts)===1)$resolved[(string)$accounts[0]->name]=$accounts[0];
    return array_values($resolved);
}

try {
    $action=trim((string)($_POST['action']??'list_pages'));
    $profile=trim((string)($_POST['profile']??''));

    if($action==='categories'){
        $account=rmx_page_helper_account($profile);
        $raw=rmx_page_helper_call($account,'GET','fb_page_categories',[
            'fields'=>'id,name,api_enum,fb_page_categories{id,name,api_enum}',
            'limit'=>500,
        ]);
        $flat=[];
        rmx_page_helper_flatten_categories((array)($raw['data']??[]),$flat);
        $seen=[];$out=[];
        foreach($flat as $row){
            $key=(string)$row['api_enum'];
            if($key===''||isset($seen[$key]))continue;
            $seen[$key]=true;$out[]=$row;
        }
        usort($out,static fn($a,$b)=>strcmp((string)$a['name'],(string)$b['name']));
        rmx_page_helper_out(['ok'=>true,'categories'=>$out]);
    }

    if($action==='create_pages'){
        $hints=json_decode((string)($_POST['profile_hints']??'[]'),true);
        if(!is_array($hints))$hints=[];
        $accounts=rmx_page_helper_resolve_hints($hints);
        if($accounts===[])throw new RuntimeException('Не удалось определить выбранные FB-аккаунты.');

        $name=trim((string)($_POST['name']??''));
        $category=trim((string)($_POST['category_enum']??''));
        $about=trim((string)($_POST['about']??''));
        if($name==='')throw new InvalidArgumentException('Название Page обязательно.');
        if($category==='')throw new InvalidArgumentException('Категория Page обязательна.');

        $results=[];
        foreach($accounts as $index=>$account){
            $label=(string)$account->name;
            try{
                $permissions=rmx_page_helper_permissions($account);
                foreach(['pages_manage_metadata','pages_show_list'] as $need){
                    if(empty($permissions[$need]))throw new RuntimeException('Нет permission '.$need.' у сохранённого User token.');
                }

                $me=rmx_page_helper_call($account,'GET','me',['fields'=>'id,name']);
                $userId=trim((string)($me['id']??''));
                if($userId==='')throw new RuntimeException('Meta не вернула user_id.');

                $before=rmx_page_helper_call($account,'GET',$userId.'/accounts',['fields'=>'id,name,category','limit'=>200]);
                $beforeIds=[];
                foreach((array)($before['data']??[]) as $p)if(is_array($p)&&!empty($p['id']))$beforeIds[(string)$p['id']]=true;

                $pageName=count($accounts)>1 ? str_replace('{n}',(string)($index+1),$name) : $name;
                $payload=['name'=>$pageName,'category_enum'=>$category,'no_notification'=>'true'];
                if($about!=='')$payload['about']=$about;
                $created=rmx_page_helper_call($account,'POST',$userId.'/accounts',$payload);

                $after=rmx_page_helper_call($account,'GET',$userId.'/accounts',['fields'=>'id,name,category','limit'=>200]);
                $page=null;
                foreach((array)($after['data']??[]) as $p){
                    if(!is_array($p)||empty($p['id']))continue;
                    $id=(string)$p['id'];
                    if(!isset($beforeIds[$id]) || (string)($p['name']??'')===$pageName){$page=$p;break;}
                }
                if($page===null && !empty($created['id']))$page=['id'=>(string)$created['id'],'name'=>$pageName];

                $results[]=['profile'=>$label,'ok'=>true,'page'=>$page,'raw_created'=>$created];
            }catch(Throwable $e){
                $msg=$e->getMessage();
                $kind='META_API';
                $low=strtolower($msg);
                if(str_contains($low,'allowlist')||str_contains($low,'whitelist'))$kind='APP_ALLOWLIST_REQUIRED';
                elseif(str_contains($low,'permission'))$kind='PERMISSION';
                elseif(str_starts_with($msg,'TRANSPORT:'))$kind='TRANSPORT';
                $results[]=['profile'=>$label,'ok'=>false,'error'=>$msg,'error_kind'=>$kind];
            }
        }
        rmx_page_helper_out(['ok'=>true,'results'=>$results]);
    }

    $account=rmx_page_helper_account($profile);
    $result=rmx_page_helper_call($account,'GET','me/accounts',['fields'=>'id,name,category','limit'=>200]);
    rmx_page_helper_out([
        'ok'=>true,
        'profile'=>(string)$account->name,
        'pages'=>array_values((array)($result['data']??[])),
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

  function bindDirectFpButton(){
    [].slice.call(document.querySelectorAll('[data-remask-fp-action]')).forEach(function(btn){
      if(btn.getAttribute('data-remask-fp-bound')==='1')return;
      btn.setAttribute('data-remask-fp-bound','1');
      btn.addEventListener('click',function(e){
        e.preventDefault();
        e.stopPropagation();
        window.open(createUrl,'_blank','noopener');
      },true);
    });
  }

  function enhance(){
    enhanceBmDialog();
    bindDirectFpButton();
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhance);
  else enhance();
  new MutationObserver(enhance).observe(document.documentElement,{childList:true,subtree:true});
})();
JS;

file_put_contents($endpointPath,$endpoint);
file_put_contents($scriptPath,$script);

$workspaceJsPath=$root . '/scripts/workspace.js';
$workspaceJs=file_get_contents($workspaceJsPath);
if($workspaceJs===false)throw new RuntimeException('workspace.js not found');
$oldMenu='<button data-act="add_bm"><i class="fa-solid fa-building"></i> Добавить BM</button><button data-act="assign_proxy">';
$newMenu='<button data-act="add_bm"><i class="fa-solid fa-building"></i> Добавить BM</button><button data-remask-fp-action="1"><i class="fa-solid fa-flag"></i> Добавить FP</button><button data-act="assign_proxy">';
$workspaceJs=str_replace($oldMenu,$newMenu,$workspaceJs,$workspaceMenuPatchCount);
if($workspaceMenuPatchCount!==1)throw new RuntimeException('Direct Add FP menu patch failed: '.(string)$workspaceMenuPatchCount);
file_put_contents($workspaceJsPath,$workspaceJs);
fwrite(STDERR,"[page-helper] Add FP inserted directly into buildActionMenu\n");

$workspace=file_get_contents($workspacePath);
if($workspace===false)throw new RuntimeException('workspace.php not found');
$tag='<script src="scripts/page-helper.js?v=20260918-page-helper-v6"></script>';
if(strpos($workspace,'scripts/page-helper.js')===false){
    if(stripos($workspace,'</body>')!==false)$workspace=str_ireplace('</body>',$tag."\n</body>",$workspace);
    else $workspace.="\n".$tag."\n";
}
file_put_contents($workspacePath,$workspace);
fwrite(STDERR,"[page-helper] BM modal + account action menu Page helper installed\n");
