<?php
declare(strict_types=1);
$root = '/var/www/html';
foreach (['AtomicJsonFile','RemaskRequestPolicy','RemaskLoginThrottle','FbAccountSerializer','MetaDraftStore','MediaLibraryStore','CreativePresetStore','MetaBundleStore','WorkspaceMetaStore','MetaJobStore','MetaCacheStore','MetaUsageStore','FileActivityLogStore'] as $name) {
    if (!copy('/tmp/railway-safe-'.$name.'.php', $root.'/classes/'.$name.'.php')) throw new RuntimeException('Hardening source unavailable: '.$name);
}
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
