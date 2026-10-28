/* REMASK_PRIVATE_LAUNCH_CATALOG_V1 */
/* REMASK_PRIVATE_LAUNCH_CATALOG_V2 */
function remaskPrivatePageReadFailure(error, profile, accountId) {
    const rawCode=String(error?.code||error?.payload?.code||error?.payload?.error?.code||error?.error?.code||error?.data?.error?.code||'');
    const rawMessage=String(error?.message||error?.payload?.message||error?.payload?.error?.message||error||'');
    const gate=(rawCode+' '+rawMessage).match(/(?:\b|_)(CHECKPOINT_REQUIRED|TWO_FACTOR_REQUIRED|SESSION_EXPIRED|BUSINESS_LOGIN_GATE)\b/)?.[1];
    const profileLabel=String(profile||'?').replace(/[^0-9]/g,'').slice(0,12)||'?';
    const accountLabel=String(accountId||'').replace(/^act_/,'').replace(/[^0-9]/g,'').slice(0,30)||'?';
    const scope=`Профиль ${profileLabel} / РК ${accountLabel}`;
    if(gate==='BUSINESS_LOGIN_GATE')
        return `${scope}: Meta остановила доступ. FP не загружены; новые действия запрещены.`;
    if(gate==='CHECKPOINT_REQUIRED')
        return `${scope}: чекпоинт Meta. Аккаунт остановлен, новые задачи запрещены.`;
    if(gate==='TWO_FACTOR_REQUIRED')
        return `${scope}: Meta запросила подтверждение 2FA. FP не загружены.`;
    if(gate==='SESSION_EXPIRED')
        return `${scope}: Meta не подтвердила вход. FP не загружены.`;
    return `${scope}: не удалось загрузить FP из Meta. Попробуйте позже.`;
}
function remaskFundingState(info) {
    if (!info) return {label:'NOT LOADED',cls:'muted'};
    if (info.error) return {label:'ERROR',cls:'job-failed'};
    const data=info.data||{};
    if (data.funding_verified === false || data.verification_status === 'NOT_CHECKED')
        return {label:'NOT CHECKED',cls:'muted'};
    if (data._cache?.stale) return {label:'STALE',cls:'muted'};
    const accountStatus=data.account_status;
    if (accountStatus === undefined || accountStatus === null || accountStatus === '')
        return {label:'UNKNOWN',cls:'muted'};
    if (Number(accountStatus)!==1) return {label:'ACCOUNT '+accountStatus,cls:'job-failed'};
    if (data.expired_funding_source_details && Object.keys(data.expired_funding_source_details).length)
        return {label:'EXPIRED',cls:'job-failed'};
    if (data.is_prepay_account) {
        const balance=Number(data.balance);
        return Number.isFinite(balance) && balance>0
            ? {label:'PREPAY BALANCE',cls:'muted'}
            : {label:'PREPAY EMPTY',cls:'muted'};
    }
    if (data.funding_source_details?.id || data.funding_source)
        return {label:'SOURCE SAVED',cls:'muted'};
    return {label:'UNKNOWN',cls:'muted'};
}
fundingState=remaskFundingState;
fundingReviewLabel=function(funding) {
    return remaskFundingState(funding ? {data:funding} : null).label;
};
$('preflight').textContent='Загрузить сохранённые РК';
$('syncMeta').textContent='Обновить список';
const remaskFundingButton=$('loadFunding');
if (remaskFundingButton) remaskFundingButton.textContent='Показать состояние оплаты';
const remaskOriginalLoadFunding=loadFunding;
loadFunding=async function(force=false) {
    await remaskOriginalLoadFunding(force);
    $('fundingStatus').textContent='Оплата считается готовой после проверки выбранного РК в Meta.';
};

function remaskPrivateLaunchUserMessage(error) {
    const code=String(error?.code||error?.payload?.code||error?.payload?.error?.code||
        error?.payload?.detail?.code||'').toUpperCase();
    const message=String(error?.message||error?.payload?.message||error?.payload?.error?.message||error||'').trim();
    const raw=(code+' '+message).toUpperCase();
    if(/CHECKPOINT_REQUIRED/.test(raw))
        return 'Чекпоинт Meta: профиль остановлен; новые задачи запрещены';
    if(/TWO_FACTOR_REQUIRED/.test(raw))
        return 'Meta запросила подтверждение входа';
    if(/BUSINESS_LOGIN_GATE|SESSION_EXPIRED/.test(raw))
        return 'Meta не подтвердила вход';
    if(/PAGE_ACCESS_UNVERIFIED|PAGE_UNVERIFIED|PAGE_ACCESS_REQUIRED/.test(raw))
        return 'Meta не подтвердила доступ к выбранной странице';
    if(/PAYMENT_SELECTION_REQUIRED|PAYMENT_UNVERIFIED/.test(raw))
        return 'Не удалось выполнить проверку выбранного режима';
    if(/^[A-Z][A-Z0-9_]{2,}$/.test(message)||/\b(?:PRIVATE_ADS|BUSINESS|SESSION|CHECKPOINT)_[A-Z0-9_]+\b/.test(message))
        return 'Не удалось завершить проверку';
    return message ? message.slice(0,240) : 'Не удалось завершить проверку';
}
const remaskPrivateLaunchNotice='Подготовьте план рекламы. Выберите сохранение черновика или публикацию.';
let remaskPrivateLaunchBusy=false;
const remaskPrivateLaunchPendingKey='remask_private_launch_pending_v1';
// Saving an actual Meta draft is an explicit operation, separate from publishing.
function remaskPrivateExecutionMode(config) {
    // Missing controls or a stale stored bundle never imply publish intent.
    // Publication must be chosen in the visible execution-mode selector.
    const mode=$('remaskPrivateExecutionMode')?.value||'DRAFT';
    if(!['DRAFT','PUBLISH'].includes(mode))throw new Error('Выберите режим создания рекламы.');
    return mode;
}
function remaskPrivateInstallExecutionMode(){
if(typeof document!=='undefined'&&document.createElement){
    const anchor=$('launchButton')||$('reviewLaunch');
    if(anchor?.parentNode&&!$('remaskPrivateExecutionMode')){
        const box=document.createElement('div');box.className='mb-3';box.style.minWidth='0';
        const label=document.createElement('label');label.htmlFor='remaskPrivateExecutionMode';label.textContent='Что создать в Meta';
        const select=document.createElement('select');select.id='remaskPrivateExecutionMode';select.className='form-control';
        select.style.maxWidth='100%';
        for(const [value,text] of [['DRAFT','Черновик в Meta'],['PUBLISH','Опубликовать рекламу']]){
            const option=document.createElement('option');option.value=value;option.textContent=text;select.appendChild(option);
        }
        const note=document.createElement('p');note.id='remaskPrivateExecutionNote';note.className='muted';
        const update=()=>{
            const draft=select.value==='DRAFT';
            note.textContent=draft?'Сохранит кампанию, группу и объявление в черновике Meta. Без публикации и проверки карты.':
                'Публикация требует проверки доступа страницы и оплаты. Начальный статус — PAUSED.';
            if($('launchButton')){
                $('launchButton').textContent=draft?'Сохранить черновик в Meta':'Создать рекламу';
                $('launchButton').setAttribute('aria-label',$('launchButton').textContent);
                $('launchButton').style.whiteSpace='normal';$('launchButton').style.maxWidth='100%';
            }
            if(typeof invalidateLaunchReview==='function')invalidateLaunchReview();
            validateReady();
        };
        select.addEventListener('change',update);box.appendChild(label);box.appendChild(select);box.appendChild(note);
        // Keep this full-width field outside the existing inline action row.
        const card=anchor.closest?.('.launch-card');
        if(card)card.insertBefore(box,card.firstChild);
        else anchor.parentNode.insertBefore(box,anchor);
        update();
    }
}
}
const remaskPrivatePaymentSelections={};
let remaskPrivatePaymentRevision=0;

