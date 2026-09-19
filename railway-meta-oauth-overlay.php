<?php
/**
 * Official Meta OAuth connection flow for ReMask.
 * No browser cookies/private Graph endpoints are used for Marketing API auth.
 */
$root = '/var/www/html';

$startPhp = <<<'PHP'
<?php
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/checkpassword.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';

if (session_status() !== PHP_SESSION_ACTIVE) session_start();

$appId = trim((string)(getenv('META_APP_ID') ?: ''));
if ($appId === '') {
    http_response_code(503);
    header('Content-Type: text/plain; charset=utf-8');
    echo "Meta OAuth is not configured: META_APP_ID is missing.";
    exit;
}

$profile = trim((string)($_GET['profile'] ?? ''));
if ($profile === '') {
    http_response_code(400);
    header('Content-Type: text/plain; charset=utf-8');
    echo "profile is required";
    exit;
}
$store = AccountStoreFactory::create(ACCOUNTSFILENAME);
if ($store->getAccountByName($profile) === null) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    echo "ReMask profile not found.";
    exit;
}

$publicDomain = trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN') ?: ''));
$redirectUri = trim((string)(getenv('META_OAUTH_REDIRECT_URI') ?: ''));
if ($redirectUri === '') {
    if ($publicDomain === '') {
        http_response_code(503);
        header('Content-Type: text/plain; charset=utf-8');
        echo "Meta OAuth redirect URI cannot be determined.";
        exit;
    }
    $redirectUri = 'https://' . $publicDomain . '/meta-oauth-callback.php';
}

$state = bin2hex(random_bytes(32));
$_SESSION['remask_meta_oauth'] = [
    'state' => $state,
    'profile' => $profile,
    'redirect_uri' => $redirectUri,
    'created_at' => time(),
];

$scopes = trim((string)(getenv('META_OAUTH_SCOPES') ?: 'ads_management,ads_read,business_management,pages_show_list,pages_read_engagement,read_insights'));
$params = [
    'client_id' => $appId,
    'redirect_uri' => $redirectUri,
    'state' => $state,
    'scope' => $scopes,
    'response_type' => 'code',
];
$version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
header('Location: https://www.facebook.com/' . rawurlencode($version) . '/dialog/oauth?' . http_build_query($params));
exit;
PHP;
file_put_contents($root . '/meta-oauth-start.php', $startPhp);

$callbackPhp = <<<'PHP'
<?php
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/classes/RemaskProxy.php';
require_once __DIR__ . '/classes/FbAccount.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';
require_once __DIR__ . '/classes/MetaEndpoint.php';

if (session_status() !== PHP_SESSION_ACTIVE) session_start();
header('Cache-Control: no-store, max-age=0');

function remask_oauth_fail(string $message, int $status = 400): never {
    http_response_code($status);
    header('Content-Type: text/html; charset=utf-8');
    $safe = htmlspecialchars($message, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
    echo '<!doctype html><meta charset="utf-8"><title>Meta OAuth</title><body style="font-family:system-ui;background:#111827;color:#e5e7eb;padding:32px"><h2>Meta OAuth не подключён</h2><p>' . $safe . '</p><p><a style="color:#93c5fd" href="/workspace.php">Вернуться в Workspace</a></p></body>';
    exit;
}

function remask_oauth_get(string $url): array {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 12,
        CURLOPT_TIMEOUT => 35,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'User-Agent: ReMask-MetaOAuth/1.0'],
    ]);
    $raw = curl_exec($ch);
    $error = curl_error($ch);
    $http = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);
    if ($raw === false) throw new RuntimeException('Meta OAuth transport error: ' . ($error ?: 'unknown cURL error'));
    $decoded = json_decode((string)$raw, true);
    if (!is_array($decoded)) throw new RuntimeException('Meta OAuth returned a non-JSON response.');
    if (isset($decoded['error']) && is_array($decoded['error'])) {
        $e = $decoded['error'];
        $message = trim((string)($e['message'] ?? 'Meta OAuth rejected the request.'));
        $code = isset($e['code']) ? ' code ' . (int)$e['code'] : '';
        throw new RuntimeException($message . $code);
    }
    if ($http < 200 || $http >= 300) throw new RuntimeException('Meta OAuth returned HTTP ' . $http . '.');
    return $decoded;
}

$appId = trim((string)(getenv('META_APP_ID') ?: ''));
$appSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));
if ($appId === '' || $appSecret === '') remask_oauth_fail('На сервере не заданы META_APP_ID / META_APP_SECRET.', 503);

