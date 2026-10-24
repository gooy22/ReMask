<?php
/** Final installed runtime: cookie authorisation, no Graph/token fallback. */
$root = '/var/www/html';
$helper = <<<'COOKIE_PROFILE'
<?php
declare(strict_types=1);
final class RemaskCookieProfile
{
    public static function directTestPath(): string
    {
        $root = (string)(getenv('REMASK_DATA_DIR') ?: getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: dirname((string)ACCOUNTSFILENAME));
        return rtrim($root, '/') . '/profile-direct-test.json';
    }

    public static function directTestUntil(string $profile, string $actor = ''): int
    {
        $selected = trim((string)(getenv('REMASK_DIRECT_TEST_PROFILE') ?: ''));
        $until = (int)(getenv('REMASK_DIRECT_TEST_UNTIL') ?: 0);
        $now = time();
        if ($profile !== '' && $selected === $profile && $until > $now && $until <= $now + 7200) return $until;
        $path = self::directTestPath();
        if ($profile === '' || $actor === '' || !is_file($path) || filesize($path) > 2048) return 0;
        $row = json_decode((string)@file_get_contents($path), true);
        $until = $row['expires_at'] ?? 0;
        return is_array($row) && ($row['profile_id'] ?? null) === $profile && ($row['actor_id'] ?? null) === $actor
            && is_int($until) && $until > $now && $until <= $now + 7200 ? $until : 0;
    }

    public static function saveDirectTest(string $profile, string $actor, int $until): void
    {
        $path = self::directTestPath();
        if (!is_dir(dirname($path)) && !mkdir(dirname($path), 0700, true)) throw new RuntimeException('DIRECT_TEST_SAVE_FAILED');
        $temporary = tempnam(dirname($path), '.direct-test-');
        if ($temporary === false) throw new RuntimeException('DIRECT_TEST_SAVE_FAILED');
        try {
            chmod($temporary, 0600);
            if (file_put_contents($temporary, json_encode(['profile_id'=>$profile, 'actor_id'=>$actor, 'expires_at'=>$until], JSON_THROW_ON_ERROR)) === false
                || !rename($temporary, $path)) throw new RuntimeException('DIRECT_TEST_SAVE_FAILED');
        } finally { if (is_file($temporary)) unlink($temporary); }
    }

    public static function validate(array $cookies, mixed $proxy, bool $requireProxy = true, string $profile = ''): array
    {
        $rows = [];
        if (!array_is_list($cookies)) {
            foreach ($cookies as $name => $value) $rows[] = ['name'=>(string)$name, 'value'=>$value];
        } else $rows = $cookies;
        $auth = [];
        foreach ($rows as &$row) {
            if (!is_array($row) || !is_string($row['name'] ?? null) || trim($row['name']) === '' ||
                (!is_string($row['value'] ?? null) && !is_int($row['value'] ?? null))) {
                throw new InvalidArgumentException('Cookies должны содержать name и value.');
            }
            $row['name'] = trim($row['name']);
            $row['value'] = (string)$row['value'];
            if (in_array($row['name'], ['c_user','xs'], true)) {
                if (isset($auth[$row['name']]) && $auth[$row['name']] !== $row['value']) {
                    throw new InvalidArgumentException('Cookies содержат разные значения ' . $row['name'] . '.');
                }
                $auth[$row['name']] = $row['value'];
            }
        }
        unset($row);
        if (!ctype_digit($auth['c_user'] ?? '') || trim($auth['xs'] ?? '') === '') {
            throw new InvalidArgumentException('Cookies должны содержать непустые c_user и xs.');
        }
        if ($requireProxy && $proxy === null && self::directTestUntil($profile, $auth['c_user']) === 0) throw new InvalidArgumentException('Прокси обязателен.');
        return array_values($rows);
    }
}
COOKIE_PROFILE;
file_put_contents($root . '/classes/RemaskCookieProfile.php', $helper);
file_put_contents($root . '/classes/RemaskCookieTxt.php', file_get_contents('/tmp/railway-cookie-txt-parser.php'));
file_put_contents($root . '/scripts/cookie-txt-import.js', file_get_contents('/tmp/railway-cookie-txt-import.js'));

