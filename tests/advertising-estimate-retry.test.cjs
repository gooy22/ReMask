const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('railway-advertising-tools.js', 'utf8');
const estimate = source.slice(source.indexOf('    async function estimate('), source.indexOf('    function changed('));
function fixture(targets, failure) {
  const scheduled = [];
  let reads = 0;
  const current = {fingerprint:'fixture',targets,spec:{targeting:{geo_locations:{countries:['UA']}}}};
  const c = {document:{hidden:false,createElement:()=>({})},creative:false,
    snapshot:()=>current,blockedTargets:new Set(),lastFingerprint:'',generation:0,
    controller:null,needsRefresh:true,lastReadAt:0,timer:null,AbortController,Date,JSON,
    requestSpec:()=>({}),status:{},results:{querySelector:()=>null,appendChild:()=>{}},render:()=>{},
    read:async()=>{reads++;if(failure)throw new Error(failure);return {estimate_ready:false};},
    setTimeout:fn=>{const task={fn};scheduled.push(task);return task;},
    clearTimeout:()=>{},invalidate:()=>{c.generation++;c.lastFingerprint='';},};
  vm.createContext(c);vm.runInContext(estimate,c);
  return {c,scheduled,reads:()=>reads};
}
(async()=>{
  const target={profile:'28',account_id:'123456789'};
  for(const code of ['CHECKPOINT_REQUIRED','SESSION_EXPIRED','PRIVATE_ADS_DRAFT_BOOTSTRAP_SCOPE_UNVERIFIED']) {
    const f=fixture([target],code);await f.c.estimate();
    assert.equal(f.reads(),1);assert.equal(f.scheduled.length,0);assert.equal(f.c.needsRefresh,false);
    await f.c.estimate(true);assert.equal(f.reads(),1); // focus/change cannot reopen the gated scope
  }
  for(const failure of ['HTTP_UNAVAILABLE',null]) {
    const f=fixture([target],failure);await f.c.estimate();
    for(let i=0;i<5 && f.scheduled.length;i++) {
      f.scheduled.shift().fn();await new Promise(resolve=>setImmediate(resolve));
    }
    assert.equal(f.reads(),3);assert.equal(f.scheduled.length,0);assert.equal(f.c.needsRefresh,false);
  }
  const empty=fixture([],null);await empty.c.estimate();
  assert.equal(empty.reads(),0);assert.equal(empty.c.needsRefresh,false);
  console.log('Authentication/scope gates stop reads; transient/pending reads bounded to three; no unselected Launch reads.');
})().catch(error=>{console.error(error);process.exitCode=1;});
