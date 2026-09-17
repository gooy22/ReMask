<?php
/**
 * Adds Facebook-like live autocomplete for Meta targeting search in the packed Railway runtime.
 * Search buttons inside interests/behaviors targeting blocks are removed from the UI.
 */
$root = '/var/www/html';
$scriptsDir = $root . '/scripts';
if (!is_dir($scriptsDir)) mkdir($scriptsDir, 0775, true);

$scriptPath = $scriptsDir . '/targeting-autocomplete.js';
$js = <<<'JS'
(() => {
  'use strict';

  if (window.__remaskTargetingAutocompleteLoaded) return;
  window.__remaskTargetingAutocompleteLoaded = true;

  const MIN_LEN = 2;
  const DEBOUNCE_MS = 260;
  const endpoint = '/ajax/metaTargetingSearch.php';
  const attached = new WeakMap();
  const inflight = new WeakMap();

  const TARGETING_RE = /(targeting|interest|interests|behavior|behaviors|behaviour|behaviours|audience|detailed|деталь|интерес|інтерес|повед|аудитор|таргет|таргетинг)/i;
  const EXCLUDE_RE = /(token|cookie|proxy|password|name|url|utm|pixel|page|creative|headline|text|budget|bid|date|time|account|campaign|adset|ad\s*name|rk|рк|карта|payment|billing)/i;
  const SEARCH_BUTTON_RE = /^(search|find|поиск|шукати|знайти|найти)$/i;

  function textOf(el) {
    if (!el) return '';
    return [el.id, el.name, el.placeholder, el.getAttribute('aria-label'), el.getAttribute('data-label'), el.className]
      .filter(Boolean)
      .join(' ');
  }

  function labelFor(input) {
    const id = input.id;
    let out = '';
    if (id) {
      const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (label) out += ' ' + label.textContent;
    }
    let p = input.parentElement;
    for (let i = 0; p && i < 5; i++, p = p.parentElement) {
      const label = p.querySelector('label, .form-label, .label, small, .muted, .section-title, h3, h4');
      if (label) out += ' ' + label.textContent;
    }
    return out;
  }

  function isTargetingInput(input) {
    if (!(input instanceof HTMLInputElement) && !(input instanceof HTMLTextAreaElement)) return false;
    if (input.disabled || input.readOnly) return false;
    const type = (input.getAttribute('type') || 'text').toLowerCase();
    if (!['text', 'search', ''].includes(type)) return false;
    const hay = `${textOf(input)} ${labelFor(input)}`;
    if (EXCLUDE_RE.test(hay)) return false;
    if (TARGETING_RE.test(hay)) return true;
    const wrap = input.closest('[data-targeting], .targeting, .audience, .interests, .behaviors, .behaviours, .detailed-targeting');
    return Boolean(wrap);
  }

  function closestBox(input) {
    return input.closest('.input-group, .form-group, .field, .mb-3, .targeting, .audience, .interests, .behaviors, .behaviours, .detailed-targeting, .card, .panel, section, form, div') || input.parentElement || document.body;
  }

  function looksLikeSearchButton(btn) {
    const raw = ((btn.textContent || btn.value || '') + ' ' + textOf(btn)).replace(/\s+/g, ' ').trim();
    if (!raw) return false;
    return SEARCH_BUTTON_RE.test(raw) || /\b(search|find)\b|поиск|шукати|знайти|найти/i.test(raw);
  }

  function removeSearchButtonsNear(input) {
    const areas = [];
    let p = input.parentElement;
    for (let i = 0; p && i < 6; i++, p = p.parentElement) areas.push(p);
    const form = input.closest('form');
    if (form) areas.push(form);

    for (const area of areas) {
      if (!area || area.dataset.remaskSearchButtonsRemoved === 'true') continue;
      const areaText = `${area.className || ''} ${area.id || ''} ${area.getAttribute?.('data-targeting') || ''} ${area.textContent || ''}`;
      if (!TARGETING_RE.test(areaText) && area !== input.parentElement) continue;
      const buttons = Array.from(area.querySelectorAll('button, input[type="button"], input[type="submit"], a.btn'));
      for (const btn of buttons) {
        if (!looksLikeSearchButton(btn)) continue;
        btn.dataset.remaskRemovedSearch = 'true';
        btn.setAttribute('aria-hidden', 'true');
        btn.setAttribute('tabindex', '-1');
        btn.style.display = 'none';
        btn.style.visibility = 'hidden';
        btn.style.width = '0';
        btn.style.minWidth = '0';
        btn.style.padding = '0';
        btn.style.margin = '0';
        btn.style.border = '0';
        btn.style.pointerEvents = 'none';
        try { btn.remove(); } catch (_) {}
      }
      area.dataset.remaskSearchButtonsRemoved = 'true';
    }
  }

  function getContext(input) {
    const form = input.closest('form') || document;
    const pick = names => {
      for (const name of names) {
        const el = form.querySelector(`[name="${name}"], #${CSS.escape(name)}`) || document.querySelector(`[name="${name}"], #${CSS.escape(name)}`);
        if (el && 'value' in el && String(el.value || '').trim() !== '') return String(el.value || '').trim();
      }
      return '';
    };
    return {
      profile: pick(['profile', 'profile_name', 'fb_profile', 'account_profile']),
      account_id: pick(['account_id', 'ad_account_id', 'rk_id', 'act_id', 'selected_account_id']),
      locale: pick(['locale', 'language']) || navigator.language || 'en_US'
    };
  }

  function normalizeResponse(payload) {
    if (!payload) return [];
    if (typeof payload === 'string') {
      try { payload = JSON.parse(payload); } catch (_) { return []; }
    }
    if (payload.res) {
      try { payload = typeof payload.res === 'string' ? JSON.parse(payload.res) : payload.res; } catch (_) {}
    }
    const arr = Array.isArray(payload) ? payload
      : Array.isArray(payload.data) ? payload.data
      : Array.isArray(payload.results) ? payload.results
      : Array.isArray(payload.items) ? payload.items
      : Array.isArray(payload.suggestions) ? payload.suggestions
      : [];
    return arr.map(item => {
      if (typeof item === 'string') return { id: item, name: item, type: '', audience_size: '', path: '', raw: item };
      const path = Array.isArray(item.path) ? item.path.join(' › ') : (item.path || item.topic || item.category || '');
      return {
        id: String(item.id || item.key || item.value || item.name || ''),
        name: String(item.name || item.title || item.label || item.value || item.id || ''),
        type: String(item.type || item.class || item.kind || item.targeting_type || ''),
        audience_size: item.audience_size || item.audienceSize || item.size || '',
        path: String(path || ''),
        raw: item
      };
    }).filter(x => x.name || x.id);
  }

  function makePanel(input) {
    const box = closestBox(input);
    if (getComputedStyle(box).position === 'static') box.style.position = 'relative';
    let panel = box.querySelector(':scope > .remask-targeting-autocomplete-panel');
    if (!panel) {
      panel = document.createElement('div');
      panel.className = 'remask-targeting-autocomplete-panel';
      panel.hidden = true;
      box.appendChild(panel);
    }
    return panel;
  }

  function setPanel(panel, html) {
    panel.innerHTML = html;
    panel.hidden = false;
  }

  function hidePanel(panel) {
    panel.hidden = true;
    panel.innerHTML = '';
  }

  function setHiddenNear(input, names, value) {
    const scope = input.closest('.form-group, .field, .input-group, .mb-3, .card, .panel, form') || document;
    for (const name of names) {
      let el = scope.querySelector(`[name="${name}"], #${CSS.escape(name)}`);
      if (!el) {
        el = document.createElement('input');
        el.type = 'hidden';
        el.name = name;
        input.insertAdjacentElement('afterend', el);
      }
      if ('value' in el) el.value = value || '';
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  function selectSuggestion(input, item) {
    input.value = item.name;
    input.dataset.selectedTargetingId = item.id || '';
    input.dataset.selectedTargetingType = item.type || '';
    input.dataset.selectedTargetingRaw = JSON.stringify(item.raw || item);
    setHiddenNear(input, ['targeting_id', 'interest_id', 'behavior_id', 'selected_targeting_id'], item.id || '');
    setHiddenNear(input, ['targeting_type', 'selected_targeting_type'], item.type || '');
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));

    const box = closestBox(input);
    const add = Array.from(box.querySelectorAll('button, a.btn, input[type="button"]')).find(btn => /add|select|choose|добав|выбр|обрати|додати/i.test(btn.textContent || btn.value || ''));
    if (add) setTimeout(() => add.click(), 0);
  }

  async function directSearch(input, panel, query, seq) {
    const prev = inflight.get(input);
    if (prev && prev.abort) prev.abort.abort();
    const abort = new AbortController();
    inflight.set(input, { seq, abort });

    const ctx = getContext(input);
    const params = new URLSearchParams();
    params.set('q', query);
    params.set('query', query);
    params.set('search', query);
    params.set('term', query);
    params.set('type', 'all');
    params.set('targeting_type', 'all');
    if (ctx.profile) params.set('profile', ctx.profile);
    if (ctx.account_id) {
      params.set('account_id', ctx.account_id);
      params.set('ad_account_id', ctx.account_id);
      params.set('act_id', ctx.account_id);
    }
    if (ctx.locale) params.set('locale', ctx.locale);

    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Accept': 'application/json' },
        body: params,
        signal: abort.signal
      });
      const text = await response.text();
      let payload;
      try { payload = JSON.parse(text); } catch (_) { payload = null; }
      const state = inflight.get(input);
      if (!state || state.seq !== seq) return;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const results = normalizeResponse(payload);
      if (!results.length) {
        setPanel(panel, '<div class="remask-targeting-empty">No matching interests/behaviors</div>');
        return;
      }
      panel.innerHTML = '';
      results.slice(0, 12).forEach(item => {
        const row = document.createElement('button');
        row.type = 'button';
        row.className = 'remask-targeting-result';
        const meta = [item.type, item.audience_size ? `Audience ${item.audience_size}` : '', item.path].filter(Boolean).join(' · ');
        row.innerHTML = `<span class="remask-targeting-name"></span>${meta ? '<small></small>' : ''}`;
        row.querySelector('.remask-targeting-name').textContent = item.name;
        const small = row.querySelector('small');
        if (small) small.textContent = meta;
        row.addEventListener('mousedown', e => { e.preventDefault(); selectSuggestion(input, item); hidePanel(panel); });
        panel.appendChild(row);
      });
      panel.hidden = false;
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      setPanel(panel, `<div class="remask-targeting-empty">Targeting endpoint error: ${String(e.message || e)}</div>`);
    }
  }

  function install(input) {
    if (attached.has(input) || !isTargetingInput(input)) return;
    removeSearchButtonsNear(input);
    const panel = makePanel(input);
    let timer = 0;
    let seq = 0;

    const run = () => {
      removeSearchButtonsNear(input);
      const query = String(input.value || '').trim();
      window.clearTimeout(timer);
      if (query.length < MIN_LEN) {
        hidePanel(panel);
        return;
      }
      const currentSeq = ++seq;
      setPanel(panel, '<div class="remask-targeting-empty">Searching Meta targeting…</div>');
      timer = window.setTimeout(() => directSearch(input, panel, query, currentSeq), DEBOUNCE_MS);
    };

    input.setAttribute('autocomplete', 'off');
    input.dataset.remaskLiveTargeting = 'true';
    input.addEventListener('input', run);
    input.addEventListener('focus', () => {
      removeSearchButtonsNear(input);
      if (String(input.value || '').trim().length >= MIN_LEN) run();
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const first = panel.querySelector('.remask-targeting-result');
        if (first) first.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
        else run();
      }
      if (e.key === 'Escape') hidePanel(panel);
    });

    attached.set(input, true);
  }

  function scan() {
    document.querySelectorAll('input[type="text"], input[type="search"], textarea').forEach(install);
  }

  function injectStyle() {
    if (document.getElementById('remask-targeting-autocomplete-style')) return;
    const style = document.createElement('style');
    style.id = 'remask-targeting-autocomplete-style';
    style.textContent = `
      button[data-remask-removed-search="true"],
      input[data-remask-removed-search="true"],
      a[data-remask-removed-search="true"] { display: none !important; width: 0 !important; min-width: 0 !important; padding: 0 !important; margin: 0 !important; border: 0 !important; visibility: hidden !important; }
      .remask-targeting-autocomplete-panel {
        position: absolute;
        z-index: 3000;
        left: 0;
        right: 0;
        top: calc(100% + 6px);
        max-height: 320px;
        overflow: auto;
        border: 1px solid rgba(255,255,255,.14);
        border-radius: 14px;
        background: rgba(16, 18, 27, .96);
        box-shadow: 0 18px 55px rgba(0,0,0,.38);
        padding: 6px;
        backdrop-filter: blur(18px);
      }
      .remask-targeting-result {
        display: block;
        width: 100%;
        text-align: left;
        border: 0;
        border-radius: 10px;
        padding: 10px 12px;
        background: transparent;
        color: inherit;
        cursor: pointer;
      }
      .remask-targeting-result:hover,
      .remask-targeting-result:focus { background: rgba(255,255,255,.09); outline: none; }
      .remask-targeting-name { display: block; font-weight: 650; line-height: 1.2; }
      .remask-targeting-result small,
      .remask-targeting-empty { display: block; color: rgba(255,255,255,.62); font-size: 12px; line-height: 1.35; margin-top: 3px; }
      .remask-targeting-empty { padding: 10px 12px; }
      input[data-remask-live-targeting="true"] { padding-right: 34px; }
    `;
    document.head.appendChild(style);
  }

  function boot() {
    injectStyle();
    scan();
    const obs = new MutationObserver(scan);
    obs.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
JS;
file_put_contents($scriptPath, $js);
fwrite(STDERR, "[remask targeting overlay] scripts/targeting-autocomplete.js ready\n");

$launchPhp = $root . '/launch.php';
if (is_file($launchPhp)) {
    $php = file_get_contents($launchPhp);
    if ($php === false) { fwrite(STDERR, "[remask targeting overlay] cannot read launch.php\n"); exit(51); }
    $tag = '<script src="scripts/targeting-autocomplete.js?v=20260917-no-search"></script>';
    if (strpos($php, 'targeting-autocomplete.js') === false) {
        if (stripos($php, '</body>') !== false) {
            $php = str_ireplace('</body>', $tag . "\n</body>", $php);
        } else {
            $php .= "\n" . $tag . "\n";
        }
    } else {
        $php = preg_replace('#<script\s+src="scripts/targeting-autocomplete\.js\?v=[^"]*"></script>#', $tag, $php) ?? $php;
    }
    file_put_contents($launchPhp, $php);
    fwrite(STDERR, "[remask targeting overlay] launch.php script tag ready\n");
} else {
    fwrite(STDERR, "[remask targeting overlay] launch.php missing\n");
    exit(52);
}
