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

function smoke_service_variant(string $name, bool $useProxy, bool $useSession): MetaAdsService {
    $base = MetaEndpoint::serviceForAccountName($name);
    $serviceRef = new ReflectionObject($base);
    $clientProp = $serviceRef->getProperty('client');
    $clientProp->setAccessible(true);
    $client = clone $clientProp->getValue($base);
    if (!$useSession && method_exists($client, 'setSessionCookies')) {
        $client->setSessionCookies('');
    }
    if (!$useProxy) {
        $clientRef = new ReflectionObject($client);
        $proxyProp = $clientRef->getProperty('proxy');
        $proxyProp->setAccessible(true);
        $proxyProp->setValue($client, null);
    }
    return new MetaAdsService($client);
}
function smoke_restore_empty_accounts(object $store): array {
    $path=(string)ACCOUNTSFILENAME;
    $current=$store->deserialize();
    $backups=glob($path.'.bak.*')?:[];
    usort($backups,static fn($a,$b)=>(@filemtime($b)?:0)<=> (@filemtime($a)?:0));
    smoke_log([
        'phase'=>'storage',
        'accounts_path_hash'=>substr(hash('sha256',$path),0,12),
        'accounts_exists'=>is_file($path),
        'accounts_size'=>is_file($path)?(int)filesize($path):0,
        'profiles_deserialized'=>count($current),
        'backup_files'=>count($backups),
    ]);
    if(count($current)>0)return $current;
    foreach(array_slice($backups,0,200) as $i=>$file){
        try{
            $candidateStore=AccountStoreFactory::create($file);
            $candidate=$candidateStore->deserialize();
            if(count($candidate)===0)continue;
            @copy($path,$path.'.bak.before-empty-recovery.'.gmdate('YmdHis'));
            if(!@copy($file,$path))continue;
            clearstatcache(true,$path);
            $restored=$store->deserialize();
            smoke_log([
                'phase'=>'storage_restore',
                'restored'=>count($restored)>0,
                'backup_rank'=>$i+1,
                'profiles_restored'=>count($restored),
                'accounts_size'=>(int)(@filesize($path)?:0),
            ]);
            return $restored;
        }catch(Throwable $e){
            continue;
        }
    }
    smoke_log(['phase'=>'storage_restore','restored'=>false,'profiles_restored'=>0]);
    return [];
}

