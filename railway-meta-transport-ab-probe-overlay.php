<?php
/** Safe A/B diagnostics: modern MetaApiClient vs legacy FbRequests using the same saved profile. */
$path = '/var/www/html/ajax/metaSyncProbe.php';
$php = file_get_contents($path);
if ($php === false) throw new RuntimeException('metaSyncProbe.php not found');

if (!str_contains($php, "require_once __DIR__ . '/../classes/FbRequests.php';")) {
    $anchor = "require_once __DIR__ . '/../classes/RemaskProxy.php';";
    $replacement = $anchor . "\nrequire_once __DIR__ . '/../classes/FbRequests.php';";
    if (!str_contains($php, $anchor)) throw new RuntimeException('probe require anchor missing');
    $php = str_replace($anchor, $replacement, $php, $count);
    if ($count !== 1) throw new RuntimeException('probe require patch count: ' . $count);
}

if (!str_contains($php, "'legacy_graph'")) {
    $anchor = <<<'PHP_CODE'
    $out['account_proxy'] = rmx_account_proxy_shape($account);
PHP_CODE;
    $insert = <<<'PHP_CODE'
    $out['account_proxy'] = rmx_account_proxy_shape($account);
    $cookieNames = [];
    foreach ((array)$account->cookies as $cookieRow) {
        if (!is_array($cookieRow)) continue;
        $name = trim((string)($cookieRow['name'] ?? ''));
        if ($name !== '') $cookieNames[] = $name;
    }
    $curlCookies = $account->getCurlCookies();
    $out['account_session'] = [
        'legacy_ready' => $account->isLegacyReady(),
        'cookie_count' => count((array)$account->cookies),
        'has_c_user' => in_array('c_user', $cookieNames, true),
        'has_xs' => in_array('xs', $cookieNames, true),
        'cookie_header_bytes' => strlen($curlCookies),
        'dtsg_present' => trim((string)($account->dtsg ?? '')) !== '',
    ];
    try {
        $legacy = (new FbRequests())->ApiGet($account, 'me?fields=id,name');
        $legacyBody = json_decode((string)($legacy['res'] ?? ''), true);
        $legacyError = is_array($legacyBody['error'] ?? null) ? $legacyBody['error'] : [];
        $transportError = trim((string)($legacy['error'] ?? ''));
        $out['legacy_graph'] = [
            'ok' => is_array($legacyBody) && isset($legacyBody['id']) && $legacyError === [] && $transportError === '',
            'has_id' => is_array($legacyBody) && isset($legacyBody['id']),
            'meta_error' => $legacyError === [] ? null : [
                'message' => isset($legacyError['message']) ? substr((string)$legacyError['message'], 0, 300) : null,
                'type' => isset($legacyError['type']) ? (string)$legacyError['type'] : null,
                'code' => isset($legacyError['code']) ? (int)$legacyError['code'] : null,
                'subcode' => isset($legacyError['error_subcode']) ? (int)$legacyError['error_subcode'] : null,
                'fbtrace_id' => isset($legacyError['fbtrace_id']) ? (string)$legacyError['fbtrace_id'] : null,
            ],
            'transport_error' => $transportError === '' ? null : substr($transportError, 0, 300),
        ];
    } catch (Throwable $legacyException) {
        $out['legacy_graph'] = [
            'ok' => false,
            'exception' => get_class($legacyException),
            'message' => substr((string)$legacyException->getMessage(), 0, 300),
        ];
    }
PHP_CODE;
    if (!str_contains($php, $anchor)) throw new RuntimeException('probe account anchor missing');
    $php = str_replace($anchor, $insert, $php, $count);
    if ($count !== 1) throw new RuntimeException('probe A/B patch count: ' . $count);
}

file_put_contents($path, $php);
fwrite(STDERR, "[meta-transport-ab-probe] safe legacy-vs-modern probe installed\n");
