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
require_once __DIR__ . '/../classes/RemaskCookieProfile.php';
require_once __DIR__ . '/../classes/RemaskCookieTxt.php';
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
        'auth_mode' => 'cookies',
        'token_required' => false,
        'session_verified' => false,
        'proxy_configured' => $acc->proxy !== null,
        'session_ready' => rmx_pm_session_ready($acc),
        'cookie_count' => count((array)$acc->cookies),
        'direct_test_until' => RemaskCookieProfile::directTestUntil((string)$acc->name, (string)$acc->userId),
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

function rmx_pm_sequence_open(int $operation): array {
    $directory = dirname((string)ACCOUNTSFILENAME);
    if (!is_dir($directory) && !mkdir($directory, 0700, true)) throw new RuntimeException('PROFILE_NUMBER_STORAGE_UNAVAILABLE');
    $lock = fopen($directory . '/profile-sequence.lock', 'c');
    if ($lock === false) throw new RuntimeException('PROFILE_NUMBER_LOCK_UNAVAILABLE');
    $deadline = microtime(true) + 2.0;
    while (!flock($lock, $operation | LOCK_NB)) {
        if (microtime(true) >= $deadline) {
            fclose($lock);
            throw new RuntimeException('PROFILE_NUMBER_BUSY');
        }
        usleep(20000);
    }
    return [$lock, $directory . '/profile-sequence.json'];
}

function rmx_pm_sequence_last(array $profiles, string $path): int {
    $saved = json_decode((string)(@file_get_contents($path) ?: ''), true);
    $last = max(0, (int)($saved['last_number'] ?? 0));
    foreach ($profiles as $profile) {
        $name = (string)$profile->name;
        if (preg_match('/^[1-9]\d{0,14}$/', $name)) $last = max($last, (int)$name);
    }
    return $last;
}


function rmx_pm_sequence_save(array $profiles, string $path): void {
    $temporary = $path . '.tmp';
    $last = rmx_pm_sequence_last($profiles, $path);
    if (file_put_contents($temporary, json_encode(['last_number'=>$last], JSON_THROW_ON_ERROR)) === false
        || !rename($temporary, $path)) throw new RuntimeException('PROFILE_NUMBER_SAVE_FAILED');
}

function rmx_pm_proxy_choices(array $profiles): array {
    $groups = [];
    foreach ($profiles as $profile) {
        if (!$profile instanceof FbAccount || $profile->proxy === null) continue;
        // Credentials distinguish proxy sessions internally, but never leave
        // the server in the choice value, label, response or diagnostic.
        $key = json_encode($profile->proxy->toArray(), JSON_THROW_ON_ERROR);
        if (!isset($groups[$key])) $groups[$key] = ['source_profile'=>(string)$profile->name,
            'label'=>(string)$profile->proxy->type.'://'.(string)$profile->proxy->ip.':'.(int)$profile->proxy->port,
            'assigned_profiles'=>0];
        $groups[$key]['assigned_profiles']++;
    }
    $choices = array_values($groups);
    usort($choices, static fn($a,$b) => ($a['assigned_profiles'] <=> $b['assigned_profiles'])
        ?: strnatcmp($b['source_profile'],$a['source_profile']));
    return $choices;
}

