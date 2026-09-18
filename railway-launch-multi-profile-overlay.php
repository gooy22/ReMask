<?php
/**
 * v94: Direct Launch multi-profile picker.
 *
 * The backend already supports per-target profile/account pairs. This overlay
 * removes the single-profile limitation in the direct Launch UI while keeping
 * the legacy #adAccount multi-select as a hidden compatibility layer for the
 * existing Review/Funding/Bindings/Job code.
 */
$root='/var/www/html';
$phpPath=$root.'/launch.php';
$jsPath=$root.'/scripts/launch.js';
if(!is_file($phpPath)||!is_file($jsPath)){fwrite(STDERR,"[multi-profile] runtime files missing\n");exit(171);}
$php=file_get_contents($phpPath);
$js=file_get_contents($jsPath);
if($php===false||$js===false){fwrite(STDERR,"[multi-profile] read failed\n");exit(172);}

/* ---------- Launch markup ---------- */
if(strpos($php,'REMASK_MULTI_PROFILE_PICKER_V1')===false){
    $oldProfile=<<<'HTML'
            <div class="col-md-5">
                <label for="profile">Facebook profile</label>
                <select id="profile" class="form-control">
                    <option value="">Select profile</option>
                    <?php foreach ($accounts as $acc) { ?>
                        <option value="<?= htmlspecialchars($acc->name, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>"><?= htmlspecialchars($acc->name, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></option>
                    <?php } ?>
                </select>
            </div>
HTML;
    $newProfile=<<<'HTML'
            <div class="col-md-4"><!-- REMASK_MULTI_PROFILE_PICKER_V1 -->
                <label for="profile">Facebook profiles <span class="muted">(Ctrl/Cmd + click, Shift = range)</span></label>
                <select id="profile" class="form-control" multiple size="7">
                    <?php foreach ($accounts as $acc) { ?>
                        <option value="<?= htmlspecialchars($acc->name, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?>"><?= htmlspecialchars($acc->name, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></option>
                    <?php } ?>
                </select>
                <div id="selectedProfileCount" class="selected-count">Selected FB: 0</div>
            </div>
HTML;
    if(strpos($php,$oldProfile)===false){fwrite(STDERR,"[multi-profile] profile markup target missing\n");exit(173);}
    $php=str_replace($oldProfile,$newProfile,$php,$pc);
    if($pc!==1){fwrite(STDERR,"[multi-profile] profile markup count=$pc\n");exit(174);}

    $oldAccounts=<<<'HTML'
            <div class="col-md-5">
                <label for="adAccount">Ad accounts <span class="muted">(Ctrl/Cmd + click for multiple)</span></label>
                <select id="adAccount" class="form-control" multiple size="7" disabled><option value="">Run preflight first</option></select>
                <div id="selectedAccountCount" class="selected-count">Selected: 0</div>
            </div>
HTML;
    $newAccounts=<<<'HTML'
            <div class="col-md-6">
                <div class="d-flex justify-content-between align-items-end mb-1">
                    <label class="mb-0">Ad accounts / RK <span class="muted">(click = one, Ctrl/Cmd = toggle, Shift = range)</span></label>
                    <div>
                        <button id="rkSelectAll" type="button" class="btn btn-secondary btn-sm">SELECT ALL</button>
                        <button id="rkClear" type="button" class="btn btn-outline-secondary btn-sm">CLEAR</button>
                    </div>
                </div>
                <input id="rkFilter" class="form-control form-control-sm mb-1" placeholder="Filter RK / FB profile / currency">
                <div class="rk-picker-wrap">
                    <table class="job-table rk-picker-table">
                        <thead><tr><th style="width:34px"></th><th>FB profile</th><th>RK</th><th>Currency</th></tr></thead>
                        <tbody id="rkPickerRows"><tr><td colspan="4" class="muted">Run preflight first.</td></tr></tbody>
                    </table>
                </div>
                <select id="adAccount" class="form-control" multiple size="7" disabled style="display:none" aria-hidden="true"><option value="">Run preflight first</option></select>
                <div id="selectedAccountCount" class="selected-count">Selected: 0</div>
            </div>
HTML;
    if(strpos($php,$oldAccounts)===false){fwrite(STDERR,"[multi-profile] RK markup target missing\n");exit(175);}
    $php=str_replace($oldAccounts,$newAccounts,$php,$ac);
    if($ac!==1){fwrite(STDERR,"[multi-profile] RK markup count=$ac\n");exit(176);}

    $cssNeedle='        .selected-count { margin-top:6px; color:#8f97a5; font-size:12px; }';
    $cssReplace=$cssNeedle."\n".<<<'CSS'
        #profile { min-height:180px; }
        .rk-picker-wrap { max-height:230px; overflow:auto; border:1px solid #454b57; border-radius:6px; background:#1f2228; }
        .rk-picker-table { margin-top:0; }
        .rk-picker-table thead th { position:sticky; top:0; z-index:2; background:#252932; }
        .rk-picker-row { cursor:pointer; user-select:none; }
        .rk-picker-row:hover { background:#303641; }
        .rk-picker-row.is-selected { background:#29436a; }
        .rk-picker-row td { vertical-align:middle; }
CSS;
    if(strpos($php,$cssNeedle)===false){fwrite(STDERR,"[multi-profile] CSS insertion target missing\n");exit(177);}
    $php=str_replace($cssNeedle,$cssReplace,$php,$cc);
    if($cc!==1){fwrite(STDERR,"[multi-profile] CSS insertion count=$cc\n");exit(178);}

    $php=str_replace(
        '<div id="preflightResult" class="status-box mt-3 muted">Choose a profile and run preflight.</div>',
        '<div id="preflightResult" class="status-box mt-3 muted">Choose one or more Facebook profiles and run preflight.</div>',
        $php
    );
}

/* ---------- JS selection helpers ---------- */
if(strpos($js,'REMASK_MULTI_PROFILE_SELECTION_V1')===false){
    $selectedNeedle=<<<'JS'
function selectedAccountIds() {
    return Array.from($('adAccount').selectedOptions || []).map((o) => o.value).filter(Boolean);
}
JS;
    $helpers=<<<'JS'
/* REMASK_MULTI_PROFILE_SELECTION_V1 */
function selectedProfileNames() {
    return Array.from($('profile')?.selectedOptions || []).map((o) => String(o.value || '').trim()).filter(Boolean);
}

function updateProfileSelectionCount() {
    const el = $('selectedProfileCount');
    if (el) el.textContent = `Selected FB: ${selectedProfileNames().length}`;
}

let rkSelectionAnchor = -1;

function rkPickerOptions() {
    return Array.from($('adAccount')?.options || []).filter((o) => o.value);
}

function dispatchRkSelectionChange() {
    $('adAccount').dispatchEvent(new Event('change', {bubbles:true}));
}

function renderRkPicker() {
    const body = $('rkPickerRows');
    if (!body) return;
    const options = rkPickerOptions();
    const filter = String($('rkFilter')?.value || '').trim().toLowerCase();
    body.innerHTML = '';
    if (!options.length) {
        body.innerHTML = '<tr><td colspan="4" class="muted">Run preflight first.</td></tr>';
        return;
    }

    let visible = 0;
    options.forEach((opt, optionIndex) => {
        const target = targetForAccount(opt.value);
        const profile = String(opt.dataset.profile || target?.profile || state.profile || '');
        const name = String(opt.dataset.name || opt.textContent || opt.value);
        const currency = String(opt.dataset.currency || '');
        const haystack = `${profile} ${name} ${opt.value} ${currency}`.toLowerCase();
        if (filter && !haystack.includes(filter)) return;
        visible++;

        const tr = document.createElement('tr');
        tr.className = `rk-picker-row${opt.selected ? ' is-selected' : ''}`;
        tr.dataset.optionIndex = String(optionIndex);

        const check = document.createElement('input');
        check.type = 'checkbox';
        check.checked = Boolean(opt.selected);
        check.tabIndex = -1;

        const checkTd = document.createElement('td');
        checkTd.appendChild(check);
        const profileTd = document.createElement('td');
        profileTd.textContent = profile || '—';
        const rkTd = document.createElement('td');
        rkTd.innerHTML = `<b>${escapeHtml(name)}</b><div class="muted">${escapeHtml(opt.value)}</div>`;
        const currencyTd = document.createElement('td');
        currencyTd.textContent = currency || '—';

        tr.appendChild(checkTd);
        tr.appendChild(profileTd);
        tr.appendChild(rkTd);
        tr.appendChild(currencyTd);

        const applyClick = (event) => {
            const all = rkPickerOptions();
            const index = Number(tr.dataset.optionIndex);
            if (!Number.isInteger(index) || !all[index]) return;

            if (event.shiftKey && rkSelectionAnchor >= 0 && all[rkSelectionAnchor]) {
                if (!(event.ctrlKey || event.metaKey)) all.forEach((o) => { o.selected = false; });
                const from = Math.min(rkSelectionAnchor, index);
                const to = Math.max(rkSelectionAnchor, index);
                for (let i = from; i <= to; i++) all[i].selected = true;
            } else if (event.ctrlKey || event.metaKey) {
                all[index].selected = !all[index].selected;
                rkSelectionAnchor = index;
            } else {
                all.forEach((o, i) => { o.selected = i === index; });
                rkSelectionAnchor = index;
            }
            dispatchRkSelectionChange();
        };

        tr.addEventListener('click', (event) => {
            if (event.target === check) return;
            applyClick(event);
        });
        check.addEventListener('click', (event) => {
            event.stopPropagation();
            const all = rkPickerOptions();
            const index = Number(tr.dataset.optionIndex);
            if (!Number.isInteger(index) || !all[index]) return;
            all[index].selected = check.checked;
            rkSelectionAnchor = index;
            dispatchRkSelectionChange();
        });

        body.appendChild(tr);
    });

    if (!visible) body.innerHTML = '<tr><td colspan="4" class="muted">No RK match this filter.</td></tr>';
}

function selectedAccountIds() {
    return Array.from($('adAccount').selectedOptions || []).map((o) => o.value).filter(Boolean);
}
JS;
    if(strpos($js,$selectedNeedle)===false){fwrite(STDERR,"[multi-profile] selectedAccountIds target missing\n");exit(179);}
    $js=str_replace($selectedNeedle,$helpers,$js,$hc);
    if($hc!==1){fwrite(STDERR,"[multi-profile] helper insertion count=$hc\n");exit(180);}
}

/* ---------- Replace single-profile preflight ---------- */
$oldPreflight=<<<'JS'
async function runPreflight(force = false) {
    if (targetMode()) {
        state.targets = [];
        state.targetBindings = {};
        state.targetBindingAssets = {pages: {}, pixels: {}, audiences: {}, images: {}, videos: {}, creatives: {}};
        renderTargetBindings();
    }
    const profile = $('profile').value;
    if (!profile) return alert('Select Facebook profile first.');
    state.profile = profile;
    resetDynamic();
    show($('preflightResult'), 'Checking token and Meta access...');
    try {
        const data = await apiJson('ajax/metaPreflight.php', formPost({profile, force: force ? '1' : '0'}));
        const accounts = data.ad_accounts?.data ?? [];
        state.accounts = accounts;
        $('adAccount').innerHTML = '';
        for (const a of accounts) {
            $('adAccount').appendChild(option(a.id, `${a.name || a.id} — ${a.id} — ${a.currency || ''}`));
        }
        $('adAccount').disabled = accounts.length === 0;

        const permissionSummary = [
            `API: ${data.api_version}`,
            `Identity: ${data.identity?.name || ''} (${data.identity?.id || ''})`,
            `ads_management: ${data.ads_management_granted ? 'YES' : 'NO'}`,
            `ads_read: ${data.ads_read_granted ? 'YES' : 'NO'}`,
            `Ad accounts: ${accounts.length}`,
            `Cache: ${data._cache?.hit ? 'HIT' : 'REFRESH'}${data._cache?.fetched_at ? ' — ' + data._cache.fetched_at : ''}`,
        ].join('\n');
        show($('preflightResult'), permissionSummary, data.ads_management_granted ? 'ready' : 'failed');
        renderApiUsage(data.api_usage || null);

        await loadPages(force);
        validateReady();
    } catch (e) {
        show($('preflightResult'), e.payload || e.message, 'failed');
    }
}
JS;
$newPreflight=<<<'JS'
/* REMASK_MULTI_PROFILE_PREFLIGHT_V1 */
async function runPreflight(force = false) {
    const profiles = selectedProfileNames();
    if (!profiles.length) return alert('Select at least one Facebook profile first.');

    state.targets = [];
    state.targetBindings = {};
    state.targetBindingAssets = {pages: {}, pixels: {}, audiences: {}, images: {}, videos: {}, creatives: {}};
    state.profile = profiles[0];
    resetDynamic();
    renderTargetBindings();
    renderRkPicker();
    updateProfileSelectionCount();

    show($('preflightResult'), `Checking ${profiles.length} Facebook profile(s) and Meta access...`);

    const results = new Array(profiles.length);
    let completed = 0;
    await runPool(profiles, Math.min(4, profiles.length), async (profile, index) => {
        try {
            const data = await apiJson('ajax/metaPreflight.php', formPost({profile, force: force ? '1' : '0'}));
            results[index] = {profile, data, error: null};
        } catch (e) {
            results[index] = {profile, data: null, error: e.payload?.message || e.message || String(e)};
        }
        completed++;
        show($('preflightResult'), `Checking Facebook profiles: ${completed}/${profiles.length}...`);
    });

    const successful = results.filter((row) => row?.data);
    if (!successful.length) {
        const errors = results.map((row) => `${row?.profile || 'FB'}: ERROR — ${row?.error || 'Preflight failed'}`).join('\n');
        show($('preflightResult'), errors || 'Preflight failed for all selected Facebook profiles.', 'failed');
        renderRkPicker();
        return;
    }

    const combinedAccounts = [];
    const combinedTargets = [];
    const seen = new Map();
    let duplicateAccounts = 0;

    for (const row of successful) {
        const accounts = row.data?.ad_accounts?.data ?? [];
        for (const raw of accounts) {
            const accountId = normalizeAccountIdClient(raw.id);
            if (!accountId) continue;
            if (seen.has(accountId)) {
                duplicateAccounts++;
                continue;
            }
            seen.set(accountId, row.profile);
            const account = {...raw, id: accountId, _profile: row.profile};
            combinedAccounts.push(account);
            combinedTargets.push({profile: row.profile, business_id: '', account_id: accountId});
        }
    }

    state.accounts = combinedAccounts;
    // The server already supports multi-profile target objects. Enable target
    // mode only when more than one FB profile is selected; one-profile behavior
    // remains fully backward-compatible.
    state.targets = profiles.length > 1 ? combinedTargets : [];
    state.profile = profiles[0];

    const select = $('adAccount');
    select.innerHTML = '';
    for (const a of combinedAccounts) {
        const opt = option(a.id, `[${a._profile}] ${a.name || a.id} — ${a.id} — ${a.currency || ''}`);
        opt.dataset.profile = a._profile || '';
        opt.dataset.name = a.name || a.id || '';
        opt.dataset.currency = a.currency || '';
        select.appendChild(opt);
    }
    select.disabled = combinedAccounts.length === 0;
    renderRkPicker();
    updateSelectedCount();

    const summary = results.map((row) => {
        if (!row?.data) return `${row?.profile || 'FB'}: ERROR — ${row?.error || 'Preflight failed'}`;
        const accounts = row.data.ad_accounts?.data ?? [];
        return `${row.profile}: OK — ${accounts.length} RK — ads_management ${row.data.ads_management_granted ? 'YES' : 'NO'} — ${row.data._cache?.hit ? 'CACHE' : 'REFRESH'}`;
    });
    if (duplicateAccounts) summary.push(`Deduplicated RK visible through multiple selected FB profiles: ${duplicateAccounts}. First selected profile is used for each duplicate RK.`);
    summary.push(`Total unique RK: ${combinedAccounts.length}.`);
    show($('preflightResult'), summary.join('\n'), results.some((row) => row?.error) ? '' : 'ready');

    const usageRow = successful.find((row) => row.data?.api_usage);
    if (usageRow) renderApiUsage(usageRow.data.api_usage || null);

    if (profiles.length === 1) {
        await loadPages(force);
    } else {
        renderTargetBindings();
    }
    validateReady();
}
JS;
if(strpos($js,'REMASK_MULTI_PROFILE_PREFLIGHT_V1')===false){
    if(strpos($js,$oldPreflight)===false){fwrite(STDERR,"[multi-profile] runPreflight target missing\n");exit(181);}
    $js=str_replace($oldPreflight,$newPreflight,$js,$rpc);
    if($rpc!==1){fwrite(STDERR,"[multi-profile] runPreflight replacement count=$rpc\n");exit(182);}
}

/* ---------- Replace single-profile change reset ---------- */
$oldListener=<<<'JS'
$('profile').addEventListener('change', () => {
    state.targets = [];
    state.targetBindings = {};
    state.targetBindingAssets = {pages: {}, pixels: {}, audiences: {}, images: {}, videos: {}, creatives: {}};
    $('profile').disabled = false; $('preflight').disabled = false; $('syncMeta').disabled = false;
    renderTargetBindings();
    invalidateAssetCache(); state.profile = $('profile').value; clearExistingMediaSelection(); resetDynamic();
});
JS;
$newListener=<<<'JS'
$('profile').addEventListener('change', () => {
    state.targets = [];
    state.targetBindings = {};
    state.targetBindingAssets = {pages: {}, pixels: {}, audiences: {}, images: {}, videos: {}, creatives: {}};
    $('profile').disabled = false; $('preflight').disabled = false; $('syncMeta').disabled = false;
    renderTargetBindings();
    invalidateAssetCache();
    state.profile = selectedProfileNames()[0] || '';
    clearExistingMediaSelection();
    resetDynamic();
    updateProfileSelectionCount();
    renderRkPicker();
});
JS;
if(strpos($js,$oldListener)===false){fwrite(STDERR,"[multi-profile] profile listener target missing\n");exit(183);}
$js=str_replace($oldListener,$newListener,$js,$lc);
if($lc!==1){fwrite(STDERR,"[multi-profile] profile listener count=$lc\n");exit(184);}

/* ---------- Picker event wiring ---------- */
if(strpos($js,'REMASK_RK_TABLE_EVENTS_V1')===false){
    $js .= <<<'JS'

/* REMASK_RK_TABLE_EVENTS_V1 */
(() => {
    const adSelect = $('adAccount');
    const filter = $('rkFilter');
    const selectAll = $('rkSelectAll');
    const clear = $('rkClear');

    adSelect?.addEventListener('change', renderRkPicker);
    filter?.addEventListener('input', renderRkPicker);

    selectAll?.addEventListener('click', () => {
        const q = String(filter?.value || '').trim().toLowerCase();
        for (const opt of rkPickerOptions()) {
            const target = targetForAccount(opt.value);
            const profile = String(opt.dataset.profile || target?.profile || state.profile || '');
            const name = String(opt.dataset.name || opt.textContent || opt.value);
            const currency = String(opt.dataset.currency || '');
            const match = !q || `${profile} ${name} ${opt.value} ${currency}`.toLowerCase().includes(q);
            if (match) opt.selected = true;
        }
        dispatchRkSelectionChange();
    });

    clear?.addEventListener('click', () => {
        for (const opt of rkPickerOptions()) opt.selected = false;
        rkSelectionAnchor = -1;
        dispatchRkSelectionChange();
    });

    if (adSelect && typeof MutationObserver !== 'undefined') {
        new MutationObserver(() => renderRkPicker()).observe(adSelect, {childList:true});
    }

    updateProfileSelectionCount();
    renderRkPicker();
})();
JS;
}

/* Cache bust after all Launch overlays. */
$php=preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260918-multi-profile-v94" type="module"></script>',
    $php,
    1,
    $sc
) ?? $php;
if($sc!==1){fwrite(STDERR,"[multi-profile] launch.js tag missing\n");exit(185);}

file_put_contents($phpPath,$php);
file_put_contents($jsPath,$js);
fwrite(STDERR,"[multi-profile] v94 multi-FB + RK table selector ready\n");
