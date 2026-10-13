<?php
require dirname(__DIR__).'/railway-template-targeting-context.php';
function expect($condition, $message) { if (!$condition) throw new RuntimeException($message); }
$profiles=[['name'=>'16','proxy_configured'=>true,'cookies_ready'=>true],['name'=>'19','proxy_configured'=>true,'cookies_ready'=>true],['name'=>'20','proxy_configured'=>true,'cookies_ready'=>true]];
$catalogs=['16'=>['profile'=>'16','ad_accounts'=>['data'=>[]]],
    '19'=>['profile'=>'19','ad_accounts'=>['data'=>[['profile'=>'19','account_id'=>'654321','business_id'=>'123456','account_status'=>null]]]],
    '20'=>['profile'=>'20','ad_accounts'=>['data'=>[['profile'=>'20','account_id'=>'222222','business_id'=>'333333','account_status'=>1]]]]];
$load=static fn($name)=>$catalogs[$name];
$context=RemaskTemplateTargetingContext::resolve($profiles,$load);
expect($context['profile']==='20' && $context['account_id']==='222222','choose available scoped identity without UI selection');
$context=RemaskTemplateTargetingContext::resolve($profiles,$load,'19');
expect($context['profile']==='19' && $context['account_id']==='654321','bounded direct-test context remains service-only');
expect($context['catalog']['ad_accounts']['data'][0]['account_status']===null,'no fabricated live status');
$catalogs['16']['ad_accounts']['data']=[['profile'=>'20','account_id'=>'999999','business_id'=>'123456','account_status'=>1]];
$catalogs['20']['ad_accounts']['data'][0]['account_status']=2;
expect(RemaskTemplateTargetingContext::resolve($profiles,static fn($name)=>$catalogs[$name])['profile']==='19','foreign and disabled accounts excluded');
try { RemaskTemplateTargetingContext::resolve([['name'=>'19','proxy_configured'=>false,'cookies_ready'=>true]],$load); throw new RuntimeException('unconfigured transport accepted'); }
catch(RuntimeException $e) { expect($e->getMessage()==='TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE','no source message'); }
try { RemaskTemplateTargetingContext::resolve($profiles,static fn($name)=>['profile'=>'foreign']); throw new RuntimeException('mismatched profile accepted'); }
catch(RuntimeException $e) { expect($e->getMessage()==='TEMPLATE_LOOKUP_PROFILE_MISMATCH','scope mismatch rejected'); }
$noCookies=$profiles; foreach($noCookies as &$row) $row['cookies_ready']=false; unset($row);
try { RemaskTemplateTargetingContext::resolve($noCookies,$load,'19'); throw new RuntimeException('direct test accepted missing cookies'); }
catch(RuntimeException $e) { expect($e->getMessage()==='TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE','cookies required even in direct mode'); }
$failure=RemaskTemplateTargetingContext::failure(new RuntimeException('PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED'),'19');
expect(str_contains($failure['message'],'подтверждения входа') && str_contains($failure['message'],'19'),'actionable checkpoint message');
expect($failure['settings_preserved']===true && !str_contains($failure['message'],'РК'),'auth repair is not destination binding');
$secret=RemaskTemplateTargetingContext::failure(new RuntimeException('proxy http://secret:password@example.com cookie=xs-sensitive'),'19');
expect(!str_contains(json_encode($secret),'password') && !str_contains(json_encode($secret),'xs-sensitive'),'transport secrets never reach UI');
$endpoint=file_get_contents(dirname(__DIR__).'/railway-template-targeting-search.php');
expect(!str_contains($endpoint,"\$input['profile']") && !str_contains($endpoint,"\$input['account_id']"),'client cannot choose destination through template lookup');
expect(str_contains($endpoint,"'checked_live'") && str_contains($endpoint,"'account_scope_verified'"),'actual Meta read proof remains required');
echo "Template targeting uses automatic scoped reads without a publishing binding.\n";
