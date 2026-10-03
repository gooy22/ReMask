<?php
declare(strict_types=1);
ini_set('display_errors','0'); ini_set('html_errors','0');
ob_start();
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
while(ob_get_level()>0)ob_end_clean();
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
function card_out(array $data,int $status=200): never {
    http_response_code($status);echo json_encode($data,JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR);exit;
}
function card_worker(string $profile,array $payload): array {
    $base=rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL')?:'http://127.0.0.1:8081'),'/');
    $key=(string)(getenv('REMASK_WORKER_API_KEY')?:'');
    if($key==='')throw new RuntimeException('CARD_WORKER_KEY_UNAVAILABLE');
    $body=json_encode($payload,JSON_THROW_ON_ERROR);
    $context=stream_context_create(['http'=>['method'=>'POST','header'=>"Content-Type: application/json\r\nAccept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
        'content'=>$body,'timeout'=>130,'ignore_errors'=>true,'follow_location'=>0]]);
    $raw=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/payment-card',false,$context);
    unset($body,$payload,$context);
    if($raw===false)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    $result=json_decode($raw,true);unset($raw);
    if(!is_array($result)||!isset($result['status']))throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    return $result;
}
function card_worker_inspect(string $profile,string $account): array {
    $base=rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL')?:'http://127.0.0.1:8081'),'/');
    $key=(string)(getenv('REMASK_WORKER_API_KEY')?:'');
    if($key==='')throw new RuntimeException('CARD_WORKER_KEY_UNAVAILABLE');
    $context=stream_context_create(['http'=>['method'=>'GET','header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
        'timeout'=>100,'ignore_errors'=>true,'follow_location'=>0]]);
    $raw=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/payment-methods?account_id='.rawurlencode($account),false,$context);
    if($raw===false)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    $result=json_decode($raw,true);
    if(!is_array($result)||($result['profile_id']??'')!==$profile||($result['account_id']??'')!==$account)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    return $result;
}
try {
    if($_SERVER['REQUEST_METHOD']!=='POST')card_out(['ok'=>false,'error'=>['message'=>'POST_REQUIRED']],405);
    $provided=(string)($_SERVER['HTTP_X_REMASK_CSRF']??'');
    if(!function_exists('remask_csrf_token')||$provided===''||!hash_equals(remask_csrf_token(),$provided))card_out(['ok'=>false,'error'=>['message'=>'CSRF_INVALID']],403);
    $input=$_POST;
    if(str_contains(strtolower((string)($_SERVER['CONTENT_TYPE']??'')),'application/json'))$input=json_decode((string)file_get_contents('php://input'),true,16,JSON_THROW_ON_ERROR);
    if(!is_array($input))throw new InvalidArgumentException('CARD_REQUEST_INVALID');
    require_once __DIR__.'/../classes/RemaskPaymentCardVault.php';
    $vault=new RemaskPaymentCardVault();$action=(string)($input['action']??'');
    if($action==='list')card_out(['ok'=>true,'data'=>$vault->all()]);
    if($action==='add')card_out(['ok'=>true,'data'=>['card'=>$vault->add($input)]]);
    if($action==='billing_update'){
        $id=(string)($input['card_id']??'');
        if(!preg_match('/^card_[a-f0-9]{24}$/D',$id))throw new InvalidArgumentException('CARD_NOT_FOUND');
        $patch=$input;unset($patch['action'],$patch['card_id']);
        card_out(['ok'=>true,'data'=>['card'=>$vault->updateBilling($id,$patch)]]);
    }
    if(!in_array($action,['prepare','bind','reconcile'],true))throw new InvalidArgumentException('CARD_ACTION_INVALID');
    $profile=trim((string)($input['profile']??''));$account=preg_replace('/^act_/','',trim((string)($input['account_id']??'')));
    if($profile===''||strlen($profile)>160||!preg_match('/^\d{5,30}$/D',$account))throw new InvalidArgumentException('INVALID_PAYMENT_TARGET');
    require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
    RemaskPrivateLaunchCatalog::asset(RemaskPrivateLaunchCatalog::load($profile),'funding',$account);
    if($action==='reconcile'){
        $id=(string)($input['card_id']??'');
        if(!preg_match('/^card_[a-f0-9]{24}$/D',$id))throw new InvalidArgumentException('CARD_NOT_FOUND');
        $expected=$vault->binding($id,$profile,$account);
        if(is_array($expected)&&$expected['status']==='IN_PROGRESS'&&time()-(strtotime((string)$expected['updated_at'])?:time())<180)throw new InvalidArgumentException('CARD_BINDING_IN_PROGRESS');
        $funding=card_worker_inspect($profile,$account);
        $result=$vault->reconcile($id,$profile,$account,$expected,$funding);
        card_out(['ok'=>true,'data'=>['result'=>['profile_id'=>$profile,'account_id'=>$account]+$result]]);
    }
    $payload=['operation'=>$action,'account_id'=>$account];$id='';$attemptId=null;
    if($action==='prepare'&&!empty($input['card_id'])&&!preg_match('/^card_[a-f0-9]{24}$/D',(string)$input['card_id']))throw new InvalidArgumentException('CARD_NOT_FOUND');
    $setupFields=['setup_country','setup_currency','setup_timezone','setup_country_mode'];
    if (array_intersect($setupFields,array_keys($input)) && !isset($input['setup_country'],$input['setup_currency'],$input['setup_timezone'])) throw new InvalidArgumentException('PAYMENT_SETUP_INVALID');
    if (isset($input['setup_country'], $input['setup_currency'], $input['setup_timezone'])) {
        $country=strtoupper(trim((string)$input['setup_country']));$currency=strtoupper(trim((string)$input['setup_currency']));$timezone=trim((string)$input['setup_timezone']);
        if (!preg_match('/^[A-Z]{2}$/D',$country)||!preg_match('/^[A-Z]{3}$/D',$currency)||!in_array($timezone,DateTimeZone::listIdentifiers(),true)) throw new InvalidArgumentException('PAYMENT_SETUP_INVALID');
        $countryMode=(string)($input['setup_country_mode']??'strict');
        if (!in_array($countryMode,['strict','prefer_ua','current'],true)) throw new InvalidArgumentException('PAYMENT_SETUP_INVALID');
        $payload['billing_setup']=['country'=>$country,'currency'=>$currency,'timezone'=>$timezone,'country_mode'=>$countryMode];
    }
    if($action==='bind'){
        $id=(string)($input['card_id']??'');$cvv=(string)($input['cvv']??'');
        if(!preg_match('/^card_[a-f0-9]{24}$/D',$id))throw new InvalidArgumentException('CARD_AND_CVV_REQUIRED');
        // A confirmed cached binding is a no-op. No CVV or PAN is needed,
        // and this does not claim a fresh Meta/payment verification.
        if($vault->linkedBinding($id,$profile,$account)!==null)card_out(['ok'=>true,'data'=>['result'=>['profile_id'=>$profile,'account_id'=>$account,'status'=>'LINKED','code'=>'ALREADY_LINKED','submitted'=>false,'funding_verified'=>false]]]);
        if(!preg_match('/^\d{3,4}$/D',$cvv))throw new InvalidArgumentException('CARD_AND_CVV_REQUIRED');
        $reviewed=($input['retry_confirmed']??'')==='1';
        if($reviewed){
            $expected=$vault->binding($id,$profile,$account);
            $funding=card_worker_inspect($profile,$account);
            $secret=$vault->secret($id);
            $binding=$vault->beginReviewed($id,$profile,$account,(string)($input['retry_review']??''),$expected,$funding);
        }else{
            $secret=$vault->secret($id);
            $binding=$vault->begin($id,$profile,$account);
        }
        $attemptId=$binding['attempt_id']??null;
        if($binding['status']==='LINKED')card_out(['ok'=>true,'data'=>['result'=>['profile_id'=>$profile,'account_id'=>$account,'status'=>'LINKED','code'=>'ALREADY_LINKED','submitted'=>false]]]);
        $payload['card']=$secret;$payload['cvv']=$cvv;unset($secret,$cvv,$input);
    }
    try {
        $result=card_worker($profile,$payload);unset($payload);
        if(($result['profile_id']??'')!==$profile||($result['account_id']??'')!==$account)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
        if($id!=='')$vault->finish($id,$profile,$account,(string)$result['status'],$result,$attemptId);
    } catch(Throwable $e){
        if($e->getMessage()==='CARD_BINDING_CHANGED')throw $e;
        if($id!=='')$vault->finish($id,$profile,$account,'SUBMITTED_UNVERIFIED',[],$attemptId);
        throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    }
    if($action==='prepare'&&($result['status']??'')==='FORM_READY'&&!empty($input['card_id'])){
        $missing=$vault->missingBilling((string)$input['card_id'],$result['fields']??[]);
        if($missing){$result['form_status']='FORM_READY';$result['status']='BLOCKED';$result['code']='CARD_BILLING_FIELDS_REQUIRED';$result['missing_fields']=$missing;}
    }
    card_out(['ok'=>true,'data'=>['result'=>$result]]);
}catch(InvalidArgumentException $e){
    card_out(['ok'=>false,'error'=>['message'=>$e->getMessage()]],400);
}catch(Throwable $e){
    // Raw request bodies, browser errors, PAN and CVV must never reach logs.
    $safe=['CARD_STORAGE_UNAVAILABLE','CARD_KEY_MISSING','CARD_KEY_INVALID','CARD_ENCRYPTION_FAILED','CARD_DECRYPTION_FAILED','CARD_STORAGE_INVALID','CARD_WORKER_KEY_UNAVAILABLE','CARD_WORKER_RESULT_UNKNOWN'];
    $code=in_array($e->getMessage(),$safe,true)?$e->getMessage():'CARD_OPERATION_FAILED';
    card_out(['ok'=>false,'error'=>['message'=>$code]],503);
}
