/* REMASK_PRIVATE_CREATIVE_LAUNCH_COMPLETION_V1 */
// Unbound templates may use the reference catalog. A selected RK uses its
// exact private scope; cached options are suggestions, never live permission proof.
function remaskPrivateTargetingScope() {
    const accounts=selectedAccountIds().map(value=>String(value).replace(/^act_/,''));
    if(accounts.length!==1)return null;
    const account=accounts[0];
    const targets=targetMode() ? state.targets.filter(row=>String(row.account_id).replace(/^act_/,'')===account) : [];
    const profile=targetMode() ? (targets.length===1 ? targets[0].profile : '') : state.profile;
    if(!profile)throw new Error('TARGETING_PROFILE_SCOPE_REQUIRED');
    return {profile:String(profile),account_id:account,execution_mode:$('remaskPrivateExecutionMode')?.value||'DRAFT'};
}
const remaskPrivateOriginalTargetingApiJson = apiJson;
apiJson = async function(url, options = {}) {
    if (['ajax/metaTargetingSearch.php','ajax/metaTemplateTargetingSearch.php'].some(path=>String(url).split('?')[0].endsWith(path))
            && (typeof options.body === 'string' || typeof options.body?.get === 'function')) {
        const input = typeof options.body === 'string' ? new URLSearchParams(options.body) : options.body;
        const body = new URLSearchParams();
        for (const key of ['type','q','limit','offset','force','cache_only']) {
            const value = input.get(key);
            if (value !== null) body.set(key, value);
        }
        const type=String(input.get('type')||'').toLowerCase();
        const resource={location:'locations',locations:'locations',interest:'interests',interests:'interests',
            behavior:'behaviors',behaviors:'behaviors',language:'locales',languages:'locales',locale:'locales',locales:'locales'}[type]
            || (typeof remaskModuleControls!=='undefined' && remaskModuleControls.isTargetingType(type) ? type : null);
        const scope=resource ? remaskPrivateTargetingScope() : null;
        if(scope){
            // One cache-only request checks static GEO/language references
            // and the shared suggestion catalog. It cannot open a profile;
            // cache entries never authorize the later draft POST.
            if(input.get('force')!=='1'){
                const cacheBody=new URLSearchParams();
                for(const key of ['type','q','limit','offset']){
                    const value=input.get(key);if(value!==null)cacheBody.set(key,value);
                }
                cacheBody.set('cache_only','1');
                try{
                    const cached=await remaskPrivateOriginalTargetingApiJson(
                        'ajax/metaTemplateTargetingSearch.php',{...options,body:cacheBody.toString()});
                    const current=remaskPrivateTargetingScope();
                    if(JSON.stringify(current)!==JSON.stringify(scope))throw new Error('TARGETING_SELECTION_CHANGED');
                    const moduleSuggestion=cached?.checked_live===false
                        && cached?.source==='private_meta_module_reference'
                        && cached?.template_account_bound===false
                        && Array.isArray(cached?.data)
                        && (cached.data.length>0 || resource==='locales');
                    if(moduleSuggestion)return cached;
                    const usable=cached?.checked_live===false
                        && cached?.source==='private_meta_template_reference'
                        && cached?.template_account_bound===false
                        && Array.isArray(cached?.data) && cached.data.length>0
                        && cached?.availability!=='NOT_LOADED';
                    if(usable)return {...cached,profile:scope.profile,account_id:scope.account_id,
                        resource,checked_live:false,account_scope_verified:false,
                        _cache:{...(cached._cache||{}),hit:true,cached_only:true,shared_reference:true}};
                }catch(error){
                    if(error?.message==='TARGETING_SELECTION_CHANGED' || options.signal?.aborted)throw error;
                    // Cache failure must not change the established exact-account fallback.
                }
            }
            const scopeKey=JSON.stringify([scope.profile,scope.account_id]);
            const blockedScopes=globalThis.remaskMetaStoppedScopes instanceof Set
                ? globalThis.remaskMetaStoppedScopes : (globalThis.remaskMetaStoppedScopes=new Set());
            if(blockedScopes.has(scopeKey))throw new Error('TARGETING_META_ACCESS_STOPPED');
            for(const [key,value] of Object.entries(scope))body.set(key,value);
            body.set('type',resource);
            let result;
            try {
                result=await remaskPrivateOriginalTargetingApiJson('ajax/metaTargetingSearch.php',{...options,body:body.toString()});
            } catch(error) {
                const failure=String(error?.meta?.code||error?.code||error?.payload?.error?.code||
                    error?.payload?.code||error?.message||'').toUpperCase();
                if(/CHECKPOINT|TWO_FACTOR_REQUIRED|SESSION_EXPIRED|BUSINESS_LOGIN_GATE|LOGIN_REQUIRED|PROFILE_META_BLOCKED/.test(failure))
                    blockedScopes.add(scopeKey);
                throw error;
            }
            const current=remaskPrivateTargetingScope();
            if(JSON.stringify(current)!==JSON.stringify(scope))throw new Error('TARGETING_SELECTION_CHANGED');
            const liveRead=result?.checked_live===true && result?.account_scope_verified===true;
            const cachedSuggestion=result?.checked_live===false && result?.account_scope_verified===false
                && result?._cache?.hit===true && result?._cache?.cached_only===true
                && result?.availability!=='NOT_LOADED';
            if((!liveRead && !cachedSuggestion)
                    || String(result.profile)!==scope.profile || String(result.account_id)!==scope.account_id
                    || result.resource!==resource)throw new Error('TARGETING_SOURCE_SCOPE_UNVERIFIED');
            return result;
        }
        return remaskPrivateOriginalTargetingApiJson('ajax/metaTemplateTargetingSearch.php', {...options, body:body.toString()});
    }
    return remaskPrivateOriginalTargetingApiJson(url, options);
};

