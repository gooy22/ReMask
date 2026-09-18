<?php
/**
 * v95: live GEO + Interests search in Launch.
 * Starts after 2 characters, debounced, aborts stale requests.
 */
$root = '/var/www/html';
$launchJs = $root . '/scripts/launch.js';
$targetingJs = $root . '/scripts/targeting-autocomplete.js';
$launchPhp = $root . '/launch.php';

if (!is_file($launchJs) || !is_file($targetingJs) || !is_file($launchPhp)) {
    fwrite(STDERR, "[live-targeting] runtime files missing\n");
    exit(191);
}

$js = file_get_contents($launchJs);
$generic = file_get_contents($targetingJs);
$php = file_get_contents($launchPhp);
if ($js === false || $generic === false || $php === false) {
    fwrite(STDERR, "[live-targeting] read failed\n");
    exit(192);
}

if (strpos($js, 'REMASK_LIVE_GEO_INTEREST_V1') === false) {
    $oldFunction = <<<'JS'
async function searchTargeting(type) {
    const isGeo = type === 'locations';
    const query = $(isGeo ? 'geoQuery' : 'interestQuery').value.trim();
    const resultsEl = $(isGeo ? 'geoResults' : 'interestResults');
    if (!query) return;
    if (!state.profile) return alert('Run preflight first.');
    resultsEl.textContent = 'Searching Meta...';
    try {
        const data = await apiJson('ajax/metaTargetingSearch.php', formPost({profile: state.profile, type, q: query, limit: 25}));
        const items = data.data ?? [];
        resultsEl.innerHTML = '';
        for (const item of items) {
            const row = document.createElement('div');
            row.className = 'search-item';
            const detail = isGeo ? [item.type, item.country_code, item.region].filter(Boolean).join(' / ') : (item.id || '');
            row.textContent = `${item.name || item.key || item.id}${detail ? ' — ' + detail : ''}`;
            row.addEventListener('click', () => {
                const target = isGeo ? state.geo : state.interests;
                const identity = isGeo ? `${item.type}:${item.key || item.country_code || item.name}` : item.id;
                const exists = target.some((x) => (isGeo ? `${x.type}:${x.key || x.country_code || x.name}` : x.id) === identity);
                if (!exists) target.push(item);
                renderPills($(isGeo ? 'selectedGeo' : 'selectedInterests'), target, isGeo ? 'geo' : 'interests');
            });
            resultsEl.appendChild(row);
        }
        if (!items.length) resultsEl.textContent = 'No results.';
    } catch (e) {
        resultsEl.textContent = `Error: ${e.payload?.message || e.message}`;
    }
}
JS;

    $newFunction = <<<'JS'
/* REMASK_LIVE_GEO_INTEREST_V1 */
const targetingSearchControllers = {locations: null, interests: null};
const targetingSearchSeq = {locations: 0, interests: 0};

async function searchTargeting(type) {
    const isGeo = type === 'locations';
    const input = $(isGeo ? 'geoQuery' : 'interestQuery');
    const resultsEl = $(isGeo ? 'geoResults' : 'interestResults');
    const query = String(input?.value || '').trim();

    if (query.length < 2) {
        targetingSearchControllers[type]?.abort();
        targetingSearchControllers[type] = null;
        if (resultsEl) resultsEl.textContent = query.length ? 'Type at least 2 characters.' : '';
        return;
    }
    if (!state.profile) {
        if (resultsEl) resultsEl.textContent = 'Run preflight first.';
        return;
    }

    targetingSearchControllers[type]?.abort();
    const controller = new AbortController();
    targetingSearchControllers[type] = controller;
    const seq = ++targetingSearchSeq[type];

    resultsEl.textContent = 'Searching Meta...';
    try {
        const data = await apiJson('ajax/metaTargetingSearch.php', {
            ...formPost({profile: state.profile, type, q: query, limit: 25}),
            signal: controller.signal,
        });
        if (seq !== targetingSearchSeq[type]) return;

        const items = data.data ?? [];
        resultsEl.innerHTML = '';
        for (const item of items) {
            const row = document.createElement('div');
            row.className = 'search-item';
            const detail = isGeo ? [item.type, item.country_code, item.region].filter(Boolean).join(' / ') : (item.id || '');
            row.textContent = `${item.name || item.key || item.id}${detail ? ' — ' + detail : ''}`;
            row.addEventListener('click', () => {
                const target = isGeo ? state.geo : state.interests;
                const identity = isGeo ? `${item.type}:${item.key || item.country_code || item.name}` : item.id;
                const exists = target.some((x) => (isGeo ? `${x.type}:${x.key || x.country_code || x.name}` : x.id) === identity);
                if (!exists) target.push(item);
                renderPills($(isGeo ? 'selectedGeo' : 'selectedInterests'), target, isGeo ? 'geo' : 'interests');
            });
            resultsEl.appendChild(row);
        }
        if (!items.length) resultsEl.textContent = 'No results.';
    } catch (e) {
        if (e?.name === 'AbortError') return;
        if (seq !== targetingSearchSeq[type]) return;
        resultsEl.textContent = `Error: ${e.payload?.message || e.message}`;
    } finally {
        if (targetingSearchControllers[type] === controller) targetingSearchControllers[type] = null;
    }
}

function installLiveTargetingInput(inputId, type) {
    const input = $(inputId);
    if (!input) return;
    let timer = 0;

    const schedule = () => {
        window.clearTimeout(timer);
        const query = String(input.value || '').trim();
        if (query.length < 2) {
            searchTargeting(type);
            return;
        }
        timer = window.setTimeout(() => searchTargeting(type), 240);
    };

    input.setAttribute('autocomplete', 'off');
    input.dataset.remaskLiveMetaSearch = 'true';
    input.addEventListener('input', schedule);
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            window.clearTimeout(timer);
            searchTargeting(type);
        }
    });
}
JS;

    if (strpos($js, $oldFunction) === false) {
        fwrite(STDERR, "[live-targeting] searchTargeting target missing\n");
        exit(193);
    }
    $js = str_replace($oldFunction, $newFunction, $js, $fc);
    if ($fc !== 1) {
        fwrite(STDERR, "[live-targeting] searchTargeting replacement count=$fc\n");
        exit(194);
    }

    $oldEvents = <<<'JS'
