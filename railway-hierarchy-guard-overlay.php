<?php
/**
 * Wraps the packed runtime ajax/metaHierarchy.php so sync failures return JSON diagnostics
 * instead of an opaque HTTP 502. Secrets are redacted from the diagnostic output.
 */
$root = '/var/www/html';
$ajaxDir = $root . '/ajax';
$target = $ajaxDir . '/metaHierarchy.php';
$base = $ajaxDir . '/metaHierarchy.base.php';

if (!is_file($target)) {
    fwrite(STDERR, "[remask hierarchy guard] metaHierarchy.php missing, skipping\n");
    exit(0);
}

$existing = file_get_contents($target);
if (strpos($existing, '__remask_hierarchy_guard') === false) {
    if (!@rename($target, $base)) {
        @copy($target, $base);
    }
}

$wrapper = <<<'PHP'
<?php
// __remask_hierarchy_guard
// Never expose raw access tokens/cookies in responses.
declare(strict_types=1);

$__remask_hierarchy_guard_done = false;
$__remask_hierarchy_guard_base = __DIR__ . '/metaHierarchy.base.php';

function remask_hierarchy_guard_is_fatal(?array $err): bool
{
    if (!$err) return false;
    return in_array((int)($err['type'] ?? 0), [E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR], true);
}

function remask_hierarchy_guard_redact(string $text): string
{
    $text = preg_replace('/EA[A-Za-z0-9_\-]{20,}/', 'EA***REDACTED***', $text) ?? $text;
    $text = preg_replace('/("access_token"\s*:\s*")[^"]+(")/i', '$1***REDACTED***$2', $text) ?? $text;
    $text = preg_replace('/("token"\s*:\s*")[^"]+(")/i', '$1***REDACTED***$2', $text) ?? $text;
    $text = preg_replace('/("cookies?"\s*:\s*").*?(")/is', '$1***REDACTED***$2', $text) ?? $text;
    return $text;
}

function remask_hierarchy_guard_hint(string $message, int $code): string
{
    $m = strtolower($message);
    if (str_contains($m, 'ads_management') || str_contains($m, 'permission')) {
        return 'Token does not expose ads_management permission, so BM/RK cannot be synced.';
    }
    if (str_contains($m, 'oauth') || str_contains($m, '190') || str_contains($m, 'invalid token') || str_contains($m, 'session')) {
        return 'Meta rejected the token/session. Re-check token validity and app permissions.';
    }
    if (str_contains($m, 'proxy') || str_contains($m, 'curl') || str_contains($m, 'timed out') || str_contains($m, 'timeout') || $code === 502 || $code === 504) {
        return 'Sync reached the server but failed while contacting Meta, most likely proxy/transport/timeout.';
    }
    if (str_contains($m, 'business') || str_contains($m, 'adaccount') || str_contains($m, 'ad account')) {
        return 'Token was accepted, but Business Manager / ad account discovery did not return usable assets.';
    }
    return 'Open Activity/History or server logs: hierarchy sync failed before returning usable BM/RK data.';
}

function remask_hierarchy_guard_send_failure(string $kind, string $message, int $code = 200, array $extra = []): void
{
    while (ob_get_level() > 0) {
        @ob_end_clean();
    }
    $message = remask_hierarchy_guard_redact($message);
    http_response_code(200);
    header('Content-Type: application/json; charset=utf-8');
    header('X-ReMask-Hierarchy-Guard: 1');
    $payload = array_merge([
        'ok' => false,
        'error' => 'META_HIERARCHY_SYNC_FAILED',
        'kind' => $kind,
        'message' => $message,
        'http_status_seen' => $code,
        'hint' => remask_hierarchy_guard_hint($message, $code),
        'synced' => false,
        'profiles_synced' => 0,
    ], $extra);
    error_log('[remask hierarchy guard] ' . json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

register_shutdown_function(function () use (&$__remask_hierarchy_guard_done, $__remask_hierarchy_guard_base): void {
    if ($__remask_hierarchy_guard_done) return;
    $err = error_get_last();
    $code = http_response_code() ?: 200;
    $out = '';
    while (ob_get_level() > 0) {
        $chunk = @ob_get_clean();
        if (is_string($chunk)) $out = $chunk . $out;
    }

    if (remask_hierarchy_guard_is_fatal($err)) {
        $msg = sprintf('%s in %s:%s', (string)($err['message'] ?? 'fatal error'), (string)($err['file'] ?? 'unknown'), (string)($err['line'] ?? '0'));
        remask_hierarchy_guard_send_failure('fatal', $msg, $code, ['base_exists' => is_file($__remask_hierarchy_guard_base)]);
        return;
    }

    if ($code >= 500 || trim($out) === '') {
        $msg = trim($out) !== '' ? $out : 'metaHierarchy returned empty response';
        remask_hierarchy_guard_send_failure('empty_or_5xx', $msg, $code, ['base_exists' => is_file($__remask_hierarchy_guard_base)]);
        return;
    }

    http_response_code($code);
    echo $out;
});

ob_start();
try {
    if (!is_file($__remask_hierarchy_guard_base)) {
        throw new RuntimeException('Original metaHierarchy.base.php is missing');
    }
    require $__remask_hierarchy_guard_base;

    $out = (string)ob_get_clean();
    $code = http_response_code() ?: 200;
    if ($code >= 500 || trim($out) === '') {
        $__remask_hierarchy_guard_done = true;
        remask_hierarchy_guard_send_failure('empty_or_5xx', trim($out) !== '' ? $out : 'metaHierarchy returned empty response', $code, ['base_exists' => true]);
        return;
    }

    $__remask_hierarchy_guard_done = true;
    http_response_code($code);
    echo $out;
} catch (Throwable $e) {
    $__remask_hierarchy_guard_done = true;
    remask_hierarchy_guard_send_failure('throwable', $e->getMessage(), http_response_code() ?: 200, [
        'exception' => get_class($e),
        'file' => basename($e->getFile()),
        'line' => $e->getLine(),
    ]);
}
PHP;

file_put_contents($target, $wrapper);

fwrite(STDERR, "[remask hierarchy guard] ajax/metaHierarchy.php wrapped\n");
