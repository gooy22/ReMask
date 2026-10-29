const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('railway-advertising-tools.js', 'utf8');
const estimate = source.slice(source.indexOf('    async function estimate('), source.indexOf('    function changed('));
function fixture(targets, failure, geoLocations={countries:['UA']}) {
  const scheduled = [];
  const renderedErrors=[];
  let reads = 0;
  const current = {fingerprint:'fixture',targets,spec:{targeting:{geo_locations:geoLocations}}};
  const c = {document:{hidden:false,createElement:()=>({})},creative:false,
    snapshot:()=>current,blockedTargets:new Set(),lastFingerprint:'',generation:0,
    controller:null,needsRefresh:true,lastReadAt:0,timer:null,AbortController,Date,JSON,
    requestSpec:()=>({}),status:{textContent:''},results:{querySelector:()=>null,appendChild:node=>renderedErrors.push(node)},render:()=>{},
    read:async()=>{reads++;if(failure)throw typeof failure==='string'?new Error(failure):failure;return {estimate_ready:false};},
    setTimeout:fn=>{const task={fn};scheduled.push(task);return task;},
    clearTimeout:()=>{},invalidate:()=>{c.generation++;c.lastFingerprint='';},};
  vm.createContext(c);vm.runInContext(estimate,c);
  return {c,scheduled,reads:()=>reads,renderedErrors};
}
(async()=>{
  const target={profile:'28',account_id:'123456789'};
  for(const code of ['CHECKPOINT_REQUIRED','SESSION_EXPIRED','BUSINESS_LOGIN_GATE','TWO_FACTOR_REQUIRED','PRIVATE_ADS_DRAFT_BOOTSTRAP_SCOPE_UNVERIFIED']) {
    const f=fixture([target],code);await f.c.estimate();
    assert.equal(f.reads(),1);assert.equal(f.scheduled.length,0);assert.equal(f.c.needsRefresh,false);
    assert.doesNotMatch(f.c.status.textContent,new RegExp(code),'the status must not expose Meta error codes');
    assert.doesNotMatch(f.renderedErrors[0].textContent,new RegExp(code),'the per-RK message must not expose Meta error codes');
    if(code==='CHECKPOINT_REQUIRED')assert.match(f.c.status.textContent,/Чекпоинт\. Аккаунт остановлен; новые задачи запрещены/);
    if(code==='BUSINESS_LOGIN_GATE'||code==='SESSION_EXPIRED')assert.match(f.c.status.textContent,/Сессия завершена/);
    if(code==='TWO_FACTOR_REQUIRED')assert.match(f.c.status.textContent,/Нужно подтвердить вход/);
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
  const noGeo=fixture([target],null,{countries:[],regions:[],cities:[],location_types:['home','recent']});
  await noGeo.c.estimate();
  assert.equal(noGeo.reads(),0,'location type defaults are not a selected GEO result');
  assert.equal(noGeo.c.status.textContent,'Добавьте страну, регион или город.');
  assert.equal(noGeo.c.needsRefresh,false);
  for(const code of ['CHECKPOINT_REQUIRED','PRIVATE_HTTP_CONNECTION_UNAVAILABLE','PRIVATE_HTTP_PROTOCOL_ERROR','TEMPLATE_LOOKUP_SOURCE_UNAVAILABLE']){
    const error=new Error('Источник недоступен.');error.meta={code};
    const f=fixture([target],error);await f.c.estimate();
    assert.equal(f.reads(),1);assert.equal(f.scheduled.length,0,'structured failures must stop without matching a code in translated text');
  }
  const request=source.slice(source.indexOf('    async function read('),source.indexOf('    function render('));
  const calls=[];
  const context={URLSearchParams,document:{querySelector:()=>({content:'fixture-csrf'})},
    fetch:async(url,options)=>{calls.push({url,options});return {ok:false,text:async()=>JSON.stringify({ok:false,error:{code:'PRIVATE_HTTP_CONNECTION_UNAVAILABLE',message:'Соединение недоступно.'}})};}};
  vm.createContext(context);vm.runInContext(request,context);
  await assert.rejects(()=>context.read('audience_estimate',{include_forecast:false},{},null),error=>error.meta.code==='PRIVATE_HTTP_CONNECTION_UNAVAILABLE');
  assert.equal(calls[0].options.headers['X-ReMask-CSRF'],'fixture-csrf');
  console.log('Authentication/scope gates stop reads; transient/pending reads bounded to three; no unselected Launch reads.');
})().catch(error=>{console.error(error);process.exitCode=1;});
