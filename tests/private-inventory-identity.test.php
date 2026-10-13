<?php
require dirname(__DIR__).'/railway-private-inventory-identity.php';
function expect($condition, $message) { if (!$condition) throw new RuntimeException($message); }
$empty=['businesses'=>[],'ad_accounts'=>[],'updated_at'=>0,'source'=>''];
$bindings=['19'=>['ad_accounts'=>['123456'=>['business_id'=>'123456','ad_account_id'=>'654321','account_name'=>'Fixture RK','account_status'=>1]]],
    '20'=>['ad_accounts'=>[['business_id'=>'222222','ad_account_id'=>'333333']]]];
$result=RemaskPrivateInventoryIdentity::merge($empty,$bindings,'19','999999');
expect(count($result['ad_accounts'])===1,'exact profile identity missing');
expect($result['ad_accounts'][0]['business_id']==='123456','wrong Business');
expect($result['ad_accounts'][0]['account_status']===null,'saved identity cannot prove ACTIVE');
expect($result['ad_accounts'][0]['advertising_access_verified']===false,'no live permission');
expect($result['updated_at']===0 && $result['source']==='','no invented fresh inventory');
expect(RemaskPrivateInventoryIdentity::merge($result,$bindings,'19','999999')===$result,'duplicate merge');
expect(RemaskPrivateInventoryIdentity::merge($empty,$bindings,'unknown','999999')===$empty,'foreign profile leakage');
expect(RemaskPrivateInventoryIdentity::merge($empty,$bindings,'19','123456')===$empty,'personal scope is not a Business');
$legacy=['19'=>['business_id'=>'123456','ad_account_id'=>'654321']];
expect(count(RemaskPrivateInventoryIdentity::merge($empty,$legacy,'19','999999')['ad_accounts'])===1,'legacy confirmation missing');
$conflict=$empty; $conflict['ad_accounts']=[['id'=>'654321','business_id'=>'777777']];
try { RemaskPrivateInventoryIdentity::merge($conflict,$bindings,'19','999999'); throw new RuntimeException('conflict was accepted'); }
catch (RuntimeException $error) { expect($error->getMessage()==='PRIVATE_ACCOUNT_RELATION_CONFLICT','wrong conflict'); }
$duplicate=['19'=>['ad_accounts'=>[['business_id'=>'123456','ad_account_id'=>'654321'],['business_id'=>'777777','ad_account_id'=>'654321']]]];
try { RemaskPrivateInventoryIdentity::merge($empty,$duplicate,'19','999999'); throw new RuntimeException('ambiguous confirmation accepted'); }
catch (RuntimeException $error) { expect($error->getMessage()==='PRIVATE_ACCOUNT_RELATION_CONFLICT','wrong ambiguous conflict'); }
echo "Confirmed RK identities stay profile-scoped and never promote live readiness.\n";
