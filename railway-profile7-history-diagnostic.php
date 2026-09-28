<?php
declare(strict_types=1);

// Read-only one-shot credential history diagnostic.
// Never prints token/cookie/proxy values.
$targetName = '7';
$dataDir = getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask');
$accountsFile = getenv('REMASK_ACCOUNTS_FILE') ?: ($dataDir . '/accounts.json');

function dlog(array $row): void {
    fwrite(STDERR, '[profile7-history] ' . json_encode($row, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) . PHP_EOL);
}
function isListArray(array $a): bool {
    return function_exists('array_is_list') ? array_is_list($a) : array_keys($a) === range(0, count($a)-1);
}
function findProfiles(mixed $node, string $wanted, array &$out, ?string $inheritedName=null, int $depth=0): void {
    if($depth>14 || !is_array($node)) return;
    $localName=$inheritedName;
    foreach(['name','profile_name','profile','label','fb_id','profile_id','account_id'] as $key){
        if(isset($node[$key]) && is_scalar($node[$key]) && trim((string)$node[$key])!==''){
            $localName=trim((string)$node[$key]);
            break;
        }
    }
    if($localName===$wanted){
        $token='';
        foreach(['token','access_token','accessToken','fb_token','meta_token'] as $key){
            if(isset($node[$key]) && is_scalar($node[$key]) && trim((string)$node[$key])!==''){
                $token=trim((string)$node[$key]);
                break;
            }
        }
        $cookies=[];
        foreach(['cookies','cookie','cookies_json','cookie_json','cookiesData','cookieData'] as $key){
            if(!array_key_exists($key,$node)) continue;
            $v=$node[$key];
            if(is_string($v)){
                $j=json_decode($v,true);
                if(is_array($j)) $v=$j;
            }
            if(is_array($v)) $cookies=$v;
            break;
        }
        $flatCookies=isListArray($cookies)?$cookies:array_values($cookies);
        $hasCUser=false;$hasXs=false;
        foreach($flatCookies as $cookie){
            if(!is_array($cookie)) continue;
            $name=(string)($cookie['name']??'');
            $value=trim((string)($cookie['value']??''));
            if($name==='c_user' && $value!=='') $hasCUser=true;
            if($name==='xs' && $value!=='') $hasXs=true;
        }
        $proxyPresent=false;
        foreach(['proxy','proxy_raw','proxyString','proxy_string','proxy_data','proxyData'] as $key){
            if(isset($node[$key]) && $node[$key]!=='' && $node[$key]!==[] && $node[$key]!==null){$proxyPresent=true;break;}
        }
        if($token!==''){
            $out[]=[
                'token'=>$token,
                'token_hash'=>substr(hash('sha256',$token),0,12),
                'token_length'=>strlen($token),
                'cookie_count'=>count($flatCookies),
                'has_c_user'=>$hasCUser,
                'has_xs'=>$hasXs,
                'proxy_present'=>$proxyPresent,
                'last_sync_at'=>(string)($node['last_sync_at']??''),
                'last_sync_error'=>(string)($node['last_sync_error']??''),
            ];
        }
    }
    foreach($node as $key=>$child){
        if(!is_array($child)) continue;
        $nextName=((string)$key===$wanted)?$wanted:$localName;
        findProfiles($child,$wanted,$out,$nextName,$depth+1);
    }
}
function graphProbe(string $token,string $variant): array {
    $url='';$headers=['Accept: application/json'];
    if($variant==='v26_bearer'){
        $url='https://graph.facebook.com/v26.0/me?fields=id%2Cname';
        $headers[]='Authorization: Bearer '.$token;
    }elseif($variant==='v26_query'){
        $url='https://graph.facebook.com/v26.0/me?fields=id%2Cname&access_token='.rawurlencode($token);
    }elseif($variant==='v25_bearer'){
        $url='https://graph.facebook.com/v25.0/me?fields=id%2Cname';
        $headers[]='Authorization: Bearer '.$token;
    }else{
        $url='https://graph.facebook.com/me?fields=id%2Cname';
        $headers[]='Authorization: Bearer '.$token;
    }
    $ch=curl_init($url);
    curl_setopt_array($ch,[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>8,
        CURLOPT_TIMEOUT=>18,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>$headers,
        CURLOPT_USERAGENT=>'ReMask-ProfileHistoryDiagnostic/1.0',
    ]);
    $raw=curl_exec($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $decoded=is_string($raw)?json_decode($raw,true):null;
    $error=is_array($decoded['error']??null)?$decoded['error']:[];
    return [
        'variant'=>$variant,
        'ok'=>is_array($decoded)&&$error===[]&&$http>=200&&$http<300,
        'http'=>$http,
        'curl_errno'=>$errno,
        'code'=>(int)($error['code']??0),
        'subcode'=>(int)($error['error_subcode']??0),
        'type'=>(string)($error['type']??''),
        'message'=>isset($error['message'])?mb_substr((string)$error['message'],0,160):'',
    ];
}

$files=[];
if(is_file($accountsFile)) $files[]=$accountsFile;
foreach(array_merge(
    glob($accountsFile.'.bak*')?:[],
    glob($accountsFile.'.bak.session-safe.*')?:[]
) as $file){
    if(is_file($file)) $files[]=$file;
}
$files=array_values(array_unique($files));
usort($files,static fn($a,$b)=>(@filemtime($b)?:0)<=> (@filemtime($a)?:0));
$files=array_slice($files,0,120);

$seen=[];
$candidates=[];
foreach($files as $file){
    $raw=@file_get_contents($file);
    if(!is_string($raw)||trim($raw)==='') continue;
    $json=json_decode($raw,true);
    if(!is_array($json)) continue;
    $rows=[];
    findProfiles($json,$targetName,$rows);
    foreach($rows as $row){
        $hash=$row['token_hash'];
        if(isset($seen[$hash])) continue;
        $seen[$hash]=true;
        $row['file']=basename($file);
        $row['file_mtime']=gmdate('c',(int)(@filemtime($file)?:0));
        $candidates[]=$row;
        if(count($candidates)>=16) break 2;
    }
}

dlog(['phase'=>'start','accounts_file'=>basename($accountsFile),'files_scanned'=>count($files),'unique_tokens'=>count($candidates)]);
foreach($candidates as $index=>$row){
    $token=$row['token'];
    unset($row['token']);
    dlog(['phase'=>'candidate','rank'=>$index+1]+$row);
    foreach(['v26_bearer','v26_query','v25_bearer','unversioned_bearer'] as $variant){
        $probe=graphProbe($token,$variant);
        dlog([
            'phase'=>'probe',
            'rank'=>$index+1,
            'token_hash'=>$row['token_hash'],
        ]+$probe);
    }
}
dlog(['phase'=>'done','unique_tokens'=>count($candidates)]);
