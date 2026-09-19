<?php
$root='/var/www/html';
$path=$root.'/bin/remask-preflight-diag.php';
$code=<<<'PHP'
<?php
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/MetaApiClient.php';
require_once __DIR__.'/../classes/AccountStoreFactory.php';
require_once __DIR__.'/../classes/FbRequests.php';

function dclean(string $m): string {
    $m=preg_replace('#(https?://)([^/@:\s]+):([^/@\s]+)@#i','$1***:***@',$m) ?? $m;
    $m=preg_replace('/(access[_-]?token|authorization|bearer)(\s*[:=]\s*|\s+)[A-Za-z0-9._\-]+/i','$1$2***',$m) ?? $m;
    return function_exists('mb_substr')?mb_substr($m,0,400):substr($m,0,400);
}
function dstep(callable $fn): array {
    try{
        $v=$fn();
        return ['ok'=>true,'count'=>is_array($v['data']??null)?count($v['data']):null,'has_id'=>isset($v['id'])];
    }catch(Throwable $e){
        $out=['ok'=>false,'error'=>dclean($e->getMessage()),'class'=>get_class($e),'code'=>$e->getCode()];
        if(method_exists($e,'toArray')){
            try{
                $a=(array)$e->toArray();
                foreach(['http_status','subcode','error_subcode','type'] as $k)if(array_key_exists($k,$a))$out[$k]=$a[$k];
            }catch(Throwable){}
        }
        return $out;
    }
}
function dclient(object $account,string $version): MetaApiClient {
    $c=new MetaApiClient((string)$account->token,$account->proxy,$version,35);
    if($account->isLegacyReady())$c->setSessionCookies($account->getCurlCookies());
    return $c;
}

$profile=trim((string)(getenv('REMASK_DIAG_PROFILE') ?: ''));
if($profile==='')exit(0);
$out=['profile'=>'configured'];
try{
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account=$store->getAccountByName($profile);
    if($account===null)throw new RuntimeException('profile not found');
    $service=MetaEndpoint::serviceForAccountName($profile);
    $out['service']=[
        'identity'=>dstep(fn()=>$service->getIdentity()),
        'permissions'=>dstep(fn()=>$service->getPermissions()),
        'ad_accounts'=>dstep(fn()=>$service->listAdAccounts()),
    ];
    foreach(['v26.0','v25.0','v24.0','v20.0'] as $v){
        $c=dclient($account,$v);
        $out['versions'][$v]['me']=dstep(fn()=>$c->get('me',['fields'=>'id,name']));
    }
    $c=dclient($account,'v26.0');
    $out['v26_edges']=[
        'permissions'=>dstep(fn()=>$c->get('me/permissions',['limit'=>200])),
        'ad_accounts'=>dstep(fn()=>$c->get('me/adaccounts',['fields'=>'id,name,account_status,currency','limit'=>50])),
    ];

    $legacy=(new ReflectionClass(FbRequests::class))->newInstance();
    foreach([
        'me'=>'me?fields=id,name',
        'ad_accounts'=>'me/adaccounts?fields=id,name,account_status,currency&limit=50'
    ] as $label=>$path){
        try{
            $raw=$legacy->ApiGet($account,$path);
            $body=json_decode((string)($raw['res']??''),true);
            $err=is_array($body['error']??null)?$body['error']:[];
            $transport=trim((string)($raw['error']??''));
            $out['legacy'][$label]=[
                'ok'=>is_array($body) && $err===[] && $transport==='',
                'has_id'=>is_array($body)&&isset($body['id']),
                'count'=>is_array($body['data']??null)?count($body['data']):null,
                'meta_error'=>$err===[]?null:[
                    'message'=>isset($err['message'])?dclean((string)$err['message']):null,
                    'type'=>$err['type']??null,
                    'code'=>isset($err['code'])?(int)$err['code']:null,
                    'subcode'=>isset($err['error_subcode'])?(int)$err['error_subcode']:null,
                ],
                'transport_error'=>$transport===''?null:dclean($transport),
            ];
        }catch(Throwable $legacyError){
            $out['legacy'][$label]=['ok'=>false,'exception'=>get_class($legacyError),'error'=>dclean($legacyError->getMessage())];
        }
    }
}catch(Throwable $e){
    $out['fatal']=['error'=>dclean($e->getMessage()),'class'=>get_class($e),'code'=>$e->getCode()];
}
fwrite(STDERR,"[preflight-cli-diag] ".json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)."\n");
PHP;
if(!is_dir(dirname($path)))mkdir(dirname($path),0775,true);
file_put_contents($path,$code);
fwrite(STDERR,"[preflight-cli-diag] runtime probe installed\n");