// Replace internal resource codes with a short, actionable audience message.
function remaskPrivateAudienceStatus(){
    const status=$('customAudienceStatus');
    if(!status)return;
    const raw=String(status.textContent||'').trim();
    if(!/^Custom Audience discovery failed:/i.test(raw))return;
    const code=raw.slice(raw.indexOf(':')+1).trim().toUpperCase();
    status.textContent=/SESSION|CHECKPOINT|LOGIN|TWO_FACTOR/.test(code)
        ? 'Обновите Facebook-сессию, чтобы загрузить свои аудитории.'
        : 'Meta не вернула список собственных аудиторий для выбранного РК. Это поле необязательное; остальные настройки сохранены.';
}
const remaskPrivateAudienceStatusRoot=$('singleAudienceBox')||document.body;
if(remaskPrivateAudienceStatusRoot)new MutationObserver(remaskPrivateAudienceStatus)
    .observe(remaskPrivateAudienceStatusRoot,{childList:true,subtree:true,characterData:true});
remaskPrivateAudienceStatus();

// A selected RK needs its own promotable Pages, not only the profile's old
// inventory. Cache misses are refreshed by the private worker automatically.
let remaskPrivatePagesGeneration=0;
loadPages=async function(force=false) {
    const generation=++remaskPrivatePagesGeneration;
    const accounts=selectedAccountIds();
    if (targetMode()) return;
    const profile=state.profile,account=accounts.length===1 ? accounts[0] : '',selection=accounts.join(','),
        initialPage=$('page').value,
        wanted=initialPage || state.pendingBundleAssets.page;
    let data,liveError;
    try {data=await loadCachedAsset('pages',account,force);}
    catch(error) {
        if (generation!==remaskPrivatePagesGeneration || profile!==state.profile
                || selectedAccountIds().join(',')!==selection) return;
        if (!account) throw error;
        liveError=error;
        // A failed permission read must not prevent saving local settings.
        // This profile's cached identities remain explicitly unverified;
        // publishing still requires the independent server-side Page check.
        data=await loadCachedAsset('pages','',false);
        data={...data,data:(data.data||[]).map(row=>({...row,
            ad_account_page_access_verified:false,source:'cached_profile_page'}))};
    }
    if (generation!==remaskPrivatePagesGeneration || profile!==state.profile
            || selectedAccountIds().join(',')!==selection) return;
    // A saved list can already be usable while its exact-account refresh is
    // pending. Keep the operator's newer selection (including an explicit
    // clear), but only if the completed response still contains that Page.
    const selectedPage=$('page').value!==initialPage ? $('page').value : wanted;
    const prior=new Map(state.pages.map(row=>[String(row.id),row]));
    state.pages=(data.data || []).map(row=>({...prior.get(String(row.id)),...row}));
    $('page').replaceChildren(new Option('Выберите страницу',''));
    for (const row of state.pages) $('page').appendChild(new Option(`${row.name || row.id} — ${row.id}`+
        (liveError?' · сохранена, доступ не проверен':''),String(row.id)));
    $('page').disabled=!state.pages.length;
    if (selectedPage && hasOptionValue($('page'),String(selectedPage))) $('page').value=String(selectedPage);
    refreshInstagram(); invalidateLaunchReview(); validateReady();
    if (liveError) throw liveError;
};
/* REMASK_SINGLE_PROFILE_PER_RK_PAGES_V1 */
let remaskSingleProfileRkBindingsTimer = null;
let remaskSingleProfileRkBindingsSignature = '';