// Both persistence backends must accept the validated cookie profile.
foreach (['FbAccountSerializer'=>'acc', 'PostgresProfileStore'=>'account'] as $class => $var) {
    $path = $root . '/classes/' . $class . '.php';
    $source = file_get_contents($path);
    $needle = '$' . $var . "->name === '' || $" . $var . "->token === ''";
    if (substr_count($source, $needle) !== 1) throw new RuntimeException('Profile store boundary missing: ' . $class);
    $source = str_replace($needle, '$' . $var . "->name === ''", $source);
    $source = str_replace(['Name and token are required.', 'Profile name and token are required.'], 'Profile name is required.', $source);
    file_put_contents($path, $source);
}

// Canonical Profile Manager accepts tokenless create and preserves omitted
// session/proxy values. Legacy Accounts and hierarchy updates use that same path.
file_put_contents($root . '/ajax/addAccount.php', <<<'ADD_ACCOUNT'
<?php
$_POST['action'] = $_POST['action'] ?? 'save';
require __DIR__ . '/metaProfileManager.php';
ADD_ACCOUNT);
file_put_contents($root . '/ajax/checkAccount.php', <<<'CHECK_ACCOUNT'
<?php
declare(strict_types=1);
ob_start();
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/RemaskProxy.php';
require_once __DIR__ . '/../classes/FbAccount.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
require_once __DIR__ . '/../classes/RemaskCookieProfile.php';
while (ob_get_level() > 0) { @ob_end_clean(); }
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
try {
    $name = trim((string)($_POST['name'] ?? ''));
    if ($name === '') throw new InvalidArgumentException('Название профиля обязательно.');
    $existing = AccountStoreFactory::create(ACCOUNTSFILENAME)->getAccountByName($name);
    $raw = trim((string)($_POST['cookies'] ?? ''));
    $cookies = $raw === '' ? (array)($existing?->cookies ?? []) : json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($cookies)) throw new InvalidArgumentException('Cookies должны быть JSON.');
    if ($cookies === [] && $existing) $cookies = (array)$existing->cookies;
    $proxyRaw = trim((string)($_POST['proxy'] ?? ''));
    $proxy = $proxyRaw !== '' ? RemaskProxy::fromSemicolonString($proxyRaw) : ($existing?->proxy ?? null);
    RemaskCookieProfile::validate($cookies, $proxy, true, $name);
    $data = ['name'=>$name, 'auth_mode'=>'cookies', 'token_required'=>false, 'cookie_format_valid'=>true, 'session_verified'=>false];
    echo json_encode(['ok'=>true, 'success'=>true, 'res'=>json_encode($data, JSON_UNESCAPED_UNICODE)]);
} catch (Throwable $e) {
    http_response_code(400);
    echo json_encode(['ok'=>false, 'success'=>false, 'error'=>$e->getMessage()], JSON_UNESCAPED_UNICODE);
}
CHECK_ACCOUNT);

$hierarchyPath = $root . '/ajax/metaHierarchy.php';
$hierarchy = file_get_contents($hierarchyPath);
$start = strpos($hierarchy, "if (\$action === 'update_profile') {");
$end = strpos($hierarchy, "if (\$action === 'assign_proxy') {", $start === false ? 0 : $start);
if ($start === false || $end === false) throw new RuntimeException('hierarchy profile update region missing');
$forward = <<<'FORWARD_UPDATE'
if ($action === 'update_profile') {
    $_POST = $input;
    $_POST['action'] = 'save';
    $_POST['name'] = $input['profile'] ?? $input['name'] ?? '';
    require __DIR__ . '/metaProfileManager.php';
    exit;
}

FORWARD_UPDATE;
$hierarchy = substr($hierarchy, 0, $start) . $forward . substr($hierarchy, $end);
file_put_contents($hierarchyPath, $hierarchy);

