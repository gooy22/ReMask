<?php
/**
 * Browser-side hierarchy autosync guard.
 * Does not run when there are 0 saved FB accounts, avoids duplicate POSTs,
 * stringifies errors correctly, and force-replaces old script tags for cache busting.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$script = <<<'JS'
(() => {
  'use strict';
  if (window.__remaskHierarchyAutoSyncLoadedV3) return;
  window.__remaskHierarchyAutoSyncLoadedV3 = true;

  const KEY = 'remask:hierarchy:auto-resync:last:v3';
  const WAIT_MS = 1100;
  const COOLDOWN_MS = 90 * 1000;
  let running = false;
  let timer = null;

  function bodyText() {
    return (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim();
  }

  function accountCounterLooksZero(txt) {
    const lower = txt.toLowerCase();
    if (/fb аккаунты\s+0\b/i.test(txt)) return true;
    if (/аккаунты\s+0\s+бизнес-менеджеры\s+0/i.test(lower)) return true;
    return false;
  }

  function hasVisibleAccountProblem(txt) {
    return /требует внимания|не синхронизирован|нет ads_management|нет bm|нет rk|не проверен|requires attention|not synced/i.test(txt);
  }

  function hasSavedAccountRow() {
    const rows = Array.from(document.querySelectorAll('table tbody tr, [data-profile], [data-account-id], [data-fb-id]'));
    return rows.some(row => {
      const t = (row.innerText || '').trim();
      if (!t || /loading/i.test(t)) return false;
      return /\b\d{8,}\b/.test(t) || /act_\d+/i.test(t) || /требует внимания|synced|attention/i.test(t);
    });
  }

  function needsSync() {
    const txt = bodyText();
    if (!/facebook assets|media buying workspace|fb аккаунт|рекламные кабинеты|аккаунты/i.test(txt)) return false;
    if (accountCounterLooksZero(txt) && !hasSavedAccountRow()) return false;
    return hasVisibleAccountProblem(txt);
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

  function human(value) {
    if (value == null) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
  }

  function summarize(payload, rawText, status) {
    if (!payload || typeof payload !== 'object') return rawText ? rawText.slice(0, 500) : ('HTTP ' + status);
    if (payload.no_profiles || payload.error === 'NO_SAVED_PROFILES') return 'Нет сохранённых FB аккаунтов. Нужно добавить token + proxy заново.';
    if (payload.hint) return human(payload.hint);
    if (Array.isArray(payload.errors) && payload.errors[0]) {
      const e = payload.errors[0];
      return human(e.hint || e.message || e.error || e);
    }
    if (Array.isArray(payload.results) && payload.results[0]) {
      const r = payload.results[0];
      return human(r.hint || r.message || r.error || r.last_sync_error || r);
    }
    if (payload.message) return human(payload.message);
    if (payload.error) return human(payload.error);
    return human(payload).slice(0, 700);
  }

  function showBanner(message, ok = false) {
    if (!message) return;
    let box = document.getElementById('remask-hierarchy-autosync-banner');
    if (!box) {
      box = document.createElement('div');
      box.id = 'remask-hierarchy-autosync-banner';
      box.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:9999;max-width:560px;padding:12px 14px;border-radius:14px;font:13px/1.35 system-ui,-apple-system,Segoe UI,sans-serif;box-shadow:0 12px 34px rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.18);background:rgba(18,22,32,.96);color:#dbe8ff;white-space:pre-wrap;';
      document.body.appendChild(box);
    }
    box.style.borderColor = ok ? 'rgba(74,222,128,.35)' : 'rgba(250,204,21,.35)';
    box.textContent = message;
    window.setTimeout(() => { if (box && box.parentNode) box.parentNode.removeChild(box); }, ok ? 4200 : 14000);
  }

  async function resync() {
    if (running || !needsSync() || !cooldownOk()) return;
    running = true;
    markRun();
    showBanner('Профиль найден, запускаю доп. синхронизацию Meta…', true);
    try {
      const res = await fetch('/ajax/metaHierarchy.php', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
          'Accept': 'application/json',
          'X-Requested-With': 'XMLHttpRequest'
        },
        body: new URLSearchParams({ reason: 'auto_resync_unsynced_profile', source: 'hierarchy_autosync_v3' })
      });
      const text = await res.text();
      let payload = null;
      try { payload = JSON.parse(text); } catch (_) {}

      if (payload && (payload.no_profiles || payload.error === 'NO_SAVED_PROFILES')) {
        return;
      }

      const ok = res.ok && payload && (
        payload.ok === true || payload.success === true || payload.synced === true || Number(payload.profiles_synced || 0) > 0
      );
      if (ok && Number(payload.profiles_synced || 0) > 0) {
        showBanner('Доп. синхронизация завершена, обновляю Workspace…', true);
        window.setTimeout(() => location.reload(), 700);
        return;
      }
      if (!ok) {
        showBanner('Доп. синхронизация не прошла:\n' + summarize(payload, text, res.status));
      }
    } catch (err) {
      showBanner('Доп. синхронизация упала в браузере: ' + human(err && err.message || err));
    } finally {
      running = false;
    }
  }

  function schedule() {
    if (timer || running) return;
    timer = window.setTimeout(() => { timer = null; resync(); }, WAIT_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();

  const mo = new MutationObserver(() => {
    if (!running && needsSync() && cooldownOk()) schedule();
  });
  if (document.documentElement) mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
JS;

file_put_contents($scriptsDir . '/hierarchy-autosync.js', $script);

$tag = '<script src="/scripts/hierarchy-autosync.js?v=20260917-autosync-v3" defer></script>';
$patched = 0;
foreach (['workspace.php', 'accounts.php', 'index.php', 'launch.php'] as $file) {
    $path = $root . '/' . $file;
    if (!is_file($path)) continue;
    $html = file_get_contents($path);
    $html2 = preg_replace('/<script\s+[^>]*src=["\']\/scripts\/hierarchy-autosync\.js(?:\?v=[^"\']*)?["\'][^>]*><\/script>\s*/i', '', $html);
    if (stripos($html2, '</body>') !== false) {
        $html2 = preg_replace('/<\/body>/i', $tag . "\n</body>", $html2, 1);
    } else {
        $html2 .= "\n" . $tag . "\n";
    }
    if ($html2 !== $html) {
        file_put_contents($path, $html2);
        $patched++;
    }
}

fwrite(STDERR, "[remask hierarchy autosync overlay] scripts/hierarchy-autosync.js ready v3\n");
fwrite(STDERR, "[remask hierarchy autosync overlay] script tags patched: $patched\n");
