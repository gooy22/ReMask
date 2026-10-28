<?php
declare(strict_types=1);
$runtime = getenv('REMASK_RUNTIME_ROOT') ?: getcwd();
require $runtime.'/classes/FbAccount.php';
require $runtime.'/classes/RemaskProxy.php';
require $runtime.'/classes/FbAccountSerializer.php';
function assertStartup(bool $condition, string $message): void {
    if (!$condition) throw new RuntimeException($message);
}
$savedKey = getenv('REMASK_CREDENTIALS_KEY');
$savedFlag = getenv('REMASK_ENCRYPT_PROFILE_JSON');
$dir = sys_get_temp_dir().'/remask-startup-'.bin2hex(random_bytes(5));
mkdir($dir, 0700);
try {
    putenv('REMASK_CREDENTIALS_KEY=hex:'.bin2hex(str_repeat('b', 32)));
    putenv('REMASK_ENCRYPT_PROFILE_JSON=1');
    $path = $dir.'/accounts.json';
    $store = new FbAccountSerializer($path);
    foreach (['old-xs', 'new-xs'] as $xs) {
        $profile = new FbAccount('4', 'fixture-token',
            json_encode([['name'=>'c_user','value'=>'12345'],['name'=>'xs','value'=>$xs]]), null, null);
        $store->addOrUpdateAccount($profile);
    }
    $current = file_get_contents($path);
    $previous = file_get_contents($path.'.previous');
    assertStartup($current !== $previous, 'fixture has no distinct backup');
    $store->ensureEncrypted();
    $store->ensureEncrypted();
    assertStartup(file_get_contents($path) === $current, 'encrypted profile rewritten at startup');
    assertStartup(file_get_contents($path.'.previous') === $previous, 'prior session overwritten at startup');

    require_once $runtime.'/classes/RemaskCookieSession.php';
    $previousAccount = $store->getPreviousAccountByName('4');
    $currentAccount = $store->getAccountByName('4');
    assertStartup($previousAccount instanceof FbAccount && $currentAccount instanceof FbAccount,
        'same-profile snapshot lookup failed');
    $previousCookies = RemaskCookieSession::map((array)$previousAccount->cookies);
    $currentCookies = RemaskCookieSession::map((array)$currentAccount->cookies);
    assertStartup(($previousCookies['xs'] ?? '') === 'old-xs', 'previous session was not read');
    assertStartup(($currentCookies['xs'] ?? '') === 'new-xs', 'current session changed before restore');
    $currentHash = hash('sha256', $currentCookies['xs']);
    $previousHash = hash('sha256', $previousCookies['xs']);
    $currentBeforeRestore = file_get_contents($path);
    $backupBeforeRestore = file_get_contents($path.'.previous');
    assertStartup(!$store->restorePreviousCookiesIfUnchanged(
        '4', '99999', $currentHash, $previousHash, $previousCookies
    ), 'different actor was allowed to restore a profile session');
    assertStartup(file_get_contents($path) === $currentBeforeRestore
        && file_get_contents($path.'.previous') === $backupBeforeRestore,
        'rejected restore changed profile storage');
    assertStartup($store->restorePreviousCookiesIfUnchanged(
        '4', '12345', $currentHash, $previousHash, $previousCookies
    ), 'verified same-actor restore failed');
    $restored = $store->getAccountByName('4');
    $rollback = $store->getPreviousAccountByName('4');
    $restoredCookies = RemaskCookieSession::map((array)$restored->cookies);
    $rollbackCookies = RemaskCookieSession::map((array)$rollback->cookies);
    assertStartup(($restoredCookies['xs'] ?? '') === 'old-xs',
        'verified previous session was not restored');
    assertStartup(($rollbackCookies['xs'] ?? '') === 'new-xs',
        'the pre-restore session was not retained as rollback');
    assertStartup($restored->token === 'fixture-token', 'restore changed the profile token');

    // Legacy webroot aliases must read and restore the snapshot beside the volume target.
    $linkPath = $dir.'/linked-accounts.json';
    symlink($path, $linkPath);
    $linked = new FbAccountSerializer($linkPath);
    foreach (['linked-old-xs', 'linked-new-xs'] as $xs) {
        $linkedProfile = new FbAccount('5', 'fixture-token',
            json_encode([['name'=>'c_user','value'=>'54321'],['name'=>'xs','value'=>$xs]]), null, null);
        $linked->addOrUpdateAccount($linkedProfile);
    }
    $linkedPrevious = $linked->getPreviousAccountByName('5');
    $linkedCurrent = $linked->getAccountByName('5');
    assertStartup($linkedPrevious instanceof FbAccount && $linkedCurrent instanceof FbAccount,
        'symlinked profile snapshot lookup failed');
    $linkedPreviousCookies = RemaskCookieSession::map((array)$linkedPrevious->cookies);
    $linkedCurrentCookies = RemaskCookieSession::map((array)$linkedCurrent->cookies);
    assertStartup(($linkedPreviousCookies['xs'] ?? '') === 'linked-old-xs',
        'symlinked previous snapshot was not read from the volume path');
    $linkedCurrentHash = hash('sha256', $linkedCurrentCookies['xs']);
    $linkedPreviousHash = hash('sha256', $linkedPreviousCookies['xs']);
    assertStartup($linked->restorePreviousCookiesIfUnchanged(
        '5', '54321', $linkedCurrentHash, $linkedPreviousHash, $linkedPreviousCookies
    ), 'symlinked verified snapshot restore failed');
    $linkedRestored = $linked->getAccountByName('5');
    $linkedRestoredCookies = RemaskCookieSession::map((array)$linkedRestored->cookies);
    assertStartup(($linkedRestoredCookies['xs'] ?? '') === 'linked-old-xs',
        'symlinked snapshot was not restored on the volume file');

    // A damaged unrelated row must not hide this profile's valid archive snapshot.
    $archivePath = $dir.'/accounts.json.bak-fixture';
    $archiveStore = new FbAccountSerializer($archivePath);
    $archiveOther = new FbAccount('archive-other', 'fixture-token',
        json_encode([['name'=>'c_user','value'=>'67890'],['name'=>'xs','value'=>'other-xs']]), null, null);
    $archiveTarget = new FbAccount('archive-target', 'fixture-token',
        json_encode([['name'=>'c_user','value'=>'12345'],['name'=>'xs','value'=>'archive-xs']]), null, null);
    $archiveStore->addOrUpdateAccount($archiveOther);
    $archiveStore->addOrUpdateAccount($archiveTarget);
    $archiveRows = json_decode((string)file_get_contents($archivePath), true, 512, JSON_THROW_ON_ERROR);
    foreach ($archiveRows as &$archiveRow) {
        if (($archiveRow['name'] ?? '') === 'archive-other') {
            $archiveRow['credentials_encrypted'] = 'v1:malformed-fixture';
        }
    }
    unset($archiveRow);
    file_put_contents($archivePath, AtomicJsonFile::encode($archiveRows));
    $fullArchiveReadFailed = false;
    try {
        $archiveStore->getAccountByName('archive-target');
    } catch (Throwable $ignored) {
        $fullArchiveReadFailed = true;
    }
    assertStartup($fullArchiveReadFailed, 'fixture did not include an unrelated damaged row');
    $archiveTargetRead = $archiveStore->getAccountByNameFromSnapshot('archive-target');
    assertStartup($archiveTargetRead instanceof FbAccount,
        'valid target row was hidden by an unrelated damaged row');
    $archiveTargetCookies = RemaskCookieSession::map((array)$archiveTargetRead->cookies);
    assertStartup(($archiveTargetCookies['xs'] ?? '') === 'archive-xs',
        'target archive session changed during isolated read');

    // Legacy plaintext migration still encrypts both the current profile and its backup.
    putenv('REMASK_ENCRYPT_PROFILE_JSON=0');
    putenv('REMASK_CREDENTIALS_KEY');
    $legacy = new FbAccountSerializer($dir.'/legacy.json');
    $legacy->addOrUpdateAccount($profile);
    putenv('REMASK_CREDENTIALS_KEY=hex:'.bin2hex(str_repeat('b', 32)));
    putenv('REMASK_ENCRYPT_PROFILE_JSON=1');
    $legacy->ensureEncrypted();
    $migrated = file_get_contents($dir.'/legacy.json');
    $backup = file_get_contents($dir.'/legacy.json.previous');
    assertStartup(str_contains($migrated, 'credentials_encrypted'), 'legacy current not encrypted');
    assertStartup(str_contains($backup, 'credentials_encrypted'), 'legacy backup not encrypted');
    assertStartup(!str_contains($migrated, 'new-xs') && !str_contains($backup, 'new-xs'), 'plaintext credential retained');
    assertStartup($legacy->getAccountByName('4')->userId === '12345', 'profile identity changed');
    $legacy->ensureEncrypted();
    assertStartup(file_get_contents($dir.'/legacy.json') === $migrated
        && file_get_contents($dir.'/legacy.json.previous') === $backup, 'migration repeated on startup');
    echo "Startup preserves an existing profile backup and migrates plaintext once.\n";
} finally {
    $savedKey === false ? putenv('REMASK_CREDENTIALS_KEY') : putenv('REMASK_CREDENTIALS_KEY='.$savedKey);
    $savedFlag === false ? putenv('REMASK_ENCRYPT_PROFILE_JSON') : putenv('REMASK_ENCRYPT_PROFILE_JSON='.$savedFlag);
    foreach (glob($dir.'/*') ?: [] as $file) unlink($file);
    rmdir($dir);
}
