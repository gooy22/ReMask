const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const workerJobsOverlay=fs.readFileSync('railway-python-worker-jobs-overlay.php','utf8');
const cardEndpoint=fs.readFileSync('railway-payment-card-endpoint.php','utf8');
assert.doesNotMatch(workerJobsOverlay,/RemaskPrivateLaunchCatalog::asset\([^\n]*'funding'/);
assert.match(workerJobsOverlay,/http_build_query\(\$query,'','&',PHP_QUERY_RFC3986\)/);
assert.match(cardEndpoint,/function card_asset_hint\(array \$input\)/);
assert.match(cardEndpoint,/card_worker_inspect\(\$profile,\$account,\$assetHint\)/);
assert.doesNotMatch(cardEndpoint,/card_worker_inspect\(\$profile,\$account\)/);
const rows=[{profile:'Fixture',id:'act_123456789',business_id:'123450001',name:'Fixture RK'},{profile:'Other',id:'act_987654321',business:{id:'987650001'},account_name:'Other RK'}];
const elements={};
function element(){return {value:'',innerHTML:'',children:[],handlers:{},disabled:false,checked:false,style:{},
  appendChild(n){this.children.push(n)},addEventListener(event,fn){this.handlers[event]=fn},querySelectorAll(){return []}};}
let requests=[],bindings=[],reviewResult=null,prepareResult=null,bindResult=null;const card={id:'card_fixture',brand:'Visa',last4:'1111',month:12,year:2099,label:'Fixture'};
const resumeStorage=new Map();
const resumeLocalStorage=new Map();
const sandbox={
  window:{screen:{colorDepth:24},innerHeight:900,innerWidth:1440,addEventListener:()=>{}},
  sessionStorage:{getItem:k=>resumeStorage.get(k)||null,setItem:(k,v)=>resumeStorage.set(k,v),removeItem:k=>resumeStorage.delete(k)},
  localStorage:{getItem:k=>resumeLocalStorage.get(k)||null,setItem:(k,v)=>resumeLocalStorage.set(k,v),removeItem:k=>resumeLocalStorage.delete(k)},
  selectedRows:()=>rows,esc:x=>x,openModal:()=>{},$:id=>elements[id]||(elements[id]=element()),
  document:{createElement:()=>element()},post:x=>x,render:()=>{},setProgress:()=>{},
  concurrent:async(items,limit,fn,done)=>{assert.equal(limit,1);for(let i=0;i<items.length;i++)done(i+1,items.length,await fn(items[i]),i)},
  apiJson:async(url,body)=>{requests.push({url,body});if(body.action==='list')return {cards:[card],bindings};
    if(body.action==='add'){assert.equal(body.cvv,undefined);return {card}}
    if(body.action==='bind')return {result:bindResult||{status:'SUBMITTED_UNVERIFIED',code:'CARD_LINK_NOT_VERIFIED',submitted:true}};
    if(body.action==='prepare')return {result:prepareResult||{status:'FORM_READY',code:'CARD_FORM_READY',submitted:false}};
    if(body.action==='reconcile')return {result:reviewResult||{status:'SUBMITTED_UNVERIFIED',code:'CARD_RECONCILE_UNVERIFIED',submitted:false,funding:{verification_status:'UNVERIFIED',funding_verified:false}}};
    return {funding:{verification_status:'LINKED',account_scope_verified:true,card_linked:true,funding_verified:false,payment_methods:[{type:'Visa',last4:'1111'}]}}}
};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync('railway-payment-inspection-ui.js','utf8'),sandbox);
(async()=>{
  await sandbox.showFunding();assert.equal(requests.length,1);assert.equal(requests[0].body.action,'list');
  assert.ok(elements.paymentCardSelect.children[0].textContent.includes('•••• 1111'));
  assert.ok(elements.paymentCardBind.handlers.click);assert.ok(elements.paymentCardSaveBind.handlers.click);
  sandbox.$('paymentCardNumber').value='4111111111111111';sandbox.$('paymentCardExpiry').value='12/99';
  for(const key of ['holder','country','address','city','region','postal_code','label'])sandbox.$('paymentCard_'+key).value='';
  await sandbox.savePaymentCard();assert.equal(elements.paymentCardNumber.value,'');
  const save=requests.find(r=>r.body.action==='add');assert.equal(save.body.cvv,undefined);
  const container=element();requests=[];
  bindResult={status:'LINKED',code:'CARD_LINK_OBSERVED',submitted:true};
  await sandbox.bindPaymentCard(rows,card,'123',container);
  assert.equal(requests.length,2);assert.equal(requests[0].body.profile,'Fixture');assert.equal(requests[1].body.profile,'Other');
  assert.equal(requests[0].body.business_id,'123450001');assert.equal(requests[0].body.account_name,'Fixture RK');
  assert.equal(requests[1].body.business_id,'987650001');assert.equal(requests[1].body.account_name,'Other RK');
  assert.equal(requests[0].body.setup_country,'UA');assert.equal(requests[0].body.setup_country_mode,'prefer_ua');
  assert.deepEqual(JSON.parse(requests[0].body.client_info),{color_depth:'24',java_enabled:false,screen_height:'900',screen_width:'1440'});
  assert.equal(requests[0].body.setup_currency,'USD');assert.equal(requests[0].body.setup_timezone,'Europe/Kyiv');
  bindResult=null;
  sandbox.$('paymentSetupCountry').value='CURRENT';
  assert.equal(sandbox.paymentSetupPayload().setup_country_mode,'current');
  assert.equal(sandbox.paymentSetupPayload().setup_country,'UA');
  const countryMessage=sandbox.paymentCardMessage({code:'CARD_FORM_READY',billing_setup_observed:{country_label:'Bangladesh',country_preserved:true,country_reason:'meta_control_locked',saved:false}});
  assert.ok(countryMessage.includes('Bangladesh'));assert.ok(countryMessage.includes('заблокировано Meta'));assert.ok(countryMessage.includes('пока не подтверждено'));
  assert.ok(sandbox.paymentCardMessage({code:'CARD_FORM_READY',card_availability:'only_this_account'}).includes('Только этот РК'));
  assert.ok(sandbox.paymentCardMessage({code:'PAYMENT_UI_UNAVAILABLE'}).includes('Billing / Payments'));
  assert.ok(sandbox.paymentCardMessage({code:'PERSONAL_AD_ACCOUNT_EXCLUDED'}).includes('внутри BM'));
  assert.ok(sandbox.paymentCardMessage({code:'INVALID_PAYMENT_TARGET'}).includes('проверку идентификатора'));
  assert.ok(sandbox.paymentCardMessage({code:'CARD_MASK_COLLISION_PREEXISTING'}).includes('Save не нажат'));
  const linkedMessage=sandbox.paymentCardMessage({code:'CARD_LINK_OBSERVED'});
  assert.ok(linkedMessage.includes('только привязка'));
  assert.ok(linkedMessage.includes('не проверялись'));
  const bankMessage=sandbox.paymentCardMessage({code:'CARD_BANK_CONFIRMATION_REQUIRED'});
  assert.ok(bankMessage.includes('затем нажмите «Проверить результат»'));
  assert.ok(bankMessage.includes('Не отправляйте карту повторно'));
  assert.ok(sandbox.paymentCardAuthEvidence({code:'CHECKPOINT_REQUIRED',diagnostic:{facebook_route:'/checkpoint'}}).includes('/checkpoint'));
  assert.equal(sandbox.paymentCardAuthEvidence({code:'CHECKPOINT_REQUIRED'}),'');
  assert.ok(!sandbox.paymentCardMessage({code:'CARD_FORM_READY',card_availability:'not_exposed'}).includes('Только этот РК'));
  const scopeFailure=sandbox.paymentCardMessage({code:'CARD_ACCOUNT_SCOPE_UNVERIFIED'});
  assert.ok(scopeFailure.includes('Карта не отправлена'));assert.ok(scopeFailure.includes('всего BM остановлено'));
  const unverifiedContainer=element();requests=[];await sandbox.bindPaymentCard(rows,card,'123',unverifiedContainer);
  assert.ok(unverifiedContainer.children[0].textContent.includes('Повторное добавление остаётся заблокированным'));
  assert.deepEqual(requests.map(r=>r.body.action),['bind','reconcile']);
  assert.equal(rows[0].funding.funding_verified,false);
  assert.ok(!JSON.stringify(container.children).includes('4111111111111111'));
  sandbox.$('paymentCardCvv').value='123';sandbox.remaskClearPaymentSecrets();assert.equal(elements.paymentCardCvv.value,'');
  requests=[];await assert.rejects(()=>sandbox.bindPaymentCard(rows,null,'123',container));assert.equal(requests.length,0);
  await assert.rejects(()=>sandbox.bindPaymentCard(rows,card,'x',container));assert.equal(requests.length,0);
  const read=element();await sandbox.inspectFundingRows(rows,read);
  assert.ok(read.children[0].textContent.includes('•••• 1111'));assert.ok(read.children[0].textContent.includes('не подтверждена'));
  requests=[];await sandbox.bindPaymentCard(rows,card,'',element());
  assert.equal(requests.length,2);assert.ok(requests.every(r=>r.body.cvv===undefined));
  sandbox.$('paymentCardCvv').value='123';requests=[];
  await elements.paymentCardPrepare.handlers.click();
  assert.equal(elements.paymentCardCvv.value,'123');assert.ok(requests.every(r=>r.body.cvv===undefined));
  requests=[];await elements.paymentCardInspect.handlers.click();assert.equal(elements.paymentCardCvv.value,'123');
  assert.equal(requests.filter(r=>r.body.action==='payment_status').length,2);
  assert.equal(requests[0].body.business_id,'123450001');assert.equal(requests[1].body.business_id,'987650001');
  elements.paymentCardSelect.value=card.id;await elements.paymentCardBind.handlers.click();
  assert.equal(elements.paymentCardCvv.value,'');
  sandbox.$('paymentCardCvv').value='123';elements.paymentCardSelect.handlers.change();assert.equal(elements.paymentCardCvv.value,'');
  bindings=[{profile:'Fixture',account_id:'123456789',card_id:card.id,last4:'1111',status:'SUBMITTED_UNVERIFIED'},
    {profile:'Unselected',account_id:'444444444',card_id:card.id,last4:'1111',status:'IN_PROGRESS'}];
  await sandbox.showFunding();elements.paymentCardSelect.value=card.id;elements.paymentCardCvv.value='123';requests=[];
  assert.equal(elements.paymentCardBind.textContent,'Продолжить привязку — 1 РК');
  assert.equal(elements.paymentCardCvvField.hidden,false);
  await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='bind').length,1);
  assert.equal(requests.find(r=>r.body.action==='bind').body.profile,'Other');
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,1,
    'the newly submitted card is immediately checked against Meta');
  bindings.push({profile:'Other',account_id:'987654321',card_id:card.id,last4:'1111',status:'SUBMITTED_UNVERIFIED'});
  await sandbox.showFunding();elements.paymentCardSelect.value=card.id;
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');assert.equal(elements.paymentCardCvvField.hidden,true);
  assert.equal(elements.paymentCardSaveBind.disabled,true);
  sandbox.$('paymentCardCvv').value='123';requests=[];
  await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,2);
  assert.ok(requests.filter(r=>r.body.action==='reconcile').every(r=>r.body.business_id));
  assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  assert.ok(requests.every(r=>r.body.cvv===undefined&&r.body.number===undefined));
  assert.ok(requests.every(r=>r.body.profile!=='Unselected'));
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');
  requests=[];await elements.paymentCardSaveBind.handlers.click();assert.equal(requests.length,0);
  reviewResult={status:'SUBMITTED_UNVERIFIED',code:'CARD_RECONCILE_NO_METHOD',submitted:false,
    retry_review:{token:'fixture-review',expires_at:new Date(Date.now()+300000).toISOString()},funding:{verification_status:'NONE'}};
  requests=[];await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  assert.equal(elements.paymentCardRetryField.hidden,false);
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');
  assert.equal(elements.paymentCardCvvField.hidden,true);
  elements.paymentCardRetryConfirmed.checked=true;elements.paymentCardRetryConfirmed.handlers.change();
  assert.equal(elements.paymentCardBind.textContent,'Повторить привязку');assert.equal(elements.paymentCardCvvField.hidden,false);
  requests=[];await elements.paymentCardBind.handlers.click();assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  elements.paymentCardCvv.value='123';requests=[];await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='bind').length,1,
    'an unverified reviewed retry stops the rest of the card batch');
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,1);
  assert.ok(requests.filter(r=>r.body.action==='bind').every(r=>r.body.retry_confirmed==='1'&&r.body.retry_review==='fixture-review'));
  assert.equal(elements.paymentCardRetryConfirmed.checked,false);assert.equal(elements.paymentCardCvv.value,'');
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');
  reviewResult.retry_review.expires_at=new Date(Date.now()-1000).toISOString();requests=[];
  await elements.paymentCardBind.handlers.click();assert.equal(elements.paymentCardRetryField.hidden,true);
  elements.paymentCardRetryConfirmed.checked=true;elements.paymentCardRetryConfirmed.handlers.change();
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');
  assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  reviewResult.retry_review.expires_at=new Date(Date.now()+300000).toISOString();
  await elements.paymentCardBind.handlers.click();elements.paymentCardSelect.handlers.change();
  assert.equal(elements.paymentCardRetryField.hidden,true);assert.equal(elements.paymentCardRetryConfirmed.checked,false);
  const selectedRows=sandbox.selectedRows;sandbox.selectedRows=()=>[rows[0]];
  bindings=[{profile:'Fixture',account_id:'123456789',card_id:card.id,last4:'1111',status:'ACTION_REQUIRED',last_result_code:'CARD_BANK_CONFIRMATION_REQUIRED'}];
  await sandbox.showFunding();elements.paymentCardSelect.value=card.id;
  assert.ok(elements.paymentCardAssignments.children.at(-1).textContent.includes('затем нажмите «Проверить результат»'));
  requests=[];await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,1);
  assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  sandbox.selectedRows=selectedRows;
  bindings=[];await sandbox.showFunding();elements.paymentCardSelect.value=card.id;
  prepareResult={status:'BLOCKED',code:'CARD_BILLING_FIELDS_REQUIRED',missing_fields:['holder','postal_code','number','cvv']};
  requests=[];await elements.paymentCardPrepare.handlers.click();
  assert.ok(requests.every(r=>r.body.card_id===card.id));
  assert.ok(elements.paymentCardBillingMissing.innerHTML.includes('paymentCardExisting_holder'));
  assert.ok(!elements.paymentCardBillingMissing.innerHTML.includes('Existing_number')&&!elements.paymentCardBillingMissing.innerHTML.includes('Existing_cvv'));
  requests=[];await elements.paymentCardBind.handlers.click();assert.equal(requests.filter(r=>r.body.action==='bind').length,0);
  sandbox.$('paymentCardExisting_holder').value='Fixture Holder';sandbox.$('paymentCardExisting_postal_code').value='00000';
  requests=[];elements.paymentCardCvv.value='123';await elements.paymentCardBind.handlers.click();
  const update=requests.find(r=>r.body.action==='billing_update');assert.equal(update.body.card_id,card.id);
  assert.equal(update.body.holder,'Fixture Holder');assert.equal(update.body.cvv,undefined);assert.equal(update.body.number,undefined);
  assert.ok(requests.findIndex(r=>r.body.action==='billing_update')<requests.findIndex(r=>r.body.action==='bind'));
  bindResult={status:'BLOCKED',code:'CARD_BILLING_FIELDS_REQUIRED',missing_fields:['city']};
  await elements.paymentCardBind.handlers.click();assert.ok(elements.paymentCardBillingMissing.innerHTML.includes('paymentCardExisting_city'));
  requests=[];const billingBatch=element();bindResult={status:'BLOCKED',code:'CARD_BILLING_FIELDS_REQUIRED',missing_fields:['city']};
  await sandbox.bindPaymentCard(rows,card,'123',billingBatch);
  assert.equal(requests.filter(r=>r.body.action==='bind').length,1);
  assert.ok(billingBatch.children[1].textContent.includes('не запускался'));
  bindings=[];await sandbox.showFunding();assert.equal(elements.paymentCardBind.textContent,'Привязать карту');
  assert.equal(elements.paymentCardCvvField.hidden,false);
  const ten=Array.from({length:10},(_,i)=>({profile:'Profile'+i,id:'act_'+(100000000+i)}));
  const states=[{...ten[0],account_id:'100000000',card_id:card.id,status:'LINKED'},
    {...ten[1],account_id:'100000001',card_id:card.id,status:'SUBMITTED_UNVERIFIED'},
    {...ten[2],account_id:'100000002',card_id:card.id,status:'ACTION_REQUIRED'},
    {...ten[3],account_id:'100000003',card_id:'card_previous',status:'IN_PROGRESS'}];
  const plan=sandbox.paymentCardTargetPlan([...ten,ten[4]],states,card.id);
  assert.equal(plan.fresh.length,6);assert.equal(plan.pending.length,2);
  assert.equal(plan.linked.length,1);assert.equal(plan.blocked.length,1);
  const beforeSave=sandbox.paymentCardTargetPlan([ten[2]],[{...states[2],submitted:false}],card.id);
  assert.equal(beforeSave.fresh.length,1);assert.equal(beforeSave.pending.length,0,
    'an observed pre-submit gate must allow continuation after resolving it');
  const bankSubmitted=sandbox.paymentCardTargetPlan([ten[2]],[{...states[2],submitted:true}],card.id);
  assert.equal(bankSubmitted.pending.length,1,'bank submission must remain reconciliation-only');
  requests=[];bindResult={status:'LINKED',code:'CARD_LINK_OBSERVED'};
  await sandbox.bindPaymentCard(plan.fresh,card,'123',element());
  assert.equal(requests.length,6);assert.equal(new Set(requests.map(r=>r.body.account_id)).size,6);
  assert.ok(requests.every(r=>r.body.number===undefined&&Number(r.body.account_id.replace('act_',''))>=100000004));
  requests=[];bindResult={status:'ACTION_REQUIRED',code:'CARD_BANK_CONFIRMATION_REQUIRED'};
  const guardedBatch=element();await sandbox.bindPaymentCard(ten.slice(0,3),card,'123',guardedBatch);
  assert.equal(requests.length,1,'a bank challenge must stop further card submissions in the batch');
  assert.ok(guardedBatch.children.some(child=>String(child.textContent||'').includes('пакет остановлен')));
  requests=[];bindResult={status:'SUBMITTED_UNVERIFIED',code:'CARD_LINK_NOT_VERIFIED',submitted:true};
  reviewResult={status:'LINKED',code:'CARD_LINK_OBSERVED',submitted:false,
    funding:{verification_status:'LINKED',account_scope_verified:true,funding_verified:false}};
  const autoConfirmedBatch=element();await sandbox.bindPaymentCard(ten.slice(0,3),card,'123',autoConfirmedBatch);
  assert.deepEqual(requests.map(r=>r.body.action),['bind','reconcile','bind','reconcile','bind','reconcile']);
  assert.equal(autoConfirmedBatch.children.filter(child=>String(child.textContent||'').includes('Meta показывает карту у выбранного РК')).length,3,
    'live Meta confirmation should allow the selected-account batch to continue');
  requests=[];reviewResult=null;
  const uncertainBatch=element();await sandbox.bindPaymentCard(ten.slice(0,3),card,'123',uncertainBatch);
  assert.deepEqual(requests.map(r=>r.body.action),['bind','reconcile'],
    'an unverified Meta submission must be rechecked once and stop the remaining batch');
  assert.ok(uncertainBatch.children.some(child=>String(child.textContent||'').includes('пакет остановлен')));
  requests=[];bindResult={status:'SUBMITTED_UNVERIFIED',code:'CARD_FLOW_TIMEOUT',submitted:null};reviewResult=null;
  const unknownBoundaryBatch=element();await sandbox.bindPaymentCard(ten.slice(0,3),card,'123',unknownBoundaryBatch);
  assert.deepEqual(requests.map(r=>r.body.action),['bind','reconcile'],
    'an unknown Save boundary must be reconciled once and stop the remaining batch');
  assert.ok(unknownBoundaryBatch.children.some(child=>String(child.textContent||'').includes('пакет остановлен')));
  sandbox.paymentCardResumeWrite(rows,card.id);
  const persisted=[...resumeLocalStorage.values()][0];
  assert.ok(!persisted.includes('4111111111111111'));assert.ok(!persisted.includes('cvv'));
  assert.ok(!persisted.includes('retry_review'));assert.ok(!persisted.includes('funding'));
  const recovered=sandbox.paymentCardResumeRead();assert.equal(recovered.rows.length,2);
  assert.equal(recovered.rows[0].business_id,'123450001');assert.equal(recovered.cardId,card.id);
  resumeLocalStorage.clear();resumeStorage.set('remask_payment_card_batch_v1',persisted);
  assert.equal(sandbox.paymentCardResumeRead().cardId,card.id);
  assert.equal(resumeLocalStorage.get('remask_payment_card_batch_v1'),persisted,'legacy session state migrates to durable storage');
  bindings=[{profile:rows[0].profile,account_id:'123456789',card_id:card.id,status:'IN_PROGRESS'}];
  requests=[];elements.paymentCardCvv.value='';await sandbox.showFunding(recovered);
  assert.equal(elements.paymentCardSelect.value,card.id);
  assert.deepEqual(requests.map(r=>r.body.action),['list']);
  assert.ok(elements.paymentCardProgress.textContent.includes('восстановлена'));
  assert.equal(elements.paymentCardBind.textContent,'Продолжить привязку — 1 РК');
  assert.equal(elements.paymentCardCvv.value,'');
  bindings=[{profile:rows[0].profile,account_id:'123456789',card_id:card.id,status:'LINKED'}];
  await sandbox.showFunding();elements.paymentCardSelect.value=card.id;elements.paymentCardCvv.value='123';requests=[];
  await elements.paymentCardInspect.handlers.click();
  assert.deepEqual(requests.map(r=>r.body.action),['reconcile','reconcile','list'],
    'selected-card live inspection must reconcile persistent links and reload target state');
  assert.ok(requests.every(r=>r.body.cvv===undefined&&r.body.number===undefined));
  assert.equal(elements.paymentCardCvv.value,'123','read-only reconciliation must not consume CVV');
  console.log('card interface: save without CVV, masked selection, serial exact targets, uncertain results and secret clearing passed');
})().catch(e=>{console.error(e);process.exitCode=1});
