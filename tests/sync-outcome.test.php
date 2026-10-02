<?php
declare(strict_types=1);
$source = file_get_contents(__DIR__ . '/../railway-workspace-sync-fix-overlay.php');
preg_match('/\/\/ REMASK_HONEST_SYNC_OUTCOME_V1\n(.*?)\n\/\/ REMASK_PERSISTENT_BM_RK_BINDING_V1/s', $source, $match);
if (!isset($match[1])) throw new RuntimeException('Sync outcome implementation missing');
eval($match[1]);
function check(bool $value): void { if (!$value) throw new RuntimeException('Sync outcome assertion failed'); }
$business = ['id'=>'987654321','accounts'=>[['id'=>'act_987654321'],['id'=>'123456789','business_id'=>'987654321','_sync_preserved'=>true]]];
$snapshot = hierarchy_asset_types_snapshot(['businesses'=>[$business], 'pages'=>[['id'=>'555555555']],
    'ad_accounts'=>[['id'=>'987654321','business_id'=>'987654321'],['id'=>'act_555555555','business_id'=>'987654321'],['id'=>'123456789','business_id'=>'987654321','_sync_preserved'=>true]],
    'profile'=>['name'=>'Fixture'], 'profiles'=>[['name'=>'Fixture','rk_count'=>3],['name'=>'Other','rk_count'=>9]]]);
check(count($snapshot['ad_accounts'])===1 && $snapshot['ad_accounts'][0]['id']==='123456789');
check($snapshot['ad_accounts'][0]['_sync_preserved']===true);
check($snapshot['businesses'][0]['ad_account_count']===1 && count($snapshot['businesses'][0]['accounts'])===1);
check($snapshot['profile']['rk_count']===1 && $snapshot['profiles'][0]['rk_count']===1 && $snapshot['profiles'][1]['rk_count']===9);
check(hierarchy_asset_types_snapshot($snapshot)===$snapshot);
foreach (['CHECKPOINT_REQUIRED','TWO_FACTOR_REQUIRED','SESSION_EXPIRED'] as $code) {
    // Authentication failure overrides even an inconsistent ready flag.
    $result = hierarchy_private_sync_outcome(['live_ready'=>true,'businesses'=>[['auth_blocked'=>true,'browser_error_code'=>$code]]]);
    check(!$result['complete'] && $result['kind'] === $code);
}
$result = hierarchy_private_sync_outcome(['live_ready'=>false,'businesses'=>[]]);
check(!$result['complete'] && $result['kind'] === 'PRIVATE_INCONCLUSIVE');
$result = hierarchy_private_sync_outcome(['live_ready'=>true,'pages_live_verified'=>false,'businesses'=>[]]);
check($result['complete'] && $result['error'] === '');
check(str_contains($source, "'status' => \$syncComplete ? 'SUCCESS' : 'ERROR'"));
echo "Authentication, inconclusive and partial Page sync outcomes passed.\n";
