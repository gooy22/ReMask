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

$expectedInternal = trim((string)(getenv('REMASK_INTERNAL_KEY') ?: ''));
$providedInternal = trim((string)($_SERVER['HTTP_X_REMASK_INTERNAL_KEY'] ?? ''));
$internalAuthorized = $expectedInternal !== ''
    && $providedInternal !== ''
    && hash_equals($expectedInternal, $providedInternal);

if (!$internalAuthorized) {
    require_once __DIR__ . '/../checkpassword.php';
}
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
    $base = rtrim(trim((string)(getenv('REMASK_PYTHON_WORKER_URL') ?: 'http://127.0.0.1:8081')), '/');

    $url = $base . '/' . ltrim($path, '/');
    $headers = ['Accept: application/json'];
    $key = trim((string)(getenv('REMASK_WORKER_API_KEY') ?: ''));
    if ($key !== '') $headers[] = 'X-Remask-Worker-Key: ' . $key;

    $body = null;
    if ($payload !== null) {
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        $headers[] = 'Content-Type: application/json';
    }

    $http = [
        'method' => strtoupper($method),
        'header' => implode("\r\n", $headers) . "\r\n",
        'timeout' => 20,
        'ignore_errors' => true,
        'follow_location' => 0,
    ];
    if ($body !== null) $http['content'] = $body;

    $context = stream_context_create(['http' => $http]);
    $raw = @file_get_contents($url, false, $context);

    $status = 0;
    foreach ((array)($http_response_header ?? []) as $line) {
        if (preg_match('#^HTTP/\\S+\\s+(\\d{3})#i', (string)$line, $m)) {
            $status = (int)$m[1];
        }
    }

    if ($raw === false) {
        $last = error_get_last();
        $detail = is_array($last) ? trim((string)($last['message'] ?? '')) : '';
        throw new RuntimeException('PYTHON_WORKER_TRANSPORT' . ($detail !== '' ? ': ' . $detail : ''));
    }

    $decoded = json_decode($raw, true);
    if (!is_array($decoded)) throw new RuntimeException('PYTHON_WORKER_INVALID_JSON');
    if ($status < 200 || $status >= 300) {
        $detailValue = $decoded['detail'] ?? $decoded['error'] ?? ('HTTP ' . $status);
        $detail = is_scalar($detailValue)
            ? trim((string)$detailValue)
            : json_encode($detailValue, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        throw new RuntimeException('PYTHON_WORKER_HTTP_' . $status . ': ' . ($detail ?: ('HTTP ' . $status)));
    }
    return $decoded;
}

