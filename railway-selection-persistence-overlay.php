<?php
/**
 * Adds local persistence for selected accounts/ad accounts/adsets checkboxes in the packed Railway runtime.
 * This preserves selections after page refresh without storing tokens/cookies/form text.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$scriptPath = $scriptsDir . '/selection-persistence.js';
$js = <<<'JS'
(() => {
  'use strict';

  if (window.__remaskSelectionPersistenceLoaded) return;
  window.__remaskSelectionPersistenceLoaded = true;

  const VERSION = '20260917-selection-v1';
  const STORE_PREFIX = 'remask:selected:v1:';
  const INCLUDE_RE = /(ad\s*account|ad_account|account|accounts|adset|ad\s*set|adsets|rk|\brk\b|рк|кабинет|кабінет|аккаунт|акаунт|selected-account|selected-adset)/i;
  const EXCLUDE_RE = /(password|pass|token|cookie|proxy|remember|terms|privacy|theme|language|lang|toggle|switch|placement|placements|feed|story|stories|instagram|facebook|messenger|audience_network|notification|auto|draft|filter|search|sync|enable|disable|active|status|payment|billing|card|pixel|page|creative|video|image|carousel|utm|url)/i;
  const SELECT_ALL_RE = /(select\s*all|all\s*filtered|filtered|выбрать\s*все|все|усі|обрати\s*всі|selectall|checkall)/i;

  function pageScope() {
    const path = location.pathname.replace(/\/+$/, '') || '/';
    const profile = document.querySelector('[name="profile"], #profile, [name="profile_name"], #profile_name')?.value || '';
    const workspace = document.querySelector('[data-workspace-id]')?.getAttribute('data-workspace-id') || '';
    return `${path}|${profile}|${workspace}`;
  }

  function storageKey() {
    return STORE_PREFIX + pageScope();
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(storageKey());
      if (!raw) return { version: VERSION, items: {} };
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return { version: VERSION, items: {} };
      if (!parsed.items || typeof parsed.items !== 'object') parsed.items = {};
      return parsed;
    } catch (_) {
      return { version: VERSION, items: {} };
    }
  }

  function saveState(state) {
    try {
      state.version = VERSION;
      state.updated_at = new Date().toISOString();
      localStorage.setItem(storageKey(), JSON.stringify(state));
    } catch (_) {}
  }

  function textOf(el) {
    if (!el) return '';
    return [el.id, el.name, el.value, el.className, el.getAttribute?.('aria-label'), el.getAttribute?.('data-id'), el.getAttribute?.('data-account-id'), el.getAttribute?.('data-ad-account-id'), el.getAttribute?.('data-adset-id')]
      .filter(Boolean)
      .join(' ');
  }

  function labelText(input) {
    let out = '';
    if (input.id) {
      const label = document.querySelector(`label[for="${CSS.escape(input.id)}"]`);
      if (label) out += ' ' + label.textContent;
    }
    const label = input.closest('label');
    if (label) out += ' ' + label.textContent;
    const row = input.closest('tr, [role="row"], .row, .account-row, .adset-row, .rk-row, .table-row, li');
    if (row) out += ' ' + row.textContent;
    const table = input.closest('table, .table, [data-table], .accounts, .adsets, .ad-accounts, .workspace-table, .inventory-table');
    if (table) {
      const head = table.querySelector('thead, .table-head, .table-header, h2, h3, h4, .title, .section-title');
      if (head) out += ' ' + head.textContent;
    }
    let p = input.parentElement;
    for (let i = 0; p && i < 4; i++, p = p.parentElement) {
      out += ' ' + (p.getAttribute('data-section') || '') + ' ' + (p.getAttribute('data-tab') || '') + ' ' + (p.id || '') + ' ' + (p.className || '');
    }
    return out.replace(/\s+/g, ' ').trim();
  }

  function isSelectAll(input, hay) {
    if (input.dataset.remaskSelectAll === 'true') return true;
    const own = `${textOf(input)} ${input.closest('label')?.textContent || ''}`;
    return SELECT_ALL_RE.test(own) || (SELECT_ALL_RE.test(hay) && !/(act_\d+|adset|ad\s*set|account\s*id|кабинет|кабінет)/i.test(hay));
  }

  function isPersistable(input) {
    if (!(input instanceof HTMLInputElement)) return false;
    if (input.type !== 'checkbox') return false;
    if (input.disabled) return false;
    const hay = `${textOf(input)} ${labelText(input)}`;
    if (EXCLUDE_RE.test(hay)) return false;
    if (isSelectAll(input, hay)) return false;
    if (INCLUDE_RE.test(hay)) return true;
    const row = input.closest('tr, [role="row"], .account-row, .adset-row, .rk-row');
    if (row && /(act_\d+|adset|ad\s*set|account|кабинет|кабінет|рк)/i.test(row.textContent || '')) return true;
    return false;
  }

  function stableId(input) {
    const attrs = ['data-adset-id', 'data-ad-account-id', 'data-account-id', 'data-id', 'value', 'id', 'name'];
    for (const attr of attrs) {
      const v = attr.startsWith('data-') ? input.getAttribute(attr) : input[attr] || input.getAttribute(attr);
      if (v && String(v).trim() && !/^(on|off|true|false|1|0|checkbox)$/i.test(String(v).trim())) {
        return String(v).trim();
      }
    }
    const row = input.closest('tr, [role="row"], .account-row, .adset-row, .rk-row, .table-row, li');
    const rowText = (row?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 180);
    const index = Array.from(document.querySelectorAll('input[type="checkbox"]')).indexOf(input);
    return `${location.pathname}|${index}|${rowText}`;
  }

  function updateSelectedCounter() {
    const selected = Array.from(document.querySelectorAll('input[type="checkbox"]')).filter(cb => isPersistable(cb) && cb.checked).length;
    document.querySelectorAll('[data-remask-selection-count], .selected-count, .selection-count').forEach(el => {
      if (/выбрано|selected|обрано|вибрано/i.test(el.textContent || '') || el.dataset.remaskSelectionCount === 'true') {
        el.textContent = (el.textContent || '').replace(/(выбрано|selected|обрано|вибрано)\s*:?\s*\d+/i, `$1: ${selected}`);
      }
    });
    document.dispatchEvent(new CustomEvent('remask:selection:persisted', { detail: { selected } }));
  }

  function persist(input) {
    const id = stableId(input);
    const state = loadState();
    if (input.checked) state.items[id] = { checked: true, label: labelText(input).slice(0, 220), saved_at: Date.now() };
    else delete state.items[id];
    saveState(state);
    updateSelectedCounter();
  }

  function restore(input) {
    if (input.dataset.remaskSelectionBound === 'true') return;
    if (!isPersistable(input)) return;
    const id = stableId(input);
    const state = loadState();
    if (state.items && state.items[id] && !input.checked) {
      input.checked = true;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    input.addEventListener('change', () => persist(input));
    input.dataset.remaskSelectionBound = 'true';
  }

  function bindSelectAll() {
    document.querySelectorAll('input[type="checkbox"]').forEach(input => {
      const hay = `${textOf(input)} ${labelText(input)}`;
      if (!isSelectAll(input, hay) || input.dataset.remaskSelectAllBound === 'true') return;
      input.dataset.remaskSelectAllBound = 'true';
      input.addEventListener('change', () => {
        setTimeout(() => {
          document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            if (isPersistable(cb)) persist(cb);
          });
        }, 60);
      });
    });
  }

  function addClearControl() {
    if (document.getElementById('remask-clear-saved-selection')) return;
    const host = document.querySelector('.selection-toolbar, .bulk-toolbar, .toolbar, .actions, main, body');
    if (!host) return;
    const btn = document.createElement('button');
    btn.id = 'remask-clear-saved-selection';
    btn.type = 'button';
    btn.textContent = 'Clear saved selection';
    btn.style.cssText = 'display:none';
    btn.addEventListener('click', () => {
      try { localStorage.removeItem(storageKey()); } catch (_) {}
      document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        if (isPersistable(cb)) {
          cb.checked = false;
          cb.dispatchEvent(new Event('change', { bubbles: true }));
        }
      });
      updateSelectedCounter();
    });
    host.appendChild(btn);
  }

  function scan() {
    document.querySelectorAll('input[type="checkbox"]').forEach(restore);
    bindSelectAll();
    addClearControl();
    updateSelectedCounter();
  }

  function boot() {
    scan();
    const obs = new MutationObserver(() => scan());
    obs.observe(document.body, { childList: true, subtree: true });
    window.addEventListener('pageshow', scan);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
JS;
file_put_contents($scriptPath, $js);
fwrite(STDERR, "[remask selection overlay] scripts/selection-persistence.js ready\n");

$targets = glob($root . '/*.php') ?: [];
$tag = '<script src="scripts/selection-persistence.js?v=20260917-selection-v1"></script>';
$patched = 0;
foreach ($targets as $file) {
    $base = basename($file);
    if (!preg_match('/^(index|workspace|launch|campaigns|adsets|accounts)\.php$/i', $base)) continue;
    $html = file_get_contents($file);
    if ($html === false || strpos($html, 'selection-persistence.js') !== false) continue;
    if (stripos($html, '</body>') !== false) {
        $html = str_ireplace('</body>', $tag . "\n</body>", $html);
    } else {
        $html .= "\n" . $tag . "\n";
    }
    file_put_contents($file, $html);
    $patched++;
}
fwrite(STDERR, "[remask selection overlay] script tags patched: {$patched}\n");
if ($patched < 1) {
    fwrite(STDERR, "[remask selection overlay] no pages patched\n");
    exit(61);
}
