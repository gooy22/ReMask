<?php
require dirname(__DIR__).'/railway-template-targeting-context.php';
function expect($condition, $message) { if (!$condition) throw new RuntimeException($message); }
$profiles=[['name'=>'16','proxy_configured'=>true,'cookies_ready'=>true],['name'=>'19','proxy_configured'=>true,'cookies_ready'=>true],['name'=>'20','proxy_configured'=>true,'cookies_ready'=>true]];
$catalogs=['16'=>['profile'=>'16','ad_accounts'=>['data'=>[]]],
    '19'=>['profile'=>'19','ad_accounts'=>['data'=>[['profile'=>'19','account_id'=>'654321','business_id'=>'123456','account_status'=>null]]]],
    '20'=>['profile'=>'20','ad_accounts'=>['data'=>[['profile'=>'20','account_id'=>'222222','business_id'=>'333333','account_status'=>1]]]]];
$load=static fn($name)=>$catalogs[$name];
$exactLoaded=[];
$exact=RemaskTemplateTargetingContext::exactCandidate($profiles[1],static function($name) use (&$exactLoaded,$catalogs) {
    $exactLoaded[]=$name; return $catalogs[$name];
},'act_654321');
expect($exact['profile']==='19' && $exact['account_id']==='654321' && $exactLoaded===['19'],
    'a live targeting fallback can read only the selected profile and exact RK');
try { RemaskTemplateTargetingContext::exactCandidate($profiles[1],$load,'222222');
    throw new LogicException('fallback borrowed another RK');
} catch(RuntimeException $error) {
    expect($error->getMessage()==='TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE','a selected profile cannot borrow a different RK');
}
try { RemaskTemplateTargetingContext::exactCandidate($profiles[1],$load,'654321',['19']);
    throw new LogicException('excluded profile supplied a targeting fallback');
} catch(RuntimeException $error) {
    expect($error->getMessage()==='TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE','an excluded profile cannot supply live targeting');
}
$new=RemaskTemplateTargetingContext::candidates([$profiles[0]],$load,'',[],true);
expect($new[0]['account_id']==='' && $new[0]['profile']==='16','empty catalogs remain eligible for read-only discovery');
$newProfile=['name'=>'25','proxy_configured'=>false,'cookies_ready'=>true];
$newLoad=static fn($name)=>$name==='25' ? ['profile'=>'25','ad_accounts'=>['data'=>[]]] : $load($name);
$preferredNew=RemaskTemplateTargetingContext::candidates([...$profiles,$newProfile],$newLoad,'25',[],true);
expect($preferredNew[0]['profile']==='25' && $preferredNew[0]['account_id']==='',
    'new direct-test source must precede saved active and unverified accounts within the bounded attempt budget');
$excludedNew=RemaskTemplateTargetingContext::candidates([...$profiles,$newProfile],$newLoad,'25',['25'],true);
expect(!in_array('25',array_column($excludedNew,'profile'),true),'preference never overrides source exclusion');
$uiDirect=RemaskTemplateTargetingContext::candidates([...$profiles,$newProfile+['direct_ready'=>true]],$newLoad,'',[],true);
expect($uiDirect[0]['profile']==='25','UI-authorized actor-validated direct lease is eligible without Railway env preference');
$uiExcluded=RemaskTemplateTargetingContext::candidates([...$profiles,$newProfile+['direct_ready'=>true]],$newLoad,'',['25'],true);
expect(!in_array('25',array_column($uiExcluded,'profile'),true),'direct lease never overrides excluded or blocked sources');
$proof=['ok'=>true,'profile'=>'20','account_id'=>'123456','business_id'=>'654321','checked_live'=>true,
    'account_scope_verified'=>true,'transport'=>'private_http_h2','source_context_discovered'=>true];
