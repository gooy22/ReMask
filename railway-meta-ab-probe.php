<?php
$profile = trim((string)(getenv('REMASK_AB_PROFILE') ?: ''));
if ($profile === '') exit(0);

require_once '/var/www/html/settings.php';
require_once '/var/www/html/classes/AccountStoreFactory.php';
require_once '/var/www/html/classes/FbAccount.php';
require_once '/var/www/html/classes/MetaApiClient.php';

function ab_step(callable $fn): array {
    try {
        $v = $fn();
        return ['ok'=>true,'has_id'=>isset($v['id']),'count'=>is_array($v['data']??null)?count($v['data']):null];
    } catch (Throwable $e) {
        $o=['ok'=>false,'message'=>$e->getMessage(),'code'=>$e->getCode(),'class'=>get_class($e)];
        if (method_exists($e,'toArray')) {
            try {
                $a=(array)$e->toArray();
                foreach(['http_status','type','code','subcode','error_subcode'] as $k) if(array_key_exists($k,$a)) $o[$k]=$a[$k];
            } catch(Throwable){}
        }
        return $o;
    }
}
function client_for(FbAccount $acc, bool $useProxy, bool $useCookies): MetaApiClient {
    $c = new MetaApiClient((string)$acc->token, $useProxy ? $acc->proxy : null, null, 35);
    if ($useCookies && $acc->isLegacyReady()) $c->setSessionCookies($acc->getCurlCookies());
    return $c;
}

try {
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $acc=$store->getAccountByName($profile);
    if(!$acc instanceof FbAccount) throw new RuntimeException('profile not found');
    $out=['profile'=>'configured','proxy_configured'=>$acc->proxy!==null,'session_ready'=>$acc->isLegacyReady()];
    foreach([
        'proxy_cookies'=>[true,true],
        'direct_cookies'=>[false,true],
        'proxy_token_only'=>[true,false],
        'direct_token_only'=>[false,false],
    ] as $label=>$flags){
        [$useProxy,$useCookies]=$flags;
        $c=client_for($acc,$useProxy,$useCookies);
        $out[$label]=[
            'me'=>ab_step(fn()=>$c->get('me',['fields'=>'id,name'])),
            'adaccounts'=>ab_step(fn()=>$c->get('me/adaccounts',['fields'=>'id,name,account_status,currency','limit'=>10])),
        ];
    }
    fwrite(STDERR,"[meta-ab] ".json_encode($out,JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE)."\n");
} catch(Throwable $e) {
    fwrite(STDERR,"[meta-ab] fatal ".get_class($e).": ".$e->getMessage()."\n");
}
