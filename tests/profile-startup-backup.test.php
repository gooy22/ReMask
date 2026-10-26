<?php
declare(strict_types=1);
$runtime = getcwd();
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