function remaskPrivatePaymentKey(profile,accountId){
    return String(profile||'').trim()+'|'+remaskPrivateAccountId(accountId);
}
function remaskPrivateSelectedPayment(profile,accountId){
    const row=remaskPrivatePaymentSelections[
        remaskPrivatePaymentKey(profile,accountId)
    ];
    return row&&/^\d{4}$/.test(String(row.last4||''))
        ?{type:String(row.type||''),last4:String(row.last4)}
        :null;
}
function remaskPrivatePaymentSelectorContainer(){
    if(typeof document==='undefined'||!document.createElement)return null;
    let container=$('remaskPrivatePaymentSelectors');
    if(container)return container;
    const anchor=$('fundingStatus')||$('reviewStatus')||$('launchResult');
    if(!anchor?.parentNode)return null;
    container=document.createElement('div');
    container.id='remaskPrivatePaymentSelectors';
    container.className='mt-2';
    anchor.parentNode.insertBefore(container,anchor.nextSibling);
    return container;
}
function remaskPrivateRenderPaymentSelector(row,methods){
    const safe=(Array.isArray(methods)?methods:[]).filter(method=>
        method&&/^\d{4}$/.test(String(method.last4||'')));
    const key=remaskPrivatePaymentKey(row.profile,row.ad_account_id);
    let current=remaskPrivateSelectedPayment(row.profile,row.ad_account_id);
    if(current&&!safe.some(method=>String(method.last4)===current.last4&&
        (!current.type||String(method.type||'').toLowerCase()===current.type.toLowerCase()))){
        delete remaskPrivatePaymentSelections[key];
        remaskPrivatePendingClear();
        current=null;
    }
    const container=remaskPrivatePaymentSelectorContainer();
    if(!container)return;
    let block=Array.from(container.children||[]).find(
        node=>node?.dataset?.paymentKey===key
    );
    if(!safe.length){
        if(block)block.hidden=true;
        return;
    }
    if(!block){
        block=document.createElement('div');
        block.dataset.paymentKey=key;
        block.className='mb-2';
        const label=document.createElement('label');
        label.textContent='РК '+row.ad_account_id+' · карта для рекламы';
        const select=document.createElement('select');
        select.className='form-select';
        select.dataset.paymentSelect='1';
        block.appendChild(label);
        block.appendChild(select);
        container.appendChild(block);
        select.addEventListener('change',()=>{
            remaskPrivatePaymentRevision++;
            const option=select.options[select.selectedIndex];
            if(!option||!option.dataset.last4){
                delete remaskPrivatePaymentSelections[key];
            }else{
                remaskPrivatePaymentSelections[key]={
                    type:String(option.dataset.type||''),
                    last4:String(option.dataset.last4||'')
                };
            }
            remaskPrivatePendingClear();
            if(typeof validateReady==='function')validateReady();
        });
    }
    block.hidden=false;
    const select=Array.from(block.children||[]).find(
        node=>node?.dataset?.paymentSelect==='1'
    );
    if(!select)return;
    select.textContent='';
    const placeholder=document.createElement('option');
    placeholder.value='';
    placeholder.textContent='Выберите карту';
    select.appendChild(placeholder);
    safe.forEach((method,index)=>{
        const option=document.createElement('option');
        option.value=String(index+1);
        option.dataset.type=String(method.type||'');
        option.dataset.last4=String(method.last4||'');
        option.textContent=(method.type?String(method.type)+' ':'')+
            '•••• '+String(method.last4);
        if(
            current
            && current.last4===String(method.last4)
            && (!current.type||current.type.toLowerCase()===String(method.type||'').toLowerCase())
        ) option.selected=true;
        select.appendChild(option);
    });
}


function remaskPrivateAccountId(value) {
    const id=String(value||'').replace(/^act_/i,'').trim();
    return /^\d{5,30}$/.test(id)?id:'';
}
function remaskPrivateTargetRows(config) {
    const executionMode=remaskPrivateExecutionMode(config);
    if(executionMode==='DRAFT'){
        const requested=config.payload?.delivery||{};
        if($('launchDeliverySelect')?.value==='ACTIVE'||Object.values(requested).some(value=>value!=='PAUSED'))
            throw new Error('Для черновика выберите статус «На паузе».');
    }
    if(Array.isArray(config?._targets)){
        // A precomputed target snapshot must not bypass the visible choice.
        // Reject stale or hidden publication intent rather than silently
        // reinterpreting a previously reviewed task.
        const rows=JSON.parse(JSON.stringify(config._targets));
        for(const row of rows){
            const launch=row?.launch;
            const mode=launch?.override?.execution_mode||
                launch?.base?.execution_mode||launch?.execution_mode;
            if(mode!==executionMode)
                throw new Error('Режим создания изменился. Повторите проверку текущего плана.');
        }
        return rows;
    }
    const ids=Array.isArray(config?.accountIds)?config.accountIds:
        (typeof selectedAccountIds==='function'?selectedAccountIds():[]);
    // Direct single-RK Launch and workspace/bulk Launch have different Page
    // controls. bindingFor() creates an empty bulk row even in direct mode.
    // Match buildPayload(): never borrow a hidden selector across modes.
    const perTarget=typeof targetMode==='function'?targetMode():
        (ids.length>1||(typeof state!=='undefined'&&Array.isArray(state.targets)&&state.targets.length>0));
    const overrides=config?.accountOverrides&&typeof config.accountOverrides==='object'
        ?config.accountOverrides:{};
    return ids.map(rawId=>{
        const accountId=remaskPrivateAccountId(rawId);
        if(!accountId)throw new Error('Некорректный номер РК.');
        const target=typeof targetForAccount==='function'
            ?(targetForAccount(accountId)||targetForAccount('act_'+accountId)||null):null;
        const option=$('adAccount')?.options
            ?Array.from($('adAccount').options).find(o=>remaskPrivateAccountId(o.value)===accountId):null;
        const account=(typeof state!=='undefined'&&Array.isArray(state.accounts))
            ?state.accounts.find(row=>remaskPrivateAccountId(row?.id||row?.account_id)===accountId):null;
        const binding=typeof bindingFor==='function'
            ?bindingFor(accountId)
            :((typeof state!=='undefined'&&state.targetBindings)
                ?(state.targetBindings[accountId]||state.targetBindings['act_'+accountId]||{}):{});
        const profile=String(target?.profile||option?.dataset?.profile||account?._profile||
            (typeof state!=='undefined'?state.profile:'')||'').trim();
        const businessId=String(target?.business_id||account?.business_id||account?.business?.id||'').trim();
        const pageId=String((perTarget?binding?.page_id:$('page')?.value)||'').trim();
        if(!profile)throw new Error('Для РК не выбран профиль Facebook: '+accountId+'.');
        if(!/^\d{5,30}$/.test(businessId))throw new Error('Для РК не указан BM: '+accountId+'.');
        if(!/^\d{5,30}$/.test(pageId))throw new Error('Выберите страницу для РК '+accountId+'.');
        const selectedPaymentMethod=executionMode==='DRAFT'?null:remaskPrivateSelectedPayment(
            profile,accountId
        );
        const override=JSON.parse(JSON.stringify(overrides[accountId]||overrides['act_'+accountId]||{}));
        // Operation mode is a form-level choice, never a stale per-RK override.
        delete override.execution_mode;
        const base={...(config.payload||{})};delete base.execution_mode;
        base.execution_mode=executionMode;
        if(config.payload?.creative?.page_id||override.creative?.page_id)
            override.creative={...(override.creative||{}),page_id:pageId};
        if(config.payload?.adset?.promoted_object?.page_id||override.adset?.promoted_object?.page_id)
            override.adset={...(override.adset||{}),promoted_object:{...(override.adset?.promoted_object||{}),page_id:pageId}};
        if(config.payload?.creative?.format==='INSTAGRAM_POST'){
            const mediaId=String((perTarget?binding?.source_instagram_media_id:
                config.payload.creative.source_instagram_media_id)||'').trim();
            if(!/^\d{5,40}$/.test(mediaId))throw new Error('Выберите публикацию Instagram для РК '+accountId+'.');
            override.creative={...(override.creative||{}),source_instagram_media_id:mediaId};
        }
        return {
            profile,
            business_id:businessId,
            ad_account_id:accountId,
            page_id:pageId,
            ...(selectedPaymentMethod
                ?{selected_payment_method:selectedPaymentMethod}
                :{}),
            launch:{
                base,
                override
            }
        };
    });
}
function remaskPrivateStructuralReadiness() {
    try {
        if (typeof currentLaunchConfigForRequest!=='function')
            return {ready:false,reason:'Проверка формы ещё загружается.'};
        const config=currentLaunchConfigForRequest();
        if (!remaskPrivateTargetRows(config).length)
            return {ready:false,reason:'Нет выбранных рекламных аккаунтов с подтверждёнными настройками.'};
        return {ready:true,reason:''};
    } catch (error) {
        // Keep the existing disabled-button safety gate. Explain which local
        // validation rejected the form instead of silently discarding it.
        const reason=String(error?.message||'Заполните обязательные настройки.')
            .replace(/[\r\n]+/g,' ').slice(0,300);
        return {ready:false,reason};
    }
}
function remaskPrivateStructuralReady() {
    return remaskPrivateStructuralReadiness().ready;
}
function remaskPrivateCsrf() {
    const token=document.querySelector('meta[name="remask-csrf"]')?.content||'';
    if(!token)throw new Error('Обновите страницу: сессия запуска требует обновления.');
    return token;
}
async function remaskPrivateWorker(payload) {
    const response=await fetch('ajax/pythonWorkerJobs.php',{
        method:'POST',
        credentials:'same-origin',
        headers:{'Accept':'application/json','Content-Type':'application/json','X-ReMask-CSRF':remaskPrivateCsrf()},
        body:JSON.stringify(payload)
    });
    let data={};
    try{data=await response.json();}catch(_){}
    if(!response.ok||data?.ok===false){
        const detail=data?.message||data?.error||data?.detail||('HTTP '+response.status);
        const error=new Error(typeof detail==='string'?detail:JSON.stringify(detail));
        const code=data?.code||data?.error?.code||data?.detail?.code;
        if(typeof code==='string'&&/^[A-Z0-9_]{1,100}$/.test(code))error.code=code;
        error.httpStatus=response.status;
        throw error;
    }
    return data;
}
function remaskPrivateHasManagedMedia(value) {
    if(!value||typeof value!=='object')return false;
    if(!Array.isArray(value)){
        if(typeof value.media_library_id==='string'&&value.media_library_id.trim())return true;
        if(Array.isArray(value.carousel_media_library_ids)&&value.carousel_media_library_ids.some(id=>typeof id==='string'&&id.trim()))return true;
        if(Array.isArray(value.staged_media)&&value.staged_media.length)return true;
    }
    return Object.values(value).some(remaskPrivateHasManagedMedia);
}
async function remaskPrivateContractStatus({render=true,config=null}={}) {
    const response=await remaskPrivateWorker({action:'private_launch_contracts'});
    const contracts=response?.contracts||{};
    const order=['CAMPAIGN','ADSET','CREATIVE','AD'];
    const rows=order.map(step=>{
        const row=contracts[step]||{};
        return {
            step,
            configured:row.configured===true,
            active:row.active===true,
            verification_configured:row.verification_configured===true,
            code:String(row.code||''),
            message:String(row.message||''),
            friendly_name:String(row.friendly_name||'')
        };
    });
    const missing=rows.filter(row=>!row.configured||!row.active||!row.verification_configured);
    if(config){
        const format=config.payload?.creative?.format||'SINGLE';
        if(Array.isArray(response.creative_formats)&&!response.creative_formats.includes(format))
            missing.push({step:'CREATIVE_FORMAT',configured:false});
        for(const target of remaskPrivateTargetRows(config)){
            const delivery=target.launch.override?.delivery||target.launch.base?.delivery||{};
            for(const section of ['campaign','adset','ad']){
                const requested=delivery[section]||target.launch.override?.[section]?.status||target.launch.base?.[section]?.status||'PAUSED';
                const key='ACTIVATE_'+section.toUpperCase();
                if(requested==='ACTIVE'&&response.operations?.[key]?.active!==true&&!missing.some(row=>row.step===key))
                    missing.push({step:key,configured:false});
            }
        }
    }
    if(render){
        remaskPrivateShow(
            missing.length?'Создание рекламы пока недоступно: серверный модуль публикации не готов.':
                'Модуль публикации готов. Проверяю настройки рекламы…',
            missing.length?'failed':'ready'
        );
    }
    return {rows,missing,ready:missing.length===0};
}

