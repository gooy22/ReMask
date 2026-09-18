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
        $matches=rmx_page_helper_resolve_hints([$profile]);
        $account=$matches[0]??null;
        if(!$account instanceof FbAccount)$account=rmx_page_helper_account($profile);
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
  function api(payload){
    var body={};
    payload=payload||{};
    Object.keys(payload).forEach(function(k){
      var v=payload[k];
      body[k]=(typeof v==='string')?v:JSON.stringify(v);
    });
    return parseResponse(fetch(endpoint,{
      method:'POST',
      credentials:'same-origin',
      headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
      body:new URLSearchParams(body)
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
    api({action:'list_pages',profile:profile}).then(function(data){
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
        setStatus(dialog,'Meta возвращает 0 Pages для этого FB-профиля.',true);
      }
    }).catch(function(e){
      setStatus(dialog,'Не удалось обновить Pages: '+e.message,true);
    });
  }

  function selectedProfileHints(){
    var out=[];
    [].slice.call(document.querySelectorAll('input[type="checkbox"]:checked')).forEach(function(cb){
      var row=cb.closest('tr');
      if(!row)return;
      var text=String(row.textContent||'').replace(/\s+/g,' ').trim();
      if(text && out.indexOf(text)===-1)out.push(text);
    });
    return out;
  }
  function injectStyle(){
    if(document.getElementById('remask-fp-modal-style'))return;
    var style=document.createElement('style');
    style.id='remask-fp-modal-style';
    style.textContent='.rmx-fp-layer{position:fixed;inset:0;z-index:12000;background:rgba(0,0,0,.68);display:flex;align-items:center;justify-content:center;padding:16px}.rmx-fp-card{width:min(620px,96vw);background:#20252e;border:1px solid #4d5868;border-radius:14px;color:#eef3fb;box-shadow:0 24px 80px rgba(0,0,0,.55)}.rmx-fp-head,.rmx-fp-foot{display:flex;align-items:center;justify-content:space-between;padding:16px 18px}.rmx-fp-head{border-bottom:1px solid #3b4452}.rmx-fp-foot{border-top:1px solid #3b4452;justify-content:flex-end;gap:10px}.rmx-fp-body{padding:16px 18px}.rmx-fp-field{margin-bottom:13px}.rmx-fp-field label{display:block;font-size:12px;color:#aab5c4;margin-bottom:6px}.rmx-fp-field input,.rmx-fp-field select,.rmx-fp-field textarea{width:100%;box-sizing:border-box;background:#262d37;color:#eef3fb;border:1px solid #4b5666;border-radius:8px;padding:10px 12px}.rmx-fp-btn{border:1px solid #536071;background:#2b333e;color:#fff;border-radius:8px;padding:9px 13px;cursor:pointer}.rmx-fp-btn.primary{background:#3478f6;border-color:#3478f6}.rmx-fp-status{font-size:12px;line-height:1.5;white-space:pre-wrap;margin-top:8px}.rmx-fp-muted{font-size:12px;color:#9da9ba;margin-bottom:12px}';
    document.head.appendChild(style);
  }
  function openCreateModal(hints){
    injectStyle();
    hints=hints||selectedProfileHints();
    if(!hints.length){alert('Сначала выбери FB-аккаунт.');return;}

    var layer=document.createElement('div');
    layer.className='rmx-fp-layer';
    layer.innerHTML='<div class="rmx-fp-card"><div class="rmx-fp-head"><strong>Добавить Facebook Page</strong><button type="button" class="rmx-fp-btn" data-close>×</button></div><div class="rmx-fp-body"><div class="rmx-fp-muted">Выбрано FB-аккаунтов: '+hints.length+'. ReMask создаст Page запросом к Meta без перехода в Facebook.</div><div class="rmx-fp-field"><label>Название Page</label><input data-name placeholder="'+(hints.length>1?'Page {n}':'Название Page')+'"></div><div class="rmx-fp-field"><label>Категория</label><select data-category><option value="">Загрузка категорий Meta…</option></select></div><div class="rmx-fp-field"><label>Описание (необязательно)</label><textarea data-about rows="3"></textarea></div><div class="rmx-fp-status" data-status></div></div><div class="rmx-fp-foot"><button type="button" class="rmx-fp-btn" data-cancel>Отмена</button><button type="button" class="rmx-fp-btn primary" data-create>Создать FP</button></div></div>';
    document.body.appendChild(layer);

    var close=function(){layer.remove();};
    layer.querySelector('[data-close]').onclick=close;
    layer.querySelector('[data-cancel]').onclick=close;
    layer.addEventListener('mousedown',function(e){if(e.target===layer)close();});

    var category=layer.querySelector('[data-category]');
    var status=layer.querySelector('[data-status]');
    api({action:'categories',profile:hints[0]}).then(function(data){
      category.innerHTML='<option value="">Выбери категорию</option>';
      (data.categories||[]).forEach(function(c){
        var o=document.createElement('option');
        o.value=c.api_enum;
        o.textContent=c.name+' — '+c.api_enum;
        category.appendChild(o);
      });
      if(!(data.categories||[]).length)throw new Error('Meta не вернула категории Page.');
    }).catch(function(e){
      category.innerHTML='<option value="">Категории недоступны</option>';
      status.style.color='#ff8080';
      status.textContent=e.message;
    });

    layer.querySelector('[data-create]').onclick=function(){
      var name=layer.querySelector('[data-name]').value.trim();
      var about=layer.querySelector('[data-about]').value.trim();
      if(!name||!category.value){
        status.style.color='#ff8080';
        status.textContent='Заполни название и категорию.';
        return;
      }
      var btn=layer.querySelector('[data-create]');
      btn.disabled=true;
      status.style.color='#c7d2e2';
      status.textContent='Создаю FP через Meta API…';

      api({
        action:'create_pages',
        profile_hints:hints,
        name:name,
        category_enum:category.value,
        about:about
      }).then(function(data){
        var results=data.results||[];
        var ok=results.filter(function(r){return r.ok;});
        var fail=results.filter(function(r){return !r.ok;});
        var lines=[];
        ok.forEach(function(r){
          lines.push('✓ '+r.profile+': '+((r.page&&r.page.name)||'Page создана')+((r.page&&r.page.id)?' ['+r.page.id+']':''));
        });
        fail.forEach(function(r){
          lines.push('✕ '+r.profile+': ['+(r.error_kind||'META_API')+'] '+(r.error||'Ошибка Meta'));
        });
        status.style.color=fail.length?'#ffd27a':'#83dd99';
        status.textContent=lines.join('\n') || 'Meta не вернула результат.';
        if(ok.length){
          window.__remaskLastCreatedPages=ok.map(function(r){return r.page;}).filter(Boolean);
          var bm=getDialog();
          if(bm)refresh(bm);
        }
      }).catch(function(e){
        status.style.color='#ff8080';
        status.textContent=e.message;
      }).finally(function(){btn.disabled=false;});
    };
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
    create.textContent='Добавить FP';
    create.className='btn btn-primary';
    create.addEventListener('click',function(){openCreateModal([profileHint(dialog)]);});

    var reload=document.createElement('button');
    reload.type='button';
    reload.textContent='Обновить Pages';
    reload.className='btn btn-secondary';
    reload.addEventListener('click',function(){refresh(dialog);});

    wrap.appendChild(create);
    wrap.appendChild(reload);
    anchor.appendChild(wrap);
    dialog.setAttribute('data-remask-page-helper','1');
    refresh(dialog);
  }

  function bindDirectFpButton(){
    [].slice.call(document.querySelectorAll('[data-remask-fp-action]')).forEach(function(btn){
      if(btn.getAttribute('data-remask-fp-bound')==='1')return;
      btn.setAttribute('data-remask-fp-bound','1');
      btn.addEventListener('click',function(e){
        e.preventDefault();
        e.stopImmediatePropagation();
        openCreateModal(selectedProfileHints());
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
$tag='<script src="scripts/page-helper.js?v=20260918-page-helper-v7"></script>';
if(strpos($workspace,'scripts/page-helper.js')===false){
    if(stripos($workspace,'</body>')!==false)$workspace=str_ireplace('</body>',$tag."\n</body>",$workspace);
    else $workspace.="\n".$tag."\n";
}
file_put_contents($workspacePath,$workspace);
fwrite(STDERR,"[page-helper] BM modal + account action menu Page helper installed\n");
