<?php
declare(strict_types=1);
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';

function smoke_kind(Throwable $e): string {
    $class = strtolower(get_class($e));
    $msg = strtolower($e->getMessage());
    if (str_contains($class, 'ratelimit') || preg_match('/rate.?limit|too many|\\b(4|17|32|613)\\b/', $msg)) return 'RATE_LIMIT';
    if (preg_match('/\\b407\\b|proxy authentication|proxy auth/', $msg)) return 'PROXY_AUTH';
    if (str_contains($class, 'transport') || preg_match('/curl|transport error|connection timed out|could not resolve|connection refused|ssl connect/', $msg)) return 'TRANSPORT';
    if (preg_match('/oauth|access token|token.*(invalid|expired)|session.*expired|\\b190\\b/', $msg)) return 'TOKEN_INVALID';
    if (preg_match('/ads_management|ads_read|business_management|permission|not authorized|\\b(10|200)\\b/', $msg)) return 'PERMISSION';
    return 'META_API';
}
function smoke_safe_message(Throwable $e): string {
    $msg = $e->getMessage();
    $msg = preg_replace('/access_token=[^&\\s]+/i', 'access_token=[redacted]', $msg) ?? $msg;
    $msg = preg_replace('/\\bEAA[A-Za-z0-9_-]{12,}\\b/', '[redacted-token]', $msg) ?? $msg;
    $msg = preg_replace('/\\b\\d{12,}\\b/', '[redacted-id]', $msg) ?? $msg;
    return mb_substr($msg, 0, 320);
}
function smoke_stage(string $profileHash, string $stage, callable $fn): array {
    try {
        $value = $fn();
        $count = null;
        if (is_array($value) && is_array($value['data'] ?? null)) $count = count($value['data']);
        smoke_log(['phase'=>'stage','profile_hash'=>$profileHash,'stage'=>$stage,'ok'=>true,'count'=>$count]);
        return ['ok'=>true,'value'=>$value];
    } catch (Throwable $e) {
        smoke_log([
            'phase'=>'stage',
            'profile_hash'=>$profileHash,
            'stage'=>$stage,
            'ok'=>false,
            'error_kind'=>smoke_kind($e),
            'error_class'=>get_class($e),
            'error_code'=>$e->getCode(),
            'message'=>smoke_safe_message($e),
        ]);
        return ['ok'=>false,'error'=>$e];
    }
}

function smoke_log(array $data): void {
    fwrite(STDOUT, '[sync-smoke] ' . json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . PHP_EOL);
}

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts = $store->deserialize();
    smoke_log(['phase'=>'start','profiles_saved'=>count($accounts),'max_profiles'=>8]);
    $tested=0; $success=0;
    foreach ($accounts as $account) {
        if (!$account instanceof FbAccount) continue;
        if ($tested >= 8 || $success >= 2) break;
        $name = trim((string)$account->name);
        if ($name === '') continue;
        $tested++;
        $profileHash = substr(hash('sha256',$name),0,12);
        try {
            $service = MetaEndpoint::serviceForAccountName($name);
            $identityStage = smoke_stage($profileHash, 'identity', static fn() => $service->getIdentity());
            $permissionsStage = smoke_stage($profileHash, 'permissions', static fn() => $service->getPermissions());
            $accountsStage = smoke_stage($profileHash, 'ad_accounts', static fn() => $service->listAdAccounts());
            if (!$identityStage['ok'] || !$permissionsStage['ok'] || !$accountsStage['ok']) {
                throw ($identityStage['error'] ?? $permissionsStage['error'] ?? $accountsStage['error']);
            }
            $preflight = MetaEndpoint::cachedPreflight($name, true);
            $direct = is_array($preflight['ad_accounts']['data'] ?? null) ? $preflight['ad_accounts']['data'] : [];
            $businessRows = [];
            $bmRkIds = [];
            $warnings = [];
            try {
                $businesses = MetaEndpoint::cachedAsset($name,'businesses','',true);
                $businessRows = is_array($businesses['data'] ?? null) ? $businesses['data'] : [];
                foreach (array_slice($businessRows,0,10) as $business) {
                    if (!is_array($business)) continue;
                    $bid = trim((string)($business['id'] ?? ''));
                    if ($bid === '') continue;
                    try {
                        $mapped = MetaEndpoint::cachedAsset($name,'business_ad_accounts',$bid,true);
                        foreach ((array)($mapped['data'] ?? []) as $rk) {
                            if (!is_array($rk)) continue;
                            $rid=(string)($rk['id'] ?? '');
                            if ($rid!=='') $bmRkIds[$rid]=true;
                        }
                        foreach ((array)($mapped['_edge_warnings'] ?? []) as $w) {
                            if (is_array($w)) $warnings[]=(string)($w['kind'] ?? 'edge_warning');
                        }
                    } catch (Throwable $e) {
                        $warnings[]='BM_RK_' . smoke_kind($e);
                    }
                }
            } catch (Throwable $e) {
                $warnings[]='BM_LIST_' . smoke_kind($e);
            }

            $fundingLoaded=0;
            foreach($direct as $rk){
                if(is_array($rk) && (($rk['_funding_metadata_loaded'] ?? false) === true)) $fundingLoaded++;
            }
            $success++;
            smoke_log([
                'phase'=>'profile',
                'profile_hash'=>$profileHash,
                'ok'=>true,
                'ads_management'=>(bool)($preflight['ads_management_granted'] ?? false),
                'business_management'=>(bool)($preflight['business_management_granted'] ?? false),
                'direct_rk'=>count($direct),
                'bm'=>count($businessRows),
                'bm_mapped_rk'=>count($bmRkIds),
                'funding_loaded_rk'=>$fundingLoaded,
                'warnings'=>array_values(array_unique($warnings)),
            ]);
        } catch (Throwable $e) {
            smoke_log([
                'phase'=>'profile',
                'profile_hash'=>$profileHash,
                'ok'=>false,
                'error_kind'=>smoke_kind($e),
                'error_class'=>get_class($e),
                'error_code'=>$e->getCode(),
                'message'=>smoke_safe_message($e),
            ]);
        }
    }
    smoke_log(['phase'=>'done','tested'=>$tested,'successful'=>$success]);
} catch (Throwable $e) {
    smoke_log(['phase'=>'fatal','ok'=>false,'error_kind'=>smoke_kind($e),'error_class'=>get_class($e)]);
}
