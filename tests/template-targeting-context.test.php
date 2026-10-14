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
$seen=[];
RemaskTemplateTargetingContext::resolve($profiles,static function($name) use (&$seen,$catalogs) {
    $seen[]=$name; return $catalogs[$name];
},'16',['16']);
expect(!in_array('16',$seen,true),'banned profile excluded before catalog or network access even when preferred');
$disabled=$profiles; $disabled[0]['banned']=true; $disabled[1]['disabled']=true;
$seen=[];
try { RemaskTemplateTargetingContext::resolve($disabled,static function($name) use (&$seen,$catalogs) {
    $seen[]=$name; return $catalogs[$name];
}); } catch(RuntimeException $error) {}
expect($seen===['20'],'explicit banned and disabled profiles cannot supply reference reads');
foreach(['PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED','PRIVATE_HTTP_PROXY_UNAVAILABLE','SESSION_EXPIRED'] as $code) {
    $failure=RemaskTemplateTargetingContext::failure(new RuntimeException($code),'16');
    expect(!str_contains($failure['message'],'16') && !isset($failure['source_profile']),'templates never ask to repair a named FB profile');
    expect($failure['settings_preserved']===true && $failure['template_account_bound']===false,'saving remains independent of lookup availability');
}
$secret=RemaskTemplateTargetingContext::failure(new RuntimeException('proxy http://secret:password@example.com cookie=xs-sensitive'),'19');
expect(!str_contains(json_encode($secret),'password') && !str_contains(json_encode($secret),'xs-sensitive'),'transport secrets never reach UI');
$file=sys_get_temp_dir().'/remask-template-reference-'.bin2hex(random_bytes(8)).'.json';
$reference=new RemaskTemplateTargetingReference($file);
try {
    expect($reference->get('interests','fixture',1000)===null,'unknown queries cannot produce invented IDs');
    try { $reference->put('interests','fixture',['data'=>[['id'=>'123456','name'=>'Synthetic']]],1000);
        throw new RuntimeException('unverified reference stored'); }
    catch(RuntimeException $error) { expect($error->getMessage()==='TEMPLATE_LOOKUP_ACCESS_UNVERIFIED','live scoped provenance required'); }
    $proof=['checked_live'=>true,'account_scope_verified'=>true,'data'=>[
        ['id'=>'123456','name'=>'Fixture reference','profile'=>'16','account_id'=>'654321','access_token'=>'synthetic-secret']]];
    $reference->put('interests','fixture',$proof,1000);
    $row=$reference->get('interests','fixture',1001);
    expect($row['data']===[['id'=>'123456','name'=>'Fixture reference']],'reference result retains actual IDs but no profile or secrets');
    expect($row['checked_live']===false && $row['template_account_bound']===false,'cache never fabricates live permission');
    expect($reference->get('locations','fixture',1001)===null,'query types isolated');
    expect($reference->get('interests','another',1001)===null,'query keys isolated');
    expect($reference->get('interests','fixture',1000+86401)['reference_stale']===true,'stale reference explicitly identified');
    expect($reference->get('interests','fixture',1000+2592001)===null,'over-age reference expires');
    $reference->block('16',1000); expect($reference->blocked(1001)===['16'],'failed source cooldown');
    expect($reference->blocked(1601)===[],'cooldown bounded without permanently banning healthy sources');
    $other=new RemaskTemplateTargetingReference($file.'-other');
    expect($other->get('interests','fixture',1001)===null,'workspaces do not share private reference stores');
} finally { foreach([$file,$file.'.lock',$file.'-other',$file.'-other.lock'] as $path) @unlink($path); }
require dirname(__DIR__).'/railway-private-form-schema.php';
$builder=['identity'=>['page_id'=>'123456','instagram_actor_id'=>'654321'],
    'existing_media'=>['source_instagram_media_id'=>'999999'],
    'targeting'=>['interests'=>[['id'=>'888888','name'=>'Fixture']], 'custom_audiences'=>[['id'=>'777777']]],
    'adset'=>['promoted_object'=>['pixel_id'=>'555555','custom_event_type'=>'PURCHASE']],
    'creative'=>['asset_feed_spec'=>['images'=>[['image_hash'=>'old-owned-hash']]]]];
$unbound=RemaskPrivateFormSchema::unbindTemplate($builder);
expect($unbound['identity']===[] && $unbound['existing_media']===[],'identity and owned posts deferred to launch');
expect($unbound['targeting']['interests']===$builder['targeting']['interests'],'common interest IDs preserved');
expect(!isset($unbound['targeting']['custom_audiences']) && $unbound['adset']['promoted_object']===['custom_event_type'=>'PURCHASE'],'account assets deferred, generic settings retained');
expect(!str_contains(json_encode($unbound),'old-owned-hash'),'nested owned media cannot leak into reusable template');
$endpoint=file_get_contents(dirname(__DIR__).'/railway-template-targeting-search.php');
expect(!str_contains($endpoint,"\$input['profile']") && !str_contains($endpoint,"\$input['account_id']"),'client cannot choose destination through template lookup');
expect(str_contains($endpoint,"'checked_live'") && str_contains($endpoint,"'account_scope_verified'"),'actual Meta read proof remains required');
echo "Template targeting uses automatic scoped reads without a publishing binding.\n";
