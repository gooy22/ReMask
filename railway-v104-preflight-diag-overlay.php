<?php
/**
 * Temporary sanitized Meta preflight diagnostic.
 * Uses the same MetaApiClient transport as ReMask; no raw Graph transport.
 */
$root='/var/www/html';
$path=$root.'/ajax/metaPreflightDiag.php';
$code=<<<'PHP'
<?php
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

$expected=(string)(getenv('REMASK_DIAG_TOKEN') ?: '');
$provided=(string)($_GET['k'] ?? '');
if($expected==='' || $provided==='' || !hash_equals($expected,$provided)){
    http_response_code(404);
    echo json_encode(['ok'=>false]);
    exit;
}

require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/MetaApiClient.php';
require_once __DIR__.'/../classes/AccountStoreFactory.php';

function diag_clean(string $message): string {
    $message=preg_replace('#(https?://)([^/@:\s]+):([^/@\s]+)@#i','$1***:***@',$message) ?? $message;
    $message=preg_replace('/(access[_-]?token|authorization|bearer)(\s*[:=]\s*|\s+)[A-Za-z0-9._\-]+/i','$1$2***',$message) ?? $message;
    return function_exists('mb_substr')?mb_substr($message,0,500):substr($message,0,500);
}
function diag_step(callable $fn): array {
    try{
        $v=$fn();
        return ['ok'=>true,'value'=>$v];
    }catch(Throwable $e){
        $out=['ok'=>false,'error'=>diag_clean($e->getMessage()),'class'=>get_class($e),'code'=>$e->getCode()];
        if(method_exists($e,'toArray')){
            try{
                $a=(array)$e->toArray();
                foreach(['type','code','subcode','error_subcode','http_status','is_transient'] as $key){
                    if(array_key_exists($key,$a))$out[$key]=$a[$key];
                }
            }catch(Throwable){}
        }
        return $out;
    }
}
function diag_client(object $account,string $version): MetaApiClient {
    $client=new MetaApiClient((string)$account->token,$account->proxy,$version,35);
    if($account->isLegacyReady())$client->setSessionCookies($account->getCurlCookies());
    return $client;
}
function diag_public_step(array $step): array {
    return [
        'ok'=>(bool)($step['ok']??false),
        'error'=>$step['error']??null,
        'class'=>$step['class']??null,
        'code'=>$step['code']??null,
        'http_status'=>$step['http_status']??null,
        'subcode'=>$step['subcode']??($step['error_subcode']??null),
        'count'=>($step['ok']??false) && is_array($step['value']['data']??null) ? count($step['value']['data']) : null,
        'has_id'=>($step['ok']??false) ? isset($step['value']['id']) : null,
    ];
}

$profile=trim((string)($_GET['profile'] ?? ''));
$out=['ok'=>false,'profile'=>$profile];
try{
    if($profile==='')throw new InvalidArgumentException('profile required');
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account=$store->getAccountByName($profile);
    if($account===null)throw new RuntimeException('profile not found');
    if(trim((string)$account->token)==='')throw new RuntimeException('empty token');

    $service=MetaEndpoint::serviceForAccountName($profile);
    $out['service']=[
        'identity'=>diag_public_step(diag_step(fn()=>$service->getIdentity())),
        'permissions'=>diag_public_step(diag_step(fn()=>$service->getPermissions())),
        'ad_accounts'=>diag_public_step(diag_step(fn()=>$service->listAdAccounts())),
    ];

    $versions=['v26.0','v25.0','v24.0','v20.0'];
    foreach($versions as $version){
        $client=diag_client($account,$version);
        $out['versions'][$version]=[
            'me'=>diag_public_step(diag_step(fn()=>$client->get('me',['fields'=>'id,name']))),
        ];
    }
    $v26=diag_client($account,'v26.0');
    $out['v26_edges']=[
        'permissions'=>diag_public_step(diag_step(fn()=>$v26->get('me/permissions',['limit'=>200]))),
        'ad_accounts'=>diag_public_step(diag_step(fn()=>$v26->get('me/adaccounts',['fields'=>'id,name,account_status,currency','limit'=>50]))),
    ];
    $out['ok']=true;
}catch(Throwable $e){
    $out['fatal']=['error'=>diag_clean($e->getMessage()),'class'=>get_class($e),'code'=>$e->getCode()];
}
echo json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
PHP;
file_put_contents($path,$code);
fwrite(STDERR,"[preflight-diag] sanitized MetaApiClient diagnostic endpoint installed\n");
