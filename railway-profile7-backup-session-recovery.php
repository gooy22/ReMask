<?php
declare(strict_types=1);

ob_start();
require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
require_once '/var/www/html/classes/FbRequests.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

function rlog(array $row): void {
    fwrite(STDERR, '[profile7-backup-session-recovery] ' . json_encode($row, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) . PHP_EOL);
}
function proxyHash(?object $proxy): string {
    if ($proxy === null) return '';
    $s='';
    foreach(['host','ip','server','port','username','user','login'] as $k){
        try{
            if(isset($proxy->$k)) $s.='|'.$k.'='.(string)$proxy->$k;
        }catch(Throwable $e){}
    }
    if($s===''){
        try{$s=(string)$proxy;}catch(Throwable $e){$s='';}
    }
    return $s!==''?substr(hash('sha256',$s),0,12):'';
}
function hasLegacySession(FbAccount $acc): bool {
    $u=false;$x=false;
    foreach((array)$acc->cookies as $cookie){
        if(!is_array($cookie)) continue;
        $name=(string)($cookie['name']??'');
        $value=trim((string)($cookie['value']??''));
        if($name==='c_user'&&$value!=='') $u=true;
        if($name==='xs'&&$value!=='') $x=true;
    }
    return $u&&$x;
}
function graphProbe(FbAccount $acc,string $token): array {
    $url='https://graph.facebook.com/v26.0/me?fields=id%2Cname&access_token='.rawurlencode($token);
    $ch=curl_init($url);
    $opt=[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>8,
        CURLOPT_TIMEOUT=>18,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Accept: application/json'],
        CURLOPT_USERAGENT=>'ReMask-BackupRecovery/1.0',
    ];
    try{$acc->proxy?->AddToCurlOptions($opt);}catch(Throwable $e){}
    curl_setopt_array($ch,$opt);
    $raw=curl_exec($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $decoded=is_string($raw)?json_decode($raw,true):null;
    $error=is_array($decoded['error']??null)?$decoded['error']:[];
    return [
        'ok'=>is_array($decoded)&&$error===[]&&$http>=200&&$http<300,
        'http'=>$http,
        'curl_errno'=>$errno,
        'code'=>(int)($error['code']??0),
        'subcode'=>(int)($error['error_subcode']??0),
        'type'=>(string)($error['type']??''),
        'message'=>isset($error['message'])?mb_substr((string)$error['message'],0,120):'',
    ];
}

$marker=(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask') . '/profile7-backup-session-recovery-v1.done';
if(is_file($marker)){
    rlog(['phase'=>'skip','reason'=>'already_done']);
    exit(0);
}

$path=(string)ACCOUNTSFILENAME;
$store=AccountStoreFactory::create($path);
$current=$store->getAccountByName('7');
if(!$current instanceof FbAccount){
    rlog(['phase'=>'stop','reason'=>'profile_not_found']);
    file_put_contents($marker,"profile_not_found\n");
    exit(0);
}
$currentProxyHash=proxyHash($current->proxy);

$backups=glob($path.'.bak*')?:[];
usort($backups,static fn($a,$b)=>(@filemtime($b)?:0)<=> (@filemtime($a)?:0));
$requests=new FbRequests();
$attempts=0;
$sessionCandidates=0;

foreach(array_slice($backups,0,120) as $file){
    try{
        $candidateStore=AccountStoreFactory::create($file);
        $candidate=$candidateStore->getAccountByName('7');
        if(!$candidate instanceof FbAccount) continue;
        if(!hasLegacySession($candidate)) continue;
        $sessionCandidates++;
        $candidateProxyHash=proxyHash($candidate->proxy);
        if($currentProxyHash!=='' && $candidateProxyHash!=='' && !hash_equals($currentProxyHash,$candidateProxyHash)){
            rlog([
                'phase'=>'candidate',
                'result'=>'skip_proxy_mismatch',
                'file'=>basename($file),
                'mtime'=>gmdate('c',(int)(@filemtime($file)?:0)),
            ]);
            continue;
        }

        $attempts++;
        $fresh=$requests->RefreshAdsManagerToken($candidate);
        $fresh=is_string($fresh)?trim($fresh):'';
        if($fresh===''){
            rlog([
                'phase'=>'candidate',
                'result'=>'ads_manager_session_inactive',
                'file'=>basename($file),
                'mtime'=>gmdate('c',(int)(@filemtime($file)?:0)),
                'cookie_count'=>count((array)$candidate->cookies),
            ]);
            continue;
        }

        $probe=graphProbe($candidate,$fresh);
        rlog([
            'phase'=>'candidate',
            'result'=>$probe['ok']?'fresh_token_valid':'fresh_token_rejected',
            'file'=>basename($file),
            'mtime'=>gmdate('c',(int)(@filemtime($file)?:0)),
            'fresh_token_hash'=>substr(hash('sha256',$fresh),0,12),
            'http'=>$probe['http'],
            'code'=>$probe['code'],
            'subcode'=>$probe['subcode'],
            'type'=>$probe['type'],
            'message'=>$probe['message'],
        ]);
        if(!$probe['ok']) continue;

        @copy($path,$path.'.bak.before-verified-session-recovery.'.gmdate('YmdHis'));
        $cookieJson=json_encode(array_values((array)$candidate->cookies),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
        $recovered=new FbAccount(
            (string)$current->name,
            $fresh,
            $cookieJson,
            $candidate->dtsg,
            $current->proxy
        );
        $store->addOrUpdateAccount($recovered);
        $verify=$store->getAccountByName('7');
        $persisted=$verify instanceof FbAccount
            && hash_equals($fresh,(string)$verify->token)
            && hasLegacySession($verify);
        rlog([
            'phase'=>'recovered',
            'ok'=>$persisted,
            'source_file'=>basename($file),
            'source_mtime'=>gmdate('c',(int)(@filemtime($file)?:0)),
            'fresh_token_hash'=>substr(hash('sha256',$fresh),0,12),
            'cookie_count'=>$verify instanceof FbAccount?count((array)$verify->cookies):0,
            'current_proxy_preserved'=>proxyHash($verify?->proxy)===$currentProxyHash,
        ]);
        file_put_contents($marker,$persisted?"recovered\n":"persist_failed\n");
        exit($persisted?0:2);
    }catch(Throwable $e){
        rlog([
            'phase'=>'candidate',
            'result'=>'exception',
            'file'=>basename($file),
            'class'=>get_class($e),
            'message'=>mb_substr((string)$e->getMessage(),0,180),
        ]);
    }
}

rlog([
    'phase'=>'done',
    'recovered'=>false,
    'backup_files'=>count($backups),
    'session_candidates'=>$sessionCandidates,
    'attempted'=>$attempts,
]);
file_put_contents($marker,"no_verified_session\n");
