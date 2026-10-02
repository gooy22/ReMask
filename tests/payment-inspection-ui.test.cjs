const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const rows = [{profile:'Fixture',id:'act_123456789',funding:{funding_source:'stale'}}];
const results = [];
let requested = null;
const sandbox = {
  selectedRows:()=>rows, openModal:()=>{}, $:()=>({appendChild:n=>results.push(n)}),
  document:{createElement:()=>({className:'',textContent:''})}, post:x=>x,
  apiJson:async(url,body)=>{requested={url,body};return {funding:{
    verification_status:'LINKED',card_linked:true,account_scope_verified:true,
    payment_methods:[{type:'Visa',last4:'1234'}],funding_verified:false}};},
  concurrent:async(items,limit,fn,done)=>{
    assert.equal(limit,1);
    for(let i=0;i<items.length;i++)done(i+1,items.length,await fn(items[i]),i);
  }, setProgress:()=>{},render:()=>{},
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('railway-payment-inspection-ui.js','utf8'),sandbox);
(async()=>{
  await sandbox.showFunding();
  assert.equal(requested.url,'ajax/pythonWorkerJobs.php');
  assert.equal(requested.body.action,'payment_status');
  assert.equal(requested.body.profile,'Fixture');
  assert.equal(rows[0].funding.funding_verified,false);
  assert.ok(results[0].textContent.includes('•••• 1234'));
  assert.ok(results[0].textContent.includes('не подтверждена'));
  sandbox.apiJson=async()=>{throw new Error('CHECKPOINT_REQUIRED')};
  results.length=0;
  await sandbox.showFunding();
  assert.equal(rows[0].funding.funding_verified,false);
  assert.equal(rows[0].funding.verification_status,'UNVERIFIED');
  assert.ok(results[0].textContent.includes('Meta требует проверки аккаунта'));
  console.log('private payment UI: serial inspection, masking, scope and auth failure passed');
})().catch(error=>{console.error(error);process.exitCode=1});
