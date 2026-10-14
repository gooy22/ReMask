<?php
require __DIR__.'/../railway-private-form-schema.php';
require __DIR__.'/../railway-advertising-helper-client.php';
function check($value,$message){if(!$value)throw new RuntimeException($message);}
$input=['profile'=>'16','account_id'=>'999999','targeting_spec'=>'{"geo_locations":{"countries":["US"]}}',
    'daily_budget'=>'500','is_cbo_enabled'=>'false'];
$candidates=[['profile'=>'20','account_id'=>'123456','catalog'=>[]]];
$seen=[];
$reader=static function($profile,$account,$catalog,$helper,$request)use(&$seen){
    $seen=[$profile,$account,$helper,$request];
    return ['profile'=>$profile,'account_id'=>$account,'business_id'=>'654321','checked_live'=>true,
        'account_scope_verified'=>true,'transport'=>'private_http_h2','estimate_ready'=>true];
};
$result=RemaskAdvertisingHelperClient::template($input,$candidates,$reader);
check($seen[0]==='20' && $seen[1]==='123456','Client selectors leaked into generic template');
check($seen[3]['daily_budget']===500 && $seen[3]['is_cbo_enabled']===false,'Input types changed');
check(!isset($result['profile'],$result['account_id'],$result['business_id']) && $result['template_account_bound']===false,'Template retained identities');
foreach([['helper'=>'preview'],['targeting_spec'=>['custom_audiences'=>[['id'=>'123456']]]],
    ['promoted_object'=>['pixel_id'=>'123456']],['daily_budget'=>'NaN'],['daily_budget'=>'-1'],['is_cbo_enabled'=>'perhaps']] as $bad){
    try{RemaskAdvertisingHelperClient::template(array_replace($input,$bad),$candidates,$reader);throw new RuntimeException('Unsafe template accepted');}
    catch(InvalidArgumentException $error){}
}
try{RemaskAdvertisingHelperClient::template($input,$candidates,static fn()=>['checked_live'=>false]);throw new RuntimeException('Unverified source accepted');}
catch(RuntimeException $error){check($error->getMessage()==='TEMPLATE_LOOKUP_ACCESS_UNVERIFIED','Wrong source guard');}
echo "Advertising template helper contracts passed\n";
