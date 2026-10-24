const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','railway-private-launch-completion.js'),'utf8');
const bundleCode=source.slice(source.indexOf('async function remaskPrivateCaptureBundle()'),
  source.indexOf('\nfunction remaskPrivateInstallBudgetPeriod()'));
const ids=['m-'+'a'.repeat(24),'m-'+'b'.repeat(24)];
const targetingCode=source.slice(source.indexOf('function remaskPrivateTargetingScope()'),
  source.indexOf('// A selected RK needs'));
function targetingFixture(){
  const calls=[];
  const ctx={URLSearchParams,MutationObserver:class{observe(){} disconnect(){}},state:{profile:'38',targets:[]},accounts:['act_120249912222240685'],mode:'DRAFT',
    remaskModuleControls:{isTargetingType:type=>type==='work_positions'},
    $:()=>({value:ctx.mode}),selectedAccountIds:()=>ctx.accounts,targetMode:()=>ctx.state.targets.length>0,
    apiJson:async(url,options)=>{
      const body=Object.fromEntries(new URLSearchParams(options.body));calls.push({url,body});
      if(ctx.failure)throw new Error(ctx.failure);
      if(ctx.pending)await ctx.pending;
      return {...{data:[{id:'6003277229371'}],profile:body.profile,account_id:body.account_id,resource:body.type,
        checked_live:true,account_scope_verified:true},...ctx.response};
    }};
  vm.createContext(ctx);vm.runInContext(targetingCode,ctx);
  return {ctx,calls,search:(type='interests',url='ajax/metaTargetingSearch.php')=>ctx.apiJson(url,
    {body:new URLSearchParams({type,q:'fitness',profile:'WRONG',account_id:'999999',limit:'25'}).toString()})};
}
test('selected RK targeting uses persistent suggestions unless an explicit refresh is requested',()=>{
  const php=fs.readFileSync(path.join(__dirname,'..','railway-launch-private-catalog-overlay.php'),'utf8');
  const start=php.indexOf("file_put_contents($root.'/ajax/metaTargetingSearch.php'");
  const end=php.indexOf("file_put_contents($root.'/ajax/metaAssetReadiness.php'",start);
  assert.ok(start>=0 && end>start,'targeting route must be present');
  const route=php.slice(start,end);
  assert.match(route, /\$input\['force'\]\s*\?\?\s*''\)\s*===\s*'1'/);
  assert.doesNotMatch(route, /RemaskAdvertisingAssetBridge::read\([^;]*,\$type,true\)/);
});
test('selected RK reads fresh GEO, interests, behaviors and languages in its exact scope',async()=>{
  for(const [type,resource] of [['locations','locations'],['interests','interests'],['behaviors','behaviors'],['languages','locales']]){
    const {ctx,calls,search}=targetingFixture();const result=await search(type);
    const scoped=()=>calls.filter(call=>call.url==='ajax/metaTargetingSearch.php');
    assert.deepEqual(scoped(),[{url:'ajax/metaTargetingSearch.php',body:{type:resource,q:'fitness',limit:'25',
      profile:'38',account_id:'120249912222240685',execution_mode:'DRAFT'}}]);
    assert.equal(result.data[0].id,'6003277229371');
    ctx.mode='PUBLISH';await search(type);
    assert.equal(scoped()[1].body.execution_mode,'PUBLISH');
    const referenceCalls=calls.filter(call=>call.url==='ajax/metaTemplateTargetingSearch.php');
    assert.equal(referenceCalls.length,type==='locations'?2:0);
    if(type==='locations')assert.equal(referenceCalls[0].body.module_only,'1');
  }
});
test('installed picker reference URL also uses the selected RK live read',async()=>{
  const {calls,search}=targetingFixture();await search('interests','ajax/metaTemplateTargetingSearch.php');
  assert.equal(calls[0].url,'ajax/metaTargetingSearch.php');assert.equal(calls[0].body.profile,'38');
});
test('single bulk target uses its owning profile, never the current unrelated profile',async()=>{
  const {ctx,calls,search}=targetingFixture();ctx.state.profile='16';
  ctx.state.targets=[{profile:'38',account_id:'120249912222240685'}];
  await search();assert.equal(calls[0].body.profile,'38');
});
test('unbound and multiple-account templates stay explicitly unbound',async()=>{
  for(const accounts of [[],['111111','222222']]){
    const {ctx,calls,search}=targetingFixture();ctx.accounts=accounts;await search();
    assert.deepEqual(calls,[{url:'ajax/metaTemplateTargetingSearch.php',body:{type:'interests',q:'fitness',limit:'25'}}]);
  }
});
test('reviewed advanced targeting types use the existing scoped sender',async()=>{
  const {calls,search}=targetingFixture();await search('work_positions');
  assert.equal(calls[0].url,'ajax/metaTargetingSearch.php');
  assert.equal(calls[0].body.profile,'38');assert.equal(calls[0].body.type,'work_positions');
});
test('unreviewed types never call an unsupported account read contract',async()=>{
  const {calls,search}=targetingFixture();await search('invented_type');
  assert.equal(calls[0].url,'ajax/metaTemplateTargetingSearch.php');
  assert.equal(calls[0].body.profile,undefined);
});
test('failed scoped read never falls back to cached or another profile reference',async()=>{
  const {ctx,calls,search}=targetingFixture();
  ctx.failure='SESSION_EXPIRED';await assert.rejects(search,/SESSION_EXPIRED/);
  assert.equal(calls.length,1);assert.equal(calls[0].url,'ajax/metaTargetingSearch.php');
});
test('fresh exact-scope Meta results and explicitly cached suggestions are selectable',async()=>{
  const {ctx,search}=targetingFixture();
  ctx.response={checked_live:false,account_scope_verified:false,
    _cache:{hit:true,cached_only:true,stale:false}};
  const result=await search();
  assert.equal(result.checked_live,false);
  assert.equal(result.account_scope_verified,false);
  assert.equal(result._cache.cached_only,true);
});
test('foreign, unmarked, or unloaded cache cannot supply scoped targeting choices',async()=>{
  for(const response of [{profile:'16'},{account_id:'999999'},{resource:'behaviors'},
      {checked_live:false},{account_scope_verified:false},
      {checked_live:false,account_scope_verified:false,_cache:{hit:false,cached_only:true}},
      {checked_live:false,account_scope_verified:false,availability:'NOT_LOADED',
        _cache:{hit:true,cached_only:true}},
      {checked_live:false,account_scope_verified:false,_cache:{hit:true,cached_only:false}}]){
    const {ctx,search}=targetingFixture();ctx.response=response;
    await assert.rejects(search,/TARGETING_SOURCE_SCOPE_UNVERIFIED/);
  }
});
test('a changed profile, RK or execution mode rejects a pending lookup response',async()=>{
  for(const change of [ctx=>ctx.state.profile='16',ctx=>ctx.accounts=['999999'],ctx=>ctx.mode='PUBLISH']){
    const {ctx,search}=targetingFixture();let release;
    ctx.pending=new Promise(done=>release=done);const pending=search();change(ctx);release();
    await assert.rejects(pending,/TARGETING_SELECTION_CHANGED/);
  }
});
test('selected RK without an unambiguous owner fails before any reference request',async()=>{
  const {ctx,calls,search}=targetingFixture();ctx.state.profile='';
  await assert.rejects(search,/TARGETING_PROFILE_SCOPE_REQUIRED/);assert.equal(calls.length,0);
});
const pagesCode=source.slice(source.indexOf('let remaskPrivatePagesGeneration=0;'),
  source.indexOf("$('adAccount')?.addEventListener"));
