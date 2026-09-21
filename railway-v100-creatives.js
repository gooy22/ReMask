const $ = (id) => document.getElementById(id);
let items = [];
let editing = null;
let previewUrl = '';
let carouselFiles = [];
let carouselPreviewUrls = [];
let metaSdkSchema = null;
let pendingMetaBuilder = null;
let metaCapabilities = null;
let metaContext = {profile:'', accountId:''};
let audienceEstimateTimer = null;
let audienceEstimateSeq = 0;
let placementCapabilitiesTimer = null;
let placementCapabilitiesSeq = 0;
let metaPlacementOptions = {};
let pendingPlacementTargeting = null;
const creativeTargetingSelections = {geo:[], excludedGeo:[], interests:[], behaviors:[]};
const creativeTargetingTimers = {geo:0, excludedGeo:0, interests:0, behaviors:0};
const creativeTargetingControllers = {geo:null, excludedGeo:null, interests:null, behaviors:null};
const coveredMetaFields = {
    campaign: new Set(['name','objective','buying_type','special_ad_categories','bid_strategy','daily_budget','lifetime_budget','spend_cap','start_time','stop_time','status']),
    adset: new Set(['name','optimization_goal','billing_event','bid_strategy','bid_amount','destination_type','daily_budget','lifetime_budget','start_time','end_time','attribution_spec','promoted_object','is_dynamic_creative','is_incremental_attribution_enabled','status']),
    targeting: new Set(['age_min','age_max','genders','locales','geo_locations','excluded_geo_locations','interests','behaviors','custom_audiences','excluded_custom_audiences','flexible_spec','exclusions','publisher_platforms','facebook_positions','instagram_positions','messenger_positions','audience_network_positions','threads_positions','whatsapp_positions','device_platforms','user_os','user_device']),
    creative: new Set(['degrees_of_freedom_spec','asset_feed_spec','platform_customizations']),
    ad: new Set(['status','conversion_domain','priority','tracking_specs']),
};

function csrf() {
    return document.querySelector('meta[name="remask-csrf"]')?.content || '';
}
function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (c) => ({
        '&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'
    }[c]));
}
function formatBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
}
async function api(url, options = {}) {
    options.headers = {...(options.headers || {})};
    if ((options.method || 'GET').toUpperCase() !== 'GET') {
        options.headers['X-ReMask-CSRF'] = csrf();
    }
    const response = await fetch(url, options);
    const raw = await response.text();
    let json;
    try { json = JSON.parse(raw); }
    catch { throw new Error('Invalid JSON (' + response.status + ')'); }
    if (!response.ok || json.ok === false) {
        const error = json.error || json;
        throw new Error(error.message || ('HTTP ' + response.status));
    }
    return json.data;
}
function setStatus(message, type = '') {
    const el = $('creativeStatus');
    el.textContent = message || '';
    el.className = 'cr-status' + (type ? ' ' + type : '');
}
function mediaHtml(src, type) {
    if (!src) return '<i class="fa-regular fa-image"></i>';
    return type === 'video'
        ? '<video src="' + esc(src) + '" controls muted preload="metadata"></video>'
        : '<img src="' + esc(src) + '" alt="">';
}
function formatLabel(format) {
    if (format === 'CAROUSEL') return 'CAROUSEL';
    if (format === 'INSTAGRAM_POST') return 'IG POST';
    return 'SINGLE';
}
function clearObjectUrls() {
    if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
        previewUrl = '';
    }
    for (const url of carouselPreviewUrls) URL.revokeObjectURL(url);
    carouselPreviewUrls = [];
}
function closeEditor() {
    $('creativeModal').classList.remove('open');
    $('creativeModal').setAttribute('aria-hidden', 'true');
    clearObjectUrls();
}
function switchTab(name) {
    document.querySelectorAll('.cr-tab').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
    document.querySelectorAll('.cr-panel').forEach((panel) => panel.classList.toggle('active', panel.dataset.panel === name));
}
function parseJsonField(id, fallback) {
    const raw = $(id).value.trim();
    if (!raw) return fallback;
    try { return JSON.parse(raw); }
    catch { throw new Error('Невалидный JSON: ' + id); }
}
function stringify(value) {
    return value === undefined || value === null || (Array.isArray(value) && !value.length) || (typeof value === 'object' && !Array.isArray(value) && !Object.keys(value).length)
        ? ''
        : JSON.stringify(value, null, 2);
}
function csvStrings(value) {
    return String(value || '').split(',').map((v) => v.trim()).filter(Boolean);
}
function csvInts(value) {
    return csvStrings(value).map((v) => Number(v)).filter((v) => Number.isInteger(v));
}
function numericValue(id) {
    const raw = $(id).value.trim();
    if (raw === '') return undefined;
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
}
function dateValue(id) {
    const raw = $(id).value.trim();
    return raw || undefined;
}
function compactObject(obj) {
    const out = {};
    for (const [key, value] of Object.entries(obj || {})) {
        if (value === undefined || value === null || value === '') continue;
        if (Array.isArray(value) && value.length === 0) continue;
        if (typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length === 0) continue;
        out[key] = value;
    }
    return out;
}
function deepMerge(base, extra) {
    const out = {...(base || {})};
    for (const [key, value] of Object.entries(extra || {})) {
        if (value && typeof value === 'object' && !Array.isArray(value) && out[key] && typeof out[key] === 'object' && !Array.isArray(out[key])) {
            out[key] = deepMerge(out[key], value);
        } else {
            out[key] = value;
        }
    }
    return out;
}
function selectedValues(selector, attr) {
    return Array.from(document.querySelectorAll(selector)).filter((el) => el.checked).map((el) => el.getAttribute(attr)).filter(Boolean);
}
function placementMode() {
    return document.querySelector('input[name="placementMode"]:checked')?.value || 'auto';
}

function placementLabel(value) {
    const labels = {
        feed:'Feed', right_hand_column:'Right column', marketplace:'Marketplace',
        video_feeds:'Video feeds', story:'Stories', search:'Search',
        instream_video:'In-stream video', facebook_reels:'Reels',
        facebook_reels_overlay:'Reels overlay', profile_feed:'Profile feed',
        notification:'Notifications', stream:'Feed', explore:'Explore',
        explore_home:'Explore home', reels:'Reels', ig_search:'Search',
        profile_reels:'Profile Reels', messenger_home:'Inbox',
        sponsored_messages:'Sponsored messages', classic:'Native / banner / interstitial',
        rewarded_video:'Rewarded video', threads_stream:'Threads feed',
        status:'Status', mobile:'Mobile', desktop:'Desktop', connected_tv:'Connected TV'
    };
    if (labels[value]) return labels[value];
    return String(value || '').replace(/_/g,' ').replace(/w/g,(m)=>m.toUpperCase());
}


function placementPlatformLabel(group) {
    return {
        facebook_positions:'Facebook',
        instagram_positions:'Instagram',
        messenger_positions:'Messenger',
        audience_network_positions:'Audience Network',
        threads_positions:'Threads',
        whatsapp_positions:'WhatsApp'
    }[group] || 'Meta';
}

function placementPreviewMode(value) {
    const v = String(value || '');
    if (v === 'right_hand_column') return 'column';
    if (['story','status'].includes(v)) return v === 'status' ? 'status' : 'story';
    if (['facebook_reels','facebook_reels_overlay','reels','profile_reels'].includes(v)) return 'reels';
    if (['marketplace','search','ig_search','explore','explore_home','classic'].includes(v)) return 'grid';
    if (['messenger_home','sponsored_messages','notification'].includes(v)) return 'messages';
    if (['video_feeds','instream_video','rewarded_video'].includes(v)) return 'video';
    return 'feed';
}

function placementPreviewDescription(group, value) {
    const key = String(value || '');
    const map = {
        feed:'Реклама появляется между обычными публикациями в ленте.',
        stream:'Реклама появляется между публикациями Instagram Feed.',
        story:'Полноэкранный вертикальный показ между Stories.',
        facebook_reels:'Вертикальный показ между Facebook Reels.',
        facebook_reels_overlay:'Рекламный блок поверх/рядом с Reels-контентом.',
        reels:'Вертикальный показ между Instagram Reels.',
        profile_reels:'Показ в разделе Reels профиля.',
        right_hand_column:'Desktop Facebook: рекламный блок в правой колонке.',
        marketplace:'Реклама встроена в сетку карточек Marketplace.',
        video_feeds:'Рекламное видео внутри Video Feeds.',
        instream_video:'Реклама показывается внутри видеоконтента.',
        search:'Рекламный результат среди результатов поиска Facebook.',
        ig_search:'Рекламный результат в Instagram Search.',
        explore:'Реклама среди публикаций Instagram Explore.',
        explore_home:'Реклама в сетке Explore Home.',
        profile_feed:'Реклама внутри ленты профиля.',
        notification:'Рекламный/промо-блок в интерфейсе уведомлений.',
        messenger_home:'Рекламный блок среди диалогов Messenger.',
        sponsored_messages:'Sponsored message внутри Messenger.',
        classic:'Native / banner / interstitial в Audience Network.',
        rewarded_video:'Видео с вознаграждением в Audience Network.',
        threads_stream:'Реклама в ленте Threads.',
        status:'Полноэкранный показ между WhatsApp Status.'
    };
    return map[key] || (placementPlatformLabel(group) + ': позиция ' + placementLabel(key) + '.');
}

