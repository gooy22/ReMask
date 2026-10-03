const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const rows=[{profile:'Fixture',id:'act_123456789'},{profile:'Other',id:'act_987654321'}];
const elements={};
function element(){return {value:'',innerHTML:'',children:[],handlers:{},disabled:false,
  appendChild(n){this.children.push(n)},addEventListener(event,fn){this.handlers[event]=fn},querySelectorAll(){return []}};}
let requests=[];const card={id:'card_fixture',brand:'Visa',last4:'1111',month:12,year:2099,label:'Fixture'};
const sandbox={
  selectedRows:()=>rows,esc:x=>x,openModal:()=>{},$:id=>elements[id]||(elements[id]=element()),
  document:{createElement:()=>element()},post:x=>x,render:()=>{},setProgress:()=>{},
  concurrent:async(items,limit,fn,done)=>{assert.equal(limit,1);for(let i=0;i<items.length;i++)done(i+1,items.length,await fn(items[i]),i)},
  apiJson:async(url,body)=>{requests.push({url,body});if(body.action==='list')return {cards:[card],bindings:[]};
    if(body.action==='add'){assert.equal(body.cvv,undefined);return {card}}
    if(body.action==='bind')return {result:{status:'SUBMITTED_UNVERIFIED',code:'CARD_LINK_NOT_VERIFIED',submitted:true}};
    if(body.action==='prepare')return {result:{status:'FORM_READY',code:'CARD_FORM_READY',submitted:false}};
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
  console.log('card interface: save without CVV, masked selection, serial exact targets, uncertain results and secret clearing passed');
})().catch(e=>{console.error(e);process.exitCode=1});
