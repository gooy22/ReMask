const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'railway-launch-private-catalog.js'), 'utf8');
const completionSource = fs.readFileSync(path.join(__dirname, '..', 'railway-private-launch-completion.js'), 'utf8');
assert.ok(completionSource.includes('status.textContent=remaskPrivatePageReadFailure(error,profile,account);'),
  'single-RK Launch must render the safe page read failure, including an explicit Meta login gate');

// Reopen the original retained Draft in a browser with empty localStorage.
// A fresh Meta read may fail; it must not create/retry a job, drop receipts,
// enable delivery, or turn accepted campaign data into a completed Draft.
(async()=>{
  class Element {
    constructor(){this.children=[];this.style={};this.events={};this._text='';}
    set textContent(value){this._text=String(value);this.children=[];}
    get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ');}
    set innerHTML(_value){throw new Error('Private job data must use text nodes.');}
    appendChild(child){this.children.push(child);return child;}
    replaceChildren(){this.children=[];this._text='';}
    setAttribute(){}
    addEventListener(name,handler){this.events[name]=handler;}
  }
  const controls=Object.fromEntries(['preflight','syncMeta','loadFunding','fundingStatus',
    'reviewLaunch','launchButton','serverDryRun','dryRunPlan','reviewStatus','launchResult',
    'jobItems','jobSummary','recentJobSelect','jobActionSelect','retryFailed','forceRetryReview',
    'continueJob','refreshInsights','activateJobAds','pauseJobAds'].map(id=>[id,new Element()]));
  const id='a'.repeat(32),profile='4',draft='1000001',campaign='1000002';
  const taskId='b'.repeat(32),otherTaskId='c'.repeat(32);
  const job={id,status:'FAILED',items_total:1,items:[{profile_id:profile,status:'FAILED',tasks:[
    {id:taskId,action:'private_launch',status:'FAILED',attempt:5,error_code:'READ_RELATION_MISMATCH',
      error_message:'<img src=x onerror=alert(1)>',payload:{ad_account_id:'120248891583720408',
        launch:{base:{execution_mode:'DRAFT'}}}},
    {id:otherTaskId,action:'private_launch',status:'FAILED',payload:{ad_account_id:'120249297411010775',
      launch:{base:{execution_mode:'DRAFT'}}}}],advertising_steps:[
    {task_id:taskId,step:'ADS_MANAGER:DRAFT',entity_id:draft,status:'SUCCESS',verified:true},
    {task_id:taskId,step:'ADS_MANAGER:FRAGMENT_CAMPAIGN',entity_id:campaign,status:'RECONCILE_REQUIRED',verified:false}]}]};
  const original=JSON.stringify(job),storage=new Map(),calls=[];
  let legacyRenders=0;
  const ctx={console,URL,queueMicrotask,setTimeout:handler=>queueMicrotask(handler),
    $:name=>controls[name],state:{},fundingState(){},fundingReviewLabel(){},loadFunding:async()=>{},validateReady(){},
    renderJob(){legacyRenders++;},localStorage:{getItem:key=>storage.get(key)||null,
      setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    window:{location:{href:'https://remask.fixture/launch.php?private_job='+id},history:{replaceState(_a,_b,url){ctx.window.location.href=url;}}},
    document:{readyState:'loading',addEventListener(){},createElement:()=>new Element(),
      querySelector:()=>({content:'fixture-csrf'})},
    fetch:async(_url,options)=>{
      const request=JSON.parse(options.body);calls.push(request);
      if(request.action==='status')return {ok:true,json:async()=>({ok:true,job})};
      if(request.action==='private_draft_read')return {ok:false,status:502,
        json:async()=>({ok:false,code:'PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE',message:'Meta requires sign-in'})};
      throw new Error('Unexpected write: '+request.action);
    }};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  await ctx.remaskPrivateRestoreSourceJob();
  assert.match(controls.jobSummary.textContent,/Ошибка/);
  assert.doesNotMatch(controls.jobSummary.textContent,/FAILED/);
  assert.equal(controls.jobItems.children.length,2,'a job may contain two Launch tasks for the same profile');
  const errorCell=controls.jobItems.children[0].children[10];
  assert.match(errorCell.textContent,/Не удалось завершить шаг/);
  assert.doesNotMatch(errorCell.textContent,/^READ_RELATION_MISMATCH/);
  const errorDetails=errorCell.children.find(child=>child.tag==='details');
  assert.ok(errorDetails,'machine details are available on demand');
  assert.equal(Boolean(errorDetails.open),false,'machine details stay collapsed');
  assert.equal(controls.jobItems.children[1].children[5].children.some(child=>
    child.textContent==='Проверить черновик в Meta'),false,'only the task with its own saved draft gets a read button');
  assert.match(controls.jobItems.textContent,new RegExp(draft));
  assert.match(controls.jobItems.textContent,new RegExp(campaign));
  assert.match(controls.jobItems.textContent,/результат не проверен/);
  assert.equal(controls.activateJobAds.disabled,true);
  assert.equal(controls.retryFailed.disabled,true,'legacy retries must not receive a private worker handle');
  assert.equal(JSON.parse(storage.get('remask_private_source_job_v1')).job_id,id);
  assert.equal(controls.recentJobSelect.value,'private:'+id);
  const idsCell=controls.jobItems.children[0].children[5];
  const button=idsCell.children.find(child=>child.textContent==='Проверить черновик в Meta');
  assert.ok(button,'a settled saved Draft offers an explicit read');
  await button.events.click();
  assert.match(idsCell.textContent,/BUSINESS_LOGIN_GATE/);
  assert.match(idsCell.textContent,new RegExp(campaign));
  assert.equal(button.disabled,false);
  assert.equal(JSON.stringify(job),original,'the read failure must preserve the immutable source journal');
  assert.deepEqual(calls.map(row=>row.action),['status','private_draft_read']);
  assert.equal(calls[1].profile_id,profile);assert.equal(calls[1].job_id,id);assert.equal(calls[1].task_id,taskId);
  ctx.renderJob({id:'legacy',items:[]});
  assert.equal(legacyRenders,0,'late legacy init must not erase the restored private result');
  assert.match(controls.jobItems.textContent,new RegExp(campaign));
  assert.match(controls.jobItems.textContent,/BUSINESS_LOGIN_GATE/,'late legacy reads must retain the fresh read failure too');
  let intercepted=false;
  await controls.recentJobSelect.events.change({preventDefault(){},stopImmediatePropagation(){intercepted=true;}});
  assert.equal(intercepted,true,'the private recent entry must not reach the legacy job endpoint');
  assert.deepEqual(calls.map(row=>row.action),['status','private_draft_read','status']);
  controls.recentJobSelect.value='legacy';
  controls.recentJobSelect.events.change({});ctx.renderJob({id:'legacy',items:[]});
  assert.equal(legacyRenders,1,'explicitly selecting a legacy recent job still works');
  calls.length=0;storage.clear();ctx.window.location.href='https://remask.fixture/launch.php?private_job=https://other.invalid';
  await ctx.remaskPrivateRestoreSourceJob();assert.equal(calls.length,0);
  console.log('Private Draft reopens from its exact handle; rejected Meta reads retain receipts without advertising writes.');
})().catch(error=>{console.error(error);process.exitCode=1;});
const workerBridge = fs.readFileSync(path.join(__dirname, '..', 'railway-python-worker-jobs-overlay.php'), 'utf8');
const targetingAutocomplete = fs.readFileSync(path.join(__dirname, '..', 'railway-targeting-autocomplete-overlay.php'), 'utf8');
assert.match(targetingAutocomplete, /const identity = \[input\.id, input\.name, input\.placeholder,/,
  'targeting autocomplete must use stable field identity instead of entered ad copy');
assert.doesNotMatch(targetingAutocomplete, /const hay = `\$\{textOf\(input\)\}/,
  'typing targeting words into Primary text must not create a Meta targeting panel');
assert.doesNotMatch(targetingAutocomplete, /areaHasTargeting\(broad\)/,
  'a search button outside targeting fields must not be removed because some other field targets');
assert.match(workerBridge, /\$request = \$path === 'media-inspect'[\s\S]*\['launch'=>\$input\['launch'\]\]/,
  'the authenticated PHP bridge must forward the exact per-RK launch to media preflight');

(async()=>{
  const overlay=fs.readFileSync(path.join(__dirname,'..','railway-launch-private-catalog-overlay.php'),'utf8');
  const assets=overlay.match(/<<<'ASSET_UI'\n([\s\S]*?)\nASSET_UI/)[1];
  const requests=[];
  let foreign=false,hold=null,partial=false,refreshFailed=false;
  let truncated=false,queryRequired=false,inventoryIncomplete=false;
  const ctx={state:{asset:{profile:'19',accountId:'act_111111111',generation:1,cache:new Map(),inflight:new Map()}},
    post:input=>input,apiJson:async(url,input)=>{
      requests.push({url,input});
      if(hold){const current=hold;hold=null;await current;}
      if(refreshFailed)throw new Error('Synthetic Meta read failure');
      return {ok:true,profile:input.profile,account_id:foreign?'999999999':input.account_id.replace(/^act_/,''),
        resource:input.resource,data:partial?null:[{id:'555555555'}],
        checked_live:input.force==='1',account_scope_verified:true,
        truncated,query_required:queryRequired,inventory_complete:!inventoryIncomplete};
    }};
  vm.createContext(ctx);vm.runInContext(assets,ctx);
  await ctx.fetchAsset('pages');
  assert.equal(requests[0].input.account_id,'act_111111111','Pages must carry the selected RK');
  assert.equal(requests[0].url,'ajax/metaAssetManager.php');
  await ctx.fetchAsset('pages');assert.equal(requests.length,1);
  ctx.state.asset.accountId='act_222222222';ctx.state.asset.generation++;
  await ctx.fetchAsset('pages');assert.equal(requests.length,2,'same-profile Pages cannot reuse another RK cache');
  const savedPages=await ctx.fetchAsset('pages');
  foreign=true;
  await assert.rejects(()=>ctx.fetchAsset('pages',true),/другого профиля/);
  assert.equal(await ctx.fetchAsset('pages'),savedPages,'foreign refresh cannot replace the last scoped list');
  foreign=false;
  let release;hold=new Promise(resolve=>release=resolve);
  const cachedRead=ctx.fetchAsset('images');
  const refreshed=ctx.fetchAsset('images',true);
  release();await cachedRead;
  assert.equal((await refreshed).checked_live,true,'Refresh must perform a fresh READ after an older cached READ');
  const imageRequests=requests.filter(r=>r.input.resource==='images');
  assert.deepEqual(imageRequests.map(r=>r.input.force),['0','1']);
  const confirmed=await ctx.fetchAsset('images');
  refreshFailed=true;
  await assert.rejects(()=>ctx.fetchAsset('images',true),/Synthetic Meta read failure/);
  refreshFailed=false;
  assert.equal(await ctx.fetchAsset('images'),confirmed,'failed refresh must retain the last confirmed list');
  partial=true;
  await assert.rejects(()=>ctx.fetchAsset('images',true),/не полностью/);
  partial=false;
  assert.equal(await ctx.fetchAsset('images'),confirmed,'partial response must not erase confirmed assets');
  for(const incomplete of ['truncated','queryRequired','inventoryIncomplete']){
    if(incomplete==='truncated')truncated=true;
    if(incomplete==='queryRequired')queryRequired=true;
    if(incomplete==='inventoryIncomplete')inventoryIncomplete=true;
    await assert.rejects(()=>ctx.fetchAsset('images',true),/не полностью/,
      incomplete+' forced READ must not be treated as a complete account inventory');
    truncated=false;queryRequired=false;inventoryIncomplete=false;
    assert.equal(await ctx.fetchAsset('images'),confirmed,
      incomplete+' must preserve the previous live-confirmed inventory');
  }
  for(const incomplete of ['truncated','queryRequired','inventoryIncomplete']){
    if(incomplete==='truncated')truncated=true;
    if(incomplete==='queryRequired')queryRequired=true;
    if(incomplete==='inventoryIncomplete')inventoryIncomplete=true;
    await assert.rejects(()=>ctx.fetchAsset('videos'),/не полностью/,
      incomplete+' unforced READ must not be cached as a complete list');
    assert.equal(ctx.state.asset.cache.has('19|videos|222222222'),false);
    truncated=false;queryRequired=false;inventoryIncomplete=false;
  }
  ctx.state.asset.accountId='';
  await assert.rejects(()=>ctx.fetchAsset('pages'),/Выберите один РК/);
  console.log('Private Workspace Assets keep exact RK scope, reject foreign responses and refresh independently.');
})().catch(e=>{console.error(e);process.exitCode=1;});
const controls = Object.fromEntries(['preflight','syncMeta','loadFunding','fundingStatus','reviewLaunch','launchButton','serverDryRun','dryRunPlan','reviewStatus'].map(id=>[id,{}]));
const context = {$:id=>controls[id],fundingState(){},fundingReviewLabel(){},loadFunding:async()=>{},validateReady(){}};
vm.createContext(context); vm.runInContext(source,context);
const label = data=>context.fundingState({data}).label;
assert.equal(context.fundingState(null).label,'NOT LOADED');
assert.equal(context.fundingState({error:'failed'}).label,'ERROR');
assert.equal(label({funding_verified:false,account_status:1,funding_source:'123'}),'NOT CHECKED');
assert.equal(label({verification_status:'NOT_CHECKED'}),'NOT CHECKED');
assert.equal(label({account_status:1,_cache:{stale:true},funding_source:'123'}),'STALE');
assert.equal(label({funding_source:'123'}),'UNKNOWN');
assert.equal(label({account_status:2,funding_source:'123'}),'ACCOUNT 2');
assert.equal(label({account_status:1,expired_funding_source_details:{id:'123'}}),'EXPIRED');
assert.equal(label({account_status:1,is_prepay_account:true,balance:0}),'PREPAY EMPTY');
assert.equal(label({account_status:1,is_prepay_account:true}),'PREPAY EMPTY');
assert.equal(label({account_status:1,is_prepay_account:true,balance:100}),'PREPAY BALANCE');
assert.equal(label({account_status:1,funding_source_details:{id:'123'}}),'SOURCE SAVED');
// Launch exposes one draft action. Intermediate review buttons and diagnostics
// stay out of the operator UI; the save handler still runs validation internally.
assert.equal(controls.launchButton.disabled,false);
assert.equal(controls.reviewLaunch.hidden,true);
assert.equal(controls.dryRunPlan.hidden,true);
assert.equal(controls.serverDryRun.hidden,true);
assert.equal(controls.launchButton.textContent,'Сохранить черновик');
assert.equal(Boolean(controls.reviewLaunch.title),false);
assert.equal(Boolean(controls.reviewStatus.textContent),false);
assert.equal(controls.preflight.textContent,'Загрузить сохранённые РК');
assert.equal(context.remaskPrivatePageReadFailure({message:'Meta requires sign-in',payload:{code:'PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE',message:'Meta requires sign-in'}},'15','act_120249392950240046'),
  'Профиль 15 / РК 120249392950240046: Meta вернула BUSINESS_LOGIN_GATE; список FP не загружен.');
const safePageFailure=context.remaskPrivatePageReadFailure({message:'xs=PRIVATE_SECRET'},'15','act_120249392950240046');
assert.match(safePageFailure,/PAGE_READ_FAILED/);
assert.doesNotMatch(safePageFailure,/PRIVATE_SECRET/);
console.log('Private Launch catalog and funding truth checks passed');

// Direct Launch uses the visible Page control; even an old per-RK binding
// must never override it. Workspace/bulk Launch uses exact target bindings.
{
  const ids=['120254071182230729'];
  const page={value:'1421882841009910'};
  let perTarget=false;
  const binding={page_id:'999999999',source_instagram_media_id:'888888888'};
  const ctx={...context,$:id=>id==='page'?page:controls[id],
    state:{profile:'36',accounts:[{id:ids[0],business_id:'1449666707266277'}]},
    targetMode:()=>perTarget,bindingFor:()=>binding};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  const config={accountIds:ids,payload:{creative:{page_id:page.value}}};
  assert.equal(ctx.remaskPrivateExecutionMode(config),'DRAFT',
    'when the visible selector is absent, stored configuration cannot imply PUBLISH');
  assert.equal(ctx.remaskPrivateTargetRows(config)[0].launch.base.execution_mode,'DRAFT');
  const precomputed={...config,_targets:[{launch:{base:{execution_mode:'PUBLISH'},override:{}}}]};
  assert.throws(()=>ctx.remaskPrivateTargetRows(precomputed),/Режим создания изменился/,
    'a saved precomputed PUBLISH target must never bypass a DRAFT UI choice');
  precomputed._targets[0].launch.base.execution_mode='DRAFT';
  assert.equal(ctx.remaskPrivateTargetRows(precomputed)[0].launch.base.execution_mode,'DRAFT');
  assert.equal(ctx.remaskPrivateTargetRows(config)[0].page_id,page.value,
    'single RK must submit the selected visible Page, not a stale/empty bulk binding');
  config.payload.creative.format='INSTAGRAM_POST';
  config.payload.creative.source_instagram_media_id='123456789';
  assert.equal(ctx.remaskPrivateTargetRows(config)[0].launch.override.creative.source_instagram_media_id,'123456789');
  delete config.payload.creative.format;
  delete config.payload.creative.source_instagram_media_id;
  delete binding.page_id;
  assert.equal(ctx.remaskPrivateTargetRows(config)[0].page_id,page.value);
  page.value='';
  assert.throws(()=>ctx.remaskPrivateTargetRows(config),/Выберите страницу/,
    'a saved payload must not supply a Page after the user clears the selector');
  page.value='1421882841009910';perTarget=true;
  assert.throws(()=>ctx.remaskPrivateTargetRows(config),/Выберите страницу/,
    'even a one-RK workspace target must not borrow the hidden single Page');
  binding.page_id='777777777';
  assert.equal(ctx.remaskPrivateTargetRows(config)[0].page_id,'777777777');
  ids.push('120254071182230730');
  ctx.bindingFor=id=>id===ids[0]?binding:{};
  ctx.state.accounts.push({id:ids[1],business_id:'1449666707266277'});
  assert.throws(()=>ctx.remaskPrivateTargetRows(config),/120254071182230730/,
    'bulk targets with a missing Page must fail rather than reuse another RK Page');
  console.log('Direct and bulk Launch Page sources stay separate, including one-RK workspace mode.');
}

(async()=>{
  const buttons=Object.fromEntries(
    ['preflight','syncMeta','loadFunding','fundingStatus','reviewLaunch','launchButton','serverDryRun','dryRunPlan','reviewStatus','launchResult','adAccount']
      .map(id=>[id,{disabled:false,title:'',textContent:''}])
  );
  buttons.adAccount.options=[
    {value:'act_111111111',dataset:{profile:'7'}},
    {value:'act_222222222',dataset:{profile:'7'}}
  ];
  const storage=new Map(),requests=[],shown=[];
  let captureHandler=null;
  let mediaMode='warn';
  const bindings={
    '111111111':{page_id:'333333333'},
    '222222222':{page_id:'444444444'}
  };
  // Existing publication-flow fixtures deliberately opt into publish.
  // A missing selector must never carry implicit publication intent.
  buttons.remaskPrivateExecutionMode={value:'PUBLISH'};
  const config={
    accountIds:['111111111','222222222'],
    payload:{
      campaign:{name:'Campaign'},
      adset:{name:'AdSet',targeting:{age_min:18,age_max:55}},
      creative:{name:'Creative',message:'base'},
      ad:{name:'Ad'}
    },
    accountOverrides:{
      '111111111':{creative:{message:'one'}},
      '222222222':{adset:{targeting:{age_max:35}}}
    }
  };
  const privateCtx={
    $:id=>buttons[id],
    fundingState(){},fundingReviewLabel(){},loadFunding:async()=>{},
    validateReady(){},
    state:{
      profile:'7',processingJob:false,
      accounts:[
        {id:'111111111',_profile:'7',business_id:'555555555'},
        {id:'222222222',_profile:'7',business_id:'666666666'}
      ],
      targetBindings:bindings
    },
    selectedAccountIds:()=>config.accountIds,
    currentLaunchConfigForRequest:()=>config,
    targetMode:()=>true,
    targetForAccount:id=>{
      const clean=String(id).replace(/^act_/,'');
      return clean==='111111111'
        ?{profile:'7',business_id:'555555555',account_id:clean}
        :{profile:'7',business_id:'666666666',account_id:clean};
    },
    bindingFor:id=>bindings[String(id).replace(/^act_/,'')]||{},
    show:(target,text)=>{target.textContent=text;shown.push(text);},
    document:{querySelector:()=>({content:'fixture-csrf'}),addEventListener(kind,handler,capture){
      if(kind==='click'&&capture===true)captureHandler=handler;
    }},
    localStorage:{
      getItem:key=>storage.get(key)||null,
      setItem:(key,value)=>storage.set(key,value),
      removeItem:key=>storage.delete(key)
    },
    crypto:{getRandomValues(values){for(let i=0;i<values.length;i++)values[i]=i+1;return values;}},
    Uint32Array,
    fetch:async(url,options)=>{
      assert.equal(options.headers['X-ReMask-CSRF'],'fixture-csrf','plans and worker jobs require the page session CSRF token');
      const body=JSON.parse(options.body);requests.push({url,body});
      if(body.action==='private_launch_contracts')
        return {ok:true,status:200,json:async()=>({ok:true,contracts:{
          CAMPAIGN:{configured:true,active:true,verification_configured:true,friendly_name:'CampaignMutation'},
          ADSET:{configured:true,active:true,verification_configured:true,friendly_name:'AdSetMutation'},
          CREATIVE:{configured:true,active:true,verification_configured:true,friendly_name:'CreativeMutation'},
          AD:{configured:true,active:true,verification_configured:true,friendly_name:'AdMutation'}
        }})};
      if(body.action==='private_launch_media_inspect'){
        if(['auth401','auth403','rate_limit'].includes(mediaMode)){
          const status=mediaMode==='auth401'?401:(mediaMode==='auth403'?403:429);
          return {ok:false,status,json:async()=>({ok:false,error:status===429?'Rate limited':'Unauthorized'})};
        }
        const facts={media_type:'image',encoding:'PNG',mime_type:'image/png',width:1200,height:600,
          aspect_ratio:2,size_bytes:1234,requires_conversion:false};
        const compatibility=mediaMode==='blocked'
          ?{locally_valid:false,code:'MEDIA_PREPARATION_REQUIRED',
            message:'Prepare this encoding as PNG/JPEG or MP4 H.264/AAC before Launch',warnings:[],facts}
          :{locally_valid:true,meta_acceptance_verified:false,facts,warnings:[{
            code:'MEDIA_PLACEMENT_LAYOUT_REVIEW',placement:'instagram:story',preferred_ratio:0.5625,
            message:'Review cropping/padding for this placement',meta_rejection_established:false
          }]};
        return {ok:true,status:200,json:async()=>({ok:true,media:[{
          id:'m-'+'a'.repeat(24),mime_type:'image/png',inspection:facts,compatibility
        }]})};
      }
      if(body.action==='private_launch_review')
        return {ok:true,status:200,json:async()=>({ok:true,review:{
          ready:true,
          configuration_valid:true,
          checked_live:true,
          api_preflight_ready:true,
          editor_state:'EDITOR_UNVERIFIED',
          missing_fields:[],
          missing_contracts:[],
          profile_id:body.profile_id,
          preflight:{
            selected_payment_method:{
              type:body.ad_account_id==='111111111'?'Visa':'Mastercard',
              last4:body.ad_account_id==='111111111'?'1111':'2222',
              linkage_status:'OBSERVED'
            },
            payment_selection_source:'single_live_method'
          }
        }})};
      if(body.action==='create')
        return {ok:true,status:200,json:async()=>({ok:true,job:{job_id:'private-job-1'}})};
      return {ok:true,status:200,json:async()=>({ok:true,job:{status:'SUCCESS',items_total:2,items_done:2}})};
    }
  };
  vm.createContext(privateCtx);vm.runInContext(source,privateCtx);

  assert.equal(buttons.launchButton.disabled,false,'structurally complete private selection should enable Launch');
  assert.equal(buttons.reviewLaunch.hidden,true);
  privateCtx.state.processingJob=true;
  privateCtx.validateReady();
  assert.equal(buttons.launchButton.disabled,true,'busy worker must still prevent duplicate submission');
  assert.equal(buttons.reviewLaunch.hidden,true);
  privateCtx.state.processingJob=false;
  privateCtx.validateReady();
  assert.equal(buttons.launchButton.disabled,false);
  assert.equal(typeof captureHandler,'function');
  const routed=[];
  const originals=[privateCtx.remaskPrivateReviewLive,privateCtx.remaskPrivateReviewOnly,privateCtx.remaskPrivateCreateJob];
  privateCtx.remaskPrivateReviewLive=()=>routed.push('meta-readiness');
  privateCtx.remaskPrivateReviewOnly=()=>routed.push('local-plan');
  privateCtx.remaskPrivateCreateJob=()=>routed.push('create');
  for(const id of ['reviewLaunch','dryRunPlan','serverDryRun']){
    let stopped=0;
    captureHandler({target:{closest:selector=>selector==='#launchButton'&&id==='launchButton'?({id,disabled:false}):null},
      preventDefault(){stopped++;},stopPropagation(){stopped++;},stopImmediatePropagation(){stopped++;}});
    assert.equal(stopped,0,'hidden review controls are not user actions');
  }
  let stopped=0;
  captureHandler({target:{closest:selector=>selector==='#launchButton'?({id:'launchButton',disabled:false}):null},
    preventDefault(){stopped++;},stopPropagation(){stopped++;},stopImmediatePropagation(){stopped++;}});
  assert.equal(stopped,3,'the Save Draft action owns Launch submission');
  assert.deepEqual(routed,['create']);
  [privateCtx.remaskPrivateReviewLive,privateCtx.remaskPrivateReviewOnly,privateCtx.remaskPrivateCreateJob]=originals;

  const originalFetch=privateCtx.fetch;
  privateCtx.fetch=async()=>({ok:true,json:async()=>({ok:true,contracts:{
    CAMPAIGN:{configured:true,active:false,verification_configured:true},
    ADSET:{configured:true,active:true,verification_configured:false},
    CREATIVE:{configured:true,active:true,verification_configured:true},
    AD:{configured:true,active:true,verification_configured:true}
  }})});
  const blocked=await privateCtx.remaskPrivateContractStatus({render:true});
  assert.equal(blocked.ready,false);
  assert.deepEqual(Array.from(blocked.missing,r=>r.step),['CAMPAIGN','ADSET']);
  assert.doesNotMatch(buttons.launchResult.textContent,/doc_id|mutation|contracts/i);
  privateCtx.fetch=originalFetch;
  const reviewed=await privateCtx.remaskPrivateReviewConfig(config,{render:false});
  assert.equal(reviewed.length,2);
  assert.deepEqual(requests.map(r=>r.body.action),['private_launch_contracts','private_launch_review','private_launch_review']);
  assert.ok(requests.every(r=>r.url==='ajax/pythonWorkerJobs.php'));
  const reviewRequests=requests.filter(r=>r.body.action==='private_launch_review');
  assert.deepEqual(reviewRequests.map(r=>r.body.page_id),['333333333','444444444']);
  assert.deepEqual(reviewRequests.map(r=>r.body.business_id),['555555555','666666666']);
  assert.equal(reviewRequests[0].body.launch.override.creative.message,'one');
  assert.equal(reviewRequests[1].body.launch.override.adset.targeting.age_max,35);
  assert.equal(reviewed[0].row.selected_payment_method.last4,'1111');
  assert.equal(reviewed[1].row.selected_payment_method.last4,'2222');
  assert.equal(reviewed[0].review.checked_live,true);
  assert.equal(reviewed[0].review.editor_state,'EDITOR_UNVERIFIED',
    'API readiness must remain separate from native editor acceptance');
  assert.deepEqual(Array.from(privateCtx.state.privateLaunchReadiness.targets,row=>row.editor_state),
    ['EDITOR_UNVERIFIED','EDITOR_UNVERIFIED']);
  assert.deepEqual(Array.from(privateCtx.state.privateLaunchReadiness.targets,row=>row.checked_live),[true,true]);
  assert.ok(requests.every(r=>r.body.doc_id===undefined&&r.body.fb_dtsg===undefined));

  // Real media preflight runs before the account/Meta readiness request and uses each target's exact launch.
  const mediaConfig=JSON.parse(JSON.stringify(config));
  const mediaId='m-'+'a'.repeat(24);
  mediaConfig.payload.creative.media_library_id=mediaId;
  mediaMode='warn';
  const mediaStart=requests.length;
  const mediaReviewed=await privateCtx.remaskPrivateReviewConfig(mediaConfig,{render:false});
  assert.ok(mediaReviewed.every(row=>row.status==='READY'&&row.media_checks?.length===1));
  assert.ok(mediaReviewed.every(row=>row.media_checks[0].compatibility.warnings.length===1),
    'placement recommendations must survive the UI review result');
  const mediaActions=requests.slice(mediaStart).map(row=>row.body.action);
  assert.deepEqual(mediaActions,[
    'private_launch_contracts','private_launch_media_inspect','private_launch_review',
    'private_launch_media_inspect','private_launch_review'
  ]);
  assert.ok(requests.slice(mediaStart).filter(row=>row.body.action==='private_launch_media_inspect')
    .every(row=>row.body.launch?.base?.creative?.media_library_id===mediaId),
    'media preflight must inspect the exact managed media selected for each target');

  mediaMode='blocked';
  const blockedStart=requests.length;
  const mediaBlocked=await privateCtx.remaskPrivateReviewConfig(mediaConfig,{render:false});
  assert.ok(mediaBlocked.every(row=>row.status==='MEDIA_BLOCKED'&&
    row.error_code==='MEDIA_PREPARATION_REQUIRED'&&row.review_attempted===false),
    'unsupported encoding must block before Meta readiness and carry a clear media error');
  assert.deepEqual(requests.slice(blockedStart).map(row=>row.body.action),[
    'private_launch_contracts','private_launch_media_inspect','private_launch_media_inspect'
  ],'a local media failure must not trigger the Meta review request');
  for(const [mode,status,errorCode] of [
    ['auth401','AUTH_REQUIRED','REMASK_AUTH_REQUIRED'],
    ['auth403','AUTH_REQUIRED','REMASK_AUTH_REQUIRED'],
    ['rate_limit','RATE_LIMITED','META_RATE_LIMITED']
  ]){
    mediaMode=mode;
    const stoppedAt=requests.length;
    const stopped=await privateCtx.remaskPrivateReviewConfig(mediaConfig,{render:false});
    assert.ok(stopped.every(row=>row.status===status&&row.error_code===errorCode&&row.review_attempted===false),
      mode+' during media preflight must retain the batch-stop classification');
    assert.deepEqual(requests.slice(stoppedAt).map(row=>row.body.action),[
      'private_launch_contracts','private_launch_media_inspect'
    ],mode+' during media preflight must stop further Meta review reads');
  }
  mediaMode='warn';

  const planned=privateCtx.remaskPrivateJobRequest(config,reviewed);
  assert.equal(planned.request.action,'create');
  assert.equal(planned.request.profiles.length,2);
  assert.ok(planned.request.profiles.every(row=>row.tasks.length===1&&row.tasks[0].action==='private_launch'));
  assert.deepEqual(Array.from(planned.request.profiles,row=>row.tasks[0].payload.page_id),['333333333','444444444']);
  assert.ok(planned.request.profiles.every(row=>row.tasks[0].payload.launch.base.campaign.name==='Campaign'));
  assert.deepEqual(
    Array.from(planned.request.profiles,row=>row.tasks[0].payload.selected_payment_method.last4),
    ['1111','2222']
  );
  assert.ok(planned.request.profiles.every(row=>row.tasks[0].payload.doc_id===undefined));

  buttons.remaskPrivateExecutionMode.value='DRAFT';
  const draftRows=privateCtx.remaskPrivateTargetRows(config);
  assert.ok(draftRows.every(row=>row.selected_payment_method===undefined
      && row.launch.base.execution_mode==='DRAFT'),
    'the explicit draft selector must omit payment selection and be serialized');
  buttons.remaskPrivateExecutionMode.value='PUBLISH';

  const reused=privateCtx.remaskPrivateJobRequest(config,reviewed);
  assert.equal(reused.reused,true);
  assert.equal(JSON.stringify(reused.request),JSON.stringify(planned.request),'lost response must reuse the identical worker Job request');

  assert.equal(storage.has('remask_private_launch_pending_v1'),true);
  assert.equal(privateCtx.remaskPrivateFinalizeJob({status:'FAILED'}),'FAILED');
  assert.equal(storage.has('remask_private_launch_pending_v1'),true,
    'failed/partial jobs must retain the exact idempotency request');
  assert.equal(privateCtx.remaskPrivateFinalizeJob({status:'PARTIAL'}),'PARTIAL');
  assert.equal(storage.has('remask_private_launch_pending_v1'),true);
  assert.equal(privateCtx.remaskPrivateFinalizeJob({status:'SUCCESS'}),'SUCCESS');
  assert.equal(storage.has('remask_private_launch_pending_v1'),true,
    'unrelated or anonymous success cannot release a pending submission');
  assert.equal(privateCtx.remaskPrivateFinalizeJob({status:'SUCCESS',idempotency_key:planned.request.idempotency_key}),'SUCCESS');
  assert.equal(storage.has('remask_private_launch_pending_v1'),false,
    'only matching terminal SUCCESS may clear the pending request');

  // Recreate one pending request so the explicit clear helper stays covered.
  privateCtx.remaskPrivateJobRequest(config,reviewed);
  assert.equal(storage.has('remask_private_launch_pending_v1'),true);
  privateCtx.remaskPrivatePendingClear();
  assert.equal(storage.has('remask_private_launch_pending_v1'),false);

  privateCtx.creativeFormat=()=> 'CAROUSEL';
  config.payload.creative.official_params={};
  config.accountOverrides['111111111'].creative.official_params={};
  privateCtx.carouselFiles=()=>[];
  privateCtx.carouselCardsPayload=()=>[{headline:'First',link:'https://example.com/a'},
    {headline:'Second',link:'https://example.com/b'}];
  vm.runInContext(`let remaskSelectedCreativePreset={format:'CAROUSEL',carousel:[
    {media_library_id:'m-first'},{media_library_id:'m-second'}]}`,privateCtx);
  config.payload._meta_official_v1=true;
  const carousel=await privateCtx.remaskPrivatePrepareConfig();
  assert.equal(carousel.payload._meta_official_v1,undefined,
    'the legacy official-builder marker must not be serialized into private Launch sections');
  assert.equal(carousel._targets[0].launch.base._meta_official_v1,undefined,
    'the exact per-RK draft payload must omit UI-only root markers');
  assert.equal(config.payload._meta_official_v1,true,
    'private preparation must not mutate the visible Launch form');
  delete config.payload._meta_official_v1;
  assert.equal(carousel.payload.creative.official_params,undefined,'omit empty optional maps before the associative PHP bridge');
  assert.equal(carousel._targets[0].launch.override.creative.official_params,undefined);
  assert.equal(JSON.stringify(config.payload.creative.official_params),'{}','preparation must preserve the original form');
  delete config.payload.creative.official_params;
  delete config.accountOverrides['111111111'].creative.official_params;
  assert.equal(carousel.payload.creative.format,'CAROUSEL');
  assert.equal(carousel.payload.creative.carousel_cards[1].headline,'Second');
  assert.deepEqual(Array.from(carousel.payload.creative.carousel_media_library_ids),['m-first','m-second']);
  assert.equal(config.payload.creative.format,undefined,'preparation must not mutate the form config');
  // Restored bundles have no active preset; retain their exact library IDs.
  vm.runInContext(`remaskSelectedCreativePreset=null; let remaskBundleMedia={format:'CAROUSEL',carousel:[
    {media_library_id:'m-restored-first'},{media_library_id:'m-restored-second'}]}`,privateCtx);
  const restored=await privateCtx.remaskPrivatePrepareConfig();
  assert.deepEqual(Array.from(restored.payload.creative.carousel_media_library_ids),
    ['m-restored-first','m-restored-second']);
  vm.runInContext(`remaskBundleMedia.carousel[1].media_library_id='m-replaced'`,privateCtx);
  assert.throws(()=>privateCtx.remaskPrivateAssertCurrentConfig(restored),/Настройки изменились/);
  privateCtx.creativeFormat=()=> 'SINGLE';
  vm.runInContext(`remaskBundleMedia={format:'SINGLE',media_library_id:'m-restored-video'}`,privateCtx);
  const singleRestored=await privateCtx.remaskPrivatePrepareConfig();
  assert.equal(singleRestored.payload.creative.media_library_id,'m-restored-video');
  vm.runInContext(`remaskBundleMedia={format:'SINGLE',media_library_id:'m-replaced-image'}`,privateCtx);
  assert.throws(()=>privateCtx.remaskPrivateAssertCurrentConfig(singleRestored),/Настройки изменились/);
  vm.runInContext(`remaskBundleMedia=null`,privateCtx);
  privateCtx.creativeFormat=()=> 'INSTAGRAM_POST';
  buttons.instagramMediaId={value:'888888888'};
  bindings['111111111'].source_instagram_media_id='777777777';
  bindings['222222222'].source_instagram_media_id='999999999';
  const instagram=await privateCtx.remaskPrivatePrepareConfig();
  assert.deepEqual(Array.from(instagram._targets,row=>row.launch.override.creative.source_instagram_media_id),
    ['777777777','999999999'],'Instagram bindings must use the finalized format for each exact RK');
  delete bindings['111111111'].source_instagram_media_id;
  delete bindings['222222222'].source_instagram_media_id;
  privateCtx.creativeFormat=()=> 'SINGLE';
  privateCtx.state.pendingWorkspaceMedia={type:'image_hash',image_hash:'media-a',account_id:'111111111'};
  await assert.rejects(()=>privateCtx.remaskPrivatePrepareConfig(),/другому РК/);
  privateCtx.state.pendingWorkspaceMedia=null;
  privateCtx.FormData=class {constructor(){this.fields=[];} append(...args){this.fields.push(args);}};
  buttons.media={files:[{name:'ad.png',size:68}]};
  let uploadCount=0;
  privateCtx.fetch=async(url,options)=>{
    uploadCount++;
    assert.equal(options.body.fields[0][1],'private_launch_media');
    assert.equal(options.body.fields[1][1],buttons.media.files[0]);
    assert.equal(options.headers['Content-Type'],undefined,'the browser must set the multipart boundary');
    assert.equal(options.headers['X-ReMask-CSRF'],'fixture-csrf','media staging requires the page session CSRF token');
    return {ok:true,json:async()=>({ok:true,media:{id:'m-'+'a'.repeat(24),sha256:'a'.repeat(64),media_type:'image',size_bytes:68}})};
  };
  const uploaded=await privateCtx.remaskPrivatePrepareConfig();
  const uploadedAgain=await privateCtx.remaskPrivatePrepareConfig();
  assert.equal(uploadCount,1,'same selected file must reuse content-addressed staging');
  assert.equal(uploaded.payload.creative.staged_media[0].sha256,'a'.repeat(64));
  assert.equal(JSON.stringify(uploadedAgain),JSON.stringify(uploaded));
  assert.equal(config.payload.creative.staged_media,undefined);
  buttons.media.files=[];

  // Editing any launch input while the server reviews the captured form must
  // prevent a stale submission. No real Meta or advertising requests are used.
  const originalReview=privateCtx.remaskPrivateReviewConfig;
  privateCtx.fetch=originalFetch;
  privateCtx.remaskPrivatePollJob=async()=>null;
  buttons.launchDeliverySelect={value:'PAUSED'};
  const changes=[
    [()=>{config.payload.creative.message='edited';},()=>{config.payload.creative.message='base';}],
    [()=>{config.accountOverrides['111111111'].creative.message='new override';},()=>{config.accountOverrides['111111111'].creative.message='one';}],
    [()=>{bindings['111111111'].page_id='777777777';},()=>{bindings['111111111'].page_id='333333333';}],
    [()=>{config.accountIds.reverse();},()=>{config.accountIds.reverse();}],
    [()=>{buttons.media.files=[{name:'replacement.png',size:68}];},()=>{buttons.media.files=[];}],
    [()=>{buttons.launchDeliverySelect.value='ACTIVE';},()=>{buttons.launchDeliverySelect.value='PAUSED';}]
  ];
  for(const [change,restore] of changes){
    let entered,release;
    const started=new Promise(resolve=>entered=resolve);
    const gate=new Promise(resolve=>release=resolve);
    privateCtx.remaskPrivateReviewConfig=async prepared=>{
      entered();await gate;
      return privateCtx.remaskPrivateTargetRows(prepared).map(row=>({row,review:{ready:true}}));
    };
    const before=requests.filter(r=>r.body.action==='create').length;
    const pending=privateCtx.remaskPrivateCreateJob();
    await started;change();release();
    assert.equal(await pending,null,'changed form must not create a job');
    assert.equal(requests.filter(r=>r.body.action==='create').length,before);
    assert.match(buttons.launchResult.textContent,/Настройки изменились/);
    restore();
  }
  privateCtx.remaskPrivateReviewConfig=originalReview;
  vm.runInContext('for(const key of Object.keys(remaskPrivatePaymentSelections))delete remaskPrivatePaymentSelections[key];',privateCtx);
  const stable=await privateCtx.remaskPrivateCreateJob();
  assert.equal(stable.job.job_id,'private-job-1','automatic masked payment discovery must allow an unchanged form');

  const originalWorker=privateCtx.remaskPrivateWorker;
  let planStarted,planRelease;
  const planning=new Promise(resolve=>planStarted=resolve);
  const planGate=new Promise(resolve=>planRelease=resolve);
  privateCtx.remaskPrivateWorker=async()=>{
    planStarted();await planGate;
    return {plan:{configuration_valid:true,checked_live:false}};
  };
  const stalePlan=privateCtx.remaskPrivateReviewOnly();
  await planning;config.payload.creative.message='changed during plan';planRelease();
  assert.equal(await stalePlan,null);
  assert.match(buttons.launchResult.textContent,/Настройки изменились/);
  config.payload.creative.message='base';
  privateCtx.remaskPrivateWorker=originalWorker;

  let stageRelease;
  buttons.media.files=[{name:'slow.png'}];
  privateCtx.remaskPrivateStageFile=()=>new Promise(resolve=>stageRelease=resolve);
  const staging=privateCtx.remaskPrivatePrepareConfig();
  config.payload.creative.message='changed during upload';
  stageRelease({id:'m-'+'c'.repeat(24),sha256:'c'.repeat(64)});
  const staleUpload=await staging;
  assert.throws(()=>privateCtx.remaskPrivateAssertCurrentConfig(staleUpload),/Настройки изменились/);
  config.payload.creative.message='base';buttons.media.files=[];

  // Replacing a saved carousel with files must select exactly one media source.
  privateCtx.creativeFormat=()=> 'CAROUSEL';
  privateCtx.carouselFiles=()=>[{name:'first.png'},{name:'second.png'}];
  privateCtx.remaskPrivateStageFile=async()=>({id:'m-'+'c'.repeat(24),sha256:'c'.repeat(64)});
  const replacement=await privateCtx.remaskPrivatePrepareConfig();
  assert.equal(replacement.payload.creative.staged_media.length,2);
  assert.equal(replacement.payload.creative.carousel_media_library_ids,undefined);
  privateCtx.creativeFormat=()=> 'SINGLE';

  // Multiple live cards must stop before Job creation and expose only masked
  // choices for the exact RK.
  const paymentHost={children:[],insertBefore(node){this.children.push(node);node.parentNode=this;}};
  buttons.fundingStatus.parentNode=paymentHost;
  privateCtx.document.createElement=tag=>{
    const node={
      tagName:String(tag).toUpperCase(),children:[],dataset:{},className:'',
      textContent:'',value:'',selected:false,parentNode:null,
      appendChild(child){this.children.push(child);child.parentNode=this;},
      addEventListener(kind,handler){this['on'+kind]=handler;}
    };
    Object.defineProperty(node,'textContent',{
      get(){return this._text||'';},
      set(value){this._text=String(value);this.children=[];}
    });
    if(tag==='select'){
      Object.defineProperty(node,'options',{get(){return this.children;}});
      node.selectedIndex=0;
    }
    return node;
  };
  privateCtx.fetch=async(url,options)=>{
    const body=JSON.parse(options.body);
    if(body.action==='private_launch_contracts')
      return {ok:true,status:200,json:async()=>({ok:true,contracts:{
        CAMPAIGN:{configured:true,active:true,verification_configured:true},ADSET:{configured:true,active:true,verification_configured:true},
        CREATIVE:{configured:true,active:true,verification_configured:true},AD:{configured:true,active:true,verification_configured:true}
      }})};
    if(body.action==='private_launch_review')
      return {ok:true,status:200,json:async()=>({ok:true,review:{
        ready:false,preflight:{
          payment_selection_required:true,
          payment_methods:[
            {type:'Visa',last4:'1111',linkage_status:'OBSERVED'},
            {type:'Mastercard',last4:'2222',linkage_status:'OBSERVED'}
          ]
        }
      }})};
    throw new Error('unexpected worker call');
  };
  const oneConfig={...config,accountIds:['111111111']};
  const waiting=await privateCtx.remaskPrivateReviewConfig(oneConfig,{render:false});
  assert.equal(waiting[0].status,'WAITING_FOR_SELECTION');
  assert.equal(waiting[0].review.ready,false);
  assert.match(waiting[0].error,/Выберите карту для РК 111111111/);
  assert.equal(paymentHost.children.length,1);
  const selectorBlock=paymentHost.children[0].children[0];
  const selector=selectorBlock.children.find(node=>node.dataset?.paymentSelect==='1');
  assert.ok(selector,'masked payment selector was not rendered');
  assert.equal(selector.children.length,3);
  assert.match(selector.children[1].textContent,/Visa.*1111/);
  assert.match(selector.children[2].textContent,/Mastercard.*2222/);

  // A new live list must invalidate a removed selection without selecting
  // its replacement; no old masked card or pending request survives.
  buttons.remaskPrivatePaymentSelectors=paymentHost.children[0];
  selector.selectedIndex=1; selector.onchange();
  assert.equal(privateCtx.remaskPrivateSelectedPayment('7','111111111').last4,'1111');
  privateCtx.remaskPrivatePendingWrite('stale-fixture',{idempotency_key:'pending-fixture'});
  privateCtx.remaskPrivateRenderPaymentSelector({profile:'7',ad_account_id:'111111111'},[
    {type:'Mastercard',last4:'2222'}
  ]);
  assert.equal(privateCtx.remaskPrivateSelectedPayment('7','111111111'),null);
  assert.equal(storage.has('remask_private_launch_pending_v1'),false);
  assert.equal(selector.children.length,2);
  assert.equal(selector.children[0].textContent,'Выберите карту');
  assert.equal(selectorBlock.hidden,false);
  selector.selectedIndex=1; selector.onchange();
  assert.equal(privateCtx.remaskPrivateSelectedPayment('7','111111111').last4,'2222');
  privateCtx.remaskPrivateRenderPaymentSelector({profile:'7',ad_account_id:'111111111'},[]);
  assert.equal(privateCtx.remaskPrivateSelectedPayment('7','111111111'),null);
  assert.equal(selectorBlock.hidden,true);
  assert.equal(paymentHost.children.length,1);

  console.log('Private Launch UI uses worker review + independent per-RK jobs with durable idempotency.');
})().catch(e=>{console.error(e);process.exitCode=1});

(async()=>{
  const overlay=fs.readFileSync(path.join(__dirname,'..','railway-launch-private-catalog-overlay.php'),'utf8');
  assert.ok(overlay.includes("require_once '/tmp/railway-private-launch-meta-marker.php';"));
  assert.ok(overlay.includes('$js=remask_patch_private_launch_meta_marker($js);'));
  assert.ok(overlay.includes('scripts/launch.js?v=20261023-private-launch-meta-marker-v1'));
  assert.match(overlay,/\/payment-methods\?'\.http_build_query\(\$paymentQuery/);
  assert.match(overlay,/'business_id'=>self::id\(\$account\['business_id'\]/);
  assert.match(overlay,/'account_name'=>trim\(\(string\)\(\$account\['name'\]/);
  assert.match(overlay,/private_facebook_billing_ui/);
  assert.match(overlay,/private_facebook_selected_rk_payment_tab/);
  assert.match(overlay,/'funding_verified'=>false/);
  assert.doesNotMatch(overlay,/'funding_verified'=>true/);
  assert.match(overlay,/PRIVATE_LAUNCH_VERIFICATION_REQUIRED/);
  const pageLoader=overlay.match(/<<<'NEW_BULK_PAGES'\n([\s\S]*?)\nNEW_BULK_PAGES/)[1];
  assert.ok(pageLoader.includes('remaskPrivatePageReadFailure'));
  assert.ok(pageLoader.includes('FP не загружены'));
  assert.ok(pageLoader.includes('catch (error)'));
  {
    const targetKey='15|120249392950240046|DRAFT';
    let pageRenderCount=0;
    let capturedPageError;
    const pageCtx={
      remaskPrivatePageReadFailure:(error,profile,accountId)=>{
        capturedPageError={code:error?.code,payloadCode:error?.payload?.code,message:error?.message};
        return context.remaskPrivatePageReadFailure(error,profile,accountId);
      },
      remaskPrivateExecutionMode:()=> 'DRAFT',
      status:{textContent:'',classList:{add(){}}},
      targets:[{profile:'15',account_id:'act_120249392950240046'}],
      force:true,
      state:{targetBindingAssets:{pages:{[targetKey]:[{id:'stale'}]},pageReadErrors:{}}},
      runPool:async(rows,_workers,fn)=>{for(const row of rows)await fn(row);},
      remaskPrivateAccountId:value=>String(value).replace(/^act_/,''),
      apiJson:async()=>{throw {message:'Meta login required',payload:{code:'PRIVATE_ADS_DRAFT_BUSINESS_LOGIN_GATE',message:'Meta login required'}};},
      formPost:value=>value,
      renderTargetBindings(){pageRenderCount++;}
    };
    vm.createContext(pageCtx);
    await vm.runInContext('(async()=>{\n'+pageLoader+'\n})()',pageCtx);
    assert.match(pageCtx.status.textContent,/BUSINESS_LOGIN_GATE/);
    assert.equal(Object.hasOwn(pageCtx.state.targetBindingAssets.pages,targetKey),false,
      'a rejected live read must not leave stale Pages selectable for this exact RK');
    assert.match(pageCtx.state.targetBindingAssets.pageReadErrors[targetKey],/BUSINESS_LOGIN_GATE/);
    assert.equal(pageRenderCount,1);
  }
  assert.match(overlay,/BUSINESS_LOGIN_GATE/);
  assert.match(overlay,/Meta отклонила сохранённую Facebook-сессию/);
  const readiness=overlay.match(/<<<'READINESS_UI'\n([\s\S]*?)\nREADINESS_UI/)[1];
  const output={}; let request;
  const ctx={selectedRows:()=>[{id:'act_333333333',profile:'7',name:'Saved RK'}],
    openModal(){},$:id=>output[id]||(output[id]={appendChild(node){this.html=node.innerHTML;}}),
    document:{createElement:()=>({})},esc:String,pill:(label)=>label,post:x=>x,setProgress(){},
    apiJson:async(url,input)=>{request={url,input};return {status:'NOT_VERIFIED',pages:{data:[{id:'222222222',name:'Existing Page',main_business_confirmed:true,main_business_id:'111111111'}]},funding:{status:'NOT_CHECKED'}};},
    concurrent:async(rows,n,fn,cb)=>{assert.equal(n,1);const result=await fn(rows[0]);cb(1,1,result,0);return [result];}};
  vm.createContext(ctx);vm.runInContext(readiness,ctx);await ctx.checkAssetsSelection();
  assert.equal(request.url,'ajax/metaAssetReadiness.php');
  assert.equal(request.input.profile,'7');
  assert.equal(request.input.account_id,'act_333333333');
  assert.match(output.assetReadinessRows.html,/Existing Page · 222222222/);
  assert.match(output.assetReadinessRows.html,/ДОСТУП FP НЕ ПОДТВЕРЖДЁН/);
  assert.match(output.assetReadinessRows.html,/Confirm основного BM 111111111: выполнен/);
  assert.doesNotMatch(output.assetReadinessRows.html,/ASSETS READY|PAGE ISSUE|EMPTY/);
  assert.match(output.assetReadinessProgress.textContent,/Проверка FP и оплаты завершена/);
  ctx.apiJson=async()=>({pages:{ad_account_page_access_verified:true,data:[
    {id:'222222222',name:'Verified Page',ad_account_page_access_verified:true},
    {id:'444444444',name:'Saved only',ad_account_page_access_verified:false}]},
    funding:{status:'LINKED',verification_status:'LINKED',card_linked:true,
      account_scope_verified:true,checked_live:true,funding_verified:false,
      payment_methods:[{type:'Visa',last4:'1111',linkage_status:'OBSERVED'}],diagnostic:{}}});
  await ctx.checkAssetsSelection();
  assert.match(output.assetReadinessRows.html,/Verified Page · 222222222 — доступ РК подтверждён/);
  assert.match(output.assetReadinessRows.html,/Saved only · 444444444 — доступ РК не подтверждён/);
  assert.match(output.assetReadinessRows.html,/Карта присутствует у выбранного РК — Visa •••• 1111/);
  assert.match(output.assetReadinessRows.html,/Платёжная\/charge verification не подтверждена/);
  ctx.apiJson=async()=>({pages:{ad_account_page_access_verified:true,data:[
    {id:'222222222',name:'Verified Page',ad_account_page_access_verified:true}]},
    funding:{status:'NONE',verification_status:'NONE',card_linked:false,
      account_scope_verified:true,checked_live:true,funding_verified:false,
      payment_methods:[],diagnostic:{}}});
  await ctx.checkAssetsSelection();
  assert.match(output.assetReadinessRows.html,/Meta live подтверждает отсутствие payment methods/);
  ctx.apiJson=async()=>({pages:{ad_account_page_access_verified:false,data:[]},
    funding:{status:'NOT_CHECKED',funding_verified:false,payment_methods:[],
      diagnostic:{code:'PAYMENT_SKIPPED_PAGE_ACCESS_UNVERIFIED'}}});
  await ctx.checkAssetsSelection();
  assert.match(output.assetReadinessRows.html,/Оплата не проверялась: сначала нужен подтверждённый доступ FP/);
  ctx.apiJson=async()=>({error:'profile scope mismatch'});await ctx.checkAssetsSelection();
  assert.match(output.assetReadinessRows.html,/profile scope mismatch/);
  assert.doesNotMatch(output.assetReadinessRows.html,/Existing Page/);
  console.log('Private Workspace Assets scope and unverified access checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

(async()=>{
  const mode={value:'DRAFT'};
  const requests=[];
  const ctx={...context,URLSearchParams,$:id=>id==='remaskPrivateExecutionMode'?mode:controls[id],
    apiJson:async(url,options)=>{requests.push({url,body:options.body});return {ok:true};}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  for(const executionMode of ['DRAFT','PUBLISH']){
    mode.value=executionMode;
    const body=new URLSearchParams({profile:'38',account_id:'120249912222240685',resource:'pages'});
    await ctx.apiJson('ajax/metaAssets.php',{body});
    assert.equal(body.get('execution_mode'),executionMode);
    assert.equal(body.get('profile'),'38');
    assert.equal(body.get('account_id'),'120249912222240685');
  }
  mode.value='DRAFT';
  await ctx.apiJson('ajax/metaAssets.php',{body:'profile=38&account_id=120249912222240685&resource=pages'});
  const encoded=new URLSearchParams(requests.at(-1).body);
  assert.equal(encoded.get('execution_mode'),'DRAFT');
  assert.equal(encoded.get('account_id'),'120249912222240685');
  const body=new URLSearchParams({resource:'images'});
  await ctx.apiJson('ajax/metaAssets.php',{body});
  assert.equal(body.has('execution_mode'),false);
  assert.equal(requests.length,4);
  console.log('The Page picker carries the explicit draft/publication mode with unchanged exact profile/RK scope.');
})().catch(e=>{console.error(e);process.exitCode=1;});
