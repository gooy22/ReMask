<?php
declare(strict_types=1);
require_once __DIR__.'/classes/RemaskReadiness.php';

$root = rtrim((string)(getenv('REMASK_DATA_DIR') ?: getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask'), '/');
$directories = [$root];
foreach (['REMASK_JOB_STORAGE_DIR'=>'jobs', 'REMASK_BUNDLE_STORAGE_DIR'=>'bundles',
    'REMASK_META_CACHE_DIR'=>'meta-cache', 'REMASK_JOB_MEDIA_DIR'=>'job-media',
    'REMASK_MEDIA_LIBRARY_DIR'=>'media-library', 'REMASK_CREATIVE_PRESET_DIR'=>'creative-presets',
    'REMASK_PYTHON_STATE_DIR'=>'python-worker-jobs', 'REMASK_PHP_SESSION_DIR'=>'php-sessions'] as $key=>$relative)
    $directories[] = (string)(getenv($key) ?: $root.'/'.$relative);
$directories[] = dirname((string)(getenv('REMASK_JOB_DB') ?: $root.'/python-worker/jobs.sqlite3'));
$accounts = (string)(getenv('REMASK_ACCOUNTS_FILE') ?: $root.'/accounts.json');
$result = RemaskReadiness::check($directories, $accounts, static function(): mixed {
    $url = rtrim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081'), '/').'/health';
    $context = stream_context_create(['http'=>['timeout'=>2, 'follow_location'=>0,
        'header'=>"Accept: application/json\r\n", 'ignore_errors'=>false]]);
    $body = @file_get_contents($url, false, $context, 0, 4097);
    return is_string($body) && strlen($body)<=4096 ? json_decode($body, true) : null;
});
http_response_code($result['ok'] ? 200 : 503);
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
echo json_encode($result, JSON_THROW_ON_ERROR);