$('geoSearch').addEventListener('click', () => searchTargeting('locations'));
$('interestSearch').addEventListener('click', () => searchTargeting('interests'));
$('geoQuery').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); searchTargeting('locations'); } });
$('interestQuery').addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); searchTargeting('interests'); } });
JS;

    $newEvents = <<<'JS'
$('geoSearch')?.addEventListener('click', () => searchTargeting('locations'));
$('interestSearch')?.addEventListener('click', () => searchTargeting('interests'));
installLiveTargetingInput('geoQuery', 'locations');
installLiveTargetingInput('interestQuery', 'interests');
JS;

    if (strpos($js, $oldEvents) === false) {
        fwrite(STDERR, "[live-targeting] old targeting events missing\n");
        exit(195);
    }
    $js = str_replace($oldEvents, $newEvents, $js, $ec);
    if ($ec !== 1) {
        fwrite(STDERR, "[live-targeting] event replacement count=$ec\n");
        exit(196);
    }
}

/* Direct Launch owns these two fields; prevent the generic targeting helper
 * from issuing a second request/panel for the same keystroke. */
if (strpos($generic, 'REMASK_SKIP_NATIVE_LIVE_TARGETING_V1') === false) {
    $needle = <<<'JS'
  function isTargetingInput(input) {
    if (!(input instanceof HTMLInputElement) && !(input instanceof HTMLTextAreaElement)) return false;
JS;
    $replace = <<<'JS'
  function isTargetingInput(input) {
    /* REMASK_SKIP_NATIVE_LIVE_TARGETING_V1 */
    if (input?.id === 'geoQuery' || input?.id === 'interestQuery') return false;
    if (!(input instanceof HTMLInputElement) && !(input instanceof HTMLTextAreaElement)) return false;
JS;
    if (strpos($generic, $needle) === false) {
        fwrite(STDERR, "[live-targeting] generic targeting hook missing\n");
        exit(197);
    }
    $generic = str_replace($needle, $replace, $generic, $gc);
    if ($gc !== 1) {
        fwrite(STDERR, "[live-targeting] generic hook replacement count=$gc\n");
        exit(198);
    }
}

$php = preg_replace(
    '#<script src="scripts/launch\.js(?:\?[^"]*)?" type="module"></script>#',
    '<script src="scripts/launch.js?v=20260919-live-targeting-v95" type="module"></script>',
    $php,
    1,
    $lc
) ?? $php;
if ($lc !== 1) {
    fwrite(STDERR, "[live-targeting] launch.js tag missing\n");
    exit(199);
}

$php = preg_replace(
    '#<script\s+src=["\']scripts/targeting-autocomplete\.js(?:\?v=[^"\']*)?["\']></script>#i',
    '<script src="scripts/targeting-autocomplete.js?v=20260919-live-targeting-v95"></script>',
    $php,
    1,
    $tc
) ?? $php;
if ($tc !== 1) {
    fwrite(STDERR, "[live-targeting] targeting script tag missing\n");
    exit(200);
}

file_put_contents($launchJs, $js);
file_put_contents($targetingJs, $generic);
file_put_contents($launchPhp, $php);
fwrite(STDERR, "[live-targeting] v95 GEO/Interests live search ready\n");
