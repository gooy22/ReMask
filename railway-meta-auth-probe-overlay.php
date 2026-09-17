<?php
$probePath = '/var/www/html/ajax/metaSyncProbe.php';
$probe = file_get_contents($probePath);
if ($probe === false) throw new RuntimeException('metaSyncProbe.php not found');
if (!str_contains($probe, "persisted_auth_state")) {
    $needle = "echo json_encode(\$out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);";
    $pos = strpos($probe, $needle);
    if ($pos === false) throw new RuntimeException('metaSyncProbe output anchor not found');
    $persist = <<<'PHP'
try {
    require_once __DIR__ . '/../classes/MetaAuthStateStore.php';
    $stateStore = new MetaAuthStateStore();
    $identityFailed = is_array($out['identity'] ?? null) && (($out['identity']['ok'] ?? null) === false);
    $identityCode = $identityFailed ? (int)($out['identity']['error_code'] ?? 0) : 0;
    $identityMessage = $identityFailed ? trim((string)($out['identity']['error'] ?? '')) : '';
    if ($identityFailed && $identityCode === 1 && $identityMessage === 'Invalid request.') {
        $stateStore->set($profile, [
            'status'=>'oauth_required',
            'message'=>'Meta Graph API отклонил текущий токен на /me. Требуется официальный Meta OAuth/Marketing API token.',
            'updated_at'=>gmdate('c'),
        ]);
        $out['persisted_auth_state'] = 'oauth_required';
    } elseif (($out['ok'] ?? false) === true) {
        $stateStore->set($profile, ['status'=>'ok','message'=>'','updated_at'=>gmdate('c')]);
        $out['persisted_auth_state'] = 'ok';
    }
} catch (Throwable $stateError) {
    $out['persisted_auth_state'] = 'write_failed';
}

PHP;
    $probe = substr($probe, 0, $pos) . $persist . substr($probe, $pos);
    file_put_contents($probePath, $probe);
}
fwrite(STDERR, "[meta-auth-probe] startup probe persists sanitized auth state\n");