expect(RemaskTemplateTargetingContext::referenceResponse('20',$proof)===$proof,'verified new context accepted');
foreach([['profile'=>'foreign'],['checked_live'=>false],['account_id'=>''],['business_id'=>''],
    ['transport'=>'chromium'],['source_context_discovered'=>false]] as $bad) {
    try { RemaskTemplateTargetingContext::referenceResponse('20',array_replace($proof,$bad)); throw new LogicException('unverified context accepted'); }
    catch(RuntimeException $error) { expect($error->getMessage()==='TEMPLATE_LOOKUP_ACCESS_UNVERIFIED','discovery proof guard'); }
}
$cachedOptions=array_replace($proof,[
    'checked_live'=>false,'account_scope_verified'=>false,'template_account_bound'=>false,
    'source'=>'private_meta_targeting_option_index',
    'data'=>[['id'=>'123456','name'=>'Travel','type'=>'interests']],
    '_cache'=>['hit'=>true,'cached_only'=>true,'index'=>true,'stale'=>false],
]);
expect(RemaskTemplateTargetingContext::referenceResponse('20',$cachedOptions)===$cachedOptions,
    'account-scoped saved Meta options are usable as suggestions without claiming live permission');
$unmarkedCache=$cachedOptions;
$unmarkedCache['_cache']['cached_only']=false;
try { RemaskTemplateTargetingContext::referenceResponse('20',$unmarkedCache);
    throw new LogicException('unmarked cached option accepted as live proof'); }
catch(RuntimeException $error) {
    expect($error->getMessage()==='TEMPLATE_LOOKUP_ACCESS_UNVERIFIED','cached options never substitute for fresh permission proof');
}
try {RemaskTemplateTargetingContext::referenceResponse('20',['detail'=>'TEMPLATE_REFERENCE_READ_TIMEOUT']);throw new LogicException('failed worker accepted');}
catch(RuntimeException $error){expect($error->getMessage()==='TEMPLATE_REFERENCE_READ_TIMEOUT','typed string failure preserved');}
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
foreach(['PRIVATE_HTTP_CONNECTION_UNAVAILABLE','PRIVATE_HTTP_PROTOCOL_ERROR','PRIVATE_HTTP_READ_UNAVAILABLE',
    'PRIVATE_HTTP_PROXY_UNAVAILABLE','PRIVATE_HTTP_READ_TIMEOUT','BUSINESS_LOGIN_GATE','TWO_FACTOR_REQUIRED'] as $code)
    expect(RemaskTemplateTargetingContext::sourceFailure(new RuntimeException($code)), 'transport failure must cool down its source: '.$code);
foreach(['INVALID_TARGETING_QUERY','TARGETING_FIELDS_UNAVAILABLE','PRIVATE_ADS_DRAFT_REQUEST_REJECTED'] as $code)
    expect(!RemaskTemplateTargetingContext::sourceFailure(new RuntimeException($code)), 'a field/query error must not disable unrelated dictionaries');