function smoke_graph_call(string $token, string $path, array $params = []): array {
    $url='https://graph.facebook.com/v26.0/'.ltrim($path,'/');
    if($params!==[])$url.='?'.http_build_query($params);
    $ch=curl_init($url);
    curl_setopt_array($ch,[
        CURLOPT_RETURNTRANSFER=>true,
        CURLOPT_FOLLOWLOCATION=>false,
        CURLOPT_CONNECTTIMEOUT=>12,
        CURLOPT_TIMEOUT=>35,
        CURLOPT_SSL_VERIFYPEER=>true,
        CURLOPT_SSL_VERIFYHOST=>2,
        CURLOPT_HTTPHEADER=>['Accept: application/json','Authorization: Bearer '.$token],
        CURLOPT_USERAGENT=>'ReMask-HistoryCheck/1.0',
    ]);
    $raw=curl_exec($ch);
    $errno=curl_errno($ch);
    $http=(int)curl_getinfo($ch,CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    $decoded=is_string($raw)?json_decode($raw,true):null;
    $error=is_array($decoded['error']??null)?$decoded['error']:[];
    return ['ok'=>is_array($decoded)&&$error===[]&&$http>=200&&$http<300,'http'=>$http,'errno'=>$errno,'data'=>$decoded,'error'=>$error];
}
function smoke_validate_candidate(string $token): array {
    $me=smoke_graph_call($token,'me',['fields'=>'id,name']);
    if(!$me['ok'])return ['ok'=>false,'stage'=>'identity','result'=>$me];
    $perms=smoke_graph_call($token,'me/permissions',['limit'=>200]);
    if(!$perms['ok'])return ['ok'=>false,'stage'=>'permissions','result'=>$perms];
    $ads=false;
    foreach((array)($perms['data']['data']??[]) as $row){
        if(is_array($row)&&($row['permission']??'')==='ads_management'&&($row['status']??'')==='granted'){$ads=true;break;}
    }
    if(!$ads)return ['ok'=>false,'stage'=>'ads_management','result'=>$perms];
    $rk=smoke_graph_call($token,'me/adaccounts',['fields'=>'id','limit'=>1]);
    if(!$rk['ok'])return ['ok'=>false,'stage'=>'ad_accounts','result'=>$rk];
    return ['ok'=>true,'stage'=>'complete','result'=>$rk];
}
function smoke_collect_tokens(mixed $node, string $profileName, array &$tokens, ?string $inheritedName = null, int $depth = 0): void {
    if ($depth > 12 || !is_array($node)) return;
    $localName = $inheritedName;
    foreach (['name','profile_name','profile','label','fb_id','profile_id','account_id'] as $key) {
        if (isset($node[$key]) && is_scalar($node[$key]) && trim((string)$node[$key]) !== '') {
            $localName = trim((string)$node[$key]);
            break;
        }
    }
    if ($localName === $profileName) {
        foreach (['token','access_token','accessToken','fb_token','meta_token'] as $key) {
            if (isset($node[$key]) && is_scalar($node[$key])) {
                $token = trim((string)$node[$key]);
                if ($token !== '') $tokens[$token] = true;
            }
        }
    }
    foreach ($node as $key => $value) {
        if (!is_array($value)) continue;
        $childName = ((string)$key === $profileName) ? $profileName : $localName;
        smoke_collect_tokens($value, $profileName, $tokens, $childName, $depth + 1);
    }
}
function smoke_try_restore_history(FbAccount $current, object $store, string $profileHash): bool {
    $currentToken=trim((string)$current->token);
    $accountsPath=(string)ACCOUNTSFILENAME;
    $files=glob($accountsPath.'.bak.*')?:[];
    usort($files,static fn($a,$b)=>(@filemtime($b)?:0)<=> (@filemtime($a)?:0));
    $seen=[hash('sha256',$currentToken)=>true];
    $tested=0;
    $rawCandidates=0;
    foreach(array_slice($files,0,200) as $file){
        if($tested>=50)break;
        $raw=@file_get_contents($file);
        if($raw===false||trim($raw)==='')continue;
        $json=json_decode($raw,true);
        if(!is_array($json))continue;
        $tokens=[];
        smoke_collect_tokens($json,(string)$current->name,$tokens);
        foreach(array_keys($tokens) as $token){
            $rawCandidates++;
            $hash=hash('sha256',$token);
            if(isset($seen[$hash]))continue;
            $seen[$hash]=true;
            $tested++;
            $validation=smoke_validate_candidate($token);
            $err=(array)($validation['result']['error']??[]);
            smoke_log([
                'phase'=>'historical_token',
                'profile_hash'=>$profileHash,
                'candidate_hash'=>substr($hash,0,12),
                'token_length'=>strlen($token),
                'ok'=>(bool)$validation['ok'],
                'stage'=>$validation['stage'],
                'http'=>(int)($validation['result']['http']??0),
                'graph_type'=>(string)($err['type']??''),
                'graph_code'=>(int)($err['code']??0),
                'graph_subcode'=>(int)($err['error_subcode']??0),
            ]);
            if(!$validation['ok'])continue;

            @copy($accountsPath,$accountsPath.'.bak.before-token-restore.'.gmdate('YmdHis'));
            $cookieJson=json_encode(array_values((array)$current->cookies),JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_THROW_ON_ERROR);
            $restored=new FbAccount((string)$current->name,$token,$cookieJson,$current->dtsg,$current->proxy);
            $store->addOrUpdateAccount($restored);
            smoke_log([
                'phase'=>'historical_restore',
                'profile_hash'=>$profileHash,
                'restored'=>true,
                'candidate_hash'=>substr($hash,0,12),
                'tested_candidates'=>$tested,
                'raw_candidates'=>$rawCandidates,
            ]);
            return true;
        }
    }
    smoke_log([
        'phase'=>'historical_restore',
        'profile_hash'=>$profileHash,
        'restored'=>false,
        'tested_candidates'=>$tested,
        'raw_candidates'=>$rawCandidates,
    ]);
    return false;
}

function smoke_raw_graph(string $token, string $profileHash): void {
    $tests = [
        ['label'=>'v26_bearer','url'=>'https://graph.facebook.com/v26.0/me?fields=id%2Cname','bearer'=>true],
        ['label'=>'v26_query','url'=>'https://graph.facebook.com/v26.0/me?fields=id%2Cname','bearer'=>false],
        ['label'=>'v25_bearer','url'=>'https://graph.facebook.com/v25.0/me?fields=id%2Cname','bearer'=>true],
        ['label'=>'unversioned_bearer','url'=>'https://graph.facebook.com/me?fields=id%2Cname','bearer'=>true],
    ];
    foreach ($tests as $test) {
        $url = $test['url'];
        $headers = ['Accept: application/json'];
        if ($test['bearer']) {
            $headers[] = 'Authorization: Bearer ' . $token;
        } else {
            $url .= '&access_token=' . rawurlencode($token);
        }
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_RETURNTRANSFER=>true,
            CURLOPT_FOLLOWLOCATION=>false,
            CURLOPT_CONNECTTIMEOUT=>12,
            CURLOPT_TIMEOUT=>35,
            CURLOPT_SSL_VERIFYPEER=>true,
            CURLOPT_SSL_VERIFYHOST=>2,
            CURLOPT_HTTPHEADER=>$headers,
            CURLOPT_USERAGENT=>'ReMask-SyncSmoke/1.0',
        ]);
        $raw = curl_exec($ch);
        $errno = curl_errno($ch);
        $http = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        curl_close($ch);
        $decoded = is_string($raw) ? json_decode($raw,true) : null;
        $error = is_array($decoded['error'] ?? null) ? $decoded['error'] : [];
        $ok = is_array($decoded) && $error === [] && $http >= 200 && $http < 300;
        smoke_log([
            'phase'=>'raw_graph',
            'profile_hash'=>$profileHash,
            'variant'=>$test['label'],
            'ok'=>$ok,
            'http'=>$http,
            'curl_errno'=>$errno,
            'graph_type'=>(string)($error['type'] ?? ''),
            'graph_code'=>(int)($error['code'] ?? 0),
            'graph_subcode'=>(int)($error['error_subcode'] ?? 0),
            'message'=>isset($error['message']) ? mb_substr((string)$error['message'],0,240) : '',
        ]);
    }
}

