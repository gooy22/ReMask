<?php
declare(strict_types=1);
$root = '/var/www/html';
foreach (['AtomicJsonFile','CredentialVault','RemaskRequestPolicy','RemaskLoginThrottle','RemaskCookieSession','FbAccountSerializer','MetaDraftStore','MediaLibraryStore','CreativePresetStore','MetaBundleStore','WorkspaceMetaStore','MetaJobStore','MetaCacheStore','MetaUsageStore','FileActivityLogStore'] as $name) {
    if (!copy('/tmp/railway-safe-'.$name.'.php', $root.'/classes/'.$name.'.php')) throw new RuntimeException('Hardening source unavailable: '.$name);
}
$pgPath=$root.'/classes/PostgresProfileStore.php';
$pg=file_get_contents($pgPath);
$pgNeedle='    private function selectSql(): string';
$pgMethod=<<<'PG_COOKIE_CAS'
    public function compareAndSwapCookies(string $name, string $actor, string $expectedXs, array $cookies): bool
    {
        require_once __DIR__.'/RemaskCookieSession.php';
        $this->pdo->beginTransaction();
        try {
            $stmt=$this->pdo->prepare($this->selectSql().' WHERE name=:name FOR UPDATE');
            $stmt->execute(['name'=>$name]);
            $row=$stmt->fetch();
            if (!$row) { $this->pdo->rollBack(); return false; }
            $account=$this->hydrate($row);
            $next=RemaskCookieSession::replacement($account, $actor, $expectedXs, $cookies);
            if ($next===null) { $this->pdo->rollBack(); return false; }
            $legacy=$this->vault->encryptJson(['cookies'=>$next,'dtsg'=>$account->dtsg], $this->context($name,'legacy'));
            $update=$this->pdo->prepare("UPDATE profiles SET cookies_encrypted=convert_to(:legacy, 'UTF8'), updated_at=now() WHERE name=:name");
            $update->execute(['legacy'=>$legacy,'name'=>$name]);
            $this->pdo->commit();
            return true;
        } catch (Throwable $error) {
            if ($this->pdo->inTransaction()) $this->pdo->rollBack();
            throw $error;
        }
    }

PG_COOKIE_CAS;
if (substr_count($pg,$pgNeedle)!==1) throw new RuntimeException('PROFILE_COOKIE_CAS_BOUNDARY_MISSING');
$pg=str_replace($pgNeedle,$pgMethod."\n".$pgNeedle,$pg);
file_put_contents($pgPath,$pg);
copy('/tmp/railway-safe-checkpassword.php', $root.'/checkpassword.php');
$settings = file_get_contents($root.'/settings.php');
$settings = str_replace("\$remaskPassword = 'change-me-now';", "\$remaskPassword = '';", $settings);
file_put_contents($root.'/settings.php', $settings);
// Apply method policy even where a valid internal key bypasses the browser session guard.
foreach (glob($root.'/ajax/*.php') as $file) {
    $source = file_get_contents($file);
    $hook = "\nrequire_once __DIR__.'/../classes/RemaskRequestPolicy.php';\nRemaskRequestPolicy::enforce(basename(__FILE__));\n";
    if (preg_match('/declare\(strict_types=1\);/', $source)) $source = preg_replace('/declare\(strict_types=1\);/', '$0'.$hook, $source, 1);
    else $source = preg_replace('/<\?php/', '$0'.$hook, $source, 1);
    file_put_contents($file, $source);
}
