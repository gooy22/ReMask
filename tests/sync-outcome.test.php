<?php
declare(strict_types=1);
$source = file_get_contents(__DIR__ . '/../railway-workspace-sync-fix-overlay.php');
preg_match('/\/\/ REMASK_HONEST_SYNC_OUTCOME_V1\n(.*?)\n\/\/ REMASK_PERSISTENT_BM_RK_BINDING_V1/s', $source, $match);
if (!isset($match[1])) throw new RuntimeException('Sync outcome implementation missing');
eval($match[1]);
function check(bool $value): void { if (!$value) throw new RuntimeException('Sync outcome assertion failed'); }
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
