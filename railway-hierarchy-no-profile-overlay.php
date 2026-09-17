<?php
/**
 * Makes ajax/metaHierarchy.php treat an empty account store as a harmless noop.
 * This prevents Workspace from showing "Meta hierarchy sync did not complete"
 * when there are 0 saved FB profiles and therefore nothing to sync.
 */
$target = '/var/www/html/ajax/metaHierarchy.php';
if (!is_file($target)) {
    fwrite(STDERR, "[remask hierarchy no-profile overlay] target not found\n");
    exit(1);
}
$php = file_get_contents($target);
$patched = 0;

$php2 = preg_replace(
    "/'ok'\s*=>\s*false,\s*\n\s*'synced'\s*=>\s*false,\s*\n\s*'profiles_found'\s*=>\s*0,/",
    "'ok' => true,\n            'success' => true,\n            'synced' => true,\n            'no_profiles' => true,\n            'profiles_found' => 0,",
    $php,
    1,
    $count
);
$patched += (int)$count;
$php = $php2;

$php2 = str_replace(
    "'error' => 'NO_SAVED_PROFILES',\n            'message' => 'No saved FB profiles found. Add the FB account again.',",
    "'error' => null,\n            'message' => 'No saved FB profiles yet. Add the FB account to start Meta sync.',",
    $php,
    $count
);
$patched += (int)$count;
$php = $php2;

file_put_contents($target, $php);
fwrite(STDERR, "[remask hierarchy no-profile overlay] patched: $patched\n");