expect(str_contains(RemaskTemplateTargetingContext::failure(new RuntimeException('BUSINESS_LOGIN_GATE'))['message'],'страницу входа'),
    'a successful connection returning a login page must not be presented as a connection outage');
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
    expect($reference->search('interests','Fixture',1001)['data'][0]['id']==='123456',
        'saved text-query results remain searchable without a browse tree or Meta connection');
    expect($reference->search('interests','',1001)['data'][0]['id']==='123456',
        'opening a picker can browse previously saved individual searches offline');
    expect($reference->search('behaviors','Fixture',1001)===null,'saved searches never cross dictionary types');
    $reference->put('interests','',array_replace($proof,['truncated'=>true,'catalog_count'=>1200]),1000);
    $browse=$reference->get('interests','',1001);
    expect($browse['truncated']===true && $browse['catalog_count']===1200,'browse cache preserves incomplete catalog metadata');
    expect($reference->search('interests','Fixture',1001)['truncated']===true,'cached text matches never claim complete catalog coverage');
    $large=array_replace($proof,['data'=>array_fill(0,501,['id'=>'123456','name'=>'Fixture'])]);
    $reference->put('behaviors','',$large,1000);
    expect(count($reference->get('behaviors','',1001)['data'])===100 && $reference->get('behaviors','',1001)['truncated']===true,
        'browser response stays bounded independently of the stored catalog');
    $many=[];for($i=0;$i<601;$i++)$many[]=['id'=>(string)(700000+$i),'name'=>'Interest '.$i];
    $reference->put('interests','',array_replace($proof,['data'=>$many]),1000);
    expect($reference->search('interests','Interest 600',1001)['data'][0]['id']==='700600',
        'search finds leaves after the old 500-row cache boundary');
    $firstPage=$reference->get('interests','',1001,0);
    $secondPage=$reference->get('interests','',1001,100);
    $lastPage=$reference->get('interests','',1001,600);
    expect(count($firstPage['data'])===100 && $firstPage['next_offset']===100
        && $firstPage['data'][0]['id']==='700000','first catalog page has exact saved IDs');
    expect(count($secondPage['data'])===100 && $secondPage['next_offset']===200
        && $secondPage['data'][0]['id']==='700100','second catalog page exposes the next 100 options');
    expect(count($lastPage['data'])===1 && $lastPage['next_offset']===null
        && $lastPage['data'][0]['id']==='700600','the last page retains the final saved leaf');
    $filteredPage=$reference->search('interests','Interest',1001,100);
    expect($filteredPage['data'][0]['id']==='700100' && $filteredPage['next_offset']===200,
        'cached query search can page later matching results');

    expect($reference->search('interests','Interest 600',1000+86401)['reference_stale']===true,
        'genuine cached labels remain searchable after one day without claiming live eligibility');
    expect($reference->search('interests','Not in the browse tree',1001)===null,
        'browse cache misses still permit actual Meta text search');
    expect($reference->get('interests','fixture',1000+86401)['reference_stale']===true,'stale reference explicitly identified');
    expect($reference->get('interests','fixture',1000+2592001)===null,'over-age reference expires');
    $reference->block('16',1000); expect($reference->blocked(1001)===['16'],'failed source cooldown');
    expect($reference->blocked(1601)===[],'cooldown bounded without permanently banning healthy sources');
    $reference->block('29',1700,'BUSINESS_LOGIN_GATE');
    try {$reference->available([['profile'=>'29']],1701);throw new LogicException('gated source accepted');}
    catch(RuntimeException $error){expect($error->getMessage()==='BUSINESS_LOGIN_GATE','cooldown preserves the actual login cause');}
    expect($reference->available([['profile'=>'30']],1701)===[['profile'=>'30']], 'stored reasons never select or leak an unrelated profile');
    expect($reference->available([['profile'=>'29']],2301)===[['profile'=>'29']], 'login cooldown remains bounded');
    expect($reference->search('interests','Fixture',1701)!==null,'login failure never hides genuine cached query results');
    $other=new RemaskTemplateTargetingReference($file.'-other');
    expect($other->get('interests','fixture',1001)===null,'workspaces do not share private reference stores');
} finally { foreach([$file,$file.'.lock',$file.'-other',$file.'-other.lock'] as $path) @unlink($path); }
$manifest=getenv('REMASK_TEST_TARGETING_REFERENCE') ?: dirname(__DIR__).'/python_backend/app/contracts/meta_targeting_reference_20261014.json';
if (!is_file($manifest)) $manifest='/var/www/html/classes/meta-targeting-reference.json';
$ru=RemaskModuleTargetingReference::search('locales','russi',$manifest);
expect($ru['data'][0]['id']==='17' && $ru['checked_live']===false,'source-derived Russian ID without a profile');
expect(RemaskModuleTargetingReference::search('locales','РУСС',$manifest)['data'][0]['id']==='17','Unicode case-insensitive search');
expect(count(RemaskModuleTargetingReference::search('locales','',$manifest)['data'])===72,'empty query lists actual languages');
expect(RemaskModuleTargetingReference::search('locales','no such language exists',$manifest)['data']===[],
    'complete language map misses do not fall through to network profiles');