function currentPlacementPreviewMedia() {
    const input = $('presetMedia');
    const file = input?.files?.[0] || null;
    if (previewUrl && file) {
        return {src:previewUrl, type:String(file.type || '').startsWith('video/') ? 'video' : 'image'};
    }
    if (carouselPreviewUrls.length && carouselPreviewUrls[0]) {
        return {src:carouselPreviewUrls[0], type:'image'};
    }
    if (editing?.format === 'SINGLE' && editing?.preview_url) {
        const type = String(editing?.media?.media_type || editing?.media?.mime_type || '');
        return {src:String(editing.preview_url), type:/video/i.test(type) ? 'video' : 'image'};
    }
    if (editing?.format === 'CAROUSEL') {
        const first = Array.isArray(editing?.carousel) ? editing.carousel[0] : null;
        const src = first?.preview_url || first?.media?.preview_url || '';
        if (src) return {src:String(src), type:'image'};
    }
    return null;
}

function placementPreviewMediaHtml() {
    const media = currentPlacementPreviewMedia();
    if (!media?.src) {
        return '<div class="cr-placement-demo-media-placeholder">YOUR CREATIVE</div>';
    }
    if (media.type === 'video') {
        return '<video src="' + esc(media.src) + '" autoplay muted loop playsinline preload="metadata"></video>';
    }
    return '<img src="' + esc(media.src) + '" alt="">';
}

function placementPreviewCards(mode) {
    const media = placementPreviewMediaHtml();
    const ad = '<div class="cr-placement-demo-card cr-placement-demo-ad"><div class="cr-placement-demo-media">' + media + '</div></div>';
    if (mode === 'grid') {
        return '<div class="cr-placement-demo-card"></div>' + ad +
            '<div class="cr-placement-demo-card"></div><div class="cr-placement-demo-card"></div>' +
            '<div class="cr-placement-demo-card"></div><div class="cr-placement-demo-card"></div>';
    }
    if (mode === 'messages') {
        return '<div class="cr-placement-demo-card"></div><div class="cr-placement-demo-card"></div>' + ad +
            '<div class="cr-placement-demo-card"></div><div class="cr-placement-demo-card"></div>';
    }
    if (mode === 'story' || mode === 'reels' || mode === 'status') return ad;
    if (mode === 'video') {
        return '<div class="cr-placement-demo-card"></div>' + ad + '<div class="cr-placement-demo-card"></div>';
    }
    return '<div class="cr-placement-demo-card"></div>' + ad +
        '<div class="cr-placement-demo-card"></div><div class="cr-placement-demo-card"></div>';
}

function renderPlacementHoverPreview(group, value) {
    const preview = $('placementHoverPreview');
    if (!preview) return;
    const mode = placementPreviewMode(value);
    const platform = placementPlatformLabel(group);
    preview.innerHTML =
        '<div class="cr-placement-preview-head">' +
            '<div><div class="cr-placement-preview-title">' + esc(platform + ' · ' + placementLabel(value)) + '</div>' +
            '<div class="cr-placement-preview-meta">Пример размещения рекламы</div></div>' +
            '<div class="cr-placement-preview-live">PREVIEW</div>' +
        '</div>' +
        '<div class="cr-placement-demo ' + esc(mode) + '">' +
            '<div class="cr-placement-demo-screen">' +
                '<div class="cr-placement-demo-top"><span class="cr-placement-demo-dot"></span>' + esc(platform) + '</div>' +
                '<div class="cr-placement-demo-feed">' + placementPreviewCards(mode) + '</div>' +
                '<div class="cr-placement-demo-column-ad"><div class="cr-placement-demo-media">' + placementPreviewMediaHtml() + '</div></div>' +
            '</div>' +
        '</div>' +
        '<div class="cr-placement-preview-foot">' + esc(placementPreviewDescription(group, value)) + '</div>';
}

function positionPlacementHoverPreview(anchorEl) {
    const preview = $('placementHoverPreview');
    if (!preview || !anchorEl) return;
    const rect = anchorEl.getBoundingClientRect();
    const width = 292;
    const estimatedHeight = 390;
    let left = rect.right + 12;
    if (left + width > window.innerWidth - 10) left = rect.left - width - 12;
    left = Math.max(10, Math.min(left, window.innerWidth - width - 10));
    let top = rect.top - 44;
    top = Math.max(10, Math.min(top, window.innerHeight - estimatedHeight - 10));
    preview.style.left = Math.round(left) + 'px';
    preview.style.top = Math.round(top) + 'px';
}

function showPlacementHoverPreview(label) {
    const input = label?.querySelector?.('[data-position-group]');
    if (!input || input.disabled) return;
    const group = String(input.getAttribute('data-position-group') || '');
    const value = String(input.value || '');
    if (!group || !value) return;
    renderPlacementHoverPreview(group, value);
    positionPlacementHoverPreview(label);
    const preview = $('placementHoverPreview');
    if (!preview) return;
    preview.classList.add('open');
    preview.setAttribute('aria-hidden','false');
}

function hidePlacementHoverPreview() {
    const preview = $('placementHoverPreview');
    if (!preview) return;
    preview.classList.remove('open');
    preview.setAttribute('aria-hidden','true');
}

function placementGroupContainerId(group) {
    return {
        facebook_positions:'placementFacebookOptions',
        instagram_positions:'placementInstagramOptions',
        messenger_positions:'placementMessengerOptions',
        audience_network_positions:'placementAudienceNetworkOptions',
        threads_positions:'placementThreadsOptions',
        whatsapp_positions:'placementWhatsappOptions'
    }[group] || '';
}

function selectedPlacementValues(group) {
    return Array.from(document.querySelectorAll('[data-position-group="' + group + '"]'))
        .filter((el) => el.checked && !el.disabled)
        .map((el) => String(el.value || '').trim())
        .filter(Boolean);
}

function placementGroupUsesAll(group) {
    return Boolean(document.querySelector('[data-position-all="' + group + '"]')?.checked);
}

function setPlacementMode(mode) {
    const target = mode === 'manual' ? 'manual' : 'auto';
    document.querySelectorAll('input[name="placementMode"]').forEach((el) => {
        el.checked = el.value === target;
    });
    const manual = $('manualPlacements');
    if (manual) manual.style.display = target === 'manual' ? 'block' : 'none';
}

function syncPlacementCardStates() {
    document.querySelectorAll('[data-placement-card]').forEach((card) => {
        const platform = card.getAttribute('data-placement-card');
        const publisher = document.querySelector('[data-publisher="' + platform + '"]');
        const enabled = Boolean(publisher?.checked);
        card.classList.toggle('disabled', !enabled);
        card.querySelectorAll('[data-position-all],[data-position-group]').forEach((el) => {
            el.disabled = !enabled;
        });
    });
}

function setPlacementGroupAll(group, checked) {
    const all = document.querySelector('[data-position-all="' + group + '"]');
    if (all) all.checked = Boolean(checked);
    document.querySelectorAll('[data-position-group="' + group + '"]').forEach((el) => {
        el.checked = Boolean(checked);
    });
}

function refreshPlacementGroupAll(group) {
    const items = Array.from(document.querySelectorAll('[data-position-group="' + group + '"]'))
        .filter((el) => !el.disabled);
    const all = document.querySelector('[data-position-all="' + group + '"]');
    if (!all) return;
    all.checked = items.length > 0 && items.every((el) => el.checked);
}

function initializeManualPlacementsIfEmpty() {
    if (placementMode() !== 'manual') return;
    const selectedPublishers = selectedValues('[data-publisher]', 'data-publisher');
    if (selectedPublishers.length > 0) {
        syncPlacementCardStates();
        return;
    }

    document.querySelectorAll('[data-publisher]').forEach((el) => {
        const platform = String(el.getAttribute('data-publisher') || '');
        const group = {
            facebook:'facebook_positions',
            instagram:'instagram_positions',
            messenger:'messenger_positions',
            audience_network:'audience_network_positions',
            threads:'threads_positions',
            whatsapp:'whatsapp_positions'
        }[platform];
        const hasOptions = group && document.querySelectorAll('[data-position-group="' + group + '"]').length > 0;
        el.checked = Boolean(hasOptions);
    });

    syncPlacementCardStates();

    for (const group of [
        'facebook_positions','instagram_positions','messenger_positions',
        'audience_network_positions','threads_positions','whatsapp_positions'
    ]) {
        const publisher = document.querySelector('[data-publisher="' + ({
            facebook_positions:'facebook',
            instagram_positions:'instagram',
            messenger_positions:'messenger',
            audience_network_positions:'audience_network',
            threads_positions:'threads',
            whatsapp_positions:'whatsapp'
        }[group]) + '"]');
        if (publisher?.checked) setPlacementGroupAll(group, true);
    }

    document.querySelectorAll('[data-device-platform]').forEach((el) => {
        el.checked = true;
    });
}