const remaskPrivateStagedFiles=new WeakMap();
async function remaskPrivateStageFile(file) {
    if(remaskPrivateStagedFiles.has(file))return remaskPrivateStagedFiles.get(file);
    const pending=(async()=>{
        const form=new FormData();
        form.append('action','private_launch_media');
        form.append('media',file,file.name);
        const response=await fetch('ajax/pythonWorkerJobs.php',{
            method:'POST',credentials:'same-origin',headers:{'X-ReMask-CSRF':remaskPrivateCsrf()},body:form
        });
        const data=await response.json();
        if(!response.ok||data.ok!==true||!/^m-[a-f0-9]{24}$/.test(data.media?.id||'')
            ||!/^[a-f0-9]{64}$/.test(data.media?.sha256||''))
            throw new Error(data.error||'Не удалось сохранить медиа.');
        return data.media;
    })();
    remaskPrivateStagedFiles.set(file,pending);
    try{return await pending;}catch(error){remaskPrivateStagedFiles.delete(file);throw error;}
}
const remaskPrivateFormSnapshots=new WeakMap();
function remaskPrivateInputSnapshot() {
    const config=JSON.parse(JSON.stringify(currentLaunchConfigForRequest()));
    const format=typeof creativeFormat==='function'?creativeFormat():'SINGLE';
    const preset=typeof remaskSelectedCreativePreset!=='undefined'?remaskSelectedCreativePreset:null;
    const selected=typeof state!=='undefined'?state.pendingWorkspaceMedia:null;
    const files=format==='CAROUSEL'&&typeof carouselFiles==='function'?Array.from(carouselFiles()):
        (format==='SINGLE'?Array.from($('media')?.files||[]).slice(0,1):[]);
    const cards=format==='CAROUSEL'&&typeof carouselCardsPayload==='function'?carouselCardsPayload():[];
    const delivery=$('launchDeliverySelect')?.value;
    const targets=remaskPrivateTargetRows(config);
    // A review may discover the sole payment method. Only an explicit user
    // change invalidates that discovery; account/page/override changes always do.
    // Restored bundle media is authoritative even when no preset is active.
    // A change while Review awaits must invalidate the pending job snapshot.
    const restoredMedia=typeof remaskBundleMedia==='undefined'?null:remaskBundleMedia;
    const signature=JSON.stringify({config,format,preset,selected,restoredMedia,cards,delivery,
        targets:targets.map(({selected_payment_method,...row})=>row),
        paymentRevision:remaskPrivatePaymentRevision});
    return {config,format,preset,selected,files,cards,delivery,targets,signature};
}
function remaskPrivateAssertCurrentConfig(config) {
    const previous=remaskPrivateFormSnapshots.get(config);
    let current;
    try{current=remaskPrivateInputSnapshot();}catch(_){}
    if(!previous||!current||previous.signature!==current.signature||
        previous.files.length!==current.files.length||
        previous.files.some((file,index)=>file!==current.files[index]))
        throw new Error('Настройки изменились во время подготовки. Повторите проверку текущего плана.');
}
async function remaskPrivatePrepareConfig() {
    // Snapshot before awaiting: Review and Job must describe the same form.
    const snapshot=remaskPrivateInputSnapshot();
    const {config,format,preset,selected,files,cards,delivery}=snapshot;
    const executionMode=remaskPrivateExecutionMode(config);
    delete config.payload.execution_mode;
    config.payload.execution_mode=executionMode;
    // This is only a legacy PHP/Graph marker; private Launch accepts Meta sections only.
    // REMASK_PRIVATE_LAUNCH_META_MARKER_V1
    delete config.payload._meta_official_v1;
    const creative=config.payload.creative||(config.payload.creative={});
    // PHP's associative JSON decode/encode turns an empty {} into []. These
    // optional maps contain no settings; omit them before the worker bridge
    // rather than relaxing Python's managed creative-field validation.
    for(const row of [creative,...Object.values(config.accountOverrides||{}).map(value=>value?.creative)]){
        const options=row?.official_params;
        if(options&&typeof options==='object'&&!Array.isArray(options)&&!Object.keys(options).length)
            delete row.official_params;
    }
    creative.format=format;
    if(delivery==='ACTIVE'||delivery==='PAUSED')config.payload.delivery={campaign:delivery,adset:delivery,ad:delivery};
    // Saved bundles retain Media Library selections even without an active
    // Creative preset. Review and Job must use those same restored identities.
    const restoredMedia=!files.length && typeof remaskBundleMedia!=='undefined'
        && remaskBundleMedia?.format===format ? remaskBundleMedia : null;
    const retainedMedia=!files.length
        ? (preset?.format===format ? preset : restoredMedia) : null;
    if(format==='CAROUSEL'){
        const saved=Array.isArray(retainedMedia?.carousel)?retainedMedia.carousel:[];
        if(cards.length<2||cards.length>10||cards.length!==(saved.length||files.length))
            throw new Error('Карусель требует от 2 до 10 подготовленных карточек.');
        creative.carousel_cards=JSON.parse(JSON.stringify(cards));
        if(saved.length)creative.carousel_media_library_ids=saved.map(card=>String(card.media_library_id||''));
    }else if(format==='SINGLE'){
        if(!files.length&&retainedMedia?.media_library_id)
            creative.media_library_id=String(retainedMedia.media_library_id);
        if(!files.length&&selected){
            const account=remaskPrivateAccountId(selected.account_id);
            if(account&&config.accountIds.some(id=>remaskPrivateAccountId(id)!==account))
                throw new Error('Выбранное медиа относится к другому РК. Выберите медиа для каждого РК.');
            const key={image_hash:'existing_image_hash',video_id:'existing_video_id',creative_id:'existing_creative_id'}[selected.type];
            if(key)creative[key]=String(selected[selected.type]||'');
        }
    }else if(format==='INSTAGRAM_POST'){
        // Existing post identity includes its CTA; the disabled local control
        // is a preference for other formats, not an override for that post.
        delete creative.call_to_action;
        const mediaId=String($('instagramMediaId')?.value||creative.source_instagram_media_id||'').trim();
        if(mediaId)creative.source_instagram_media_id=mediaId;
    }
    const targets=remaskPrivateTargetRows(config);
    if(files.length){
        delete creative.media_library_id;
        delete creative.carousel_media_library_ids;
        creative.staged_media=await Promise.all(files.map(remaskPrivateStageFile));
    }
    config._targets=targets.map(row=>({...row,launch:{...row.launch,base:config.payload}}));
    remaskPrivateFormSnapshots.set(config,snapshot);
    return config;
}

