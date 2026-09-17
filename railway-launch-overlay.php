<?php
/**
 * Railway build overlay for the clean-preview runtime.
 * The normal unified patch may not match scripts/launch.js from this runtime,
 * so apply the small Launch tracking change with tolerant string/regex edits.
 */

function replace_once(string &$text, string $search, string $replace, string $label): void
{
    $count = 0;
    $text = str_replace($search, $replace, $text, $count);
    if ($count > 0) {
        fwrite(STDERR, "[remask overlay] {$label}: {$count}\n");
    }
}

$root = '/var/www/html';
$launchJsPath = $root . '/scripts/launch.js';
$launchPhpPath = $root . '/launch.php';

if (is_file($launchJsPath)) {
    $js = file_get_contents($launchJsPath);
    if ($js === false) {
        fwrite(STDERR, "[remask overlay] cannot read scripts/launch.js\n");
        exit(30);
    }

    if (strpos($js, 'url_tags') === false) {
        $count = 0;
        $js = preg_replace(
            "/existing_creative_id:\\s*''\\s*}/",
            "existing_creative_id: '', url_tags: ''}",
            $js,
            1,
            $count
        );
        fwrite(STDERR, "[remask overlay] binding url_tags default: {$count}\n");
    }

    replace_once($js, 'colspan="8" class="muted">No RK selected.', 'colspan="9" class="muted">No RK selected.', 'empty selected RK colspan');

    if (strpos($js, "const trackingTd = document.createElement('td');") === false) {
        $trackingBlock = <<<'JS'
        const trackingTd = document.createElement('td');
        const trackingInput = document.createElement('input');
        trackingInput.className = 'form-control';
        trackingInput.style.minWidth = '230px';
        trackingInput.placeholder = 'Use global URL tags';
        trackingInput.value = binding.url_tags || '';
        trackingInput.addEventListener('change', () => {
            binding.url_tags = trackingInput.value.trim();
            invalidateLaunchReview();
            validateReady();
        });
        trackingTd.appendChild(trackingInput);
        const trackingHint = document.createElement('div');
        trackingHint.className = 'muted mt-1';
        trackingHint.textContent = binding.url_tags ? 'Per-RK override' : 'Global tracking';
        trackingTd.appendChild(trackingHint);

JS;
        $needle = "        const statusReady = Boolean(binding.page_id) && (!needsPixel || Boolean(binding.pixel_id));";
        if (strpos($js, $needle) !== false) {
            $js = str_replace($needle, $trackingBlock . $needle, $js, $count);
            fwrite(STDERR, "[remask overlay] tracking input block: {$count}\n");
        } else {
            fwrite(STDERR, "[remask overlay] tracking input insertion point not found\n");
            exit(31);
        }
    }

    $js = preg_replace(
        '/tr\\.appendChild\\(pageTd\\);\\s*tr\\.appendChild\\(pixelTd\\);\\s*tr\\.appendChild\\(audienceTd\\);\\s*tr\\.appendChild\\(mediaTd\\);\\s*tr\\.appendChild\\(statusTd\\);/',
        'tr.appendChild(pageTd); tr.appendChild(pixelTd); tr.appendChild(audienceTd); tr.appendChild(mediaTd); tr.appendChild(trackingTd); tr.appendChild(statusTd);',
        $js,
        1,
        $appendCount
    );
    if ($appendCount > 0) {
        fwrite(STDERR, "[remask overlay] append tracking td: {$appendCount}\n");
    } elseif (strpos($js, 'tr.appendChild(trackingTd);') === false) {
        fwrite(STDERR, "[remask overlay] append tracking insertion point not found\n");
        exit(32);
    }

    if (strpos($js, 'row.creative.url_tags') === false) {
        $needle = "            else if (binding.existing_image_hash) row.creative.existing_image_hash = binding.existing_image_hash;";
        if (strpos($js, $needle) !== false) {
            $replace = $needle . "\n            if (binding.url_tags) row.creative.url_tags = binding.url_tags;";
            $js = str_replace($needle, $replace, $js, $count);
            fwrite(STDERR, "[remask overlay] payload url_tags exact: {$count}\n");
        } else {
            $js = preg_replace(
                '/(binding\\.existing_image_hash\\)[^;]*row\\.creative\\.existing_image_hash\\s*=\\s*binding\\.existing_image_hash;)/',
                '$1' . "\n            if (binding.url_tags) row.creative.url_tags = binding.url_tags;",
                $js,
                1,
                $count
            );
            fwrite(STDERR, "[remask overlay] payload url_tags regex: {$count}\n");
            if ($count === 0) {
                fwrite(STDERR, "[remask overlay] payload url_tags insertion point not found\n");
                exit(33);
            }
        }
    }

    file_put_contents($launchJsPath, $js);
    fwrite(STDERR, "[remask overlay] scripts/launch.js ready\n");
} else {
    fwrite(STDERR, "[remask overlay] scripts/launch.js missing\n");
    exit(34);
}

