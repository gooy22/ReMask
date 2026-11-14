<?php
/**
 * Adds a lightweight internal worker to the packed Railway runtime.
 * It processes queued Meta launch jobs from the persistent REMASK_DATA_DIR without relying on an open browser tab.
 */
$root = '/var/www/html';

$binDir = $root . '/bin';
if (!is_dir($binDir)) mkdir($binDir, 0775, true);

$workerPath = $binDir . '/remask-worker.php';
$workerPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
require_once __DIR__ . '/../classes/MetaJobExecutor.php';

function remask_worker_data_dir(): string
{
    $dir = getenv('REMASK_DATA_DIR') ?: '/var/lib/remask';
    return rtrim($dir, '/');
}

function remask_worker_jobs_dir(): string
{
    $dir = getenv('REMASK_JOB_STORAGE_DIR') ?: (remask_worker_data_dir() . '/jobs');
    return rtrim($dir, '/');
}

function remask_worker_log(string $message, array $context = []): void
{
    $dir = remask_worker_data_dir();
    if (!is_dir($dir)) @mkdir($dir, 0775, true);
    $line = '[' . gmdate('c') . '] ' . $message;
    if ($context !== []) $line .= ' ' . json_encode($context, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    $line .= PHP_EOL;
    @file_put_contents($dir . '/worker.log', $line, FILE_APPEND | LOCK_EX);
    fwrite(STDOUT, $line);
}

function remask_worker_job_files(): array
{
    $dir = remask_worker_jobs_dir();
    if (!is_dir($dir)) return [];
    $files = glob($dir . '/*.json') ?: [];
    usort($files, static fn($a, $b) => filemtime($a) <=> filemtime($b));
    return $files;
}

function remask_worker_job_id(string $file, array $job): string
{
    $id = trim((string)($job['id'] ?? $job['job_id'] ?? ''));
    if ($id !== '') return $id;
    return preg_replace('/\.json$/', '', basename($file)) ?: '';
}

function remask_worker_item_statuses(array $job): array
{
    $items = [];
    foreach (['items', 'job_items', 'accounts', 'targets'] as $key) {
        if (isset($job[$key]) && is_array($job[$key])) { $items = $job[$key]; break; }
    }
    $statuses = [];
    foreach ($items as $item) {
        if (!is_array($item)) continue;
        $statuses[] = strtoupper(trim((string)($item['status'] ?? '')));
    }
    return $statuses;
}

function remask_worker_should_process(array $job): bool
{
    $jobStatus = strtoupper(trim((string)($job['status'] ?? '')));
    if (in_array($jobStatus, ['COMPLETED', 'COMPLETE', 'SUCCESS', 'DONE', 'CANCELLED', 'CANCELED', 'ABORTED'], true)) return false;

    $statuses = remask_worker_item_statuses($job);
    if ($statuses === []) {
        return in_array($jobStatus, ['', 'CREATED', 'QUEUED', 'PENDING', 'RUNNING', 'PROCESSING', 'PARTIAL'], true);
    }

    foreach ($statuses as $status) {
        if (in_array($status, ['', 'CREATED', 'QUEUED', 'PENDING', 'RETRY'], true)) return true;
    }
    return false;
}

function remask_worker_tick(int $maxItems): array
{
    $store = MetaEndpoint::jobStore();
    $processed = 0;
    $checked = 0;
    $errors = [];

    foreach (remask_worker_job_files() as $file) {
        if ($processed >= $maxItems) break;
        $raw = @file_get_contents($file);
        if ($raw === false || trim($raw) === '') continue;
        $job = json_decode($raw, true);
        if (!is_array($job) || !remask_worker_should_process($job)) continue;

        $jobId = remask_worker_job_id($file, $job);
        if ($jobId === '') continue;
        $checked++;

        try {
            $result = $store->processNext($jobId, [MetaJobExecutor::class, 'process']);
            $processed++;
            remask_worker_log('processed job item', [
                'job_id' => $jobId,
                'status' => $result['status'] ?? null,
                'processed' => $processed,
            ]);
        } catch (Throwable $e) {
            $errors[] = ['job_id' => $jobId, 'error' => $e->getMessage()];
            remask_worker_log('worker process error', ['job_id' => $jobId, 'error' => $e->getMessage()]);
        }
    }

    return ['ok' => true, 'checked_jobs' => $checked, 'processed_items' => $processed, 'errors' => $errors];
}

$dir = remask_worker_data_dir();
if (!is_dir($dir)) @mkdir($dir, 0775, true);
$lockPath = $dir . '/worker.lock';
$lock = fopen($lockPath, 'c');
if (!$lock) {
    remask_worker_log('cannot open worker lock', ['path' => $lockPath]);
    exit(2);
}
if (!flock($lock, LOCK_EX | LOCK_NB)) {
    remask_worker_log('worker already running');
    exit(0);
}

$maxItems = (int)(getenv('REMASK_WORKER_MAX_ITEMS_PER_TICK') ?: 3);
if ($maxItems < 1) $maxItems = 1;
if ($maxItems > 20) $maxItems = 20;

try {
    $result = remask_worker_tick($maxItems);
    if (($result['processed_items'] ?? 0) > 0 || !empty($result['errors'])) {
        remask_worker_log('tick done', $result);
    }
    exit(0);
} catch (Throwable $e) {
    remask_worker_log('tick fatal', ['error' => $e->getMessage()]);
    exit(1);
}
PHP;
file_put_contents($workerPath, $workerPhp);
fwrite(STDERR, "[remask worker overlay] bin/remask-worker.php ready\n");

$statusPath = $root . '/ajax/metaWorkerStatus.php';
$statusPhp = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';

try {
    $dataDir = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
    $jobsDir = rtrim((string)(getenv('REMASK_JOB_STORAGE_DIR') ?: ($dataDir . '/jobs')), '/');
    $logFile = $dataDir . '/worker.log';
    $tail = [];
    if (is_file($logFile)) {
        $lines = file($logFile, FILE_IGNORE_NEW_LINES) ?: [];
        $tail = array_slice($lines, -30);
    }
    $jobFiles = is_dir($jobsDir) ? (glob($jobsDir . '/*.json') ?: []) : [];
    MetaEndpoint::ok([
        'mode' => getenv('REMASK_JOB_EXECUTION_MODE') ?: 'browser',
        'data_dir' => $dataDir,
        'data_dir_exists' => is_dir($dataDir),
        'data_dir_writable' => is_writable($dataDir),
        'jobs_dir' => $jobsDir,
        'job_files' => count($jobFiles),
        'worker_log_exists' => is_file($logFile),
        'worker_log_tail' => $tail,
        'worker_pid_file' => is_file($dataDir . '/worker.pid') ? trim((string)file_get_contents($dataDir . '/worker.pid')) : '',
    ]);
} catch (Throwable $e) {
    MetaEndpoint::fail($e);
}
PHP;
file_put_contents($statusPath, $statusPhp);
fwrite(STDERR, "[remask worker overlay] ajax/metaWorkerStatus.php ready\n");
