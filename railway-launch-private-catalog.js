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

const remaskPrivateLaunchNotice='Подготовьте план рекламы. При запуске ReMask проверит доступ страницы и оплату выбранных РК.';
let remaskPrivateLaunchBusy=false;
const remaskPrivateLaunchPendingKey='remask_private_launch_pending_v1';
const remaskPrivatePaymentSelections={};

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
    if(Array.isArray(config?._targets))return JSON.parse(JSON.stringify(config._targets));
    const ids=Array.isArray(config?.accountIds)?config.accountIds:
        (typeof selectedAccountIds==='function'?selectedAccountIds():[]);
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
        const pageId=String(binding?.page_id||'').trim();
        if(!profile)throw new Error('Для РК не выбран профиль Facebook: '+accountId+'.');
        if(!/^\d{5,30}$/.test(businessId))throw new Error('Для РК не указан BM: '+accountId+'.');
        if(!/^\d{5,30}$/.test(pageId))throw new Error('Выберите страницу для РК '+accountId+'.');
        const selectedPaymentMethod=remaskPrivateSelectedPayment(
            profile,accountId
        );
        const override=JSON.parse(JSON.stringify(overrides[accountId]||overrides['act_'+accountId]||{}));
        if(config.payload?.creative?.page_id||override.creative?.page_id)
            override.creative={...(override.creative||{}),page_id:pageId};
        if(config.payload?.adset?.promoted_object?.page_id||override.adset?.promoted_object?.page_id)
            override.adset={...(override.adset||{}),promoted_object:{...(override.adset?.promoted_object||{}),page_id:pageId}};
        if(config.payload?.creative?.format==='INSTAGRAM_POST'){
            const mediaId=String(binding?.source_instagram_media_id||config.payload.creative.source_instagram_media_id||'').trim();
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
                base:config.payload||{},
                override
            }
        };
    });
}
function remaskPrivateStructuralReady() {
    try {
        if (typeof currentLaunchConfigForRequest!=='function') return false;
        const config=currentLaunchConfigForRequest();
        return remaskPrivateTargetRows(config).length>0;
    } catch (_) {
        return false;
    }
}
async function remaskPrivateWorker(payload) {
    const response=await fetch('ajax/pythonWorkerJobs.php',{
        method:'POST',
        credentials:'same-origin',
        headers:{'Accept':'application/json','Content-Type':'application/json'},
        body:JSON.stringify(payload)
    });
    let data={};
    try{data=await response.json();}catch(_){}
    if(!response.ok||data?.ok===false){
        const detail=data?.message||data?.error||data?.detail||('HTTP '+response.status);
        throw new Error(typeof detail==='string'?detail:JSON.stringify(detail));
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
            method:'POST',credentials:'same-origin',body:form
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
async function remaskPrivatePrepareConfig() {
    // Snapshot before awaiting: Review and Job must describe the same form.
    const config=JSON.parse(JSON.stringify(currentLaunchConfigForRequest()));
    const creative=config.payload.creative||(config.payload.creative={});
    const format=typeof creativeFormat==='function'?creativeFormat():'SINGLE';
    const preset=typeof remaskSelectedCreativePreset!=='undefined'?remaskSelectedCreativePreset:null;
    const selected=typeof state!=='undefined'?state.pendingWorkspaceMedia:null;
    const files=format==='CAROUSEL'&&typeof carouselFiles==='function'?carouselFiles():
        (format==='SINGLE'?Array.from($('media')?.files||[]).slice(0,1):[]);
    creative.format=format;
    const delivery=$('launchDeliverySelect')?.value;
    if(delivery==='ACTIVE'||delivery==='PAUSED')config.payload.delivery={campaign:delivery,adset:delivery,ad:delivery};
    if(format==='CAROUSEL'){
        const cards=typeof carouselCardsPayload==='function'?carouselCardsPayload():[];
        const saved=preset?.format==='CAROUSEL'?preset.carousel||[]:[];
        if(cards.length<2||cards.length>10||cards.length!==(saved.length||files.length))
            throw new Error('Карусель требует от 2 до 10 подготовленных карточек.');
        creative.carousel_cards=JSON.parse(JSON.stringify(cards));
        if(saved.length)creative.carousel_media_library_ids=saved.map(card=>String(card.media_library_id||''));
    }else if(format==='SINGLE'){
        if(!files.length&&preset?.format==='SINGLE'&&preset.media_library_id)
            creative.media_library_id=String(preset.media_library_id);
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
    if(files.length)creative.staged_media=await Promise.all(files.map(remaskPrivateStageFile));
    config._targets=targets.map(row=>({...row,launch:{...row.launch,base:config.payload}}));
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
async function remaskPrivateReviewConfig(config,{render=true}={}) {
    const contractStatus=await remaskPrivateContractStatus({render,config});
    if(!contractStatus.ready){
        const missing=contractStatus.missing.map(row=>row.step).join(', ');
        throw new Error('Модуль публикации не готов ('+missing+').');
    }
    const rows=remaskPrivateTargetRows(config);
    const results=[];
    for(let index=0;index<rows.length;index++){
        const row=rows[index];
        if(render)remaskPrivateShow('Проверка рекламы '+(index+1)+'/'+rows.length+' · РК '+row.ad_account_id+'…');
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
            throw new Error(
                'Выберите карту для РК '+
                row.ad_account_id+' и повторите проверку.'
            );
        }
        if(review.ready!==true)throw new Error(
            'Проверка не подтвердила готовность РК '+
            row.ad_account_id+'.'
        );
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
        results.push({row,review});
    }
    if(render)remaskPrivateShow(
        'Настройки проверены: '+results.length+'/'+rows.length+
        ' РК. После создания будет применён выбранный статус.',
        'ready'
    );
    return results;
}
async function remaskPrivateReviewOnly() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const config=await remaskPrivatePrepareConfig();
        const rows=remaskPrivateTargetRows(config);
        const plans=[];
        for(const row of rows){
            const response=await remaskPrivateWorker({action:'private_launch_plan',profile_id:row.profile,
                business_id:row.business_id,ad_account_id:row.ad_account_id,page_id:row.page_id,launch:row.launch});
            if(response.plan?.configuration_valid!==true||response.plan?.checked_live!==false)
                throw new Error('Сервер не подтвердил подготовку плана.');
            plans.push(response.plan);
        }
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
                    if($('pauseJobAds'))$('pauseJobAds').disabled=!sourceTasks.some(task=>task.action==='private_launch');
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
    try{
        const config=await remaskPrivatePrepareConfig();
        const reviewed=await remaskPrivateReviewConfig(config,{render:true});
        if(!reviewed?.length)return null;
        const planned=remaskPrivateJobRequest(config,reviewed);
        remaskPrivateShow((planned.reused?'Повторяю сохранённую отправку':'Создаю')+
            ' задачу для '+reviewed.length+' РК…');
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
        remaskPrivateShow('Создание рекламы недоступно: '+String(error?.message||error)+
            '. При потере ответа повторная отправка использует сохранённую задачу.','failed');
        return null;
    }finally{
        remaskPrivateLaunchBusy=false;validateReady();
    }
}

const remaskOriginalValidateReady=validateReady;
validateReady=function() {
    remaskOriginalValidateReady();
    const structurallyReady=remaskPrivateStructuralReady();
    for (const id of ['reviewLaunch','launchButton','serverDryRun','dryRunPlan']) {
        const button=$(id);
        if(!button)continue;
        button.disabled=!structurallyReady||remaskPrivateLaunchBusy||
            (typeof state!=='undefined'&&state.processingJob===true);
        button.title=structurallyReady?remaskPrivateLaunchNotice:
            'Выберите РК и страницу и заполните обязательные параметры объявления.';
    }
};
validateReady();
if ($('reviewStatus')) $('reviewStatus').textContent=remaskPrivateLaunchNotice;
if ($('reviewLaunch')) $('reviewLaunch').textContent='Подготовить план';

// Search remains attached to an explicitly selected RK/profile rather than
// the old Graph token. The PHP bridge rejects an unbound account context.
if(typeof apiJson==='function'){
    const originalAdvertisingApiJson=apiJson;
    apiJson=async function(url,options={}){
        const body=options.body;
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
        else remaskPrivateReviewOnly();
    },true);
}
