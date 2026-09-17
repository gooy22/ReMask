<?php
/**
 * Replaces ajax/metaProfileManager.php with the SAME profile store contract used
 * by ajax/addAccount.php: AccountStoreFactory + FbAccount + RemaskProxy.
 * No hand-written alternate accounts.json schema.
 */
$root = '/var/www/html';
$ajaxDir = $root . '/ajax';
if (!is_dir($ajaxDir)) mkdir($ajaxDir, 0775, true);
$target = $ajaxDir . '/metaProfileManager.php';

$php = <<<'PHP'
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

function remask_pm_out(array $payload, int $status = 200): void {
    http_response_code($status);
    while (ob_get_level() > 0) { @ob_end_clean(); }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function remask_pm_input(): array {
    $input = $_POST;
    $raw = (string)file_get_contents('php://input');
    if ($raw !== '') {
        $json = json_decode($raw, true);
        if (is_array($json)) {
            $input = array_replace($input, $json);
        } elseif (!$input) {
            parse_str($raw, $parsed);
            if (is_array($parsed)) $input = $parsed;
        }
    }
    return $input;
}

function remask_pm_safe_profile(FbAccount $acc): array {
    return [
        'name' => $acc->name,
        'user_id' => $acc->userId,
        'proxy_configured' => $acc->proxy !== null,
        'proxy' => $acc->proxy?->toArray(),
        'legacy_ready' => $acc->isLegacyReady(),
        'token_saved' => $acc->token !== '',
    ];
}

try {
    $input = remask_pm_input();
    $action = strtolower(trim((string)($input['action'] ?? 'list')));
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);

    if (in_array($action, ['list', 'get', 'load', 'all'], true)) {
        $profiles = array_map('remask_pm_safe_profile', $store->deserialize());
        remask_pm_out([
            'ok' => true,
            'success' => true,
            'count' => count($profiles),
            'profiles' => $profiles,
            'accounts' => $profiles,
        ]);
    }

    if (in_array($action, ['delete', 'remove'], true)) {
        $name = trim((string)($input['name'] ?? $input['profile'] ?? ''));
        if ($name === '') throw new InvalidArgumentException('Profile name is required.');
        $remaining = $store->deleteAccountByName($name);
        remask_pm_out([
            'ok' => true,
            'success' => true,
            'deleted' => true,
            'count' => count($remaining),
        ]);
    }

    if (!in_array($action, ['create', 'save', 'upsert', 'add'], true)) {
        remask_pm_out(['ok' => false, 'success' => false, 'error' => 'UNSUPPORTED_ACTION', 'message' => 'Unsupported profile action.'], 400);
    }

    $name = trim((string)($input['name'] ?? $input['profile_name'] ?? ''));
    if ($name === '') throw new InvalidArgumentException('Название обязательно.');

    $existing = $store->getAccountByName($name);

    $tokenInput = trim((string)($input['token'] ?? $input['access_token'] ?? ''));
    $token = $tokenInput !== '' ? $tokenInput : (string)($existing?->token ?? '');
    if ($token === '') throw new InvalidArgumentException('Token обязателен для нового профиля.');

    $cookiesInput = trim((string)($input['cookies'] ?? ''));
    if ($cookiesInput === '' && $existing !== null) {
        $cookies = json_encode($existing->cookies, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $dtsg = $existing->dtsg;
    } else {
        $cookies = $cookiesInput !== '' ? $cookiesInput : '[]';
        $dtsg = null;
    }

    // Validate cookies now, before touching the store.
    $decodedCookies = json_decode($cookies, true);
    if (!is_array($decodedCookies)) throw new InvalidArgumentException('Cookies должны быть JSON-массивом.');

    $clearProxy = filter_var($input['clear_proxy'] ?? false, FILTER_VALIDATE_BOOLEAN);
    $proxyInput = trim((string)($input['proxy'] ?? ''));
    if ($clearProxy) {
        $proxy = null;
    } elseif ($proxyInput === '' && $existing !== null) {
        $proxy = $existing->proxy;
    } elseif ($proxyInput !== '') {
        $proxy = RemaskProxy::fromSemicolonString($proxyInput);
    } else {
        $proxy = null;
    }

    $account = new FbAccount($name, $token, $cookies, $dtsg, $proxy);
    $store->addOrUpdateAccount($account);

    // Verify persistence through the same serializer the rest of ReMask uses.
    $saved = $store->getAccountByName($name);
    if (!$saved instanceof FbAccount || $saved->token === '') {
        throw new RuntimeException('Профиль не сохранился в AccountStore.');
    }

    $count = count($store->deserialize());
    remask_pm_out([
        'ok' => true,
        'success' => true,
        'saved' => true,
        'count' => $count,
        'profile' => remask_pm_safe_profile($saved),
    ]);
} catch (Throwable $e) {
    remask_pm_out([
        'ok' => false,
        'success' => false,
        'error' => 'PROFILE_MANAGER_FAILED',
        'message' => $e->getMessage(),
    ], 400);
}
PHP;

file_put_contents($target, $php);
fwrite(STDERR, "[remask account manager overlay] metaProfileManager now uses AccountStoreFactory/FbAccount\n");
