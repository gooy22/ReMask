<?php
declare(strict_types=1);
ini_set('display_errors','0'); ini_set('html_errors','0');
ob_start();
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
// Authentication and CSRF initialization are complete. Worker reads and card
// operations can take a minute; holding the file-session lock here makes other
// pages in the same signed-in browser wait for that entire request.
if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
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
function card_asset_hint(array $input): array {
    if(!array_intersect(['business_id','business_asset_id','account_name'],array_keys($input)))return [];
    $business=trim((string)($input['business_id']??''));
    $alias=trim((string)($input['business_asset_id']??''));
    $name=trim((string)($input['account_name']??''));
    if(!preg_match('/^\d{5,30}$/D',$business)||($alias!==''&&!preg_match('/^\d{5,30}$/D',$alias))||strlen($name)>160||preg_match('/[\x00-\x1f]/',$name))throw new InvalidArgumentException('PAYMENT_ACCOUNT_BINDING_MISSING');
    return ['business_id'=>$business,'business_asset_id'=>$alias,'name'=>$name];
}
function card_worker_inspect(string $profile,string $account,array $assetHint=[]): array {
    $base=rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL')?:'http://127.0.0.1:8081'),'/');
    $key=(string)(getenv('REMASK_WORKER_API_KEY')?:'');
    if($key==='')throw new RuntimeException('CARD_WORKER_KEY_UNAVAILABLE');
    $context=stream_context_create(['http'=>['method'=>'GET','header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",
        'timeout'=>100,'ignore_errors'=>true,'follow_location'=>0]]);
    $query=['account_id'=>$account];
    if($assetHint){$query['business_id']=$assetHint['business_id'];if($assetHint['business_asset_id']!=='')$query['business_asset_id']=$assetHint['business_asset_id'];if($assetHint['name']!=='')$query['account_name']=$assetHint['name'];}
    $raw=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/payment-methods?'.http_build_query($query,'','&',PHP_QUERY_RFC3986),false,$context);
    if($raw===false)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    $result=json_decode($raw,true);
    // The inspection route returns a deliberately sanitized code for auth gates.
    // Preserve only known codes; arbitrary response bodies never reach the UI.
    $known=['SESSION_EXPIRED','CHECKPOINT_REQUIRED','TWO_FACTOR_REQUIRED','PROFILE_CONTEXT_ERROR',
        'PAYMENT_INSPECTION_TIMEOUT','PAYMENT_BROWSER_CRASHED','PAYMENT_UI_UNAVAILABLE','PAYMENT_HTTP_UNAVAILABLE',
        'PAYMENT_ACCOUNT_BINDING_MISSING','PERSONAL_AD_ACCOUNT_EXCLUDED','INVALID_PAYMENT_TARGET'];
    if(is_array($result)&&is_string($result['detail']??null)&&in_array($result['detail'],$known,true))throw new RuntimeException($result['detail']);
    if(!is_array($result)||($result['profile_id']??'')!==$profile||($result['account_id']??'')!==$account)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
    return $result;
}
try {
    // Explicit maintenance-only read. Does not run on prepare/bind/reconcile,
    // never accepts card fields, and exports public JS definitions only.
    if($_SERVER['REQUEST_METHOD']==='GET'&&($_GET['action']??'')==='contract_sources'){
        $profile=trim((string)($_GET['profile']??''));$account=(string)($_GET['account_id']??'');
        if($profile===''||strlen($profile)>160||!preg_match('/^\d{5,30}$/D',$account))throw new InvalidArgumentException('INVALID_PAYMENT_TARGET');
        $base=rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL')?:'http://127.0.0.1:8081'),'/');
        $key=(string)(getenv('REMASK_WORKER_API_KEY')?:'');
        if($key==='')throw new RuntimeException('CARD_WORKER_KEY_UNAVAILABLE');
        $context=stream_context_create(['http'=>['method'=>'GET','header'=>"Accept: application/json\r\nX-Remask-Worker-Key: ".$key."\r\n",'timeout'=>100,'ignore_errors'=>true,'follow_location'=>0]]);
        $raw=@file_get_contents($base.'/api/v1/profiles/'.rawurlencode($profile).'/payment-contract-sources?'.http_build_query(['account_id'=>$account],'','&',PHP_QUERY_RFC3986),false,$context);
        $result=$raw===false?null:json_decode($raw,true);unset($raw,$context,$key);
        $sourceErrors=['SESSION_EXPIRED','CHECKPOINT_REQUIRED','TWO_FACTOR_REQUIRED','BUSINESS_LOGIN_GATE',
            'PROFILE_CONTEXT_ERROR','INVALID_PAYMENT_TARGET','PAYMENT_ACCOUNT_BINDING_MISSING','PERSONAL_AD_ACCOUNT_EXCLUDED',
            'PAYMENT_CONTRACT_SOURCE_TIMEOUT','PAYMENT_SOURCE_NETWORK_UNAVAILABLE','PAYMENT_DOCUMENT_UNAVAILABLE',
            'PAYMENT_CONTRACT_SOURCE_UNAVAILABLE'];
        if(is_array($result)&&is_string($result['detail']??null)&&in_array($result['detail'],$sourceErrors,true))throw new RuntimeException($result['detail']);
        if(!is_array($result)||($result['profile_id']??'')!==$profile||($result['account_id']??'')!==$account)throw new RuntimeException('PAYMENT_CONTRACT_SOURCE_UNAVAILABLE');
        // A source snapshot is a file, so Safari can save the exact JSON bytes
        // instead of printing a potentially clipped document to PDF.
        header('Content-Disposition: attachment; filename="remask-payment-contracts.json"');
        card_out(['ok'=>true,'data'=>$result]);
    }
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
    $assetHint=card_asset_hint($input);
    // The worker is the source of truth for payment targets. Its resolver
    // accepts both synced inventory and an exact confirmed RK creation, while
    // rejecting personal or ambiguous accounts. Requiring the PHP-side launch
    // catalog here made a newly created RK fail until a separate inventory sync.
    if($action==='reconcile'){
        $id=(string)($input['card_id']??'');
        if(!preg_match('/^card_[a-f0-9]{24}$/D',$id))throw new InvalidArgumentException('CARD_NOT_FOUND');
        $expected=$vault->binding($id,$profile,$account);
        if(is_array($expected)&&$expected['status']==='IN_PROGRESS'&&time()-(strtotime((string)$expected['updated_at'])?:time())<180)throw new InvalidArgumentException('CARD_BINDING_IN_PROGRESS');
        $httpResult=card_worker($profile,['operation'=>'reconcile','account_id'=>$account,'card_id'=>$id,'asset_hint'=>$assetHint]);
        if(($httpResult['profile_id']??'')!==$profile||($httpResult['account_id']??'')!==$account)throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
        if(($httpResult['code']??'')!=='CARD_HTTP_INTENT_NOT_FOUND'){
            if(($httpResult['status']??'')==='LINKED'){
                $confirmed=$vault->reconcile($id,$profile,$account,$expected,$httpResult['funding']??[]);
                if(($confirmed['status']??'')!=='LINKED')throw new RuntimeException('CARD_WORKER_RESULT_UNKNOWN');
            }elseif(($httpResult['funding']['inventory_complete']??false)===true&&
                    ($httpResult['funding']['verification_status']??'')==='NONE'){
                $review=$vault->reconcile($id,$profile,$account,$expected,$httpResult['funding']);
                if(isset($review['retry_review']))$httpResult=array_replace($httpResult,[
                    'code'=>'CARD_RECONCILE_NO_METHOD','retry_review'=>$review['retry_review']]);
            }
            card_out(['ok'=>true,'data'=>['result'=>$httpResult]]);
        }
        $funding=card_worker_inspect($profile,$account,$assetHint);
        $result=$vault->reconcile($id,$profile,$account,$expected,$funding);
        card_out(['ok'=>true,'data'=>['result'=>['profile_id'=>$profile,'account_id'=>$account]+$result]]);
    }
    $payload=['operation'=>$action,'account_id'=>$account];$id='';$attemptId=null;
    if($assetHint){
        // Navigation hint from the selected Workspace row. The worker still
        // proves the exact RK inside Meta before touching card fields.
        $payload['asset_hint']=$assetHint;
    }
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
        $clientInfo=$input['client_info']??null;
        if(is_string($clientInfo))$clientInfo=json_decode($clientInfo,true,4,JSON_THROW_ON_ERROR);
        if(!is_array($clientInfo)||array_diff(array_keys($clientInfo),['color_depth','java_enabled','screen_height','screen_width'])||count($clientInfo)!==4||($clientInfo['java_enabled']??null)!==false)throw new InvalidArgumentException('CARD_CLIENT_CONTEXT_REQUIRED');
        $payload['client_info']=$clientInfo;
        foreach(['network_consent','recurring_consent'] as $flag){
            if(array_key_exists($flag,$input)){
                if($input[$flag]!=='1')throw new InvalidArgumentException('CARD_CONSENT_INVALID');
                // Never invent affirmative consent; it must come from the user.
                $payload[$flag]=true;
            }
        }
        $reviewed=($input['retry_confirmed']??'')==='1';
        if($reviewed){
            $expected=$vault->binding($id,$profile,$account);
            $checked=card_worker($profile,['operation'=>'reconcile','account_id'=>$account,'card_id'=>$id,'asset_hint'=>$assetHint]);
            $funding=is_array($checked['funding']??null)?$checked['funding']:card_worker_inspect($profile,$account,$assetHint);
            $secret=$vault->secret($id);
            $binding=$vault->beginReviewed($id,$profile,$account,(string)($input['retry_review']??''),$expected,$funding);
            if(is_string($expected['attempt_id']??null))$payload['reviewed_attempt_id']=$expected['attempt_id'];
        }else{
            $secret=$vault->secret($id);
            $binding=$vault->begin($id,$profile,$account);
        }
        $attemptId=$binding['attempt_id']??null;
        if($binding['status']==='LINKED')card_out(['ok'=>true,'data'=>['result'=>['profile_id'=>$profile,'account_id'=>$account,'status'=>'LINKED','code'=>'ALREADY_LINKED','submitted'=>false]]]);
        $payload['card_id']=$id;$payload['attempt_id']=$attemptId;
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
    $message=$e->getMessage();
    $code=($message==='BUSINESS_LOGIN_GATE'||preg_match('/^(?:CARD|PAYMENT|PROFILE|PERSONAL|INVALID|PRIVATE_LAUNCH|SESSION|CHECKPOINT|TWO_FACTOR)_[A-Z0-9_]{1,80}$/D',$message))
        ? $message : 'CARD_OPERATION_FAILED';
    error_log(sprintf('[payment-card] operation=%s profile=%s account=%s exception=%s code=%s',
        preg_replace('/[^A-Za-z0-9_.-]/','_', (string)($input['action']??'')),
        preg_replace('/[^A-Za-z0-9_.-]/','_', (string)($input['profile']??'')),
        preg_replace('/[^0-9]/','', (string)($input['account_id']??'')),
        get_class($e),$code));
    card_out(['ok'=>false,'error'=>['message'=>$code]],503);
}
