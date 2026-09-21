<?php
/**
 * Internal profile-context resolver for the ReMask Python worker.
 *
 * Security contract:
 * - requires REMASK_INTERNAL_KEY and X-Remask-Internal-Key;
 * - returns only cookies, proxy URL and User-Agent;
 * - never returns access tokens, fb_dtsg or other account secrets not needed by
 *   the profile-bound HTTP session;
 * - intended to be called over Railway private networking.
 */
$root = '/var/www/html';
$target = $root . '/ajax/pythonProfileContext.php';

$php = <<<'PHP_CODE'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function rmx_py_out(array $payload, int $status = 200): void {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function rmx_py_header(string $name): string {
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return trim((string)($_SERVER[$key] ?? ''));
}

function rmx_py_cookie_map(mixed $raw): array {
    if (!is_array($raw)) return [];
    $out = [];
    if (!array_is_list($raw)) {
        foreach ($raw as $name => $value) {
            if (is_scalar($value) && trim((string)$name) !== '') {
                $out[(string)$name] = (string)$value;
            }
        }
        return $out;
    }
    foreach ($raw as $row) {
        if (!is_array($row)) continue;
        $name = trim((string)($row['name'] ?? ''));
        $value = (string)($row['value'] ?? '');
        if ($name !== '' && $value !== '') $out[$name] = $value;
    }
    return $out;
}

function rmx_py_proxy_url(?RemaskProxy $proxy): ?string {
    if (!$proxy) return null;
    $scheme = in_array($proxy->type, ['socks5','socks5h'], true) ? $proxy->type : 'http';
    $auth = '';
    if ($proxy->login !== '' || $proxy->password !== '') {
        $auth = rawurlencode($proxy->login) . ':' . rawurlencode($proxy->password) . '@';
    }
    return $scheme . '://' . $auth . $proxy->ip . ':' . $proxy->port;
}

try {
    $expected = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
    if ($expected === '') rmx_py_out(['ok'=>false,'error'=>'INTERNAL_KEY_NOT_CONFIGURED'], 503);

    $provided = rmx_py_header('X-Remask-Internal-Key');
    if ($provided === '' || !hash_equals($expected, $provided)) {
        rmx_py_out(['ok'=>false,'error'=>'UNAUTHORIZED'], 401);
    }

    $profile = trim((string)($_GET['profile_id'] ?? $_POST['profile_id'] ?? ''));
    if ($profile === '') rmx_py_out(['ok'=>false,'error'=>'PROFILE_REQUIRED'], 400);

    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $account = $store->getAccountByName($profile);
    if (!$account instanceof FbAccount) rmx_py_out(['ok'=>false,'error'=>'PROFILE_NOT_FOUND'], 404);

    $ua = trim((string)(getenv('REMASK_PYTHON_USER_AGENT') ?: ''));
    if ($ua === '') {
        $ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            . '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';
    }

    rmx_py_out([
        'ok' => true,
        'profile_id' => $profile,
        'cookies' => rmx_py_cookie_map($account->cookies),
        'proxy' => rmx_py_proxy_url($account->proxy),
        'user_agent' => $ua,
    ]);
} catch (Throwable $e) {
    error_log('[python-profile-context] ' . get_class($e) . ': ' . $e->getMessage());
    rmx_py_out(['ok'=>false,'error'=>'PROFILE_CONTEXT_FAILED'], 500);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[python-profile-context] internal profile resolver installed\n");