$workspacePath = $root . '/scripts/workspace.js';
$js = file_get_contents($workspacePath);
// Profile Manager returns top-level fields; hierarchy returns a data envelope.
// Read both and retain precise backend errors instead of reducing them to HTTP 502.
$apiJson = <<<'COOKIE_API_JSON'
async function apiJson(url, options={}) { const r=await fetch(url,options); const t=await r.text(); let j; try{j=JSON.parse(t)}catch{throw new Error(`Invalid JSON (${r.status})`)} if(!r.ok||j.ok===false){const message=typeof j.error==='string'?(j.message||j.error):(j.error?.message||j.message);throw new Error(message||`HTTP ${r.status}`)} return j.data===undefined?j:j.data; }
COOKIE_API_JSON;
$js = preg_replace('/async function apiJson\\(url, options=\\{\\}\\) \\{[^\\n]+\\}/', $apiJson, $js, 1, $apiCount);
if ($apiCount !== 1) throw new RuntimeException('Cookie profile API response boundary missing');

$positions = [strpos($js, 'function prepareAddProfile'), strpos($js, 'function prepareEditProfile')];
if (in_array(false, $positions, true)) throw new RuntimeException('Workspace profile forms missing');
$start = min($positions);
$end = strpos($js, 'function launchTargets(', $start);
if ($end === false) throw new RuntimeException('Workspace profile form boundary missing');
$js = substr($js, 0, $start) . file_get_contents('/tmp/railway-cookie-profile-ui.js') . "\n" . substr($js, $end);
file_put_contents($workspacePath, $js);

// Block official transport before a client is created, including profiles
// which still contain historical tokens. Keep private GraphQL and CSRF intact.
$clientPath = $root . '/classes/MetaApiClient.php';
$client = file_get_contents($clientPath);
$needle = '        $accessToken = trim($accessToken);';
if (substr_count($client, $needle) !== 1) throw new RuntimeException('MetaApiClient constructor boundary missing');
$client = str_replace($needle, "        throw new RuntimeException('COOKIE_ONLY_GRAPH_DISABLED: Graph API отключён; используется Facebook-сессия.');\n" . $needle, $client);
file_put_contents($clientPath, $client);
$fbPath = $root . '/classes/FbRequests.php';
$fb = file_get_contents($fbPath);
foreach (['ApiGet','ApiPost','GetNewToken'] as $method) {
    $pattern = '/((?:public|private) function ' . $method . '\([^)]*\)(?:\s*:\s*\??[A-Za-z]+)?\s*\{)/';
    $guard = $method === 'GetNewToken' ? "\n        return null; // COOKIE_ONLY_GRAPH_DISABLED\n" : "\n        throw new RuntimeException('COOKIE_ONLY_GRAPH_DISABLED');\n";
    $fb = preg_replace_callback($pattern, fn($m) => $m[1] . $guard, $fb, 1, $count);
    if ($count !== 1) throw new RuntimeException('FbRequests boundary missing: ' . $method);
}
$execute = '    private function Execute(FbAccount $acc, array $optArray): array';
$pos = strpos($fb, $execute);
$brace = $pos === false ? false : strpos($fb, '{', $pos);
if ($brace === false) throw new RuntimeException('FbRequests Execute boundary missing');
$guard = <<<'GRAPH_GUARD'

        $host = strtolower((string)parse_url((string)($optArray[CURLOPT_URL] ?? ''), PHP_URL_HOST));
        if ($host === 'graph.facebook.com' || $host === 'graph-video.facebook.com' || str_ends_with($host, '.graph.facebook.com')) throw new RuntimeException('COOKIE_ONLY_GRAPH_DISABLED');
GRAPH_GUARD;
file_put_contents($fbPath, substr($fb, 0, $brace + 1) . $guard . substr($fb, $brace + 1));

