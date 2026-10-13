/* REMASK_PRIVATE_CREATIVE_LAUNCH_COMPLETION_V1 */
let remaskBundleMedia = null;
let remaskBundleRestoreGeneration = 0;
const remaskPrivateOriginalClearPreset = clearCreativeLibrarySelection;
clearCreativeLibrarySelection = function() {
    remaskBundleMedia = null;
    remaskBundleRestoreGeneration++;
    return remaskPrivateOriginalClearPreset();
};

function remaskBundleMediaSnapshot(preset) {
    if (!preset) return null;
    const cards = preset.format === 'CAROUSEL' ? carouselCardsPayload() : [];
    return {
        id: String(preset.id || ''), name: String(preset.name || 'Медиа набора'),
        format: preset.format || 'SINGLE',
        media_library_id: preset.media_library_id || '',
        instagram_media_id: preset.instagram_media_id || '',
        carousel: (preset.carousel || []).map((card, index) => ({
            media_library_id: card.media_library_id || '', headline: card.headline || '',
            description: card.description || '', link: card.link || '', call_to_action: card.call_to_action || '',
            ...(cards[index] || {})
        })),
        meta_builder: JSON.parse(JSON.stringify(preset.meta_builder || {}))
    };
}

function bundleFormState() {
    const data = remaskLegacyBundleFormState();
    data.version = 2;
    data.fields.budgetPeriod = $('budgetPeriod')?.value || 'DAILY';
    data.fields.conversionLocation = $('conversionLocation')?.value || '';
    data.fields.launchDeliverySelect = $('launchDeliverySelect')?.value || 'PAUSED';
    data.meta_builder = remaskCurrentMetaBuilderForJob();
    data.media = remaskBundleMediaSnapshot(remaskSelectedCreativePreset) || remaskBundleMedia;
    const selected = state.pendingWorkspaceMedia;
    data.workspace_media = selected ? {
        type: selected.type, account_id: String(selected.account_id || ''),
        image_hash: selected.image_hash || '', video_id: selected.video_id || '',
        creative_id: selected.creative_id || '', name: selected.name || ''
    } : null;
    return data;
}

async function remaskPrivateCaptureBundle() {
    const data = bundleFormState();
    const format = creativeFormat();
    const files = format === 'CAROUSEL' ? carouselFiles() :
        format === 'SINGLE' ? Array.from($('media')?.files || []).slice(0, 1) : [];
    if (files.length) {
        const staged = await Promise.all(files.map(remaskPrivateStageFile));
        const cards = format === 'CAROUSEL' ? carouselCardsPayload() : [];
        if (format === 'CAROUSEL' && (cards.length !== staged.length || staged.length < 2 || staged.length > 10))
            throw new Error('Для набора нужны все карточки карусели: от 2 до 10.');
        data.media = {
            id: '', name: 'Медиа набора', format, meta_builder: {},
            media_library_id: format === 'SINGLE' ? staged[0].id : '',
            carousel: cards.map((card, index) => ({...card, media_library_id: staged[index].id})),
            checksums: staged.map(row => ({id: row.id, sha256: row.sha256}))
        };
        data.workspace_media = null;
    }
    return data;
}

async function applyBundleData(data) {
    clearCreativeLibrarySelection();
    const generation = remaskBundleRestoreGeneration;
    clearExistingMediaSelection();
    remaskBundleMedia = null;
    if ($('media')) $('media').value = '';
    if ($('carouselMedia')) $('carouselMedia').value = '';
    // Loading old bundles must clear advanced values from the previous form.
    remaskPopulateLaunchMetaSdkFields(data?.meta_builder || {});
    remaskLegacyApplyBundleData(data);
    remaskPrivateBudgetLabel();
    const snapshot = data?.media;
    if (snapshot) {
        const ids = snapshot.format === 'CAROUSEL' ? (snapshot.carousel || []).map(card => card.media_library_id)
            : snapshot.format === 'SINGLE' ? [snapshot.media_library_id] : [];
        if (ids.some(id => !/^m-[a-f0-9]{24}$/.test(String(id || ''))))
            throw new Error('В наборе нет полного списка сохранённых медиа.');
        if (ids.length) {
            const result = await apiJson('ajax/metaBundleMedia.php?ids=' + encodeURIComponent(JSON.stringify(ids)));
            if (generation !== remaskBundleRestoreGeneration)
                throw new Error('Выбор медиа изменён во время загрузки набора.');
            if (!Array.isArray(result.media) || result.media.length !== ids.length)
                throw new Error('Медиа набора удалено или недоступно. Выберите файл заново.');
            for (const expected of snapshot.checksums || []) {
                if (!result.media.some(row => row.id === expected.id && row.sha256 === expected.sha256))
                    throw new Error('Контрольная сумма медиа набора изменилась.');
            }
            remaskSelectedCreativePreset = {...snapshot, meta_builder: data.meta_builder || {},
                carousel: (snapshot.carousel || []).map(card => ({...card,
                    media:result.media.find(row => row.id === card.media_library_id)})),
                media: result.media[0], missing_media: false};
        } else if (snapshot.format === 'INSTAGRAM_POST') {
            remaskSelectedCreativePreset = {...snapshot, meta_builder: data.meta_builder || {}};
        }
        remaskBundleMedia = snapshot;
        if ($('creativeLibrarySelect')) $('creativeLibrarySelect').value = snapshot.id || '';
        renderCreativeLibraryStatus();
        renderCreativeFormat();
    }
    if (!snapshot && data?.workspace_media) {
        state.pendingWorkspaceMedia = {...data.workspace_media};
        if (typeof renderExistingMediaSelection === 'function') renderExistingMediaSelection();
    }
    invalidateLaunchReview();
    validateReady();
}