function remaskPageStatus() {
    let status=$('remaskPageReadStatus');
    if (!status) {
        status=document.createElement('p'); status.id='remaskPageReadStatus';
        status.className='muted'; status.setAttribute('role','status'); $('page')?.after(status);
    }
    return status;
}

function remaskSyncSingleProfileRkMode() {
    const profiles=typeof selectedProfileNames==='function'
        ? selectedProfileNames()
        : Array.from($('profile')?.selectedOptions||[]).map(option=>String(option.value||'').trim()).filter(Boolean);
    if (profiles.length!==1) return targetMode();
    const ids=selectedAccountIds();
    const page=$('page'), card=$('targetBindingsCard'), status=remaskPageStatus();

    if (ids.length>1) {
        const targets=ids.map(rawId=>{
            const accountId=String(rawId||'').replace(/^act_/i,'');
            const account=(state.accounts||[]).find(row=>
                String(row?.id||row?.account_id||'').replace(/^act_/i,'')===accountId);
            const prior=typeof targetForAccount==='function' ? targetForAccount(accountId) : null;
            return {
                profile:String(account?._profile||prior?.profile||profiles[0]),
                business_id:String(account?.business_id||account?.business?.id||prior?.business_id||''),
                account_id:accountId
            };
        }).filter(row=>/^\d{5,30}$/.test(row.account_id));

        state.targets=targets;
        state.targetBindings=state.targetBindings||{};
        state.targetBindingAssets=state.targetBindingAssets||{pages:{},pixels:{},audiences:{},images:{},videos:{},creatives:{}};
        if (page) { page.value=''; page.disabled=true; }
        if (card) card.style.display='block';
        status.textContent='FP загружаются отдельно для каждого РК в таблице привязок ниже.';
        renderTargetBindings();
        validateReady();

        const signature=profiles[0]+'|'+targets.map(row=>row.account_id).sort().join(',');
        remaskSingleProfileRkBindingsSignature=signature;
        clearTimeout(remaskSingleProfileRkBindingsTimer);
        remaskSingleProfileRkBindingsTimer=setTimeout(()=>{
            if (signature!==remaskSingleProfileRkBindingsSignature || !targetMode()) return;
            const button=$('loadTargetBindings');
            if (button && !button.disabled) button.click();
        },500);
        return true;
    }

    clearTimeout(remaskSingleProfileRkBindingsTimer);
    remaskSingleProfileRkBindingsSignature='';
    state.targets=[];
    if (page) { page.value=''; page.disabled=ids.length!==1; }
    if (card) card.style.display='none';
    status.textContent='';
    renderTargetBindings();
    validateReady();
    return false;
}

$('adAccount')?.addEventListener('change',()=>{
    const targetModeForSelection=remaskSyncSingleProfileRkMode();
    if (targetModeForSelection || selectedAccountIds().length!==1) {remaskPrivatePagesGeneration++;return;}
    const profile=state.profile,account=selectedAccountIds()[0];
    const status=remaskPageStatus();
    status.textContent='Загружаем страницы выбранного РК…';
    loadPages().then(()=>{
        if(profile===state.profile && selectedAccountIds()[0]===account) status.textContent='';
    }).catch((error)=>{
        if(profile===state.profile && selectedAccountIds()[0]===account)
            status.textContent=remaskPrivatePageReadFailure(error,profile,account);
    });
});

