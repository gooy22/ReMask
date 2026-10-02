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
  const readiness=overlay.match(/<<<'READINESS_UI'\n([\s\S]*?)\nREADINESS_UI/)[1];
  const output={}; let request;
  const ctx={selectedRows:()=>[{id:'act_333333333',profile:'7',name:'Saved RK'}],
    openModal(){},$:id=>output[id]||(output[id]={appendChild(node){this.html=node.innerHTML;}}),
    document:{createElement:()=>({})},esc:String,pill:(label)=>label,post:x=>x,setProgress(){},
    apiJson:async(url,input)=>{request={url,input};return {status:'NOT_VERIFIED',pages:{data:[{id:'222222222',name:'Existing Page'}]},funding:{status:'NOT_CHECKED'}};},
    concurrent:async(rows,n,fn,cb)=>{assert.equal(n,1);const result=await fn(rows[0]);cb(1,1,result,0);return [result];}};
  vm.createContext(ctx);vm.runInContext(readiness,ctx);await ctx.checkAssetsSelection();
  assert.equal(request.url,'ajax/metaAssetReadiness.php');
  assert.equal(request.input.profile,'7');
  assert.equal(request.input.account_id,'act_333333333');
  assert.match(output.assetReadinessRows.html,/Existing Page · 222222222/);
  assert.match(output.assetReadinessRows.html,/ДОСТУП FP НЕ ПРОВЕРЕН/);
  assert.doesNotMatch(output.assetReadinessRows.html,/ASSETS READY|PAGE ISSUE|EMPTY/);
  assert.match(output.assetReadinessProgress.textContent,/ещё не подтверждает/);
  ctx.apiJson=async()=>({error:'profile scope mismatch'});await ctx.checkAssetsSelection();
  assert.match(output.assetReadinessRows.html,/profile scope mismatch/);
  assert.doesNotMatch(output.assetReadinessRows.html,/Existing Page/);
  console.log('Private Workspace Assets scope and unverified access checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