if (is_file($launchPhpPath)) {
    $php = file_get_contents($launchPhpPath);
    if ($php === false) {
        fwrite(STDERR, "[remask overlay] cannot read launch.php\n");
        exit(35);
    }
    if (strpos($php, 'Existing Media / Tracking') === false) {
        replace_once($php, 'Existing Media</small>', 'Existing Media / Tracking</small>', 'launch title tracking');
    }
    if (strpos($php, '<th>Tracking</th>') === false) {
        replace_once($php, '<th>Existing Media</th><th>Status</th>', '<th>Existing Media</th><th>Tracking</th><th>Status</th>', 'launch table tracking th');
    }
    replace_once($php, 'colspan="8" class="muted">Open Launch from Workspace with selected RK.', 'colspan="9" class="muted">Open Launch from Workspace with selected RK.', 'launch table empty colspan');
    file_put_contents($launchPhpPath, $php);
    fwrite(STDERR, "[remask overlay] launch.php ready\n");
}

$checkAccountPath = $root . '/ajax/checkAccount.php';
$checkAccountPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/ResponseFormatter.php';

function remask_check_proxy(?string $proxy): string
{
    $proxy = trim((string)$proxy);
    if ($proxy === '') return '';
    if (preg_match('/^http:(?!\/\/)/i', $proxy)) {
        $proxy = 'http://' . substr($proxy, 5);
    }
    if (!preg_match('#^(https?|socks5h?|socks5)://#i', $proxy) && preg_match('/^[^\s\/@:]+:\d+$/', $proxy)) {
        $proxy = 'http://' . $proxy;
    }
    if (!preg_match('#^(https?|socks5h?|socks5)://[^\s]+:\d+(?:/)?$#i', $proxy) && !filter_var($proxy, FILTER_VALIDATE_URL)) {
        throw new InvalidArgumentException('Proxy format is invalid. Use http://host:port or http://user:pass@host:port.');
    }
    return $proxy;
}

function remask_graph_get(string $path, array $params, string $token, string $proxy): array
{
    $version = getenv('META_GRAPH_API_VERSION') ?: 'v26.0';
    if (!preg_match('/^v\d+\.\d+$/', $version)) $version = 'v26.0';
    $url = 'https://graph.facebook.com/' . $version . '/' . ltrim($path, '/');
    if ($params !== []) $url .= '?' . http_build_query($params);

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 10,
        CURLOPT_TIMEOUT => 25,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
        CURLOPT_HTTPHEADER => [
            'Accept: application/json',
            'Authorization: Bearer ' . $token,
        ],
        CURLOPT_USERAGENT => 'ReMask-MetaApiCheck/1.0',
    ]);
    if ($proxy !== '') curl_setopt($ch, CURLOPT_PROXY, $proxy);

    $raw = curl_exec($ch);
    $curlError = curl_error($ch);
    $httpStatus = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($raw === false) {
        throw new RuntimeException('Transport failed before Meta response: ' . ($curlError ?: 'unknown cURL error'));
    }
    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) {
        throw new RuntimeException('Meta returned non-JSON response, HTTP ' . $httpStatus . '.');
    }
    if (isset($decoded['error']) && is_array($decoded['error'])) {
        $err = $decoded['error'];
        $message = trim((string)($err['message'] ?? 'Meta rejected request.'));
        $code = isset($err['code']) ? (int)$err['code'] : 0;
        $subcode = isset($err['error_subcode']) ? (int)$err['error_subcode'] : 0;
        $type = trim((string)($err['type'] ?? ''));
        $parts = [$message];
        if ($type !== '') $parts[] = 'type ' . $type;
        if ($code) $parts[] = 'code ' . $code;
        if ($subcode) $parts[] = 'subcode ' . $subcode;
        throw new RuntimeException('Meta API check failed: ' . implode(', ', $parts));
    }
    if ($httpStatus < 200 || $httpStatus >= 300) {
        throw new RuntimeException('Meta API returned HTTP ' . $httpStatus . '.');
    }
    return $decoded;
}

try {
    $token = trim((string)($_POST['token'] ?? $_POST['access_token'] ?? ''));
    if ($token === '') throw new InvalidArgumentException('Access token is required.');
    $proxy = remask_check_proxy($_POST['proxy'] ?? '');

    $me = remask_graph_get('me', ['fields' => 'id,name'], $token, $proxy);
    $permissions = remask_graph_get('me/permissions', ['limit' => 200], $token, $proxy);
    $adsManagementGranted = false;
    foreach ((array)($permissions['data'] ?? []) as $permission) {
        if (!is_array($permission)) continue;
        if (($permission['permission'] ?? '') === 'ads_management' && ($permission['status'] ?? '') === 'granted') {
            $adsManagementGranted = true;
            break;
        }
    }
    if (!$adsManagementGranted) {
        throw new RuntimeException('ads_management permission is not granted for this token.');
    }

    $adAccounts = remask_graph_get('me/adaccounts', [
        'fields' => 'id,name,account_status,currency,disable_reason',
        'limit' => 50,
    ], $token, $proxy);

    ResponseFormatter::Respond(['res' => json_encode([
        'ok' => true,
        'profile' => [
            'id' => (string)($me['id'] ?? ''),
            'name' => (string)($me['name'] ?? ''),
        ],
        'ads_management_granted' => true,
        'ad_accounts_count' => count((array)($adAccounts['data'] ?? [])),
        'proxy_used' => $proxy !== '',
        'message' => 'Meta API profile is valid.',
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR)]);
} catch (Throwable $e) {
    http_response_code(200);
    ResponseFormatter::Respond(['error' => $e->getMessage()]);
}
PHP;

if (!is_dir(dirname($checkAccountPath))) {
    mkdir(dirname($checkAccountPath), 0775, true);
}
file_put_contents($checkAccountPath, $checkAccountPhp);
fwrite(STDERR, "[remask overlay] ajax/checkAccount.php ready\n");