const remaskUnsupportedObjectiveMessage = 'Сохранённая цель недоступна для автозалива. Выберите доступную цель; остальные настройки сохранены.';
let remaskRestoredLaunchObjective = '';
let remaskPendingLaunchRules = null;
const remaskLaunchRuleFields = ['optimizationGoal','billingEvent','bidStrategy','bidAmount','conversionEvent'];
function remaskLaunchRulesFromBuilder(builder) {
    const adset=builder?.adset || {};
    return {optimizationGoal:adset.optimization_goal,billingEvent:adset.billing_event,
        bidStrategy:adset.bid_strategy,bidAmount:adset.bid_amount,
        conversionEvent:adset.promoted_object?.custom_event_type};
}
function remaskRestoreRuleValue(id,value) {
    const select=$(id);
    if (!select || value===undefined || value===null || value==='') return;
    if (select.tagName==='SELECT' && !hasOptionValue(select,String(value))) {
        const option=new Option('Сохранённое значение недоступно: '+value,String(value));
        option.disabled=true; option.dataset.remaskUnsupportedRule='true'; select.appendChild(option);
    }
    select.value=String(value);
}
function remaskLaunchRuleIssue() {
    return remaskLaunchRuleFields.some(id=>$(id)?.selectedOptions?.[0]?.dataset.remaskUnsupportedRule)
        ? 'Сохранённая оптимизация, оплата или стратегия недоступна для этой цели. Выберите доступное значение.' : '';
}
function remaskRestoreLaunchObjective(value) {
    const select = $('objective');
    if (!select) return;
    const saved = String(value || select.value || '');
    remaskRestoredLaunchObjective = saved;
    for (const option of Array.from(select.options)) {
        if (option.dataset.remaskLegacyObjective && option.value !== saved) option.remove();
    }
    if (saved && !hasOptionValue(select, saved)) {
        const legacy = new Option('Сохранённая цель недоступна — выберите новую', saved);
        legacy.disabled = true;
        legacy.dataset.remaskLegacyObjective = 'true';
        select.appendChild(legacy);
    }
    if (saved) select.value = saved;
    let warning = $('remaskUnsupportedObjective');
    if (!warning) {
        warning = document.createElement('div');
        warning.id = 'remaskUnsupportedObjective';
        warning.className = 'alert alert-warning';
        warning.setAttribute('role', 'alert');
        select.after(warning);
    }
    // A preset can load before initializeLaunch's original config request
    // resolves. Missing configuration is not proof of an unsupported goal.
    const loaded = Boolean(state.launchConfig);
    const supported = state.launchConfig?.objectives?.[select.value]?.enabled === true;
    warning.hidden = !loaded || supported;
    warning.textContent = !loaded || supported ? '' : remaskUnsupportedObjectiveMessage;
}

// The first config request starts before this overlay is installed. Observe
// its option replacement as well as later explicit configuration reloads.
if ($('objective')) new MutationObserver(() => {
    remaskRestoreLaunchObjective(remaskRestoredLaunchObjective || $('objective').value);
}).observe($('objective'), {childList:true});

const remaskPrivateOriginalLoadConfig = loadLaunchConfig;
loadLaunchConfig = async function() {
    const saved = $('objective')?.value;
    if (state.launchConfig && !remaskPendingLaunchRules)
        remaskPendingLaunchRules=Object.fromEntries(remaskLaunchRuleFields.map(id=>[id,$(id)?.value]));
    await remaskPrivateOriginalLoadConfig();
    remaskRestoreLaunchObjective(remaskRestoredLaunchObjective || saved);
    invalidateLaunchReview(); validateReady();
};
$('objective')?.addEventListener('change',()=>{remaskPendingLaunchRules=null;},{capture:true});
$('objective')?.addEventListener('change', () => {
    remaskRestoreLaunchObjective($('objective').value);
    invalidateLaunchReview(); validateReady();
});

