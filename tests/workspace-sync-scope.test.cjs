const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const overlay = fs.readFileSync('railway-workspace-sync-fix-overlay.php','utf8');
const source = overlay.match(/\$newSync = <<<'JS'\n([\s\S]*?)\nJS;/)[1];

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
