const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('railway-launch-private-catalog.js','utf8');

function fixture(size=30) {
  const controls=Object.fromEntries(['preflight','syncMeta','loadFunding','fundingStatus',
    'reviewLaunch','launchButton','serverDryRun','dryRunPlan','reviewStatus','launchResult','adAccount']
    .map(id=>[id,{}]));
  const rows=Array.from({length:size},(_,index)=>({profile:String(index%3),
    ad_account_id:String(100000+index),business_id:String(200000+index),page_id:String(300000+index)}));
  const config={accountIds:rows.map(row=>row.ad_account_id),payload:{
    campaign:{name:'Fixture campaign'},adset:{name:'Fixture set',daily_budget:500},
    creative:{name:'Fixture creative',message:'Fixture',link:'https://example.com/',existing_image_hash:'fixture-image'},
    ad:{name:'Fixture ad'},delivery:{campaign:'PAUSED',adset:'PAUSED',ad:'PAUSED'}}};
  const storage=new Map(),requests=[];
  const ctx={$:id=>controls[id],fundingState(){},fundingReviewLabel(){},loadFunding:async()=>{},validateReady(){},
    state:{profile:'0',accounts:rows.map(row=>({id:row.ad_account_id,_profile:row.profile,business_id:row.business_id}))},
    targetMode:()=>true,targetForAccount:id=>rows.find(row=>row.ad_account_id===id),
    bindingFor:id=>({page_id:rows.find(row=>row.ad_account_id===id)?.page_id}),
    currentLaunchConfigForRequest:()=>config,
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    Uint32Array,crypto:{getRandomValues:values=>{values.fill(1);return values;}}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  ctx.remaskPrivatePollJob=async()=>null;
  ctx.remaskPrivateWorker=async payload=>{
    requests.push(payload);
    if(payload.action==='private_launch_contracts')return {contracts:Object.fromEntries(
      ['CAMPAIGN','ADSET','CREATIVE','AD'].map(step=>[step,{configured:true,active:true,verification_configured:true}]))};
    if(payload.action==='private_launch_review')return await ctx.review(payload);
    if(payload.action==='create')return {job:{job_id:'fixture-job'}};
    throw Error('Unexpected operation');
  };
  ctx.review=async()=>({review:{ready:true,preflight:{}}});
  return {ctx,config,rows,requests,storage,controls};
}

(async()=>{
  {
    const {ctx,config,requests,controls}=fixture(1);
    controls.remaskPrivateExecutionMode={value:'DRAFT'};
    config.payload.execution_mode='PUBLISH';
    config.accountOverrides={[config.accountIds[0]]:{execution_mode:'PUBLISH'}};
    const rows=ctx.remaskPrivateTargetRows(config);
    assert.equal(rows[0].launch.base.execution_mode,'DRAFT');
    assert.equal(rows[0].launch.override.execution_mode,undefined,'a stale target override cannot request publication');
    await ctx.remaskPrivateCreateJob();
    const created=requests.find(row=>row.action==='create');
    assert.equal(created.profiles[0].tasks[0].payload.launch.base.execution_mode,'DRAFT');
    const task={action:'private_launch',status:'SUCCESS',payload:created.profiles[0].tasks[0].payload};
    assert.equal(ctx.remaskPrivateCanDeliver({},task,'ACTIVE'),false);
    assert.equal(ctx.remaskPrivateCanDeliver({},task,'PAUSED'),false);
    const draftFingerprint=ctx.remaskPrivateFingerprint(rows);
    controls.remaskPrivateExecutionMode.value='PUBLISH';
    const publicationRows=ctx.remaskPrivateTargetRows(config);
    assert.equal(publicationRows[0].launch.base.execution_mode,undefined);
    assert.notEqual(ctx.remaskPrivateFingerprint(publicationRows),draftFingerprint,'draft and publication must not share an intent');
    controls.remaskPrivateExecutionMode.value='DRAFT';
    config.payload.delivery.ad='ACTIVE';
    assert.throws(()=>ctx.remaskPrivateTargetRows(config),/На паузе/);
  }
  for(const size of [1,3,5,10,30]){
    const {ctx,config,requests,rows}=fixture(size);
    ctx.review=async payload=>{
      if(payload.ad_account_id===rows[0].ad_account_id){
        const error=Error('Meta denied Page advertising access');
        error.code='PRIVATE_ADS_DRAFT_REQUEST_REJECTED';throw error;
      }
      return {review:{ready:true,preflight:{}}};
    };
    const results=await ctx.remaskPrivateReviewConfig(config,{render:false});
    assert.equal(results.length,size);
    assert.equal(results[0].status,'BLOCKED');
    assert.equal(results.filter(row=>row.status==='READY').length,size-1);
    assert.deepEqual(requests.filter(row=>row.action==='private_launch_review').map(row=>row.ad_account_id),
      rows.map(row=>row.ad_account_id),'a denied first target must not prevent later independent READs');
    for(let index=0;index<size;index++)assert.deepEqual(
      [results[index].row.profile,results[index].row.business_id,results[index].row.page_id],
      [rows[index].profile,rows[index].business_id,rows[index].page_id]);
    assert.equal(ctx.state.privateLaunchReadiness.admission_policy,'all_required');
  }

  {
    const {ctx,config,requests,rows}=fixture();
    ctx.review=async payload=>{
      if(payload.ad_account_id===rows[0].ad_account_id){
        const error=Error('Meta session requires legitimate restoration');
        error.code='PRIVATE_ADS_DRAFT_CHECKPOINT_REQUIRED';throw error;
      }
      return {review:{ready:true,preflight:{}}};
    };
    const results=await ctx.remaskPrivateReviewConfig(config,{render:false});
    assert.equal(results.filter(row=>row.status==='AUTH_REQUIRED').length,10);
    assert.equal(results.filter(row=>row.status==='READY').length,20);
    assert.equal(requests.filter(row=>row.action==='private_launch_review'&&row.profile_id==='0').length,1,
      'a confirmed auth gate must stop further reads for the same selected profile');
    assert.equal(results[3].review_attempted,false);
  }

  for(const httpStatus of [401,403]){
    const {ctx,config,requests}=fixture();
    ctx.review=async()=>{const error=Error('ReMask authentication/CSRF failed');error.httpStatus=httpStatus;throw error;};
    const results=await ctx.remaskPrivateReviewConfig(config,{render:false});
    assert.equal(requests.filter(row=>row.action==='private_launch_review').length,1);
    assert.ok(results.every(row=>row.status==='AUTH_REQUIRED'&&row.error_code==='REMASK_AUTH_REQUIRED'));
  }

  for(const failure of [
    {message:'Quota rejected',httpStatus:429},
    {message:'Quota rejected',code:'PRIVATE_HTTP_RATE_LIMITED'},
    {message:'Meta rejected the advertising request (code=613, http_status=200)'},
  ]){
    const {ctx,config,requests}=fixture();
    ctx.review=async()=>{throw Object.assign(Error(failure.message),failure);};
    const results=await ctx.remaskPrivateReviewConfig(config,{render:false});
    assert.equal(requests.filter(row=>row.action==='private_launch_review').length,1);
    assert.ok(results.every(row=>row.status==='RATE_LIMITED'&&row.error_code==='META_RATE_LIMITED'));
    assert.equal(results[1].review_attempted,false,'quota refusal must not redirect reads to another selected account');
  }

  {
    const {ctx,config,rows,requests,storage}=fixture();
    ctx.review=async payload=>payload.ad_account_id===rows[0].ad_account_id
      ?{review:{ready:false,preflight:{payment_selection_required:true,payment_methods:[{type:'Visa',last4:'1111'}]}}}
      :{review:{ready:true,preflight:{}}};
    const result=await ctx.remaskPrivateCreateJob();
    assert.equal(result,null);
    assert.equal(requests.filter(row=>row.action==='private_launch_review').length,30);
    assert.equal(requests.filter(row=>row.action==='create').length,0,
      'diagnosing independent targets does not silently change batch admission or enqueue blocked targets');
    assert.equal(ctx.state.privateLaunchReadiness.targets[0].status,'WAITING_FOR_SELECTION');
    assert.equal(storage.has('remask_private_launch_pending_v1'),false);
  }

  {
    const {ctx,rows,requests,storage}=fixture();
    const accepted=await ctx.remaskPrivateCreateJob();
    assert.equal(accepted.job.job_id,'fixture-job');
    const jobs=requests.filter(row=>row.action==='create');assert.equal(jobs.length,1);
    assert.equal(jobs[0].profiles.length,30);
    assert.deepEqual(Array.from(jobs[0].profiles,row=>row.tasks[0].payload.page_id),rows.map(row=>row.page_id));
    assert.ok(jobs[0].profiles.every(row=>row.tasks[0].payload.launch.base.delivery.ad==='PAUSED'));
    assert.equal(new Set(jobs[0].profiles.map(row=>row.tasks[0].idempotency_key)).size,30);
    assert.ok(storage.has('remask_private_launch_pending_v1'));
  }
  console.log('1/3/5/10/30-target review reports every outcome, preserves scope, stops auth repeats and keeps admission/idempotency guards.');
})().catch(error=>{console.error(error);process.exitCode=1;});