try {
    $input = rmx_pwj_input();
    $action = strtolower(trim((string)($input['action'] ?? 'status')));

    if ($action === 'health' || $action === 'ready') {
        $health = rmx_pwj_worker_request('GET', '/ready');
        rmx_pwj_out(['ok'=>true,'worker'=>$health]);
    }

    if ($action === 'preflight') {
        $profileId = trim((string)($input['profile_id'] ?? ''));
        if ($profileId === '' || strlen($profileId) > 160) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PROFILE_ID'], 400);
        }

        $result = rmx_pwj_worker_request(
            'POST',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/preflight'
        );
        rmx_pwj_out(['ok'=>true,'preflight'=>$result]);
    }

    if ($action === 'docids') {
        $operation = trim((string)($input['operation'] ?? $_GET['operation'] ?? ''));
        $path = '/api/v1/facebook/docids';
        if ($operation !== '') {
            $path .= '?operation=' . rawurlencode(strtoupper($operation));
        }
        $result = rmx_pwj_worker_request('GET', $path);
        rmx_pwj_out(['ok'=>true,'registry'=>$result]);
    }

    if ($action === 'register_docid') {
        $operation = strtoupper(trim((string)($input['operation'] ?? 'CREATE_BM')));
        if (!in_array($operation, ['CREATE_BM','LIST_PAGES'], true)) {
            rmx_pwj_out(['ok'=>false,'error'=>'UNSUPPORTED_DOCID_OPERATION'], 400);
        }

        $docId = trim((string)($input['doc_id'] ?? ''));
        if (!preg_match('/^\d{5,40}$/', $docId)) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_DOC_ID'], 400);
        }

        $defaultMode = $operation === 'LIST_PAGES'
            ? 'account_quality_user_pages_v1'
            : 'scope_selector_business_creation_v1';
        $defaultEndpoint = $operation === 'LIST_PAGES'
            ? 'https://www.facebook.com/api/graphql/'
            : 'https://business.facebook.com/api/graphql/';

        $candidate = [
            'doc_id' => $docId,
            'friendly_name' => trim((string)($input['friendly_name'] ?? '')),
            'variables_mode' => trim((string)($input['variables_mode'] ?? $defaultMode)),
            'endpoint_url' => trim((string)($input['endpoint_url'] ?? $defaultEndpoint)),
            'source' => trim((string)($input['source'] ?? 'manual_ui')),
            'priority' => (int)($input['priority'] ?? 7500),
            'observed_at' => trim((string)($input['observed_at'] ?? '')),
        ];

        $result = rmx_pwj_worker_request(
            'POST',
            '/api/v1/facebook/docids/' . rawurlencode($operation),
            $candidate
        );
        rmx_pwj_out(['ok'=>true,'registry'=>$result]);
    }

    if ($action === 'create') {
        $profiles = $input['profiles'] ?? null;
        if (!is_array($profiles) || $profiles === []) {
            rmx_pwj_out(['ok'=>false,'error'=>'PROFILES_REQUIRED'], 400);
        }

        // Preserve JSON object semantics for empty task payloads.
        foreach ($profiles as $pi => $profileRow) {
            if (!is_array($profileRow)) continue;
            $tasks = $profileRow['tasks'] ?? [];
            if (!is_array($tasks)) continue;
            foreach ($tasks as $ti => $taskRow) {
                if (!is_array($taskRow)) continue;

                // Dynamic fields are forwarded as-is, but private Facebook
                // browser-session routing is explicitly not accepted here.
                foreach (['doc_id','fb_dtsg'] as $forbiddenKey) {
                    if (array_key_exists($forbiddenKey, $taskRow)) {
                        rmx_pwj_out([
                            'ok'=>false,
                            'error'=>'PRIVATE_SESSION_ROUTE_NOT_ALLOWED',
                            'message'=>'Private Facebook browser-session fields are not supported by the worker bridge.',
                        ], 400);
                    }
                }

                if (isset($taskRow['route'])) {
                    $route = trim((string)$taskRow['route']);
                    if ($route === '' || str_contains($route, '://') || !preg_match('/^[A-Za-z0-9._-]{1,120}$/', $route)) {
                        rmx_pwj_out(['ok'=>false,'error'=>'INVALID_TASK_ROUTE'], 400);
                    }
                    $profiles[$pi]['tasks'][$ti]['route'] = $route;
                }

                if (!array_key_exists('payload', $taskRow) || $taskRow['payload'] === []) {
                    $profiles[$pi]['tasks'][$ti]['payload'] = (object)[];
                }
                if (!array_key_exists('variables', $taskRow) || $taskRow['variables'] === []) {
                    $profiles[$pi]['tasks'][$ti]['variables'] = (object)[];
                } elseif (!is_array($taskRow['variables'])) {
                    rmx_pwj_out(['ok'=>false,'error'=>'TASK_VARIABLES_MUST_BE_OBJECT'], 400);
                }
            }
        }

        $payload = ['profiles'=>$profiles];
        $idem = trim((string)($input['idempotency_key'] ?? ''));
        if ($idem !== '') $payload['idempotency_key'] = $idem;

        $job = rmx_pwj_worker_request('POST', '/api/v1/jobs', $payload);
        $jobId = trim((string)($job['job_id'] ?? ''));
        error_log(
            '[python-worker-jobs] create accepted job=' . ($jobId !== '' ? $jobId : '<missing>')
            . ' profiles=' . count($profiles)
        );
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