// Product labels must describe the active session mode.
foreach (['workspace.php','launch.php','accounts.php','menu.php'] as $file) {
    $path = $root . '/' . $file;
    if (!is_file($path)) continue;
    $s = file_get_contents($path);
    $s = str_replace(['Official Meta API','доступные по token'], ['Facebook cookies','сохранённые BM'], $s);
    $s = preg_replace('#scripts/workspace\.js(?:\?[^"\']*)?#', 'scripts/workspace.js?v=20261004-python-worker-ui-v220-cookie-only-v1-numbered-v1-txt-v3-direct-import-v5-bundle-http-saved-publish-v1', $s);
    $s = preg_replace('#scripts/accounts\.js(?:\?[^"\']*)?#', 'scripts/accounts.js?v=20261002-cookie-only-v1-numbered-v1-txt-v3-direct-import-v5-bundle-http', $s);
    file_put_contents($path, $s);
}
// Shared import UI on Workspace and Accounts; no browser storage of shop secrets.
foreach (['workspace.php','accounts.php'] as $file) {
    $path = $root . '/' . $file;
    $source = file_get_contents($path);
    if (substr_count($source, '</body>') !== 1) throw new RuntimeException('TXT UI body boundary missing: ' . $file);
    $source = str_replace('</body>', '<script src="scripts/cookie-txt-import.js?v=20261020-txt-saved-proxy-v4" defer></script></body>', $source);
    file_put_contents($path, $source);
}
// A legacy token input is removed before the Accounts form becomes usable.
$accountsPath = $root . '/accounts.php';
$accounts = file_get_contents($accountsPath);
$accounts = preg_replace('#<div[^>]*><label>Access token</label><input[^>]*name="token"[^>]*></div>#', '', $accounts, 1, $count);
if ($count !== 1) throw new RuntimeException('Accounts token field boundary missing');
$accounts = str_replace([
    'Профили, token, legacy cookies и proxy.',
    'Добавить / обновить Facebook API Profile',
    'Для Workspace/Launch обязательны только название и Meta access token. Cookies и proxy — опционально.',
    '<th>API</th>', '<th>Legacy cookies</th>',
    '<span class="app-pill ok">TOKEN</span>',
    "'READY' : 'API ONLY'", "'ASSIGNED' : 'DIRECT'",
    '(legacy, optional)', 'CHECK API &amp; SAVE', 'CHECK API & SAVE'
], [
    'Профили, Facebook cookies и прокси.',
    'Добавить / обновить Facebook профиль',
    'Нужны название, Facebook cookies и прокси. Токен Ads Manager не нужен. Сохранение не подтверждает действительность Facebook-сессии.',
    '<th>Авторизация</th>', '<th>Cookies</th>',
    '<span class="app-pill">COOKIES</span>',
    "'СОХРАНЕНЫ' : 'НЕТ COOKIES'", "'ASSIGNED' : 'НЕ ЗАДАН'",
    '(c_user и xs)', 'СОХРАНИТЬ', 'СОХРАНИТЬ'
], $accounts);
file_put_contents($accountsPath, $accounts);
file_put_contents($root . '/scripts/accounts.js', file_get_contents('/tmp/railway-cookie-accounts.js'));
fwrite(STDERR, "[cookie-only] profile forms and official transport guards installed\n");

// REMASK_PROFILE_COOKIE_REFRESH_V1: persist only Meta-issued same-actor session updates.