function remaskPrivateShow(text,kind='') {
    const target=$('launchResult')||$('reviewStatus');
    if(typeof show==='function'&&target)show(target,text,kind);
    else if(target)target.textContent=text;
}
function remaskPrivateNonce() {
    try{
        const values=new Uint32Array(4);crypto.getRandomValues(values);
        return Array.from(values,v=>v.toString(16).padStart(8,'0')).join('');
    }catch(_){
        return String(Date.now())+'-'+String(Math.random()).slice(2);
    }
}
function remaskPrivateFingerprint(rows) {
    return JSON.stringify(rows.map(row=>({
        profile:row.profile,business_id:row.business_id,ad_account_id:row.ad_account_id,
        page_id:row.page_id,
        selected_payment_method:row.selected_payment_method||null,
        launch:row.launch
    })));
}
function remaskPrivateIntentDigest(value) {
    // Stable across reloads and cleared localStorage. The backend compares the
    // full fingerprint before adopting this key, so collisions fail closed.
    const text=String(value);
    let a=0x811c9dc5,b=0x9e3779b9,c=0x85ebca6b,d=0xc2b2ae35;
    for(let i=0;i<text.length;i++){
        const code=text.charCodeAt(i);
        a=Math.imul(a^code,0x01000193)>>>0;
        b=Math.imul(b^code,0x5bd1e995)>>>0;b^=b>>>13;
        c=Math.imul(c+code,0x27d4eb2d)>>>0;c^=c>>>15;
        d=Math.imul(d^(code+i),0x165667b1)>>>0;d^=d>>>16;
    }
    a=Math.imul(a^(a>>>16),0x85ebca6b)>>>0;
    b=Math.imul(b^(b>>>13),0xc2b2ae35)>>>0;
    c=Math.imul(c^(c>>>16),0x85ebca77)>>>0;
    d=Math.imul(d^(d>>>16),0x27d4eb2f)>>>0;
    return [a,b,c,d].map(part=>part.toString(16).padStart(8,'0')).join('');
}
function remaskPrivatePendingRead(fingerprint) {
    try{
        const saved=JSON.parse(localStorage.getItem(remaskPrivateLaunchPendingKey)||'null');
        if(saved&&saved.version===1&&saved.fingerprint===fingerprint&&saved.request)return saved.request;
    }catch(_){}
    return null;
}
function remaskPrivatePendingWrite(fingerprint,request) {
    try{localStorage.setItem(remaskPrivateLaunchPendingKey,JSON.stringify({
        version:1,updated:Date.now(),fingerprint,request
    }));}catch(_){throw new Error('Не удалось сохранить ключ отправки. Освободите место в браузере и повторите.');}
}
function remaskPrivatePendingClear() {
    try{localStorage.removeItem(remaskPrivateLaunchPendingKey);}catch(_){}
}
function remaskPrivateRenderMediaPreflight(results,render=true) {
    // Media is inspected automatically as part of Save Draft. Placement hints
    // are not Meta errors and do not belong in the Launch result panel.
    const box=$('remaskPrivateMediaPreflight');
    if(box){box.replaceChildren();box.hidden=true;}
}

