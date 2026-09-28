<?php
declare(strict_types=1);

ob_start();
require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
require_once '/var/www/html/classes/MetaEndpoint.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

function sdlog(array $row): void {
    fwrite(STDERR, '[profile7-saved-context] ' . json_encode($row, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) . PHP_EOL);
}
function safeMessage(Throwable $e): string {
    $m=(string)$e->getMessage();
    $m=preg_replace('/access_token=[^&\\s]+/i','access_token=[redacted]',$m);
    $m=preg_replace('/Bearer\\s+[A-Za-z0-9._-]+/i','Bearer [redacted]',$m);
    return mb_substr((string)$m,0,600);
}

try {
    $store=AccountStoreFactory::create((string)ACCOUNTSFILENAME);
    $account=$store->getAccountByName('7');
    if(!$account instanceof FbAccount){
        sdlog(['phase'=>'skip','reason'=>'profile_not_found']);
        exit(0);
    }
    $cookieNames=[];
    foreach((array)$account->cookies as $cookie){
        if(is_array($cookie) && isset($cookie['name'])) $cookieNames[]=(string)$cookie['name'];
    }
    sdlog([
        'phase'=>'context',
        'token_hash'=>substr(hash('sha256',trim((string)$account->token)),0,12),
        'token_length'=>strlen(trim((string)$account->token)),
        'cookie_count'=>count((array)$account->cookies),
        'has_c_user'=>in_array('c_user',$cookieNames,true),
        'has_xs'=>in_array('xs',$cookieNames,true),
        'proxy_present'=>$account->proxy!==null,
    ]);

    $service=MetaEndpoint::serviceForAccountName('7');
    foreach([
        'identity'=>static fn()=>$service->getIdentity(),
        'ad_accounts'=>static fn()=>$service->listAdAccounts(1),
        'businesses'=>static fn()=>$service->listBusinesses(),
    ] as $stage=>$call){
        try{
            $result=$call();
            $data=is_array($result['data']??null)?$result['data']:[];
            sdlog([
                'phase'=>'probe',
                'stage'=>$stage,
                'ok'=>true,
                'rows'=>count($data),
                'identity_present'=>$stage==='identity'
                    ? trim((string)($result['id']??''))!==''
                    : null,
            ]);
        }catch(Throwable $e){
            sdlog([
                'phase'=>'probe',
                'stage'=>$stage,
                'ok'=>false,
                'class'=>get_class($e),
                'code'=>$e->getCode(),
                'message'=>safeMessage($e),
            ]);
        }
    }
}catch(Throwable $e){
    sdlog(['phase'=>'fatal','class'=>get_class($e),'message'=>safeMessage($e)]);
}
