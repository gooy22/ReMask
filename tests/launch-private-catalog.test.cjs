const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '..', 'railway-launch-private-catalog.js'), 'utf8');
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
assert.equal(controls.launchButton.disabled,true);
assert.equal(controls.reviewLaunch.disabled,true);
assert.equal(controls.dryRunPlan.disabled,true);
assert.match(controls.reviewStatus.textContent,/Private Launch/);
assert.equal(controls.preflight.textContent,'Загрузить сохранённые РК');
console.log('Private Launch catalog and funding truth checks passed');

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
  const bindings={
    '111111111':{page_id:'333333333'},
    '222222222':{page_id:'444444444'}
  };
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
    targetForAccount:id=>{
      const clean=String(id).replace(/^act_/,'');
      return clean==='111111111'
        ?{profile:'7',business_id:'555555555',account_id:clean}
        :{profile:'7',business_id:'666666666',account_id:clean};
    },
    bindingFor:id=>bindings[String(id).replace(/^act_/,'')]||{},
    show:(target,text)=>{target.textContent=text;shown.push(text);},
    document:{addEventListener(kind,handler,capture){
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
      const body=JSON.parse(options.body);requests.push({url,body});
      if(body.action==='private_launch_contracts')
        return {ok:true,status:200,json:async()=>({ok:true,contracts:{
          CAMPAIGN:{configured:true,friendly_name:'CampaignMutation'},
          AD_SET:{configured:true,friendly_name:'AdSetMutation'},
          CREATIVE:{configured:true,friendly_name:'CreativeMutation'},
          AD:{configured:true,friendly_name:'AdMutation'}
        }})};
      if(body.action==='private_launch_review')
        return {ok:true,status:200,json:async()=>({ok:true,review:{ready:true,profile_id:body.profile_id}})};
      if(body.action==='create')
        return {ok:true,status:200,json:async()=>({ok:true,job:{job_id:'private-job-1'}})};
      return {ok:true,status:200,json:async()=>({ok:true,job:{status:'SUCCESS',items_total:2,items_done:2}})};
    }
  };
  vm.createContext(privateCtx);vm.runInContext(source,privateCtx);

  assert.equal(buttons.launchButton.disabled,false,'structurally complete private selection should enable Launch');
  assert.equal(buttons.reviewLaunch.disabled,false);
  assert.equal(typeof captureHandler,'function');

  const reviewed=await privateCtx.remaskPrivateReviewConfig(config,{render:false});
  assert.equal(reviewed.length,2);
  assert.deepEqual(requests.map(r=>r.body.action),['private_launch_contracts','private_launch_review','private_launch_review']);
  assert.ok(requests.every(r=>r.url==='ajax/pythonWorkerJobs.php'));
  const reviewRequests=requests.filter(r=>r.body.action==='private_launch_review');
  assert.deepEqual(reviewRequests.map(r=>r.body.page_id),['333333333','444444444']);
  assert.deepEqual(reviewRequests.map(r=>r.body.business_id),['555555555','666666666']);
  assert.equal(reviewRequests[0].body.launch.override.creative.message,'one');
  assert.equal(reviewRequests[1].body.launch.override.adset.targeting.age_max,35);
  assert.ok(requests.every(r=>r.body.doc_id===undefined&&r.body.fb_dtsg===undefined));

  const planned=privateCtx.remaskPrivateJobRequest(config,reviewed);
  assert.equal(planned.request.action,'create');
  assert.equal(planned.request.profiles.length,2);
  assert.ok(planned.request.profiles.every(row=>row.tasks.length===1&&row.tasks[0].action==='private_launch'));
  assert.deepEqual(Array.from(planned.request.profiles,row=>row.tasks[0].payload.page_id),['333333333','444444444']);
  assert.ok(planned.request.profiles.every(row=>row.tasks[0].payload.launch.base.campaign.name==='Campaign'));
  assert.ok(planned.request.profiles.every(row=>row.tasks[0].payload.doc_id===undefined));

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
  assert.equal(storage.has('remask_private_launch_pending_v1'),false,
    'only terminal SUCCESS may clear the pending request');

  // Recreate one pending request so the explicit clear helper stays covered.
  privateCtx.remaskPrivateJobRequest(config,reviewed);
  assert.equal(storage.has('remask_private_launch_pending_v1'),true);
  privateCtx.remaskPrivatePendingClear();
  assert.equal(storage.has('remask_private_launch_pending_v1'),false);
  console.log('Private Launch UI uses worker review + independent per-RK jobs with durable idempotency.');
})().catch(e=>{console.error(e);process.exitCode=1});

(async()=>{
  const overlay=fs.readFileSync(path.join(__dirname,'..','railway-launch-private-catalog-overlay.php'),'utf8');
  assert.match(overlay,/\/payment-methods\?'\.http_build_query\(\$paymentQuery/);
  assert.match(overlay,/'business_id'=>self::id\(\$account\['business_id'\]/);
  assert.match(overlay,/'account_name'=>trim\(\(string\)\(\$account\['name'\]/);
  assert.match(overlay,/private_facebook_billing_ui/);
  assert.match(overlay,/private_facebook_selected_rk_payment_tab/);
  assert.match(overlay,/'funding_verified'=>false/);
  assert.doesNotMatch(overlay,/'funding_verified'=>true/);
  assert.match(overlay,/PRIVATE_LAUNCH_VERIFICATION_REQUIRED/);
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
