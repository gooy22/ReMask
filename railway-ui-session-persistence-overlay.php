<?php
declare(strict_types=1);

/**
 * Force the ReMask UI login session onto the Railway volume before the legacy
 * auth gate calls session_start(). docker-start.sh also configures php.ini, but
 * this source-level guard makes the contract explicit for every web request.
 */
$target='/var/www/html/checkpassword.php';
$src=file_get_contents($target);
if($src===false){
    fwrite(STDERR,"[ui-session] checkpassword.php missing\n");
    exit(341);
}
if(strpos($src,'REMASK_UI_SESSION_PERSISTENCE_V2')!==false){
    fwrite(STDERR,"[ui-session] already patched\n");
    exit(0);
}

$snippet=<<<'PHP_CODE'
/* REMASK_UI_SESSION_PERSISTENCE_V2 */
$rmxUiSessionDir = trim((string)(getenv('REMASK_PHP_SESSION_DIR') ?: ''));
if ($rmxUiSessionDir === '') {
    $rmxUiSessionDir = rtrim(
        (string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')),
        '/'
    ) . '/php-sessions';
}
if (!is_dir($rmxUiSessionDir)) {
    @mkdir($rmxUiSessionDir, 0700, true);
}
if (is_dir($rmxUiSessionDir)) {
    @ini_set('session.save_handler', 'files');
    @ini_set('session.save_path', $rmxUiSessionDir);
    @ini_set('session.gc_maxlifetime', '604800');
    @ini_set('session.cookie_lifetime', '604800');
    @ini_set('session.cookie_httponly', '1');
    @ini_set('session.cookie_samesite', 'Lax');
    if (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') {
        @ini_set('session.cookie_secure', '1');
    }
}
/* REMASK_UI_SESSION_PERSISTENCE_V2_END */

PHP_CODE;

$pattern='/@?session_start\s*\([^;]*\)\s*;/';
if(!preg_match($pattern,$src,$m,PREG_OFFSET_CAPTURE)){
    fwrite(STDERR,"[ui-session] session_start anchor missing\n");
    exit(342);
}
$offset=$m[0][1];
$src=substr($src,0,$offset).$snippet.substr($src,$offset);

if(file_put_contents($target,$src)===false){
    fwrite(STDERR,"[ui-session] cannot patch checkpassword.php\n");
    exit(343);
}
fwrite(STDERR,"[ui-session] volume-backed auth session guard installed\n");
