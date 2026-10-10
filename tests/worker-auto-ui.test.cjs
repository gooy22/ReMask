const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('railway-python-worker-ui.js', 'utf8');
// Both BM and RK context menus must expose the same automatic bulk action.
for(const [tab,label] of [['businesses','Добавить RK'],['ad_accounts','Проверить Assets']]){
  let inserted;
  const parent={querySelector(){return null;},insertBefore(action){inserted=action;}};
  const anchor={textContent:label,parentNode:parent,cloneNode(){return {
    tagName:'BUTTON',removeAttribute(){},setAttribute(){},addEventListener(kind,callback){this.click=callback;}};}};
  let prepared=0;
  const menu={state:{activeTab:tab},document:{querySelectorAll(){return [anchor];}},
    pythonWorkerPrepareCommonPage(){prepared++;}};
  vm.createContext(menu);
  const begin=source.indexOf('function pythonWorkerEnhanceCommonPageMenu()');
  vm.runInContext(source.slice(begin,source.indexOf('\nfunction pythonWorkerEl',begin)),menu);
  menu.pythonWorkerEnhanceCommonPageMenu();
  assert.equal(inserted.textContent,'Добавить FP');
  inserted.click({preventDefault(){},stopPropagation(){},stopImmediatePropagation(){}});
  assert.equal(prepared,1);
}
// One bulk action prepares two BM accounts per profile without any Page picker.
(async()=>{
  const profiles=Array.from({length:100},(_,i)=>String(i+1));
  const inventory={ad_accounts:[]}; const scopes={};
  for(const profile of profiles){
    scopes[profile]='900000'+profile;
    for(let n=1;n<=2;n++)inventory.ad_accounts.push({profile_id:profile,
      business_id:'800000'+profile+n,id:'act_700000'+profile+n,name:'BM account'});
    inventory.ad_accounts.push({profile_id:profile,business_id:scopes[profile],id:'act_600000'+profile});
  }
  inventory.ad_accounts.push({...inventory.ad_accounts[0]});
  const calls=[]; const ui={busy:false,workerOnline:true};
  const bulk={state:{activeTab:'profiles',inventory},pythonWorkerUiState:ui,
    selectedRows:()=>profiles.map(profile=>({profile_id:profile})),
    pythonWorkerSelectionRefresh(){},pythonWorkerSetText(){},pythonWorkerClearBatchState(){},
    localStorage:{setItem(){}},async pythonWorkerPoll(){},
    pythonWorkerStableKey:v=>v,
    async pythonWorkerProfileProvisioningState(profile){return {personal_scope_id:scopes[profile]};},
    async pythonWorkerMapLimit(items,limit,fn){assert.equal(limit,6);await Promise.all(items.map(fn));},
    async pythonWorkerBridge(payload){calls.push(JSON.parse(JSON.stringify(payload)));return {job:{job_id:'bulk-100'}};}};
  vm.createContext(bulk);
  const begin=source.indexOf('function pythonWorkerPageTargetPlan(');
  vm.runInContext(source.slice(begin,source.indexOf('\nfunction pythonWorkerEnhanceCommonPageMenu',begin)),bulk);
  await bulk.pythonWorkerPrepareCommonPage();
  assert.equal(calls.length,1); assert.equal(calls[0].profiles.length,100);
  assert.equal(calls[0].profiles.reduce((sum,p)=>sum+p.tasks.length,0),200);
  for(const group of calls[0].profiles)for(const task of group.tasks){
    assert.equal(task.payload.parameters.FAN_PAGES.count,1);
    assert.equal(task.payload.parameters.FAN_PAGES.policies_accepted,true);
    assert.notEqual(task.payload.parameters.PAGE_ACCESS.business_id,scopes[group.profile_id]);
    assert.deepEqual(task.payload.steps,['PROXY_CHECK','FAN_PAGES','PAGE_ACCESS']);
  }
  const account=inventory.ad_accounts[0];
  assert.equal(bulk.pythonWorkerPageTargetPlan(inventory,'ad_accounts',[account],scopes).targets.length,1);
  assert.equal(bulk.pythonWorkerPageTargetPlan(inventory,'businesses',[{profile_id:account.profile_id,id:account.business_id}],scopes).targets.length,1);
  console.log('100 profiles / 200 BM accounts: one automatic FP job, no personal accounts or duplicates.');
})().catch(error=>{console.error(error);process.exitCode=1;});
// A failed FP preflight must preserve the real auth reason, never synthesize
// checkpoint, and the read-only session button must never enqueue CREATE.
(async()=>{
  const messages=[]; const calls=[]; const elms=[];
  const authState={workerOnline:true,busy:false,jobId:'previous-failed-job'};
  class AuthElement {
    constructor(tag){this.tag=tag;this.children=[];this.events={};this.value='';}
    appendChild(child){this.children.push(child);}
    addEventListener(kind,cb){this.events[kind]=cb;}
  }
  const auth={pythonWorkerUiState:authState,window:{},
    document:{body:new AuthElement('body'),createElement(tag){const e=new AuthElement(tag);elms.push(e);return e;}},
    pythonWorkerSelectedProfiles:()=>['9'], pythonWorkerEnsureBmModalStyle(){}, pythonWorkerCloseOwnBmModal(){},
    pythonWorkerSelectionRefresh(){},pythonWorkerSetText:(id,text)=>messages.push({id,text}),
    async pythonWorkerMapLimit(items,limit,fn){for(const item of items)await fn(item);},
    async pythonWorkerBridge(payload){calls.push(payload);return {preflight:auth.result};}};
  vm.createContext(auth);
  const preStart=source.indexOf('function pythonWorkerIsProfileAuthBlockedCode(');
  vm.runInContext(source.slice(preStart,source.indexOf('\nfunction pythonWorkerCurrentStep',preStart)),auth);
  const filterStart=source.indexOf('async function pythonWorkerFilterFanPageReadyProfiles(');
  vm.runInContext(source.slice(filterStart,source.indexOf('\nasync function pythonWorkerStartAutoRkFanPages',filterStart)),auth);
  const createStart=source.indexOf('async function pythonWorkerStartFanPages(');
  vm.runInContext(source.slice(createStart,source.indexOf('\nwindow.pythonWorkerStartFanPages',createStart)),auth);
  for(const code of ['CHECKPOINT_REQUIRED','SESSION_EXPIRED','TWO_FACTOR_REQUIRED','FACEBOOK_TEMPORARILY_BLOCKED']){
    auth.result={ok:true,facebook_session_ready:false,auth_blocked:true,auth_error_code:code,
      browser_business:{error_code:code,error:'Original Facebook reason'}};
    calls.length=0;
    await assert.rejects(auth.pythonWorkerStartFanPages({profiles:['9'],configs:{'9':{base_name:'Page',category:'Digital creator',count:1}}}),
      error=>error.message.includes('9: '+code+': Original Facebook reason') &&
        (code==='CHECKPOINT_REQUIRED'||!error.message.includes('CHECKPOINT_REQUIRED')));
    assert.deepEqual(calls.map(p=>p.action),['preflight']);
    assert.equal(calls[0].purpose,'fan_pages');
    assert.equal(authState.jobId,'previous-failed-job');
    assert.equal(messages.some(m=>m.id==='pythonPwJob'&&m.text===''),false);
  }
  auth.result={ok:true,facebook_session_ready:true,auth_blocked:false,
    browser_business:{session_ready:true,error_code:'BUSINESS_CREATE_UI_UNAVAILABLE',error:'BM create unavailable',
      page_discovery_error_code:'SESSION_EXPIRED',page_discovery_error:'Page redirected to login'}};
  await assert.rejects(auth.pythonWorkerProfilePreflight('9'),/SESSION_EXPIRED: Page redirected to login/);
  auth.result={ok:true,auth_blocked:true,auth_error_code:'TWO_FACTOR_REQUIRED',
    browser_business:{error_code:'TWO_FACTOR_REQUIRED',error:'Original 2FA reason'}};
  await auth.pythonWorkerOpenOwnFanPageModal();
  calls.length=0;
  await elms.find(e=>e.textContent==='Проверить FB-сессию').events.click();
  assert.deepEqual(calls.map(p=>p.action),['preflight']);
  const status=elms.find(e=>e.className==='pwbm-status');
  assert.match(status.textContent,/9: TWO_FACTOR_REQUIRED: Original 2FA reason/);
  assert.doesNotMatch(status.textContent,/checkpoint|CHECKPOINT_REQUIRED/i);
  assert.match(status.textContent,/FP Job не создавался/);
  console.log('FP preflight reasons and read-only session check passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
const start = source.indexOf('async function pythonWorkerOpenAutoModal()');
const end = source.indexOf('async function pythonWorkerStartFanPages', start);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.value = ''; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); }
  addEventListener(kind, cb) { this.events[kind] = cb; }
}
const body = new Element('body'); const elements = [];
const captured = [];
const state = {workerOnline:true, busy:false};
const storage = new Map();
const sandbox = {document:{body,createElement(tag) { const el = new Element(tag); elements.push(el); return el; }},
  crypto:webcrypto, pythonWorkerUiState:state, pythonWorkerSelectedProfiles:()=>['7','8'],
  pythonWorkerEnsureBmModalStyle(){},pythonWorkerCloseOwnBmModal(){},pythonWorkerSelectionRefresh(){},
  pythonWorkerClearBatchState(){},pythonWorkerSetText(){},localStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},
  async pythonWorkerBridge(payload) { captured.push(JSON.parse(JSON.stringify(payload))); if (captured.length === 1) throw Error('lost response'); return {job:{job_id:'saved-job'}}; },
  async pythonWorkerPoll(){}
};
vm.createContext(sandbox); vm.runInContext(source.slice(start,end), sandbox);
(async()=>{
  await sandbox.pythonWorkerOpenAutoModal();
  const inputs = elements.filter(el=>el.tag==='input');
  const create = elements.find(el=>el.textContent==='Prepare');
  const summary = elements.find(el=>el.className==='pwbm-status');
  assert.equal(create.disabled,false); assert.match(summary.textContent,/2 проф\., 1 FP \+ 1 BM \+ 1 РК на профиль \(1 BM = 1 РК\)\. Всего комплектов: 2/);
  inputs[0].value='21'; inputs[0].events.input(); assert.equal(create.disabled,true);
  assert.equal(inputs[0].max,'2', 'new Prepare jobs never request a third BM');
  inputs[0].value='4'; inputs[0].events.input(); assert.equal(create.disabled,true);
  inputs[0].value='2'; inputs[0].events.input(); assert.match(summary.textContent,/2 FP \+ 2 BM \+ 2 РК/); assert.match(summary.textContent,/Всего комплектов: 4/);
  await create.events.click(); assert.equal(state.busy,false); assert.equal(create.textContent,'Повторить отправку');
  assert.equal(inputs.every(el=>el.disabled),true);
  assert.ok(storage.has('remask_python_worker_prepare_pending_v1'));
  elements.length=0;
  sandbox.pythonWorkerSelectedProfiles=()=>['99'];
  await sandbox.pythonWorkerOpenAutoModal();
  const restored = elements.find(el=>el.textContent==='Повторить отправку');
  assert.ok(restored); assert.ok(elements.some(el=>/профилей 7, 8/.test(el.textContent||'')));
  assert.equal(elements.filter(el=>el.tag==='input')[0].value,'2');
  await restored.events.click(); assert.equal(state.jobId,'saved-job');
  assert.equal(storage.has('remask_python_worker_prepare_pending_v1'),false);
  assert.deepEqual(captured[0],captured[1], 'lost response retry must not change the accepted Job');
  assert.equal(captured[0].profiles.length,2);
  assert.equal(captured[0].profiles[0].tasks[0].action,'prepare');
  assert.equal(captured[0].profiles[0].tasks[0].payload.desired.ad_accounts,2);
  assert.equal(captured[0].profiles[0].tasks[0].payload.desired.payment,true);
  assert.equal(captured[0].profiles[0].tasks[0].payload.auto_generate,undefined);
  assert.equal(captured[0].profiles[0].tasks[0].payload.batch_count,undefined);
  assert.equal(captured[0].profiles[0].tasks[0].payload.scope_key,'prepare:7');
  assert.equal(captured[0].profiles[0].tasks[0].idempotency_key,'prepare:7');
  assert.equal(captured[0].profiles[1].tasks[0].payload.scope_key,'prepare:8');
  // Independent BM: an empty optional Page must not block submission or
  // turn different explicit CREATE requests into the same empty-Page scope.
  const bmStart=source.indexOf('async function pythonWorkerStartBusiness(');
  const bmEnd=source.indexOf('\nfunction pythonWorkerVisible(',bmStart);
  vm.runInContext(source.slice(bmStart,bmEnd),sandbox);
  const bmRequests=[];
  sandbox.pythonWorkerBridge=async payload=>{bmRequests.push(JSON.parse(JSON.stringify(payload)));return {job:{job_id:'bm-job'}};};
  state.busy=false;
  await sandbox.pythonWorkerStartBusiness('',{profiles:['7'],configs:{'7':{name:'Independent BM',user_email:'owner@example.com'}}});
  const bm=bmRequests[0].profiles[0].tasks[0].payload;
  assert.equal(bm.parameters.BUSINESS.attach_page,false);
  assert.equal(bm.parameters.BUSINESS.page_id,undefined);
  assert.match(bm.scope_key,/^add-bm-[0-9]+-/);
  state.busy=false;
  await sandbox.pythonWorkerStartBusiness('',{profiles:['7'],configs:{'7':{name:'Referenced BM',page_id:'222222222'}}});
  const withPage=bmRequests[1].profiles[0].tasks[0].payload;
  assert.equal(withPage.parameters.BUSINESS.attach_page,false);
  assert.equal(withPage.parameters.BUSINESS.page_id,'222222222');
  assert.match(withPage.scope_key,/^add-bm-[0-9]+-/);
  assert.notEqual(withPage.scope_key,bm.scope_key);
  state.busy=false;
  await sandbox.pythonWorkerStartBusiness('',{profiles:['7'],configs:{'7':{name:'Another independent BM',page_id:'222222222'}}});
  assert.notEqual(bmRequests[2].profiles[0].tasks[0].payload.scope_key,withPage.scope_key,
    'two explicit independent BM creations must not collapse to the selected Page scope');
  // Exercise the actual capture-phase interceptor with the production menu
  // caption. "Добавить RK" previously fell through to legacy Graph create.
  const interceptStart=source.indexOf('function pythonWorkerInstallBusinessAddRkInterceptor()');
  const interceptEnd=source.indexOf('\nfunction pythonWorkerInitUi()',interceptStart);
  let intercept, opened=0;
  sandbox.document.addEventListener=(kind,cb,capture)=>{
    assert.equal(kind,'click'); assert.equal(capture,true); intercept=cb;
  };
  sandbox.state={activeTab:'businesses'};
  let bmTargets=[{profile_id:'7',business_id:'2478360152656679'}];
  sandbox.pythonWorkerSelectedBusinessTargets=()=>bmTargets;
  sandbox.pythonWorkerOpenSelectedBusinessAdAccountModal=async()=>{opened++;};
  vm.runInContext(source.slice(interceptStart,interceptEnd),sandbox);
  sandbox.pythonWorkerInstallBusinessAddRkInterceptor();
  function menuClick(label) {
    const flags=[];
    const event={target:{closest:()=>({textContent:label})},
      preventDefault(){flags.push('prevent');},
      stopPropagation(){flags.push('stop');},
      stopImmediatePropagation(){flags.push('immediate');}};
    intercept(event);
    return flags;
  }
  for (const label of ['Добавить RK','Добавить РК','Добавить рекламный кабинет','Добавить рекламные кабинеты','Add RK','Add ad account']) {
    const n=opened;
    assert.deepEqual(menuClick(label),['prevent','stop','immediate'],label);
    assert.equal(opened,n+1,label+' must open the worker modal');
  }
  const n=opened;
  assert.deepEqual(menuClick('Создать RK'),[]);
  sandbox.state.activeTab='profiles';
  assert.deepEqual(menuClick('Добавить RK'),[]);
  sandbox.state.activeTab='businesses'; bmTargets=[];
  assert.deepEqual(menuClick('Добавить RK'),[]);
  assert.equal(opened,n,'unrelated clicks must not open Add RK');
  // Failed Page inventory reads must not be converted into "no Pages"
  // and trigger unexpected FP CREATE Jobs.
  const fpStart=source.indexOf('async function pythonWorkerStartAutoRkFanPages()');
  const fpEnd=source.indexOf('\nwindow.pythonWorkerStartAutoRkFanPages',fpStart);
  vm.runInContext(source.slice(fpStart,fpEnd),sandbox);
  const fpTarget={profile_id:'7',business_id:'2478360152656679',ad_account_id:'123456789'};
  sandbox.pythonWorkerSelectedAdAccountTargets=()=>[fpTarget];
  sandbox.pythonWorkerMapLimit=async(items,limit,fn)=>{for(let i=0;i<items.length;i++)await fn(items[i],i);};
  sandbox.pythonWorkerProfilePreflight=async()=>({});
  sandbox.pythonWorkerFpAuthBlockedMessage=()=> '';
  let fpJobs=[];
  sandbox.pythonWorkerStartRkFanPageTargets=async(...args)=>{fpJobs.push(args);};
  sandbox.pythonWorkerLoadPages=async()=>{throw Error('temporary cache failure');};
  state.busy=false; state.fpResolving=false;
  await assert.rejects(sandbox.pythonWorkerStartAutoRkFanPages(),/не удалось прочитать существующие Pages профиля 7/);
  assert.equal(fpJobs.length,0);
  assert.equal(state.fpResolving,false);
  sandbox.pythonWorkerLoadPages=async()=>[{id:'1289628847574478',name:'Media Shopsw'}];
  await sandbox.pythonWorkerStartAutoRkFanPages();
  assert.equal(fpJobs.length,1);
  assert.equal(fpJobs[0][2]['0'].mode,'attach_existing');
  assert.equal(fpJobs[0][2]['0'].existing_page_id,'1289628847574478');
  sandbox.pythonWorkerLoadPages=async()=>[];
  await sandbox.pythonWorkerStartAutoRkFanPages();
  assert.equal(fpJobs.length,2);
  assert.equal(fpJobs[1][2]['0'].mode,'create','a confirmed empty inventory may create an FP');
  // Completed Add RK batches must respect the individual retry flags. This
  // exercises the actual button refresh and bridge calls, including a mixed
  // batch whose checkpoint sibling must never receive retry_failed.
  const retryButton={disabled:false};
  const checkpoint={status:'FAILED',error_code:'CHECKPOINT_REQUIRED',retryable:true,
    tasks:[{status:'FAILED',retryable:true}],
    provisioning_steps:[{status:'FAILED',result:{phase:'CREATE_NOT_SUBMITTED',cookies:'must not render',url:'https://example.test/?token=secret'}}]};
  const temporary={status:'FAILED',error_code:'NETWORK_ERROR',retryable:true};
  const permanent={status:'FAILED',error_code:'PAGE_ADD_UI_CHANGED',retryable:false};
  const rs={batchJobIds:['checkpoint','temporary','permanent','success'],batchTargets:[],jobId:'',
    busy:false,workerOnline:true,job:{items:[checkpoint]}};
  const calls=[];
  const jobs={checkpoint:{items:[checkpoint]},temporary:{items:[temporary]},
    permanent:{items:[permanent]},success:{items:[{status:'SUCCESS',retryable:true}]}};
  const rb={pythonWorkerUiState:rs,document:{querySelectorAll:()=>[]},
    pythonWorkerSelectedProfiles:()=>[],pythonWorkerEl:id=>id==='pythonProvisionRetry'?retryButton:null,
    pythonWorkerEnsureRkFanPageActions(){},pythonWorkerSetText(){},pythonWorkerPersistBatchState(){},
    async pythonWorkerMapLimit(items,limit,fn){for(const item of items)await fn(item);},
    async pythonWorkerBridge(payload){calls.push(payload);return payload.action==='status'
      ?{job:jobs[payload.job_id]}:{result:{requeued:1}};},
    async pythonWorkerPollAdAccountBatch(){},async pythonWorkerPoll(){}};
  vm.createContext(rb);
  const authStart=source.indexOf('function pythonWorkerIsProfileAuthBlockedCode(');
  vm.runInContext(source.slice(authStart,source.indexOf('\nasync function pythonWorkerProfilePreflight',authStart)),rb);
  const retryHelperStart=source.indexOf('function pythonWorkerItemCanRetry(');
  vm.runInContext(source.slice(retryHelperStart,source.indexOf('\nfunction pythonWorkerErrorText',retryHelperStart)),rb);
  const retryStart=source.indexOf('async function pythonWorkerRetryFailed(');
  vm.runInContext(source.slice(retryStart,source.indexOf('\nfunction pythonWorkerSetBmDialogStatus',retryStart)),rb);
  rb.pythonWorkerSelectionRefresh();
  assert.equal(retryButton.disabled,true,'a checkpoint-only batch must disable retry despite stale true flags');
  rs.job.items=[permanent]; rb.pythonWorkerSelectionRefresh(); assert.equal(retryButton.disabled,true);
  rs.job.items=[checkpoint,temporary]; rb.pythonWorkerSelectionRefresh(); assert.equal(retryButton.disabled,false);
  rs.busy=true; rb.pythonWorkerSelectionRefresh(); assert.equal(retryButton.disabled,true); rs.busy=false;
  for(const code of ['CHECKPOINT_REQUIRED','SESSION_EXPIRED','TWO_FACTOR_REQUIRED','FACEBOOK_TEMPORARILY_BLOCKED']){
    assert.equal(rb.pythonWorkerItemCanRetry({...temporary,error_code:code}),false,code);
    assert.equal(rb.pythonWorkerItemCanRetry({...temporary,tasks:[{status:'FAILED',error_code:code,retryable:true}]}),false,code+' task');
  }
  await rb.pythonWorkerRetryFailed();
  assert.deepEqual(calls.filter(p=>p.action==='retry_failed').map(p=>p.job_id),['temporary']);
  assert.equal(calls.some(p=>p.action==='preflight'),false,'retry must not open another Facebook session');
  rs.batchJobIds=[];rs.jobId='checkpoint';rs.busy=false;rs.job={items:[checkpoint]};calls.length=0;
  await rb.pythonWorkerRetryFailed();assert.equal(calls.length,0,'a direct call must also guard a blocked single Job');
  const details=Array.from(rb.pythonWorkerFailureDetails({...checkpoint,worker_job_id:'saved-job'}));
  assert.deepEqual(details,['Job saved-job','CREATE не отправлен','Повтор недоступен']);
  assert.equal(details.join(' ').includes('secret'),false);
  console.log('Auto/BM/FP interface and checkpoint-safe single/mixed batch retries passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});


// An ambiguous CREATE must show its saved candidate without promoting it to SUCCESS.
{
  const detailsStart = source.indexOf('function pythonWorkerFailureDetails(item)');
  const detailsEnd = source.indexOf('function pythonWorkerSelectionRefresh()', detailsStart);
  const detailsSandbox = {pythonWorkerItemCanRetry:()=>true};
  vm.createContext(detailsSandbox);
  vm.runInContext(source.slice(detailsStart, detailsEnd), detailsSandbox);
  const details = detailsSandbox.pythonWorkerFailureDetails({
    provisioning_steps:[{status:'FAILED',result:{
      phase:'CREATE_RESULT_UNKNOWN', business_id:'1632909278268870',
      create_response_ad_account_id:'act_123456789',
      capture_candidate_verification:[{reason:'inventory_not_confirmed',
        diagnostics:[{exact_name_ids:['123456789'],friendly_name:'InventoryQuery'}]}]
    }}]
  });
  assert.ok(details.includes('РК-кандидат 123456789'));
  assert.ok(details.includes('BM 1632909278268870'));
  assert.ok(details.includes('Проверка: inventory_not_confirmed'));
  assert.ok(!details.some(value=>/SUCCESS/.test(value)));
  const invalid = detailsSandbox.pythonWorkerFailureDetails({
    provisioning_steps:[{status:'FAILED',result:{
      create_response_ad_account_id:'secret=value', business_id:'secret',
      capture_candidate_verification:[{diagnostics:[{exact_name_ids:['secret']}]}]
    }}]
  });
  assert.ok(!invalid.some(value=>value.includes('secret')));
}
