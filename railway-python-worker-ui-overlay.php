<?php
/**
 * Installs the Python worker bulk provisioning panel into the existing Workspace.
 */
$phpPath = '/var/www/html/workspace.php';
$jsPath = '/var/www/html/scripts/workspace.js';
$addonPath = '/tmp/railway-python-worker-ui.js';
$pagesEndpointPath = '/var/www/html/ajax/pythonWorkerPages.php';

$pagesEndpoint = <<<'PHP_PAGES'
<?php
declare(strict_types=1);

ini_set('display_errors', '0');
ini_set('html_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

ob_start();
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaEndpoint.php';
while (ob_get_level() > 0) { @ob_end_clean(); }

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function rmx_pwp_out(array $payload, int $status = 200): void {
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

try {
    $raw = (string)file_get_contents('php://input');
    $input = $_POST;
    if ($raw !== '') {
        $json = json_decode($raw, true);
        if (is_array($json)) $input = array_replace($input, $json);
    }

    $profile = trim((string)($input['profile'] ?? $_GET['profile'] ?? ''));
    if ($profile === '') {
        rmx_pwp_out(['ok'=>false,'error'=>'PROFILE_REQUIRED'], 400);
    }

    $result = MetaEndpoint::peekCachedAsset($profile, 'pages', '');
    $rows = is_array($result['data'] ?? null) ? $result['data'] : [];

    if ($rows === []) {
        $result = MetaEndpoint::cachedAsset($profile, 'pages', '', false);
        $rows = is_array($result['data'] ?? null) ? $result['data'] : [];
    }

    $pages = [];

    foreach ($rows as $row) {
        if (!is_array($row)) continue;
        $id = trim((string)($row['id'] ?? ''));
        if ($id === '') continue;
        $pages[] = [
            'id' => $id,
            'name' => trim((string)($row['name'] ?? $id)),
            'category' => trim((string)($row['category'] ?? '')),
        ];
    }

    rmx_pwp_out([
        'ok' => true,
        'profile' => $profile,
        'pages' => $pages,
        'count' => count($pages),
    ]);
} catch (Throwable $e) {
    error_log('[python-worker-pages] ' . get_class($e) . ': ' . $e->getMessage());
    $detail = ['message'=>$e->getMessage(), 'type'=>get_class($e)];
    if (method_exists($e, 'toArray')) {
        try { $detail = array_replace($detail, (array)$e->toArray()); } catch (Throwable) {}
    }
    rmx_pwp_out(['ok'=>false,'error'=>'PAGES_LOAD_FAILED','detail'=>$detail], 502);
}
PHP_PAGES;

file_put_contents($pagesEndpointPath, $pagesEndpoint);

$php = file_get_contents($phpPath);
$js = file_get_contents($jsPath);
$addon = file_get_contents($addonPath);

if ($php === false || $js === false || $addon === false) {
    throw new RuntimeException('Python worker UI runtime inputs are missing.');
}

if (strpos($php, 'REMASK_PYTHON_WORKER_PANEL_V1') === false) {
    $panel = <<<'HTML'
<!-- REMASK_PYTHON_WORKER_PANEL_V1 -->
<style>
#pythonWorkerPanel{margin-top:18px;border:1px solid #383f4b;border-radius:10px;background:#20242b;padding:16px}
#pythonWorkerPanel .pw-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}
#pythonWorkerPanel .pw-title{font-size:16px;font-weight:700}
#pythonWorkerPanel .pw-sub{font-size:12px;color:#9aa4b2;margin-top:3px}
#pythonWorkerPanel .pw-health{display:inline-flex;align-items:center;gap:6px;margin-top:7px;font-size:11px;color:#9aa4b2}
#pythonWorkerPanel .pw-health::before{content:'';width:7px;height:7px;border-radius:50%;background:#6b7280;display:inline-block}
#pythonWorkerPanel .pw-health[data-state="online"]{color:#9fe3b2}
#pythonWorkerPanel .pw-health[data-state="online"]::before{background:#3fbf73}
#pythonWorkerPanel .pw-health[data-state="offline"]{color:#ff9a9a}
#pythonWorkerPanel .pw-health[data-state="offline"]::before{background:#e85d68}
#pythonWorkerPanel .pw-actions{display:flex;gap:8px;flex-wrap:wrap}
#pythonWorkerPanel .pw-actions button{min-height:34px}
#pythonWorkerPanel .pw-meta{display:grid;grid-template-columns:repeat(5,minmax(90px,1fr));gap:8px;margin-top:14px}
#pythonWorkerPanel .pw-card{border:1px solid #343b46;border-radius:8px;padding:9px 10px;background:#1b1f25}
#pythonWorkerPanel .pw-card b{display:block;font-size:18px}
#pythonWorkerPanel .pw-card span{font-size:11px;color:#929cab;text-transform:uppercase;letter-spacing:.04em}
#pythonWorkerPanel .pw-progress{height:9px;border-radius:999px;background:#11151a;overflow:hidden;margin-top:14px;border:1px solid #333a44}
#pythonWorkerPanel .pw-progress>div{height:100%;width:0%;background:#5d8fe8;transition:width .2s ease}
#pythonWorkerPanel .pw-status{margin-top:10px;font-size:12px;color:#aeb7c4;white-space:pre-wrap}
#pythonWorkerPanel .pw-job{margin-top:6px;font-size:11px;color:#7f8998;word-break:break-all}
#pythonWorkerPanel .pw-table-wrap{margin-top:12px;max-height:310px;overflow:auto;border:1px solid #343b46;border-radius:8px}
#pythonWorkerPanel table{width:100%;border-collapse:collapse;font-size:12px}
#pythonWorkerPanel th,#pythonWorkerPanel td{padding:8px 9px;border-bottom:1px solid #303640;text-align:left;vertical-align:top}
#pythonWorkerPanel th{position:sticky;top:0;background:#262b33;z-index:1}
#pythonWorkerPanel tr:last-child td{border-bottom:0}
#pythonWorkerPanel .pw-pill{display:inline-block;padding:2px 7px;border-radius:999px;font-size:10px;font-weight:700;background:#343b46}
#pythonWorkerPanel .pw-QUEUED{background:#4b5563}
#pythonWorkerPanel .pw-RUNNING{background:#315f91}
#pythonWorkerPanel .pw-SUCCESS{background:#2f6b48}
#pythonWorkerPanel .pw-FAILED{background:#7f3f46}
#pythonWorkerPanel .pw-PARTIAL{background:#806626}
#pythonWorkerPanel .pw-error{max-width:460px;white-space:pre-wrap;word-break:break-word;color:#d4a4a8}
@media(max-width:900px){#pythonWorkerPanel .pw-meta{grid-template-columns:repeat(2,minmax(90px,1fr))}}
</style>
<section id="pythonWorkerPanel">
  <div class="pw-head">
    <div>
      <div class="pw-title">Mass provisioning</div>
      <div class="pw-sub">Выбранные FB-профили → Job → worker queue → profile context → proxy_check → private GraphQL.</div>
      <div id="pythonPwWorkerHealth" class="pw-health" data-state="checking">Worker: проверяю…</div>
    </div>
    <div class="pw-actions">
      <button id="pythonProvisionStart" type="button" class="btn btn-primary" disabled>Запустить provisioning</button>
      <button id="pythonProvisionRetry" type="button" class="btn btn-secondary" disabled>Retry Failed</button>
    </div>
  </div>
  <div class="pw-meta">
    <div class="pw-card"><b id="pythonPwTotal">0</b><span>Total</span></div>
    <div class="pw-card"><b id="pythonPwQueued">0</b><span>Queued</span></div>
    <div class="pw-card"><b id="pythonPwRunning">0</b><span>Running</span></div>
    <div class="pw-card"><b id="pythonPwSuccess">0</b><span>Success</span></div>
    <div class="pw-card"><b id="pythonPwFailed">0</b><span>Failed</span></div>
  </div>
  <div class="pw-progress"><div id="pythonPwProgress"></div></div>
  <div id="pythonPwStatus" class="pw-status">Выберите FB-профили в Workspace.</div>
  <div id="pythonPwJob" class="pw-job"></div>
  <div class="pw-table-wrap">
    <table>
      <thead><tr><th>FB profile</th><th>Status</th><th>Step</th><th>Error</th></tr></thead>
      <tbody id="pythonPwRows"><tr><td colspan="4">Нет активного Job.</td></tr></tbody>
    </table>
  </div>
</section>
HTML;

    $bodyPos = strripos($php, '</body>');
    if ($bodyPos === false) {
        throw new RuntimeException('workspace.php closing body tag not found.');
    }
    $php = substr($php, 0, $bodyPos) . $panel . "\n" . substr($php, $bodyPos);

    $php = preg_replace(
        '#scripts/workspace\.js(?:\?[^"\']*)?#',
        'scripts/workspace.js?v=20260922-python-worker-ui-v142',
        $php,
        1,
        $scriptCount
    ) ?? $php;

    if ($scriptCount !== 1) {
        throw new RuntimeException('workspace.js script tag not found for cache bust.');
    }
}

$workerMarker = '/* REMASK_PYTHON_WORKER_UI_V1';
$workerPos = strpos($js, $workerMarker);

if ($workerPos === false) {
    $js .= "\n" . $addon . "\n";
} else {
    $js = rtrim(substr($js, 0, $workerPos)) . "\n\n" . $addon . "\n";
}

$php = preg_replace(
    '#scripts/workspace\.js(?:\?[^"\']*)?#',
    'scripts/workspace.js?v=20260922-python-worker-ui-v142',
    $php,
    1
) ?? $php;

file_put_contents($phpPath, $php);
file_put_contents($jsPath, $js);
fwrite(STDERR, "[python-worker-ui] Workspace bulk provisioning panel installed\n");