function rmx_pm_txt_import(array $input, mixed $store, bool $preview): array {
    $text = $input['text'] ?? null;
    if (!is_string($text)) throw new InvalidArgumentException('TXT_REQUIRED: выберите TXT файл.');
    $records = RemaskCookieTxt::parse($text);
    $selection = $input['selected_lines'] ?? null;
    if ($selection !== null) {
        if (!is_array($selection) || count($selection) > RemaskCookieTxt::MAX_RECORDS) throw new InvalidArgumentException('TXT_SELECTION_INVALID');
        $selected = [];
        foreach ($selection as $line) {
            if (!is_int($line) || $line < 1) throw new InvalidArgumentException('TXT_SELECTION_INVALID');
            $selected[$line] = true;
        }
        if (!$preview) $records = array_values(array_filter($records, fn($r)=>isset($selected[$r['line']])));
    }
    if (!$preview && !$records) throw new InvalidArgumentException('TXT_SELECTION_EMPTY: нет выбранных записей.');
    $proxy = null;
    $sourceProfile = rmx_pm_find_scalar($input, ['source_profile']);
    $rawProxy = rmx_pm_find_scalar($input, ['proxy']);
    $enableDirect = !$preview && rmx_pm_find_bool($input, ['allow_direct_test'], false);
    $refreshExisting = !$preview && rmx_pm_find_bool($input, ['refresh_existing_sessions'], false);
    if ($enableDirect && $refreshExisting) throw new InvalidArgumentException('TXT_REFRESH_DIRECT_CONFLICT: обновление существующих сессий нельзя объединять с тестом без прокси.');
    if (!$preview && $sourceProfile !== '' && ($rawProxy !== '' || $enableDirect)) {
        throw new InvalidArgumentException('TXT_PROXY_SOURCE_CONFLICT: выберите один способ назначения прокси.');
    }
    if ($enableDirect && (count($records) !== 1 || $rawProxy !== '' || $sourceProfile !== '')) {
        throw new InvalidArgumentException('TXT_DIRECT_SINGLE_ACCOUNT_REQUIRED: выберите один аккаунт без прокси.');
    }
    // Recheck UID duplicates and allocate names within the same lock as manual create.
    [$lock, $sequencePath] = rmx_pm_sequence_open($preview ? LOCK_SH : LOCK_EX);
    try {
        $profiles = $store->deserialize();
        if (!$preview && $sourceProfile !== '') {
            $source = $store->getAccountByName($sourceProfile);
            if (!$source instanceof FbAccount || $source->proxy === null)
                throw new InvalidArgumentException('TXT_PROXY_SOURCE_UNAVAILABLE: сохранённый прокси недоступен.');
            $proxy = $source->proxy;
        }
        $known = [];
        foreach ($profiles as $profile) {
            $uid = RemaskCookieTxt::userId((array)$profile->cookies);
            if ($uid !== '') $known[$uid][] = (string)$profile->name;
        }
        foreach ($records as &$record) {
            if ($record['status'] !== 'ready' || !isset($known[$record['user_id']])) continue;
            $matches = array_values(array_unique($known[$record['user_id']]));
            $record['status'] = 'already_exists';
            $record['refreshable'] = count($matches) === 1;
            $record['profile_name'] = count($matches) === 1
                ? $matches[0] : 'Несколько профилей (' . count($matches) . ')';
            if (count($matches) !== 1) $record['error'] = 'AMBIGUOUS_EXISTING_PROFILE';
        }
        unset($record);
        $hasNewProfiles = count(array_filter($records, fn($r)=>$r['status'] === 'ready')) > 0;
        if (!$preview && $hasNewProfiles && !$enableDirect && $sourceProfile === '' && $proxy === null) {
            try {
                $raw = rmx_pm_find_scalar($input, ['proxy']);
                if ($raw === '') throw new InvalidArgumentException();
                $proxy = RemaskProxy::fromSemicolonString($raw);
                if ($proxy === null) throw new InvalidArgumentException();
            } catch (Throwable $e) { throw new InvalidArgumentException('TXT_PROXY_INVALID: укажите корректный прокси.'); }
        }
        $next = rmx_pm_sequence_last($profiles, $sequencePath) + 1;
        $result = []; $imported = 0; $refreshed = 0; $errors = 0; $skipped = 0; $ready = 0;
        foreach ($records as $record) {
            $refreshWriteFailed = false;
            if (!$preview && $refreshExisting && $record['status'] === 'already_exists'
                && !empty($record['refreshable'])) {
                $profileName = (string)$record['profile_name'];
                $existing = $store->getAccountByName($profileName);
                if (!$existing instanceof FbAccount
                    || RemaskCookieTxt::userId((array)$existing->cookies) !== (string)$record['user_id']) {
                    $record['status'] = 'error';
                    $record['error'] = 'EXISTING_PROFILE_CHANGED';
                } else {
                    try {
                        // Replace only browser cookies after an explicit user choice
                        // and exact actor match. Keep name, token, dtsg, and proxy.
                        $cookies = RemaskCookieProfile::validate($record['cookies'], $existing->proxy, false, $profileName);
                        if (RemaskCookieTxt::userId($cookies) !== (string)$record['user_id']) {
                            throw new InvalidArgumentException('FACEBOOK_ACTOR_MISMATCH');
                        }
                        $updated = new FbAccount(
                            (string)$existing->name,
                            (string)$existing->token,
                            json_encode($cookies, JSON_THROW_ON_ERROR),
                            $existing->dtsg,
                            $existing->proxy
                        );
                        $store->addOrUpdateAccount($updated);
                        $saved = $store->getAccountByName($profileName);
                        if (!$saved instanceof FbAccount
                            || RemaskCookieTxt::userId((array)$saved->cookies) !== (string)$record['user_id']) {
                            throw new RuntimeException('SESSION_REFRESH_SAVE_FAILED');
                        }
                        $record['status'] = 'session_refreshed';
                        $record['session_verified'] = false;
                        $refreshed++;
                    } catch (Throwable $e) {
                        $record['status'] = 'error';
                        $record['error'] = 'SESSION_REFRESH_SAVE_FAILED';
                        $refreshWriteFailed = true;
                    }
                }
            }
            if ($record['status'] === 'ready') {
                if ($preview) {
                    $record['profile_name'] = (string)$next++;
                    $ready++;
                } else {
                    $name = (string)$next;
                    try {
                        $cookies = RemaskCookieProfile::validate($record['cookies'], $proxy, !$enableDirect);
                        $account = new FbAccount($name, '', json_encode($cookies, JSON_THROW_ON_ERROR), null, $proxy);
                        $store->addOrUpdateAccount($account);
                        $saved = $store->getAccountByName($name);
                        if (!$saved instanceof FbAccount || RemaskCookieTxt::userId((array)$saved->cookies) !== $record['user_id']) throw new RuntimeException();
                        if ($enableDirect) RemaskCookieProfile::saveDirectTest($name, $record['user_id'], time() + 7200);
                        rmx_pm_sequence_save($store->deserialize(), $sequencePath);
                        $record['status'] = 'imported';
                        $record['profile_name'] = $name;
                        $known[$record['user_id']] = $name;
                        $imported++; $next++;
                    } catch (Throwable $e) {
                        // Never echo storage/proxy exception text which may contain credentials.
                        $record['status'] = 'error';
                        $record['error'] = 'TXT_SAVE_FAILED';
                        $result[] = RemaskCookieTxt::safe($record);
                        // Partial result is explicit; do not continue after an uncertain write.
                        $errors++;
                        break;
                    }
                }
            } elseif ($record['status'] === 'session_refreshed') {
                // The explicit refresh was saved; profile metadata stayed intact.
            } elseif ($record['status'] === 'error') $errors++;
            else $skipped++;
            $result[] = RemaskCookieTxt::safe($record);
            if ($refreshWriteFailed) break;
        }
        return ['ok'=>true,'success'=>true,'preview'=>$preview,'records'=>$result,'total'=>count($records),
            'ready'=>$ready,'imported'=>$imported,'refreshed'=>$refreshed,'skipped'=>$skipped,'errors'=>$errors,
            'processed'=>count($result),'session_verified'=>false,'next_number'=>$next,
            'count'=>count($store->deserialize()),
            'proxy_choices'=>$preview ? rmx_pm_proxy_choices($profiles) : []];
    } finally { fclose($lock); }
}

