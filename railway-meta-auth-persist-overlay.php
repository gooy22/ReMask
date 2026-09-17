<?php
/**
 * Persist sanitized Meta authorization state independently of the workspace store.
 * This lives on REMASK_DATA_DIR so it survives refreshes/restarts and works with
 * both file-backed and PostgreSQL workspace metadata.
 */
$root = '/var/www/html';

$storeCode = <<<'PHP'
<?php
final class MetaAuthStateStore
{
    private string $path;

    public function __construct(?string $path = null)
    {
        $base = rtrim((string)(getenv('REMASK_DATA_DIR') ?: '/var/lib/remask'), '/');
        $this->path = $path ?: ($base . '/meta-auth-state.json');
        $dir = dirname($this->path);
        if (!is_dir($dir) && !mkdir($dir, 0700, true) && !is_dir($dir)) {
            throw new RuntimeException('Cannot create Meta auth state directory.');
        }
    }

    public function all(): array
    {
        $lock = fopen($this->path . '.lock', 'c+');
        if ($lock === false) return [];
        try {
            if (!flock($lock, LOCK_SH)) return [];
            return $this->readUnlocked();
        } finally {
            @flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    public function get(string $profile): ?array
    {
        $all = $this->all();
        return isset($all[$profile]) && is_array($all[$profile]) ? $all[$profile] : null;
    }

    public function set(string $profile, array $state): void
    {
        $profile = trim($profile);
        if ($profile === '') return;
        $allowed = ['ok','oauth_required','api_error','pending_sync'];
        $status = (string)($state['status'] ?? 'api_error');
        if (!in_array($status, $allowed, true)) $status = 'api_error';
        $message = trim((string)($state['message'] ?? ''));
        if (function_exists('mb_substr')) $message = mb_substr($message, 0, 500);
        else $message = substr($message, 0, 500);
        $row = [
            'status' => $status,
            'message' => $message,
            'updated_at' => (string)($state['updated_at'] ?? gmdate('c')),
        ];

        $lock = fopen($this->path . '.lock', 'c+');
        if ($lock === false) throw new RuntimeException('Cannot lock Meta auth state.');
        try {
            if (!flock($lock, LOCK_EX)) throw new RuntimeException('Cannot lock Meta auth state.');
            $all = $this->readUnlocked();
            $all[$profile] = $row;
            $json = json_encode($all, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR);
            $tmp = $this->path . '.tmp.' . getmypid() . '.' . bin2hex(random_bytes(4));
            if (file_put_contents($tmp, $json . "\n", LOCK_EX) === false) throw new RuntimeException('Cannot write Meta auth state.');
            @chmod($tmp, 0600);
            if (!rename($tmp, $this->path)) {
                @unlink($tmp);
                throw new RuntimeException('Cannot replace Meta auth state.');
            }
            @chmod($this->path, 0600);
        } finally {
            @flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    public function clear(string $profile): void
    {
        $profile = trim($profile);
        if ($profile === '') return;
        $lock = fopen($this->path . '.lock', 'c+');
        if ($lock === false) return;
        try {
            if (!flock($lock, LOCK_EX)) return;
            $all = $this->readUnlocked();
            unset($all[$profile]);
            $json = json_encode($all, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR);
            file_put_contents($this->path, $json . "\n", LOCK_EX);
            @chmod($this->path, 0600);
        } finally {
            @flock($lock, LOCK_UN);
            fclose($lock);
        }
    }

    private function readUnlocked(): array
    {
        if (!is_file($this->path)) return [];
        $raw = @file_get_contents($this->path);
        if ($raw === false || trim($raw) === '') return [];
        $decoded = json_decode($raw, true);
        return is_array($decoded) ? $decoded : [];
    }
}
PHP;
file_put_contents($root . '/classes/MetaAuthStateStore.php', $storeCode);

// Expose the persisted state as part of every hierarchy snapshot.
$hierarchyPath = $root . '/ajax/metaHierarchy.php';
$hierarchy = file_get_contents($hierarchyPath);
if ($hierarchy === false) throw new RuntimeException('metaHierarchy.php not found');

$requireNeedle = "require_once __DIR__ . '/../classes/ProxyHealthService.php';";
if (!str_contains($hierarchy, "MetaAuthStateStore.php")) {
    if (!str_contains($hierarchy, $requireNeedle)) throw new RuntimeException('MetaAuthStateStore require anchor not found');
    $hierarchy = str_replace($requireNeedle, $requireNeedle . "\nrequire_once __DIR__ . '/../classes/MetaAuthStateStore.php';", $hierarchy, $requireCount);
    if ($requireCount !== 1) throw new RuntimeException('MetaAuthStateStore require patch failed: ' . $requireCount);
}

$profileFn = 'function hierarchy_profile_snapshot(string $profile, ?array $workspaceMeta = null): array';
if (!str_contains($hierarchy, 'function hierarchy_auth_state_store()')) {
    $pos = strpos($hierarchy, $profileFn);
    if ($pos === false) throw new RuntimeException('hierarchy_profile_snapshot anchor not found');
    $helper = <<<'PHP'
function hierarchy_auth_state_store(): MetaAuthStateStore
{
    static $store = null;
    if (!$store instanceof MetaAuthStateStore) $store = new MetaAuthStateStore();
    return $store;
}

PHP;
    $hierarchy = substr($hierarchy, 0, $pos) . $helper . substr($hierarchy, $pos);
}

if (!str_contains($hierarchy, "'auth_state' => hierarchy_auth_state_store()->get(\$profile)")) {
    $legacyNeedle = "            'legacy_ready' => \$account->isLegacyReady(),";
    if (!str_contains($hierarchy, $legacyNeedle)) throw new RuntimeException('profile auth_state snapshot anchor not found');
    $hierarchy = str_replace($legacyNeedle, $legacyNeedle . "\n            'auth_state' => hierarchy_auth_state_store()->get(\$profile),", $hierarchy, $snapshotCount);
    if ($snapshotCount !== 1) throw new RuntimeException('profile auth_state snapshot patch failed: ' . $snapshotCount);
}

// Patch only the sync_profile preflight that the OAuth overlay already made explicit.
if (!str_contains($hierarchy, "'status'=>'oauth_required'")) {
    $syncMarker = "    if (\$action === 'sync_profile') {";
    $syncPos = strpos($hierarchy, $syncMarker);
    if ($syncPos === false) throw new RuntimeException('sync_profile anchor not found');

    $preflightNeedle = "            MetaEndpoint::cachedPreflight(\$profile, true);";
    $preflightPos = strpos($hierarchy, $preflightNeedle, $syncPos);
    if ($preflightPos === false) throw new RuntimeException('sync_profile preflight anchor not found');
    $afterPreflight = $preflightPos + strlen($preflightNeedle);
    $hierarchy = substr($hierarchy, 0, $afterPreflight)
        . "\n            hierarchy_auth_state_store()->set(\$profile, ['status'=>'ok','message'=>'','updated_at'=>gmdate('c')]);"
        . substr($hierarchy, $afterPreflight);

    $oauthThrow = "                throw new RuntimeException('META_OAUTH_REQUIRED: текущий EAA-токен отклонён официальным Graph API даже на /me. Подключите токен через Meta OAuth/Marketing API.');";
    $oauthPos = strpos($hierarchy, $oauthThrow, $syncPos);
    if ($oauthPos === false) throw new RuntimeException('META_OAUTH_REQUIRED throw anchor not found');
    $oauthSet = "                hierarchy_auth_state_store()->set(\$profile, ['status'=>'oauth_required','message'=>'Meta Graph API отклонил текущий токен на /me. Требуется официальный Meta OAuth/Marketing API token.','updated_at'=>gmdate('c')]);\n";
    $hierarchy = substr($hierarchy, 0, $oauthPos) . $oauthSet . substr($hierarchy, $oauthPos);

    $genericThrow = "            throw \$metaAuthError;";
    $genericPos = strpos($hierarchy, $genericThrow, $oauthPos);
    if ($genericPos === false) throw new RuntimeException('generic Meta auth throw anchor not found');
    $genericSet = "            hierarchy_auth_state_store()->set(\$profile, ['status'=>'api_error','message'=>'Meta API sync failed (code ' . (string)\$metaAuthError->getCode() . ').','updated_at'=>gmdate('c')]);\n";
    $hierarchy = substr($hierarchy, 0, $genericPos) . $genericSet . substr($hierarchy, $genericPos);
}
file_put_contents($hierarchyPath, $hierarchy);

// After a successful official OAuth exchange, clear the stale oauth_required banner
// immediately but keep the profile marked pending until a real Graph preflight passes.
$callbackPath = $root . '/meta-oauth-callback.php';
$callback = file_get_contents($callbackPath);
if ($callback === false) throw new RuntimeException('meta-oauth-callback.php not found');
if (!str_contains($callback, "MetaAuthStateStore.php")) {
    $anchor = "require_once __DIR__ . '/classes/MetaEndpoint.php';";
    if (!str_contains($callback, $anchor)) throw new RuntimeException('OAuth callback require anchor not found');
    $callback = str_replace($anchor, $anchor . "\nrequire_once __DIR__ . '/classes/MetaAuthStateStore.php';", $callback, $cbRequireCount);
    if ($cbRequireCount !== 1) throw new RuntimeException('OAuth callback require patch failed');
}
if (!str_contains($callback, "'status'=>'pending_sync'")) {
    $anchor = "    \$store->addOrUpdateAccount(\$updated);";
    if (!str_contains($callback, $anchor)) throw new RuntimeException('OAuth callback persistence anchor not found');
    $callback = str_replace($anchor, $anchor . "\n    (new MetaAuthStateStore())->set(\$profile, ['status'=>'pending_sync','message'=>'Meta OAuth подключён. Запустите синхронизацию профиля.','updated_at'=>gmdate('c')]);", $callback, $cbStateCount);
    if ($cbStateCount !== 1) throw new RuntimeException('OAuth callback pending state patch failed');
}
file_put_contents($callbackPath, $callback);

// Sanitized same-origin endpoint for the Workspace UI.
$stateEndpoint = <<<'PHP'
<?php
require_once __DIR__ . '/../settings.php';
require_once __DIR__ . '/../checkpassword.php';
require_once __DIR__ . '/../classes/MetaAuthStateStore.php';
require_once __DIR__ . '/../classes/AccountStoreFactory.php';
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, max-age=0');
try {
    $existing = [];
    $profiles = AccountStoreFactory::create(ACCOUNTSFILENAME)->deserialize();
    foreach ($profiles as $account) {
        if ($account instanceof FbAccount && $account->name !== '') $existing[$account->name] = true;
    }
    $all = (new MetaAuthStateStore())->all();
    $states = [];
    foreach ($all as $profile => $state) {
        if (!isset($existing[$profile]) || !is_array($state)) continue;
        $states[$profile] = [
            'status'=>(string)($state['status'] ?? ''),
            'message'=>(string)($state['message'] ?? ''),
            'updated_at'=>(string)($state['updated_at'] ?? ''),
        ];
    }
    echo json_encode(['ok'=>true,'states'=>$states], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
} catch (Throwable $e) {
    http_response_code(500);
    echo json_encode(['ok'=>false,'error'=>'Meta auth state unavailable.'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}
PHP;
file_put_contents($root . '/ajax/metaAuthState.php', $stateEndpoint);

// UI is deliberately separate from the compact Workspace renderer. It annotates a
// matching row when possible and always renders a persistent global auth banner.
$stateJs = <<<'JS'
(() => {
  let cached = {};
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const active = s => s && ['oauth_required','api_error','pending_sync'].includes(String(s.status || ''));

  function label(state) {
    if (!state) return '';
    if (state.status === 'oauth_required') return 'META AUTH ERROR';
    if (state.status === 'pending_sync') return 'META OAUTH · НУЖНА СИНХРОНИЗАЦИЯ';
    if (state.status === 'api_error') return 'META API ERROR';
    return '';
  }

  function apply() {
    document.querySelectorAll('[data-remask-auth-row]').forEach(el => el.remove());
    const entries = Object.entries(cached).filter(([,s]) => active(s));
    for (const [profile, state] of entries) {
      for (const row of document.querySelectorAll('tr')) {
        if (!String(row.textContent || '').includes(profile)) continue;
        const cell = row.querySelector('td') || row;
        if (cell.querySelector('[data-remask-auth-row="'+CSS.escape(profile)+'"]')) break;
        const badge = document.createElement('div');
        badge.dataset.remaskAuthRow = profile;
        badge.style.cssText = 'margin-top:5px;font-size:11px;font-weight:700;letter-spacing:.02em;color:' + (state.status === 'pending_sync' ? '#fbbf24' : '#f87171');
        badge.textContent = label(state) + (state.message ? ' · ' + state.message : '');
        cell.appendChild(badge);
        break;
      }
    }

    let box = document.getElementById('remaskPersistentMetaAuth');
    if (!entries.length) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement('div');
      box.id = 'remaskPersistentMetaAuth';
      box.style.cssText = 'margin:10px 0;padding:11px 13px;border:1px solid rgba(248,113,113,.36);border-radius:12px;background:rgba(127,29,29,.14);font-size:13px;line-height:1.45';
      const oauth = document.getElementById('remaskMetaOAuth');
      const anchor = oauth || document.getElementById('workspaceStatus');
      if (anchor) anchor.insertAdjacentElement('afterend', box); else document.body.prepend(box);
    }
    box.innerHTML = '<strong>Meta API:</strong> ' + entries.map(([profile,state]) => '<span><b>'+esc(profile)+'</b> — '+esc(state.message || label(state))+'</span>').join(' · ');
  }

  async function refresh() {
    try {
      const r = await fetch('ajax/metaAuthState.php', {credentials:'same-origin', cache:'no-store'});
      const d = await r.json();
      if (d && d.ok && d.states && typeof d.states === 'object') cached = d.states;
    } catch (_) {}
    apply();
  }

  const start = () => {
    refresh();
    const observer = new MutationObserver(() => apply());
    observer.observe(document.body, {childList:true, subtree:true});
    setInterval(refresh, 15000);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start); else start();
})();
JS;
file_put_contents($root . '/scripts/meta-auth-state.js', $stateJs);

$workspacePath = $root . '/workspace.php';
$workspace = file_get_contents($workspacePath);
if ($workspace === false) throw new RuntimeException('workspace.php not found');
if (!str_contains($workspace, 'scripts/meta-auth-state.js')) {
    $tag = '<script src="scripts/meta-auth-state.js?v=1"></script>';
    if (str_contains($workspace, '</body>')) $workspace = str_replace('</body>', $tag . "\n</body>", $workspace);
    else $workspace .= "\n" . $tag . "\n";
    file_put_contents($workspacePath, $workspace);
}

fwrite(STDERR, "[meta-auth-persist] persistent sanitized Meta auth state + Workspace annotation ready\n");