function smoke_transport_matrix(string $name, string $profileHash, FbAccount $account): void {
    $variants = [
        'saved_context' => [true, true],
        'no_session' => [true, false],
        'no_proxy' => [false, true],
        'token_only' => [false, false],
    ];
    foreach ($variants as $label => [$useProxy,$useSession]) {
        if ($label === 'no_session' && !$account->isLegacyReady()) continue;
        if ($label === 'no_proxy' && $account->proxy === null) continue;
        try {
            $service = smoke_service_variant($name, $useProxy, $useSession);
            $identity = $service->getIdentity();
            smoke_log([
                'phase'=>'transport',
                'profile_hash'=>$profileHash,
                'variant'=>$label,
                'ok'=>true,
                'proxy_enabled'=>$useProxy && $account->proxy !== null,
                'session_enabled'=>$useSession && $account->isLegacyReady(),
                'identity_present'=>trim((string)($identity['id'] ?? '')) !== '',
            ]);
        } catch (Throwable $e) {
            smoke_log([
                'phase'=>'transport',
                'profile_hash'=>$profileHash,
                'variant'=>$label,
                'ok'=>false,
                'proxy_enabled'=>$useProxy && $account->proxy !== null,
                'session_enabled'=>$useSession && $account->isLegacyReady(),
                'error_kind'=>smoke_kind($e),
                'error_class'=>get_class($e),
                'error_code'=>$e->getCode(),
                'message'=>smoke_safe_message($e),
            ]);
        }
    }
}

function smoke_log(array $data): void {
    fwrite(STDOUT, '[sync-smoke] ' . json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) . PHP_EOL);
}

try {
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $accounts = smoke_restore_empty_accounts($store);
    smoke_log(['phase'=>'start','profiles_saved'=>count($accounts),'max_profiles'=>8]);
    $tested=0; $success=0;
    foreach ($accounts as $account) {
        if (!$account instanceof FbAccount) continue;
        if ($tested >= 8 || $success >= 2) break;
        $name = trim((string)$account->name);
        if ($name === '') continue;
        $tested++;
        $profileHash = substr(hash('sha256',$name),0,12);
        smoke_log([
            'phase'=>'credential_shape',
            'profile_hash'=>$profileHash,
            'token_length'=>strlen(trim((string)$account->token)),
            'proxy_configured'=>$account->proxy !== null,
            'legacy_ready'=>$account->isLegacyReady(),
            'cookie_count'=>count((array)$account->cookies),
        ]);
        smoke_raw_graph(trim((string)$account->token), $profileHash);
        $currentValidation=smoke_validate_candidate(trim((string)$account->token));
        if(!$currentValidation['ok']){
            $restored=smoke_try_restore_history($account,$store,$profileHash);
            if($restored){
                $fresh=$store->getAccountByName($name);
                if($fresh instanceof FbAccount)$account=$fresh;
            }
        }
        smoke_transport_matrix($name, $profileHash, $account);
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
