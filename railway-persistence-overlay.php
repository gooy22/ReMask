<?php
declare(strict_types=1);

/**
 * ReMask persistent-storage compatibility layer.
 *
 * The runtime historically used local files from /var/www/html while Railway
 * deployments need all mutable state on the mounted volume. This overlay makes
 * ACCOUNTSFILENAME volume-backed and exposes canonical persistent paths to the
 * rest of the runtime.
 */

$root = '/var/www/html';
$settingsPath = $root . '/settings.php';

if (!is_file($settingsPath)) {
    fwrite(STDERR, "[persistence] settings.php missing\n");
    exit(301);
}

$settings = file_get_contents($settingsPath);
if ($settings === false) {
    fwrite(STDERR, "[persistence] cannot read settings.php\n");
    exit(302);
}

function rmx_persistence_replace_constant(string $source, string $name, string $replacement, int &$count): string
{
    $count = 0;

    $definePattern = '/define\s*\(\s*([\'\"])' . preg_quote($name, '/') . '\\1\s*,\s*.*?\)\s*;/s';
    $patched = preg_replace($definePattern, $replacement, $source, 1, $defineCount);
    if ($patched === null) {
        throw new RuntimeException('regex failure while patching ' . $name);
    }
    if ($defineCount > 0) {
        $count = $defineCount;
        return $patched;
    }

    $constPattern = '/\bconst\s+' . preg_quote($name, '/') . '\s*=\s*.*?;/s';
    $patched = preg_replace($constPattern, $replacement, $source, 1, $constCount);
    if ($patched === null) {
        throw new RuntimeException('regex failure while patching const ' . $name);
    }
    $count = $constCount;
    return $patched;
}

$accountsDefinition = <<<'PHP_CODE'
define(
    'ACCOUNTSFILENAME',
    getenv('REMASK_ACCOUNTS_FILE')
        ?: rtrim((string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')), '/')
            . '/accounts.json'
);
PHP_CODE;

$accountPatchCount = 0;
$settings = rmx_persistence_replace_constant(
    $settings,
    'ACCOUNTSFILENAME',
    $accountsDefinition,
    $accountPatchCount
);

if ($accountPatchCount === 0) {
    $settings .= "\n\n" . <<<'PHP_CODE'
/* REMASK_PERSISTENCE_ACCOUNTS_V1 */
if (!defined('ACCOUNTSFILENAME')) {
    define(
        'ACCOUNTSFILENAME',
        getenv('REMASK_ACCOUNTS_FILE')
            ?: rtrim((string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')), '/')
                . '/accounts.json'
    );
}
PHP_CODE;
}

if (strpos($settings, 'REMASK_PERSISTENCE_ROOT_V1') === false) {
    $settings .= "\n\n" . <<<'PHP_CODE'
/* REMASK_PERSISTENCE_ROOT_V1 */
if (!defined('REMASK_MEDIA_LIBRARY_DIR')) {
    define(
        'REMASK_MEDIA_LIBRARY_DIR',
        getenv('REMASK_MEDIA_LIBRARY_DIR')
            ?: rtrim((string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')), '/')
                . '/media-library'
    );
}
if (!defined('REMASK_MEDIA_LIBRARY_MAX_BYTES')) {
    define('REMASK_MEDIA_LIBRARY_MAX_BYTES', 157286400);
}
PHP_CODE;
}

if (file_put_contents($settingsPath, $settings) === false) {
    fwrite(STDERR, "[persistence] cannot write settings.php\n");
    exit(303);
}

fwrite(
    STDERR,
    "[persistence] settings patched; ACCOUNTSFILENAME=" .
    ($accountPatchCount > 0 ? "replaced" : "guarded") .
    "; persistent root uses REMASK_DATA_DIR/RAILWAY_VOLUME_MOUNT_PATH\n"
);
