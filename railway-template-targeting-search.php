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
    $profiles = [];
    foreach ((array)AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $row) {
        if (!$row instanceof FbAccount) continue;
        try { RemaskCookieProfile::validate((array)$row->cookies, $row->proxy, false); $cookiesReady = true; }
        catch (InvalidArgumentException $error) { $cookiesReady = false; }
        $profiles[] = ['name'=>(string)$row->name, 'proxy_configured'=>$row->proxy !== null, 'cookies_ready'=>$cookiesReady,
            'direct_ready'=>RemaskCookieProfile::directTestUntil((string)$row->name,(string)$row->userId)>time()];
    }
    $until = (int)(getenv('REMASK_DIRECT_TEST_UNTIL') ?: 0);
    $preferred = $until > time() && $until <= time()+7200 ? trim((string)(getenv('REMASK_DIRECT_TEST_PROFILE') ?: '')) : '';
    $excluded = array_values(array_filter(array_map('trim', explode(',', (string)(getenv('REMASK_TEMPLATE_LOOKUP_EXCLUDED_PROFILES') ?: '')))));
    $candidates = $reference->available(RemaskTemplateTargetingContext::candidates($profiles,
        [RemaskPrivateLaunchCatalog::class,'load'], $preferred, $excluded, true),time());
    // Ignore all client profile/account selectors. Launch keeps its own explicit scope.
    $attempted = []; $lastError = new RuntimeException('TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE');
    foreach ($candidates as $context) {
        if (isset($attempted[$context['profile']])) continue;
        if (count($attempted) >= 2) break; // bounded read fallback, never mutation retry
        $attempted[$context['profile']] = true;
        try {
            // Complete bounded reference tree is cached; browser responses stay at 100 choices.
            $result = RemaskTemplateTargetingContext::readReference($context['profile'],$type,
                ['q'=>$query,'limit'=>$query==='' ? 20000 : 50]);
            if (($result['checked_live'] ?? null) !== true || ($result['account_scope_verified'] ?? null) !== true)
                throw new RuntimeException('TEMPLATE_LOOKUP_ACCESS_UNVERIFIED');
            $reference->put($type, $query, $result, time());
            MetaEndpoint::ok(['data'=>array_slice($result['data'] ?? [],$offset,100), 'checked_live'=>true,
                'offset'=>$offset, 'next_offset'=>count($result['data'] ?? [])>$offset+100 ? $offset+100 : null,
                'truncated'=>($result['truncated'] ?? false) === true || count($result['data'] ?? [])>100,
                'catalog_count'=>(int)($result['catalog_count'] ?? count($result['data'] ?? [])),
                'source'=>'private_meta_template_lookup', 'template_account_bound'=>false]);
            exit;
        } catch (Throwable $error) {
            $lastError = $error;
            // A query/schema failure says nothing about unrelated resources.
            if (RemaskTemplateTargetingContext::sourceFailure($error))
                $reference->block($context['profile'], time(), $error->getMessage());
        }
    }
    throw $lastError;
} catch (Throwable $error) {
    http_response_code($error instanceof InvalidArgumentException ? 400 : 409);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode(['ok'=>false,'error'=>RemaskTemplateTargetingContext::failure($error,
        (string)($context['profile'] ?? ''))], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
}
