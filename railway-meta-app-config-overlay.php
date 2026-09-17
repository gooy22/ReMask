<?php
/**
 * Allow an authenticated ReMask operator to configure the Meta App credentials
 * from Workspace when Railway env vars are not present. Secrets stay server-side
 * on REMASK_DATA_DIR and are never returned to the browser.
 */
$root = '/var/www/html';

$configStore = <<<'PHP'
<?php
final class MetaAppConfigStore
{
    private string $path;

    public function __construct(?string $path = null)
    {
        $base = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
        $this->path = $path ?: ($base . '/meta-app-config.json');
    }

    public function effective(): array
    {
        $envId = trim((string)(getenv('META_APP_ID') ?: ''));
        $envSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));
        if ($envId !== '' || $envSecret !== '') {
            return [
                'app_id' => $envId,
                'app_secret' => $envSecret,
                'configured' => $envId !== '' && $envSecret !== '',
                'source' => 'environment',
            ];
        }
        $stored = $this->read();
        $id = trim((string)($stored['app_id'] ?? ''));
        $secret = trim((string)($stored['app_secret'] ?? ''));
        return [
            'app_id' => $id,
            'app_secret' => $secret,
            'configured' => $id !== '' && $secret !== '',
            'source' => ($id !== '' || $secret !== '') ? 'persistent' : 'none',
        ];
    }

    public function save(string $appId, string $appSecret): void
    {
        $appId = trim($appId);
        $appSecret = trim($appSecret);
        if (!preg_match('/^[0-9]{5,30}$/', $appId)) {
            throw new InvalidArgumentException('Meta App ID должен состоять из цифр.');
        }
        if (strlen($appSecret) < 16 || strlen($appSecret) > 128 || preg_match('/\\s/', $appSecret)) {
            throw new InvalidArgumentException('Meta App Secret имеет неверный формат.');
        }
        $dir = dirname($this->path);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException('Cannot create Meta app config directory.');
        }
        $payload = json_encode([
            'app_id' => $appId,
            'app_secret' => $appSecret,
            'updated_at' => gmdate('c'),
        ], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR);
        $tmp = $this->path . '.tmp.' . getmypid() . '.' . bin2hex(random_bytes(4));
        if (file_put_contents($tmp, $payload . "\n", LOCK_EX) === false) {
            throw new RuntimeException('Cannot write Meta app config.');
        }
        @chmod($tmp, 0600);
        if (!rename($tmp, $this->path)) {
            @unlink($tmp);
            throw new RuntimeException('Cannot replace Meta app config.');
        }
        @chmod($this->path, 0600);
    }

    private function read(): array
    {
        if (!is_file($this->path)) return [];
        $raw = @file_get_contents($this->path);
        if ($raw === false || trim($raw) === '') return [];
        $decoded = json_decode($raw, true);
        return is_array($decoded) ? $decoded : [];
    }
}
PHP;
file_put_contents($root . '/classes/MetaAppConfigStore.php', $configStore);

function patch_require_once(string $path, string $anchor, string $line): void {
    $text = file_get_contents($path);
    if ($text === false) throw new RuntimeException(basename($path) . ' not found');
    if (!str_contains($text, $line)) {
        if (!str_contains($text, $anchor)) throw new RuntimeException('Require anchor not found in ' . basename($path));
        $text = str_replace($anchor, $anchor . "\n" . $line, $text, $count);
        if ($count !== 1) throw new RuntimeException('Require patch failed in ' . basename($path) . ': ' . $count);
        file_put_contents($path, $text);
    }
}

// meta-oauth-start.php: use env credentials when present, otherwise persistent config.
$startPath = $root . '/meta-oauth-start.php';
patch_require_once($startPath, "require_once __DIR__ . '/classes/AccountStoreFactory.php';", "require_once __DIR__ . '/classes/MetaAppConfigStore.php';");
$start = file_get_contents($startPath);
$oldStart = "$appId = trim((string)(getenv('META_APP_ID') ?: ''));";
$newStart = "$metaAppConfig = (new MetaAppConfigStore())->effective();\n$appId = trim((string)($metaAppConfig['app_id'] ?? ''));";
if (str_contains($start, $oldStart)) {
    $start = str_replace($oldStart, $newStart, $start, $count);
    if ($count !== 1) throw new RuntimeException('OAuth start app id patch failed: ' . $count);
    file_put_contents($startPath, $start);
} elseif (!str_contains($start, 'new MetaAppConfigStore()')) {
    throw new RuntimeException('OAuth start app id anchor missing');
}

