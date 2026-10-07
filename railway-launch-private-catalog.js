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
    $('fundingStatus').textContent='Платёжная привязка считается готовой только после exact live-проверки выбранного РК.';
};

const remaskPrivateLaunchNotice='Private Launch: перед CREATE ReMask проверяет exact BM/RK, доступ выбранной FP, ACTIVE RK, карту и полный серверный mutation contract.';
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
    const container=remaskPrivatePaymentSelectorContainer();
    if(!container||!safe.length)return;
    const key=remaskPrivatePaymentKey(row.profile,row.ad_account_id);
    let block=Array.from(container.children||[]).find(
        node=>node?.dataset?.paymentKey===key
    );
    if(!block){
        block=document.createElement('div');
        block.dataset.paymentKey=key;
        block.className='mb-2';
        const label=document.createElement('label');
        label.textContent='RK '+row.ad_account_id+' · карта для Launch';
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
    const select=Array.from(block.children||[]).find(
        node=>node?.dataset?.paymentSelect==='1'
    );
    if(!select)return;
    const current=remaskPrivateSelectedPayment(
        row.profile,row.ad_account_id
    );
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
            && (!current.type||current.type===String(method.type||''))
        ) option.selected=true;
        select.appendChild(option);
    });
}


function remaskPrivateAccountId(value) {
    const id=String(value||'').replace(/^act_/i,'').trim();
    return /^\d{5,30}$/.test(id)?id:'';
}
function remaskPrivateTargetRows(config) {
    const ids=Array.isArray(config?.accountIds)?config.accountIds:
        (typeof selectedAccountIds==='function'?selectedAccountIds():[]);
    const overrides=config?.accountOverrides&&typeof config.accountOverrides==='object'
        ?config.accountOverrides:{};
    return ids.map(rawId=>{
        const accountId=remaskPrivateAccountId(rawId);
        if(!accountId)throw new Error('Private Launch: invalid RK id.');
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
        if(!profile)throw new Error('Private Launch: Facebook profile is missing for RK '+accountId+'.');
        if(!/^\d{5,30}$/.test(businessId))throw new Error('Private Launch: BM is missing for RK '+accountId+'.');
        if(!/^\d{5,30}$/.test(pageId))throw new Error('Private Launch: select a Page for RK '+accountId+'.');
        const selectedPaymentMethod=remaskPrivateSelectedPayment(
            profile,accountId
        );
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
                override:overrides[accountId]||overrides['act_'+accountId]||{}
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
async function remaskPrivateContractStatus({render=true}={}) {
    const response=await remaskPrivateWorker({action:'private_launch_contracts'});
    const contracts=response?.contracts||{};
    const order=['CAMPAIGN','AD_SET','CREATIVE','AD'];
    const rows=order.map(step=>{
        const row=contracts[step]||{};
        return {
            step,
            configured:row.configured===true,
            code:String(row.code||''),
            message:String(row.message||''),
            friendly_name:String(row.friendly_name||'')
        };
    });
    const missing=rows.filter(row=>!row.configured);
    if(render){
        remaskPrivateShow(
            'Private Launch contracts · '+
            rows.map(row=>row.step+' '+(row.configured?'READY':'MISSING')).join(' · ')+
            (missing.length?' · запуск заблокирован до полного комплекта':''),
            missing.length?'failed':'ready'
        );
    }
    return {rows,missing,ready:missing.length===0};
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
    }));}catch(_){}
}
function remaskPrivatePendingClear() {
    try{localStorage.removeItem(remaskPrivateLaunchPendingKey);}catch(_){}
}
async function remaskPrivateReviewConfig(config,{render=true}={}) {
    const contractStatus=await remaskPrivateContractStatus({render});
    if(!contractStatus.ready){
        const missing=contractStatus.missing.map(row=>row.step).join(', ');
        throw new Error('Private Launch contracts missing: '+missing+'.');
    }
    const rows=remaskPrivateTargetRows(config);
    const results=[];
    for(let index=0;index<rows.length;index++){
        const row=rows[index];
        if(render)remaskPrivateShow('Private Launch Review '+(index+1)+'/'+rows.length+' · RK '+row.ad_account_id+'…');
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
                'Private Launch: выберите карту для RK '+
                row.ad_account_id+' и повторите Review.'
            );
        }
        if(review.ready!==true)throw new Error(
            'Private Launch Review did not confirm RK '+
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
        'Private Launch Review READY: '+results.length+'/'+rows.length+
        ' RK · exact Page access + ACTIVE RK + linked card + 4 private mutation contracts.',
        'ready'
    );
    return results;
}
async function remaskPrivateReviewOnly() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const config=currentLaunchConfigForRequest();
        return await remaskPrivateReviewConfig(config,{render:true});
    }catch(error){
        remaskPrivateShow('Private Launch blocked: '+String(error?.message||error),'failed');
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
    if(status==='SUCCESS')remaskPrivatePendingClear();
    return status;
}
async function remaskPrivatePollJob(jobId) {
    for(let attempt=0;attempt<180;attempt++){
        await new Promise(resolve=>setTimeout(resolve,2000));
        try{
            const data=await remaskPrivateWorker({action:'status',job_id:jobId});
            const job=data?.job||{};
            const done=Number(job.items_done||job.items_completed||0);
            const total=Number(job.items_total||0);
            remaskPrivateShow(
                'Private Launch Job '+jobId+' · '+String(job.status||'RUNNING')+
                (total?' · '+done+'/'+total:'')
            );
            if(['SUCCESS','FAILED','PARTIAL'].includes(String(job.status||'').toUpperCase())){
                remaskPrivateFinalizeJob(job);
                return job;
            }
        }catch(_){return null;}
    }
    return null;
}
async function remaskPrivateCreateJob() {
    if(remaskPrivateLaunchBusy)return null;
    remaskPrivateLaunchBusy=true;validateReady();
    try{
        const config=currentLaunchConfigForRequest();
        const reviewed=await remaskPrivateReviewConfig(config,{render:true});
        if(!reviewed?.length)return null;
        const planned=remaskPrivateJobRequest(config,reviewed);
        remaskPrivateShow((planned.reused?'Повторяю сохранённую отправку':'Создаю')+
            ' Private Launch Job для '+reviewed.length+' RK…');
        const accepted=await remaskPrivateWorker(planned.request);
        const jobId=String(accepted?.job?.job_id||'').trim();
        if(!jobId)throw new Error('Worker did not return job_id.');
        remaskPrivateShow(
            'Private Launch Job принят: '+jobId+' · '+reviewed.length+
            ' RK. Idempotency request сохраняется до terminal SUCCESS.'
        );
        remaskPrivatePollJob(jobId);
        return accepted;
    }catch(error){
        remaskPrivateShow('Private Launch blocked: '+String(error?.message||error)+
            '. Если ответ Job потерян, следующий клик использует тот же idempotency key.','failed');
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
            'Private Launch: выберите RK и Page и заполните обязательные параметры объявления.';
    }
};
validateReady();
if ($('reviewStatus')) $('reviewStatus').textContent=remaskPrivateLaunchNotice;

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
