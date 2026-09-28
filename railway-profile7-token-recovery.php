<?php
declare(strict_types=1);

ob_start();
require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

function tlog(array $row): void {
    fwrite(STDERR,'[profile7-token-recovery] '.json_encode($row,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE).PHP_EOL);
}
function targetUserId(FbAccount $acc): string {
    foreach((array)$acc->cookies as $cookie){
        if(!is_array($cookie)) continue;
        if((string)($cookie['name']??'')==='c_user'){
            $v=preg_replace('/\D+/','',(string)($cookie['value']??''));
            if(is_string($v)&&$v!=='') return $v;
        }
    }
    return '';
}
function probeToken(FbAccount $acc,string $token): array {
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
        CURLOPT_USERAGENT=>'ReMask-TokenRecovery/1.0',
    ];
    try{$acc->proxy?->AddToCurlOptions($opt);}catch(Throwable $e){}
    curl_setopt_array($ch,$opt);
    $raw=curl_exec($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $json=is_string($raw)?json_decode($raw,true):null;
    $err=is_array($json['error']??null)?$json['error']:[];
    return [
        'ok'=>is_array($json)&&$err===[]&&$http>=200&&$http<300,
        'http'=>$http,
        'curl_errno'=>$errno,
        'id'=>is_array($json)?preg_replace('/\D+/','',(string)($json['id']??'')):'',
        'code'=>(int)($err['code']??0),
        'subcode'=>(int)($err['error_subcode']??0),
        'type'=>(string)($err['type']??''),
        'message'=>isset($err['message'])?mb_substr((string)$err['message'],0,100):'',
    ];
}

$dataDir=getenv('REMASK_DATA_DIR') ?: '/var/lib/remask';
$marker=$dataDir.'/profile7-token-recovery-v1.done';
if(is_file($marker)){
    tlog(['phase'=>'skip','reason'=>'already_done']);
    exit(0);
}

$store=AccountStoreFactory::create((string)ACCOUNTSFILENAME);
$current=$store->getAccountByName('7');
if(!$current instanceof FbAccount){
    tlog(['phase'=>'stop','reason'=>'profile_not_found']);
    file_put_contents($marker,"profile_not_found\n");
    exit(0);
}
$targetId=targetUserId($current);
if($targetId===''){
    tlog(['phase'=>'stop','reason'=>'target_c_user_missing']);
    file_put_contents($marker,"target_id_missing\n");
    exit(0);
}

$candidates=[];
$filesScanned=0;
$bytesScanned=0;
$it=new RecursiveIteratorIterator(
    new RecursiveDirectoryIterator($dataDir,FilesystemIterator::SKIP_DOTS)
);
foreach($it as $fi){
    if(!$fi->isFile()) continue;
    $size=(int)$fi->getSize();
    if($size<=0 || $size>8*1024*1024) continue;
    $path=$fi->getPathname();
    $raw=@file_get_contents($path);
    if(!is_string($raw)||$raw==='') continue;
    $filesScanned++;
    $bytesScanned+=strlen($raw);
    if(!preg_match_all('/EAAB[A-Za-z0-9._-]{40,600}/',$raw,$m)) continue;
    foreach($m[0] as $token){
        $token=rtrim((string)$token,"\\\"' ,;)}]\r\n\t");
        if(strlen($token)<45||strlen($token)>700) continue;
        $hash=substr(hash('sha256',$token),0,12);
        if(!isset($candidates[$hash])){
            $candidates[$hash]=[
                'token'=>$token,
                'hash'=>$hash,
                'source'=>basename($path),
            ];
        }
        if(count($candidates)>=80) break 2;
    }
}

tlog([
    'phase'=>'scan',
    'files_scanned'=>$filesScanned,
    'bytes_scanned'=>$bytesScanned,
    'unique_candidates'=>count($candidates),
]);

$currentHash=substr(hash('sha256',trim((string)$current->token)),0,12);
$attempt=0;
foreach($candidates as $row){
    $attempt++;
    $probe=probeToken($current,$row['token']);
    $sameUser=$probe['ok'] && $probe['id']!=='' && hash_equals($targetId,$probe['id']);
    tlog([
        'phase'=>'probe',
        'attempt'=>$attempt,
        'token_hash'=>$row['hash'],
        'source'=>$row['source'],
        'is_current'=>hash_equals($currentHash,$row['hash']),
        'ok'=>$probe['ok'],
        'same_user'=>$sameUser,
        'http'=>$probe['http'],
        'code'=>$probe['code'],
        'subcode'=>$probe['subcode'],
        'type'=>$probe['type'],
        'message'=>$probe['message'],
    ]);
    if(!$sameUser) continue;

    @copy((string)ACCOUNTSFILENAME,(string)ACCOUNTSFILENAME.'.bak.before-token-recovery.'.gmdate('YmdHis'));
    $current->token=$row['token'];
    $store->addOrUpdateAccount($current);
    $verify=$store->getAccountByName('7');
    $persisted=$verify instanceof FbAccount
        && hash_equals($row['token'],(string)$verify->token);
    tlog([
        'phase'=>'recovered',
        'ok'=>$persisted,
        'token_hash'=>$row['hash'],
        'source'=>$row['source'],
        'same_user'=>true,
    ]);
    file_put_contents($marker,$persisted?"recovered\n":"persist_failed\n");
    exit($persisted?0:2);
}

tlog([
    'phase'=>'done',
    'recovered'=>false,
    'unique_candidates'=>count($candidates),
]);
file_put_contents($marker,"no_valid_candidate\n");