expect(RemaskModuleTargetingReference::search('locations','Ukraine',$manifest)['data'][0]['key']==='UA','country English alias preserves ISO key');
$catalogPage1=RemaskModuleTargetingReference::search('locations','',$manifest,0);
$catalogPage2=RemaskModuleTargetingReference::search('locations','',$manifest,100);
expect($catalogPage1['next_offset']===100 && count($catalogPage1['data'])===100,
    'installed Meta region catalog shows its first page');
expect($catalogPage2['offset']===100 && count($catalogPage2['data'])>0
    && $catalogPage1['data'][0]['id']!==$catalogPage2['data'][0]['id'],
    'installed Meta region catalog exposes later entries without profile binding');

expect(RemaskModuleTargetingReference::search('locations','unsupported city query',$manifest)===null,'unknown cities still require live source');
$seen=[];
$available=RemaskTemplateTargetingContext::resolve($profiles,static function($name) use (&$seen,$catalogs) {
    $seen[]=$name; if($name==='16') throw new RuntimeException('BROKEN_SAVED_CATALOG'); return $catalogs[$name];
});
expect($available['profile']==='19','one unreadable saved catalog cannot break all lookup candidates');
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
$selectedProfileAt=strpos($endpoint,"\$selectedProfile=trim((string)(\$input['profile'] ?? ''))");
$selectedAccountAt=strpos($endpoint,"\$selectedAccount=preg_replace('/^act_/i','',trim((string)(\$input['account_id'] ?? '')))");
$exactScopeAt=strpos($endpoint,'RemaskTemplateTargetingContext::exactCandidate(');
expect($selectedProfileAt!==false && $selectedAccountAt!==false && $exactScopeAt!==false
    && $selectedProfileAt<$exactScopeAt && $selectedAccountAt<$exactScopeAt,
    'live targeting fallback is constrained to the explicitly selected profile and exact RK');
expect(str_contains($endpoint,"'checked_live'") && str_contains($endpoint,"'account_scope_verified'"),'actual Meta read proof remains required');
$cachedIndexAt=strpos($endpoint,"\$cacheMeta=\$result['_cache'] ?? []");
$liveProofAt=strpos($endpoint,"if ((\$result['checked_live'] ?? null) !== true");
expect($cachedIndexAt!==false && $liveProofAt!==false && $cachedIndexAt<$liveProofAt,
    'saved index values are returned as suggestions before the live-read proof gate');
$moduleOnlyAt=strpos($endpoint,'if ($moduleOnly)');
$profileLookupAt=strpos($endpoint,"\$selectedProfile=trim((string)(\$input['profile'] ?? ''))");
expect($moduleOnlyAt!==false && $profileLookupAt!==false && $moduleOnlyAt<$profileLookupAt,
    'static GEO/language misses return before opening any profile or Meta context');
$cacheOnlyAt=strpos($endpoint,'if ($cacheOnly)');
expect($cacheOnlyAt!==false && $profileLookupAt!==false && $cacheOnlyAt<$profileLookupAt,
    'shared-cache misses return before opening any profile or Meta context');
expect(str_contains($endpoint,"'availability'=>'NOT_LOADED'") && str_contains($endpoint,"'template_account_bound'=>false"),
    'cache-only misses remain suggestion-only and cannot authorize a draft');
$catalogOverlay=file_get_contents(dirname(__DIR__).'/railway-launch-private-catalog-overlay.php');
$seedGuardAt=strpos($catalogOverlay,"if (in_array(\$type,['interests','behaviors'],true)");
$liveGuardAt=strpos($catalogOverlay,"\$result['checked_live']",$seedGuardAt);
$scopeGuardAt=strpos($catalogOverlay,"\$result['account_scope_verified']",$seedGuardAt);
$putAt=strpos($catalogOverlay,'$reference->put(',$seedGuardAt);
expect($seedGuardAt!==false && $liveGuardAt!==false && $scopeGuardAt!==false
    && $putAt!==false && $liveGuardAt<$putAt && $scopeGuardAt<$putAt,
    'only verified exact-account interest and behavior reads seed the shared suggestion reference');
echo "Template targeting uses automatic scoped reads without a publishing binding.\n";
