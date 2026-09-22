<?php
/**
 * Persistent Python-worker job snapshot mirror.
 * Stores non-secret Job/JobItem/Task state on ReMask's existing persistent
 * /var/lib/remask volume. Accessible only with REMASK_INTERNAL_KEY.
 */
$root = '/var/www/html';
$target = $root . '/ajax/pythonWorkerState.php';

$php = <<<'PHP_CODE'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function rmx_pws_out(array $payload, int $status = 200): void {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function rmx_pws_header(string $name): string {
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    return trim((string)($_SERVER[$key] ?? ''));
}

function rmx_pws_input(): array {
    $raw = (string)file_get_contents('php://input');
    if ($raw === '') return $_POST;
    $json = json_decode($raw, true);
    return is_array($json) ? $json : $_POST;
}

function rmx_pws_has_forbidden_key(mixed $value): bool {
    static $forbidden = [
        'card_number' => true,
        'cvv' => true,
        'cvc' => true,
        'csc' => true,
        'pan' => true,
    ];
    if (!is_array($value)) return false;
    foreach ($value as $key => $child) {
        if (is_string($key) && isset($forbidden[strtolower($key)])) return true;
        if (rmx_pws_has_forbidden_key($child)) return true;
    }
    return false;
}

function rmx_pws_dir(): string {
    $dataDir = rtrim((string)(getenv('REMASK_DATA_DIR') ?: (getenv('RAILWAY_VOLUME_MOUNT_PATH') ?: '/var/lib/remask')), '/');
    $dir = rtrim((string)(getenv('REMASK_PYTHON_STATE_DIR') ?: ($dataDir . '/python-worker-jobs')), '/');
    if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
        throw new RuntimeException('STATE_DIR_CREATE_FAILED');
    }
    return $dir;
}

function rmx_pws_job_path(string $id): string {
    if (!preg_match('/^[a-zA-Z0-9_-]{8,160}$/', $id)) {
        throw new InvalidArgumentException('INVALID_JOB_ID');
    }
    return rmx_pws_dir() . '/' . $id . '.json';
}

try {
    $expected = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
    if ($expected === '') rmx_pws_out(['ok'=>false,'error'=>'INTERNAL_KEY_NOT_CONFIGURED'], 503);
    $provided = rmx_pws_header('X-Remask-Internal-Key');
    if ($provided === '' || !hash_equals($expected, $provided)) {
        rmx_pws_out(['ok'=>false,'error'=>'UNAUTHORIZED'], 401);
    }

    $input = rmx_pws_input();
    $action = strtolower(trim((string)($input['action'] ?? $_GET['action'] ?? 'health')));

    if ($action === 'health') {
        $dir = rmx_pws_dir();
        rmx_pws_out(['ok'=>true,'service'=>'remask-python-worker-state','writable'=>is_writable($dir)]);
    }

    if ($action === 'save') {
        $job = $input['job'] ?? null;
        if (!is_array($job)) rmx_pws_out(['ok'=>false,'error'=>'JOB_REQUIRED'], 400);
        if (rmx_pws_has_forbidden_key($job)) {
            rmx_pws_out(['ok'=>false,'error'=>'RAW_PAYMENT_DATA_REJECTED'], 400);
        }
        $id = trim((string)($job['id'] ?? ''));
        $path = rmx_pws_job_path($id);
        $encoded = json_encode($job, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $tmp = $path . '.tmp.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp, $encoded, LOCK_EX) === false) {
            throw new RuntimeException('STATE_WRITE_FAILED');
        }
        @chmod($tmp, 0600);
        if (!rename($tmp, $path)) {
            @unlink($tmp);
            throw new RuntimeException('STATE_RENAME_FAILED');
        }
        rmx_pws_out(['ok'=>true,'job_id'=>$id,'bytes'=>strlen($encoded)]);
    }

    if ($action === 'list') {
        $dir = rmx_pws_dir();
        $files = glob($dir . '/*.json') ?: [];
        usort($files, static fn(string $a, string $b): int => ((int)@filemtime($b)) <=> ((int)@filemtime($a)));
        $jobs = [];
        foreach (array_slice($files, 0, 1000) as $file) {
            $raw = @file_get_contents($file);
            if ($raw === false || $raw === '') continue;
            $job = json_decode($raw, true);
            if (is_array($job)) $jobs[] = $job;
        }
        rmx_pws_out(['ok'=>true,'count'=>count($jobs),'jobs'=>$jobs]);
    }

    if ($action === 'get') {
        $id = trim((string)($input['job_id'] ?? $_GET['job_id'] ?? ''));
        $path = rmx_pws_job_path($id);
        if (!is_file($path)) rmx_pws_out(['ok'=>false,'error'=>'JOB_NOT_FOUND'], 404);
        $raw = (string)file_get_contents($path);
        $job = json_decode($raw, true);
        if (!is_array($job)) throw new RuntimeException('STATE_INVALID_JSON');
        rmx_pws_out(['ok'=>true,'job'=>$job]);
    }

    rmx_pws_out(['ok'=>false,'error'=>'UNSUPPORTED_ACTION'], 400);
} catch (Throwable $e) {
    error_log('[python-worker-state] ' . get_class($e) . ': ' . $e->getMessage());
    rmx_pws_out(['ok'=>false,'error'=>'PYTHON_WORKER_STATE_FAILED'], 500);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[python-worker-state] persistent snapshot endpoint installed\n");
