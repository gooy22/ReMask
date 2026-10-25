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
    if ($_SERVER['REQUEST_METHOD'] === 'POST' && (int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 167772160)
        rmx_pwj_out(['ok'=>false,'error'=>'MEDIA_UPLOAD_TRANSPORT_LIMIT'], 413);
    $input = $_POST;
    // Authenticated maintenance GETs are read-only. Never accept a GET action
    // that creates/retries a job or changes a contract.
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        $requested = trim((string)($_GET['action'] ?? ''));
        if (in_array($requested, ['csrf','health','ready','payment_status','funding_status','preflight','profile_state','docids','private_launch_contracts','private_launch_sources','status','get','private_launch_media_preview'], true)) {
            $input['action'] = $requested;
            if ($requested === 'private_launch_media_preview') $input['id'] = $_GET['id'] ?? '';
        }
        return $input;
    }
    $raw = str_contains(strtolower((string)($_SERVER['CONTENT_TYPE'] ?? '')), 'application/json')
        ? (string)file_get_contents('php://input') : '';
    if ($raw !== '') {
        $json = json_decode($raw, true);
        if (is_array($json)) $input = array_replace($input, $json);
    }
    return $input;
}

final class RmxPwjWorkerHttpError extends RuntimeException {
    public array $detail;
    public function __construct(string $message, array $detail) {
        parent::__construct($message);
        $this->detail = $detail;
    }
}

function rmx_pwj_worker_request(string $method, string $path, ?array $payload = null, int $timeoutSeconds = 20): array {
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
        'timeout' => max(2, min($timeoutSeconds, 180)),
        'ignore_errors' => true,
        'follow_location' => 0,
    ];
    if ($body !== null) $http['content'] = $body;

    $context = stream_context_create(['http' => $http]);
    // Authentication and any CSRF/session writes have already completed.
    // Worker GETs are read-only; persist the session before awaiting them so
    // a slow readiness/status read cannot serialize the operator's other tabs.
    if (strtoupper($method) === 'GET' && session_status() === PHP_SESSION_ACTIVE) {
        session_write_close();
    }

    $raw = @file_get_contents($url, false, $context);

    $status = 0;
    foreach ((array)($http_response_header ?? []) as $line) {
        if (preg_match('#^HTTP/\\S+\\s+(\\d{3})#i', (string)$line, $m)) {
            $status = (int)$m[1];
        }
    }

    if ($raw === false) {
        // PHP transport diagnostics may contain private hostnames, headers,
        // request URLs or other service credentials. Never expose them.
        throw new RuntimeException('PYTHON_WORKER_TRANSPORT');
    }

    $decoded = json_decode($raw, true);
    // Worker endpoints return JSON objects, never bare arrays. PHP's
    // associative json_decode otherwise accepts both as PHP arrays.
    if (!is_array($decoded) || !str_starts_with(ltrim($raw), '{'))
        throw new RuntimeException('PYTHON_WORKER_INVALID_JSON');
    if ($status < 200 || $status >= 300) {
        // Upstream message text is untrusted and may contain tokens, URLs
        // or raw response fragments. Retain only safe, typed diagnostics.
        $detailValue = $decoded['detail'] ?? $decoded['error'] ?? null;
        $safe = [];
        if (is_array($detailValue)) {
            $code = $detailValue['code'] ?? null;
            if (is_string($code) && preg_match('/^[A-Z0-9_]{1,100}$/D', $code)) $safe['code'] = $code;
            // Validation messages for the private draft input validator are
            // fixed server-authored text. Preserve that one safe diagnostic so
            // the Launch form can name the rejected field without exposing raw
            // Meta responses, request payloads, or other upstream messages.
            $validationMessage = $detailValue['message'] ?? null;
            if ($code === 'PRIVATE_ADS_DRAFT_INVALID_INPUT' && is_string($validationMessage)) {
                $validationMessage = strip_tags($validationMessage);
                $validationMessage = preg_replace('/[\\x00-\\x1F\\x7F]+/u', ' ', $validationMessage);
                $validationMessage = preg_replace('#https?://[^\\s]+#i', '[URL]', $validationMessage);
                $validationMessage = trim($validationMessage);
                if ($validationMessage !== '' && strlen($validationMessage) <= 240
                    && !preg_match('/(?:cookie|authorization|access[_ -]?token|password|secret|fb_dtsg)/i', $validationMessage)) {
                    $safe['message'] = $validationMessage;
                }
            }
            if (($detailValue['retryable'] ?? false) === true) $safe['retryable'] = true;
        }
        throw new RmxPwjWorkerHttpError('PYTHON_WORKER_HTTP_' . $status, $safe);
    }
    return $decoded;
}

