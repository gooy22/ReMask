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
    if (!in_array($type, ['locations','interests','behaviors','locales'], true)) throw new InvalidArgumentException('UNKNOWN_TARGETING_TYPE');
    $query = trim((string)($input['q'] ?? ''));
    if (strlen($query) < 2 || strlen($query) > 200) throw new InvalidArgumentException('INVALID_TARGETING_QUERY');
    $reference = new RemaskTemplateTargetingReference(dirname(ACCOUNTSFILENAME).'/template-targeting-'.hash('sha256',ACCOUNTSFILENAME).'.json');
    $cached = $reference->get($type, $query, time());
    if ($cached !== null) { MetaEndpoint::ok($cached); exit; }
    $profiles = [];
    foreach ((array)AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize() as $row) {
        if (!$row instanceof FbAccount) continue;
        try { RemaskCookieProfile::validate((array)$row->cookies, $row->proxy, false); $cookiesReady = true; }
        catch (InvalidArgumentException $error) { $cookiesReady = false; }
        $profiles[] = ['name'=>(string)$row->name, 'proxy_configured'=>$row->proxy !== null, 'cookies_ready'=>$cookiesReady];
    }
    $until = (int)(getenv('REMASK_DIRECT_TEST_UNTIL') ?: 0);
    $preferred = $until > time() && $until <= time()+7200 ? trim((string)(getenv('REMASK_DIRECT_TEST_PROFILE') ?: '')) : '';
    $excluded = array_values(array_filter(array_map('trim', explode(',', (string)(getenv('REMASK_TEMPLATE_LOOKUP_EXCLUDED_PROFILES') ?: '')))));
    $excluded = array_unique(array_merge($excluded, $reference->blocked(time())));
    $candidates = RemaskTemplateTargetingContext::candidates($profiles, [RemaskPrivateLaunchCatalog::class,'load'], $preferred, $excluded);
    // Ignore all client profile/account selectors. Launch keeps its own explicit scope.
    $attempted = []; $lastError = new RuntimeException('TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE');
    foreach ($candidates as $context) {
        if (isset($attempted[$context['profile']])) continue;
        if (count($attempted) >= 2) break; // bounded read fallback, never mutation retry
        $attempted[$context['profile']] = true;
        try {
            $result = RemaskAdvertisingAssetBridge::read([
                'profile'=>$context['profile'], 'account_id'=>$context['account_id'], 'q'=>$query,
                'limit'=>25], $context['catalog'], $type, true);
            if (($result['checked_live'] ?? null) !== true || ($result['account_scope_verified'] ?? null) !== true)
                throw new RuntimeException('TEMPLATE_LOOKUP_ACCESS_UNVERIFIED');
            $reference->put($type, $query, $result, time());
            MetaEndpoint::ok(['data'=>$result['data'] ?? [], 'checked_live'=>true,
                'source'=>'private_meta_template_lookup', 'template_account_bound'=>false]);
            exit;
        } catch (Throwable $error) {
            $lastError = $error; $reference->block($context['profile'], time());
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
