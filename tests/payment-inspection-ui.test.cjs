const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const rows=[{profile:'Fixture',id:'act_123456789'},{profile:'Other',id:'act_987654321'}];
const elements={};
function element(){return {value:'',innerHTML:'',children:[],handlers:{},disabled:false,checked:false,style:{},
  appendChild(n){this.children.push(n)},addEventListener(event,fn){this.handlers[event]=fn},querySelectorAll(){return []}};}
let requests=[],bindings=[],reviewResult=null,prepareResult=null,bindResult=null;const card={id:'card_fixture',brand:'Visa',last4:'1111',month:12,year:2099,label:'Fixture'};
const sandbox={
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
  await sandbox.bindPaymentCard(rows,card,'123',container);
  assert.equal(requests.length,2);assert.equal(requests[0].body.profile,'Fixture');assert.equal(requests[1].body.profile,'Other');
  assert.equal(requests[0].body.setup_country,'UA');assert.equal(requests[0].body.setup_country_mode,'prefer_ua');
  assert.equal(requests[0].body.setup_currency,'USD');assert.equal(requests[0].body.setup_timezone,'Europe/Kyiv');
  sandbox.$('paymentSetupCountry').value='CURRENT';
  assert.equal(sandbox.paymentSetupPayload().setup_country_mode,'current');
  assert.equal(sandbox.paymentSetupPayload().setup_country,'UA');
  const countryMessage=sandbox.paymentCardMessage({code:'CARD_FORM_READY',billing_setup_observed:{country_label:'Bangladesh',country_preserved:true,country_reason:'meta_control_locked',saved:false}});
  assert.ok(countryMessage.includes('Bangladesh'));assert.ok(countryMessage.includes('заблокировано Meta'));assert.ok(countryMessage.includes('пока не подтверждено'));
  assert.ok(sandbox.paymentCardMessage({code:'CARD_FORM_READY',card_availability:'only_this_account'}).includes('Только этот РК'));
  assert.ok(!sandbox.paymentCardMessage({code:'CARD_FORM_READY',card_availability:'not_exposed'}).includes('Только этот РК'));
  const scopeFailure=sandbox.paymentCardMessage({code:'CARD_ACCOUNT_SCOPE_UNVERIFIED'});
  assert.ok(scopeFailure.includes('Карта не отправлена'));assert.ok(scopeFailure.includes('всего BM остановлено'));
  assert.ok(container.children[0].textContent.includes('Повторное добавление остановлено'));
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
  await elements.paymentCardInspect.handlers.click();assert.equal(elements.paymentCardCvv.value,'123');
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
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,0);
  bindings.push({profile:'Other',account_id:'987654321',card_id:card.id,last4:'1111',status:'SUBMITTED_UNVERIFIED'});
  await sandbox.showFunding();elements.paymentCardSelect.value=card.id;
  assert.equal(elements.paymentCardBind.textContent,'Проверить результат');assert.equal(elements.paymentCardCvvField.hidden,true);
  assert.equal(elements.paymentCardSaveBind.disabled,true);
  sandbox.$('paymentCardCvv').value='123';requests=[];
  await elements.paymentCardBind.handlers.click();
  assert.equal(requests.filter(r=>r.body.action==='reconcile').length,2);
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
  assert.equal(requests.filter(r=>r.body.action==='bind').length,2);
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
  bindings=[];await sandbox.showFunding();assert.equal(elements.paymentCardBind.textContent,'Привязать и проверить');
  assert.equal(elements.paymentCardCvvField.hidden,false);
  const ten=Array.from({length:10},(_,i)=>({profile:'Profile'+i,id:'act_'+(100000000+i)}));
  const states=[{...ten[0],account_id:'100000000',card_id:card.id,status:'LINKED'},
    {...ten[1],account_id:'100000001',card_id:card.id,status:'SUBMITTED_UNVERIFIED'},
    {...ten[2],account_id:'100000002',card_id:card.id,status:'ACTION_REQUIRED'},
    {...ten[3],account_id:'100000003',card_id:'card_previous',status:'IN_PROGRESS'}];
  const plan=sandbox.paymentCardTargetPlan([...ten,ten[4]],states,card.id);
  assert.equal(plan.fresh.length,6);assert.equal(plan.pending.length,2);
  assert.equal(plan.linked.length,1);assert.equal(plan.blocked.length,1);
  requests=[];bindResult={status:'LINKED',code:'CARD_LINK_OBSERVED'};
  await sandbox.bindPaymentCard(plan.fresh,card,'123',element());
  assert.equal(requests.length,6);assert.equal(new Set(requests.map(r=>r.body.account_id)).size,6);
  assert.ok(requests.every(r=>r.body.number===undefined&&Number(r.body.account_id.replace('act_',''))>=100000004));
  console.log('card interface: save without CVV, masked selection, serial exact targets, uncertain results and secret clearing passed');
})().catch(e=>{console.error(e);process.exitCode=1});
