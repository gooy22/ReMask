<?php
/**
 * Internal profile-context resolver for the ReMask Python worker.
 *
 * Security contract:
 * - requires REMASK_INTERNAL_KEY and X-Remask-Internal-Key;
 * - returns profile cookies, saved access token, proxy URL and User-Agent only
 *   to the authenticated internal worker resolver;
 * - never exposes this endpoint to browser UI without REMASK_INTERNAL_KEY;
 * - intended for localhost / Railway private networking only.
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
require_once __DIR__ . '/../classes/MetaEndpoint.php';

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

function rmx_py_saved_pages(FbAccount $account): array {
    $vars = get_object_vars($account);
    $candidates = [];

    foreach (['pages','fan_pages','fanPages'] as $key) {
        if (isset($vars[$key]) && is_array($vars[$key])) {
            $candidates[] = $vars[$key];
        }
    }

    foreach (['meta_hierarchy','metaHierarchy'] as $key) {
        $node = $vars[$key] ?? null;
        if (is_array($node) && isset($node['pages']) && is_array($node['pages'])) {
            $candidates[] = $node['pages'];
        }
    }

    $pages = [];
    $seen = [];
    foreach ($candidates as $rows) {
        foreach ($rows as $row) {
            if (!is_array($row)) continue;
            $id = trim((string)($row['id'] ?? $row['page_id'] ?? ''));
            if ($id === '' || !ctype_digit($id) || isset($seen[$id])) continue;

            $name = trim((string)($row['name'] ?? $row['page_name'] ?? $id));
            $tasks = [];
            foreach ((array)($row['tasks'] ?? []) as $task) {
                if (is_scalar($task)) $tasks[] = (string)$task;
            }

            $businessId = '';
            $business = $row['business'] ?? null;
            if (is_array($business)) {
                $businessId = trim((string)($business['id'] ?? ''));
            } elseif (is_scalar($business)) {
                $businessId = trim((string)$business);
            }

            $pages[] = [
                'id' => $id,
                'name' => $name !== '' ? $name : $id,
                'category' => trim((string)($row['category'] ?? '')),
                'tasks' => $tasks,
                'business_id' => $businessId,
                'is_owned' => array_key_exists('is_owned', $row) ? (bool)$row['is_owned'] : null,
            ];
            $seen[$id] = true;
        }
    }

    return $pages;
}

function rmx_py_profile_pages(FbAccount $account, string $profile): array {
    $pages = rmx_py_saved_pages($account);
    $seen = [];
    foreach ($pages as $row) {
        $id = trim((string)($row['id'] ?? ''));
        if ($id !== '') $seen[$id] = true;
    }

    try {
        $cached = MetaEndpoint::peekCachedAsset($profile, 'pages', '');
        foreach ((array)($cached['data'] ?? []) as $row) {
            if (!is_array($row)) continue;
            $id = trim((string)($row['id'] ?? ''));
            if ($id === '' || !ctype_digit($id) || isset($seen[$id])) continue;

            $businessId = '';
            $business = $row['business'] ?? null;
            if (is_array($business)) {
                $businessId = trim((string)($business['id'] ?? ''));
            } elseif (is_scalar($business)) {
                $businessId = trim((string)$business);
            }

            $tasks = [];
            foreach ((array)($row['tasks'] ?? []) as $task) {
                if (is_scalar($task)) $tasks[] = (string)$task;
            }

            $pages[] = [
                'id' => $id,
                'name' => trim((string)($row['name'] ?? $id)) ?: $id,
                'category' => trim((string)($row['category'] ?? '')),
                'tasks' => $tasks,
                'business_id' => $businessId,
                'is_owned' => array_key_exists('is_owned', $row) ? (bool)$row['is_owned'] : null,
            ];
            $seen[$id] = true;
        }
    } catch (Throwable $e) {
        error_log(
            '[python-profile-context] Page cache lookup failed profile_hash='
            . substr(hash('sha256', $profile), 0, 12)
            . ' error=' . get_class($e)
        );
    }

    usort($pages, static function(array $a, array $b): int {
        $aBound = trim((string)($a['business_id'] ?? '')) !== '' ? 1 : 0;
        $bBound = trim((string)($b['business_id'] ?? '')) !== '' ? 1 : 0;
        if ($aBound !== $bBound) return $aBound <=> $bBound;
        return strcasecmp((string)($a['name'] ?? ''), (string)($b['name'] ?? ''));
    });

    return $pages;
}

function rmx_py_public_identity(FbAccount $account, string $profile): array {
    $vars = get_object_vars($account);

    $pick = static function(array $names) use ($vars): string {
        foreach ($names as $name) {
            if (!array_key_exists($name, $vars)) continue;
            $value = $vars[$name];
            if (!is_scalar($value)) continue;
            $text = trim((string)$value);
            if ($text !== '') return $text;
        }
        return '';
    };

    $email = $pick(['email','user_email','mail','login','username']);
    if ($email !== '' && filter_var($email, FILTER_VALIDATE_EMAIL) === false) {
        $email = '';
    }
    if ($email === '' && filter_var($profile, FILTER_VALIDATE_EMAIL) !== false) {
        $email = $profile;
    }

    $displayName = $pick(['full_name','display_name','fb_name','name']);
    if ($displayName === '') $displayName = $profile;

    $firstName = $pick(['first_name','firstname','firstName']);
    $lastName = $pick(['last_name','lastname','lastName']);

    if ($firstName === '' || $lastName === '') {
        $parts = preg_split('/\s+/u', trim($displayName)) ?: [];
        $parts = array_values(array_filter($parts, static fn($v): bool => trim((string)$v) !== ''));
        if ($firstName === '' && count($parts) >= 1) $firstName = (string)$parts[0];
        if ($lastName === '' && count($parts) >= 2) $lastName = (string)$parts[count($parts)-1];
    }

    return [
        'display_name' => $displayName,
        'email' => $email,
        'first_name' => $firstName,
        'last_name' => $lastName,
    ];
}

try {
    $expected = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
    if ($expected === '') rmx_py_out(['ok'=>false,'error'=>'INTERNAL_KEY_NOT_CONFIGURED'], 503);

    $provided = rmx_py_header('X-Remask-Internal-Key');
    if ($provided === '' || !hash_equals($expected, $provided)) {
        rmx_py_out(['ok'=>false,'error'=>'UNAUTHORIZED'], 401);
    }

    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);
    $action = strtolower(trim((string)($_GET['action'] ?? $_POST['action'] ?? 'resolve')));

    if ($action === 'list') {
        $profiles = [];
        foreach ($store->deserialize() as $candidate) {
            if (!$candidate instanceof FbAccount) continue;
            $profiles[] = [
                'profile_id' => $candidate->name,
                'proxy_configured' => $candidate->proxy !== null,
                'cookies_present' => is_array($candidate->cookies) && count($candidate->cookies) > 0,
                'token_present' => trim((string)$candidate->token) !== '',
            ];
        }
        rmx_py_out(['ok'=>true,'profiles'=>$profiles,'count'=>count($profiles)]);
    }

    $profile = trim((string)($_GET['profile_id'] ?? $_POST['profile_id'] ?? ''));
    if ($profile === '') rmx_py_out(['ok'=>false,'error'=>'PROFILE_REQUIRED'], 400);

    $account = $store->getAccountByName($profile);
    if (!$account instanceof FbAccount) rmx_py_out(['ok'=>false,'error'=>'PROFILE_NOT_FOUND'], 404);

    $ua = trim((string)(getenv('REMASK_PYTHON_USER_AGENT') ?: ''));
    if ($ua === '') {
        $ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
            . '(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36';
    }

    $identity = rmx_py_public_identity($account, $profile);

    rmx_py_out([
        'ok' => true,
        'profile_id' => $profile,
        'cookies' => rmx_py_cookie_map($account->cookies),
        'access_token' => trim((string)$account->token),
        'proxy' => rmx_py_proxy_url($account->proxy),
        'user_agent' => $ua,
        'display_name' => $identity['display_name'],
        'email' => $identity['email'],
        'first_name' => $identity['first_name'],
        'last_name' => $identity['last_name'],
        'pages' => rmx_py_profile_pages($account, $profile),
    ]);
} catch (Throwable $e) {
    error_log('[python-profile-context] ' . get_class($e) . ': ' . $e->getMessage());
    rmx_py_out(['ok'=>false,'error'=>'PROFILE_CONTEXT_FAILED'], 500);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[python-profile-context] internal profile resolver installed\n");