function remaskPrivateRenderReviewResults(results,render=true) {
    const targets=results.map(entry=>({profile_id:entry.row.profile,
        business_id:entry.row.business_id,ad_account_id:entry.row.ad_account_id,
        page_id:entry.row.page_id,status:entry.status,
        error_code:entry.error_code||'',message:entry.error||'',
        checked_live:entry.review?.checked_live===true,
        editor_state:entry.review?.editor_state||'EDITOR_UNVERIFIED',
        missing_fields:Array.isArray(entry.review?.missing_fields)?entry.review.missing_fields:[],
        missing_contracts:Array.isArray(entry.review?.missing_contracts)?entry.review.missing_contracts:[],
        review_attempted:entry.review_attempted!==false}));
    if(typeof state!=='undefined')state.privateLaunchReadiness={admission_policy:'all_required',targets};
    if(!render||typeof document==='undefined'||!document.createElement)return;
    const box=$('remaskPrivateAccountReviews');
    if(box){box.replaceChildren();box.hidden=true;}
    remaskPrivateRenderMediaPreflight(results,render);
}
async function remaskPrivateReviewConfig(config,{render=true,assertCurrent=null}={}) {
    remaskPrivateRenderReviewResults([],render);
    const contractStatus=await remaskPrivateContractStatus({render,config});
    if(assertCurrent)assertCurrent();
    if(!contractStatus.ready){
        const missing=contractStatus.missing.map(row=>row.step).join(', ');
        throw new Error('Модуль публикации не готов ('+missing+').');
    }
    const rows=remaskPrivateTargetRows(config);
    const results=[];
    const authBlockedProfiles=new Map();
    let batchStop=null;
    for(let index=0;index<rows.length;index++){
        if(assertCurrent)assertCurrent();
        const row=rows[index];
        const prior=batchStop||authBlockedProfiles.get(row.profile);
        if(prior){
            results.push({row,review:{ready:false},status:prior.status||'AUTH_REQUIRED',
                error_code:prior.code,error:prior.message,review_attempted:false});
            continue;
        }
        if(render)remaskPrivateShow('Проверка рекламы '+(index+1)+'/'+rows.length+' · РК '+row.ad_account_id+'…');
        let mediaChecks=[];
        let mediaInspectionPending=false;
        try{
            if(remaskPrivateHasManagedMedia(row.launch)){
                mediaInspectionPending=true;
                const inspected=await remaskPrivateWorker({
                    action:'private_launch_media_inspect',
                    launch:row.launch
                });
                if(assertCurrent)assertCurrent();
                mediaChecks=Array.isArray(inspected?.media)?inspected.media:[];
                if(!mediaChecks.length)throw Object.assign(new Error('Сервер не подтвердил выбранные Media Library файлы.'),{code:'MEDIA_PREFLIGHT_EMPTY'});
                mediaInspectionPending=false;
                const incompatible=mediaChecks.filter(item=>item?.compatibility?.locally_valid!==true);
                if(incompatible.length){
                    const first=incompatible[0];
                    const code=String(first?.compatibility?.code||'MEDIA_PREFLIGHT_FAILED');
                    const message=String(first?.compatibility?.message||(code==='MEDIA_PREPARATION_REQUIRED'
                        ?'Подготовьте копию в PNG/JPEG или MP4 H.264/AAC, затем повторите проверку.'
                        :'Локальная проверка медиа не пройдена.'));
                    results.push({row,review:{ready:false,preflight:{local_media:mediaChecks}},
                        status:'MEDIA_BLOCKED',error_code:code,error:message,
                        media_checks:mediaChecks,review_attempted:false});
                    continue;
                }
            }
            const response=await remaskPrivateWorker({
                action:'private_launch_review',
                profile_id:row.profile,
                business_id:row.business_id,
                ad_account_id:row.ad_account_id,
                page_id:row.page_id,
                ...(row.selected_payment_method
                    ?{selected_payment_method:row.selected_payment_method}
                    :{}),
                launch:row.launch
            });
            if(assertCurrent)assertCurrent();
            const review=response?.review||{};
            const preflight=review?.preflight||{};
            if(
                review.ready!==true
                && preflight.payment_selection_required===true
            ){
                remaskPrivateRenderPaymentSelector(
                    row,
                    preflight.payment_methods||[]
                );
                results.push({row,review,status:'WAITING_FOR_SELECTION',
                    error_code:'PAYMENT_SELECTION_REQUIRED',
                    error:'Выберите карту для РК '+row.ad_account_id+' и повторите проверку.',
                    media_checks:mediaChecks});
                continue;
            }
            if(review.ready!==true){
                results.push({row,review,status:'BLOCKED',error_code:'ACCOUNT_NOT_READY',
                    error:'Проверка не подтвердила готовность РК '+row.ad_account_id+'.',
                    media_checks:mediaChecks});
                continue;
            }
            const selected=preflight.selected_payment_method;
            if(selected&&/^\d{4}$/.test(String(selected.last4||''))){
                remaskPrivatePaymentSelections[
                    remaskPrivatePaymentKey(row.profile,row.ad_account_id)
                ]={
                    type:String(selected.type||''),
                    last4:String(selected.last4)
                };
                row.selected_payment_method={
                    type:String(selected.type||''),
                    last4:String(selected.last4)
                };
            }
            results.push({row,review,status:'READY',media_checks:mediaChecks});
        }catch(error){
            if(assertCurrent)assertCurrent();
            const message=String(error?.message||error);
            const gate=(String(error?.code||'')+' '+message).match(/(?:\b|_)(CHECKPOINT_REQUIRED|TWO_FACTOR_REQUIRED|SESSION_EXPIRED|BUSINESS_LOGIN_GATE)\b/)?.[1];
            const code=error?.code||gate||(mediaInspectionPending?'MEDIA_PREFLIGHT_FAILED':'REVIEW_FAILED');
            if(gate)authBlockedProfiles.set(row.profile,{code:gate,message});
            if(error?.httpStatus===401||error?.httpStatus===403)
                batchStop={code:'REMASK_AUTH_REQUIRED',message,status:'AUTH_REQUIRED'};
            // A quota failure is not an account-local readiness failure.
            // Stop the original batch rather than reading through other RKs.
            if(error?.httpStatus===429||/(?:^|_)(?:RATE_LIMIT(?:ED)?|THROTTLED)(?:_|$)/.test(code)||
                (/Meta rejected the advertising request/.test(message)&&/\bcode=(?:4|17|32|613)\b/.test(message)))
                batchStop={code:'META_RATE_LIMITED',message,status:'RATE_LIMITED'};
            results.push({row,review:{ready:false},status:batchStop?.status||(gate?'AUTH_REQUIRED':(mediaInspectionPending?'MEDIA_BLOCKED':'BLOCKED')),
                error_code:batchStop?.code||code,error:message,media_checks:mediaChecks,
                review_attempted:mediaInspectionPending?false:true});
        }
    }
    const ready=results.filter(entry=>entry.review?.ready===true).length;
    remaskPrivateRenderReviewResults(results,render);
    if(render)remaskPrivateShow(
        'Настройки проверены: '+ready+'/'+rows.length+' РК готовы.'+
        (ready===rows.length?' После создания будет применён выбранный статус.':
            ' Причины по каждому РК показаны ниже. Задача не создана.'),
        ready===rows.length?'ready':'failed'
    );
    return results;
}
async function remaskPrivateReviewOnly() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const config=await remaskPrivatePrepareConfig();
        remaskPrivateAssertCurrentConfig(config);
        const rows=remaskPrivateTargetRows(config);
        const plans=[];
        for(const row of rows){
            const response=await remaskPrivateWorker({action:'private_launch_plan',profile_id:row.profile,
                business_id:row.business_id,ad_account_id:row.ad_account_id,page_id:row.page_id,launch:row.launch});
            if(response.plan?.configuration_valid!==true||response.plan?.checked_live!==false)
                throw new Error('Сервер не подтвердил подготовку плана.');
            plans.push(response.plan);
        }
        remaskPrivateAssertCurrentConfig(config);
        const incomplete=plans.some(plan=>plan.missing_contracts?.length);
        remaskPrivateShow('План подготовлен для '+plans.length+' РК. Проверка Meta не выполнялась.'+
            (incomplete?' Для запуска ещё требуется настройка серверного модуля публикации.':''));
        return plans;
    }catch(error){
        remaskPrivateShow('Создание рекламы недоступно: '+String(error?.message||error),'failed');
        return null;
    }finally{
        remaskPrivateLaunchBusy=false;validateReady();
    }
}
async function remaskPrivateReviewLive() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const config=await remaskPrivatePrepareConfig();
        const assertCurrent=()=>remaskPrivateAssertCurrentConfig(config);
        assertCurrent();
        remaskPrivateShow('Проверяю готовность выбранных РК в Meta. Рекламные объекты не создаются…');
        const results=await remaskPrivateReviewConfig(config,{render:true,assertCurrent});
        assertCurrent();
        const ready=results.filter(entry=>entry.review?.ready===true).length;
        remaskPrivateShow('Проверка Meta: '+ready+'/'+results.length+' РК готовы к выбранной операции. '+
            'Рекламные объекты не создавались; публикация и редактор Meta не подтверждены.'+
            (ready===results.length?'':' Причины по каждому РК показаны ниже.'),
            ready===results.length?'ready':'failed');
        return results;
    }catch(error){
        remaskPrivateRenderReviewResults([],true);
        remaskPrivateShow('Проверка Meta не завершена: '+String(error?.message||error),'failed');
        return null;
    }finally{
        remaskPrivateLaunchBusy=false;validateReady();
    }
}
function remaskPrivateJobRequest(config,reviewed) {
    const rows=reviewed.map(entry=>entry.row);
    const fingerprint=remaskPrivateFingerprint(rows);
    const pending=remaskPrivatePendingRead(fingerprint);
    if(pending)return {request:pending,fingerprint,reused:true};
    const request={
        action:'create',
        idempotency_key:'private-launch-batch-'+remaskPrivateIntentDigest(fingerprint),
        profiles:rows.map(row=>{
            const rowFingerprint=remaskPrivateFingerprint([row]);
            const key='private-launch-'+row.profile+'-'+row.ad_account_id+'-'+
                remaskPrivateIntentDigest(rowFingerprint);
            return {
                profile_id:row.profile,
                tasks:[{
                    action:'private_launch',
                    idempotency_key:key,
                    payload:{
                        launch_key:key,
                        business_id:row.business_id,
                        ad_account_id:row.ad_account_id,
                        page_id:row.page_id,
                        ...(row.selected_payment_method
                            ?{selected_payment_method:row.selected_payment_method}
                            :{}),
                        launch:row.launch
                    }
                }]
            };
        })
    };
    remaskPrivatePendingWrite(fingerprint,request);
    return {request,fingerprint,reused:false};
}
function remaskPrivateFinalizeJob(job) {
    const status=String(job?.status||'').toUpperCase();
    if(status==='SUCCESS'){
        try{
            const saved=JSON.parse(localStorage.getItem(remaskPrivateLaunchPendingKey)||'null');
            if(saved?.request?.idempotency_key===job.idempotency_key)remaskPrivatePendingClear();
        }catch(_){}
    }
    return status;
}
let remaskPrivatePollGeneration=0;
// The private worker journal is a different schema/store from legacy metaJobs.
// Restore it by its own handle and render retained receipts without claiming
// that accepted fragments or an old SUCCESS are a fresh Meta read.
function remaskPrivateIsJob(job) {
    return /^[a-f0-9]{32}$/.test(String(job?.id||'')) && (job.items||[]).some(item=>
        (item.tasks||[]).some(task=>['private_launch','private_delivery'].includes(task.action)));
}
function remaskPrivateRememberJob(jobId) {
    if(!/^[a-f0-9]{32}$/.test(String(jobId||'')))return;
    try{localStorage.setItem('remask_private_source_job_v1',JSON.stringify({job_id:jobId}));}catch(_){}
    try{
        const url=new URL(window.location.href);
        url.searchParams.set('private_job',jobId);
        window.history.replaceState(null,'',url.toString());
    }catch(_){}
}
function remaskPrivateJobStatusLabel(value) {
    const status=String(value||'').trim().toUpperCase();
    const labels={QUEUED:'В очереди',RUNNING:'В работе',SUCCESS:'Готово',FAILED:'Ошибка',
        PARTIAL:'Выполнено частично',CANCELLED:'Остановлено'};
    return labels[status]||'Неизвестный статус';
}
function remaskPrivateStepLabel(value) {
    const key=String(value||'').trim().toUpperCase();
    const labels={
        'ADS_MANAGER:DRAFT':'Черновик',
        'ADS_MANAGER:FRAGMENT_CAMPAIGN':'Кампания',
        'ADS_MANAGER:FRAGMENT_ADSET':'Группа объявлений',
        'ADS_MANAGER:FRAGMENT_AD_SET':'Группа объявлений',
        'ADS_MANAGER:FRAGMENT_CREATIVE':'Креатив',
        'ADS_MANAGER:FRAGMENT_AD':'Объявление',
        'ADS_MANAGER:PUBLISH':'Публикация',
        PRIVATE_LAUNCH:'Запуск рекламы',
        PREPARE:'Подготовка комплекта'
    };
    return labels[key]||(/^[A-Z0-9_]+(?::[A-Z0-9_]+)*$/.test(key)?'Шаг Meta':String(value||''));
}
function remaskPrivateErrorLabel(code) {
    const key=String(code||'').trim().toUpperCase();
    const labels={
        PRIVATE_ADS_DRAFT_READ_SPEC_MISMATCH:'Настройки черновика в Meta отличаются от сохранённых.',
        PRIVATE_ADS_DRAFT_TRANSPORT_UNAVAILABLE:'Не удалось связаться с Meta. Черновик не создан.',
        PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE:'Meta остановила доступ. Новые действия запрещены.',
        BUSINESS_LOGIN_GATE:'Meta остановила доступ. Новые действия запрещены.',
        PROFILE_META_BLOCKED:'Meta остановила доступ. Новые действия запрещены.',
        PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED:'Чекпоинт Meta: аккаунт остановлен.',
        CHECKPOINT_REQUIRED:'Чекпоинт Meta: аккаунт остановлен.',
        PRIVATE_ADS_DRAFT_RESULT_UNKNOWN:'Meta не подтвердила результат. Перед продолжением нужна сверка.',
        PRIVATE_ADS_DRAFT_INVALID_INPUT:'Проверьте настройки черновика.',
        PRIVATE_LAUNCH_PLAN_CHANGED:'У этой задачи уже сохранены другие настройки. Новый запрос не отправлен.',
        PRIVATE_LAUNCH_EXISTING_INTENT:'Для этого РК уже есть незавершённый черновик. Продолжите сохранённую задачу.',
        PRIVATE_LAUNCH_MULTIPLE_PENDING_INTENTS:'Для этого РК найдены несколько черновиков. Новые объекты не создавались; нужна сверка.'
    };
    if(labels[key])return labels[key];
    if(/LOGIN|SESSION_EXPIRED|AUTHENTICATION/.test(key))return 'Meta запросила вход. Действие остановлено.';
    return 'Не удалось завершить шаг. Подробности доступны ниже.';
}
function remaskPrivateAppendDetails(container,title,body) {
    if(!body)return;
    const details=document.createElement('details');
    details.className='remask-private-technical';
    const heading=document.createElement('summary');heading.textContent=title;
    const pre=document.createElement('pre');pre.textContent=body;
    details.appendChild(heading);details.appendChild(pre);container.appendChild(details);
}
function remaskPrivateShowReadResult(container,message,technical) {
    container.replaceChildren();
    const visible=document.createElement('span');visible.textContent=message;container.appendChild(visible);
    remaskPrivateAppendDetails(container,'Технические подробности',technical);
}
function remaskPrivateRecentJob(job) {
    const select=$('recentJobSelect');
    if(!select||typeof document==='undefined'||!remaskPrivateIsJob(job))return;
    const value='private:'+job.id;
    let entry=Array.from(select.children||[]).find(child=>child.value===value);
    if(!entry){entry=document.createElement('option');entry.value=value;select.appendChild(entry);}
    entry.textContent='Meta · '+job.id+' · '+remaskPrivateJobStatusLabel(job.status);select.value=value;
}
function remaskPrivateRenderJob(job) {
    if(!remaskPrivateIsJob(job)||typeof document==='undefined')return;
    const body=$('jobItems'),summary=$('jobSummary');
    if(!body||!summary)return;
    summary.textContent='Задача '+job.id+' · '+remaskPrivateJobStatusLabel(job.status)+
        ' · сохранённый журнал. Проверка в Meta выполняется отдельно.';
    body.replaceChildren();
    for(const item of job.items||[]){
        for(const task of item.tasks||[]){
            const steps=(item.advertising_steps||[]).filter(step=>step.task_id===task.id);
            const ids=steps.filter(step=>step.entity_id).map(step=>remaskPrivateStepLabel(step.step)+': '+step.entity_id+
                (step.verified===true?' (подтверждено чтением)':' (результат не проверен)'));
            const payload=task.payload||{};
            const errorCode=String(task.error_code||'').trim();
            const errorMessage=String(task.error_message||'').trim();
            const errorLabel=(errorCode||errorMessage)?remaskPrivateErrorLabel(errorCode):'';
            const tr=document.createElement('tr');
            for(const value of [String(item.profile_id||'')+' / '+String(payload.ad_account_id||''),
                remaskPrivateJobStatusLabel(task.status),remaskPrivateStepLabel(steps.at(-1)?.step||task.action),task.attempt||0,'HTTP/2',ids.join('\n'),
                '', '', '', '',errorLabel]){
                const td=document.createElement('td');td.textContent=String(value??'');
                td.style.whiteSpace='pre-wrap';tr.appendChild(td);
            }
            if(errorCode||errorMessage){
                remaskPrivateAppendDetails(tr.children[10],'Технические подробности',
                    [errorCode,errorMessage].filter(Boolean).join('\n'));
            }
            const launch=payload.launch;
            const draftMode=(launch?.base?.execution_mode||launch?.execution_mode)==='DRAFT';
            if(task.action==='private_launch'&&draftMode&&/^[a-f0-9]{32}$/.test(String(task.id||''))&&
                ['SUCCESS','FAILED','PARTIAL'].includes(String(job.status||'').toUpperCase())&&
                steps.some(step=>step.step==='ADS_MANAGER:DRAFT'&&step.entity_id)&&
                !steps.some(step=>step.step==='ADS_MANAGER:PUBLISH')){
                const button=document.createElement('button');button.type='button';
                button.className='btn btn-secondary';button.textContent='Проверить черновик в Meta';
                const result=document.createElement('div');result.className='muted';
                const terminalReadKey='remask_private_meta_terminal_v1:'+String(item.profile_id||'');
                let terminalReadCode='';
                try{terminalReadCode=String(localStorage.getItem(terminalReadKey)||'');}catch(_){}
                let terminalStopped=Boolean(terminalReadCode);
                if(terminalStopped){
                    button.disabled=true;button.textContent='Действия остановлены';
                    result.textContent=remaskPrivateErrorLabel(terminalReadCode);
                }
                button.addEventListener('click',async()=>{
                    if(terminalStopped)return;
                    button.disabled=true;result.textContent='Читаю сохранённый черновик…';
                    try{
                        const response=await remaskPrivateWorker({action:'private_draft_read',
                            profile_id:String(item.profile_id),job_id:job.id,task_id:String(task.id)});
                        const read=response?.read;
                        if(read?.draft_verified!==false||read?.advertising_mutation_posts!==0||read?.persisted!==false)
                            throw new Error('Сервер не подтвердил режим чтения.');
                        const rows=Array.isArray(read.fragment_reads)?read.fragment_reads:[];
                        const matching=rows.filter(row=>row.relationships_match===true&&row.settings_match===true).length;
                        const safeShape=value=>{
                            if(!value||typeof value!=='object')return 'unknown';
                            if(Object.prototype.hasOwnProperty.call(value,'value'))return String(value.value);
                            if(Number.isSafeInteger(value.length))return String(value.type||'value')+':'+value.length;
                            return String(value.type||'unknown');
                        };
                        const checks=rows.map(row=>{
                            const relationChecks=row.checks&&typeof row.checks==='object'?row.checks:{};
                            const relationFailures=Object.entries(relationChecks).filter(([,ok])=>ok!==true).map(([key])=>key);
                            const fields=Array.isArray(row.settings_diagnostic?.fields)?row.settings_diagnostic.fields:[];
                            const settingFailures=fields.filter(field=>field.matches!==true).map(field=>
                                field.field+'['+field.count+'] '+safeShape(field.expected)+' / '+safeShape(field.observed));
                            const parent=row.observed?.parent_ad_object_id;
                            const parentValue=parent?.present===true?safeShape(parent):'absent';
                            return (row.level||'fragment')+': связи '+
                                (relationFailures.length?relationFailures.join(','):'совпали')+
                                '; настройки '+(settingFailures.length?settingFailures.join(','):'совпали')+
                                '; parent '+parentValue;
                        });
                        const pending=read.pending_stage&&typeof read.pending_stage==='object'
                            ?String(read.pending_stage.stage||'unknown')+' ('+String(read.pending_stage.reason||'unknown')+')':'';
                        const technical=checks.join('\n')+(pending?'\nОжидающий этап: '+pending:'')+
                            '\nПолнота черновика и редактор не проверялись.';
                        remaskPrivateShowReadResult(result,
                            'Черновик '+String(read.draft_id||'')+' прочитан. Совпало фрагментов: '+
                                matching+' из '+rows.length+'. Публикация не выполнялась.',
                            technical);
                    }catch(error){
                        const code=String(error?.code||'');
                        const message=String(error?.message||error||'');
                        const label=code?remaskPrivateErrorLabel(code):'Не удалось прочитать черновик Meta.';
                        terminalStopped=[
                            'CHECKPOINT_REQUIRED','PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED',
                            'BUSINESS_LOGIN_GATE','PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE','PROFILE_META_BLOCKED'
                        ].includes(code)||/BUSINESS_LOGIN_GATE/.test(code);
                        if(terminalStopped){
                            try{localStorage.setItem(terminalReadKey,code||'PROFILE_META_BLOCKED');}catch(_){}
                            button.disabled=true;button.textContent='Действия остановлены';
                        }
                        remaskPrivateShowReadResult(result,label,
                            [code,message].filter(Boolean).join('\n'));
                    }finally{if(!terminalStopped)button.disabled=false;}
                });
                tr.children[5].appendChild(button);tr.children[5].appendChild(result);
            }
            body.appendChild(tr);
        }
    }
    // The legacy buttons dispatch to the legacy store. Never send a private
    // worker handle there, or expose publication controls for a DRAFT.
    for(const id of ['retryFailed','forceRetryReview','continueJob','refreshInsights']){
        if($(id))$(id).disabled=true;
    }
    const draftOnly=(job.items||[]).flatMap(item=>item.tasks||[]).every(task=>
        task.action==='private_launch'&&
        (task.payload?.launch?.base?.execution_mode||task.payload?.launch?.execution_mode)==='DRAFT');
    if(draftOnly){
        for(const id of ['activateJobAds','pauseJobAds'])if($(id))$(id).disabled=true;
    }
    if($('jobActionSelect'))$('jobActionSelect').hidden=draftOnly;
    remaskPrivateRecentJob(job);
}
async function remaskPrivatePollJob(jobId) {
    const generation=++remaskPrivatePollGeneration;
    let failures=0;
    for(let attempt=0;attempt<180;attempt++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        if(generation!==remaskPrivatePollGeneration)return null;
        try{
            const data=await remaskPrivateWorker({action:'status',job_id:jobId});
            if(generation!==remaskPrivatePollGeneration)return null;
            const job=data?.job||{};
            if(job.id!==jobId||!remaskPrivateIsJob(job))throw new Error('Worker returned another job.');
            failures=0;
            if(typeof state!=='undefined'){
                state.privateAdvertisingJob=job;
                const tasks=(job.items||[]).flatMap(item=>item.tasks||[]);
                if(tasks.some(task=>task.action==='private_launch')){
                    state.privateAdvertisingSourceJob=job;
                    try{localStorage.setItem('remask_private_source_job_v1',JSON.stringify({job_id:jobId}));}catch(_){}
                }
                const source=state.privateAdvertisingSourceJob;
                if(source){
                    const sourceTasks=(source.items||[]).flatMap(item=>item.tasks||[]);
                    if($('activateJobAds'))$('activateJobAds').disabled=!(source.items||[]).some(item=>(item.tasks||[]).some(task=>remaskPrivateCanDeliver(item,task,'ACTIVE')));
                    if($('pauseJobAds'))$('pauseJobAds').disabled=!(source.items||[]).some(item=>(item.tasks||[]).some(task=>remaskPrivateCanDeliver(item,task,'PAUSED')));
                    if(typeof renderJobActionMenu==='function')renderJobActionMenu();
                }
            }
            remaskPrivateRenderJob(job);
            const done=(job.items||[]).filter(item=>['SUCCESS','FAILED','PARTIAL'].includes(item.status)).length;
            const total=Number(job.items_total||0);
            const advertising=(job.items||[]).flatMap(item=>item.advertising_steps||[]);
            const publishing=advertising.some(step=>step.publication?.code==='PUBLISH_PENDING');
            remaskPrivateShow(
                'Задача '+jobId+' · '+remaskPrivateJobStatusLabel(job.status)+
                (total?' · '+done+'/'+total:'')+
                (publishing?' · Meta обрабатывает публикацию. Retry Failed продолжит ожидание результата.':'')
            );
            if(['SUCCESS','FAILED','PARTIAL'].includes(String(job.status||'').toUpperCase())){
                const draftTasks=(job.items||[]).flatMap(item=>item.tasks||[]).filter(task=>task.action==='private_launch');
                if(String(job.status).toUpperCase()==='SUCCESS'&&draftTasks.length&&draftTasks.every(task=>task.result?.draft_verified===true&&task.result?.published===false))
                    remaskPrivateShow('Данные черновика сохранены в Meta · задача '+jobId+'. Редактор Meta ещё не проверен. Реклама не опубликована.');
                remaskPrivateFinalizeJob(job);
                if(String(job.status).toUpperCase()==='SUCCESS'){
                    try{
                        const saved=JSON.parse(localStorage.getItem('remask_private_delivery_pending_v1')||'null');
                        if(saved?.request?.idempotency_key===job.idempotency_key)localStorage.removeItem('remask_private_delivery_pending_v1');
                    }catch(_){}
                }
                return job;
            }
        }catch(_){
            failures++;
            remaskPrivateShow('Связь с задачей '+jobId+' прервана. Восстанавливаю чтение статуса…');
            if(failures>=5){
                remaskPrivateShow('Не удалось получить статус задачи '+jobId+'. Она остаётся в Jobs; повторная отправка рекламы не выполнялась.','failed');
                return null;
            }
        }
    }
    return null;
}

