<?php
require_once __DIR__ . '/settings.php';

$https = (!empty($_SERVER['HTTPS']) && strtolower((string)$_SERVER['HTTPS']) !== 'off')
    || strtolower((string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '')) === 'https';
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_set_cookie_params([
        'httponly' => true,
        'secure' => $https,
        'samesite' => 'Lax',
        'path' => '/',
    ]);
    /* REMASK_UI_SESSION_PERSISTENCE_V2 */
$rmxUiSessionDir = trim((string)(getenv('REMASK_PHP_SESSION_DIR') ?: ''));
if ($rmxUiSessionDir === '') {
    $rmxUiSessionDir = rtrim(
        (string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')),
        '/'
    ) . '/php-sessions';
}
if (!is_dir($rmxUiSessionDir)) {
    @mkdir($rmxUiSessionDir, 0700, true);
}
if (is_dir($rmxUiSessionDir)) {
    @ini_set('session.save_handler', 'files');
    @ini_set('session.save_path', $rmxUiSessionDir);
    @ini_set('session.gc_maxlifetime', '604800');
    @ini_set('session.cookie_lifetime', '604800');
    @ini_set('session.cookie_httponly', '1');
    @ini_set('session.cookie_samesite', 'Lax');
    if (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') {
        @ini_set('session.cookie_secure', '1');
    }
}
/* REMASK_UI_SESSION_PERSISTENCE_V2_END */
session_start();
}

function remask_is_ajax_request(): bool
{
    $path = (string)(parse_url((string)($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH) ?: '');
    return str_contains($path, '/ajax/');
}

function remask_auth_fail(string $message, int $status = 401): never
{
    http_response_code($status);
    header('Cache-Control: no-store');
    if (remask_is_ajax_request()) {
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['ok' => false, 'error' => ['message' => $message, 'type' => 'auth']], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        exit;
    }
    $action = htmlspecialchars((string)(parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/'), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    $safeMessage = htmlspecialchars($message, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    echo '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ReMask Login</title><link href="styles/bootstrap.min.css" rel="stylesheet"><link href="styles/app.css" rel="stylesheet"></head><body class="app-login"><div class="app-login-card"><div class="app-login-brand"><span class="app-brand-mark">R</span><div><div class="app-login-title">ReMask</div><div class="app-login-sub">MEDIA BUYING WORKSPACE</div></div></div><p class="app-login-message">' . $safeMessage . '</p><form method="post" action="' . $action . '"><input type="password" name="remask_login_password" autocomplete="current-password" class="form-control" placeholder="Пароль" required><input type="hidden" name="remask_login" value="1"><button class="btn btn-primary w-100 mt-3" type="submit">ВОЙТИ</button></form></div></body></html>';
    exit;
}

function remask_csrf_token(): string
{
    if (empty($_SESSION['remask_csrf']) || !is_string($_SESSION['remask_csrf'])) {
        $_SESSION['remask_csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['remask_csrf'];
}

if (!defined('PASSWORD') || (string)PASSWORD === '' || (string)PASSWORD === 'change-me-now') remask_auth_fail('Service password is not configured.', 503);

$loginPassword = null;
if (isset($_POST['remask_login'], $_POST['remask_login_password'])) {
    $loginPassword = (string)$_POST['remask_login_password'];

}

if ($loginPassword !== null) {
    require_once __DIR__.'/classes/RemaskLoginThrottle.php';
    $throttle = new RemaskLoginThrottle($rmxUiSessionDir ?? session_save_path());
    if (!$throttle->allow()) remask_auth_fail('Too many login attempts. Try again later.', 429);
    if (!hash_equals((string)PASSWORD, $loginPassword)) remask_auth_fail('Password does not match.', 403);
    $throttle->clear();
    session_regenerate_id(true);
    $_SESSION['remask_authenticated'] = true;
    $_SESSION['remask_csrf'] = bin2hex(random_bytes(32));
    header('Location: ' . ((string)(parse_url((string)($_SERVER['REQUEST_URI'] ?? '/'), PHP_URL_PATH) ?: '/')), true, 303);
    exit;
}

if (empty($_SESSION['remask_authenticated'])) {
    // One-time migration compatibility for sessions created by the old code.
    if (isset($_SESSION['password']) && hash_equals((string)PASSWORD, (string)$_SESSION['password'])) {
        unset($_SESSION['password']);
        session_regenerate_id(true);
        $_SESSION['remask_authenticated'] = true;
    } else {
        remask_auth_fail('Authentication required.');
    }
}

remask_csrf_token();

if (remask_is_ajax_request()) {
    require_once __DIR__.'/classes/RemaskRequestPolicy.php';
    RemaskRequestPolicy::enforce(basename((string)(parse_url((string)($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH) ?: '')));
}

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
if (!in_array($method, ['GET', 'HEAD', 'OPTIONS'], true)) {
    $provided = (string)($_SERVER['HTTP_X_REMASK_CSRF'] ?? ($_POST['remask_csrf'] ?? ''));
    if ($provided === '' || !hash_equals((string)$_SESSION['remask_csrf'], $provided)) {
        remask_auth_fail('CSRF validation failed. Reload the page and try again.', 403);
    }
}
