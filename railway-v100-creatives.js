const $ = (id) => document.getElementById(id);
let items = [];
let editing = null;
let previewUrl = '';
let carouselFiles = [];
let carouselPreviewUrls = [];

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
function currentCarouselMeta() {
    return Array.from(document.querySelectorAll('#carouselRows .cr-carousel-row')).map((row) => ({
        headline: row.querySelector('.car-headline')?.value.trim() || '',
        description: row.querySelector('.car-description')?.value.trim() || '',
        link: row.querySelector('.car-link')?.value.trim() || '',
    }));
}
function renderFormat() {
    const format = $('presetFormat').value;
    $('singleSection').style.display = format === 'SINGLE' ? 'block' : 'none';
    $('carouselSection').style.display = format === 'CAROUSEL' ? 'block' : 'none';
    $('instagramSection').style.display = format === 'INSTAGRAM_POST' ? 'block' : 'none';
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
function openEditor(item = null) {
    clearObjectUrls();
    editing = item;
    carouselFiles = [];
    $('creativeForm').reset();
    $('creativeId').value = item?.id || '';
    $('creativeEditorTitle').textContent = item ? 'Редактирование' : 'Новое крео';

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
    $('presetInstagramMediaId').value = item?.instagram_media_id || '';
    $('presetMedia').value = '';
    $('presetCarousel').value = '';

    $('singlePreview').innerHTML = item?.format === 'SINGLE' && item.media
        ? mediaHtml(item.preview_url, item.media.media_type)
        : '<i class="fa-regular fa-image"></i>';
    $('singleCurrent').textContent = item?.format === 'SINGLE' && item.media
        ? item.media.original_name + ' · ' + formatBytes(item.media.size_bytes)
        : '';

    renderFormat();
    renderCarousel();
    setStatus('');
    $('creativeModal').classList.add('open');
    $('creativeModal').setAttribute('aria-hidden', 'false');
}
function render() {
    const query = $('creativeSearch').value.trim().toLowerCase();
    const rows = items.filter((item) => {
        const haystack = [
            item.name,
            item.creative_name,
            item.ad_name,
            item.message,
            item.headline,
            item.description,
            item.destination_url,
            item.media?.original_name
        ].join(' ').toLowerCase();
        return !query || haystack.includes(query);
    });

    $('creativeCount').textContent = rows.length + ' крео';
    if (!rows.length) {
        $('creativeGrid').innerHTML = '<div class="cr-empty">' + (items.length ? 'Ничего не найдено' : 'Пока пусто') + '</div>';
        return;
    }

    $('creativeGrid').innerHTML = rows.map((item) => {
        let fileLabel = '';
        if (item.format === 'SINGLE') {
            fileLabel = item.media?.original_name || 'Файл отсутствует';
        } else if (item.format === 'CAROUSEL') {
            fileLabel = (item.carousel?.length || 0) + ' карточок';
        } else {
            fileLabel = 'Instagram ' + (item.instagram_media_id || '');
        }

        const preview = item.preview_url
            ? mediaHtml(item.preview_url, item.format === 'SINGLE' ? item.media?.media_type : 'image')
            : '<div class="cr-empty">Instagram post / reel</div>';

        return '<article class="cr-card" data-id="' + esc(item.id) + '">' +
            '<div class="cr-preview">' + preview + '<span class="cr-format">' + formatLabel(item.format) + '</span></div>' +
            '<div class="cr-body">' +
                '<div class="cr-name" title="' + esc(item.name || item.id) + '">' + esc(item.name || item.id) + '</div>' +
                '<div class="cr-file">' + esc(fileLabel) + '</div>' +
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
    const format = $('presetFormat').value;
    const form = new FormData();
    const id = $('creativeId').value.trim();

    form.append('action', 'save');
    if (id) form.append('id', id);
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
        instagram_media_id: $('presetInstagramMediaId').value.trim(),
    };
    for (const [key, value] of Object.entries(values)) form.append(key, value);

    if (format === 'SINGLE') {
        const file = $('presetMedia').files[0];
        if (file) form.append('media', file, file.name);
        if (!id && !file) {
            setStatus('Выбери image или video.', 'bad');
            return;
        }
    } else if (format === 'CAROUSEL') {
        const meta = currentCarouselMeta();
        const existingCount = editing?.format === 'CAROUSEL' ? (editing.carousel || []).length : 0;
        const count = carouselFiles.length || existingCount;
        if (count < 2 || count > 10) {
            setStatus('Carousel требует 2–10 изображений.', 'bad');
            return;
        }
        form.append('carousel_cards', JSON.stringify(meta));
        for (const file of carouselFiles) form.append('carousel_media[]', file, file.name);
    } else {
        if (!/^\d+$/.test($('presetInstagramMediaId').value.trim())) {
            setStatus('Instagram media ID должен быть числом.', 'bad');
            return;
        }
    }

    $('saveCreative').disabled = true;
    setStatus('Сохраняю…');
    try {
        const data = await api('ajax/creativeLibrary.php', {method:'POST', body:form});
        items = data.items || [];
        render();
        setStatus('Сохранено.', 'ok');
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

$('newCreative').addEventListener('click', () => openEditor());
$('closeCreative').addEventListener('click', closeEditor);
$('cancelCreative').addEventListener('click', closeEditor);
$('creativeModal').addEventListener('click', (event) => {
    if (event.target === $('creativeModal')) closeEditor();
});
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && $('creativeModal').classList.contains('open')) closeEditor();
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