function renderPlacementOptions(options = {}, targeting = null) {
    const previous = {};
    const groups = [
        'facebook_positions','instagram_positions','messenger_positions',
        'audience_network_positions','threads_positions','whatsapp_positions'
    ];
    for (const group of groups) {
        previous[group] = {
            all: placementGroupUsesAll(group),
            selected: selectedPlacementValues(group)
        };
    }
    const previousPublishers = selectedValues('[data-publisher]', 'data-publisher');
    const previousDevices = selectedValues('[data-device-platform]', 'data-device-platform');
    const previousMode = placementMode();

    metaPlacementOptions = options || {};

    const livePublishers = new Set(Array.isArray(metaPlacementOptions.publisher_platforms)
        ? metaPlacementOptions.publisher_platforms.map(String)
        : []);
    const platformToGroup = {
        facebook:'facebook_positions',
        instagram:'instagram_positions',
        messenger:'messenger_positions',
        audience_network:'audience_network_positions',
        threads:'threads_positions',
        whatsapp:'whatsapp_positions'
    };
    document.querySelectorAll('[data-placement-card]').forEach((card) => {
        const platform = String(card.getAttribute('data-placement-card') || '');
        const group = platformToGroup[platform];
        const hasPositions = group && Array.isArray(metaPlacementOptions[group]) && metaPlacementOptions[group].length > 0;
        card.style.display = (livePublishers.has(platform) || hasPositions) ? '' : 'none';
    });

    for (const group of groups) {
        const id = placementGroupContainerId(group);
        const box = id ? $(id) : null;
        if (!box) continue;
        const values = Array.isArray(metaPlacementOptions[group]) ? metaPlacementOptions[group] : [];
        box.innerHTML = values.map((value) =>
            '<label class="cr-check" data-placement-preview="1" title="Наведи для предпросмотра"><input type="checkbox" data-position-group="' + esc(group) +
            '" value="' + esc(value) + '"> ' + esc(placementLabel(value)) + '</label>'
        ).join('') || '<span class="cr-hint">Meta не вернула доступные позиции.</span>';
    }

    const deviceBox = $('placementDeviceOptions');
    if (deviceBox) {
        const devices = Array.isArray(metaPlacementOptions.device_platforms) ? metaPlacementOptions.device_platforms : [];
        deviceBox.innerHTML = devices.map((value) =>
            '<label class="cr-check"><input type="checkbox" data-device-platform="' + esc(value) +
            '"> ' + esc(placementLabel(value)) + '</label>'
        ).join('');
    }

    const source = targeting || pendingPlacementTargeting;
    if (source) {
        const hasPlacementConfig = [
            'publisher_platforms','facebook_positions','instagram_positions','messenger_positions',
            'audience_network_positions','threads_positions','whatsapp_positions','device_platforms'
        ].some((key) => Array.isArray(source[key]) && source[key].length);
        setPlacementMode(hasPlacementConfig ? 'manual' : 'auto');

        const publishers = Array.isArray(source.publisher_platforms) ? source.publisher_platforms : [];
        setCheckboxValues('[data-publisher]', 'data-publisher', publishers);

        for (const group of groups) {
            const selected = Array.isArray(source[group]) ? source[group].map(String) : [];
            const allToggle = document.querySelector('[data-position-all="' + group + '"]');
            if (allToggle) allToggle.checked = selected.length === 0;
            document.querySelectorAll('[data-position-group="' + group + '"]').forEach((el) => {
                el.checked = selected.includes(String(el.value));
            });
        }
        setCheckboxValues('[data-device-platform]', 'data-device-platform',
            Array.isArray(source.device_platforms) ? source.device_platforms : []);
        pendingPlacementTargeting = null;
    } else {
        setPlacementMode(previousMode);
        setCheckboxValues('[data-publisher]', 'data-publisher', previousPublishers);
        setCheckboxValues('[data-device-platform]', 'data-device-platform', previousDevices);
        for (const group of groups) {
            const state = previous[group] || {all:true, selected:[]};
            const allToggle = document.querySelector('[data-position-all="' + group + '"]');
            if (allToggle) allToggle.checked = state.all;
            document.querySelectorAll('[data-position-group="' + group + '"]').forEach((el) => {
                el.checked = state.selected.includes(String(el.value));
            });
        }
    }

    syncPlacementCardStates();
    if (!source && placementMode() === 'manual') initializeManualPlacementsIfEmpty();
}

async function loadPlacementCapabilities(refresh = false) {
    const options = metaCapabilities?.placement_options || {};
    renderPlacementOptions(options, pendingPlacementTargeting);
    const grid = $('placementPlatformGrid');
    const devices = $('placementDevices');
    if (grid) grid.style.display = 'grid';
    if (devices) devices.style.display = (options.device_platforms || []).length ? 'block' : 'none';
    const status = $('placementCapabilitiesStatus');
    if (status) {
        status.className = 'cr-hint cr-placement-live';
        status.textContent = 'Official Meta SDK placements';
    }
}
function schedulePlacementCapabilities(delay = 350) {
    clearTimeout(placementCapabilitiesTimer);
    placementCapabilitiesTimer = setTimeout(() => loadPlacementCapabilities(false), delay);
}

function idsToAudience(value) {
    return csvStrings(value).map((id) => ({id}));
}


function creativeTargetIdentity(kind, item) {
    if (kind === 'geo' || kind === 'excludedGeo') {
        return [item?.type || '', item?.key || item?.country_code || item?.name || ''].join(':');
    }
    return String(item?.id || item?.key || '');
}

function creativeTargetLabel(kind, item) {
    if (kind === 'geo' || kind === 'excludedGeo') {
        const main = item?.name || item?.country_code || item?.key || 'GEO';
        const meta = [item?.type, item?.country_code, item?.region].filter(Boolean).join(' · ');
        return {main:String(main), meta:String(meta)};
    }
    return {main:String(item?.name || item?.id || item?.key || ''), meta:String(item?.id || item?.key || '')};
}

function renderCreativeTargetPills(kind) {
    const ids = {geo:'mbGeoPills', excludedGeo:'mbExcludedGeoPills', interests:'mbInterestPills', behaviors:'mbBehaviorPills'};
    const box = $(ids[kind]);
    if (!box) return;
    const rows = creativeTargetingSelections[kind] || [];
    box.innerHTML = '';
    for (const item of rows) {
        const label = creativeTargetLabel(kind, item);
        const pill = document.createElement('span');
        pill.className = 'cr-target-pill';
        pill.title = label.meta;
        const text = document.createElement('span');
        text.textContent = label.main;
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '×';
        remove.title = 'Удалить';
        remove.addEventListener('click', () => {
            const identity = creativeTargetIdentity(kind, item);
            creativeTargetingSelections[kind] = creativeTargetingSelections[kind].filter((row) => creativeTargetIdentity(kind,row) !== identity);
            syncCreativeTargetingRaw(kind);
            renderCreativeTargetPills(kind);
        });
        pill.append(text, remove);
        box.appendChild(pill);
    }
}

function geoSpecFromRows(rows) {
    const spec = {};
    const countries = [];
    const regions = [];
    const cities = [];
    const zips = [];
    const geoMarkets = [];
    for (const item of rows || []) {
        const type = String(item?.type || '').toLowerCase();
        if (type === 'country') {
            const code = String(item?.country_code || item?.key || '').trim();
            if (code && !countries.includes(code)) countries.push(code);
        } else if (type === 'region') {
            const key = String(item?.key || '').trim();
            if (key && !regions.some((row) => String(row.key) === key)) regions.push({key});
        } else if (type === 'city') {
            const key = String(item?.key || '').trim();
            if (!key || cities.some((row) => String(row.key) === key)) continue;
            const row = {key};
            if (Number.isFinite(Number(item?.radius)) && Number(item.radius) > 0) row.radius = Number(item.radius);
            if (item?.distance_unit) row.distance_unit = item.distance_unit;
            cities.push(row);
        } else if (type === 'zip') {
            const key = String(item?.key || '').trim();
            if (key && !zips.some((row) => String(row.key) === key)) zips.push({key});
        } else if (type === 'geo_market') {
            const key = String(item?.key || '').trim();
            if (key && !geoMarkets.some((row) => String(row.key) === key)) geoMarkets.push({key});
        }
    }
    if (countries.length) spec.countries = countries;
    if (regions.length) spec.regions = regions;
    if (cities.length) spec.cities = cities;
    if (zips.length) spec.zips = zips;
    if (geoMarkets.length) spec.geo_markets = geoMarkets;
    return spec;
}

function syncCreativeTargetingRaw(kind) {
    if (kind === 'geo') {
        $('mbGeo').value = stringify(geoSpecFromRows(creativeTargetingSelections.geo));
    } else if (kind === 'excludedGeo') {
        $('mbExcludedGeo').value = stringify(geoSpecFromRows(creativeTargetingSelections.excludedGeo));
    } else if (kind === 'interests') {
        $('mbInterests').value = stringify((creativeTargetingSelections.interests || []).map((row) => ({
            id:String(row.id || ''), name:String(row.name || row.id || '')
        })).filter((row) => row.id));
    } else if (kind === 'behaviors') {
        $('mbBehaviors').value = stringify((creativeTargetingSelections.behaviors || []).map((row) => ({
            id:String(row.id || ''), name:String(row.name || row.id || '')
        })).filter((row) => row.id));
    }
}

