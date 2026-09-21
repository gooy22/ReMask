<?php
/**
 * ReMask PHP -> Python worker job bridge.
 * Browser talks only to same-origin PHP; PHP talks to the worker over Railway
 * private networking and attaches the worker API key server-side.
 */
$root = '/var/www/html';
$target = $root . '/ajax/pythonWorkerJobs.php';

$php = <<<'PHP_CODE'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

ob_start();
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function rmx_pwj_out(array $payload, int $status = 200): void {
    http_response_code($status);
    while (ob_get_level() > 0) { @ob_end_clean(); }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function rmx_pwj_input(): array {
    $input = $_POST;
    $raw = (string)file_get_contents('php://input');
    if ($raw !== '') {
        $json = json_decode($raw, true);
        if (is_array($json)) $input = array_replace($input, $json);
    }
    return $input;
}

function rmx_pwj_worker_request(string $method, string $path, ?array $payload = null): array {
    $base = rtrim(trim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: '')), '/');
    if ($base === '') throw new RuntimeException('PYTHON_WORKER_NOT_CONFIGURED');

    $url = $base . '/' . ltrim($path, '/');
    $headers = ['Accept: application/json'];
    $key = trim((string)(getenv('REMASK_WORKER_API_KEY') ?: ''));
    if ($key !== '') $headers[] = 'X-Remask-Worker-Key: ' . $key;

    $ch = curl_init($url);
    $opts = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HEADER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT => 20,
        CURLOPT_CUSTOMREQUEST => strtoupper($method),
        CURLOPT_HTTPHEADER => $headers,
    ];

    if ($payload !== null) {
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $opts[CURLOPT_POSTFIELDS] = $body;
        $opts[CURLOPT_HTTPHEADER][] = 'Content-Type: application/json';
    }

    curl_setopt_array($ch, $opts);
    $raw = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
    curl_close($ch);

    if ($raw === false) {
        throw new RuntimeException('PYTHON_WORKER_TRANSPORT: ' . ($error ?: ('cURL errno ' . $errno)));
    }

    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) throw new RuntimeException('PYTHON_WORKER_INVALID_JSON');
    if ($status < 200 || $status >= 300) {
        $detail = trim((string)($decoded['detail'] ?? $decoded['error'] ?? ('HTTP ' . $status)));
        throw new RuntimeException('PYTHON_WORKER_HTTP_' . $status . ': ' . $detail);
    }
    return $decoded;
}

try {
    $input = rmx_pwj_input();
    $action = strtolower(trim((string)($input['action'] ?? 'status')));

    if ($action === 'create') {
        $profiles = $input['profiles'] ?? null;
        if (!is_array($profiles) || $profiles === []) {
            rmx_pwj_out(['ok'=>false,'error'=>'PROFILES_REQUIRED'], 400);
        }

        $payload = ['profiles'=>$profiles];
        $idem = trim((string)($input['idempotency_key'] ?? ''));
        if ($idem !== '') $payload['idempotency_key'] = $idem;

        $job = rmx_pwj_worker_request('POST', '/api/v1/jobs', $payload);
        rmx_pwj_out(['ok'=>true,'job'=>$job]);
    }

    $jobId = trim((string)($input['job_id'] ?? $_GET['job_id'] ?? ''));
    if ($jobId === '') rmx_pwj_out(['ok'=>false,'error'=>'JOB_ID_REQUIRED'], 400);
    if (!preg_match('/^[a-zA-Z0-9_-]{8,160}$/', $jobId)) {
        rmx_pwj_out(['ok'=>false,'error'=>'INVALID_JOB_ID'], 400);
    }

    if ($action === 'status' || $action === 'get') {
        $job = rmx_pwj_worker_request('GET', '/api/v1/jobs/' . rawurlencode($jobId));
        rmx_pwj_out(['ok'=>true,'job'=>$job]);
    }

    if ($action === 'retry' || $action === 'retry_failed') {
        $result = rmx_pwj_worker_request('POST', '/api/v1/jobs/' . rawurlencode($jobId) . '/retry-failed');
        rmx_pwj_out(['ok'=>true,'result'=>$result]);
    }

    rmx_pwj_out(['ok'=>false,'error'=>'UNSUPPORTED_ACTION'], 400);
} catch (Throwable $e) {
    error_log('[python-worker-jobs] ' . get_class($e) . ': ' . $e->getMessage());
    $message = $e->getMessage();
    if (str_starts_with($message, 'PYTHON_WORKER_NOT_CONFIGURED')) {
        rmx_pwj_out(['ok'=>false,'error'=>'PYTHON_WORKER_NOT_CONFIGURED'], 503);
    }
    rmx_pwj_out(['ok'=>false,'error'=>'PYTHON_WORKER_FAILED','message'=>$message], 502);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[python-worker-jobs] same-origin PHP job bridge installed\n");
