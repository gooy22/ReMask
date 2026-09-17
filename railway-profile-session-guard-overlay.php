<?php
/**
 * Session-safe canonical Workspace profile manager.
 * - Accepts flat and nested profile/account/data/payload requests.
 * - Never clears existing cookies/dtsg merely because a save request omitted them.
 * - Session removal requires explicit clear_session=true.
 * - Uses the canonical AccountStoreFactory/FbAccount serializer.
 */
$target = '/var/www/html/ajax/metaProfileManager.php';
$php = <<<'PHP_CODE'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

ob_start();
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function rmx_pm_is_list(array $a): bool {
    return function_exists('array_is_list') ? array_is_list($a) : array_keys($a) === range(0, count($a) - 1);
}
function rmx_pm_decode_nested(mixed $value): mixed {
    if (!is_string($value)) return $value;
    $trim = trim($value);
    if ($trim === '' || (!str_starts_with($trim, '{') && !str_starts_with($trim, '['))) return $value;
    $decoded = json_decode($trim, true);
    return is_array($decoded) ? $decoded : $value;
}
function rmx_pm_input(): array {
    $input = $_POST;
    $raw = (string)file_get_contents('php://input');
    if ($raw !== '') {
        $decoded = json_decode($raw, true);
        if (is_array($decoded)) $input = array_replace_recursive($input, $decoded);
        elseif (!$input) {
            parse_str($raw, $parsed);
            if (is_array($parsed)) $input = $parsed;
        }
    }
    foreach (['profile','account','data','payload'] as $key) {
        if (array_key_exists($key, $input)) $input[$key] = rmx_pm_decode_nested($input[$key]);
    }
    return $input;
}
function rmx_pm_find_scalar(mixed $node, array $keys, int $depth = 0): string {
    if ($depth > 7) return '';
    $node = rmx_pm_decode_nested($node);
    if (!is_array($node)) return '';
    foreach ($keys as $key) {
        if (array_key_exists($key, $node) && is_scalar($node[$key])) {
            $v = trim((string)$node[$key]);
            if ($v !== '') return $v;
        }
    }
    foreach (['profile','account','data','payload'] as $key) {
        if (!array_key_exists($key, $node)) continue;
        $v = rmx_pm_find_scalar($node[$key], $keys, $depth + 1);
        if ($v !== '') return $v;
    }
    return '';
}
function rmx_pm_find_bool(mixed $node, array $keys, bool $default = false, int $depth = 0): bool {
    if ($depth > 7) return $default;
    $node = rmx_pm_decode_nested($node);
    if (!is_array($node)) return $default;
    foreach ($keys as $key) {
        if (array_key_exists($key, $node)) return filter_var($node[$key], FILTER_VALIDATE_BOOLEAN);
    }
    foreach (['profile','account','data','payload'] as $key) {
        if (array_key_exists($key, $node)) {
            $child = rmx_pm_decode_nested($node[$key]);
            if (is_array($child)) {
                foreach ($keys as $wanted) {
                    if (array_key_exists($wanted, $child)) return filter_var($child[$wanted], FILTER_VALIDATE_BOOLEAN);
                }
            }
        }
    }
    return $default;
}
function rmx_pm_find_cookies(mixed $node, bool &$provided, int $depth = 0): ?array {
    if ($depth > 8) return null;
    $node = rmx_pm_decode_nested($node);
    if (!is_array($node)) return null;
    foreach (['cookies','cookie','cookies_json','cookie_json','cookiesData','cookieData'] as $key) {
        if (!array_key_exists($key, $node)) continue;
        $provided = true;
        $value = rmx_pm_decode_nested($node[$key]);
        if (is_array($value)) return rmx_pm_is_list($value) ? array_values($value) : $value;
        if (is_string($value) && trim($value) === '') return [];
        return null;
    }
    foreach (['profile','account','data','payload'] as $key) {
        if (!array_key_exists($key, $node)) continue;
        $found = rmx_pm_find_cookies($node[$key], $provided, $depth + 1);
        if ($provided) return $found;
    }
    return null;
}
function rmx_pm_session_ready(FbAccount $acc): bool {
    $hasUser = false; $hasXs = false;
    foreach ((array)$acc->cookies as $cookie) {
        if (!is_array($cookie)) continue;
        $name = (string)($cookie['name'] ?? '');
        if ($name === 'c_user' && trim((string)($cookie['value'] ?? '')) !== '') $hasUser = true;
        if ($name === 'xs' && trim((string)($cookie['value'] ?? '')) !== '') $hasXs = true;
    }
    return $hasUser && $hasXs;
}
function rmx_pm_safe(FbAccount $acc): array {
    return [
        'name' => $acc->name,
        'user_id' => $acc->userId,
        'token_saved' => trim((string)$acc->token) !== '',
        'proxy_configured' => $acc->proxy !== null,
        'session_ready' => rmx_pm_session_ready($acc),
        'cookie_count' => count((array)$acc->cookies),
        'legacy_ready' => $acc->isLegacyReady(),
    ];
}
function rmx_pm_out(array $payload, int $status = 200): void {
    http_response_code($status);
    $legacy = $payload;
    unset($legacy['res']);
    $payload['res'] = json_encode($legacy, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    while (ob_get_level() > 0) { @ob_end_clean(); }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

try {
    $input = rmx_pm_input();
    $action = strtolower(rmx_pm_find_scalar($input, ['action','cmd','op','mode']));
    $hasSaveFields = rmx_pm_find_scalar($input, ['token','access_token','accessToken','fb_token','meta_token']) !== '';
    if ($action === '') $action = $hasSaveFields ? 'save' : 'list';
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);

    if (in_array($action, ['list','get','load','all'], true)) {
        $profiles = array_map('rmx_pm_safe', $store->deserialize());
        rmx_pm_out(['ok'=>true,'success'=>true,'count'=>count($profiles),'profiles'=>$profiles,'accounts'=>$profiles]);
    }
    if (in_array($action, ['delete','remove'], true)) {
        $name = rmx_pm_find_scalar($input, ['name','profile_name','profile','id','fb_id','account_id']);
        if ($name === '') throw new InvalidArgumentException('Profile name is required.');
        $remaining = $store->deleteAccountByName($name);
        rmx_pm_out(['ok'=>true,'success'=>true,'deleted'=>true,'count'=>count($remaining)]);
    }
    if (!in_array($action, ['create','save','upsert','add'], true)) {
        rmx_pm_out(['ok'=>false,'success'=>false,'error'=>'UNSUPPORTED_ACTION','message'=>'Unsupported profile action.'], 400);
    }

    $name = rmx_pm_find_scalar($input, ['name','profile_name','label','fb_id','profile_id','account_id']);
    if ($name === '') throw new InvalidArgumentException('Название профиля обязательно.');
    $existing = $store->getAccountByName($name);

    $tokenInput = rmx_pm_find_scalar($input, ['token','access_token','accessToken','fb_token','meta_token']);
    $token = $tokenInput !== '' ? $tokenInput : (string)($existing?->token ?? '');
    if (trim($token) === '') throw new InvalidArgumentException('Token обязателен для нового профиля.');

    $clearSession = rmx_pm_find_bool($input, ['clear_session','clear_cookies'], false);
    $cookiesProvided = false;
    $incomingCookies = rmx_pm_find_cookies($input, $cookiesProvided);
    if ($cookiesProvided && $incomingCookies === null) throw new InvalidArgumentException('Cookies имеют неверный JSON-формат.');

    if ($clearSession) {
        $cookies = [];
        $dtsg = null;
    } elseif ($cookiesProvided && $incomingCookies !== []) {
        $cookies = $incomingCookies;
        $dtsgInput = rmx_pm_find_scalar($input, ['dtsg','fb_dtsg','fbDtsg']);
        $dtsg = $dtsgInput !== '' ? $dtsgInput : null;
    } elseif ($existing instanceof FbAccount) {
        // Core guard: omission or accidental empty cookies must never erase a saved session.
        $cookies = (array)$existing->cookies;
        $dtsg = $existing->dtsg;
    } else {
        $cookies = [];
        $dtsg = null;
    }

    $clearProxy = rmx_pm_find_bool($input, ['clear_proxy'], false);
    $proxyInput = rmx_pm_find_scalar($input, ['proxy','proxy_raw','proxyString','proxy_string']);
    if ($clearProxy) $proxy = null;
    elseif ($proxyInput !== '') $proxy = RemaskProxy::fromSemicolonString($proxyInput);
    elseif ($existing instanceof FbAccount) $proxy = $existing->proxy;
    else $proxy = null;

    $accountsPath = (string)ACCOUNTSFILENAME;
    if (is_file($accountsPath) && filesize($accountsPath) > 2) {
        @copy($accountsPath, $accountsPath . '.bak.session-safe.' . gmdate('YmdHis'));
    }

    $cookieJson = json_encode(array_values($cookies), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    $account = new FbAccount($name, $token, $cookieJson, $dtsg, $proxy);
    $store->addOrUpdateAccount($account);
    $saved = $store->getAccountByName($name);
    if (!$saved instanceof FbAccount) throw new RuntimeException('Профиль не сохранился.');

    rmx_pm_out([
        'ok'=>true,
        'success'=>true,
        'saved'=>true,
        'profile'=>rmx_pm_safe($saved),
        'count'=>count($store->deserialize()),
    ]);
} catch (Throwable $e) {
    rmx_pm_out(['ok'=>false,'success'=>false,'error'=>'PROFILE_MANAGER_FAILED','message'=>$e->getMessage()], 400);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[profile-session-guard] canonical profile manager now preserves session context unless explicitly cleared\n");