$pending = $_SESSION['remask_meta_oauth'] ?? null;
unset($_SESSION['remask_meta_oauth']);
if (!is_array($pending) || (int)($pending['created_at'] ?? 0) < time() - 900) remask_oauth_fail('OAuth-сессия отсутствует или истекла. Запустите подключение заново.');
$expectedState = (string)($pending['state'] ?? '');
$state = (string)($_GET['state'] ?? '');
if ($expectedState === '' || $state === '' || !hash_equals($expectedState, $state)) remask_oauth_fail('OAuth state не совпал; подключение отменено.');
if (isset($_GET['error'])) remask_oauth_fail('Meta не выдала разрешение: ' . (string)($_GET['error_description'] ?? $_GET['error']));
$code = trim((string)($_GET['code'] ?? ''));
if ($code === '') remask_oauth_fail('Meta не вернула authorization code.');

$profile = trim((string)($pending['profile'] ?? ''));
$redirectUri = trim((string)($pending['redirect_uri'] ?? ''));
if ($profile === '' || $redirectUri === '') remask_oauth_fail('OAuth-сессия повреждена.');
$version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';

try {
    $exchange = remask_oauth_get('https://graph.facebook.com/' . rawurlencode($version) . '/oauth/access_token?' . http_build_query([
        'client_id' => $appId,
        'client_secret' => $appSecret,
        'redirect_uri' => $redirectUri,
        'code' => $code,
    ]));
    $token = trim((string)($exchange['access_token'] ?? ''));
    if ($token === '') throw new RuntimeException('Meta OAuth did not return an access token.');

    // Best-effort long-lived exchange. If Meta declines it, the valid initial user token is kept.
    try {
        $long = remask_oauth_get('https://graph.facebook.com/' . rawurlencode($version) . '/oauth/access_token?' . http_build_query([
            'grant_type' => 'fb_exchange_token',
            'client_id' => $appId,
            'client_secret' => $appSecret,
            'fb_exchange_token' => $token,
        ]));
        if (isset($long['access_token']) && trim((string)$long['access_token']) !== '') $token = trim((string)$long['access_token']);
    } catch (Throwable $ignored) {
        // Initial token is already an official OAuth token; long-lived exchange is optional here.
    }

    // Verify official Graph access before touching persistent profile credentials.
    $me = remask_oauth_get('https://graph.facebook.com/' . rawurlencode($version) . '/me?' . http_build_query([
        'fields' => 'id,name',
        'access_token' => $token,
    ]));
    if (trim((string)($me['id'] ?? '')) === '') throw new RuntimeException('Meta /me verification returned no user ID.');

    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $existing = $store->getAccountByName($profile);
    if ($existing === null) throw new RuntimeException('ReMask profile no longer exists.');
    $cookies = json_encode($existing->cookies, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
    $updated = new FbAccount($existing->name, $token, $cookies, $existing->dtsg, $existing->proxy);
    $store->addOrUpdateAccount($updated);
    try { MetaEndpoint::invalidateProfileCache($profile); } catch (Throwable $ignored) {}

    header('Location: /workspace.php?meta_oauth=connected&profile=' . rawurlencode($profile));
    exit;
} catch (Throwable $e) {
    remask_oauth_fail($e->getMessage());
}
PHP;
file_put_contents($root . '/meta-oauth-callback.php', $callbackPhp);

$statusPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
try {
    $appId = trim((string)(getenv('META_APP_ID') ?: ''));
    $appSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));
    $publicDomain = trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN') ?: ''));
    $redirectUri = trim((string)(getenv('META_OAUTH_REDIRECT_URI') ?: ''));
    if ($redirectUri === '' && $publicDomain !== '') $redirectUri = 'https://' . $publicDomain . '/meta-oauth-callback.php';
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $profiles = [];
    foreach ($store->deserialize() as $account) {
        if ($account instanceof FbAccount && $account->name !== '') $profiles[] = $account->name;
    }
    echo json_encode([
        'ok'=>true,
        'configured'=>$appId !== '' && $appSecret !== '',
        'app_id_configured'=>$appId !== '',
        'app_secret_configured'=>$appSecret !== '',
        'redirect_uri'=>$redirectUri,
        'profiles'=>array_values($profiles),
        'required_permissions'=>['ads_management','ads_read'],
        'optional_business_permissions'=>['business_management','pages_show_list','pages_read_engagement','read_insights'],
    ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['ok'=>false,'error'=>$e->getMessage()], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}
PHP;
file_put_contents($root . '/ajax/metaOAuthStatus.php', $statusPhp);

$oauthJs = <<<'JS'
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  async function initMetaOAuth() {
    const anchor = document.getElementById('workspaceStatus');
    if (!anchor || document.getElementById('remaskMetaOAuth')) return;
    let data;
    try {
      const r = await fetch('ajax/metaOAuthStatus.php', {credentials:'same-origin', cache:'no-store'});
      data = await r.json();
    } catch (_) { return; }
    if (!data || !data.ok) return;

    const box = document.createElement('div');
    box.id = 'remaskMetaOAuth';
    box.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:10px 0;padding:10px 12px;border:1px solid rgba(148,163,184,.22);border-radius:12px;background:rgba(15,23,42,.42);font-size:13px';
    if (!data.configured) {
      box.innerHTML = '<strong>Meta API:</strong><span>официальный OAuth не настроен — нужны META_APP_ID и META_APP_SECRET.</span>' +
        (data.redirect_uri ? '<code style="opacity:.75">Callback: '+esc(data.redirect_uri)+'</code>' : '');
    } else {
      const profiles = Array.isArray(data.profiles) ? data.profiles : [];
      const options = profiles.map(p => '<option value="'+esc(p)+'">'+esc(p)+'</option>').join('');
      box.innerHTML = '<strong>Meta API:</strong><select id="remaskMetaOAuthProfile" style="max-width:240px">'+options+'</select><button type="button" id="remaskMetaOAuthConnect">Подключить Meta OAuth</button><span style="opacity:.72">ads_management + ads_read</span>';
      setTimeout(() => {
        const button = document.getElementById('remaskMetaOAuthConnect');
        if (button) button.onclick = () => {
          const select = document.getElementById('remaskMetaOAuthProfile');
          const profile = select ? select.value : '';
          if (profile) location.href = '/meta-oauth-start.php?profile=' + encodeURIComponent(profile);
        };
      }, 0);
    }
    anchor.insertAdjacentElement('afterend', box);

    const q = new URLSearchParams(location.search);
    if (q.get('meta_oauth') === 'connected') {
      anchor.textContent = 'Meta OAuth подключён. Запустите синхронизацию профиля.';
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initMetaOAuth);
  else initMetaOAuth();
})();
JS;
file_put_contents($root . '/scripts/meta-oauth.js', $oauthJs);

