<?php
declare(strict_types=1);
require __DIR__.'/../railway-payment-card-vault.php';
function expect(bool $test,string $message): void {if(!$test)throw new RuntimeException($message);}
function rejected(callable $fn,string $code): void {try{$fn();throw new RuntimeException('Expected rejection');}catch(InvalidArgumentException $e){expect($e->getMessage()===$code,'Unexpected rejection');}}
function endpointNoCvvFixture(): void {
    $root=sys_get_temp_dir().'/remask-card-endpoint-'.bin2hex(random_bytes(5));
    mkdir($root.'/ajax',0700,true);mkdir($root.'/classes',0700,true);
    try {
        copy(__DIR__.'/../railway-payment-card-endpoint.php',$root.'/ajax/paymentCards.php');
        copy(__DIR__.'/../railway-payment-card-vault.php',$root.'/classes/RemaskPaymentCardVault.php');
        file_put_contents($root.'/settings.php',"<?php function remask_csrf_token(){return 'fixture';}");
        file_put_contents($root.'/checkpassword.php','<?php // Isolated test authentication.');
        // The PHP-side catalog can lag a confirmed worker-created RK. Payment
        // actions must validate their exact target in the worker instead.
        file_put_contents($root.'/classes/RemaskPrivateLaunchCatalog.php',"<?php class RemaskPrivateLaunchCatalog {static function load(\$p){return [];} static function asset(\$c,\$kind,\$id){throw new InvalidArgumentException('STALE_PHP_CATALOG');}}");
        file_put_contents($root.'/invoke.php', <<<'INVOKE'
<?php
putenv('REMASK_DATA_DIR='.$argv[1].'/state');
$_SERVER=['REQUEST_METHOD'=>'POST','HTTP_X_REMASK_CSRF'=>'fixture'];
$_POST=['action'=>'bind','profile'=>'Fixture','account_id'=>$argv[3],'card_id'=>$argv[2]];
require $argv[1].'/ajax/paymentCards.php';
INVOKE);
        $v=new RemaskPaymentCardVault($root.'/state/payment-cards');
        $c=$v->add(['number'=>'4111111111111111','month'=>12,'year'=>2099]);
        $v->begin($c['id'],'Fixture','123456789');$v->finish($c['id'],'Fixture','123456789','LINKED');
        // A no-op must not even decrypt PAN, let alone call the Meta worker.
        $path=$root.'/state/payment-cards/cards.json';$data=json_decode(file_get_contents($path),true);
        $data['cards'][$c['id']]['encrypted']='invalid-fixture-cipher';file_put_contents($path,json_encode($data));
        $before=file_get_contents($path);
        foreach(['123456789','987654321'] as $account){
            $output=[];$exit=0;
            exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($root.'/invoke.php').' '.escapeshellarg($root).' '.escapeshellarg($c['id']).' '.escapeshellarg($account),$output,$exit);
            expect($exit===0,'Endpoint fixture failed');$response=json_decode(implode("\n",$output),true);
            if($account==='123456789'){
                expect(($response['data']['result']['code']??'')==='ALREADY_LINKED','Linked request still needs CVV or decrypts PAN');
                expect($response['data']['result']['submitted']===false&&$response['data']['result']['funding_verified']===false,'No-op claimed fresh submission or verification');
            }else expect(($response['error']['message']??'')==='CARD_AND_CVV_REQUIRED','New target bypassed CVV');
            expect(file_get_contents($path)===$before,'No-CVV endpoint mutated binding state');
        }
        file_put_contents($root.'/inspect.php', <<<'INSPECT'
<?php
putenv('REMASK_DATA_DIR='.$argv[1].'/state');putenv('REMASK_PYTHON_WORKER_URL=fixture://worker');putenv('REMASK_WORKER_API_KEY=fixture');
class FixtureInspectionStream {
    public $context;private string $body='';private int $pos=0;
    function stream_open($path,$mode,$options,&$opened): bool {
        $http=stream_context_get_options($this->context)['http'];
        file_put_contents($GLOBALS['argv'][1].'/request.json',json_encode(['url'=>$path,'http'=>$http]));
        $this->body=file_get_contents($GLOBALS['argv'][1].'/funding.json');return true;
    }
    function stream_read($count): string {$chunk=substr($this->body,$this->pos,$count);$this->pos+=strlen($chunk);return $chunk;}
    function stream_eof(): bool {return $this->pos>=strlen($this->body);}
    function stream_stat(): array {return [];}
}
stream_wrapper_register('fixture',FixtureInspectionStream::class);
$_SERVER=['REQUEST_METHOD'=>'POST','HTTP_X_REMASK_CSRF'=>'fixture'];
$_POST=['action'=>'reconcile','profile'=>'Fixture','account_id'=>'act_123456789','card_id'=>$argv[2],
    'funding'=>['verification_status'=>'LINKED','checked_live'=>true],'cvv'=>'fixture-forbidden'];
require $argv[1].'/ajax/paymentCards.php';
INSPECT);
        $v->finish($c['id'],'Fixture','123456789','SUBMITTED_UNVERIFIED');
        $proof=['profile_id'=>'Fixture','account_id'=>'123456789','account_scope_verified'=>true,'checked_live'=>true,
            'source'=>'private_facebook_billing_ui','verification_status'=>'LINKED','payment_methods'=>[['type'=>'Visa','last4'=>'1111']]];
        foreach(['UNVERIFIED','LINKED'] as $status){
            file_put_contents($root.'/funding.json',json_encode(array_replace($proof,['verification_status'=>$status])));
            $output=[];$exit=0;exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($root.'/inspect.php').' '.escapeshellarg($root).' '.escapeshellarg($c['id']),$output,$exit);
            expect($exit===0,'Inspection endpoint fixture failed');$response=json_decode(implode("\n",$output),true);
            expect(($response['data']['result']['status']??'')===($status==='LINKED'?'LINKED':'SUBMITTED_UNVERIFIED'),'Inspection trusted POST proof or decrypted corrupt PAN');
            $request=json_decode(file_get_contents($root.'/request.json'),true);
            expect($request['http']['method']==='GET'&&!isset($request['http']['content']),'Reconciliation submitted financial payload');
            expect(str_ends_with($request['url'],'/profiles/Fixture/payment-methods?account_id=123456789'),'Inspection target mismatch');
            expect(!str_contains(json_encode($request),'fixture-forbidden'),'CVV passed to inspection');
        }
        $before=file_get_contents($path);
        foreach(['SESSION_EXPIRED','CHECKPOINT_REQUIRED','TWO_FACTOR_REQUIRED','PROFILE_CONTEXT_ERROR','PAYMENT_INSPECTION_TIMEOUT','PAYMENT_BROWSER_CRASHED',
            'PAYMENT_UI_UNAVAILABLE','PAYMENT_ACCOUNT_BINDING_MISSING','PERSONAL_AD_ACCOUNT_EXCLUDED','INVALID_PAYMENT_TARGET',
            'sensitive fixture message','SESSION_EXPIRED sensitive fixture message'] as $detail){
            file_put_contents($root.'/funding.json',json_encode(['detail'=>$detail]));
            $output=[];$exit=0;exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($root.'/inspect.php').' '.escapeshellarg($root).' '.escapeshellarg($c['id']),$output,$exit);
            expect($exit===0,'Inspection error endpoint fixture failed');$response=json_decode(implode("\n",$output),true);
            $expectedCode=str_contains($detail,'sensitive')?'CARD_WORKER_RESULT_UNKNOWN':$detail;
            expect(($response['error']['message']??'')===$expectedCode,'Inspection hid known gate or leaked raw worker message');
            expect(file_get_contents($path)===$before,'Inspection failure mutated binding');
        }
        // Exercise the endpoint with synthetic credentials and an isolated stream wrapper.
        file_put_contents($root.'/operation.php', <<<'OPERATION'
<?php
putenv('REMASK_DATA_DIR='.$argv[1].'/state');putenv('REMASK_PYTHON_WORKER_URL=fixture://worker');putenv('REMASK_WORKER_API_KEY=fixture');
class FixtureOperationStream {
    public $context;private string $body='';private int $pos=0;
    function stream_open($path,$mode,$options,&$opened): bool {
        $http=stream_context_get_options($this->context)['http'];
        file_put_contents($GLOBALS['argv'][1].'/operations.jsonl',json_encode(['url'=>$path,'http'=>$http])."\n",FILE_APPEND);
        $this->body=file_get_contents($GLOBALS['argv'][1].($http['method']==='GET'?'/funding.json':'/result.json'));return true;
    }
    function stream_read($count): string {$chunk=substr($this->body,$this->pos,$count);$this->pos+=strlen($chunk);return $chunk;}
    function stream_eof(): bool {return $this->pos>=strlen($this->body);}
    function stream_stat(): array {return [];}
}
stream_wrapper_register('fixture',FixtureOperationStream::class);
$_SERVER=['REQUEST_METHOD'=>'POST','HTTP_X_REMASK_CSRF'=>'fixture'];$_POST=json_decode(file_get_contents($argv[1].'/input.json'),true);
require $argv[1].'/ajax/paymentCards.php';
OPERATION);
        $c=$v->add(['number'=>'4111111111111111','month'=>12,'year'=>2099]);
        $invoke=function(array $input)use($root):array{
            file_put_contents($root.'/input.json',json_encode($input));file_put_contents($root.'/operations.jsonl','');
            $output=[];$exit=0;exec(escapeshellarg(PHP_BINARY).' '.escapeshellarg($root.'/operation.php').' '.escapeshellarg($root),$output,$exit);
            expect($exit===0,'Operation endpoint failed');return json_decode(implode("\n",$output),true);
        };
        file_put_contents($root.'/result.json',json_encode(['profile_id'=>'Fixture','account_id'=>'123456789','status'=>'FORM_READY','fields'=>[['kind'=>'holder','required'=>false],['kind'=>'number','required'=>true],['kind'=>'cvv','required'=>true]]]));
        $input=['action'=>'prepare','profile'=>'Fixture','account_id'=>'123456789','card_id'=>$c['id']];
        $result=$invoke($input);expect(($result['data']['result']['missing_fields']??[])===['holder'],'Prepare missed stored billing gap');
        $operations=file($root.'/operations.jsonl',FILE_IGNORE_NEW_LINES);$request=json_decode($operations[0],true);
        $payload=json_decode($request['http']['content'],true);expect(!isset($payload['card'],$payload['cvv'],$payload['card_id']),'Prepare forwarded card data');
        $result=$invoke(['action'=>'billing_update','card_id'=>$c['id'],'holder'=>'Fixture Holder']);
        expect(($result['data']['card']['id']??'')===$c['id']&&file_get_contents($root.'/operations.jsonl')==='','Billing edit touched worker');
        $result=$invoke($input);expect(($result['data']['result']['status']??'')==='FORM_READY','Fixed metadata still blocked');
        $v->finish($c['id'],'Fixture','123456789','SUBMITTED_UNVERIFIED');
        $data=json_decode(file_get_contents($path),true);$data['bindings'][hash('sha256','Fixture|123456789')]['updated_at']=gmdate('c',time()-240);file_put_contents($path,json_encode($data));
        $old=$v->binding($c['id'],'Fixture','123456789');$empty=array_replace($proof,['verification_status'=>'NONE','payment_methods'=>[]]);
        $review=$v->reconcile($c['id'],'Fixture','123456789',$old,$empty)['retry_review'];
        file_put_contents($root.'/funding.json',json_encode($empty));file_put_contents($root.'/result.json',json_encode(['profile_id'=>'Fixture','account_id'=>'123456789','status'=>'BLOCKED','code'=>'CARD_SAVE_CONTROL_UNAVAILABLE','submitted'=>false]));
        $bind=['action'=>'bind','profile'=>'Fixture','account_id'=>'123456789','card_id'=>$c['id'],'cvv'=>'123','retry_confirmed'=>'1','retry_review'=>$review['token']];
        $result=$invoke(array_replace($bind,['retry_confirmed'=>true]));expect(($result['error']['message']??'')==='CARD_BINDING_RECONCILE_REQUIRED','Nonexplicit retry accepted');
        expect(file_get_contents($root.'/operations.jsonl')==='','Blocked retry reached worker');
        file_put_contents($root.'/funding.json',json_encode(array_replace($empty,['verification_status'=>'UNVERIFIED'])));
        $result=$invoke($bind);expect(($result['error']['message']??'')==='CARD_RETRY_ACCOUNT_NOT_EMPTY','Retry trusted stale review');
        expect(count(file($root.'/operations.jsonl'))===1,'Unverified retry submitted card');
        file_put_contents($root.'/funding.json',json_encode($empty));$result=$invoke($bind);
        expect(($result['data']['result']['code']??'')==='CARD_SAVE_CONTROL_UNAVAILABLE','Reviewed endpoint failed');
        $operations=array_map(fn($line)=>json_decode($line,true),file($root.'/operations.jsonl'));
        expect(count($operations)===2&&$operations[0]['http']['method']==='GET'&&$operations[1]['http']['method']==='POST','Reviewed retry did not recheck first or submitted repeatedly');
        $payload=json_decode($operations[1]['http']['content'],true);
        expect($payload['operation']==='bind'&&!isset($payload['retry_review'],$payload['retry_confirmed']),'Review leaked into worker transport');
    } finally {
        $files=new RecursiveIteratorIterator(new RecursiveDirectoryIterator($root,FilesystemIterator::SKIP_DOTS),RecursiveIteratorIterator::CHILD_FIRST);
        foreach($files as $file){if($file->isDir())rmdir($file->getPathname());else unlink($file->getPathname());}rmdir($root);
    }
}
endpointNoCvvFixture();
$directory=sys_get_temp_dir().'/remask-card-vault-'.bin2hex(random_bytes(5));
try{
    $vault=new RemaskPaymentCardVault($directory);
    $fixture=['number'=>'4111 1111 1111 1111','month'=>12,'year'=>2099,'holder'=>'Test Holder','label'=>'Fixture'];
    $card=$vault->add($fixture);expect($card['last4']==='1111','Mask');
    $duplicate=$vault->add($fixture);expect($card['id']===$duplicate['id'],'Deduplication');
    expect(count($vault->all()['cards'])===1,'Duplicate stored');
    $disk=file_get_contents($directory.'/cards.json');
    foreach(['4111111111111111','Test Holder'] as $secret)expect(!str_contains($disk,$secret),'Plaintext secret on disk');
    // Random base64 ciphertext can contain "cvv" or "cvc" by chance.
    // Check actual JSON keys and decrypted payload instead of those substrings.
    $stored=json_decode($disk,true,32,JSON_THROW_ON_ERROR);
    $assertNoSecurityCode=function(array $data)use(&$assertNoSecurityCode):void{
        foreach($data as $key=>$value){
            expect(!in_array((string)$key,['security_code','cvv','cvc'],true),'Security-code field on disk');
            if(is_array($value))$assertNoSecurityCode($value);
        }
    };
    $assertNoSecurityCode($stored);$assertNoSecurityCode($vault->secret($card['id']));
    $public=json_encode($vault->all());expect(!str_contains($public,'encrypted')&&!str_contains($public,'fingerprint')&&!str_contains($public,'4111111111111111'),'Public secret');
    expect($vault->secret($card['id'])['number']==='4111111111111111','Decrypt');
    expect((fileperms($directory.'/key')&0777)===0600,'Key permissions');
    expect((fileperms($directory.'/cards.json')&0777)===0600,'Vault permissions');
    foreach(['cvv','cvc','security_code'] as $field)rejected(fn()=>$vault->add($fixture+[$field=>'fixture']),'CVV_MUST_NOT_BE_SAVED');
    rejected(fn()=>$vault->add(array_replace($fixture,['number'=>'4111111111111112'])),'CARD_NUMBER_INVALID');
    rejected(fn()=>$vault->add(array_replace($fixture,['month'=>0])),'CARD_EXPIRY_INVALID');
    rejected(fn()=>$vault->add(array_replace($fixture,['year'=>2001])),'CARD_EXPIRY_INVALID');
    $vault->begin($card['id'],'Fixture','123456789');
    rejected(fn()=>$vault->begin($card['id'],'Fixture','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    $expected=$vault->binding($card['id'],'Fixture','123456789');
    $proof=['profile_id'=>'Fixture','account_id'=>'123456789','account_scope_verified'=>true,'checked_live'=>true,
        'source'=>'private_facebook_billing_ui','verification_status'=>'LINKED','payment_methods'=>[['type'=>'Visa','last4'=>'1111']]];
    rejected(fn()=>$vault->reconcile($card['id'],'Fixture','123456789',$expected,$proof),'CARD_BINDING_IN_PROGRESS');
    $vault->finish($card['id'],'Fixture','123456789','SUBMITTED_UNVERIFIED');
    expect($vault->linkedBinding($card['id'],'Fixture','123456789')===null,'Unknown binding treated as linked');
    $expected=$vault->binding($card['id'],'Fixture','123456789');$before=file_get_contents($directory.'/cards.json');
    foreach([
        ['profile_id'=>'Other'],['account_id'=>'987654321'],['account_scope_verified'=>false],['checked_live'=>false],
        ['checked_live'=>1],['source'=>'cached'],['verification_status'=>'NONE'],['payment_methods'=>[['type'=>'Visa','last4'=>'2222']]],
        ['payment_methods'=>[['type'=>'Mastercard','last4'=>'1111']]],['payment_methods'=>[]]
    ] as $invalid){
        $result=$vault->reconcile($card['id'],'Fixture','123456789',$expected,array_replace($proof,$invalid));
        expect($result['status']==='SUBMITTED_UNVERIFIED'&&$result['submitted']===false&&$result['funding_verified']===false,'Unsafe reconciliation claim');
        expect(file_get_contents($directory.'/cards.json')===$before,'Unproven inspection unlocked retry');
    }
    // Native HTTP proof needs all fresh RK/BM/payment relation gates, and a
    // single observed credential. Filtered emptiness never unlocks resubmit.
    $native=array_replace($proof,['source'=>'private_facebook_billing_static_methods',
        'business_scope_verified'=>true,'payment_account_relation_verified'=>true,
        'methods_query_verified'=>true,'browser_started'=>false,'inventory_complete'=>false,
        'payment_methods'=>[['type'=>'Visa','last4'=>'1111','credential_id'=>'native-card-node']]]);
    foreach([['business_scope_verified'=>false],['business_scope_verified'=>1],
        ['payment_account_relation_verified'=>false],['methods_query_verified'=>false],['browser_started'=>true],
        ['payment_methods'=>[['type'=>'Visa','last4'=>'1111']]],
        ['payment_methods'=>[['type'=>'Visa','last4'=>'1111','credential_id'=>'native-card-node'],
                             ['type'=>'Visa','last4'=>'1111','credential_id'=>'second-card-node']]],
        ['verification_status'=>'NONE','payment_methods'=>[]]
    ] as $invalid){
        $result=$vault->reconcile($card['id'],'Fixture','123456789',$expected,array_replace($native,$invalid));
        expect($result['status']==='SUBMITTED_UNVERIFIED'&&!isset($result['retry_review']),'Unsafe native HTTP proof reconciled');
        expect(file_get_contents($directory.'/cards.json')===$before,'Incomplete native proof changed durable binding');
    }
    $vault->begin($card['id'],'HTTP profile','423456789');
    $vault->finish($card['id'],'HTTP profile','423456789','SUBMITTED_UNVERIFIED');
    $nativeExpected=$vault->binding($card['id'],'HTTP profile','423456789');
    $nativeScope=array_replace($native,['profile_id'=>'HTTP profile','account_id'=>'423456789']);
    $nativeResult=$vault->reconcile($card['id'],'HTTP profile','423456789',$nativeExpected,$nativeScope);
    expect($nativeResult['status']==='LINKED'&&$nativeResult['funding_verified']===false,'Native positive proof not reconciled');
    $nativeExpected=$vault->binding($card['id'],'HTTP profile','423456789');
    $nativeOther=array_replace($nativeScope,['payment_methods'=>[['type'=>'Visa','last4'=>'9999','credential_id'=>'other-node']]]);
    $vault->reconcile($card['id'],'HTTP profile','423456789',$nativeExpected,$nativeOther);
    expect($vault->linkedBinding($card['id'],'HTTP profile','423456789')!==null,'Filtered native read erased last verified link');
    rejected(fn()=>$vault->reconcile($card['id'],'Fixture','123456789',null,$proof),'CARD_BINDING_CHANGED');
    $result=$vault->reconcile($card['id'],'Fixture','123456789',$expected,$proof);
    expect($result['status']==='LINKED'&&$result['submitted']===false&&$result['funding_verified']===false,'Read-only positive proof not reconciled');
    expect($vault->linkedBinding($card['id'],'Fixture','123456789')['checked_live']===true,'Live proof not retained');
    $vault->finish($card['id'],'Fixture','123456789','SUBMITTED_UNVERIFIED',['code'=>'CARD_LINK_NOT_VERIFIED','submitted'=>null,'number'=>'4111111111111111','cvv'=>'123']);
    $state=$vault->binding($card['id'],'Fixture','123456789');expect($state['last_result_code']==='CARD_LINK_NOT_VERIFIED'&&$state['submitted']===null,'Safe result metadata lost');
    expect(!str_contains(file_get_contents($directory.'/cards.json'),'4111111111111111'),'Raw result stored');
    rejected(fn()=>$vault->begin($card['id'],'Fixture','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    $vault->finish($card['id'],'Fixture','123456789','LINKED');
    expect($vault->linkedBinding($card['id'],'Fixture','123456789')['status']==='LINKED','No-CVV linked lookup');
    expect($vault->linkedBinding($card['id'],'Other profile','123456789')===null,'No-CVV lookup crossed profile');
    expect($vault->linkedBinding($card['id'],'Fixture','987654321')===null,'No-CVV lookup crossed account');
    expect($vault->begin($card['id'],'Fixture','123456789')['status']==='LINKED','Idempotent linkage');
    expect($vault->begin($card['id'],'Other profile','123456789')['status']==='IN_PROGRESS','Profile scope');
    expect($vault->linkedBinding($card['id'],'Other profile','123456789')===null,'In-progress binding treated as linked');
    $saved=$vault->updateBilling($card['id'],['address'=>'Fixture Street','city'=>'Fixture City']);
    expect($saved['id']===$card['id']&&count($vault->all()['cards'])===1,'Billing update duplicated card');
    expect($vault->secret($card['id'])['number']==='4111111111111111','Billing update changed PAN');
    expect($vault->missingBilling($card['id'],[['kind'=>'holder','required'=>false],['kind'=>'postal_code','required'=>true],['kind'=>'cvv','required'=>true]])===['postal_code'],'Billing preflight leaked or invented fields');
    foreach(['cvv','number','month'] as $forbidden)rejected(fn()=>$vault->updateBilling($card['id'],[$forbidden=>'fixture']),'CARD_BILLING_PATCH_INVALID');
    // Meta renders Amex as "American Express"; vault aliases must still prove
    // the exact saved card. Discover must not be collapsed to generic "Card".
    $amex=$vault->add(['number'=>'378282246310005','month'=>12,'year'=>2099,'holder'=>'Amex Fixture']);
    expect($amex['brand']==='Amex','Amex brand detection');
    $vault->begin($amex['id'],'Amex profile','223456789');
    $vault->finish($amex['id'],'Amex profile','223456789','SUBMITTED_UNVERIFIED');
    $amexExpected=$vault->binding($amex['id'],'Amex profile','223456789');
    $amexProof=['profile_id'=>'Amex profile','account_id'=>'223456789','account_scope_verified'=>true,'checked_live'=>true,
        'source'=>'private_facebook_billing_ui','verification_status'=>'LINKED','payment_methods'=>[['type'=>'American Express','last4'=>'0005']]];
    expect($vault->reconcile($amex['id'],'Amex profile','223456789',$amexExpected,$amexProof)['status']==='LINKED','Amex alias was not reconciled');

    $discover=$vault->add(['number'=>'6011111111111117','month'=>12,'year'=>2099,'holder'=>'Discover Fixture']);
    expect($discover['brand']==='Discover','Discover brand detection');
    $vault->begin($discover['id'],'Discover profile','323456789');
    $vault->finish($discover['id'],'Discover profile','323456789','SUBMITTED_UNVERIFIED');
    $discoverExpected=$vault->binding($discover['id'],'Discover profile','323456789');
    $discoverProof=['profile_id'=>'Discover profile','account_id'=>'323456789','account_scope_verified'=>true,'checked_live'=>true,
        'source'=>'private_facebook_billing_ui','verification_status'=>'LINKED','payment_methods'=>[['type'=>'Discover','last4'=>'1117']]];
    expect($vault->reconcile($discover['id'],'Discover profile','323456789',$discoverExpected,$discoverProof)['status']==='LINKED','Discover live proof was not reconciled');
    $vault->finish($card['id'],'Other profile','123456789','SUBMITTED_UNVERIFIED');
    $key=hash('sha256','Other profile|123456789');$path=$directory.'/cards.json';
    $data=json_decode(file_get_contents($path),true);$data['bindings'][$key]['updated_at']=gmdate('c',time()-240);file_put_contents($path,json_encode($data));
    $old=$vault->binding($card['id'],'Other profile','123456789');
    $empty=array_replace($proof,['profile_id'=>'Other profile','verification_status'=>'NONE','payment_methods'=>[]]);
    $before=file_get_contents($path);
    foreach([['checked_live'=>false],['checked_live'=>1],['account_id'=>'987654321'],['source'=>'cached'],['verification_status'=>'UNVERIFIED'],['payment_methods'=>[['type'=>'Visa','last4'=>'1111']]]] as $bad){
        $check=$vault->reconcile($card['id'],'Other profile','123456789',$old,array_replace($empty,$bad));
        expect(!isset($check['retry_review']),'Unproven absence offered retry');
    }
    $check=$vault->reconcile($card['id'],'Other profile','123456789',$old,$empty);$token=$check['retry_review']['token'];
    expect(file_get_contents($path)===$before,'Review silently unlocked financial submission');
    rejected(fn()=>$vault->begin($card['id'],'Other profile','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    rejected(fn()=>$vault->beginReviewed($card['id'],'Other profile','123456789','invalid',$old,$empty),'CARD_RETRY_REVIEW_EXPIRED');
    $tampered=substr($token,0,-1).(str_ends_with($token,'a')?'b':'a');
    rejected(fn()=>$vault->beginReviewed($card['id'],'Other profile','123456789',$tampered,$old,$empty),'CARD_RETRY_REVIEW_INVALID');
    rejected(fn()=>$vault->beginReviewed($card['id'],'Other profile','123456789',$token,$old,array_replace($empty,['verification_status'=>'LINKED'])),'CARD_RETRY_ACCOUNT_NOT_EMPTY');
    $new=$vault->beginReviewed($card['id'],'Other profile','123456789',$token,$old,$empty);
    expect($new['reviewed_retry']===true&&$new['attempt_id']!==$old['attempt_id'],'Retry has no independent attempt');
    $before=file_get_contents($path);
    rejected(fn()=>$vault->beginReviewed($card['id'],'Other profile','123456789',$token,$old,$empty),'CARD_BINDING_CHANGED');
    rejected(fn()=>$vault->finish($card['id'],'Other profile','123456789','FAILED',[],$old['attempt_id']),'CARD_BINDING_CHANGED');
    expect(file_get_contents($path)===$before,'Late result overwrote reviewed retry');
    $vault->finish($card['id'],'Other profile','123456789','ACTION_REQUIRED',[],$new['attempt_id']);
    $data=json_decode(file_get_contents($path),true);$data['bindings'][$key]['updated_at']=gmdate('c',time()-240);file_put_contents($path,json_encode($data));
    $old=$vault->binding($card['id'],'Other profile','123456789');
    expect(!isset($vault->reconcile($card['id'],'Other profile','123456789',$old,$empty)['retry_review']),'Bank action was made retryable');
    // Pre-submit Meta gates remain actionable; bank/unknown submissions do not.
    $pre=$vault->begin($card['id'],'Before save','123456789');
    $vault->finish($card['id'],'Before save','123456789','ACTION_REQUIRED',
        ['code'=>'PAYMENT_ACCOUNT_SETUP_REQUIRED','submitted'=>false],$pre['attempt_id']);
    expect($vault->binding($card['id'],'Before save','123456789')['status']==='BLOCKED','Pre-submit gate stranded target');
    $next=$vault->begin($card['id'],'Before save','123456789');
    expect($next['attempt_id']!==$pre['attempt_id'],'Pre-submit continuation reused attempt');
    $vault->finish($card['id'],'Before save','123456789','ACTION_REQUIRED',
        ['code'=>'CARD_BANK_CONFIRMATION_REQUIRED','submitted'=>true],$next['attempt_id']);
    rejected(fn()=>$vault->begin($card['id'],'Before save','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    rejected(fn()=>$vault->finish($card['id'],'Before save','123456789','LINKED',[],$next['attempt_id']),'CARD_BINDING_CHANGED');
    // Old stored pre-submit ACTION_REQUIRED rows also survive an upgrade.
    $legacyKey=hash('sha256','Before save|123456789');
    $data=json_decode(file_get_contents($path),true);$data['bindings'][$legacyKey]['submitted']=false;file_put_contents($path,json_encode($data));
    $visible=array_values(array_filter($vault->all()['bindings'],fn($b)=>$b['profile']==='Before save'))[0];
    expect($visible['status']==='BLOCKED','Legacy pre-submit gate stayed pending in UI');
    expect($vault->begin($card['id'],'Before save','123456789')['status']==='IN_PROGRESS','Legacy pre-submit gate blocked continuation');
    foreach([true,null,'missing'] as $submission){
        $profile='Inconsistent '.json_encode($submission);
        $attempt=$vault->begin($card['id'],$profile,'123456789');
        $metadata=$submission==='missing'?[]:['submitted'=>$submission];
        $vault->finish($card['id'],$profile,'123456789','BLOCKED',$metadata,$attempt['attempt_id']);
        expect($vault->binding($card['id'],$profile,'123456789')['status']==='SUBMITTED_UNVERIFIED','Uncertain failure released retry');
        rejected(fn()=>$vault->begin($card['id'],$profile,'123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    }
    // A conclusive live check must invalidate a previously cached LINKED row.
    $vault->begin($card['id'],'Stale link','123456789');
    $vault->finish($card['id'],'Stale link','123456789','LINKED',['submitted'=>true]);
    $staleKey=hash('sha256','Stale link|123456789');
    $data=json_decode(file_get_contents($path),true);$data['bindings'][$staleKey]['updated_at']=gmdate('c',time()-240);file_put_contents($path,json_encode($data));
    $stale=$vault->binding($card['id'],'Stale link','123456789');
    $missing=array_replace($empty,['profile_id'=>'Stale link']);
    $uncertain=array_replace($missing,['verification_status'=>'UNVERIFIED']);
    $vault->reconcile($card['id'],'Stale link','123456789',$stale,$uncertain);
    expect($vault->linkedBinding($card['id'],'Stale link','123456789')!==null,'Inconclusive check erased previous proof');
    $checked=$vault->reconcile($card['id'],'Stale link','123456789',$stale,$missing);
    expect($vault->linkedBinding($card['id'],'Stale link','123456789')===null,'Fresh absence left cached LINKED');
    $changed=$vault->binding($card['id'],'Stale link','123456789');
    expect($changed['submitted']===true&&$changed['updated_at']===$stale['updated_at'],'Read-only check rewrote attempt history');
    expect(isset($checked['retry_review']),'Confirmed stale absence cannot be reviewed');
    rejected(fn()=>$vault->begin($card['id'],'Stale link','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    expect($vault->beginReviewed($card['id'],'Stale link','123456789',$checked['retry_review']['token'],$changed,$missing)['status']==='IN_PROGRESS','Stale link review is not bound to persisted state');
    $vault->finish($card['id'],'Stale link','123456789','LINKED');
    $stale=$vault->binding($card['id'],'Stale link','123456789');
    $other=array_replace($proof,['profile_id'=>'Stale link','payment_methods'=>[['type'=>'Visa','last4'=>'2222']]]);
    $checked=$vault->reconcile($card['id'],'Stale link','123456789',$stale,$other);
    expect($vault->linkedBinding($card['id'],'Stale link','123456789')===null&&!isset($checked['retry_review']),'Different live card left stale link or allowed repeat');
    $data=json_decode(file_get_contents($directory.'/cards.json'),true);$bytes=base64_decode($data['cards'][$card['id']]['encrypted']);$bytes[30]=chr(ord($bytes[30])^1);
    $data['cards'][$card['id']]['encrypted']=base64_encode($bytes);file_put_contents($directory.'/cards.json',json_encode($data));
    try{$vault->secret($card['id']);throw new RuntimeException('Tamper accepted');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_DECRYPTION_FAILED','Authenticated encryption');}
    unlink($directory.'/key');
    try{new RemaskPaymentCardVault($directory);throw new RuntimeException('Key replaced');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_KEY_MISSING','Missing key replaced');}
    echo "card vault: encryption, masking, deduplication, CVV rejection, scope and uncertain submission passed\n";
}finally{foreach(glob($directory.'/*')?:[] as $file)unlink($file);rmdir($directory);}
