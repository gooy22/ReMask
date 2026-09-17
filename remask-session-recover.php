<?php
declare(strict_types=1);

/**
 * Recover only missing Facebook session context (cookies/dtsg) from historical
 * accounts.json backups on the persistent Railway volume. Tokens and proxies
 * from the current canonical AccountStore remain authoritative.
 * Cookie/token values are never printed.
 */
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/classes/FbAccount.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';

function rmx_is_list(array $value): bool {
    return function_exists('array_is_list') ? array_is_list($value) : array_keys($value) === range(0, count($value) - 1);
}

function rmx_records(array $root): array {
    if (rmx_is_list($root)) return $root;
    foreach (['accounts', 'profiles', 'items', 'data'] as $key) {
        if (isset($root[$key]) && is_array($root[$key])) return array_values($root[$key]);
    }
    return [$root];
}

function rmx_decode_jsonish(mixed $value): mixed {
    if (!is_string($value)) return $value;
    $trim = trim($value);
    if ($trim === '' || (!str_starts_with($trim, '[') && !str_starts_with($trim, '{'))) return $value;
    $decoded = json_decode($trim, true);
    return is_array($decoded) ? $decoded : $value;
}

function rmx_find_scalar_recursive(mixed $node, array $keys, int $depth = 0): string {
    if ($depth > 8) return '';
    $node = rmx_decode_jsonish($node);
    if (!is_array($node)) return '';
    foreach ($keys as $key) {
        if (array_key_exists($key, $node) && is_scalar($node[$key])) {
            $value = trim((string)$node[$key]);
            if ($value !== '') return $value;
        }
    }
    foreach ($node as $child) {
        if (!is_array($child) && !is_string($child)) continue;
        $found = rmx_find_scalar_recursive($child, $keys, $depth + 1);
        if ($found !== '') return $found;
    }
    return '';
}

function rmx_cookie_names(array $cookies): array {
    $names = [];
    foreach ($cookies as $cookie) {
        if (!is_array($cookie)) continue;
        $name = trim((string)($cookie['name'] ?? ''));
        if ($name !== '') $names[$name] = true;
    }
    return $names;
}

function rmx_looks_like_cookie_jar(array $node): bool {
    if ($node === [] || !rmx_is_list($node)) return false;
    foreach ($node as $item) {
        if (is_array($item) && isset($item['name']) && array_key_exists('value', $item)) return true;
    }
    return false;
}

function rmx_find_cookie_jar_recursive(mixed $node, int $depth = 0): array {
    if ($depth > 10) return [];
    $node = rmx_decode_jsonish($node);
    if (!is_array($node)) return [];
    if (rmx_looks_like_cookie_jar($node)) {
        $names = rmx_cookie_names($node);
        if (isset($names['c_user']) && isset($names['xs'])) return array_values($node);
    }
    foreach (['cookies','cookie','cookies_json','cookie_json','cookiesData','cookieData','session','session_data'] as $key) {
        if (!array_key_exists($key, $node)) continue;
        $found = rmx_find_cookie_jar_recursive($node[$key], $depth + 1);
        if ($found !== []) return $found;
    }
    foreach ($node as $child) {
        if (!is_array($child) && !is_string($child)) continue;
        $found = rmx_find_cookie_jar_recursive($child, $depth + 1);
        if ($found !== []) return $found;
    }
    return [];
}

function rmx_token_hash(string $token): string {
    $token = trim($token);
    return $token === '' ? '' : hash('sha256', $token);
}

try {
    $accountsFile = (string)(getenv('REMASK_ACCOUNTS_FILE') ?: ACCOUNTSFILENAME);
    $store = AccountStoreFactory::create($accountsFile);
    $current = $store->deserialize();

    $patterns = [
        $accountsFile . '.bak.*',
        $accountsFile . '.bak',
        $accountsFile . '.backup*',
        dirname($accountsFile) . '/accounts*.bak*',
    ];
    $backupFiles = [];
    foreach ($patterns as $pattern) {
        foreach ((array)glob($pattern) as $file) {
            if (is_file($file) && realpath($file) !== realpath($accountsFile)) $backupFiles[$file] = @filemtime($file) ?: 0;
        }
    }
    arsort($backupFiles, SORT_NUMERIC);

    $currentHashes = [];
    foreach ($current as $account) {
        if ($account instanceof FbAccount) {
            $hash = rmx_token_hash((string)$account->token);
            if ($hash !== '') $currentHashes[$hash] = true;
        }
    }

    $candidateRows = [];
    $rowsScanned = 0;
    $rowsWithToken = 0;
    $sameTokenRows = 0;
    $cookieJarsSeen = 0;
    foreach (array_keys($backupFiles) as $file) {
        $raw = @file_get_contents($file);
        if (!is_string($raw) || trim($raw) === '') continue;
        $json = json_decode($raw, true);
        if (!is_array($json)) continue;
        foreach (rmx_records($json) as $row) {
            if (!is_array($row)) continue;
            $rowsScanned++;
            $token = rmx_find_scalar_recursive($row, ['token','access_token','accessToken','fb_token','meta_token']);
            $hash = rmx_token_hash($token);
            if ($hash !== '') $rowsWithToken++;
            if ($hash !== '' && isset($currentHashes[$hash])) $sameTokenRows++;

            $cookies = rmx_find_cookie_jar_recursive($row);
            if ($cookies !== []) $cookieJarsSeen++;
            if ($hash === '' || $cookies === []) continue;

            if (!isset($candidateRows[$hash])) {
                $candidateRows[$hash] = [
                    'cookies' => $cookies,
                    'dtsg' => rmx_find_scalar_recursive($row, ['dtsg','fb_dtsg','fbDtsg']),
                    'source' => basename($file),
                ];
            }
        }
    }

    $recoverable = 0;
    $restored = 0;
    $sources = [];
    foreach ($current as $account) {
        if (!$account instanceof FbAccount) continue;
        $existingNames = rmx_cookie_names((array)$account->cookies);
        if (isset($existingNames['c_user']) && isset($existingNames['xs'])) continue;

        $hash = rmx_token_hash((string)$account->token);
        if ($hash === '' || !isset($candidateRows[$hash])) continue;
        $recoverable++;
        $candidate = $candidateRows[$hash];

        if ($restored === 0 && is_file($accountsFile)) {
            @copy($accountsFile, $accountsFile . '.pre-session-recovery-' . gmdate('YmdHis'));
        }

        $cookieJson = json_encode($candidate['cookies'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $dtsg = trim((string)$candidate['dtsg']);
        $replacement = new FbAccount(
            (string)$account->name,
            (string)$account->token,
            $cookieJson,
            $dtsg !== '' ? $dtsg : null,
            $account->proxy
        );
        $store->addOrUpdateAccount($replacement);
        $restored++;
        $sources[(string)$candidate['source']] = true;
    }

    $safeSources = array_slice(array_keys($sources), 0, 5);
    fwrite(STDERR,
        '[session-recovery] backups=' . count($backupFiles)
        . ' rows=' . $rowsScanned
        . ' token_rows=' . $rowsWithToken
        . ' same_token_rows=' . $sameTokenRows
        . ' cookie_jars=' . $cookieJarsSeen
        . ' candidates=' . count($candidateRows)
        . ' recoverable=' . $recoverable
        . ' restored=' . $restored
        . ($safeSources ? ' sources=' . implode(',', $safeSources) : '')
        . "\n"
    );
} catch (Throwable $e) {
    fwrite(STDERR, '[session-recovery] failed=' . get_class($e) . ':' . substr($e->getMessage(), 0, 240) . "\n");
}
