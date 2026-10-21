/* REMASK_PRIVATE_LAUNCH_CATALOG_V1 */
/* REMASK_PRIVATE_LAUNCH_CATALOG_V2 */
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

const remaskPrivateLaunchNotice='Подготовьте план рекламы. Выберите сохранение черновика или публикацию.';
let remaskPrivateLaunchBusy=false;
const remaskPrivateLaunchPendingKey='remask_private_launch_pending_v1';
// Saving an actual Meta draft is an explicit operation, separate from publishing.
function remaskPrivateExecutionMode(config) {
    const mode=$('remaskPrivateExecutionMode')?.value||config?.payload?.execution_mode||'PUBLISH';
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
    if(Array.isArray(config?._targets))return JSON.parse(JSON.stringify(config._targets));
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
        const selectedPaymentMethod=remaskPrivateSelectedPayment(
            profile,accountId
        );
        const override=JSON.parse(JSON.stringify(overrides[accountId]||overrides['act_'+accountId]||{}));
        // Operation mode is a form-level choice, never a stale per-RK override.
        delete override.execution_mode;
        const base={...(config.payload||{})};delete base.execution_mode;
        if(executionMode==='DRAFT')base.execution_mode='DRAFT';
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
    if(executionMode==='DRAFT')config.payload.execution_mode='DRAFT';
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
function remaskPrivateRenderReviewResults(results,render=true) {
    const targets=results.map(entry=>({profile_id:entry.row.profile,
        business_id:entry.row.business_id,ad_account_id:entry.row.ad_account_id,
        page_id:entry.row.page_id,status:entry.status,
        error_code:entry.error_code||'',message:entry.error||'',
        review_attempted:entry.review_attempted!==false}));
    if(typeof state!=='undefined')state.privateLaunchReadiness={admission_policy:'all_required',targets};
    if(!render||typeof document==='undefined'||!document.createElement)return;
    const anchor=$('launchResult')||$('reviewStatus');
    if(!anchor?.parentNode)return;
    let box=$('remaskPrivateAccountReviews');
    if(!box){
        box=document.createElement('div');box.id='remaskPrivateAccountReviews';
        box.className='mt-2';box.setAttribute('aria-label','Последняя проверка рекламных кабинетов');
        anchor.parentNode.insertBefore(box,anchor.nextSibling);
    }
    box.replaceChildren();
    const heading=document.createElement('p');heading.textContent='Результаты последней проверки РК';
    box.appendChild(heading);
    const list=document.createElement('ul');
    for(const target of targets){
        const item=document.createElement('li');
        item.textContent='Профиль '+target.profile_id+' · РК '+target.ad_account_id+' · '+
            (target.status==='READY'?'ГОТОВ':target.error_code+': '+target.message);
        list.appendChild(item);
    }
    box.appendChild(list);
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
        try{
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
                    error:'Выберите карту для РК '+row.ad_account_id+' и повторите проверку.'});
                continue;
            }
            if(review.ready!==true){
                results.push({row,review,status:'BLOCKED',error_code:'ACCOUNT_NOT_READY',
                    error:'Проверка не подтвердила готовность РК '+row.ad_account_id+'.'});
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
            results.push({row,review,status:'READY'});
        }catch(error){
            if(assertCurrent)assertCurrent();
            const message=String(error?.message||error);
            const gate=(String(error?.code||'')+' '+message).match(/(?:\b|_)(CHECKPOINT_REQUIRED|TWO_FACTOR_REQUIRED|SESSION_EXPIRED|BUSINESS_LOGIN_GATE)\b/)?.[1];
            const code=error?.code||gate||'REVIEW_FAILED';
            if(gate)authBlockedProfiles.set(row.profile,{code:gate,message});
            if(error?.httpStatus===401||error?.httpStatus===403)
                batchStop={code:'REMASK_AUTH_REQUIRED',message,status:'AUTH_REQUIRED'};
            // A quota failure is not an account-local readiness failure.
            // Stop the original batch rather than reading through other RKs.
            if(error?.httpStatus===429||/(?:^|_)(?:RATE_LIMIT(?:ED)?|THROTTLED)(?:_|$)/.test(code)||
                (/Meta rejected the advertising request/.test(message)&&/\bcode=(?:4|17|32|613)\b/.test(message)))
                batchStop={code:'META_RATE_LIMITED',message,status:'RATE_LIMITED'};
            results.push({row,review:{ready:false},status:batchStop?.status||(gate?'AUTH_REQUIRED':'BLOCKED'),
                error_code:batchStop?.code||code,error:message});
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
    const nonce=remaskPrivateNonce();
    const request={
        action:'create',
        idempotency_key:'private-launch-batch-'+nonce,
        profiles:rows.map((row,index)=>{
            const key='private-launch-'+row.profile+'-'+row.ad_account_id+'-'+nonce+'-'+(index+1);
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
async function remaskPrivatePollJob(jobId) {
    const generation=++remaskPrivatePollGeneration;
    let failures=0;
    for(let attempt=0;attempt<180;attempt++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        if(generation!==remaskPrivatePollGeneration)return null;
        try{
            const data=await remaskPrivateWorker({action:'status',job_id:jobId});
            if(generation!==remaskPrivatePollGeneration)return null;
            failures=0;
            const job=data?.job||{};
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
            const done=Number(job.items_done||job.items_completed||0);
            const total=Number(job.items_total||0);
            const advertising=(job.items||[]).flatMap(item=>item.advertising_steps||[]);
            const publishing=advertising.some(step=>step.publication?.code==='PUBLISH_PENDING');
            remaskPrivateShow(
                'Задача '+jobId+' · '+String(job.status||'RUNNING')+
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
    if((task.payload?.launch?.base?.execution_mode||task.payload?.launch?.execution_mode)==='DRAFT')return false;
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
    if(saved&&typeof saved.job_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(saved.job_id)){
        await remaskPrivatePollJob(saved.job_id);
    }
}
if(typeof document!=='undefined'){
    if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>remaskPrivateRestoreSourceJob(),{once:true});
    else remaskPrivateRestoreSourceJob();
}
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
        if(blocked.length)throw new Error('Готовы '+(reviewed.length-blocked.length)+'/'+reviewed.length+
            ' РК. Проверка не пройдена: '+blocked.map(entry=>entry.row.ad_account_id+': '+entry.error).join(' · '));
        const planned=remaskPrivateJobRequest(config,reviewed);
        remaskPrivateShow((planned.reused?'Повторяю сохранённую отправку':'Создаю')+
            ' задачу для '+reviewed.length+' РК…');
        submitting=true;
        const accepted=await remaskPrivateWorker(planned.request);
        const jobId=String(accepted?.job?.job_id||'').trim();
        if(!jobId)throw new Error('Сервер не вернул номер задачи.');
        remaskPrivateShow(
            'Задача принята: '+jobId+' · '+reviewed.length+' РК. Результаты доступны в Jobs.'
        );
        try{localStorage.setItem('remask_private_source_job_v1',JSON.stringify({job_id:jobId}));}catch(_){}
        remaskPrivatePollJob(jobId);
        return accepted;
    }catch(error){
        const message=String(error?.message||error).replace(/[.!?]+$/,'');
        remaskPrivateShow('Создание рекламы недоступно: '+message+'.'+
            (submitting?' При потере ответа повторная отправка использует сохранённую задачу.':''),'failed');
        return null;
    }finally{
        remaskPrivateLaunchBusy=false;validateReady();
    }
}

const remaskOriginalValidateReady=validateReady;
validateReady=function() {
    remaskOriginalValidateReady();
    const readiness=remaskPrivateStructuralReadiness();
    const busy=remaskPrivateLaunchBusy||
        (typeof state!=='undefined'&&state.processingJob===true);
    for (const id of ['reviewLaunch','launchButton','serverDryRun','dryRunPlan']) {
        const button=$(id);
        if(!button)continue;
        // Form-completeness failures are reported by the click handler:
        // Local plans and explicit Meta readiness are separate from creation.
        // A disabled button prevented operators from reaching either handler.
        // Only in-flight work disables the controls to prevent duplicate jobs.
        button.disabled=busy;
        button.title=busy?'Подождите завершения текущей проверки.':
            (readiness.ready?remaskPrivateLaunchNotice:
            'Проверка при нажатии: '+readiness.reason);
    }
    if($('remaskPrivateExecutionMode')&&$('launchButton')){
        $('launchButton').textContent=$('remaskPrivateExecutionMode').value==='DRAFT'?'Сохранить черновик в Meta':'Создать рекламу';
        $('launchButton').setAttribute?.('aria-label',$('launchButton').textContent);
    }
    // Keep validation visible before the click without blocking interaction.
    // Never skip validation inside remaskPrivatePrepareConfig or server review.
    const anchor=$('reviewLaunch')||$('launchButton');
    if(anchor?.parentNode) {
        let diagnostic=$('remaskPrivateDisabledReason');
        if(!diagnostic) {
            diagnostic=document.createElement('div');
            diagnostic.id='remaskPrivateDisabledReason';
            diagnostic.className='muted mt-2';
            diagnostic.setAttribute('role','status');
            diagnostic.setAttribute('aria-live','polite');
            anchor.parentNode.appendChild(diagnostic);
        }
        diagnostic.hidden=readiness.ready||busy;
        diagnostic.textContent=diagnostic.hidden?'':'Перед запуском проверьте: '+readiness.reason+
            ' Нажатие на кнопку покажет результат проверки.';
    }
};
validateReady();
if ($('reviewStatus')) $('reviewStatus').textContent=remaskPrivateLaunchNotice;
if ($('reviewLaunch')) $('reviewLaunch').textContent='Проверить готовность в Meta';

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

if(globalThis.remaskMediaTools){
    globalThis.remaskMediaTools.install($('creativeFormat')?.parentElement||$('media'),async()=>{
        const config=await remaskPrivatePrepareConfig();
        remaskPrivateAssertCurrentConfig(config);
        return {creative:config.payload.creative,targeting:config.payload.adset?.targeting||{},
            assertCurrent:()=>remaskPrivateAssertCurrentConfig(config)};
    },async(media,index,captured)=>{
        captured.assertCurrent();
        const snapshot=bundleFormState();
        const selected=captured.media.map(row=>({media_library_id:row.id,sha256:row.sha256}));
        selected[index]={media_library_id:media.id,sha256:media.sha256};
        snapshot.media=captured.creative.format==='CAROUSEL'
            ?{format:'CAROUSEL',carousel:captured.creative.carousel_cards.map((card,i)=>({...card,...selected[i]}))}
            :{format:'SINGLE',...selected[0]};
        snapshot.media.checksums=selected.map(row=>({id:row.media_library_id,sha256:row.sha256}));
        await applyBundleData(snapshot);
    });
}

// Capture before legacy handlers. Old metaLaunchReview/metaJobCreate endpoints
// remain blocked, so no click can fall through to the token-based Graph path.
if(typeof document!=='undefined'&&document.addEventListener){
    document.addEventListener('click',event=>{
        const button=event.target?.closest?.('#reviewLaunch,#launchButton,#serverDryRun,#dryRunPlan');
        if(!button||button.disabled)return;
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        if(button.id==='launchButton')remaskPrivateCreateJob();
        else if(button.id==='reviewLaunch')remaskPrivateReviewLive();
        else remaskPrivateReviewOnly();
    },true);
}