function hydrateCreativeTargetingSelections() {
    creativeTargetingSelections.geo = [];
    creativeTargetingSelections.excludedGeo = [];
    creativeTargetingSelections.interests = [];
    creativeTargetingSelections.behaviors = [];

    try {
        const geo = parseJsonField('mbGeo', {});
        for (const code of Array.isArray(geo?.countries) ? geo.countries : []) {
            creativeTargetingSelections.geo.push({type:'country', key:String(code), country_code:String(code), name:String(code)});
        }
        for (const row of Array.isArray(geo?.regions) ? geo.regions : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.geo.push({type:'region', ...row});
        }
        for (const row of Array.isArray(geo?.cities) ? geo.cities : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.geo.push({type:'city', ...row});
        }
    } catch {}

    try {
        const geo = parseJsonField('mbExcludedGeo', {});
        for (const code of Array.isArray(geo?.countries) ? geo.countries : []) {
            creativeTargetingSelections.excludedGeo.push({type:'country', key:String(code), country_code:String(code), name:String(code)});
        }
        for (const row of Array.isArray(geo?.regions) ? geo.regions : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.excludedGeo.push({type:'region', ...row});
        }
        for (const row of Array.isArray(geo?.cities) ? geo.cities : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.excludedGeo.push({type:'city', ...row});
        }
        for (const row of Array.isArray(geo?.zips) ? geo.zips : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.excludedGeo.push({type:'zip', ...row});
        }
        for (const row of Array.isArray(geo?.geo_markets) ? geo.geo_markets : []) {
            if (row && typeof row === 'object' && row.key !== undefined) creativeTargetingSelections.excludedGeo.push({type:'geo_market', ...row});
        }
    } catch {}

    try {
        const interests = parseJsonField('mbInterests', []);
        if (Array.isArray(interests)) creativeTargetingSelections.interests = interests.filter((row) => row && (row.id || typeof row === 'string')).map((row) => typeof row === 'string' ? {id:row,name:row} : row);
    } catch {}

    try {
        const behaviors = parseJsonField('mbBehaviors', []);
        if (Array.isArray(behaviors)) creativeTargetingSelections.behaviors = behaviors.filter((row) => row && (row.id || typeof row === 'string')).map((row) => typeof row === 'string' ? {id:row,name:row} : row);
    } catch {}

    renderCreativeTargetPills('geo');
    renderCreativeTargetPills('excludedGeo');
    renderCreativeTargetPills('interests');
    renderCreativeTargetPills('behaviors');
}

function addCreativeTarget(kind, item) {
    const identity = creativeTargetIdentity(kind, item);
    if (!identity) return;
    if (!(creativeTargetingSelections[kind] || []).some((row) => creativeTargetIdentity(kind,row) === identity)) {
        creativeTargetingSelections[kind].push(item);
        syncCreativeTargetingRaw(kind);
        renderCreativeTargetPills(kind);
    }
}

function creativeTargetSearchConfig(kind) {
    return {
        geo:{input:'mbGeoSearch', results:'mbGeoResults', apiType:'locations'},
        excludedGeo:{input:'mbExcludedGeoSearch', results:'mbExcludedGeoResults', apiType:'locations'},
        interests:{input:'mbInterestSearch', results:'mbInterestResults', apiType:'interests'},
        behaviors:{input:'mbBehaviorSearch', results:'mbBehaviorResults', apiType:'behaviors'}
    }[kind];
}

function closeCreativeTargetResults(kind) {
    const cfg = creativeTargetSearchConfig(kind);
    const box = cfg ? $(cfg.results) : null;
    if (!box) return;
    box.classList.remove('open');
    box.innerHTML = '';
}

function renderCreativeTargetResults(kind, items) {
    const cfg = creativeTargetSearchConfig(kind);
    const box = cfg ? $(cfg.results) : null;
    if (!box) return;
    box.innerHTML = '';
    if (!items.length) {
        box.innerHTML = '<div class="cr-target-empty">Ничего не найдено</div>';
        box.classList.add('open');
        return;
    }
    for (const item of items) {
        const row = document.createElement('div');
        row.className = 'cr-target-result';
        const label = creativeTargetLabel(kind === 'excludedGeo' ? 'geo' : kind, item);
        row.innerHTML = '<div>' + esc(label.main) + '</div>' + (label.meta ? '<small>' + esc(label.meta) + '</small>' : '');
        row.addEventListener('click', () => {
            addCreativeTarget(kind, item);
            const input = $(cfg.input);
            if (input) input.value = '';
            closeCreativeTargetResults(kind);
        });
        box.appendChild(row);
    }
    box.classList.add('open');
}

async function searchCreativeTargeting(kind) {
    const cfg = creativeTargetSearchConfig(kind);
    if (!cfg) return;
    const input = $(cfg.input);
    const results = $(cfg.results);
    const query = String(input?.value || '').trim();

    clearTimeout(creativeTargetingTimers[kind]);
    creativeTargetingControllers[kind]?.abort();
    creativeTargetingControllers[kind] = null;

    if (query.length < 2) {
        if (results) {
            results.classList.remove('open');
            results.innerHTML = '';
        }
        return;
    }

    const controller = new AbortController();
    creativeTargetingControllers[kind] = controller;
    if (results) {
        results.innerHTML = '<div class="cr-target-empty">Ищу в Meta…</div>';
        results.classList.add('open');
    }

    const params = new URLSearchParams();
    params.set('type', cfg.apiType);
    params.set('q', query);
    params.set('limit', '25');

    try {
        const data = await api('ajax/metaTargetingSearch.php', {
            method:'POST',
            headers:{'Content-Type':'application/x-www-form-urlencoded; charset=UTF-8'},
            body:params.toString(),
            signal:controller.signal
        });
        if (creativeTargetingControllers[kind] !== controller) return;
        const rows = Array.isArray(data) ? data
            : Array.isArray(data?.data) ? data.data
            : Array.isArray(data?.items) ? data.items
            : [];
        renderCreativeTargetResults(kind, rows);
    } catch (error) {
        if (error?.name === 'AbortError') return;
        if (creativeTargetingControllers[kind] !== controller) return;
        if (results) {
            results.innerHTML = '<div class="cr-target-empty">Meta: ' + esc(error.message) + '</div>';
            results.classList.add('open');
        }
    } finally {
        if (creativeTargetingControllers[kind] === controller) creativeTargetingControllers[kind] = null;
    }
}

function installCreativeTargetSearch(kind) {
    const cfg = creativeTargetSearchConfig(kind);
    const input = cfg ? $(cfg.input) : null;
    if (!input) return;
    input.addEventListener('input', () => {
        clearTimeout(creativeTargetingTimers[kind]);
        creativeTargetingTimers[kind] = setTimeout(() => searchCreativeTargeting(kind), 240);
    });
    input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            clearTimeout(creativeTargetingTimers[kind]);
            searchCreativeTargeting(kind);
        }
        if (event.key === 'Escape') closeCreativeTargetResults(kind);
    });
}

function setMetaSelectOptions(id, values, options = {}) {
    const el = $(id);
    if (!el) return;
    const previous = String(el.value || '');
    const fallback = String(options.fallback ?? '');
    const placeholder = options.placeholder ?? null;
    const normalized = Array.from(new Set((values || []).map((v) => String(v)).filter(Boolean)));

    el.innerHTML = '';
    if (placeholder !== null) el.appendChild(new Option(String(placeholder), ''));
    for (const value of normalized) el.appendChild(new Option(value, value));

    if (previous && normalized.includes(previous)) el.value = previous;
    else if (fallback && (fallback === '' || normalized.includes(fallback))) el.value = fallback;
    else if (placeholder !== null) el.value = '';
    else if (normalized.length) el.value = normalized[0];
}

function schemaEnum(group, field) {
    const values = metaSdkSchema?.[group]?.enums?.[field];
    return Array.isArray(values) ? values : [];
}

function constrainTargetingCheckboxes(selector, attr, allowed) {
    const set = new Set((allowed || []).map(String));
    document.querySelectorAll(selector).forEach((el) => {
        const value = String(el.getAttribute(attr) || '');
        const supported = set.size === 0 || set.has(value);
        el.disabled = !supported;
        const label = el.closest('label');
        if (label) {
            label.style.display = supported ? '' : 'none';
            label.title = supported ? '' : 'Нет в текущей Meta SDK schema';
        }
        if (!supported) el.checked = false;
    });
}