$workspacePath = $root . '/workspace.php';
$workspace = file_get_contents($workspacePath);
if ($workspace === false) throw new RuntimeException('workspace.php not found');
if (!str_contains($workspace, 'scripts/meta-oauth.js')) {
    $tag = '<script src="scripts/meta-oauth.js?v=1"></script>';
    if (str_contains($workspace, '</body>')) $workspace = str_replace('</body>', $tag . "\n</body>", $workspace);
    else $workspace .= "\n" . $tag . "\n";
    file_put_contents($workspacePath, $workspace);
}

// Make the current session-bound/Ads-Manager-token failure explicit in Workspace.
$hierarchyPath = $root . '/ajax/metaHierarchy.php';
$hierarchy = file_get_contents($hierarchyPath);
if ($hierarchy === false) throw new RuntimeException('metaHierarchy.php not found');
$needle = "        MetaEndpoint::cachedPreflight(\$profile, true);";
$replacement = <<<'PHP'
        try {
            MetaEndpoint::cachedPreflight($profile, true);
        } catch (Throwable $metaAuthError) {
            $metaAuthMessage = trim((string)$metaAuthError->getMessage());
            $metaAuthCode = (int)$metaAuthError->getCode();
            if (
                ($metaAuthCode === 1 && $metaAuthMessage === 'Invalid request.')
                || $metaAuthCode === 190
                || stripos($metaAuthMessage, 'Error loading application') !== false
            ) {
                throw new RuntimeException('META_OAUTH_REQUIRED: сохранённый Meta access token больше не принимается Graph API. Переподключите этот FB-профиль через Meta OAuth.');
            }
            throw $metaAuthError;
        }
PHP;
if (!str_contains($hierarchy, 'META_OAUTH_REQUIRED')) {
    $syncMarker = "    if (\$action === 'sync_profile') {";
    $syncPos = strpos($hierarchy, $syncMarker);
    $preflightPos = $syncPos === false ? false : strpos($hierarchy, $needle, $syncPos);
    if ($syncPos === false || $preflightPos === false) {
        throw new RuntimeException('metaHierarchy OAuth sync_profile patch target not found');
    }
    $hierarchy = substr($hierarchy, 0, $preflightPos)
        . $replacement
        . substr($hierarchy, $preflightPos + strlen($needle));
}
file_put_contents($hierarchyPath, $hierarchy);

// Same clear message on the account validation endpoint.
$checkPath = $root . '/ajax/checkAccount.php';
$check = file_get_contents($checkPath);
if ($check !== false) {
    $oldCatch = <<<'PHP'
} catch (Throwable $e) {
    http_response_code(200);
    ResponseFormatter::Respond(['error' => $e->getMessage()]);
}
PHP;
    $newCatch = <<<'PHP'
} catch (Throwable $e) {
    http_response_code(200);
    $message = $e->getMessage();
    if (str_contains($message, 'Invalid request') || str_contains($message, 'Error loading application') || str_contains($message, 'code 190')) {
        $message = 'Сохранённый Meta access token больше не принимается Graph API. Переподключите FB-профиль через Meta OAuth.';
    }
    ResponseFormatter::Respond(['error' => $message]);
}
PHP;
    $changed = 0;
    $check = str_replace($oldCatch, $newCatch, $check, $changed);
    if ($changed === 1) file_put_contents($checkPath, $check);
}

fwrite(STDERR, "[meta-oauth] official OAuth endpoints + Workspace connector ready\n");