function remaskPrivateCanDeliver(item,task,status){
    if(task?.action!=='private_launch')return false;
    const launch=task.payload?.launch;
    const executionMode=launch?.base?.execution_mode||launch?.execution_mode;
    // Legacy tasks with missing intent must never inherit publishing controls.
    // An account-level override is not a valid source of publication consent.
    if(executionMode!=='PUBLISH'||(launch?.override&&
        Object.prototype.hasOwnProperty.call(launch.override,'execution_mode')))return false;
    if(status==='PAUSED'||task.status==='SUCCESS')return true;
    return (item.advertising_steps||[]).some(step=>step.task_id===task.id&&step.verified===true&&
        step.status==='SUCCESS'&&['ADS_MANAGER:PUBLISH','PUBLISH_FAMILY'].includes(step.step)&&
        ['campaign_id','adset_id','creative_id','ad_id'].every(key=>/^\d{5,40}$/.test(String(step.outputs?.[key]||''))));
}

async function remaskPrivateDelivery(status){
    const source=typeof state!=='undefined'?state.privateAdvertisingSourceJob:null;
    if(!source||!['ACTIVE','PAUSED'].includes(status)||remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const targets=[];
        for(const item of source.items||[]){
            for(const task of item.tasks||[]){
                if(!remaskPrivateCanDeliver(item,task,status))continue;
                const payload=task.payload||{};
                const sourceKey=String(payload.launch_key||task.idempotency_key||'');
                if(!sourceKey)continue;
                targets.push({profile:String(item.profile_id||''),source_launch_key:sourceKey,
                    business_id:payload.business_id,ad_account_id:payload.ad_account_id,page_id:payload.page_id,
                    ...(payload.selected_payment_method?{selected_payment_method:payload.selected_payment_method}:{})});
            }
        }
        if(!targets.length)throw new Error('В этой задаче нет подходящих рекламных объектов.');
        const fingerprint=JSON.stringify({job:source.job_id||source.id,status,targets});
        let request=null;
        try{const saved=JSON.parse(localStorage.getItem('remask_private_delivery_pending_v1')||'null');
            if(saved?.fingerprint===fingerprint)request=saved.request;}catch(_){}
        if(!request){
            const nonce=remaskPrivateNonce();
            request={action:'create',idempotency_key:'advertising-delivery-'+nonce,profiles:targets.map((row,index)=>{
                const key='advertising-delivery-'+nonce+'-'+index;
                const {profile,...payload}=row;
                return {profile_id:profile,tasks:[{action:'private_delivery',idempotency_key:key,
                    payload:{...payload,status,delivery_key:key}}]};
            })};
            try{localStorage.setItem('remask_private_delivery_pending_v1',JSON.stringify({fingerprint,request}));}
            catch(_){throw new Error('Не удалось сохранить ключ изменения статуса. Освободите место в браузере и повторите.');}
        }
        const accepted=await remaskPrivateWorker(request);
        const jobId=String(accepted?.job?.job_id||'');
        if(!jobId)throw new Error('Сервер не вернул номер задачи.');
        remaskPrivateShow((status==='ACTIVE'?'Активация':'Пауза')+' · задача '+jobId+'. Результаты доступны в Jobs.');
        remaskPrivatePollJob(jobId);
        return accepted;
    }catch(error){remaskPrivateShow(String(error?.message||error),'failed');return null;}
    finally{remaskPrivateLaunchBusy=false;validateReady();}
}
if(typeof setJobDeliveryStatus==='function')setJobDeliveryStatus=remaskPrivateDelivery;

