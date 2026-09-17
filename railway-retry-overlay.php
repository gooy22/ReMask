<?php
/**
 * Adds safe retry/resume endpoints to the packed Railway runtime.
 * This avoids editing the archived runtime directly.
 */
$root = '/var/www/html';
$ajaxDir = $root . '/ajax';
if (!is_dir($ajaxDir)) mkdir($ajaxDir, 0775, true);

$retryPath = $ajaxDir . '/metaJobRetry.php';
$retryPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';

function remask_retry_input(): array
{
    $ct = strtolower((string)($_SERVER['CONTENT_TYPE'] ?? ''));
    if (str_contains($ct, 'application/json')) {
        $raw = file_get_contents('php://input') ?: '{}';
        $json = json_decode($raw, true);
        return is_array($json) ? $json : [];
    }
    return $_POST + $_GET;
}

function remask_retry_data_dir(): string
{
    return rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
}

function remask_retry_jobs_dir(): string
{
    return rtrim((string)(getenv('REMASK_JOB_STORAGE_DIR') ?: remask_retry_data_dir() . '/jobs'), '/');
}

function remask_retry_find_job_file(string $jobId): string
{
    $jobId = trim($jobId);
    if ($jobId === '') throw new InvalidArgumentException('job_id is required');
    $dir = remask_retry_jobs_dir();
    $candidates = [
        $dir . '/' . basename($jobId) . '.json',
        $dir . '/' . basename($jobId),
    ];
    foreach ($candidates as $file) {
        if (is_file($file)) return $file;
    }
    foreach ((glob($dir . '/*.json') ?: []) as $file) {
        $raw = @file_get_contents($file);
        if ($raw === false || trim($raw) === '') continue;
        $job = json_decode($raw, true);
        if (!is_array($job)) continue;
        $id = trim((string)($job['id'] ?? $job['job_id'] ?? ''));
        if ($id === $jobId) return $file;
    }
    throw new RuntimeException('Job file not found: ' . $jobId);
}

function remask_retry_items_key(array $job): string
{
    foreach (['items', 'job_items', 'accounts', 'targets'] as $key) {
        if (isset($job[$key]) && is_array($job[$key])) return $key;
    }
    throw new RuntimeException('Job items array not found.');
}

function remask_retry_normalize_status(string $status): string
{
    return strtoupper(trim($status));
}

function remask_retry_reset_item(array $item): array
{
    $item['status'] = 'QUEUED';
    $item['retry_requested_at'] = gmdate('c');
    unset($item['error'], $item['errors'], $item['last_error'], $item['failed_at'], $item['completed_at'], $item['started_at']);
    if (isset($item['steps']) && is_array($item['steps'])) {
        $kept = [];
        foreach ($item['steps'] as $step) {
            if (!is_array($step)) continue;
            $stepStatus = remask_retry_normalize_status((string)($step['status'] ?? ''));
            if ($stepStatus === 'SUCCESS') $kept[] = $step;
        }
        $item['steps'] = $kept;
    }
    return $item;
}

function remask_retry_should_reset(string $mode, string $status): bool
{
    $status = remask_retry_normalize_status($status);
    return match ($mode) {
        'retry_failed' => in_array($status, ['FAILED', 'ERROR', 'REJECTED'], true),
        'resume_queued' => in_array($status, ['', 'CREATED', 'QUEUED', 'PENDING', 'RETRY'], true),
        'reset_stuck' => in_array($status, ['RUNNING', 'PROCESSING', 'LOCKED'], true),
        'retry_all_unfinished' => !in_array($status, ['SUCCESS', 'DONE', 'COMPLETED', 'COMPLETE'], true),
        default => false,
    };
}

try {
    $input = remask_retry_input();
    $jobId = trim((string)($input['job_id'] ?? ''));
    $mode = trim((string)($input['mode'] ?? 'retry_failed'));
    $allowed = ['retry_failed', 'resume_queued', 'reset_stuck', 'retry_all_unfinished'];
    if (!in_array($mode, $allowed, true)) throw new InvalidArgumentException('Invalid retry mode.');

    $file = remask_retry_find_job_file($jobId);
    $lockPath = remask_retry_data_dir() . '/job-retry.lock';
    $lock = fopen($lockPath, 'c');
    if (!$lock || !flock($lock, LOCK_EX)) throw new RuntimeException('Cannot lock retry state.');

    $raw = file_get_contents($file);
    if ($raw === false || trim($raw) === '') throw new RuntimeException('Job file is empty.');
    $job = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($job)) throw new RuntimeException('Job file is invalid.');

    $key = remask_retry_items_key($job);
    $changed = 0;
    $seen = 0;
    foreach ($job[$key] as $i => $item) {
        if (!is_array($item)) continue;
        $seen++;
        $status = (string)($item['status'] ?? '');
        if (!remask_retry_should_reset($mode, $status)) continue;
        $job[$key][$i] = remask_retry_reset_item($item);
        $changed++;
    }

    if ($changed > 0) {
        $job['status'] = 'QUEUED';
        $job['updated_at'] = gmdate('c');
        $job['retry_mode'] = $mode;
        $tmp = $file . '.tmp.' . getmypid();
        file_put_contents($tmp, json_encode($job, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_THROW_ON_ERROR));
        rename($tmp, $file);
    }

    MetaEndpoint::ok([
        'job_id' => $jobId,
        'mode' => $mode,
        'items_seen' => $seen,
        'items_requeued' => $changed,
        'file' => basename($file),
        'message' => $changed > 0 ? 'Job items requeued for background worker.' : 'No matching items to requeue.',
    ]);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP;
file_put_contents($retryPath, $retryPhp);
fwrite(STDERR, "[remask retry overlay] ajax/metaJobRetry.php ready\n");