function populatePrimaryMetaControls() {
    if (!metaSdkSchema) return;

    setMetaSelectOptions('mbObjective', schemaEnum('campaign','objective'), {fallback:'OUTCOME_TRAFFIC'});
    setMetaSelectOptions('mbSpecialCategory', schemaEnum('campaign','special_ad_categories'), {fallback:'NONE'});
    setMetaSelectOptions('mbCampaignBidStrategy', schemaEnum('campaign','bid_strategy'), {placeholder:'Meta default'});
    setMetaSelectOptions('mbCampaignStatus', schemaEnum('campaign','status'), {fallback:'PAUSED'});

    const schemaOptimizationGoals = schemaEnum('adset','optimization_goal');
    const liveOptimizationGoals = (metaCapabilities?.conversion_goals || [])
        .map((row) => String(row?.performance_goal || '').trim())
        .filter(Boolean);
    setMetaSelectOptions('mbOptimizationGoal', [...new Set([...liveOptimizationGoals, ...schemaOptimizationGoals])], {fallback:'LINK_CLICKS'});
    setMetaSelectOptions('mbBillingEvent', schemaEnum('adset','billing_event'), {fallback:'IMPRESSIONS'});
    setMetaSelectOptions('mbAdsetBidStrategy', schemaEnum('adset','bid_strategy'), {fallback:'LOWEST_COST_WITHOUT_CAP'});
    setMetaSelectOptions('mbDestinationType', schemaEnum('adset','destination_type'), {placeholder:'Meta default'});
    setMetaSelectOptions('mbAdsetStatus', schemaEnum('adset','status'), {fallback:'PAUSED'});
    setMetaSelectOptions('mbAdStatus', schemaEnum('ad','status'), {fallback:'PAUSED'});

    if (Array.isArray(metaCapabilities?.cta_types) && metaCapabilities.cta_types.length) {
        setMetaSelectOptions('presetCta', metaCapabilities.cta_types, {fallback:'LEARN_MORE'});
    }
    if (Array.isArray(metaCapabilities?.preview_formats) && metaCapabilities.preview_formats.length) {
        const preferred = [
            'MOBILE_FEED_STANDARD','DESKTOP_FEED_STANDARD','FACEBOOK_STORY_MOBILE',
            'FACEBOOK_REELS_MOBILE','INSTAGRAM_STANDARD','INSTAGRAM_STORY','INSTAGRAM_REELS',
            'MARKETPLACE_MOBILE','MESSENGER_MOBILE_STORY_MEDIA','WHATSAPP_STATUS_MEDIA'
        ];
        const all = [...new Set([...preferred.filter((x) => metaCapabilities.preview_formats.includes(x)), ...metaCapabilities.preview_formats])];
        setMetaSelectOptions('metaPreviewFormat', all, {fallback:'MOBILE_FEED_STANDARD'});
    }

}

function renderMetaDatalist(id, rows, valueKey = 'id', labelKeys = ['name']) {
    const list = $(id);
    if (!list) return;
    list.innerHTML = '';
    for (const row of rows || []) {
        const value = String(row?.[valueKey] ?? '').trim();
        if (!value) continue;
        const label = labelKeys.map((key) => String(row?.[key] ?? '').trim()).filter(Boolean).join(' · ');
        const option = document.createElement('option');
        option.value = value;
        if (label) option.label = label;
        list.appendChild(option);
    }
}

async function loadMetaContext() {
    const data = await api('ajax/metaCreativeCapabilities.php');
    metaCapabilities = data || {};
    if (data?.schema) metaSdkSchema = data.schema;
    renderMetaDatalist(
        'mbConversionEventOptions',
        (data?.standard_conversion_events || []).map((name) => ({id:name,name})),
        'id',
        ['name']
    );
    return data;
}

function metaFieldId(group, field) {
    return 'sdk_' + group + '_' + field.replace(/[^a-zA-Z0-9_]/g, '_');
}
function metaTypeIsComplex(type) {
    type = String(type || '');
    return type.includes('list<') || type.includes('Object') || type.includes('map') || type.includes('Targeting') || type.includes('IDName') || type.includes('AdSet') || type.includes('AdCreative');
}
function metaFieldValue(el, type, field) {
    if (!el) return undefined;
    const raw = String(el.value ?? '').trim();
    if (raw === '') return undefined;
    type = String(type || 'string');

    if (type === 'bool') {
        if (raw === 'true') return true;
        if (raw === 'false') return false;
        return undefined;
    }
    if (type === 'unsigned int' || type === 'int') {
        const value = Number(raw);
        if (!Number.isInteger(value)) throw new Error(field + ': нужно целое число.');
        return value;
    }
    if (type === 'float') {
        const value = Number(raw);
        if (!Number.isFinite(value)) throw new Error(field + ': нужно число.');
        return value;
    }
    if (metaTypeIsComplex(type)) {
        try {
            return JSON.parse(raw);
        } catch {
            if (type === 'list<string>' || type === 'list<enum>') return csvStrings(raw);
            if (type === 'list<unsigned int>') return csvInts(raw);
            throw new Error(field + ': невалидный JSON.');
        }
    }
    return raw;
}
function metaFieldDisplayValue(value, type) {
    if (value === undefined || value === null) return '';
    if (type === 'bool') return value === true ? 'true' : value === false ? 'false' : '';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}
