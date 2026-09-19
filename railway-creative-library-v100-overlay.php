<?php
/**
 * v100: replace the simplified Creative Library with the complete current
 * Launch Creative/Ad feature set and support saved Carousel media in Launch.
 */
$root = '/var/www/html';
$copies = [
    '/tmp/remask-v100-creativeLibrary.php' => $root . '/ajax/creativeLibrary.php',
    '/tmp/remask-v100-creativePreview.php' => $root . '/ajax/creativePreview.php',
    '/tmp/remask-v100-creatives.php' => $root . '/creatives.php',
    '/tmp/remask-v100-creatives.js' => $root . '/scripts/creatives.js',
];
foreach ($copies as $from => $to) {
    if (!is_file($from)) { fwrite(STDERR, "[creative-v100] missing source $from\n"); exit(281); }
    if (!copy($from, $to)) { fwrite(STDERR, "[creative-v100] copy failed $from -> $to\n"); exit(282); }
}

$jobPath = $root . '/ajax/metaJobCreate.php';
$job = file_get_contents($jobPath);
if ($job === false) { fwrite(STDERR, "[creative-v100] could not read metaJobCreate.php\n"); exit(283); }

if (strpos($job, 'REMASK_CAROUSEL_LIBRARY_V1') === false) {
    $anchor = <<<'PHP_CODE'
    $carouselUpload = isset($_FILES['carousel_media']) && is_array($_FILES['carousel_media']) ? $_FILES['carousel_media'] : null;
PHP_CODE;
    $insert = <<<'PHP_CODE'
    /* REMASK_CAROUSEL_LIBRARY_V1 */
    $carouselLibraryIdsRaw = trim((string)($_POST['carousel_media_library_ids'] ?? ''));
    $carouselLibraryIds = $carouselLibraryIdsRaw !== ''
        ? json_decode($carouselLibraryIdsRaw, true, 512, JSON_THROW_ON_ERROR)
        : [];
    if (!is_array($carouselLibraryIds)) throw new InvalidArgumentException('carousel_media_library_ids must be a JSON array.');
    $carouselLibraryIds = array_values(array_filter(
        array_map(static fn($v) => trim((string)$v), $carouselLibraryIds),
        static fn($v) => $v !== ''
    ));

    if ($carouselLibraryIds !== []) {
        $existingCarouselUpload = isset($_FILES['carousel_media']) && is_array($_FILES['carousel_media'])
            ? $_FILES['carousel_media']
            : null;
        $existingNames = $existingCarouselUpload !== null && is_array($existingCarouselUpload['name'] ?? null)
            ? array_values((array)$existingCarouselUpload['name'])
            : [];
        if (count(array_filter($existingNames, static fn($v) => trim((string)$v) !== '')) > 0) {
            throw new InvalidArgumentException('Choose uploaded Carousel or saved Carousel, not both.');
        }
        if (count($carouselLibraryIds) < 2 || count($carouselLibraryIds) > 10) {
            throw new InvalidArgumentException('Carousel requires 2 to 10 saved images.');
        }

        $library = MediaLibraryStoreFactory::create(REMASK_MEDIA_LIBRARY_DIR, REMASK_MEDIA_LIBRARY_MAX_BYTES);
        $names = [];
        $tmpNames = [];
        $errors = [];
        foreach ($carouselLibraryIds as $index => $mediaId) {
            $item = $library->getInternal($mediaId);
            if ($item === null) throw new InvalidArgumentException('Saved Carousel media is missing for card ' . ($index + 1) . '.');
            if (($item['media_type'] ?? '') !== 'image') throw new InvalidArgumentException('Carousel currently supports image cards only.');
            $names[] = (string)$item['original_name'];
            $tmpNames[] = (string)$item['path'];
            $errors[] = UPLOAD_ERR_OK;
        }
        $_FILES['carousel_media'] = [
            'name' => $names,
            'tmp_name' => $tmpNames,
            'error' => $errors,
        ];
    }

PHP_CODE;
    if (strpos($job, $anchor) === false) { fwrite(STDERR, "[creative-v100] carousel upload anchor missing\n"); exit(284); }
    $job = str_replace($anchor, $insert . $anchor, $job, $count);
    if ($count !== 1) { fwrite(STDERR, "[creative-v100] carousel upload insertion count=$count\n"); exit(285); }
    file_put_contents($jobPath, $job);
}

$launchPath = $root . '/scripts/launch.js';
$launch = file_get_contents($launchPath);
if ($launch === false) { fwrite(STDERR, "[creative-v100] could not read launch.js\n"); exit(286); }

