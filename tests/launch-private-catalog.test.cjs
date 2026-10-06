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
assert.match(controls.reviewStatus.textContent,/Запуск рекламы недоступен/);
assert.equal(controls.preflight.textContent,'Загрузить сохранённые РК');
console.log('Private Launch catalog and funding truth checks passed');

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