function renderMetaSdkFields() {
    const root = $('metaSdkFields');
    if (!root || !metaSdkSchema) return;
    const groups = ['campaign','adset','targeting','creative','ad'];
    root.innerHTML = '';

    for (const group of groups) {
        const spec = metaSdkSchema[group] || {};
        const fields = spec.fields || {};
        const enums = spec.enums || {};
        const names = Object.keys(fields).filter((field) => !coveredMetaFields[group]?.has(field));

        const details = document.createElement('details');
        details.className = 'cr-sdk-group';
        details.open = false;
        details.dataset.sdkGroup = group;

        const summary = document.createElement('summary');
        summary.innerHTML = '<span>' + esc(group.toUpperCase()) + '</span><span class="cr-sdk-count">' + names.length + ' полей</span>';
        details.appendChild(summary);

        const grid = document.createElement('div');
        grid.className = 'cr-sdk-fields';

        for (const field of names) {
            const type = fields[field] || 'string';
            const wrap = document.createElement('div');
            wrap.className = 'cr-sdk-field';
            wrap.dataset.search = (group + ' ' + field + ' ' + type).toLowerCase();

            const label = document.createElement('label');
            label.htmlFor = metaFieldId(group, field);
            label.innerHTML = '<span>' + esc(field) + '</span><span class="cr-sdk-type">' + esc(type) + '</span>';
            wrap.appendChild(label);

            let input;
            const enumValues = Array.isArray(enums[field]) ? enums[field] : [];
            if (type === 'bool') {
                input = document.createElement('select');
                input.className = 'form-control';
                input.innerHTML = '<option value="">Meta default</option><option value="true">true</option><option value="false">false</option>';
            } else if (type === 'enum' && enumValues.length) {
                input = document.createElement('select');
                input.className = 'form-control';
                input.appendChild(new Option('Meta default', ''));
                for (const value of enumValues) input.appendChild(new Option(value, value));
            } else if (metaTypeIsComplex(type)) {
                input = document.createElement('textarea');
                input.className = 'form-control cr-json';
                input.placeholder = type.startsWith('list<') ? '[]' : '{}';
            } else {
                input = document.createElement('input');
                input.className = 'form-control';
                if (type === 'unsigned int' || type === 'int' || type === 'float') input.type = 'number';
                else input.type = 'text';
                if (type === 'datetime') input.placeholder = 'ISO 8601 / Meta datetime';
            }

            input.id = metaFieldId(group, field);
            input.dataset.metaGroup = group;
            input.dataset.metaField = field;
            input.dataset.metaType = type;
            wrap.appendChild(input);
            grid.appendChild(wrap);
        }

        details.appendChild(grid);
        root.appendChild(details);
    }
    $('metaSchemaStatus').textContent = 'SDK-схема загружена';
    if (pendingMetaBuilder) {
        const saved = pendingMetaBuilder;
        pendingMetaBuilder = null;
        populateMetaSdkFields(saved);
    }
}
function readMetaSdkFields() {
    const out = {campaign:{},adset:{},targeting:{},creative:{},ad:{}};
    document.querySelectorAll('[data-meta-group][data-meta-field]').forEach((el) => {
        const group = el.dataset.metaGroup;
        const field = el.dataset.metaField;
        const type = el.dataset.metaType || 'string';
        const value = metaFieldValue(el, type, field);
        if (value !== undefined) out[group][field] = value;
    });
    return out;
}
function populateMetaSdkFields(builder) {
    if (!metaSdkSchema || !$('metaSdkFields')?.children.length) {
        pendingMetaBuilder = builder || {};
        return;
    }
    builder = builder || {};
    document.querySelectorAll('[data-meta-group][data-meta-field]').forEach((el) => {
        const group = el.dataset.metaGroup;
        const field = el.dataset.metaField;
        const type = el.dataset.metaType || 'string';
        const value = builder[group]?.[field];
        el.value = metaFieldDisplayValue(value, type);
    });
}
function filterMetaSdkFields() {
    const q = ($('metaFieldSearch')?.value || '').trim().toLowerCase();
    document.querySelectorAll('.cr-sdk-group').forEach((group) => {
        let visible = 0;
        group.querySelectorAll('.cr-sdk-field').forEach((field) => {
            const match = !q || (field.dataset.search || '').includes(q);
            field.classList.toggle('cr-sdk-hidden', !match);
            if (match) visible++;
        });
        group.classList.toggle('cr-sdk-hidden', visible === 0);
        if (q && visible > 0) group.open = true;
    });
}
async function loadMetaSdkSchema() {
    const initial = await loadMetaContext('', '', false);
    metaSdkSchema = initial?.schema || metaSdkSchema || {};
    metaCapabilities = initial || {};
    renderMetaSdkFields();
    populatePrimaryMetaControls();
    renderPlacementOptions(metaCapabilities?.placement_options || {}, pendingPlacementTargeting);
    const status = $('placementCapabilitiesStatus');
    if (status) {
        status.className = 'cr-hint cr-placement-live';
        status.textContent = 'Official Meta SDK placements';
    }
}
function buildMetaBuilder() {
    const special = $('mbSpecialCategory').value;
    const sdkFields = readMetaSdkFields();
    let campaign = deepMerge(parseJsonField('mbAdvancedCampaign', {}), sdkFields.campaign);
    campaign = deepMerge(campaign, compactObject({
        name: $('mbCampaignName').value.trim(),
        objective: $('mbObjective').value,
        buying_type: $('mbBuyingType').value,
        special_ad_categories: special && special !== 'NONE' ? [special] : [],
        bid_strategy: $('mbCampaignBidStrategy').value,
        daily_budget: numericValue('mbCampaignDailyBudget'),
        lifetime_budget: numericValue('mbCampaignLifetimeBudget'),
        spend_cap: numericValue('mbCampaignSpendCap'),
        start_time: dateValue('mbCampaignStart'),
        stop_time: dateValue('mbCampaignStop'),
        status: $('mbCampaignStatus').value,
    }));

    const promotedExtra = parseJsonField('mbPromotedObject', {});
    const promotedObject = deepMerge(promotedExtra, compactObject({
        custom_event_type: $('mbConversionEvent').value.trim(),
    }));
    let adset = deepMerge(parseJsonField('mbAdvancedAdset', {}), sdkFields.adset);
    adset = deepMerge(adset, compactObject({
        name: $('mbAdsetName').value.trim(),
        optimization_goal: $('mbOptimizationGoal').value,
        billing_event: $('mbBillingEvent').value,
        bid_strategy: $('mbAdsetBidStrategy').value,
        bid_amount: numericValue('mbBidAmount'),
        destination_type: $('mbDestinationType').value,
        daily_budget: numericValue('mbAdsetDailyBudget'),
        lifetime_budget: numericValue('mbAdsetLifetimeBudget'),
        start_time: dateValue('mbAdsetStart'),
        end_time: dateValue('mbAdsetEnd'),
        attribution_spec: parseJsonField('mbAttributionSpec', undefined),
        promoted_object: Object.keys(promotedObject).length ? promotedObject : undefined,
        is_dynamic_creative: $('mbDynamicCreative').checked ? true : undefined,
        is_incremental_attribution_enabled: $('mbIncrementalAttribution').checked ? true : undefined,
        status: $('mbAdsetStatus').value,
    }));

    let targeting = deepMerge(parseJsonField('mbAdvancedTargeting', {}), sdkFields.targeting);
    const manualPlacements = placementMode() === 'manual';
    const publisherPlatforms = manualPlacements ? selectedValues('[data-publisher]', 'data-publisher') : [];
    const devicePlatforms = manualPlacements ? selectedValues('[data-device-platform]', 'data-device-platform') : [];
    if (manualPlacements && publisherPlatforms.length === 0) {
        throw new Error('Manual placements: выбери хотя бы одну платформу.');
    }
    if (manualPlacements) {
        const platformGroups = {
            facebook:'facebook_positions',
            instagram:'instagram_positions',
            messenger:'messenger_positions',
            audience_network:'audience_network_positions',
            threads:'threads_positions',
            whatsapp:'whatsapp_positions'
        };
        for (const platform of publisherPlatforms) {
            const group = platformGroups[platform];
            if (!group || placementGroupUsesAll(group)) continue;
            if (selectedPlacementValues(group).length === 0) {
                throw new Error('Manual placements: для ' + placementLabel(platform) + ' выбери место показа или «Все доступные».');
            }
        }
    }

    const placementSpec = manualPlacements ? compactObject({
        publisher_platforms: publisherPlatforms,
        facebook_positions: placementGroupUsesAll('facebook_positions') ? undefined : selectedPlacementValues('facebook_positions'),
        instagram_positions: placementGroupUsesAll('instagram_positions') ? undefined : selectedPlacementValues('instagram_positions'),
        messenger_positions: placementGroupUsesAll('messenger_positions') ? undefined : selectedPlacementValues('messenger_positions'),
        audience_network_positions: placementGroupUsesAll('audience_network_positions') ? undefined : selectedPlacementValues('audience_network_positions'),
        threads_positions: placementGroupUsesAll('threads_positions') ? undefined : selectedPlacementValues('threads_positions'),
        whatsapp_positions: placementGroupUsesAll('whatsapp_positions') ? undefined : selectedPlacementValues('whatsapp_positions'),
        device_platforms: devicePlatforms,
    }) : {};

    // Advantage+ placements means no manual placement restriction at all.
    if (!manualPlacements) {
        for (const key of [
            'publisher_platforms','facebook_positions','instagram_positions','messenger_positions',
            'audience_network_positions','threads_positions','whatsapp_positions','device_platforms'
        ]) delete targeting[key];
    }

    targeting = deepMerge(targeting, compactObject({
        age_min: numericValue('mbAgeMin'),
        age_max: numericValue('mbAgeMax'),
        genders: $('mbGender').value ? [Number($('mbGender').value)] : undefined,
        locales: csvInts($('mbLocales').value),
        geo_locations: parseJsonField('mbGeo', undefined),
        excluded_geo_locations: parseJsonField('mbExcludedGeo', undefined),
        interests: parseJsonField('mbInterests', undefined),
        behaviors: parseJsonField('mbBehaviors', undefined),
        flexible_spec: parseJsonField('mbFlexibleSpec', undefined),
        exclusions: parseJsonField('mbExclusions', undefined),
        ...placementSpec,
        user_os: csvStrings($('mbUserOs').value),
        user_device: csvStrings($('mbUserDevice').value),
    }));

    let creative = deepMerge(parseJsonField('mbAdvancedCreative', {}), sdkFields.creative);
    creative = deepMerge(creative, compactObject({
        degrees_of_freedom_spec: parseJsonField('mbDegreesOfFreedom', undefined),
        asset_feed_spec: parseJsonField('mbAssetFeedSpec', undefined),
        platform_customizations: parseJsonField('mbPlatformCustomizations', undefined),
    }));
    const existing_media = {};
    let ad = deepMerge(parseJsonField('mbAdvancedAd', {}), sdkFields.ad);
    ad = deepMerge(ad, compactObject({
        status: $('mbAdStatus').value,
        conversion_domain: $('mbConversionDomain').value.trim(),
        priority: numericValue('mbAdPriority'),
        tracking_specs: parseJsonField('mbTrackingSpecs', undefined),
    }));

    const identity = {};

    return {campaign, adset, targeting, identity, existing_media, creative, ad};
}
function setCheckboxValues(selector, attr, values) {
    const wanted = new Set((values || []).map(String));
    document.querySelectorAll(selector).forEach((el) => {
        el.checked = wanted.has(String(el.getAttribute(attr)));
    });
}
function populateMetaBuilder(builder) {
    builder = builder || {};
    const campaign = builder.campaign || {};
    const adset = builder.adset || {};
    const targeting = builder.targeting || {};
    const identity = builder.identity || {};
    const creative = builder.creative || {};
    const ad = builder.ad || {};

    $('mbCampaignName').value = campaign.name || '';
    if (campaign.objective && Array.from($('mbObjective').options).some((o) => o.value === campaign.objective)) $('mbObjective').value = campaign.objective;
    $('mbBuyingType').value = campaign.buying_type || 'AUCTION';
    $('mbSpecialCategory').value = (campaign.special_ad_categories || [])[0] || 'NONE';
    $('mbCampaignBidStrategy').value = campaign.bid_strategy || '';
    $('mbCampaignDailyBudget').value = campaign.daily_budget ?? '';
    $('mbCampaignLifetimeBudget').value = campaign.lifetime_budget ?? '';
    $('mbCampaignSpendCap').value = campaign.spend_cap ?? '';
    $('mbCampaignStart').value = campaign.start_time || '';
    $('mbCampaignStop').value = campaign.stop_time || '';
    $('mbCampaignStatus').value = campaign.status || 'PAUSED';

    $('mbAdsetName').value = adset.name || '';
    if (adset.optimization_goal && Array.from($('mbOptimizationGoal').options).some((o) => o.value === adset.optimization_goal)) $('mbOptimizationGoal').value = adset.optimization_goal;
    if (adset.billing_event && Array.from($('mbBillingEvent').options).some((o) => o.value === adset.billing_event)) $('mbBillingEvent').value = adset.billing_event;
    $('mbAdsetBidStrategy').value = adset.bid_strategy || 'LOWEST_COST_WITHOUT_CAP';
    $('mbBidAmount').value = adset.bid_amount ?? '';
    $('mbDestinationType').value = adset.destination_type || '';
    $('mbAdsetDailyBudget').value = adset.daily_budget ?? '';
    $('mbAdsetLifetimeBudget').value = adset.lifetime_budget ?? '';
    $('mbAdsetStart').value = adset.start_time || '';
    $('mbAdsetEnd').value = adset.end_time || '';
    $('mbAttributionSpec').value = stringify(adset.attribution_spec);
    $('mbDynamicCreative').checked = Boolean(adset.is_dynamic_creative);
    $('mbIncrementalAttribution').checked = Boolean(adset.is_incremental_attribution_enabled);
    $('mbAdsetStatus').value = adset.status || 'PAUSED';
    const promoted = adset.promoted_object || {};
    $('mbConversionEvent').value = promoted.custom_event_type || '';
    const promotedTemplate = {...promoted};
    delete promotedTemplate.pixel_id;
    delete promotedTemplate.custom_conversion_id;
    $('mbPromotedObject').value = stringify(promotedTemplate);

    $('mbAgeMin').value = targeting.age_min ?? 18;
    $('mbAgeMax').value = targeting.age_max ?? 65;
    $('mbGender').value = Array.isArray(targeting.genders) && targeting.genders.length === 1 ? String(targeting.genders[0]) : '';
    $('mbLocales').value = (targeting.locales || []).join(',');
    $('mbGeo').value = stringify(targeting.geo_locations);
    $('mbExcludedGeo').value = stringify(targeting.excluded_geo_locations);
    $('mbInterests').value = stringify(targeting.interests);
    $('mbBehaviors').value = stringify(targeting.behaviors);
    $('mbFlexibleSpec').value = stringify(targeting.flexible_spec);
    $('mbExclusions').value = stringify(targeting.exclusions);
    pendingPlacementTargeting = targeting;
    renderPlacementOptions(metaPlacementOptions, targeting);
    $('mbUserOs').value = (targeting.user_os || []).join(',');
    $('mbUserDevice').value = (targeting.user_device || []).join(',');

    $('mbDegreesOfFreedom').value = stringify(creative.degrees_of_freedom_spec);
    $('mbAssetFeedSpec').value = stringify(creative.asset_feed_spec);
    $('mbPlatformCustomizations').value = stringify(creative.platform_customizations);
    $('mbAdStatus').value = ad.status || 'PAUSED';
    $('mbConversionDomain').value = ad.conversion_domain || '';
    $('mbAdPriority').value = ad.priority ?? '';
    $('mbTrackingSpecs').value = stringify(ad.tracking_specs);

    // Keep the complete saved sections here so rare official Meta fields survive editing.
    $('mbAdvancedCampaign').value = stringify(campaign);
    $('mbAdvancedAdset').value = stringify(adset);
    $('mbAdvancedTargeting').value = stringify(targeting);
    $('mbAdvancedCreative').value = stringify(creative);
    $('mbAdvancedAd').value = stringify(ad);
    hydrateCreativeTargetingSelections();
    populateMetaSdkFields(builder);
}
function currentCarouselMeta() {
    return Array.from(document.querySelectorAll('#carouselRows .cr-carousel-row')).map((row) => ({
        headline: row.querySelector('.car-headline')?.value.trim() || '',
        description: row.querySelector('.car-description')?.value.trim() || '',
        link: row.querySelector('.car-link')?.value.trim() || '',
    }));
}
function clearMetaPreview() {
    const wrap = $('metaPreviewFrameWrap');
    const frame = $('metaPreviewFrame');
    if (wrap) wrap.style.display = 'none';
    if (frame) frame.srcdoc = '';
}
function renderFormat() {
    clearMetaPreview();
    const format = $('presetFormat').value;
    $('singleSection').style.display = format === 'SINGLE' ? 'block' : 'none';
    $('carouselSection').style.display = format === 'CAROUSEL' ? 'block' : 'none';

}
function renderCarousel() {
    const useNewFiles = carouselFiles.length > 0;
    const existing = editing?.format === 'CAROUSEL' ? (editing.carousel || []) : [];
    const rows = useNewFiles ? carouselFiles : existing;
    $('carouselHint').textContent = rows.length ? rows.length + ' карточок' : 'Выбери 2–10 изображений';
    if (!rows.length) {
        $('carouselRows').innerHTML = '';
        return;
    }

    const defaults = {
        headline: $('presetHeadline').value || '',
        description: $('presetDescription').value || '',
        link: $('presetUrl').value || '',
    };

    if (useNewFiles) {
        for (const url of carouselPreviewUrls) URL.revokeObjectURL(url);
        carouselPreviewUrls = carouselFiles.map((file) => URL.createObjectURL(file));
    }

    $('carouselRows').innerHTML = rows.map((row, index) => {
        const oldCard = existing[index] || {};
        const meta = useNewFiles ? {
            headline: oldCard.headline || defaults.headline,
            description: oldCard.description || defaults.description,
            link: oldCard.link || defaults.link,
        } : row;
        const name = useNewFiles ? row.name : (row.media?.original_name || ('Card ' + (index + 1)));
        const preview = useNewFiles ? carouselPreviewUrls[index] : (row.preview_url || '');
        const size = useNewFiles ? row.size : (row.media?.size_bytes || 0);

        return '<div class="cr-carousel-row" data-index="' + index + '">' +
            '<div class="cr-thumb">' + (preview ? '<img src="' + esc(preview) + '" alt="">' : (index + 1)) + '</div>' +
            '<div><b>' + esc(name) + '</b><div class="cr-hint">' + esc(formatBytes(size)) + '</div></div>' +
            '<input class="form-control car-headline" placeholder="Headline" value="' + esc(meta.headline || '') + '">' +
            '<input class="form-control car-description" placeholder="Description" value="' + esc(meta.description || '') + '">' +
            '<input class="form-control car-link" placeholder="Link" value="' + esc(meta.link || '') + '">' +
        '</div>';
    }).join('');
}
function resetBuilderDefaults() {
    $('mbObjective').value = 'OUTCOME_TRAFFIC';
    $('mbBuyingType').value = 'AUCTION';
    $('mbSpecialCategory').value = 'NONE';
    $('mbCampaignStatus').value = 'PAUSED';
    $('mbOptimizationGoal').value = 'LINK_CLICKS';
    $('mbBillingEvent').value = 'IMPRESSIONS';
    $('mbAdsetBidStrategy').value = 'LOWEST_COST_WITHOUT_CAP';
    $('mbAdsetStatus').value = 'PAUSED';
    $('mbAgeMin').value = '18';
    $('mbAgeMax').value = '65';
    $('mbAdStatus').value = 'PAUSED';
    setPlacementMode('auto');
    setCheckboxValues('[data-publisher]', 'data-publisher', []);
    setCheckboxValues('[data-device-platform]', 'data-device-platform', []);
}
function openEditor(item = null) {
    clearObjectUrls();
    editing = item;
    carouselFiles = [];
    $('creativeForm').reset();
    resetBuilderDefaults();
    $('creativeId').value = item?.id || '';
    $('creativeEditorTitle').textContent = item ? 'Редактирование связки' : 'Новая связка';

    $('presetName').value = item?.name || '';
    $('presetCreativeName').value = item?.creative_name || '';
    $('presetAdName').value = item?.ad_name || '';
    $('presetMessage').value = item?.message || '';
    $('presetHeadline').value = item?.headline || '';
    $('presetDescription').value = item?.description || '';
    $('presetUrl').value = item?.destination_url || '';
    $('presetCta').value = item?.cta || 'LEARN_MORE';
    $('presetTags').value = item?.url_tags || '';
    $('presetFormat').value = item?.format || 'SINGLE';
    $('presetMedia').value = '';
    $('presetCarousel').value = '';

    populateMetaBuilder(item?.meta_builder || {});
    $('singlePreview').innerHTML = item?.format === 'SINGLE' && item.media
        ? mediaHtml(item.preview_url, item.media.media_type)
        : '<i class="fa-regular fa-image"></i>';
    $('singleCurrent').textContent = item?.format === 'SINGLE' && item.media
        ? item.media.original_name + (item.media.size_bytes ? (' · ' + formatBytes(item.media.size_bytes)) : '')
        : 'Изображение или видео.';

    renderFormat();
    renderCarousel();
    switchTab('campaign');
    setStatus('');
    $('creativeModal').classList.add('open');
    $('creativeModal').setAttribute('aria-hidden', 'false');
}
function render() {
    const query = $('creativeSearch').value.trim().toLowerCase();
    const rows = items.filter((item) => {
        const builder = item.meta_builder || {};
        const haystack = [
            item.name,item.creative_name,item.ad_name,item.message,item.headline,item.description,item.destination_url,
            item.media?.original_name,builder.campaign?.name,builder.campaign?.objective,builder.adset?.name,builder.adset?.optimization_goal
        ].join(' ').toLowerCase();
        return !query || haystack.includes(query);
    });

    $('creativeCount').textContent = rows.length + ' связок';
    if (!rows.length) {
        $('creativeGrid').innerHTML = '<div class="cr-empty">' + (items.length ? 'Ничего не найдено' : 'Пока пусто') + '</div>';
        return;
    }

    $('creativeGrid').innerHTML = rows.map((item) => {
        let fileLabel = '';
        if (item.format === 'SINGLE') fileLabel = item.media?.original_name || 'Файл отсутствует';
        else if (item.format === 'CAROUSEL') fileLabel = (item.carousel?.length || 0) + ' карточок';
        else fileLabel = 'Файл отсутствует';

        const objective = item.meta_builder?.campaign?.objective || '';
        const preview = item.preview_url
            ? mediaHtml(item.preview_url, item.format === 'SINGLE' ? item.media?.media_type : 'image')
            : '<div class="cr-empty">Нет превью</div>';

        return '<article class="cr-card" data-id="' + esc(item.id) + '">' +
            '<div class="cr-preview">' + preview + '<span class="cr-format">' + formatLabel(item.format) + '</span></div>' +
            '<div class="cr-body">' +
                '<div class="cr-name" title="' + esc(item.name || item.id) + '">' + esc(item.name || item.id) + '</div>' +
                '<div class="cr-file">' + esc([objective,fileLabel].filter(Boolean).join(' · ')) + '</div>' +
                '<div class="cr-actions">' +
                    '<a class="cr-launch" href="launch.php?creative_preset=' + encodeURIComponent(item.id) + '">В АВТОЗАЛИВ</a>' +
                    '<button class="cr-icon" type="button" data-action="edit" title="Изменить"><i class="fa-solid fa-pen"></i></button>' +
                    '<button class="cr-icon" type="button" data-action="duplicate" title="Дублировать"><i class="fa-regular fa-copy"></i></button>' +
                    '<button class="cr-icon" type="button" data-action="delete" title="Удалить"><i class="fa-regular fa-trash-can"></i></button>' +
                '</div>' +
            '</div>' +
        '</article>';
    }).join('');
}
async function load() {
    const data = await api('ajax/creativeLibrary.php?action=list');
    items = data.items || [];
    render();
}
async function save(event) {
    event.preventDefault();
    let metaBuilder;
    try { metaBuilder = buildMetaBuilder(); }
    catch (error) { setStatus(error.message, 'bad'); return; }

    const format = $('presetFormat').value;
    const form = new FormData();
    const id = $('creativeId').value.trim();

    form.append('action', 'save');
    if (id) form.append('id', id);
    form.append('meta_builder', JSON.stringify(metaBuilder));
    const values = {
        name: $('presetName').value.trim(),
        creative_name: $('presetCreativeName').value.trim(),
        ad_name: $('presetAdName').value.trim(),
        message: $('presetMessage').value.trim(),
        headline: $('presetHeadline').value.trim(),
        description: $('presetDescription').value.trim(),
        destination_url: $('presetUrl').value.trim(),
        cta: $('presetCta').value,
        url_tags: $('presetTags').value.trim(),
        format,
    };
    for (const [key, value] of Object.entries(values)) form.append(key, value);

    if (format === 'SINGLE') {
        const file = $('presetMedia').files[0];
        if (file) form.append('media', file, file.name);
        if (!id && !file) {
            setStatus('Выбери image или video.', 'bad');
            switchTab('creative');
            return;
        }
    } else if (format === 'CAROUSEL') {
        const meta = currentCarouselMeta();
        const existingCount = editing?.format === 'CAROUSEL' ? (editing.carousel || []).length : 0;
        const count = carouselFiles.length || existingCount;
        if (count < 2 || count > 10) {
            setStatus('Carousel требует 2–10 изображений.', 'bad');
            switchTab('creative');
            return;
        }
        form.append('carousel_cards', JSON.stringify(meta));
        for (const file of carouselFiles) form.append('carousel_media[]', file, file.name);
    }

    $('saveCreative').disabled = true;
    setStatus('Сохраняю…');
    try {
        const data = await api('ajax/creativeLibrary.php', {method:'POST', body:form});
        items = data.items || [];
        render();
        setStatus('Связка сохранена.', 'ok');
        setTimeout(closeEditor, 250);
    } catch (error) {
        setStatus(error.message, 'bad');
    } finally {
        $('saveCreative').disabled = false;
    }
}
async function itemAction(id, action) {
    const item = items.find((row) => row.id === id);
    if (!item) return;
    if (action === 'edit') {
        openEditor(item);
        return;
    }
    if (action === 'delete' && !confirm('Удалить "' + (item.name || id) + '"?')) return;
    const form = new FormData();
    form.append('action', action);
    form.append('id', id);
    try {
        const data = await api('ajax/creativeLibrary.php', {method:'POST', body:form});
        items = data.items || [];
        render();
    } catch (error) {
        alert(error.message);
    }
}

