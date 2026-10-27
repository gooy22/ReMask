<?php
declare(strict_types=1);
require_once getenv('REMASK_PROFILE_COOKIE_AUTH_STATE_CLASS')
    ?: (__DIR__ . '/../railway-safe-ProfileCookieAuthStateStore.php');

function assertState(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}

$dir = sys_get_temp_dir() . '/remask-cookie-auth-' . bin2hex(random_bytes(6));
if (!mkdir($dir, 0700, true)) throw new RuntimeException('temporary directory unavailable');
$path = $dir . '/state.json';
try {
    $store = new ProfileCookieAuthStateStore($path);
    $oldXs = 'old-session-fixture';
    $newXs = 'new-session-fixture';
    $oldHash = hash('sha256', $oldXs);
    $store->mark('profile-15', '123456789', $oldHash);

    assertState($store->matches('profile-15', '123456789', $oldXs), 'same actor/session should be blocked');
    $savedState = (string)file_get_contents($path);
    assertState(!str_contains($savedState, $oldXs) && !str_contains($savedState, $newXs), 'raw auth cookies must not be persisted');
    assertState(!$store->matches('profile-15', '123456789', $newXs), 'new session must not inherit stale block');
    assertState(!$store->matches('profile-16', '123456789', $oldXs), 'marker must be profile-isolated');
    assertState(!$store->matches('profile-15', '987654321', $oldXs), 'marker must be actor-isolated');

    $store->clearIfStale('profile-15', '123456789', $newXs);
    assertState(!$store->matches('profile-15', '123456789', $oldXs), 'stale marker should clear after cookie replacement');
    $store->mark('profile-15', '123456789', hash('sha256', $newXs));
    $store->clearIfStale('profile-15', '123456789', $newXs);
    assertState($store->matches('profile-15', '123456789', $newXs), 'current marker must remain');
} finally {
    foreach (glob($dir . '/*') ?: [] as $file) @unlink($file);
    @rmdir($dir);
}
echo "profile cookie auth state: ok\n";