// meta-oauth-callback.php: exchange the code using the same effective credentials.
$callbackPath = $root . '/meta-oauth-callback.php';
patch_require_once($callbackPath, "require_once __DIR__ . '/classes/MetaEndpoint.php';", "require_once __DIR__ . '/classes/MetaAppConfigStore.php';");
$callback = file_get_contents($callbackPath);
$oldCb = "$appId = trim((string)(getenv('META_APP_ID') ?: ''));\n$appSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));";
$newCb = "$metaAppConfig = (new MetaAppConfigStore())->effective();\n$appId = trim((string)($metaAppConfig['app_id'] ?? ''));\n$appSecret = trim((string)($metaAppConfig['app_secret'] ?? ''));";
if (str_contains($callback, $oldCb)) {
    $callback = str_replace($oldCb, $newCb, $callback, $count);
    if ($count !== 1) throw new RuntimeException('OAuth callback credential patch failed: ' . $count);
    file_put_contents($callbackPath, $callback);
} elseif (!str_contains($callback, 'new MetaAppConfigStore()')) {
    throw new RuntimeException('OAuth callback credential anchor missing');
}

// metaOAuthStatus.php: report configuration state without returning the secret.
$statusPath = $root . '/ajax/metaOAuthStatus.php';
patch_require_once($statusPath, "require_once __DIR__ . '/../classes/AccountStoreFactory.php';", "require_once __DIR__ . '/../classes/MetaAppConfigStore.php';");
$status = file_get_contents($statusPath);
$oldStatus = "$appId = trim((string)(getenv('META_APP_ID') ?: ''));\n    $appSecret = trim((string)(getenv('META_APP_SECRET') ?: ''));";
$newStatus = "$metaAppConfig = (new MetaAppConfigStore())->effective();\n    $appId = trim((string)($metaAppConfig['app_id'] ?? ''));\n    $appSecret = trim((string)($metaAppConfig['app_secret'] ?? ''));";
if (str_contains($status, $oldStatus)) {
    $status = str_replace($oldStatus, $newStatus, $status, $count);
    if ($count !== 1) throw new RuntimeException('OAuth status credential patch failed: ' . $count);
}
if (!str_contains($status, "'config_source'=>")) {
    $anchor = "        'configured'=>$appId !== '' && $appSecret !== '',";
    if (!str_contains($status, $anchor)) throw new RuntimeException('OAuth status configured anchor missing');
    $status = str_replace($anchor, $anchor . "\n        'config_source'=>(string)($metaAppConfig['source'] ?? 'none'),", $status, $count);
    if ($count !== 1) throw new RuntimeException('OAuth status source patch failed');
}
file_put_contents($statusPath, $status);

$endpoint = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaAppConfigStore.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');

function remask_config_public(array $cfg): array {
    $id = trim((string)($cfg['app_id'] ?? ''));
    $masked = $id === '' ? '' : (strlen($id) <= 6 ? str_repeat('*', strlen($id)) : substr($id, 0, 3) . str_repeat('*', max(3, strlen($id) - 6)) . substr($id, -3));
    $domain = trim((string)(getenv('RAILWAY_PUBLIC_DOMAIN') ?: ''));
    $redirect = trim((string)(getenv('META_OAUTH_REDIRECT_URI') ?: ''));
    if ($redirect === '' && $domain !== '') $redirect = 'https://' . $domain . '/meta-oauth-callback.php';
    return [
        'configured'=>(bool)($cfg['configured'] ?? false),
        'source'=>(string)($cfg['source'] ?? 'none'),
        'app_id_masked'=>$masked,
        'app_secret_configured'=>trim((string)($cfg['app_secret'] ?? '')) !== '',
        'redirect_uri'=>$redirect,
    ];
}