try {
    $input = rmx_pm_input();
    $action = strtolower(rmx_pm_find_scalar($input, ['action','cmd','op','mode']));
    if ($action === 'update_profile') $action = 'save';
    $hasSaveFields = rmx_pm_find_scalar($input, ['name','profile_name','label']) !== '';
    if ($action === '') $action = $hasSaveFields ? 'save' : 'list';
    $store = AccountStoreFactory::create(ACCOUNTSFILENAME);

    if (in_array($action, ['import_preview','import_txt'], true)) {
        rmx_pm_out(rmx_pm_txt_import($input, $store, $action === 'import_preview'));
    }

    if ($action === 'next_number') {
        [$sequenceLock, $sequencePath] = rmx_pm_sequence_open(LOCK_SH);
        $next = rmx_pm_sequence_last($store->deserialize(), $sequencePath) + 1;
        fclose($sequenceLock);
        rmx_pm_out(['ok'=>true,'success'=>true,'next_number'=>$next,
            'direct_test_until'=>RemaskCookieProfile::directTestUntil((string)$next)]);
    }

    if (in_array($action, ['list','get','load','all'], true)) {
        $profiles = array_map('rmx_pm_safe', $store->deserialize());
        rmx_pm_out(['ok'=>true,'success'=>true,'count'=>count($profiles),'profiles'=>$profiles,'accounts'=>$profiles]);
    }
    if (in_array($action, ['delete','remove'], true)) {
        [$mutationLock] = rmx_pm_sequence_open(LOCK_EX);
        $name = rmx_pm_find_scalar($input, ['name','profile_name','profile','id','fb_id','account_id']);
        if ($name === '') throw new InvalidArgumentException('Profile name is required.');
        $remaining = $store->deleteAccountByName($name);
        rmx_pm_out(['ok'=>true,'success'=>true,'deleted'=>true,'count'=>count($remaining)]);
    }

    // REMASK_SESSION_ONLY_UPDATE_V1
    if (in_array($action, ['session_update','update_session','refresh_session'], true)) {
        [$mutationLock] = rmx_pm_sequence_open(LOCK_EX);
        $name = rmx_pm_find_scalar($input, ['name','profile_name','profile','id','fb_id','account_id']);
        if ($name === '') throw new InvalidArgumentException('Profile name is required.');

        $existing = $store->getAccountByName($name);
        if (!$existing instanceof FbAccount) {
            throw new RuntimeException('PROFILE_NOT_FOUND');
        }

        $cookiesProvided = false;
        $incomingCookies = rmx_pm_find_cookies($input, $cookiesProvided);
        if (!$cookiesProvided || !is_array($incomingCookies) || $incomingCookies === []) {
            throw new InvalidArgumentException('Fresh Facebook Cookies JSON is required.');
        }

        $incomingCookies = RemaskCookieProfile::validate($incomingCookies, $existing->proxy, true, $name);
        $existingUserId = RemaskCookieTxt::userId((array)$existing->cookies);
        $incomingUserId = RemaskCookieTxt::userId($incomingCookies);
        if ($existingUserId !== '' && $incomingUserId !== $existingUserId) {
            throw new InvalidArgumentException('FACEBOOK_ACTOR_MISMATCH: session update must keep the same Facebook account.');
        }

        $hasCUser = false;
        $hasXs = false;
        foreach ((array)$incomingCookies as $cookie) {
            if (!is_array($cookie)) continue;
            $cookieName = trim((string)($cookie['name'] ?? ''));
            $cookieValue = trim((string)($cookie['value'] ?? ''));
            if ($cookieName === 'c_user' && $cookieValue !== '') $hasCUser = true;
            if ($cookieName === 'xs' && $cookieValue !== '') $hasXs = true;
        }
        if (!$hasCUser || !$hasXs) {
            throw new InvalidArgumentException('Fresh session must contain c_user and xs.');
        }

        $accountsPath = (string)ACCOUNTSFILENAME;


        $cookieJson = json_encode(
            array_values($incomingCookies),
            JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR
        );
        $dtsgInput = rmx_pm_find_scalar($input, ['dtsg','fb_dtsg','fbDtsg']);
        $updated = new FbAccount(
            (string)$existing->name,
            (string)$existing->token,
            $cookieJson,
            $dtsgInput !== '' ? $dtsgInput : null,
            $existing->proxy
        );
        $store->addOrUpdateAccount($updated);

        // REMASK_PRIVATE_SESSION_UPDATE_V1
        // Updating browser cookies must not touch the Ads Manager/Graph token.
        $tokenRefreshed = false;
        $refreshError = '';

        $saved = $store->getAccountByName($name);
        if (!$saved instanceof FbAccount) {
            throw new RuntimeException('PROFILE_SESSION_SAVE_FAILED');
        }

        rmx_pm_out([
            'ok'=>true,
            'success'=>true,
            'session_updated'=>true,
            'token_refreshed'=>$tokenRefreshed,
            'token_refresh_error'=>$refreshError,
            'profile'=>rmx_pm_safe($saved),
        ]);
    }

    if (!in_array($action, ['create','save','upsert','add'], true)) {
        rmx_pm_out(['ok'=>false,'success'=>false,'error'=>'UNSUPPORTED_ACTION','message'=>'Unsupported profile action.'], 400);
    }

    $name = rmx_pm_find_scalar($input, ['name','profile_name','profile','label','fb_id','profile_id','account_id']);
    $creating = in_array($action, ['create','add'], true);
    $sequenceLock = null;
    $sequencePath = '';
    {
        // Serialize allocation and save, including named legacy creates.
        [$sequenceLock, $sequencePath] = rmx_pm_sequence_open(LOCK_EX);
        if ($creating && rmx_pm_find_bool($input, ['auto_number'], false)) {
            $name = (string)(rmx_pm_sequence_last($store->deserialize(), $sequencePath) + 1);
        }
    }
    if ($name === '') throw new InvalidArgumentException('Название профиля обязательно.');
    $existing = $store->getAccountByName($name);
    if ($creating && $existing instanceof FbAccount) throw new InvalidArgumentException('PROFILE_ALREADY_EXISTS: создание не может перезаписать существующий профиль.');

    // Retain historical stored data, but never import/use an Ads Manager token.
    $token = (string)($existing?->token ?? '');

    // REMASK_SESSION_CLEAR_EXPLICIT_V2
    // A normal profile save must never clear a working Facebook session.
    // Session deletion is intentionally disabled on save/create paths.
    $clearSession = false;
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

    $enableDirect = rmx_pm_find_bool($input, ['allow_direct_test'], false);
    if ($enableDirect && $proxy !== null) throw new InvalidArgumentException('Для режима без прокси удалите текущий прокси.');
    $cookies = RemaskCookieProfile::validate($cookies, $proxy, !$clearProxy && !$enableDirect, $name);

    $accountsPath = (string)ACCOUNTSFILENAME;


    $cookieJson = json_encode(array_values($cookies), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
    $account = new FbAccount($name, $token, $cookieJson, $dtsg, $proxy);
    $store->addOrUpdateAccount($account);
    $saved = $store->getAccountByName($name);
    if (!$saved instanceof FbAccount) throw new RuntimeException('Профиль не сохранился.');
    if ($enableDirect) {
        RemaskCookieProfile::saveDirectTest($name, (string)$saved->userId, time() + 7200);
    } elseif ($proxy !== null && RemaskCookieProfile::directTestUntil($name, (string)$saved->userId)) {
        RemaskCookieProfile::saveDirectTest($name, (string)$saved->userId, 0);
    }
    if ($sequenceLock !== null) {
        $last = rmx_pm_sequence_last($store->deserialize(), $sequencePath);
        $temporary = $sequencePath . '.tmp';
        if (file_put_contents($temporary, json_encode(['last_number'=>$last], JSON_THROW_ON_ERROR)) === false
            || !rename($temporary, $sequencePath)) throw new RuntimeException('PROFILE_NUMBER_SAVE_FAILED');
        fclose($sequenceLock);
    }

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