function pagesFixture(initial='') {
  const page={value:initial,options:[],replaceChildren(...options){this.options=options;this.value='';},
    appendChild(option){this.options.push(option);}};
  let resolve;
  const pending=new Promise(done=>resolve=done);
  const ctx={$:()=>page,state:{profile:'38',pages:[],pendingBundleAssets:{page:'1408248045702451'}},
    accountIds:['120249912222240685'],selectedAccountIds:()=>ctx.accountIds,targetMode:()=>false,
    loadCachedAsset:()=>pending,Option:function(text,value){this.text=text;this.value=value;},
    hasOptionValue:(select,value)=>select.options.some(option=>option.value===value),
    refreshInstagram(){},invalidateLaunchReview(){},validateReady(){}};
  vm.createContext(ctx);vm.runInContext(pagesCode,ctx);
  return {ctx,page,resolve};
}
test('Page refresh keeps a selection made while the exact-account read is pending',async()=>{
  const {ctx,page,resolve}=pagesFixture();
  const pending=ctx.loadPages();
  page.value='1408248045702451';
  resolve({data:[{id:page.value,name:'PrgssTeam'}]});await pending;
  assert.equal(page.value,'1408248045702451');
});
test('Page refresh respects a newer choice or clear and never retains an absent Page',async()=>{
  for(const selected of ['1555555555555','']){
    const {ctx,page,resolve}=pagesFixture('1408248045702451');
    const pending=ctx.loadPages();page.value=selected;
    resolve({data:[{id:'1408248045702451'},{id:'1555555555555'}]});await pending;
    assert.equal(page.value,selected);
  }
  const {ctx,page,resolve}=pagesFixture();const pending=ctx.loadPages();
  page.value='1408248045702451';resolve({data:[{id:'1555555555555'}]});await pending;
  assert.equal(page.value,'');
});
test('Page refresh cannot overwrite a changed account scope',async()=>{
  const {ctx,page,resolve}=pagesFixture('1408248045702451');const pending=ctx.loadPages();
  ctx.accountIds=['120249999999999999'];page.value='1555555555555';
  resolve({data:[{id:'1408248045702451'}]});await pending;
  assert.equal(page.value,'1555555555555');assert.equal(page.options.length,0);
});
function fixture(){
  const ctx={
    $:()=>null,state:{},remaskBundleRestoreGeneration:0,remaskBundleMedia:null,
    bundleFormState:()=>({fields:{dailyBudget:'500'},meta_builder:{},workspace_media:null}),
    creativeFormat:()=> 'CAROUSEL',carouselFiles:()=>[{name:'a.png'},{name:'b.png'}],
    carouselCardsPayload:()=>[{headline:'First'},{headline:'Second'}],
    clearCreativeLibrarySelection(){ctx.remaskBundleRestoreGeneration++;ctx.remaskSelectedCreativePreset=null;},
    clearExistingMediaSelection(){},remaskPopulateLaunchMetaSdkFields(){},remaskLaunchRulesFromBuilder:()=>({}),
    remaskRestoreLaunchObjective(){},remaskLegacyApplyBundleData(){},remaskPrivateBudgetLabel(){},
    renderCreativeLibraryStatus(){},renderCreativeFormat(){},invalidateLaunchReview(){},validateReady(){},
    apiJson:async()=>({media:ids.map(id=>({id,sha256:'c'.repeat(64)}))})
  };
  vm.createContext(ctx);vm.runInContext(bundleCode,ctx);return ctx;
}
function bundle(){return {fields:{},meta_builder:{},media:{format:'CAROUSEL',
  carousel:ids.map((id,i)=>({media_library_id:id,headline:'Card '+i}))}};}
