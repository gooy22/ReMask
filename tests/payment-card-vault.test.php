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
        file_put_contents($root.'/classes/RemaskPrivateLaunchCatalog.php',"<?php class RemaskPrivateLaunchCatalog {static function load(\$p){return [];} static function asset(\$c,\$kind,\$id){return ['id'=>\$id];}}");
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
    foreach(['4111111111111111','Test Holder','security_code','cvv','cvc'] as $secret)expect(!str_contains($disk,$secret),'Secret on disk');
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
    $data=json_decode(file_get_contents($directory.'/cards.json'),true);$bytes=base64_decode($data['cards'][$card['id']]['encrypted']);$bytes[30]=chr(ord($bytes[30])^1);
    $data['cards'][$card['id']]['encrypted']=base64_encode($bytes);file_put_contents($directory.'/cards.json',json_encode($data));
    try{$vault->secret($card['id']);throw new RuntimeException('Tamper accepted');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_DECRYPTION_FAILED','Authenticated encryption');}
    unlink($directory.'/key');
    try{new RemaskPaymentCardVault($directory);throw new RuntimeException('Key replaced');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_KEY_MISSING','Missing key replaced');}
    echo "card vault: encryption, masking, deduplication, CVV rejection, scope and uncertain submission passed\n";
}finally{foreach(glob($directory.'/*')?:[] as $file)unlink($file);rmdir($directory);}
