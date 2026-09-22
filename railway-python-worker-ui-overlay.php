<?php
/**
 * Installs the Python worker bulk provisioning panel into the existing Workspace.
 */
$phpPath = '/var/www/html/workspace.php';
$jsPath = '/var/www/html/scripts/workspace.js';
$addonPath = '/tmp/railway-python-worker-ui.js';

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
      <div class="pw-sub">Выбранные FB-профили → Job → worker queue → profile context → proxy_check → persistent state.</div>
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
        'scripts/workspace.js?v=20260922-python-worker-ui-v133',
        $php,
        1,
        $scriptCount
    ) ?? $php;

    if ($scriptCount !== 1) {
        throw new RuntimeException('workspace.js script tag not found for cache bust.');
    }
}

if (strpos($js, 'REMASK_PYTHON_WORKER_UI_V2') === false) {
    $js .= "\n" . $addon . "\n";
}

$php = preg_replace(
    '#scripts/workspace\.js(?:\?[^"\']*)?#',
    'scripts/workspace.js?v=20260922-python-worker-ui-v133',
    $php,
    1
) ?? $php;

file_put_contents($phpPath, $php);
file_put_contents($jsPath, $js);
fwrite(STDERR, "[python-worker-ui] Workspace bulk provisioning panel installed\n");
