<?php
$profile=trim((string)(getenv('REMASK_COOKIE_TOKEN_PROBE_PROFILE')?:''));
if($profile==='')exit(0);

require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/MetaApiClient.php';

function probe_fetch(FbAccount $acc,string $url,bool $useProxy): array {
    $ch=curl_init($url);
    $opts=[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>true,
        CURLOPT_MAXREDIRS=>5,
        CURLOPT_CONNECTTIMEOUT=>15,
        CURLOPT_TIMEOUT=>40,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_ENCODING=>'',
        CURLOPT_COOKIE=>$acc->getCurlCookies(),
        CURLOPT_USERAGENT=>'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
        CURLOPT_HTTPHEADER=>[
            'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language: en-US,en;q=0.9',
            'Cache-Control: no-cache',
            'Pragma: no-cache',
            'Upgrade-Insecure-Requests: 1',
        ],
    ];
    if($useProxy && $acc->proxy!==null)$acc->proxy->AddToCurlOptions($opts);
    curl_setopt_array($ch,$opts);
    $body=curl_exec($ch);
    $err=curl_error($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    $final=(string)curl_getinfo($ch,CURLINFO_EFFECTIVE_URL);
    curl_close($ch);
    if(!is_string($body))$body='';
    return ['http'=>$http,'errno'=>$errno,'error'=>$err,'final'=>$final,'body'=>$body];
}
function probe_tokens(string $body): array {
    $patterns=[
        '/accessToken\\?["\']?\s*[:=]\s*\\?["\'](EAA[A-Za-z0-9_\-]+)\\?["\']/i',
        '/["\']accessToken["\']\s*:\s*["\'](EAA[A-Za-z0-9_\-]+)["\']/i',
        '/access_token\\?["\']?\s*[:=]\s*\\?["\']?(EAA[A-Za-z0-9_\-]+)/i',
        '/\b(EAA[A-Za-z0-9_\-]{80,})\b/',
    ];
    $out=[];
    foreach($patterns as $p){
        if(preg_match_all($p,$body,$m)){
            foreach((array)($m[1]??[]) as $t){
                $t=trim((string)$t);
                if($t!=='' && strlen($t)>=80)$out[$t]=true;
            }
        }
    }
    return array_keys($out);
}
function probe_validate(string $token, FbAccount $acc): array {
    try{
        $c=new MetaApiClient($token,$acc->proxy,null,25);
        if($acc->isLegacyReady())$c->setSessionCookies($acc->getCurlCookies());
        $me=$c->get('me',['fields'=>'id,name']);
        return ['ok'=>isset($me['id']),'error'=>null,'code'=>null];
    }catch(Throwable $e){
        $o=['ok'=>false,'error'=>$e->getMessage(),'code'=>$e->getCode()];
        if(method_exists($e,'toArray')){
            try{
                $a=(array)$e->toArray();
                if(isset($a['http_status']))$o['http_status']=$a['http_status'];
                if(isset($a['type']))$o['type']=$a['type'];
                if(isset($a['code']))$o['code']=$a['code'];
            }catch(Throwable){}
        }
        return $o;
    }
}

try{
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $acc=$store->getAccountByName($profile);
    if(!$acc instanceof FbAccount)throw new RuntimeException('profile not found');
    $savedHash=substr(hash('sha256',(string)$acc->token),0,16);
    $urls=[
        'adsmanager'=>'https://www.facebook.com/adsmanager/manage/',
        'billing'=>'https://www.facebook.com/ads/manager/account_settings/account_billing/',
    ];
    $out=['profile'=>'configured','session_ready'=>$acc->isLegacyReady(),'proxy_configured'=>$acc->proxy!==null];
    foreach(['proxy'=>true,'direct'=>false] as $mode=>$useProxy){
        foreach($urls as $label=>$url){
            $r=probe_fetch($acc,$url,$useProxy);
            $tokens=probe_tokens($r['body']);
            $cands=[];
            foreach($tokens as $t){
                $h=substr(hash('sha256',$t),0,16);
                $cands[]=[
                    'hash'=>$h,
                    'len'=>strlen($t),
                    'same_as_saved'=>$h===$savedHash,
                    'validation'=>probe_validate($t,$acc),
                ];
            }
            $u=parse_url($r['final']);
            $body=$r['body'];
            $plain=html_entity_decode(strip_tags($body),ENT_QUOTES|ENT_HTML5,'UTF-8');
            $plain=preg_replace('/\\s+/u',' ',(string)$plain) ?? '';
            $plain=preg_replace('/EAA[A-Za-z0-9_\\-]{20,}/','EAA[redacted]',$plain) ?? $plain;
            $plain=preg_replace('/[A-Fa-f0-9]{32,}/','[redacted-long-id]',$plain) ?? $plain;
            $snippet=function_exists('mb_substr')?mb_substr(trim($plain),0,500):substr(trim($plain),0,500);
            preg_match('/<title[^>]*>(.*?)<\\/title>/is',$body,$tm);
            $title=isset($tm[1])?trim(html_entity_decode(strip_tags((string)$tm[1]),ENT_QUOTES|ENT_HTML5,'UTF-8')):'';
            $out[$mode][$label]=[
                'http'=>$r['http'],
                'errno'=>$r['errno'],
                'curl_error'=>$r['error'],
                'final_host'=>$u['host']??'',
                'final_path'=>$u['path']??'',
                'bytes'=>strlen($body),
                'title'=>$title,
                'snippet'=>$snippet,
                'login_marker'=>(stripos($body,'login_form')!==false || stripos($body,'login.php')!==false),
                'checkpoint_marker'=>(stripos($body,'checkpoint')!==false),
                'candidate_count'=>count($cands),
                'candidates'=>$cands,
            ];
        }
    }
    fwrite(STDERR,"[cookie-token-probe] ".json_encode($out,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE)."\n");
}catch(Throwable $e){
    fwrite(STDERR,"[cookie-token-probe] fatal ".get_class($e).": ".$e->getMessage()."\n");
}
