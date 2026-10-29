<?php
declare(strict_types=1);
require_once __DIR__.'/../settings.php';
require_once __DIR__.'/../checkpassword.php';
require_once __DIR__.'/../classes/MetaEndpoint.php';
require_once __DIR__.'/../classes/AccountStoreFactory.php';
require_once __DIR__.'/../classes/FbAccount.php';
require_once __DIR__.'/../classes/RemaskCookieProfile.php';
require_once __DIR__.'/../classes/RemaskPrivateLaunchCatalog.php';
require_once __DIR__.'/../classes/RemaskAdvertisingAssetBridge.php';
require_once __DIR__.'/../classes/RemaskTemplateTargetingContext.php';
try {
    $input = MetaEndpoint::input();
    $type = strtolower(trim((string)($input['type'] ?? '')));
    $type = ['language'=>'locales','languages'=>'locales','locale'=>'locales'][$type] ?? $type;
    $editor=json_decode((string)file_get_contents(__DIR__.'/../classes/meta-editor-modules.json'),true,512,JSON_THROW_ON_ERROR);
    $types=array_keys($editor['targeting_types'] ?? []);
    if (!in_array($type, array_merge(['locations','interests','behaviors','locales'],$types), true)) throw new InvalidArgumentException('UNKNOWN_TARGETING_TYPE');
    $query = trim((string)($input['q'] ?? ''));
    if (strlen($query) > 200) throw new InvalidArgumentException('INVALID_TARGETING_QUERY');
    // A page is a view of a retained, account-independent reference tree;
    // it never authorizes another profile's advertising mutations.
    $rawOffset=(string)($input['offset'] ?? '0');
    if (!preg_match('/^(0|[1-9][0-9]{0,4})$/D',$rawOffset) || (int)$rawOffset>19900 || (int)$rawOffset%100!==0)
        throw new InvalidArgumentException('INVALID_TARGETING_OFFSET');
    $offset=(int)$rawOffset;
    $moduleOnly = (string)($input['module_only'] ?? '') === '1';
    if (array_key_exists('module_only',$input) && !$moduleOnly)
        throw new InvalidArgumentException('INVALID_TARGETING_MODE');
    if ($moduleOnly && !in_array($type,['locations','locales'],true))
        throw new InvalidArgumentException('INVALID_TARGETING_MODE');
    $cacheOnly = (string)($input['cache_only'] ?? '') === '1';
    if (array_key_exists('cache_only',$input) && !$cacheOnly)
        throw new InvalidArgumentException('INVALID_TARGETING_MODE');
    $module = RemaskModuleTargetingReference::search($type,$query,
        __DIR__.'/../classes/meta-targeting-reference.json',$offset);
    if ($module !== null) { MetaEndpoint::ok($module); exit; }
    if ($moduleOnly) {
        MetaEndpoint::ok(['data'=>[],'checked_live'=>false,'availability'=>'NOT_LOADED',
            'source'=>'private_meta_module_reference','offset'=>$offset,'next_offset'=>null,
            'template_account_bound'=>false]);
        exit;
    }
    $reference = new RemaskTemplateTargetingReference(dirname(ACCOUNTSFILENAME).'/template-targeting-'.hash('sha256',ACCOUNTSFILENAME).'.json');
    $cached = $reference->get($type, $query, time(), $offset);
    if ($cached !== null) { MetaEndpoint::ok($cached); exit; }
    if (($cached=$reference->search($type,$query,time(),$offset)) !== null) {
        MetaEndpoint::ok($cached); exit;
    }
    if ($cacheOnly) {
        MetaEndpoint::ok(['data'=>[],'checked_live'=>false,'account_scope_verified'=>false,
            'availability'=>'NOT_LOADED','source'=>'private_meta_template_reference','offset'=>$offset,
            'next_offset'=>null,'template_account_bound'=>false]);
        exit;
    }
    $selectedProfile=trim((string)($input['profile'] ?? ''));
    $selectedAccount=preg_replace('/^act_/i','',trim((string)($input['account_id'] ?? '')));
    // A shared cache miss may use only the exact Launch selection. Unscoped
    // editors and malformed selectors never probe another saved profile.
    if ($selectedProfile === '' || strlen($selectedProfile)>160
        || !preg_match('/^[0-9]{5,30}$/D',$selectedAccount)) {
        MetaEndpoint::ok(['data'=>[],'checked_live'=>false,'account_scope_verified'=>false,
            'availability'=>'NOT_LOADED','source'=>'private_meta_template_reference','offset'=>$offset,
            'next_offset'=>null,'template_account_bound'=>false]);
        exit;
    }
    $profileRow=null;
    foreach ((array)AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $row) {
        if (!$row instanceof FbAccount || (string)$row->name !== $selectedProfile) continue;
        try { RemaskCookieProfile::validate((array)$row->cookies, $row->proxy, false); $cookiesReady = true; }
        catch (InvalidArgumentException $error) { $cookiesReady = false; }
        $profileRow=['name'=>(string)$row->name,'proxy_configured'=>$row->proxy !== null,
            'cookies_ready'=>$cookiesReady,
            'direct_ready'=>RemaskCookieProfile::directTestUntil((string)$row->name,(string)$row->userId)>time()];
        break;
    }
    if (!is_array($profileRow)) throw new RuntimeException('TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE');
    $excluded=array_values(array_filter(array_map('trim',
        explode(',',(string)(getenv('REMASK_TEMPLATE_LOOKUP_EXCLUDED_PROFILES') ?: '')))));
    $candidate=RemaskTemplateTargetingContext::exactCandidate($profileRow,
        [RemaskPrivateLaunchCatalog::class,'load'],$selectedAccount,$excluded);
    $context=$reference->available([$candidate],time())[0];
    try {
        // This single bounded read is scoped to the selected Launch profile/RK.
        $result=RemaskTemplateTargetingContext::readReference($selectedProfile,$type,
            ['q'=>$query,'limit'=>$query==='' ? 20000 : 50]);
        $result=RemaskTemplateTargetingContext::referenceResponse($selectedProfile,$result);
        $returnedAccount=preg_replace('/^act_/i','',trim((string)($result['account_id'] ?? '')));
        if ($returnedAccount!==$selectedAccount)
            throw new RuntimeException('TEMPLATE_LOOKUP_ACCOUNT_SCOPE_MISMATCH');
        $cacheMeta=$result['_cache'] ?? [];
        if (($cacheMeta['hit'] ?? false)===true && ($cacheMeta['cached_only'] ?? false)===true
            && in_array($result['source'] ?? '',[
                'private_meta_targeting_option_index','ads_manager_private_http','private_meta_targetingbrowse'
            ],true) && is_array($result['data'] ?? null) && count($result['data'])>0) {
            MetaEndpoint::ok(['data'=>$result['data'], 'checked_live'=>false,
                'account_scope_verified'=>false, 'profile'=>$selectedProfile,
                'account_id'=>$selectedAccount, 'business_id'=>(string)($result['business_id'] ?? ''),
                'resource'=>$type, 'transport'=>'private_http_h2', 'source_context_discovered'=>true,
                '_cache'=>['hit'=>true, 'cached_only'=>true, 'stale'=>($cacheMeta['stale'] ?? false)===true],
                'offset'=>$offset, 'next_offset'=>$result['next_offset'] ?? null,
                'truncated'=>($result['truncated'] ?? false) === true,
                'catalog_count'=>(int)($result['catalog_count'] ?? count($result['data'])),
                'source'=>$result['source'], 'template_account_bound'=>false,
                'reference_checked_at'=>(int)($result['checked_at'] ?? 0),
                'reference_stale'=>($cacheMeta['stale'] ?? false)===true]);
            exit;
        }
        $reference->put($type, $query, $result, time());
        MetaEndpoint::ok(['data'=>array_slice($result['data'] ?? [],$offset,100), 'checked_live'=>true,
            'account_scope_verified'=>true, 'profile'=>$selectedProfile, 'account_id'=>$selectedAccount,
            'business_id'=>(string)($result['business_id'] ?? ''), 'resource'=>$type,
            'transport'=>'private_http_h2', 'source_context_discovered'=>true,
            'offset'=>$offset, 'next_offset'=>count($result['data'] ?? [])>$offset+100 ? $offset+100 : null,
            'truncated'=>($result['truncated'] ?? false) === true || count($result['data'] ?? [])>100,
            'catalog_count'=>(int)($result['catalog_count'] ?? count($result['data'] ?? [])),
            'source'=>'private_meta_template_lookup', 'template_account_bound'=>false]);
        exit;
    } catch (Throwable $error) {
        if (RemaskTemplateTargetingContext::sourceFailure($error))
            $reference->block($selectedProfile,time(),$error->getMessage());
        throw $error;
    }
} catch (Throwable $error) {
    http_response_code($error instanceof InvalidArgumentException ? 400 : 409);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode(['ok'=>false,'error'=>RemaskTemplateTargetingContext::failure($error,
        (string)($context['profile'] ?? ''))], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}
