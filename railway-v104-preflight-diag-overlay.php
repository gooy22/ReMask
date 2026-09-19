<?php
/**
 * Temporary sanitized Meta preflight diagnostic.
 * Protected by REMASK_DIAG_TOKEN and never returns access tokens/cookies/proxy credentials.
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
function diag_graph(string $version,string $token,string $mode,string $path='me',array $params=['fields'=>'id,name']): array {
    $url='https://graph.facebook.com/'.rawurlencode($version).'/'.ltrim($path,'/');
    $headers=['Accept: application/json','User-Agent: ReMask-PreflightDiag/1.0'];
    if($mode==='bearer')$headers[]='Authorization: Bearer '.$token;
    else $params['access_token']=$token;
    if($params!==[])$url.='?'.http_build_query($params);
    $ch=curl_init($url);
    curl_setopt_array($ch,[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>10,
        CURLOPT_TIMEOUT=>25,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>$headers,
    ]);
    $raw=curl_exec($ch);
    $err=curl_error($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if($raw===false)return ['ok'=>false,'http'=>$http,'transport_error'=>diag_clean($err ?: ('cURL errno '.$errno))];
    $d=json_decode((string)$raw,true);
    if(!is_array($d))return ['ok'=>false,'http'=>$http,'error'=>'non-json'];
    if(isset($d['error'])&&is_array($d['error'])){
        $e=$d['error'];
        return [
            'ok'=>false,'http'=>$http,
            'message'=>diag_clean((string)($e['message']??'Meta error')),
            'type'=>(string)($e['type']??''),
            'code'=>isset($e['code'])?(int)$e['code']:null,
            'subcode'=>isset($e['error_subcode'])?(int)$e['error_subcode']:null,
        ];
    }
    return ['ok'=>$http>=200&&$http<300,'http'=>$http,'has_id'=>isset($d['id']),'data_count'=>is_array($d['data']??null)?count($d['data']):null];
}

$profile=trim((string)($_GET['profile'] ?? ''));
$out=['ok'=>false,'profile'=>$profile];
try{
    if($profile==='')throw new InvalidArgumentException('profile required');
    $store=AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account=$store->getAccountByName($profile);
    if($account===null)throw new RuntimeException('profile not found');
    $token=trim((string)$account->token);
    if($token==='')throw new RuntimeException('empty token');

    $service=MetaEndpoint::serviceForAccountName($profile);
    $identity=diag_step(fn()=>$service->getIdentity());
    $permissions=diag_step(fn()=>$service->getPermissions());
    $accounts=diag_step(fn()=>$service->listAdAccounts());

    $out['service']=[
        'identity'=>['ok'=>$identity['ok'],'error'=>$identity['error']??null,'code'=>$identity['code']??null],
        'permissions'=>['ok'=>$permissions['ok'],'error'=>$permissions['error']??null,'code'=>$permissions['code']??null],
        'ad_accounts'=>[
            'ok'=>$accounts['ok'],
            'count'=>$accounts['ok']?count((array)($accounts['value']['data']??[])):null,
            'error'=>$accounts['error']??null,
            'code'=>$accounts['code']??null,
        ],
    ];
    $out['direct']=[
        'v26_me_query'=>diag_graph('v26.0',$token,'query'),
        'v26_me_bearer'=>diag_graph('v26.0',$token,'bearer'),
        'v25_me_query'=>diag_graph('v25.0',$token,'query'),
        'v24_me_query'=>diag_graph('v24.0',$token,'query'),
        'v20_me_query'=>diag_graph('v20.0',$token,'query'),
        'v26_permissions_query'=>diag_graph('v26.0',$token,'query','me/permissions',['limit'=>200]),
        'v26_adaccounts_query'=>diag_graph('v26.0',$token,'query','me/adaccounts',['fields'=>'id,name,account_status,currency','limit'=>50]),
    ];
    $out['ok']=true;
}catch(Throwable $e){
    $out['fatal']=['error'=>diag_clean($e->getMessage()),'class'=>get_class($e),'code'=>$e->getCode()];
}
echo json_encode($out,JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
PHP;
file_put_contents($path,$code);
fwrite(STDERR,"[preflight-diag] sanitized diagnostic endpoint installed\n");