// initializeLaunch's first request can predate this overlay. Its continuation
// resolves this shared function after the selected preset has installed intent.
const remaskPrivateOriginalObjectiveRules=applyObjectiveRules;
applyObjectiveRules=function(desired={}) {
    const pending=remaskPendingLaunchRules;
    const values=pending ? {...pending,...desired} : desired;
    if (pending && state.launchConfig) remaskRestoreLaunchObjective(remaskRestoredLaunchObjective);
    remaskPrivateOriginalObjectiveRules(values);
    if (pending && state.launchConfig) {
        remaskPendingLaunchRules=null;
        for (const id of remaskLaunchRuleFields) remaskRestoreRuleValue(id,values[id]);
        updateBidControls(); invalidateLaunchReview(); validateReady();
    }
};
for (const id of remaskLaunchRuleFields) for (const event of ['input','change'])
    $(id)?.addEventListener(event,()=>{if (remaskPendingLaunchRules) remaskPendingLaunchRules[id]=$(id).value;});

let remaskBundleMedia = null;
let remaskBundleRestoreGeneration = 0;
const remaskPrivateOriginalClearPreset = clearCreativeLibrarySelection;
clearCreativeLibrarySelection = function() {
    remaskPendingLaunchRules=null;
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
    const data = JSON.parse(JSON.stringify(bundleFormState()));
    const format = creativeFormat();
    const files = format === 'CAROUSEL' ? carouselFiles() :
        format === 'SINGLE' ? Array.from($('media')?.files || []).slice(0, 1) : [];
    const cards = format === 'CAROUSEL' ? JSON.parse(JSON.stringify(carouselCardsPayload())) : [];
    if (files.length) {
        if (format === 'CAROUSEL' && (cards.length !== files.length || files.length < 2 || files.length > 10))
            throw new Error('Для набора нужны все карточки карусели: от 2 до 10.');
        const staged = await Promise.all(files.map(remaskPrivateStageFile));
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
    remaskPendingLaunchRules={...remaskLaunchRulesFromBuilder(data?.meta_builder),...(data?.fields || {})};
    remaskRestoreLaunchObjective(data?.fields?.objective || data?.meta_builder?.campaign?.objective);
    remaskLegacyApplyBundleData(data);
    remaskRestoreLaunchObjective(data?.fields?.objective || data?.meta_builder?.campaign?.objective);
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
            if (!Array.isArray(result.media) || result.media.length !== ids.length ||
                ids.some((id, index) => result.media[index]?.id !== id))
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

// Retain card-specific CTA from the installed saved-Carousel editor.
// The legacy carouselCardsPayload() reads media/headline/description/URL but
// omits CTA, silently replacing every card with the creative-level default.
const remaskOriginalCarouselCardsPayload = carouselCardsPayload;
carouselCardsPayload = function() {
    const cards=remaskOriginalCarouselCardsPayload();
    const rows=Array.from($('carouselCards')?.querySelectorAll('.carousel-card')||[]);
    return cards.map((card,index)=>{
        const control=rows[index]?.querySelector('.carousel-cta');
        if(!control)return card;
        const updated={...card};
        const action=String(control.value||'').trim();
        if(action)updated.call_to_action=action;
        else delete updated.call_to_action;
        return updated;
    });
};

function remaskPrivateCtaControlState(){
    const control=$('cta');if(!control)return;
    const existing=creativeFormat()==='INSTAGRAM_POST';
    control.disabled=existing;
    control.title=existing?'CTA сохраняется в выбранной публикации Instagram.':'';
}
$('creativeFormat')?.addEventListener('change',remaskPrivateCtaControlState);
remaskPrivateCtaControlState();

const remaskPrivateOriginalBuildPayload = buildPayload;
buildPayload = function() {
    if (!currentObjectiveSpec()?.enabled) throw new Error(remaskUnsupportedObjectiveMessage);
    if (remaskLaunchRuleIssue()) throw new Error(remaskLaunchRuleIssue());
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
    if (!currentObjectiveSpec()?.enabled) throw new Error(remaskUnsupportedObjectiveMessage);
    remaskPrivateOriginalValidatePayload(payload, accountIds);
    if ((payload.campaign?.lifetime_budget || payload.adset?.lifetime_budget) && !payload.adset?.end_time)
        throw new Error('Для бюджета на весь срок укажите окончание показа.');
};
applyCreativeLibrarySelection = function() {
    remaskBundleMedia = null;
    remaskBundleRestoreGeneration++;
    remaskPrivateOriginalApplyPreset();
    const builder = remaskSelectedCreativePreset?.meta_builder || {};
    remaskRestoreLaunchObjective(builder.campaign?.objective);
    remaskPendingLaunchRules=remaskLaunchRulesFromBuilder(builder);
    if (state.launchConfig) applyObjectiveRules();
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

// Owned Instagram posts are chosen in Launch, separately for each selected RK.
const remaskPrivateInstagramChoices = new Map();
const remaskPrivateOriginalRenderBindings = renderTargetBindings;
renderTargetBindings = function() {
    remaskPrivateOriginalRenderBindings();
    if (!targetMode() || creativeFormat() !== 'INSTAGRAM_POST') return;
    const rows = Array.from($('targetBindingRows')?.querySelectorAll('tr') || []);
    selectedTargets().forEach((target,index) => {
        const row = rows[index];
        const input = row?.querySelector('input[placeholder="Instagram media ID"]');
        if (!input) return;
        const binding = bindingFor(target.account_id);
        const actor = String(binding.instagram_actor_id || '');
        const key = JSON.stringify([String(target.profile),remaskPrivateAccountId(target.account_id),actor]);
        const select = document.createElement('select'); select.className='form-control';
        select.setAttribute('aria-label','Публикация Instagram для РК '+remaskPrivateAccountId(target.account_id));
        select.appendChild(new Option('Выберите публикацию Instagram',''));
        const fill = () => {
            select.length=1;
            for (const post of remaskPrivateInstagramChoices.get(key) || [])
                select.appendChild(new Option(String(post.caption || post.id).slice(0,100),String(post.id)));
            if (hasOptionValue(select,binding.source_instagram_media_id)) select.value=binding.source_instagram_media_id;
        };
        fill();
        select.addEventListener('change',()=>{
            binding.source_instagram_media_id=select.value; input.value=select.value;
            invalidateLaunchReview(); validateReady();
        });
        const load = document.createElement('button'); load.type='button'; load.className='btn btn-secondary';
        load.textContent='Загрузить публикации Instagram'; load.disabled=!actor;
        const status = document.createElement('div'); status.className='muted'; status.setAttribute('role','status');
        if (!actor) status.textContent='Сначала выберите страницу с подключённым Instagram для этого РК.';
        load.addEventListener('click',async()=>{
            load.disabled=true; status.textContent='Загружаю публикации этого РК…';
            try {
                const account=remaskPrivateAccountId(target.account_id);
                const result=await apiJson('ajax/metaAssetManager.php',formPost({profile:target.profile,
                    account_id:account,resource:'instagram_media',force:'1',limit:'500'}));
                if (!row.isConnected || String(binding.instagram_actor_id || '')!==actor) return;
                if (result.profile!==target.profile || remaskPrivateAccountId(result.account_id)!==account
                    || result.resource!=='instagram_media' || result.checked_live!==true || result.account_scope_verified!==true)
                    throw new Error('Доступ к публикациям этого РК не подтверждён.');
                const posts=(Array.isArray(result.data)?result.data:[]).filter(post=>
                    /^[1-9][0-9]{4,39}$/.test(String(post.id || '')) && String(post.instagram_actor_id || '')===actor);
                remaskPrivateInstagramChoices.set(key,posts); fill();
                status.textContent=posts.length?'Выберите публикацию для этого РК.':'Доступных публикаций для выбранного Instagram нет.';
            } catch (error) { if(row.isConnected) status.textContent='Не удалось загрузить публикации этого РК. Проверьте подключение выбранного профиля в автозаливе.'; }
            finally { load.disabled=false; }
        });
        input.before(select,load,status);
    });
};