async function remaskPrivateRestoreSourceJob(){
    let saved=null;
    try{saved=JSON.parse(localStorage.getItem('remask_private_source_job_v1')||'null');}catch(_){}
    let linked='';
    try{linked=new URL(window.location.href).searchParams.get('private_job')||'';}catch(_){}
    const jobId=linked||saved?.job_id;
    if(typeof jobId==='string'&&/^[a-f0-9]{32}$/.test(jobId)){
        const job=await remaskPrivatePollJob(jobId);
        if(remaskPrivateIsJob(job))remaskPrivateRememberJob(jobId);
    }
}
if(typeof document!=='undefined'){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>remaskPrivateRestoreSourceJob(),{once:true});
    else remaskPrivateRestoreSourceJob();
}
// A late legacy init/status response must not erase the restored worker view.
// Explicitly choosing a legacy recent job leaves private polling first.
if(typeof renderJob==='function'){
    const remaskLegacyRenderJob=renderJob;
    renderJob=function(job){
        const selected=typeof state!=='undefined'?state.privateAdvertisingJob:null;
        if(remaskPrivateIsJob(selected))return;
        return remaskLegacyRenderJob(job);
    };
}
if(typeof loadRecentJobs==='function'){
    const remaskLegacyRecentJobs=loadRecentJobs;
    loadRecentJobs=async function(){
        await remaskLegacyRecentJobs();
        if(typeof state!=='undefined')remaskPrivateRecentJob(state.privateAdvertisingJob);
    };
}
if(typeof document!=='undefined')$('recentJobSelect')?.addEventListener('change',async event=>{
    const value=String($('recentJobSelect').value||'');
    if(/^private:[a-f0-9]{32}$/.test(value)){
        event.preventDefault();event.stopImmediatePropagation();
        await remaskPrivatePollJob(value.slice(8));return;
    }
    if(typeof state!=='undefined')state.privateAdvertisingJob=null;
    ++remaskPrivatePollGeneration;
},true);
async function remaskPrivateCreateJob() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    let submitting=false;
    try{
        const config=await remaskPrivatePrepareConfig();
        remaskPrivateAssertCurrentConfig(config);
        const reviewed=await remaskPrivateReviewConfig(config,{render:true,
            assertCurrent:()=>remaskPrivateAssertCurrentConfig(config)});
        remaskPrivateAssertCurrentConfig(config);
        if(!reviewed?.length)return null;
        const blocked=reviewed.filter(entry=>entry.review?.ready!==true);
        if(blocked.length)throw new Error('Черновик не сохранён для РК '+
            blocked.map(entry=>entry.row.ad_account_id+': '+entry.error).join(' · ')+'.');
        const planned=remaskPrivateJobRequest(config,reviewed);
        remaskPrivateShow(remaskPrivateExecutionMode()==='DRAFT'?'Сохраняю черновик…':'Отправляю рекламу…');
        submitting=true;
        const accepted=await remaskPrivateWorker(planned.request);
        const jobId=String(accepted?.job?.job_id||'').trim();
        if(!jobId)throw new Error('Сервер не вернул номер задачи.');
        remaskPrivateShow('Задача запущена. Результат появится в «Задачи и результаты».');
        remaskPrivateRememberJob(jobId);
        remaskPrivatePollJob(jobId);
        return accepted;
    }catch(error){
        const message=remaskPrivateLaunchUserMessage(error).replace(/[.!?]+$/,'');
        remaskPrivateShow((submitting?'Не удалось подтвердить сохранение. ':'Черновик не сохранён. ')+message+
            (submitting?' Проверьте статус в «Задачи и результаты» перед повторной отправкой.':''),'failed');
        return null;
    }finally{
        remaskPrivateLaunchBusy=false;validateReady();
    }
}