try {
    $input = rmx_pwj_input();
    $action = strtolower(trim((string)($input['action'] ?? 'status')));

    if ($action === 'csrf') {
        if (!function_exists('remask_csrf_token')) {
            rmx_pwj_out(['ok'=>false,'error'=>'CSRF_TOKEN_PROVIDER_UNAVAILABLE'], 500);
        }
        rmx_pwj_out(['ok'=>true,'csrf'=>remask_csrf_token()]);
    }

    if ($action === 'private_session_probe') {
        if (strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET')) !== 'POST') {
            rmx_pwj_out(['ok'=>false,'error'=>'METHOD_NOT_ALLOWED'], 405);
        }
        $profileId = trim((string)($input['profile_id'] ?? ''));
        $businessId = $input['business_id'] ?? null;
        $accountId = $input['ad_account_id'] ?? null;
        $cookies = $input['cookies'] ?? null;
        if ($profileId === '' || strlen($profileId) > 160
            || !is_string($businessId) || !preg_match('/^\d{5,30}$/D', $businessId)
            || !is_string($accountId) || !preg_match('/^\d{5,30}$/D', $accountId)
            || !is_array($cookies) || array_is_list($cookies) || count($cookies) < 2 || count($cookies) > 64) {
            rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_SESSION_PROBE_INVALID_INPUT'], 400);
        }
        if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
        $probe = rmx_pwj_worker_request('POST',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/private-session-probe',
            ['business_id'=>$businessId,'ad_account_id'=>$accountId,'cookies'=>$cookies], 50);
        rmx_pwj_out(['ok'=>true,'probe'=>$probe]);
    }

    if ($action === 'health' || $action === 'ready') {
        $health = rmx_pwj_worker_request('GET', '/health');

        $ready = null;
        $readinessError = '';
        try {
            $ready = rmx_pwj_worker_request('GET', '/ready');
        } catch (Throwable $readyError) {
            $readinessError = $readyError->getMessage();
            error_log('[python-worker-jobs] readiness degraded: ' . $readinessError);
        }

        rmx_pwj_out([
            'ok' => true,
            'worker' => $health,
            'ready' => is_array($ready) && (($ready['ready'] ?? false) === true),
            'readiness' => is_array($ready) ? $ready : null,
            'readiness_error' => $readinessError,
        ]);
    }

    if ($action === 'payment_status' || $action === 'funding_status') {
        $profileId = trim((string)($input['profile_id'] ?? $input['profile'] ?? ''));
        $accountId = preg_replace('/^act_/', '', trim((string)($input['account_id'] ?? '')));
        if ($profileId === '' || strlen($profileId) > 160 || !preg_match('/^\d{5,30}$/', $accountId)) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PAYMENT_TARGET'], 400);
        }
        $businessId=trim((string)($input['business_id']??''));
        $businessAssetId=trim((string)($input['business_asset_id']??''));
        $accountName=trim((string)($input['account_name']??''));
        if(($businessId!==''||$businessAssetId!==''||$accountName!=='')
            &&(!preg_match('/^\d{5,30}$/D',$businessId)
                ||($businessAssetId!==''&&!preg_match('/^\d{5,30}$/D',$businessAssetId))
                ||strlen($accountName)>160||preg_match('/[\x00-\x1f]/',$accountName))) {
            rmx_pwj_out(['ok'=>false,'error'=>'PAYMENT_ACCOUNT_BINDING_MISSING'],400);
        }
        $query=['account_id'=>$accountId];
        if($businessId!=='')$query['business_id']=$businessId;
        if($businessAssetId!=='')$query['business_asset_id']=$businessAssetId;
        if($accountName!=='')$query['account_name']=$accountName;
        $result = rmx_pwj_worker_request(
            'GET',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/payment-methods?' . http_build_query($query,'','&',PHP_QUERY_RFC3986),
            null,
            130
        );
        if ((string)($result['account_id'] ?? '') !== $accountId || (string)($result['profile_id'] ?? '') !== $profileId) {
            rmx_pwj_out(['ok'=>false,'error'=>'PAYMENT_RESULT_SCOPE_MISMATCH'], 409);
        }
        rmx_pwj_out(['ok'=>true,'profile'=>$profileId,'account_id'=>$accountId,'funding'=>$result]);
    }

    if ($action === 'preflight') {
        $profileId = trim((string)($input['profile_id'] ?? ''));
        if ($profileId === '' || strlen($profileId) > 160) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PROFILE_ID'], 400);
        }

        $purpose = trim((string)($input['purpose'] ?? 'business'));
        if (!in_array($purpose, ['business', 'fan_pages'], true)) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PREFLIGHT_PURPOSE'], 400);
        }
        $result = rmx_pwj_worker_request(
            'POST',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/preflight?purpose=' . rawurlencode($purpose),
            null,
            150
        );
        rmx_pwj_out(['ok'=>true,'preflight'=>$result]);
    }

    if ($action === 'profile_state') {
        $profileId = trim((string)($input['profile_id'] ?? ''));
        if ($profileId === '' || strlen($profileId) > 160) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PROFILE_ID'], 400);
        }

        $result = rmx_pwj_worker_request(
            'GET',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/provisioning-state'
        );
        rmx_pwj_out(['ok'=>true,'state'=>$result]);
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
        if (!in_array($operation, ['CREATE_BM','CREATE_AD_ACCOUNT','LIST_PAGES'], true)) {
            rmx_pwj_out(['ok'=>false,'error'=>'UNSUPPORTED_DOCID_OPERATION'], 400);
        }

        $docId = trim((string)($input['doc_id'] ?? ''));
        if (!preg_match('/^\d{5,40}$/', $docId)) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_DOC_ID'], 400);
        }

        $defaultMode = $operation === 'LIST_PAGES'
            ? 'account_quality_user_pages_v1'
            : (
                $operation === 'CREATE_AD_ACCOUNT'
                    ? 'business_ad_account_create_v1'
                    : 'scope_selector_business_creation_v1'
            );
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

    if ($action === 'private_launch_contracts') {
        $result = rmx_pwj_worker_request(
            'GET',
            '/api/v1/private-launch/contracts'
        );
        $contracts = is_array($result['contracts'] ?? null)
            ? $result['contracts']
            : [];
        $safe = [];
        foreach (['CAMPAIGN','ADSET','CREATIVE','AD'] as $step) {
            $row = is_array($contracts[$step] ?? null)
                ? $contracts[$step]
                : [];
            $safe[$step] = [
                'configured' => (($row['configured'] ?? false) === true),
                'active' => (($row['active'] ?? false) === true),
                'verification_configured' => (($row['verification_configured'] ?? false) === true),
                'verification_code' => trim((string)($row['verification_code'] ?? '')),
                'code' => trim((string)($row['code'] ?? '')),
                'message' => trim((string)($row['message'] ?? '')),
                'friendly_name' => trim((string)($row['friendly_name'] ?? '')),
                'endpoint_host' => trim((string)($row['endpoint_host'] ?? '')),
                'result_paths' => (int)($row['result_paths'] ?? 0),
            ];
        }
        $operations = [];
        foreach (['IMAGE_UPLOAD','VIDEO_UPLOAD','PUBLISH_FAMILY','ACTIVATE_CAMPAIGN','ACTIVATE_ADSET','ACTIVATE_AD'] as $name) {
            $row = is_array($result['operations'][$name] ?? null) ? $result['operations'][$name] : [];
            $operations[$name] = ['configured'=>(($row['configured'] ?? false) === true),
                'active'=>(($row['active'] ?? false) === true),
                'verification_configured'=>(($row['verification_configured'] ?? false) === true)];
        }
        $formats = array_values(array_intersect(['SINGLE','CAROUSEL','INSTAGRAM_POST'],
            is_array($result['creative_formats'] ?? null) ? $result['creative_formats'] : []));
        rmx_pwj_out(['ok'=>true,'contracts'=>$safe,'operations'=>$operations,'creative_formats'=>$formats]);
    }

    if ($action === 'private_launch_sources') {
        $result = rmx_pwj_worker_request('GET', '/api/v1/private-launch/maintenance-candidate');
        rmx_pwj_out($result);
    }

    if ($action === 'private_launch_media_preview') {
        if ($_SERVER['REQUEST_METHOD'] !== 'GET') rmx_pwj_out(['ok'=>false,'error'=>'GET_REQUIRED'], 405);
        $id = $input['id'] ?? '';
        if (!is_string($id) || !preg_match('/^m-[a-f0-9]{24}$/D', $id)) rmx_pwj_out(['ok'=>false,'error'=>'INVALID_MEDIA_REFERENCE'], 400);
        require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
        $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
        $row = $library->getInternal($id);
        $root = realpath(REMASK_MEDIA_LIBRARY_DIR);
        $path = is_array($row) ? realpath((string)($row['path'] ?? '')) : false;
        $mime = $row['mime_type'] ?? '';
        if ($root === false || $path === false || !in_array(dirname($path), [$root.'/files',$root.'/pg-cache'], true)
            || !in_array($mime, ['image/png','image/jpeg','video/mp4'], true)) rmx_pwj_out(['ok'=>false,'error'=>'MEDIA_PREVIEW_UNAVAILABLE'], 404);
        header('Content-Type: '.$mime); header('X-Content-Type-Options: nosniff');
        header("Content-Security-Policy: default-src 'none'; sandbox");
        header('Content-Length: '.filesize($path)); readfile($path); exit;
    }

    if ($action === 'private_launch_media_resolve') {
        // Filesystem paths are an internal worker capability, never a UI API.
        if (!$internalAuthorized) rmx_pwj_out(['ok'=>false,'error'=>'INTERNAL_MEDIA_ACCESS_REQUIRED'], 403);
        if ($_SERVER['REQUEST_METHOD'] !== 'POST') rmx_pwj_out(['ok'=>false,'error'=>'POST_REQUIRED'], 405);
        $ids = $input['ids'] ?? null;
        if (!is_array($ids) || count($ids) < 1 || count($ids) > 10) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_MEDIA_SELECTION'], 400);
        }
        require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
        $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
        $root = realpath(REMASK_MEDIA_LIBRARY_DIR);
        $records = [];
        foreach (array_unique($ids) as $id) {
            if (!is_string($id) || !preg_match('/^m-[a-f0-9]{24}$/D', $id)) {
                rmx_pwj_out(['ok'=>false,'error'=>'INVALID_MEDIA_REFERENCE'], 400);
            }
            $row = $library->getInternal($id);
            $path = is_array($row) ? realpath((string)($row['path'] ?? '')) : false;
            if ($root === false || $path === false || !is_readable($path)
                || !in_array(dirname($path), [$root.'/files',$root.'/pg-cache'], true)) {
                rmx_pwj_out(['ok'=>false,'error'=>'MEDIA_LIBRARY_ITEM_UNAVAILABLE'], 404);
            }
            $safe = array_intersect_key($row, array_flip(['id','sha256','size_bytes','media_type','mime_type']));
            $records[] = array_replace($safe, ['path'=>$path]);
        }
        rmx_pwj_out(['ok'=>true,'media'=>$records]);
    }

    if (in_array($action, ['private_launch_media_inspect','private_launch_media_prepare'], true)) {
        if ($_SERVER['REQUEST_METHOD'] !== 'POST') rmx_pwj_out(['ok'=>false,'error'=>'POST_REQUIRED'], 405);
        $path = $action === 'private_launch_media_inspect' ? 'media-inspect' : 'media-prepare';
        $request = $path === 'media-inspect'
            ? (is_array($input['launch'] ?? null)
                ? ['launch'=>$input['launch']]
                : ['creative'=>$input['creative'] ?? null,'targeting'=>$input['targeting'] ?? []])
            : ['reference'=>$input['reference'] ?? null,'options'=>$input['options'] ?? null];
        rmx_pwj_out(rmx_pwj_worker_request('POST', '/api/v1/private-launch/'.$path, $request, 180));
    }

    if ($action === 'private_launch_plan') {
        if ($_SERVER['REQUEST_METHOD'] !== 'POST') rmx_pwj_out(['ok'=>false,'error'=>'POST_REQUIRED'], 405);
        $profileId = trim((string)($input['profile_id'] ?? ''));
        if ($profileId === '' || strlen($profileId) > 160 || !is_array($input['launch'] ?? null)) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_ADVERTISING_PLAN'], 400);
        }
        $plan = rmx_pwj_worker_request('POST', '/api/v1/private-launch/plan', [
            'profile_id'=>$profileId,'business_id'=>$input['business_id'] ?? '',
            'ad_account_id'=>$input['ad_account_id'] ?? '', 'page_id'=>$input['page_id'] ?? '',
            'launch'=>$input['launch'],
        ]);
        rmx_pwj_out(['ok'=>true,'plan'=>$plan]);
    }

    if ($action === 'private_launch_media') {
        if ($_SERVER['REQUEST_METHOD'] !== 'POST') rmx_pwj_out(['ok'=>false,'error'=>'POST_REQUIRED'], 405);
        require_once __DIR__ . '/../classes/MediaLibraryStoreFactory.php';
        $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
        $upload = $_FILES['media'] ?? null;
        if (is_array($upload) && in_array(($upload['error'] ?? null), [UPLOAD_ERR_INI_SIZE,UPLOAD_ERR_FORM_SIZE], true))
            rmx_pwj_out(['ok'=>false,'error'=>'MEDIA_UPLOAD_TRANSPORT_LIMIT'], 413);
        if (!is_array($upload) || ($upload['error'] ?? UPLOAD_ERR_NO_FILE) !== UPLOAD_ERR_OK
            || !is_uploaded_file((string)($upload['tmp_name'] ?? ''))) {
            rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_LAUNCH_MEDIA_UPLOAD_INVALID'], 400);
        }
        $record = $library->add($upload['tmp_name'], (string)($upload['name'] ?? 'media'));
        // Reuse the durable content-addressed library. Never expose a path or
        // treat a local library ID as a Meta image hash/video ID.
        rmx_pwj_out(['ok'=>true,'media'=>array_intersect_key($record,
            array_flip(['id','sha256','size_bytes','media_type','mime_type']))]);
    }

    if ($action === 'private_launch_review') {
        $profileId = trim((string)($input['profile_id'] ?? $input['profile'] ?? ''));
        $businessId = trim((string)($input['business_id'] ?? ''));
        $accountId = preg_replace('/^act_/', '', trim((string)($input['ad_account_id'] ?? $input['account_id'] ?? '')));
        $pageId = trim((string)($input['page_id'] ?? ''));
        $launch = $input['launch'] ?? null;

        if ($profileId === '' || strlen($profileId) > 160) {
            rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PROFILE_ID'], 400);
        }
        foreach ([
            'business_id'=>$businessId,
            'ad_account_id'=>$accountId,
            'page_id'=>$pageId,
        ] as $label=>$value) {
            if (!preg_match('/^\d{5,30}$/D', (string)$value)) {
                rmx_pwj_out(['ok'=>false,'error'=>'INVALID_PRIVATE_LAUNCH_TARGET','field'=>$label], 400);
            }
        }
        if (!is_array($launch)) {
            rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_LAUNCH_PAYLOAD_REQUIRED'], 400);
        }

        // Mutation doc_ids, fb_dtsg and request envelopes are intentionally
        // server-side only. The browser may supply ad configuration, never a
        // private Meta transport contract.
        $forbidden = ['doc_id','docId','fb_dtsg','jazoest','request_envelope','endpoint_url'];
        $walk = function($value) use (&$walk, $forbidden): void {
            if (!is_array($value)) return;
            foreach ($value as $key=>$child) {
                if (in_array((string)$key, $forbidden, true)) {
                    rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_LAUNCH_TRANSPORT_FIELD_FORBIDDEN'], 400);
                }
                if (is_array($child)) $walk($child);
            }
        };
        $walk($launch);

        $reviewPayload = [
            'business_id'=>$businessId,
            'ad_account_id'=>$accountId,
            'page_id'=>$pageId,
            'launch'=>$launch,
        ];
        if (isset($input['selected_payment_method'])) {
            $selector = $input['selected_payment_method'];
            if (!is_array($selector) || array_diff(array_keys($selector), ['type','brand','last4'])
                || !preg_match('/^\d{4}$/D', (string)($selector['last4'] ?? ''))) {
                rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID'], 400);
            }
            $brand = trim((string)($selector['brand'] ?? $selector['type'] ?? ''));
            if ($brand !== '' && !preg_match('/^[A-Za-z][A-Za-z .+-]{0,39}$/D', $brand)) {
                rmx_pwj_out(['ok'=>false,'error'=>'PRIVATE_LAUNCH_PAYMENT_SELECTION_INVALID'], 400);
            }
            $reviewPayload['selected_payment_method'] = ['type'=>$brand,'last4'=>(string)$selector['last4']];
        }
        // Auth and CSRF have already been checked. A read-only Meta review
        // must not hold the operator's PHP session lock while awaiting HTTP.
        if (session_status() === PHP_SESSION_ACTIVE) session_write_close();
        $review = rmx_pwj_worker_request(
            'POST',
            '/api/v1/profiles/' . rawurlencode($profileId) . '/private-launch-review',
            $reviewPayload,
            180
        );
        rmx_pwj_out(['ok'=>true,'review'=>$review]);
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
        $consent = ($input['consent_page_policies'] ?? false) === true
            ? ['consent_page_policies'=>true] : null;
        $result = rmx_pwj_worker_request('POST', '/api/v1/jobs/' . rawurlencode($jobId) . '/retry-failed', $consent, 120);
        rmx_pwj_out(['ok'=>true,'result'=>$result]);
    }

    rmx_pwj_out(['ok'=>false,'error'=>'UNSUPPORTED_ACTION'], 400);
} catch (Throwable $e) {
    $message = $e->getMessage();
    $safeMessage = preg_match('/^[A-Z][A-Z0-9_]{2,100}$/D', $message)
        ? $message : 'PYTHON_WORKER_FAILED';
    error_log('[python-worker-jobs] ' . get_class($e) . ': ' . $safeMessage);
    if (($action ?? '') === 'private_launch_review' && $e instanceof RmxPwjWorkerHttpError
        && preg_match('/^[A-Z0-9_]{1,100}$/D', (string)($e->detail['code'] ?? ''))) {
        // Preserve the typed readiness failure across PHP. Do not make the
        // browser recover control flow from JSON embedded in an error string.
        rmx_pwj_out(['ok'=>false, 'error'=>$e->detail['code'], 'code'=>$e->detail['code'],
            'message'=>$e->detail['message'] ?? $e->detail['code'],
            'retryable'=>($e->detail['retryable'] ?? false) === true], 502);
    }
    if (str_starts_with($message, 'PYTHON_WORKER_NOT_CONFIGURED')) {
        rmx_pwj_out(['ok'=>false,'error'=>'PYTHON_WORKER_NOT_CONFIGURED'], 503);
    }
    rmx_pwj_out(['ok'=>false,'error'=>'PYTHON_WORKER_FAILED','message'=>$safeMessage], 502);
}
PHP_CODE;

file_put_contents($target, $php);
fwrite(STDERR, "[python-worker-jobs] same-origin PHP job bridge installed\n");