try {
    $store = new MetaAppConfigStore();
    if ($_SERVER['REQUEST_METHOD'] === 'GET') {
        echo json_encode(['ok'=>true] + remask_config_public($store->effective()), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    }
    if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
        http_response_code(405);
        echo json_encode(['ok'=>false,'error'=>'Method not allowed.']);
        exit;
    }
    if (($_SERVER['HTTP_X_REQUESTED_WITH'] ?? '') !== 'ReMask') {
        http_response_code(403);
        echo json_encode(['ok'=>false,'error'=>'Missing ReMask request header.']);
        exit;
    }
    $origin = trim((string)($_SERVER['HTTP_ORIGIN'] ?? ''));
    $host = trim((string)($_SERVER['HTTP_HOST'] ?? ''));
    if ($origin !== '' && $host !== '') {
        $originHost = (string)(parse_url($origin, PHP_URL_HOST) ?: '');
        if ($originHost === '' || strcasecmp($originHost, preg_replace('/:\\d+$/', '', $host)) !== 0) {
            http_response_code(403);
            echo json_encode(['ok'=>false,'error'=>'Origin rejected.']);
            exit;
        }
    }
    $effective = $store->effective();
    if (($effective['source'] ?? '') === 'environment') {
        http_response_code(409);
        echo json_encode(['ok'=>false,'error'=>'META_APP_ID / META_APP_SECRET уже заданы через Railway environment.']);
        exit;
    }
    $raw = file_get_contents('php://input');
    $body = json_decode((string)$raw, true);
    if (!is_array($body)) $body = $_POST;
    $appId = trim((string)($body['app_id'] ?? ''));
    $appSecret = trim((string)($body['app_secret'] ?? ''));
    $store->save($appId, $appSecret);
    echo json_encode(['ok'=>true] + remask_config_public($store->effective()), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
} catch (Throwable $e) {
    http_response_code($e instanceof InvalidArgumentException ? 400 : 500);
    echo json_encode(['ok'=>false,'error'=>$e->getMessage()], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}
PHP;
file_put_contents($root . '/ajax/metaAppConfig.php', $endpoint);

$js = <<<'JS'
(() => {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function getConfig() {
    const r = await fetch('ajax/metaAppConfig.php', {credentials:'same-origin', cache:'no-store'});
    return await r.json();
  }

  async function init() {
    let data;
    try { data = await getConfig(); } catch (_) { return; }
    if (!data || !data.ok || data.configured || data.source === 'environment') return;
    if (document.getElementById('remaskMetaAppSetup')) return;

    const oauth = document.getElementById('remaskMetaOAuth');
    const anchor = oauth || document.getElementById('workspaceStatus');
    if (!anchor) return;

    const box = document.createElement('div');
    box.id = 'remaskMetaAppSetup';
    box.style.cssText = 'margin:10px 0;padding:12px;border:1px solid rgba(96,165,250,.35);border-radius:12px;background:rgba(30,64,175,.10);font-size:13px';
    box.innerHTML = '<div style="font-weight:700;margin-bottom:8px">Meta API setup</div>' +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
      '<input id="remaskMetaAppId" inputmode="numeric" autocomplete="off" placeholder="Meta App ID" style="min-width:180px;max-width:260px">' +
      '<input id="remaskMetaAppSecret" type="password" autocomplete="new-password" placeholder="Meta App Secret" style="min-width:220px;max-width:320px">' +
      '<button type="button" id="remaskSaveMetaApp">Сохранить Meta App</button>' +
      '</div>' +
      (data.redirect_uri ? '<div style="margin-top:8px;opacity:.8">OAuth Redirect URI: <code>'+esc(data.redirect_uri)+'</code></div>' : '') +
      '<div id="remaskMetaAppMsg" style="margin-top:8px;opacity:.85"></div>';
    anchor.insertAdjacentElement('afterend', box);

    const btn = document.getElementById('remaskSaveMetaApp');
    btn.onclick = async () => {
      const appId = (document.getElementById('remaskMetaAppId')?.value || '').trim();
      const appSecret = (document.getElementById('remaskMetaAppSecret')?.value || '').trim();
      const msg = document.getElementById('remaskMetaAppMsg');
      if (!appId || !appSecret) { msg.textContent = 'Введите App ID и App Secret.'; return; }
      btn.disabled = true;
      msg.textContent = 'Сохраняю…';
      try {
        const r = await fetch('ajax/metaAppConfig.php', {
          method:'POST', credentials:'same-origin', cache:'no-store',
          headers:{'Content-Type':'application/json','X-Requested-With':'ReMask'},
          body:JSON.stringify({app_id:appId, app_secret:appSecret})
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || 'Не удалось сохранить Meta App.');
        msg.textContent = 'Meta App сохранён. Перезагружаю OAuth…';
        setTimeout(() => location.reload(), 450);
      } catch (e) {
        msg.textContent = e && e.message ? e.message : 'Не удалось сохранить Meta App.';
        btn.disabled = false;
      }
    };
  }

  const boot = () => setTimeout(init, 150);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
JS;
file_put_contents($root . '/scripts/meta-app-config.js', $js);

$workspacePath = $root . '/workspace.php';
$workspace = file_get_contents($workspacePath);
if ($workspace === false) throw new RuntimeException('workspace.php not found');
if (!str_contains($workspace, 'scripts/meta-app-config.js')) {
    $tag = '<script src="scripts/meta-app-config.js?v=1"></script>';
    if (str_contains($workspace, '</body>')) $workspace = str_replace('</body>', $tag . "\n</body>", $workspace);
    else $workspace .= "\n" . $tag . "\n";
    file_put_contents($workspacePath, $workspace);
}

fwrite(STDERR, "[meta-app-config] authenticated persistent Meta App setup ready\n");
