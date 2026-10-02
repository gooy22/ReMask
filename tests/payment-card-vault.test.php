<?php
declare(strict_types=1);
require __DIR__.'/../railway-payment-card-vault.php';
function expect(bool $test,string $message): void {if(!$test)throw new RuntimeException($message);}
function rejected(callable $fn,string $code): void {try{$fn();throw new RuntimeException('Expected rejection');}catch(InvalidArgumentException $e){expect($e->getMessage()===$code,'Unexpected rejection');}}
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
    $vault->finish($card['id'],'Fixture','123456789','SUBMITTED_UNVERIFIED');
    rejected(fn()=>$vault->begin($card['id'],'Fixture','123456789'),'CARD_BINDING_RECONCILE_REQUIRED');
    $vault->finish($card['id'],'Fixture','123456789','LINKED');
    expect($vault->begin($card['id'],'Fixture','123456789')['status']==='LINKED','Idempotent linkage');
    expect($vault->begin($card['id'],'Other profile','123456789')['status']==='IN_PROGRESS','Profile scope');
    $data=json_decode(file_get_contents($directory.'/cards.json'),true);$bytes=base64_decode($data['cards'][$card['id']]['encrypted']);$bytes[30]=chr(ord($bytes[30])^1);
    $data['cards'][$card['id']]['encrypted']=base64_encode($bytes);file_put_contents($directory.'/cards.json',json_encode($data));
    try{$vault->secret($card['id']);throw new RuntimeException('Tamper accepted');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_DECRYPTION_FAILED','Authenticated encryption');}
    unlink($directory.'/key');
    try{new RemaskPaymentCardVault($directory);throw new RuntimeException('Key replaced');}catch(RuntimeException $e){expect($e->getMessage()==='CARD_KEY_MISSING','Missing key replaced');}
    echo "card vault: encryption, masking, deduplication, CVV rejection, scope and uncertain submission passed\n";
}finally{foreach(glob($directory.'/*')?:[] as $file)unlink($file);rmdir($directory);}
