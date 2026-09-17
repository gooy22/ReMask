<?php
declare(strict_types=1);

/**
 * Recover only missing Facebook session context (cookies/dtsg) from historical
 * accounts.json backups on the persistent Railway volume. Tokens and proxies
 * from the current canonical AccountStore remain authoritative.
 *
 * Safety: cookie/token values are never printed.
 */
require_once __DIR__ . '/settings.php';
require_once __DIR__ . '/classes/FbAccount.php';
require_once __DIR__ . '/classes/AccountStoreFactory.php';

function rmx_recover_records(array $root): array {
    if (function_exists('array_is_list') ? array_is_list($root) : array_keys($root) === range(0, count($root) - 1)) {
        return $root;
    }
    foreach (['accounts', 'profiles', 'items', 'data'] as $key) {
        if (isset($root[$key]) && is_array($root[$key])) return array_values($root[$key]);
    }
    return [];
}

function rmx_recover_scalar(array $row, array $keys): string {
    foreach ($keys as $key) {
        if (isset($row[$key]) && is_scalar($row[$key])) return trim((string)$row[$key]);
    }
    return '';
}

function rmx_recover_cookies(array $row): array {
    foreach (['cookies', 'cookie', 'cookies_json', 'cookie_json'] as $key) {
        if (!array_key_exists($key, $row)) continue;
        $value = $row[$key];
        if (is_array($value)) return array_values($value);
        if (is_string($value) && trim($value) !== '') {
            $decoded = json_decode($value, true);
            if (is_array($decoded)) return array_values($decoded);
        }
    }
    return [];
}

function rmx_recover_cookie_names(array $cookies): array {
    $names = [];
    foreach ($cookies as $cookie) {
        if (!is_array($cookie)) continue;
        $name = trim((string)($cookie['name'] ?? ''));
        if ($name !== '') $names[$name] = true;
    }
    return $names;
}

function rmx_recover_token_hash(string $token): string {
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

    $candidateRows = [];
    foreach (array_keys($backupFiles) as $file) {
        $raw = @file_get_contents($file);
        if (!is_string($raw) || trim($raw) === '') continue;
        $json = json_decode($raw, true);
        if (!is_array($json)) continue;
        foreach (rmx_recover_records($json) as $row) {
            if (!is_array($row)) continue;
            $token = rmx_recover_scalar($row, ['token','access_token','accessToken','fb_token','meta_token']);
            $hash = rmx_recover_token_hash($token);
            if ($hash === '') continue;
            $cookies = rmx_recover_cookies($row);
            if ($cookies === []) continue;
            $names = rmx_recover_cookie_names($cookies);
            if (!isset($names['c_user']) || !isset($names['xs'])) continue;
            if (!isset($candidateRows[$hash])) {
                $candidateRows[$hash] = [
                    'cookies' => $cookies,
                    'dtsg' => rmx_recover_scalar($row, ['dtsg','fb_dtsg','fbDtsg']),
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
        $existingNames = rmx_recover_cookie_names((array)$account->cookies);
        if (isset($existingNames['c_user']) && isset($existingNames['xs'])) continue;

        $hash = rmx_recover_token_hash((string)$account->token);
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
    fwrite(STDERR, '[session-recovery] backups=' . count($backupFiles)
        . ' candidates=' . count($candidateRows)
        . ' recoverable=' . $recoverable
        . ' restored=' . $restored
        . ($safeSources ? ' sources=' . implode(',', $safeSources) : '')
        . "\n");
} catch (Throwable $e) {
    fwrite(STDERR, '[session-recovery] failed=' . get_class($e) . ':' . substr($e->getMessage(), 0, 240) . "\n");
}
