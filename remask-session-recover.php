<?php
declare(strict_types=1);

/**
 * Recover only missing Facebook session context from historical files on the
 * persistent Railway volume. Current tokens/proxies remain authoritative.
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
    foreach (['accounts','profiles','items','data'] as $key) {
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
function rmx_cookie_value(array $cookies, string $wanted): string {
    foreach ($cookies as $cookie) {
        if (!is_array($cookie)) continue;
        if ((string)($cookie['name'] ?? '') === $wanted) return trim((string)($cookie['value'] ?? ''));
    }
    return '';
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
    $dataDir = dirname($accountsFile);
    $store = AccountStoreFactory::create($accountsFile);
    $current = $store->deserialize();

    $currentByToken = [];
    $currentByProfileId = [];
    foreach ($current as $account) {
        if (!$account instanceof FbAccount) continue;
        $hash = rmx_token_hash((string)$account->token);
        if ($hash !== '') $currentByToken[$hash] = (string)$account->name;
        foreach ([(string)$account->name, (string)$account->userId] as $id) {
            $id = trim($id);
            if ($id !== '') $currentByProfileId[$id] = (string)$account->name;
        }
    }

    // Search every small regular file on the persistent data volume. JSON-only
    // extraction prevents interpreting arbitrary logs/media as credentials.
    $sourceFiles = [];
    $iter = new RecursiveIteratorIterator(
        new RecursiveDirectoryIterator($dataDir, FilesystemIterator::SKIP_DOTS),
        RecursiveIteratorIterator::LEAVES_ONLY
    );
    foreach ($iter as $info) {
        if (!$info instanceof SplFileInfo || !$info->isFile()) continue;
        $path = $info->getPathname();
        $size = $info->getSize();
        if ($size <= 0 || $size > 5 * 1024 * 1024) continue;
        // Never use our emergency pre-recovery snapshots as a preferred source.
        if (str_contains(basename($path), '.pre-session-recovery-')) continue;
        $sourceFiles[$path] = $info->getMTime();
    }
    arsort($sourceFiles, SORT_NUMERIC);

    $candidateByAccount = [];
    $filesScanned = 0;
    $jsonFiles = 0;
    $rowsScanned = 0;
    $rowsWithToken = 0;
    $sameTokenRows = 0;
    $cookieJarsSeen = 0;
    $profileCookieMatches = 0;
    $rawSessionMarkers = 0;

    foreach (array_keys($sourceFiles) as $file) {
        $filesScanned++;
        $raw = @file_get_contents($file);
        if (!is_string($raw) || trim($raw) === '') continue;
        if (str_contains($raw, 'c_user') && str_contains($raw, 'xs')) $rawSessionMarkers++;
        $json = json_decode($raw, true);
        if (!is_array($json)) continue;
        $jsonFiles++;

        foreach (rmx_records($json) as $row) {
            if (!is_array($row)) continue;
            $rowsScanned++;
            $token = rmx_find_scalar_recursive($row, ['token','access_token','accessToken','fb_token','meta_token']);
            $hash = rmx_token_hash($token);
            if ($hash !== '') $rowsWithToken++;

            $cookies = rmx_find_cookie_jar_recursive($row);
            if ($cookies === []) continue;
            $cookieJarsSeen++;

            $matchedAccount = null;
            $matchKind = null;
            if ($hash !== '' && isset($currentByToken[$hash])) {
                $sameTokenRows++;
                $matchedAccount = $currentByToken[$hash];
                $matchKind = 'token';
            } else {
                $cUser = rmx_cookie_value($cookies, 'c_user');
                if ($cUser !== '' && isset($currentByProfileId[$cUser])) {
                    $profileCookieMatches++;
                    $matchedAccount = $currentByProfileId[$cUser];
                    $matchKind = 'c_user';
                }
            }
            if ($matchedAccount === null || isset($candidateByAccount[$matchedAccount])) continue;

            $candidateByAccount[$matchedAccount] = [
                'cookies' => $cookies,
                'dtsg' => rmx_find_scalar_recursive($row, ['dtsg','fb_dtsg','fbDtsg']),
                'source' => basename($file),
                'match' => $matchKind,
            ];
        }
    }

    $recoverable = 0;
    $restored = 0;
    $sources = [];
    $matchKinds = [];
    foreach ($current as $account) {
        if (!$account instanceof FbAccount) continue;
        $existingNames = rmx_cookie_names((array)$account->cookies);
        if (isset($existingNames['c_user']) && isset($existingNames['xs'])) continue;
        $name = (string)$account->name;
        if (!isset($candidateByAccount[$name])) continue;
        $recoverable++;
        $candidate = $candidateByAccount[$name];

        if ($restored === 0 && is_file($accountsFile)) {
            @copy($accountsFile, $accountsFile . '.pre-session-recovery-' . gmdate('YmdHis'));
        }
        $cookieJson = json_encode($candidate['cookies'], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR);
        $dtsg = trim((string)$candidate['dtsg']);
        $replacement = new FbAccount(
            $name,
            (string)$account->token,
            $cookieJson,
            $dtsg !== '' ? $dtsg : null,
            $account->proxy
        );
        $store->addOrUpdateAccount($replacement);
        $restored++;
        $sources[(string)$candidate['source']] = true;
        $matchKinds[(string)$candidate['match']] = true;
    }

    fwrite(STDERR,
        '[session-recovery] files=' . $filesScanned
        . ' json=' . $jsonFiles
        . ' raw_session_markers=' . $rawSessionMarkers
        . ' rows=' . $rowsScanned
        . ' token_rows=' . $rowsWithToken
        . ' same_token_rows=' . $sameTokenRows
        . ' cookie_jars=' . $cookieJarsSeen
        . ' c_user_matches=' . $profileCookieMatches
        . ' candidates=' . count($candidateByAccount)
        . ' recoverable=' . $recoverable
        . ' restored=' . $restored
        . ($matchKinds ? ' match=' . implode(',', array_keys($matchKinds)) : '')
        . ($sources ? ' sources=' . implode(',', array_slice(array_keys($sources), 0, 5)) : '')
        . "\n"
    );
} catch (Throwable $e) {
    fwrite(STDERR, '[session-recovery] failed=' . get_class($e) . ':' . substr($e->getMessage(), 0, 240) . "\n");
}