test('bundle captures cards and fields before a delayed media upload',async()=>{
  const ctx=fixture();
  const fields={dailyBudget:'500'},cards=[{headline:'Before 1'},{headline:'Before 2'}];
  ctx.bundleFormState=()=>({fields,meta_builder:{}});
  ctx.carouselCardsPayload=()=>cards;
  let release;
  const gate=new Promise(resolve=>release=resolve);
  let index=0;
  ctx.remaskPrivateStageFile=async()=>{const id=ids[index++];await gate;return {id,sha256:'c'.repeat(64)};};
  const pending=ctx.remaskPrivateCaptureBundle();
  fields.dailyBudget='900';cards[0].headline='After';cards.reverse();release();
  const result=await pending;
  assert.equal(result.fields.dailyBudget,'500');
  assert.deepEqual(Array.from(result.media.carousel,row=>[row.headline,row.media_library_id]),
    [['Before 1',ids[0]],['Before 2',ids[1]]]);
});
test('incomplete carousel is rejected before staging files',async()=>{
  const ctx=fixture();let staged=0;
  ctx.carouselCardsPayload=()=>[{headline:'Only one'}];
  ctx.remaskPrivateStageFile=async()=>{staged++;};
  await assert.rejects(()=>ctx.remaskPrivateCaptureBundle(),/все карточки/);
  assert.equal(staged,0);
});
test('bundle restore rejects foreign, duplicated or reordered media even without checksums',async()=>{
  for(const response of [[ids[0],ids[0]],[ids[1],ids[0]],[ids[0],'m-'+'d'.repeat(24)]]){
    const ctx=fixture();ctx.apiJson=async()=>({media:response.map(id=>({id}))});
    await assert.rejects(()=>ctx.applyBundleData(bundle()),/удалено или недоступно/);
    assert.equal(ctx.remaskSelectedCreativePreset,null);
  }
});
test('bundle restore preserves exact order and checksums, and respects a newer selection',async()=>{
  const ctx=fixture();const data=bundle();
  data.media.checksums=ids.map(id=>({id,sha256:'c'.repeat(64)}));
  await ctx.applyBundleData(data);
  assert.deepEqual(Array.from(ctx.remaskSelectedCreativePreset.carousel,row=>row.media.id),ids);
  data.media.checksums[0].sha256='e'.repeat(64);
  await assert.rejects(()=>ctx.applyBundleData(data),/Контрольная сумма/);
  let release;ctx.apiJson=()=>new Promise(resolve=>release=resolve);
  const pending=ctx.applyBundleData(bundle());
  ctx.clearCreativeLibrarySelection();
  release({media:ids.map(id=>({id}))});
  await assert.rejects(()=>pending,/Выбор медиа изменён/);
  assert.equal(ctx.remaskSelectedCreativePreset,null);
});
