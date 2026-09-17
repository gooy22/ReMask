<?php
/**
 * Browser-side hierarchy autosync guard v4.
 * Runs early, blocks legacy metaHierarchy calls when there are no saved profiles,
 * and cleans stale status/loading text on empty Workspace.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$script = <<<'JS'
(() => {
  'use strict';
  if (window.__remaskHierarchyGuardV4) return;
  window.__remaskHierarchyGuardV4 = true;

  const KEY = 'remask:hierarchy:auto-resync:last:v4';
  const WAIT_MS = 1200;
  const COOLDOWN_MS = 90 * 1000;
  let running = false;
  let timer = null;

  function textOf(node) {
    return ((node && node.innerText) || '').replace(/\s+/g, ' ').trim();
  }

  function bodyText() {
    return textOf(document.body);
  }

  function fbCountFromText(txt) {
    const m1 = txt.match(/FB\s*аккаунты\s*(\d+)\b/i);
    if (m1) return Number(m1[1]);
    const m2 = txt.match(/Аккаунты\s*(\d+)\s+Бизнес-менеджеры/i);
    if (m2) return Number(m2[1]);
    return null;
  }

  function hasRealProfileRow() {
    const rows = Array.from(document.querySelectorAll('tbody tr, .asset-row, tr[data-profile], tr[data-account-id], [data-fb-profile], [data-fb-id]'));
    return rows.some(row => {
      const t = textOf(row);
      if (!t || /loading|загрузка/i.test(t)) return false;
      if (/^[-—]+$/.test(t)) return false;
      return /\b\d{8,}\b/.test(t) || /act_\d+/i.test(t) || /требует внимания|requires attention|ads_management|synced|profile/i.test(t);
    });
  }

  function workspaceLooksEmpty() {
    const txt = bodyText();
    if (!/facebook assets|media buying workspace|fb аккаунт|аккаунты/i.test(txt)) return false;
    const count = fbCountFromText(txt);
    if (count === 0 && !hasRealProfileRow()) return true;
    if (count === null && /Аккаунты\s+0\s+Бизнес-менеджеры\s+0\s+Рекламные кабинеты\s+0/i.test(txt) && !hasRealProfileRow()) return true;
    return false;
  }

  function cleanEmptyWorkspace() {
    if (!document.body || !workspaceLooksEmpty()) return;

    const bad = /Meta hierarchy sync did not complete|Доп\. синхронизация не прошла|Invalid JSON|\[object Object\]/i;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(n => {
      if (bad.test(n.nodeValue || '')) n.nodeValue = '';
    });

    Array.from(document.querySelectorAll('td, div, span, p, small')).forEach(el => {
      const t = textOf(el);
      if (/^Loading\.\.\.$/i.test(t)) {
        el.textContent = 'Нет сохранённых FB аккаунтов. Нажми «Добавить FB аккаунт».';
      }
    });
  }

  function noProfilePayload() {
    const payload = {
      ok: true,
      success: true,
      synced: true,
      no_profiles: true,
      profiles_found: 0,
      profiles_synced: 0,
      businesses_count: 0,
      ad_accounts_count: 0,
      results: [],
      res: JSON.stringify({
        ok: true,
        synced: true,
        no_profiles: true,
        profiles_found: 0,
        profiles_synced: 0,
        businesses_count: 0,
        ad_accounts_count: 0,
        accounts: [],
        profiles: [],
        businesses: [],
        ad_accounts: []
      })
    };
    return JSON.stringify(payload);
  }

  function shouldBlockHierarchy(url) {
    try {
      const u = typeof url === 'string' ? url : (url && url.url) || '';
      if (!/\/ajax\/metaHierarchy\.php(?:\?|$)/i.test(u)) return false;
      return workspaceLooksEmpty();
    } catch (_) {
      return false;
    }
  }

  const nativeFetch = window.fetch;
  if (typeof nativeFetch === 'function') {
    window.fetch = function(input, init) {
      if (shouldBlockHierarchy(input)) {
        cleanEmptyWorkspace();
        return Promise.resolve(new Response(noProfilePayload(), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        }));
      }
      return nativeFetch.apply(this, arguments);
    };
  }

  const NativeXHR = window.XMLHttpRequest;
  if (NativeXHR && NativeXHR.prototype) {
    const nativeOpen = NativeXHR.prototype.open;
    const nativeSend = NativeXHR.prototype.send;
    NativeXHR.prototype.open = function(method, url) {
      this.__remaskHierarchyUrl = url;
      return nativeOpen.apply(this, arguments);
    };
    NativeXHR.prototype.send = function(body) {
      if (shouldBlockHierarchy(this.__remaskHierarchyUrl || '')) {
        cleanEmptyWorkspace();
        const xhr = this;
        setTimeout(() => {
          try {
            Object.defineProperty(xhr, 'readyState', { configurable: true, value: 4 });
            Object.defineProperty(xhr, 'status', { configurable: true, value: 200 });
            Object.defineProperty(xhr, 'statusText', { configurable: true, value: 'OK' });
            Object.defineProperty(xhr, 'responseText', { configurable: true, value: noProfilePayload() });
            Object.defineProperty(xhr, 'response', { configurable: true, value: noProfilePayload() });
          } catch (_) {}
          if (typeof xhr.onreadystatechange === 'function') xhr.onreadystatechange();
          if (typeof xhr.onload === 'function') xhr.onload();
        }, 0);
        return;
      }
      return nativeSend.apply(this, arguments);
    };
  }

  function hasVisibleAccountProblem(txt) {
    return /требует внимания|не синхронизирован|нет ads_management|нет bm|нет rk|не проверен|requires attention|not synced/i.test(txt);
  }

  function needsSync() {
    if (workspaceLooksEmpty()) return false;
    const txt = bodyText();
    if (!/facebook assets|media buying workspace|fb аккаунт|рекламные кабинеты|аккаунты/i.test(txt)) return false;
    return hasRealProfileRow() && hasVisibleAccountProblem(txt);
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
    if (payload.no_profiles || payload.error === 'NO_SAVED_PROFILES') return '';
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
    if (!message || workspaceLooksEmpty()) return;
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
        body: new URLSearchParams({ reason: 'auto_resync_unsynced_profile', source: 'hierarchy_autosync_v4' })
      });
      const text = await res.text();
      let payload = null;
      try { payload = JSON.parse(text); } catch (_) {}
      if (payload && (payload.no_profiles || payload.error === 'NO_SAVED_PROFILES')) return;
      const ok = res.ok && payload && (payload.ok === true || payload.success === true || payload.synced === true || Number(payload.profiles_synced || 0) > 0);
      if (ok && Number(payload.profiles_synced || 0) > 0) {
        showBanner('Доп. синхронизация завершена, обновляю Workspace…', true);
        window.setTimeout(() => location.reload(), 700);
        return;
      }
      const msg = summarize(payload, text, res.status);
      if (!ok && msg) showBanner('Доп. синхронизация не прошла:\n' + msg);
    } catch (err) {
      showBanner('Доп. синхронизация упала в браузере: ' + human(err && err.message || err));
    } finally {
      running = false;
    }
  }

  function schedule() {
    cleanEmptyWorkspace();
    if (timer || running || !needsSync()) return;
    timer = window.setTimeout(() => { timer = null; cleanEmptyWorkspace(); resync(); }, WAIT_MS);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', schedule, { once: true });
  else schedule();

  const mo = new MutationObserver(() => {
    cleanEmptyWorkspace();
    if (!running && needsSync() && cooldownOk()) schedule();
  });
  if (document.documentElement) mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
})();
JS;

file_put_contents($scriptsDir . '/hierarchy-autosync.js', $script);

$tag = '<script src="/scripts/hierarchy-autosync.js?v=20260917-autosync-v4"></script>';
$patched = 0;
foreach (['workspace.php', 'accounts.php', 'index.php', 'launch.php'] as $file) {
    $path = $root . '/' . $file;
    if (!is_file($path)) continue;
    $html = file_get_contents($path);
    $html2 = preg_replace('/<script\s+[^>]*src=["\']\/scripts\/hierarchy-autosync\.js(?:\?v=[^"\']*)?["\'][^>]*><\/script>\s*/i', '', $html);
    if (stripos($html2, '</head>') !== false) {
        $html2 = preg_replace('/<\/head>/i', $tag . "\n</head>", $html2, 1);
    } elseif (stripos($html2, '<body') !== false) {
        $html2 = preg_replace('/(<body\b[^>]*>)/i', '$1' . "\n" . $tag, $html2, 1);
    } elseif (stripos($html2, '</body>') !== false) {
        $html2 = preg_replace('/<\/body>/i', $tag . "\n</body>", $html2, 1);
    } else {
        $html2 = $tag . "\n" . $html2;
    }
    if ($html2 !== $html) {
        file_put_contents($path, $html2);
        $patched++;
    }
}

fwrite(STDERR, "[remask hierarchy autosync overlay] scripts/hierarchy-autosync.js ready v4 early guard\n");
fwrite(STDERR, "[remask hierarchy autosync overlay] script tags patched: $patched\n");