document.querySelectorAll('.cr-tab').forEach((button) => button.addEventListener('click', () => switchTab(button.dataset.tab)));
$('newCreative').addEventListener('click', () => openEditor());
$('closeCreative').addEventListener('click', closeEditor);
$('cancelCreative').addEventListener('click', closeEditor);
$('creativeModal').addEventListener('click', (event) => { if (event.target === $('creativeModal')) closeEditor(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && $('creativeModal').classList.contains('open')) closeEditor(); });
document.addEventListener('click', (event) => {
    if (event.target.closest('.cr-target-box')) return;
    document.querySelectorAll('.cr-target-results.open').forEach((el) => el.classList.remove('open'));
});
$('presetFormat').addEventListener('change', renderFormat);
$('presetMedia').addEventListener('change', function () {
    const file = this.files[0];
    if (!file) return;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    $('singlePreview').innerHTML = mediaHtml(previewUrl, file.type.startsWith('video/') ? 'video' : 'image');
    $('singleCurrent').textContent = file.name + ' · ' + formatBytes(file.size);
});
$('presetCarousel').addEventListener('change', function () {
    carouselFiles = Array.from(this.files || []);
    renderCarousel();
});
$('creativeForm').addEventListener('submit', save);
$('creativeSearch').addEventListener('input', render);
$('metaFieldSearch')?.addEventListener('input', filterMetaSdkFields);
installCreativeTargetSearch('geo');
installCreativeTargetSearch('excludedGeo');
installCreativeTargetSearch('interests');
installCreativeTargetSearch('behaviors');
for (const id of ['mbGeo','mbExcludedGeo','mbInterests','mbBehaviors']) {
    $(id)?.addEventListener('change', () => {
        hydrateCreativeTargetingSelections();
    });
}

