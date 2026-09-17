<?php
/**
 * Adds browser-side auto-resync for profiles that are saved but still show
 * attention/unsynced status in Workspace. It calls the existing guarded
 * ajax/metaHierarchy.php endpoint and shows a clear banner if Meta/proxy rejects it.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$script = <<<'JS'
(() => {
  'use strict';
  if (window.__remaskHierarchyAutoSyncLoaded) return;
  window.__remaskHierarchyAutoSyncLoaded = true;

  const KEY = 'remask:hierarchy:auto-resync:last';
  const WAIT_MS = 900;
  const COOLDOWN_MS = 90 * 1000;

  function bodyText() {
    return (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
  }

  function needsSync() {
    const txt = bodyText().toLowerCase();
    if (!/facebook assets|media buying workspace|fb аккаунт|рекламные кабинеты|аккаунты/i.test(txt)) return false;
    return /требует внимания|не синхронизирован|нет ads_management|нет bm|нет rk|не проверен|requires attention|not synced/i.test(txt);
  }

  function cooldownOk() {
    const last = Number(sessionStorage.getItem(KEY) || localStorage.getItem(KEY) || 0);
    return !last || Date.now() - last > COOLDOWN_MS;
  }

  function markRun() {
    const now = String(Date.now());
    sessionStorage.setItem(KEY, now);
    localStorage.setItem(KEY, now);
  }

  function showBanner(message, ok = false) {
    let box = document.getElementById('remask-hierarchy-autosync-banner');
    if (!box) {
      box = document.createElement('div');
      box.id = 'remask-hierarchy-autosync-banner';
      box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:9999;max-width:520px;padding:12px 14px;border-radius:14px;font:13px/1.35 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 12px 34px rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.18);background:rgba(18,22,32,.96);color:#dbe8ff;white-space:pre-wrap;';
      document.body.appendChild(box);
    }
    box.style.borderColor = ok ? 'rgba(74,222,128,.35)' : 'rgba(250,204,21,.35)';
    box.textContent = message;
    window.setTimeout(() => { if (box && box.parentNode) box.parentNode.removeChild(box); }, ok ? 4500 : 12000);
  }

  function summarize(payload) {
    if (!payload || typeof payload !== 'object') return 'Синхронизация вернула пустой ответ.';
    if (payload.hint) return payload.hint;
    if (Array.isArray(payload.errors) && payload.errors[0]) return payload.errors[0].hint || payload.errors[0].message || 'Meta sync error';
    if (payload.message) return payload.message;
    if (payload.error) return String(payload.error);
    return 'Meta не вернула usable BM/RK данные.';
  }

  async function resync() {
    if (!needsSync() || !cooldownOk()) return;
    markRun();
    showBanner('Профиль сохранён, запускаю доп. синхронизацию Meta…', true);
    try {
      const res = await fetch('/ajax/metaHierarchy.php', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Accept': 'application/json' },
        body: new URLSearchParams({ reason: 'auto_resync_unsynced_profile' })
      });
      const text = await res.text();
      let payload = null;
      try { payload = JSON.parse(text); } catch (_) {}
      const ok = res.ok && payload && (payload.ok === true || payload.success === true || payload.synced === true || Number(payload.profiles_synced || 0) > 0);
      if (ok) {
        showBanner('Доп. синхронизация завершена, обновляю Workspace…', true);
        window.setTimeout(() => location.reload(), 700);
        return;
      }
      showBanner('Доп. синхронизация не прошла:\n' + summarize(payload) + '\n\nЕсли это proxy — нужен рабочий user:pass proxy или whitelist Railway IP. Если token — нужен токен с ads_management.');
    } catch (err) {
      showBanner('Доп. синхронизация упала в браузере: ' + String(err && err.message || err));
    }
  }

  function schedule() {
    window.setTimeout(resync, WAIT_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();

  const mo = new MutationObserver(() => {
    if (needsSync() && cooldownOk()) schedule();
  });
  if (document.documentElement) mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
JS;

file_put_contents($scriptsDir . '/hierarchy-autosync.js', $script);

$tag = '<script src="/scripts/hierarchy-autosync.js?v=20260917-autosync-v1" defer></script>';
$patched = 0;
foreach (['workspace.php', 'accounts.php', 'index.php', 'launch.php'] as $file) {
    $path = $root . '/' . $file;
    if (!is_file($path)) continue;
    $html = file_get_contents($path);
    if (strpos($html, 'hierarchy-autosync.js') !== false) continue;
    if (stripos($html, '</body>') !== false) {
        $html = preg_replace('/<\/body>/i', $tag . "\n</body>", $html, 1);
    } else {
        $html .= "\n" . $tag . "\n";
    }
    file_put_contents($path, $html);
    $patched++;
}

fwrite(STDERR, "[remask hierarchy autosync overlay] scripts/hierarchy-autosync.js ready\n");
fwrite(STDERR, "[remask hierarchy autosync overlay] script tags patched: $patched\n");