const remaskOriginalValidateReady=validateReady;
function remaskPrivateHideReviewControls() {
    for (const id of ['reviewLaunch','serverDryRun','dryRunPlan']) {
        const button=$(id);
        if(button){button.hidden=true;button.setAttribute?.('aria-hidden','true');}
    }
}
validateReady=function() {
    remaskOriginalValidateReady();
    const busy=remaskPrivateLaunchBusy||
        (typeof state!=='undefined'&&state.processingJob===true);
    remaskPrivateHideReviewControls();
    const button=$('launchButton');
    if(button){
        button.disabled=busy;
        button.title=busy?'Сохраняем черновик…':'';
        if(remaskPrivateExecutionMode()==='DRAFT')button.textContent='Сохранить черновик';
    }
    if($('reviewStatus'))$('reviewStatus').textContent='';
    const diagnostic=$('remaskPrivateDisabledReason');
    if(diagnostic){diagnostic.replaceChildren();diagnostic.hidden=true;}
};
validateReady();
// Search remains attached to an explicitly selected RK/profile rather than
// the old Graph token. The PHP bridge rejects an unbound account context.
if(typeof apiJson==='function'){
    const originalAdvertisingApiJson=apiJson;
    apiJson=async function(url,options={}){
        const body=options.body;
        if(String(url).includes('metaAssets.php')){
            const input=typeof body==='string'?new URLSearchParams(body):body;
            if(typeof input?.get==='function'&&input.get('resource')==='pages'){
                input.set('execution_mode',remaskPrivateExecutionMode());
                if(typeof body==='string')options={...options,body:input.toString()};
            }
        }
        if(String(url).includes('metaTargetingSearch.php')&&typeof body?.get==='function'&&!body.get('account_id')){
            const profile=String(body.get('profile')||'');
            const ids=typeof selectedAccountIds==='function'?selectedAccountIds():[];
            const matches=ids.filter(id=>String((typeof targetForAccount==='function'?targetForAccount(remaskPrivateAccountId(id))?.profile:'')||state.profile||'')===profile);
            if(matches.length)body.set('account_id',remaskPrivateAccountId(matches[0]));
        }
        return originalAdvertisingApiJson(url,options);
    };
}

// Delivery uses the same Launch form and existing Bootstrap controls.
if(typeof document!=='undefined'&&document.createElement&&$('launchButton')?.parentNode&&!$('launchDeliverySelect')){
    const block=document.createElement('div');block.className='mb-2';
    const label=document.createElement('label');label.htmlFor='launchDeliverySelect';label.textContent='После создания';
    const select=document.createElement('select');select.id='launchDeliverySelect';select.className='form-control';
    for(const [value,text] of [['','Статус из настроек'],['PAUSED','Оставить на паузе'],['ACTIVE','Активировать рекламу']]){
        const option=document.createElement('option');option.value=value;option.textContent=text;select.appendChild(option);
    }
    try{select.value=localStorage.getItem('remask_launch_delivery_v1')||'';}catch(_){}
    select.addEventListener('change',()=>{
        try{localStorage.setItem('remask_launch_delivery_v1',select.value);}catch(_){}
        remaskPrivatePendingClear();validateReady();
    });
    block.appendChild(label);block.appendChild(select);$('launchButton').parentNode.insertBefore(block,$('launchButton'));
}

remaskPrivateInstallExecutionMode();

// Media compatibility runs automatically in the save flow. The manual
// inspection/crop/pad panel is kept out of Launch; it is not a Meta action.
// Capture before legacy handlers. Old metaLaunchReview/metaJobCreate endpoints
// remain blocked, so no click can fall through to the token-based Graph path.
if(typeof document!=='undefined'&&document.addEventListener){
    document.addEventListener('click',event=>{
        const button=event.target?.closest?.('#launchButton');
        if(!button||button.disabled)return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        remaskPrivateCreateJob();
    },true);
}


/* REMASK_AUTOUPLOAD_LAZY_CUSTOM_AUDIENCES_V1 */
/* Custom Audiences are optional. Do not make every RK selection wait for an
   Ads Manager document; read them only on demand or when restoring saved IDs. */
(function installLazyCustomAudienceDiscovery() {
    if (typeof loadCustomAudiences !== 'function'
            || typeof selectedAccountIds !== 'function'
            || typeof state === 'undefined') return;

    const originalLoad = loadCustomAudiences;
    const buttonId = 'remaskLoadCustomAudiences';
    let currentKey = '';
    let loadedKey = '';
    let inFlightKey = '';
    let inFlightPromise = null;

    function isTargetMode() {
        return typeof targetMode === 'function' && targetMode();
    }

    function selectedKey() {
        if (isTargetMode()) return '';
        const ids = selectedAccountIds();
        if (!Array.isArray(ids) || ids.length !== 1) return '';
        const profile = String(state.profile || $('profile')?.value || '').trim();
        const account = String(ids[0] || '').trim().replace(/^act_/, '');
        return profile && account ? profile + '|' + account : '';
    }

    function hasPendingAudiences() {
        const pending = state.pendingBundleAssets || {};
        return ['customAudiences', 'excludedCustomAudiences'].some((key) =>
            Array.isArray(pending[key]) && pending[key].some((id) => String(id || '').trim())
        );
    }

    function getButton() {
        if (typeof document === 'undefined') return null;
        let button = document.getElementById(buttonId);
        const status = $('customAudienceStatus');
        if (!button && status?.parentNode && document.createElement) {
            button = document.createElement('button');
            button.id = buttonId;
            button.type = 'button';
            button.className = 'btn btn-outline-secondary btn-sm mt-2';
            button.textContent = 'Загрузить аудитории';
            status.parentNode.insertBefore(button, status);
            button.addEventListener('click', () => loadCustomAudiences(true));
        }
        return button;
    }

    function setButton(text, disabled, hidden = false) {
        const button = getButton();
        if (!button) return;
        button.textContent = text;
        button.disabled = Boolean(disabled);
        button.hidden = Boolean(hidden);
    }

    function deferForCurrentAccount(key) {
        if (currentKey !== key) {
            currentKey = key;
            loadedKey = '';
            state.customAudiences = [];
            state.launchReview = null;
            for (const id of ['customAudiences', 'excludedCustomAudiences']) {
                const select = $(id);
                if (!select) continue;
                select.innerHTML = '<option value="">Список не загружен</option>';
                select.disabled = true;
            }
        }
        setButton('Загрузить аудитории', false);
        const status = $('customAudienceStatus');
        if (status) status.textContent = 'Не загружены. Нажмите «Загрузить аудитории», если они нужны.';
    }

    async function load(force = false) {
        if (isTargetMode()) {
            currentKey = '';
            loadedKey = '';
            setButton('Аудитории доступны в привязках по РК', true, true);
            return originalLoad();
        }

        const key = selectedKey();
        if (!key) {
            currentKey = '';
            loadedKey = '';
            const ids = selectedAccountIds();
            const prompt = ids.length > 1 ? 'Выберите один РК' : 'Выберите РК';
            state.customAudiences = [];
            state.launchReview = null;
            for (const id of ['customAudiences', 'excludedCustomAudiences']) {
                const select = $(id);
                if (!select) continue;
                select.innerHTML = '<option value="">' + prompt + '</option>';
                select.disabled = true;
            }
            const status = $('customAudienceStatus');
            if (status) status.textContent = prompt + ', чтобы загрузить аудитории.';
            setButton(prompt, true);
            return;
        }

        const changed = currentKey !== key;
        if (changed) deferForCurrentAccount(key);
        if (inFlightKey === key && inFlightPromise) return inFlightPromise;
        if (!force && loadedKey === key) return;
        if (!force && !hasPendingAudiences()) return;

        const button = getButton();
        if (button) {
            button.hidden = false;
            button.disabled = true;
            button.textContent = 'Загружаю…';
        }
        const status = $('customAudienceStatus');
        if (status) status.textContent = 'Загружаем аудитории…';

        const request = Promise.resolve().then(() => originalLoad()).then(() => {
            if (selectedKey() !== key) return;
            const message = String($('customAudienceStatus')?.textContent || '');
            const failed = /failed|error|timeout|timed out|gateway|\b504\b|не удалось|не вернула список|ошибк|таймаут|не загруз/i.test(message);
            if (failed) {
                setButton('Повторить загрузку', false);
            } else {
                loadedKey = key;
                setButton('Аудитории загружены', true);
            }
        }).catch(() => {
            if (selectedKey() !== key) return;
            if (status) status.textContent = 'Не удалось загрузить аудитории. Остальная форма доступна.';
            setButton('Повторить загрузку', false);
        }).finally(() => {
            if (inFlightPromise === request) {
                inFlightPromise = null;
                inFlightKey = '';
            }
        });
        inFlightKey = key;
        inFlightPromise = request;
        return request;
    }

    loadCustomAudiences = load;

    if (typeof loadSelectedBundle === 'function') {
        const originalBundleLoad = loadSelectedBundle;
        loadSelectedBundle = function(...args) {
            const result = originalBundleLoad.apply(this, args);
            if (hasPendingAudiences()) Promise.resolve(loadCustomAudiences(true)).catch(() => {});
            return result;
        };
    }

    getButton();
})();