if (strpos($launch, 'REMASK_COMPLETE_CREATIVE_PRESET_V1') === false) {
    $stateAnchor = <<<'JS_CODE'
let remaskCreativePresets = [];
let remaskSelectedCreativePreset = null;
JS_CODE;
    $stateReplacement = <<<'JS_CODE'
let remaskCreativePresets = [];
let remaskSelectedCreativePreset = null;

/* REMASK_COMPLETE_CREATIVE_PRESET_V1 */
function renderSavedCarouselPreset(cards) {
    const box = $('carouselCards');
    if (!box) return;
    cards = Array.isArray(cards) ? cards : [];
    if (!cards.length) {
        box.innerHTML = '<div class="muted">Saved Carousel has no cards.</div>';
        return;
    }
    let rows = '';
    for (let index = 0; index < cards.length; index++) {
        const card = cards[index] || {};
        const fileName = card.media?.original_name || ('Card ' + (index + 1));
        rows += '<tr class="carousel-card" data-index="' + index + '">' +
            '<td>' + (index + 1) + '</td>' +
            '<td><b>' + escapeHtml(fileName) + '</b><div class="muted">Saved library media</div></td>' +
            '<td><input class="form-control carousel-headline" value="' + escapeHtml(card.headline || '') + '"></td>' +
            '<td><input class="form-control carousel-description" value="' + escapeHtml(card.description || '') + '"></td>' +
            '<td><input class="form-control carousel-link" value="' + escapeHtml(card.link || '') + '"></td>' +
        '</tr>';
    }
    box.innerHTML = '<div class="app-table-wrap"><table class="app-table"><thead><tr><th>#</th><th>Файл</th><th>Headline</th><th>Description</th><th>Link</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}
JS_CODE;
    if (strpos($launch, $stateAnchor) === false) { fwrite(STDERR, "[creative-v100] library state anchor missing\n"); exit(287); }
    $launch = str_replace($stateAnchor, $stateReplacement, $launch, $count);
    if ($count !== 1) { fwrite(STDERR, "[creative-v100] library state replacement count=$count\n"); exit(288); }

    $labelOld = <<<'JS_CODE'
function remaskCreativePresetLabel(item) {
    const media = item?.media || {};
    return (item?.name || item?.id || 'Creative') + (media.original_name ? ' — ' + media.original_name : '');
}
JS_CODE;
    $labelNew = <<<'JS_CODE'
function remaskCreativePresetLabel(item) {
    const media = item?.media || {};
    const format = item?.format || 'SINGLE';
    let detail = media.original_name || '';
    if (format === 'CAROUSEL') detail = ((item?.carousel || []).length || 0) + ' cards';
    if (format === 'INSTAGRAM_POST') detail = 'Instagram ' + (item?.instagram_media_id || '');
    return (item?.name || item?.id || 'Creative') + ' [' + format + ']' + (detail ? ' — ' + detail : '');
}
JS_CODE;
    if (strpos($launch, $labelOld) === false) { fwrite(STDERR, "[creative-v100] preset label function missing\n"); exit(289); }
    $launch = str_replace($labelOld, $labelNew, $launch, $count);

    $statusPattern = '/function renderCreativeLibraryStatus\(\) \{[\s\S]*?\n\}\n\nfunction clearCreativeLibrarySelection/';
    $statusNew = <<<'JS_CODE'
function renderCreativeLibraryStatus() {
    const status = $('creativeLibraryStatus');
    if (!status) return;
    if (!remaskSelectedCreativePreset) {
        status.textContent = 'Не выбрано. Можно загрузить файл ниже или выбрать существующий Meta asset.';
        if (!state.pendingWorkspaceMedia) $('media').disabled = false;
        return;
    }
    const item = remaskSelectedCreativePreset;
    const format = item.format || 'SINGLE';
    let detail = '';
    if (format === 'SINGLE') detail = item.media?.original_name || '';
    else if (format === 'CAROUSEL') detail = ((item.carousel || []).length || 0) + ' cards';
    else detail = 'Instagram ' + (item.instagram_media_id || '');
    status.textContent = 'Библиотека: ' + (item.name || item.id) + ' · ' + format + (detail ? ' · ' + detail : '');
    $('media').value = '';
    $('media').disabled = format === 'SINGLE';
}

function clearCreativeLibrarySelection
JS_CODE;
    $launch = preg_replace($statusPattern, $statusNew, $launch, 1, $count);
    if ($count !== 1) { fwrite(STDERR, "[creative-v100] status function replace count=$count\n"); exit(290); }

    $applyPattern = '/function applyCreativeLibrarySelection\(\) \{[\s\S]*?\n\}\n\nasync function loadCreativeLibraryForLaunch/';
    $applyNew = <<<'JS_CODE'
function applyCreativeLibrarySelection() {
    const select = $('creativeLibrarySelect');
    const id = select?.value || '';
    const item = remaskCreativePresets.find(function(row){ return row.id === id; }) || null;
    if (!item) {
        clearCreativeLibrarySelection();
        return;
    }
    if (item.missing_media) {
        alert('У этого крео отсутствует сохранённый media-файл.');
        clearCreativeLibrarySelection();
        return;
    }

    clearExistingMediaSelection();
    clearPerAccountExistingMediaForLibrary();
    remaskSelectedCreativePreset = item;

    const format = item.format || 'SINGLE';
    if (hasOptionValue($('creativeFormat'), format)) $('creativeFormat').value = format;

    $('creativeName').value = item.creative_name || item.name || '';
    $('adName').value = item.ad_name || item.name || '';
    $('message').value = item.message || '';
    $('headline').value = item.headline || '';
    $('description').value = item.description || '';
    $('destinationUrl').value = item.destination_url || '';
    if (item.cta && hasOptionValue($('cta'), item.cta)) $('cta').value = item.cta;
    $('urlTags').value = item.url_tags || '';
    if ($('instagramMediaId')) $('instagramMediaId').value = item.instagram_media_id || '';

    renderCreativeFormat();
    if (format === 'CAROUSEL') renderSavedCarouselPreset(item.carousel || []);
    renderCreativeLibraryStatus();
    invalidateLaunchReview();
    validateReady();
}

async function loadCreativeLibraryForLaunch
JS_CODE;
    $launch = preg_replace($applyPattern, $applyNew, $launch, 1, $count);
    if ($count !== 1) { fwrite(STDERR, "[creative-v100] apply preset replace count=$count\n"); exit(291); }

    $varsOld = <<<'JS_CODE'
    const file = (isCarousel || isInstagramPost) ? null : $('media').files[0];
    const existingMedia = (isCarousel || isInstagramPost) ? null : state.pendingWorkspaceMedia;
    const libraryPreset = (isCarousel || isInstagramPost) ? null : remaskSelectedCreativePreset;
    const perAccountExisting = !isCarousel && targetMode() && selectedTargets().every((target) => {
JS_CODE;
    $varsNew = <<<'JS_CODE'
    const file = (isCarousel || isInstagramPost) ? null : $('media').files[0];
    const existingMedia = (isCarousel || isInstagramPost) ? null : state.pendingWorkspaceMedia;
    const libraryPreset = remaskSelectedCreativePreset;
    const libraryCarousel = isCarousel && libraryPreset?.format === 'CAROUSEL' ? (libraryPreset.carousel || []) : [];
    const perAccountExisting = !isCarousel && targetMode() && selectedTargets().every((target) => {
JS_CODE;
    if (strpos($launch, $varsOld) === false) { fwrite(STDERR, "[creative-v100] createJob variable block missing\n"); exit(292); }
    $launch = str_replace($varsOld, $varsNew, $launch, $count);

    $carouselCheckOld = "    if (isCarousel && (files.length < 2 || files.length > 10)) return alert('Carousel требует от 2 до 10 изображений.');";
    $carouselCheckNew = "    if (isCarousel && (((libraryCarousel.length || files.length) < 2) || ((libraryCarousel.length || files.length) > 10))) return alert('Carousel требует от 2 до 10 изображений.');";
    if (strpos($launch, $carouselCheckOld) === false) { fwrite(STDERR, "[creative-v100] carousel count check missing\n"); exit(293); }
    $launch = str_replace($carouselCheckOld, $carouselCheckNew, $launch, $count);

    $formOld = <<<'JS_CODE'
    if (isCarousel) {
        const cards = carouselCardsPayload();
        if (cards.length !== files.length) return alert('Carousel cards were not prepared. Re-select the images.');
        form.append('carousel_cards', JSON.stringify(cards));
        for (const carouselFile of files) form.append('carousel_media[]', carouselFile, carouselFile.name);
    } else if (isInstagramPost && !targetMode()) form.append('source_instagram_media_id', $('instagramMediaId').value.trim());
    else if (libraryPreset?.media_library_id) form.append('media_library_id', libraryPreset.media_library_id);
JS_CODE;
    $formNew = <<<'JS_CODE'
    if (isCarousel) {
        const cards = carouselCardsPayload();
        const mediaCount = libraryCarousel.length || files.length;
        if (cards.length !== mediaCount) return alert('Carousel cards were not prepared.');
        form.append('carousel_cards', JSON.stringify(cards));
        if (libraryCarousel.length) {
            form.append('carousel_media_library_ids', JSON.stringify(libraryCarousel.map((card) => card.media_library_id)));
        } else {
            for (const carouselFile of files) form.append('carousel_media[]', carouselFile, carouselFile.name);
        }
    } else if (isInstagramPost && !targetMode()) form.append('source_instagram_media_id', $('instagramMediaId').value.trim());
    else if (libraryPreset?.format === 'SINGLE' && libraryPreset?.media_library_id) form.append('media_library_id', libraryPreset.media_library_id);
JS_CODE;
    if (strpos($launch, $formOld) === false) { fwrite(STDERR, "[creative-v100] createJob FormData block missing\n"); exit(294); }
    $launch = str_replace($formOld, $formNew, $launch, $count);

    $carouselRenderOld = "    if (carousel) renderCarouselCards();";
    $carouselRenderNew = "    if (carousel) { if (remaskSelectedCreativePreset?.format === 'CAROUSEL') renderSavedCarouselPreset(remaskSelectedCreativePreset.carousel || []); else renderCarouselCards(); }";
    if (strpos($launch, $carouselRenderOld) === false) { fwrite(STDERR, "[creative-v100] renderCreativeFormat carousel line missing\n"); exit(295); }
    $launch = str_replace($carouselRenderOld, $carouselRenderNew, $launch, $count);

    file_put_contents($launchPath, $launch);
}

fwrite(STDERR, "[creative-v100] full Creative/Ad functions + saved Carousel Launch handoff ready\n");
