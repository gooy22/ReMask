const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const overlay = fs.readFileSync('railway-workspace-sync-fix-overlay.php','utf8');
const source = overlay.match(/\$newSync = <<<'JS'\n([\s\S]*?)\nJS;/)[1];
const applySource = overlay.match(/\$newApplySnapshot = <<<'JS'\n([\s\S]*?)\nJS;/)[1];

function snapshotPreservation() {
  const sandbox={state:{inventory:{
    profiles:[{name:'8',proxy:'saved proxy'},{name:'7'}],
    businesses:[{profile:'8',id:'11111111'},{profile:'7',id:'22222222'}],
    ad_accounts:[{profile:'8',id:'33333333'},{profile:'7',id:'44444444'}],
  }}};
  vm.createContext(sandbox); vm.runInContext(applySource,sandbox);
  const before=JSON.stringify(sandbox.state.inventory);
  assert.equal(sandbox.applySnapshot({profile:{name:'8'},sync_complete:false,
    businesses:[],ad_accounts:[]}),false);
  assert.equal(JSON.stringify(sandbox.state.inventory),before);
  assert.equal(sandbox.applySnapshot({profile:{name:'8',session_updated:true}}),true);
  assert.equal(sandbox.state.inventory.businesses.length,2);
  assert.equal(sandbox.state.inventory.ad_accounts.length,2);
  assert.equal(sandbox.state.inventory.profiles.length,2);
  assert.equal(sandbox.state.inventory.profiles.find(p=>p.name==='8').proxy,'saved proxy');
  // Explicitly verified empty arrays still clear this profile only.
  assert.equal(sandbox.applySnapshot({profile:{name:'8'},sync_complete:true,
    businesses:[],ad_accounts:[]}),true);
  assert.equal(sandbox.state.inventory.businesses.length,1);
  assert.equal(sandbox.state.inventory.ad_accounts.length,1);
  assert.equal(sandbox.state.inventory.profiles.length,2);
}

function businessOnlySnapshot() {
  const sandbox={state:{inventory:{profiles:[],businesses:[],ad_accounts:[]}}};
  vm.createContext(sandbox);vm.runInContext(applySource,sandbox);
  sandbox.applySnapshot({profile:{name:'9',user_id:'61594882851656'},sync_complete:true,
    businesses:[{id:'61594882851656'},{id:'934505709362142'}],ad_accounts:[
      {id:'2279305019588057',business_id:'61594882851656'},
      {id:'1152836437079070',business_id:'934505709362142'},
      {id:'123456789'},
      {id:'987654321',business_id:'934505709362142',is_personal:true}]});
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.state.inventory.businesses)).map(r=>r.id),['934505709362142']);
  assert.deepEqual(JSON.parse(JSON.stringify(sandbox.state.inventory.ad_accounts)).map(r=>r.id),['1152836437079070']);
}

async function sessionRefreshOutcome() {
  const refreshOverlay=fs.readFileSync('railway-profile-error-fix-overlay.php','utf8');
  const refresh=refreshOverlay.slice(refreshOverlay.indexOf('async function remaskSessionRefreshRun(){'),
    refreshOverlay.indexOf('function remaskSessionRefreshUpdateButton(){'));
  const status={textContent:''}; let applied=0,rendered=0;
  const ctx={window:{prompt:()=>JSON.stringify([
    {name:'c_user',value:'fixture-user'},{name:'xs',value:'fixture-session'},
  ])},remaskSelectedProfileNameForSessionRefresh:()=> '8',
    $:()=>status,post:x=>x,profileSaveJson:async()=>({session_updated:true}),
    apiJson:async()=>({sync_complete:false,sync_error:'LIVE_INVENTORY_TIMEOUT:rk_inventory'}),
    applySnapshot:()=>{applied++;return true;},render:()=>{rendered++;},updateSelectionUi(){}};
  vm.createContext(ctx);vm.runInContext(refresh,ctx);
  await assert.rejects(ctx.remaskSessionRefreshRun(),/LIVE_INVENTORY_TIMEOUT:rk_inventory/);
  assert.equal(applied,0); assert.equal(rendered,0);
  assert.ok(!status.textContent.includes('Meta синхронизирована'));
  ctx.apiJson=async()=>({sync_complete:true,sync_warnings:['Список РК проверен частично']});
  await ctx.remaskSessionRefreshRun();
  assert.equal(applied,1);assert.equal(rendered,1);
  assert.ok(status.textContent.includes('Список РК проверен частично'));
}

async function run(activeTab, rows) {
  const calls = [];
  const elements = new Map();
  const sandbox = {
    state:{running:false,activeTab}, selectedRows:()=>rows,
    $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
    updateSelectionUi(){},setProgress(){},render(){},
    setTimeout,clearTimeout,Date,Math,
    post:payload=>payload,applySnapshot:()=>true,
    apiJson:async(url,payload)=>{calls.push(payload);return {sync_complete:true};},
    concurrent:async(items,limit,fn)=>{const output=[];for(const item of items)output.push(await fn(item));return output;},
  };
  vm.createContext(sandbox);vm.runInContext(source,sandbox);
  await sandbox.syncSelection();
  return JSON.parse(JSON.stringify(calls));
}

(async()=>{
  snapshotPreservation();
  businessOnlySnapshot();
  await sessionRefreshOutcome();
  const bm = await run('businesses',[{profile:'8',id:'1632909278268870'}]);
  assert.equal(bm.length,1);
  assert.equal(bm[0].profile,'8');
  assert.equal(bm[0].business_id,'1632909278268870');
  const profiles = await run('profiles',[{name:'8'}]);
  assert.equal(profiles.length,1);
  assert.equal(profiles[0].profile,'8');
  assert.ok(!('business_id' in profiles[0]));
  const accounts = await run('ad_accounts',[
    {profile:'8',business_id:'1632909278268870',id:'act_1758104775449075'},
    {profile:'8',business_id:'1632909278268870',id:'act_1111111111'},
    {profile:'8',business_id:'1760742031708754',id:'act_2222222222'},
  ]);
  assert.equal(accounts.length,2);
  assert.deepEqual(accounts.map(r=>r.business_id),['1632909278268870','1760742031708754']);
  assert.ok(accounts.every(r=>r.profile==='8'));
  console.log('Selected BM/RK scope and full profile request passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
