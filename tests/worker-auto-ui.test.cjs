const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const {webcrypto} = require('node:crypto');
const source = fs.readFileSync('railway-python-worker-ui.js', 'utf8');
const start = source.indexOf('async function pythonWorkerOpenAutoModal()');
const end = source.indexOf('async function pythonWorkerStartFanPages', start);
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.value = ''; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.append(child); }
  addEventListener(kind, cb) { this.events[kind] = cb; }
}
const body = new Element('body'); const elements = [];
const captured = [];
const state = {workerOnline:true, busy:false};
const storage = new Map();
const sandbox = {document:{body,createElement(tag) { const el = new Element(tag); elements.push(el); return el; }},
  crypto:webcrypto, pythonWorkerUiState:state, pythonWorkerSelectedProfiles:()=>['7','8'],
  pythonWorkerEnsureBmModalStyle(){},pythonWorkerCloseOwnBmModal(){},pythonWorkerSelectionRefresh(){},
  pythonWorkerClearBatchState(){},pythonWorkerSetText(){},localStorage:{setItem(k,v){storage.set(k,v);},getItem(k){return storage.get(k)||null;},removeItem(k){storage.delete(k);}},
  async pythonWorkerBridge(payload) { captured.push(JSON.parse(JSON.stringify(payload))); if (captured.length === 1) throw Error('lost response'); return {job:{job_id:'saved-job'}}; },
  async pythonWorkerPoll(){}
};
vm.createContext(sandbox); vm.runInContext(source.slice(start,end), sandbox);
(async()=>{
  await sandbox.pythonWorkerOpenAutoModal();
  const inputs = elements.filter(el=>el.tag==='input');
  const create = elements.find(el=>el.textContent==='Запустить');
  const summary = elements.find(el=>el.className==='pwbm-status');
  assert.equal(create.disabled,false); assert.match(summary.textContent,/FP 2, BM 2, РК 2/);
  inputs[0].value='21'; inputs[0].events.input(); assert.equal(create.disabled,true);
  inputs[0].value='2'; inputs[0].events.input(); assert.match(summary.textContent,/FP 4, BM 4, РК 4/);
  await create.events.click(); assert.equal(state.busy,false); assert.equal(create.textContent,'Повторить отправку');
  assert.equal(inputs.every(el=>el.disabled),true);
  assert.ok(storage.has('remask_python_worker_auto_pending_v1'));
  elements.length=0;
  sandbox.pythonWorkerSelectedProfiles=()=>['99'];
  await sandbox.pythonWorkerOpenAutoModal();
  const restored = elements.find(el=>el.textContent==='Повторить отправку');
  assert.ok(restored); assert.ok(elements.some(el=>/профилей 7, 8/.test(el.textContent||'')));
  assert.equal(elements.filter(el=>el.tag==='input')[0].value,'2');
  await restored.events.click(); assert.equal(state.jobId,'saved-job');
  assert.equal(storage.has('remask_python_worker_auto_pending_v1'),false);
  assert.deepEqual(captured[0],captured[1], 'lost response retry must not change the accepted Job');
  assert.equal(captured[0].profiles.length,2);
  assert.equal(captured[0].profiles[0].tasks[0].payload.batch_count,2);
  assert.deepEqual(captured[0].profiles[0].tasks[0].payload.steps,['PROXY_CHECK','FAN_PAGES','BUSINESS','AD_ACCOUNT']);
  console.log('Auto modal: counts, validation, POST payload and lost-response idempotency passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
