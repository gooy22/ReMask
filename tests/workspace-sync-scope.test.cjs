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

async function syncLatencyMetricsAreAggregateOnly() {
  const logs=[],elements=new Map(),profile='fixture-private-profile';
  const state={running:false,activeTab:'profiles'};
  const sandbox={
    state,selectedRows:()=>[{name:profile}],
    $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
    updateSelectionUi(){},setProgress(){},render(){},
    Date,Math,setTimeout,clearTimeout,post:payload=>payload,
    console:{info:(...args)=>logs.push(args.join(' '))},
    applySnapshot:()=>true,
    async apiJson(){
      return {sync_complete:true,sync_timings_ms:{
        php_context:12,worker_bridge:55,php_total:73,
        // Incoming arbitrary fields must never be logged:
        profile,proxy:'PRIVATE-CREDENTIAL'
      }};
    },
    async concurrent(items,limit,fn){
      const result=[];
      for(const item of items)result.push(await fn(item));
      return result;
    },
  };
  vm.createContext(sandbox);vm.runInContext(source,sandbox);
  await sandbox.syncSelection();
  const metric=state.lastSyncTimingMs;
  assert.equal(metric.ui_http_calls,1);
  assert.equal(metric.ui_http_parallel_peak,1);
  assert.equal(metric.ui_apply_count,1);
  assert.equal(metric.php_context_ms_sum,12);
  assert.equal(metric.worker_bridge_ms_sum,55);
  assert.equal(metric.php_total_ms_sum,73);
  assert.ok(metric.ui_wall_ms>=0 && metric.ui_render_ms>=0);
  assert.equal(state.running,false);
  assert.equal(logs.length,1);
  assert.ok(logs[0].includes('[ReMask sync latency]'));
  assert.ok(!logs[0].includes(profile));
  assert.ok(!logs[0].includes('PRIVATE-CREDENTIAL'));
}

async function stalledReconciliation() {
  const calls=[], elements=new Map(); let now=0, timerId=0;
  const timers=new Map();
  const sandbox={state:{running:false,activeTab:'profiles'},selectedRows:()=>[{name:'35'}],
    $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
    updateSelectionUi(){},setProgress(){},render(){},
    Date:{now:()=>now},Math,post:value=>value,
    setTimeout(callback,ms){const id=++timerId;timers.set(id,{callback,ms});return id;},
    clearTimeout:id=>timers.delete(id),
    applySnapshot(){throw Error('Failed reconciliation cannot replace inventory');},
    apiJson:async(url,payload)=>{
      calls.push(payload);
      if(payload.action==='sync_profile')throw Error('Failed to fetch');
      return new Promise(()=>{});
    },
    concurrent:async(items,limit,fn)=>Promise.all(items.map(fn)),
  };
  vm.createContext(sandbox);vm.runInContext(source,sandbox);
  const completion=sandbox.syncSelection();
  // Let the failed initial request reach its local-result reconciliation.
  for(let i=0;i<20&&!calls.some(call=>call.action==='sync_result');i++)await Promise.resolve();
  assert.equal(calls.filter(call=>call.action==='sync_profile').length,1);
  assert.equal(calls.filter(call=>call.action==='sync_result').length,1);
  const [id,timer]=Array.from(timers).find(([,value])=>value.ms<=3000)||[];
  assert.ok(timer,'local reconciliation requires a bounded timer');
  now+=timer.ms;timers.delete(id);timer.callback();
  await completion;
  assert.equal(sandbox.state.running,false);
  assert.equal(calls.length,2,'a hung status read must not queue further requests');
  assert.match(elements.get('workspaceStatus').textContent,/Failed to fetch/);
}

async function independentProfilesRunInParallelButSameProfileSerial() {
  const rows=[
    {profile:'alpha',id:'1111111111'},
    {profile:'alpha',id:'2222222222'},
    {profile:'bravo',id:'3333333333'},
    {profile:'charlie',id:'4444444444'},
  ];
  const active=new Set(),calls=[],finished=[],progress=[];
  let peak=0,sameProfileOverlap=false,poolLimit=0;
  const elements=new Map();
  const scope={
    state:{running:false,activeTab:'businesses'},selectedRows:()=>rows,
    $:id=>{if(!elements.has(id))elements.set(id,{});return elements.get(id);},
    updateSelectionUi(){},render(){},setProgress:(done,total)=>progress.push([done,total]),
    post:value=>value,applySnapshot:()=>true,Date,Math,setTimeout,clearTimeout,
    async apiJson(url,input){
      if(input.action!=='sync_profile')throw Error('Unexpected network route');
      calls.push({profile:input.profile,business:input.business_id});
      if(active.has(input.profile))sameProfileOverlap=true;
      active.add(input.profile);
      peak=Math.max(peak,active.size);
      await new Promise(resolve=>setTimeout(resolve,3));
      active.delete(input.profile);
      finished.push(input.profile+'|'+input.business_id);
      return {sync_complete:true};
    },
    async concurrent(items,limit,worker){
      poolLimit=limit;
      let next=0;
      await Promise.all(Array.from({length:Math.min(limit,items.length)},async()=>{
        for(;;){
          const index=next++;
          if(index>=items.length)return;
          await worker(items[index]);
        }
      }));
      return [];
    },
  };
  vm.createContext(scope);vm.runInContext(source,scope);
  await scope.syncSelection();
  assert.equal(poolLimit,2,'At most two independent profile reads per browser');
  assert.equal(peak,2,'Independent profiles must actually overlap');
  assert.equal(sameProfileOverlap,false,'The same profile must remain serial');
  assert.equal(calls.length,4,'Every selected BM must still be visited');
  assert.deepEqual(finished.filter(x=>x.startsWith('alpha|')),
    ['alpha|1111111111','alpha|2222222222']);
  assert.deepEqual(progress.at(-1),[4,4]);
  assert.equal(scope.state.running,false);
}

(async()=>{
  snapshotPreservation();
  businessOnlySnapshot();
  await sessionRefreshOutcome();
  await syncLatencyMetricsAreAggregateOnly();
  await independentProfilesRunInParallelButSameProfileSerial();
  await stalledReconciliation();
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