function remaskPrivateInstallBudgetPeriod() {
    const level = $('budgetLevel');
    if (!level || $('budgetPeriod')) return;
    const field = document.createElement('div');
    field.className = level.parentElement.className;
    const label = document.createElement('label');
    label.htmlFor = 'budgetPeriod'; label.textContent = 'Период бюджета';
    const select = document.createElement('select');
    select.id = 'budgetPeriod'; select.className = 'form-control';
    select.appendChild(new Option('В день', 'DAILY'));
    select.appendChild(new Option('На весь срок', 'LIFETIME'));
    field.append(label, select);
    level.parentElement.after(field);
    select.addEventListener('change', () => { remaskPrivateBudgetLabel(); invalidateLaunchReview(); validateReady(); });
}
function remaskPrivateBudgetLabel() {
    const label = $('dailyBudget')?.labels?.[0];
    if (label) label.textContent = $('budgetPeriod')?.value === 'LIFETIME' ? 'Бюджет на весь срок' : 'Дневной бюджет';
}
remaskPrivateInstallBudgetPeriod();

const remaskPrivateOriginalBuildPayload = buildPayload;
buildPayload = function() {
    const payload = remaskPrivateOriginalBuildPayload();
    if ($('budgetPeriod')?.value === 'LIFETIME') {
        for (const section of ['campaign','adset']) {
            if (Object.hasOwn(payload[section], 'daily_budget')) {
                payload[section].lifetime_budget = payload[section].daily_budget;
                delete payload[section].daily_budget;
            }
        }
    }
    return payload;
};

const remaskPrivateOriginalApplyPreset = applyCreativeLibrarySelection;
const remaskPrivateOriginalValidatePayload = validatePayloadClient;
validatePayloadClient = function(payload, accountIds) {
    remaskPrivateOriginalValidatePayload(payload, accountIds);
    if ((payload.campaign?.lifetime_budget || payload.adset?.lifetime_budget) && !payload.adset?.end_time)
        throw new Error('Для бюджета на весь срок укажите окончание показа.');
};
applyCreativeLibrarySelection = function() {
    remaskBundleMedia = null;
    remaskBundleRestoreGeneration++;
    remaskPrivateOriginalApplyPreset();
    const builder = remaskSelectedCreativePreset?.meta_builder || {};
    const lifetime = builder.campaign?.lifetime_budget || builder.adset?.lifetime_budget;
    if ($('budgetPeriod')) $('budgetPeriod').value = lifetime ? 'LIFETIME' : 'DAILY';
    if (lifetime) {
        $('budgetLevel').value = builder.campaign?.lifetime_budget ? 'CAMPAIGN' : 'ADSET';
        $('dailyBudget').value = String(lifetime);
    }
    remaskPrivateBudgetLabel();
    invalidateLaunchReview(); validateReady();
};

// A changed primary control, including a cleared selection, is authoritative.
const remaskPrivateOriginalBuilder = remaskApplySavedMetaBuilderToPayload;
remaskApplySavedMetaBuilderToPayload = function(payload) {
    const preset = remaskSelectedCreativePreset;
    if (!preset?.meta_builder) return remaskPrivateOriginalBuilder(payload);
    const saved = preset.meta_builder;
    const filtered = JSON.parse(JSON.stringify(saved));
    for (const [group, fields] of Object.entries(remaskLaunchCoveredFields)) {
        if (!filtered[group]) continue;
        for (const field of fields) {
            // Covered fields with no corresponding primary Launch editor retain
            // their preset value; the actual form can still override them.
            const edited = {
                campaign: ['name','objective','special_ad_categories','daily_budget','lifetime_budget'],
                adset: ['name','optimization_goal','billing_event','bid_strategy','bid_amount','destination_type',
                    'daily_budget','lifetime_budget','start_time','end_time'],
                targeting: ['age_min','age_max','locales','geo_locations','interests','behaviors',
                    'flexible_spec','custom_audiences','excluded_custom_audiences','publisher_platforms'],
                creative: [], ad: ['name']
            }[group] || [];
            if (edited.includes(field)) delete filtered[group][field];
        }
    }
    // These primary audience controls are not all in the legacy covered list.
    for (const key of ['custom_audiences','excluded_custom_audiences']) delete filtered.targeting?.[key];
    for (const key of ['pixel_id','custom_event_type']) delete filtered.adset?.promoted_object?.[key];
    if (filtered.adset?.promoted_object && !Object.keys(filtered.adset.promoted_object).length)
        delete filtered.adset.promoted_object;
    for (const key of ['page_id','instagram_actor_id']) delete filtered.identity?.[key];
    preset.meta_builder = filtered;
    try { return remaskPrivateOriginalBuilder(payload); }
    finally { preset.meta_builder = saved; }
};

// Restored local media belongs to this form, not to a later unrelated upload.
$('media')?.addEventListener('change', () => {
    if ($('media').files.length) { remaskBundleMedia = null; remaskBundleRestoreGeneration++; }
});
$('carouselMedia')?.addEventListener('change', () => {
    if (!$('carouselMedia').files.length) return;
    remaskBundleMedia = null;
    clearCreativeLibrarySelection();
    clearPerAccountExistingMediaForLibrary();
    renderCarouselCards();
});
$('carouselCards')?.addEventListener('input', () => { invalidateLaunchReview(); validateReady(); });
$('creativeFormat')?.addEventListener('change', () => {
    remaskBundleMedia = null; remaskBundleRestoreGeneration++;
});
