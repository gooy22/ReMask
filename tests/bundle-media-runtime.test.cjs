const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'..','railway-private-launch-completion.js'),'utf8');
const bundleCode=source.slice(source.indexOf('async function remaskPrivateCaptureBundle()'),
  source.indexOf('\nfunction remaskPrivateInstallBudgetPeriod()'));
const ids=['m-'+'a'.repeat(24),'m-'+'b'.repeat(24)];
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
