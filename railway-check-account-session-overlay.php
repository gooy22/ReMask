<?php
/**
 * Session-aware checkAccount endpoint.
 * Browser-session Meta tokens may need the same saved Facebook cookies that the
 * canonical MetaApiClient receives after import. Never echoes secret values.
 */
$target = '/var/www/html/ajax/checkAccount.php';
$php = <<<'PHP_CODE'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/ResponseFormatter.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/MetaApiClient.php';

function rmx_check_cookie_jar(mixed $raw): array
{
    if (is_array($raw)) $decoded = $raw;
    else {
        $text = trim((string)$raw);
        if ($text === '') return [];
        $decoded = json_decode($text, true);
        if (!is_array($decoded)) throw new InvalidArgumentException('Cookies must be valid JSON.');
    }
    $rows = array_is_list($decoded) ? $decoded : array_values($decoded);
    $out = [];
    foreach ($rows as $cookie) {
        if (!is_array($cookie)) continue;
        $name = trim((string)($cookie['name'] ?? ''));
        $value = (string)($cookie['value'] ?? '');
        if ($name === '' || $value === '') continue;
        $out[] = ['name'=>$name, 'value'=>$value];
    }
    return $out;
}

function rmx_check_cookie_header(array $cookies): string
{
    $parts = [];
    foreach ($cookies as $cookie) {
        $name = (string)($cookie['name'] ?? '');
        $value = (string)($cookie['value'] ?? '');
        if ($name === '' || $value === '') continue;
        $parts[] = $name . '=' . $value;
    }
    return implode('; ', $parts);
}

function rmx_check_saved_account(string $token): ?FbAccount
{
    try {
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        foreach ((array)$store->deserialize() as $account) {
            if (!$account instanceof FbAccount) continue;
            $saved = trim((string)$account->token);
            if ($saved !== '' && hash_equals(hash('sha256', $saved), hash('sha256', $token))) return $account;
        }
    } catch (Throwable) {
    }
    return null;
}

function rmx_check_list_all(MetaApiClient $client, string $path, array $params): array
{
    $items=[];
    $after='';
    $seen=[];
    do {
        $pageParams=$params;
        $pageParams['limit']=500;
        if($after!=='')$pageParams['after']=$after;
        $page=$client->get($path,$pageParams);
        $data=is_array($page['data']??null)?$page['data']:[];
        foreach($data as $item) if(is_array($item)) $items[]=$item;

        $next=(string)($page['paging']['cursors']['after']??'');
        $hasNext=!empty($page['paging']['next']) && $next!=='' && $data!==[] && !isset($seen[$next]);
        if($hasNext)$seen[$next]=true;
        $after=$hasNext?$next:'';
    } while($after!=='');
    return ['data'=>$items];
}

try {
    $token = trim((string)($_POST['token'] ?? $_POST['access_token'] ?? ''));
    if ($token === '') throw new InvalidArgumentException('Access token is required.');

    $savedAccount = rmx_check_saved_account($token);

    $proxyRaw = trim((string)($_POST['proxy'] ?? ''));
    $proxy = $proxyRaw !== ''
        ? RemaskProxy::fromSemicolonString($proxyRaw)
        : ($savedAccount?->proxy ?? null);

    $cookies = rmx_check_cookie_jar($_POST['cookies'] ?? $_POST['cookie'] ?? '');
    $usedSavedSession = false;
    if ($cookies === [] && $savedAccount instanceof FbAccount) {
        $cookies = rmx_check_cookie_jar((array)$savedAccount->cookies);
        $usedSavedSession = $cookies !== [];
    }
    $cookieHeader = rmx_check_cookie_header($cookies);

    // Canonical Meta transport: same client class used by Pages/BM/RK/Launch.
    $client = new MetaApiClient($token, $proxy, null, 35);
    if ($cookieHeader !== '') $client->setSessionCookies($cookieHeader);

    $me = $client->get('me', ['fields'=>'id,name']);
    $permissions = $client->get('me/permissions', ['limit'=>200]);

    $granted=[];
    foreach ((array)($permissions['data'] ?? []) as $permission) {
        if (!is_array($permission)) continue;
        if (($permission['status'] ?? '') !== 'granted') continue;
        $name=trim((string)($permission['permission']??''));
        if($name!=='')$granted[$name]=true;
    }
    if (empty($granted['ads_management'])) {
        throw new RuntimeException('ads_management permission is not granted for this token.');
    }

    $adAccounts = rmx_check_list_all($client,'me/adaccounts',[
        'fields'=>'id,name,account_status,currency,disable_reason',
    ]);

    ResponseFormatter::Respond(['res'=>json_encode([
        'ok'=>true,
        'profile'=>[
            'id'=>(string)($me['id']??''),
            'name'=>(string)($me['name']??''),
        ],
        'permissions'=>array_keys($granted),
        'ads_management_granted'=>true,
        'business_management_granted'=>!empty($granted['business_management']),
        'ad_accounts_count'=>count((array)($adAccounts['data']??[])),
        'proxy_used'=>$proxy!==null,
        'session_used'=>$cookieHeader!=='',
        'saved_session_used'=>$usedSavedSession,
        'cookie_count'=>count($cookies),
        'transport'=>[
            'client'=>'MetaApiClient',
            'network_identity'=>'profile_bound',
            'proxy_configured'=>$proxy!==null,
            'direct_fallback'=>false,
        ],
        'message'=>'Meta API profile is valid.',
    ], JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_THROW_ON_ERROR)]);
} catch (Throwable $e) {
    http_response_code(200);
    $detail=['message'=>$e->getMessage(),'type'=>get_class($e)];
    if(method_exists($e,'toArray')){
        try{$detail=array_replace($detail,(array)$e->toArray());}catch(Throwable){}
    }
    ResponseFormatter::Respond([
        'error'=>$e->getMessage(),
        'meta_error'=>$detail,
    ]);
}
PHP_CODE;
file_put_contents($target, $php);
fwrite(STDERR, "[check-account-session] checkAccount uses explicit or saved token session/proxy context\n");
