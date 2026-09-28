<?php
declare(strict_types=1);

ob_start();
require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

function slog(array $row): void {
    fwrite(STDERR, '[profile7-session-restore] ' . json_encode($row, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) . PHP_EOL);
}
function hasSession(FbAccount $acc): bool {
    $u=false;$x=false;
    foreach((array)$acc->cookies as $cookie){
        if(!is_array($cookie)) continue;
        $name=(string)($cookie['name']??'');
        $value=trim((string)($cookie['value']??''));
        if($name==='c_user' && $value!=='') $u=true;
        if($name==='xs' && $value!=='') $x=true;
    }
    return $u && $x;
}

$marker=(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask') . '/profile7-session-restore-v1.done';
if(is_file($marker)){
    slog(['phase'=>'skip','reason'=>'already_done']);
    exit(0);
}

$path=(string)ACCOUNTSFILENAME;
$store=AccountStoreFactory::create($path);
$current=$store->getAccountByName('7');
if(!$current instanceof FbAccount){
    slog(['phase'=>'skip','reason'=>'profile_not_found']);
    file_put_contents($marker,"profile_not_found\n");
    exit(0);
}
if(hasSession($current)){
    slog([
        'phase'=>'skip',
        'reason'=>'current_session_already_ready',
        'token_hash'=>substr(hash('sha256',trim((string)$current->token)),0,12),
        'cookie_count'=>count((array)$current->cookies),
    ]);
    file_put_contents($marker,"already_ready\n");
    exit(0);
}

$backups=glob($path.'.bak*')?:[];
usort($backups,static fn($a,$b)=>(@filemtime($b)?:0)<=> (@filemtime($a)?:0));
$source=null;
$backupAcc=null;
foreach(array_slice($backups,0,80) as $file){
    try{
        $candidateStore=AccountStoreFactory::create($file);
        $candidate=$candidateStore->getAccountByName('7');
        if(!$candidate instanceof FbAccount) continue;
        if(!hasSession($candidate)) continue;
        $source=$file;
        $backupAcc=$candidate;
        break;
    }catch(Throwable $e){
        continue;
    }
}
if(!$backupAcc instanceof FbAccount || $source===null){
    slog(['phase'=>'skip','reason'=>'no_session_backup_found','backups_scanned'=>count($backups)]);
    file_put_contents($marker,"no_backup\n");
    exit(0);
}

@copy($path,$path.'.bak.before-session-restore.'.gmdate('YmdHis'));
$cookieJson=json_encode(array_values((array)$backupAcc->cookies),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
$restored=new FbAccount(
    (string)$current->name,
    (string)$current->token,
    $cookieJson,
    $backupAcc->dtsg,
    $current->proxy
);
$store->addOrUpdateAccount($restored);
$fresh=$store->getAccountByName('7');
$ok=$fresh instanceof FbAccount && hasSession($fresh);

slog([
    'phase'=>'restore',
    'ok'=>$ok,
    'current_token_hash'=>substr(hash('sha256',trim((string)$current->token)),0,12),
    'source_token_hash'=>substr(hash('sha256',trim((string)$backupAcc->token)),0,12),
    'source_file'=>basename($source),
    'source_mtime'=>gmdate('c',(int)(@filemtime($source)?:0)),
    'cookie_count'=>$fresh instanceof FbAccount ? count((array)$fresh->cookies) : 0,
    'has_session'=>$ok,
    'token_preserved'=>$fresh instanceof FbAccount
        ? hash_equals((string)$current->token,(string)$fresh->token)
        : false,
]);
file_put_contents($marker,$ok?"restored\n":"restore_failed\n");