$profileContextPath = $root . '/ajax/pythonProfileContext.php';
$profileContextSource = file_get_contents($profileContextPath);
if (!is_string($profileContextSource) || substr_count($profileContextSource, '<?php') !== 1) {
    throw new RuntimeException('Python profile context boundary missing');
}
$cookieRefreshHandler = <<<'PROFILE_COOKIE_REFRESH'
if (strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET')) === 'POST') {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    $expectedKey = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
    $providedKey = trim((string)($_SERVER['HTTP_X_REMASK_INTERNAL_KEY'] ?? ''));
    if ($expectedKey === '' || $providedKey === '' || !hash_equals($expectedKey, $providedKey)) {
        http_response_code(401);
        echo json_encode(['ok'=>false,'error'=>'UNAUTHORIZED']);
        exit;
    }
    try {
        $raw = file_get_contents('php://input');
        if (!is_string($raw) || strlen($raw) > 2097152) throw new InvalidArgumentException();
        $input = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
        if (!is_array($input) || ($input['action'] ?? '') !== 'refresh_profile_cookies') {
            http_response_code(400);
            echo json_encode(['ok'=>false,'error'=>'INVALID_ACTION']);
            exit;
        }
        $profile = trim((string)($input['profile_id'] ?? ''));
        $actor = trim((string)($input['actor_id'] ?? ''));
        $expectedXs = (string)($input['expected_xs'] ?? '');
        $cookieInput = $input['cookies'] ?? null;
        if ($profile === '' || strlen($profile) > 255 || $actor === '' ||
            !ctype_digit($actor) || $expectedXs === '' || !is_array($cookieInput)) {
            throw new InvalidArgumentException();
        }
        require_once __DIR__ . '/../settings.php';
        require_once __DIR__ . '/../classes/FbAccount.php';
        require_once __DIR__ . '/../classes/AccountStoreFactory.php';
        require_once __DIR__ . '/../classes/RemaskCookieProfile.php';
        $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
        $account = $store->getAccountByName($profile);
        if (!$account) {
            http_response_code(404);
            echo json_encode(['ok'=>false,'error'=>'PROFILE_NOT_FOUND']);
            exit;
        }
        $toMap = static function ($value): array {
            if (!is_array($value)) return [];
            $map = [];
            if (!array_is_list($value)) {
                foreach ($value as $name => $cookieValue) {
                    if (is_array($cookieValue)) {
                        $name = $cookieValue['name'] ?? '';
                        $cookieValue = $cookieValue['value'] ?? null;
                    }
                    if (!is_string($name) && !is_int($name)) continue;
                    if (!is_string($cookieValue) && !is_int($cookieValue)) continue;
                    $name = trim((string)$name);
                    $cookieValue = (string)$cookieValue;
                    if ($name === '') continue;
                    if (isset($map[$name]) && $map[$name] !== $cookieValue) {
                        throw new InvalidArgumentException();
                    }
                    $map[$name] = $cookieValue;
                }
                return $map;
            }
            foreach ($value as $row) {
                if (!is_array($row) || !is_string($row['name'] ?? null) ||
                    (!is_string($row['value'] ?? null) && !is_int($row['value'] ?? null))) continue;
                $name = trim($row['name']);
                $cookieValue = (string)$row['value'];
                if ($name === '') continue;
                if (isset($map[$name]) && $map[$name] !== $cookieValue) {
                    throw new InvalidArgumentException();
                }
                $map[$name] = $cookieValue;
            }
            return $map;
        };
        $oldCookies = $toMap($account->cookies ?? []);
        if (($oldCookies['c_user'] ?? '') !== $actor ||
            !hash_equals((string)($oldCookies['xs'] ?? ''), $expectedXs)) {
            http_response_code(409);
            echo json_encode(['ok'=>false,'error'=>'PROFILE_SESSION_CHANGED']);
            exit;
        }
        $rows = RemaskCookieProfile::validate($cookieInput, $account->proxy ?? null, false, $profile);
        $newCookies = $toMap($rows);
        if (($newCookies['c_user'] ?? '') !== $actor || trim((string)($newCookies['xs'] ?? '')) === '') {
            throw new InvalidArgumentException();
        }
        $account->cookies = $newCookies;
        $store->addOrUpdateAccount($account);
        echo json_encode(['ok'=>true,'updated'=>true,'cookie_count'=>count($newCookies)]);
    } catch (Throwable $e) {
        http_response_code(400);
        echo json_encode(['ok'=>false,'error'=>'PROFILE_COOKIE_REFRESH_FAILED']);
    }
    exit;
}
PROFILE_COOKIE_REFRESH;
$profileContextSource = preg_replace('/<\?php/', '<?php' . "\n" . $cookieRefreshHandler . "\n", $profileContextSource, 1, $count);
if ($count !== 1) throw new RuntimeException('Python profile context POST boundary missing');
file_put_contents($profileContextPath, $profileContextSource);