const creativeConfigIds = [
    'mbObjective','mbDestinationType','mbOptimizationGoal','mbConversionEvent',
    'mbAgeMin','mbAgeMax','mbGender','mbLocales','mbGeo','mbExcludedGeo',
    'mbInterests','mbBehaviors','mbFlexibleSpec','mbExclusions','mbUserOs','mbUserDevice'
];
for (const id of creativeConfigIds) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', () => {
        if (id === 'mbObjective' || id === 'mbOptimizationGoal') schedulePlacementCapabilities(0);
    });
}

document.querySelectorAll('input[name="placementMode"]').forEach((el) => {
    el.addEventListener('change', () => {
        setPlacementMode(placementMode());
        if (placementMode() === 'manual') initializeManualPlacementsIfEmpty();
    });
});

document.querySelectorAll('[data-publisher]').forEach((el) => {
    el.addEventListener('change', () => {
        syncPlacementCardStates();
    });
});

document.addEventListener('change', (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.hasAttribute('data-position-all')) {
        const group = String(target.getAttribute('data-position-all') || '');
        setPlacementGroupAll(group, target.checked);
        return;
    }
    if (target.hasAttribute('data-position-group')) {
        const group = String(target.getAttribute('data-position-group') || '');
        refreshPlacementGroupAll(group);
        return;
    }
    if (target.hasAttribute('data-device-platform')) {
    }
});

$('refreshPlacements')?.addEventListener('click', () => {
    loadPlacementCapabilities(true);
});
const placementPreviewRoot = $('placementPlatformGrid');
placementPreviewRoot?.addEventListener('mouseover', (event) => {
    const label = event.target.closest?.('[data-placement-preview="1"]');
    if (!label || !placementPreviewRoot.contains(label)) return;
    if (event.relatedTarget && label.contains(event.relatedTarget)) return;
    showPlacementHoverPreview(label);
});
placementPreviewRoot?.addEventListener('mouseout', (event) => {
    const label = event.target.closest?.('[data-placement-preview="1"]');
    if (!label || !placementPreviewRoot.contains(label)) return;
    if (event.relatedTarget && label.contains(event.relatedTarget)) return;
    hidePlacementHoverPreview();
});
placementPreviewRoot?.addEventListener('focusin', (event) => {
    const label = event.target.closest?.('[data-placement-preview="1"]');
    if (label) showPlacementHoverPreview(label);
});
placementPreviewRoot?.addEventListener('focusout', () => hidePlacementHoverPreview());
$('creativeModal')?.querySelector?.('.cr-modal')?.addEventListener('scroll', () => hidePlacementHoverPreview(), {passive:true});
window.addEventListener('resize', () => hidePlacementHoverPreview(), {passive:true});

$('refreshCreatives').addEventListener('click', () => load().catch((error) => alert(error.message)));
$('creativeGrid').addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const card = button.closest('[data-id]');
    if (card) itemAction(card.dataset.id, button.dataset.action);
});

load().catch((error) => {
    $('creativeGrid').innerHTML = '<div class="cr-empty">' + esc(error.message) + '</div>';
});
loadMetaSdkSchema().catch((error) => {
    const statusEl = $('metaSchemaStatus');
    if (statusEl) statusEl.textContent = 'SDK-схема недоступна: ' + error.message;
});
loadPlacementCapabilities(false);
