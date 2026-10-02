const fs=require('node:fs');const vm=require('node:vm');const assert=require('node:assert/strict');
const source=fs.readFileSync('railway-cookie-profile-ui.js','utf8');
const elements=new Map();let modal,requests=[];
const ctx={JSON,Map,esc:String,openModal(title,html,label,submit){modal={title,html,label,submit};},
  $:id=>elements.get(id),post:x=>x,async apiJson(url,payload){requests.push({url,payload});return {next_number:8};},
  async loadInventory(){},closeModal(){},selectedRows:()=>[{name:'7'}]};
const apiSource=fs.readFileSync('railway-cookie-only-overlay.php','utf8').match(/<<<'COOKIE_API_JSON'\n([\s\S]*?)\nCOOKIE_API_JSON;/)[1];
ctx.fetch=async(url,payload)=>{requests.push({url,payload});return{ok:true,status:200,text:async()=>JSON.stringify({ok:true,next_number:8})};};
vm.createContext(ctx);vm.runInContext(apiSource,ctx);vm.runInContext(source,ctx);
const cookies=[{name:'c_user',value:'123456789'},{name:'xs',value:'test-fixture-only'}];
assert.equal(ctx.remaskCookieRows(JSON.stringify(cookies)).length,2);
assert.equal(ctx.remaskCookieRows(JSON.stringify({c_user:'123456789',xs:'test-fixture-only'})).length,2);
for(const raw of ['[]','{}','null','invalid',JSON.stringify([{name:'c_user',value:'123'}]),JSON.stringify([{name:'c_user',value:'123'},{name:'xs',value:''}]),JSON.stringify([...cookies,{name:'xs',value:'other-fixture'}])]){
  assert.throws(()=>ctx.remaskCookieRows(raw),raw);
}
(async()=>{
  const previousFetch=ctx.fetch;
  ctx.fetch=async()=>({ok:true,status:200,text:async()=>JSON.stringify({ok:true,data:{marker:'envelope'}})});
  assert.equal((await ctx.apiJson('fixture')).marker,'envelope');
  ctx.fetch=async()=>({ok:false,status:502,text:async()=>JSON.stringify({ok:false,error:'PAYMENT_UI_UNAVAILABLE'})});
  await assert.rejects(ctx.apiJson('fixture'),/PAYMENT_UI_UNAVAILABLE/);
  ctx.fetch=async()=>({ok:false,status:400,text:async()=>JSON.stringify({ok:false,error:'PROFILE_MANAGER_FAILED',message:'PROFILE_NUMBER_BUSY'})});
  await assert.rejects(ctx.apiJson('fixture'),/PROFILE_NUMBER_BUSY/);
  ctx.fetch=previousFetch;
  await ctx.prepareAddProfile();assert.ok(modal.html.includes('value="8" readonly'));requests=[];assert.equal(/newProfileToken|type="password"/.test(modal.html),false);
  for(const [id,value] of Object.entries({newProfileName:'Fixture',newProfileProxy:'http:127.0.0.1:8080:u:p',newProfileCookies:JSON.stringify(cookies)}))elements.set(id,{value});
  await modal.submit();assert.equal(requests.length,1);assert.equal('token' in requests[0].payload,false);
  assert.equal(requests[0].payload.action,'create');assert.equal(requests[0].payload.auto_number,'1');assert.equal(requests[0].url,'ajax/metaProfileManager.php');
  requests=[];elements.get('newProfileProxy').value='';await assert.rejects(modal.submit(),/Прокси обязателен/);assert.equal(requests.length,0);
  ctx.prepareEditProfile();assert.equal(/editToken|editClearSession/.test(modal.html),false);
  elements.set('editCookies',{value:''});elements.set('editProxy',{value:''});elements.set('editClearProxy',{checked:false});
  await modal.submit();assert.equal('token' in requests[0].payload,false);assert.equal(requests[0].payload.cookies,'');assert.equal(requests[0].payload.proxy,'');
  // Exercise the legacy Accounts module functions after removing its import.
  const legacy=fs.readFileSync('railway-cookie-accounts.js','utf8').replace(/^import .*\n/,'');
  const form={name:{value:'Fixture',readOnly:false},cookies:{value:JSON.stringify(cookies)},proxy:{value:'http:127.0.0.1:8080:u:p'}};
  let posts=[];const old={window:{addEventListener(){},location:{reload(){}}},document:{add:form},alert(){},
    Requests:{async post(url,body){posts.push({url,body});return{};},async checkResponse(){return{success:true};}}};
  vm.createContext(old);vm.runInContext(legacy,old);await old.addAccount();
  assert.deepEqual(posts.map(x=>x.url),['ajax/checkAccount.php','ajax/addAccount.php']);
  assert.equal(posts.some(x=>new URLSearchParams(x.body).has('token')),false);
  assert.equal(new URLSearchParams(posts[1].body).get('action'),'create');
  assert.equal(new URLSearchParams(posts[1].body).get('auto_number'),'1');
  old.fetch=async()=>({json:async()=>({ok:true,next_number:8})});
  await old.remaskLoadNextProfileNumber();assert.equal(form.name.value,'8');assert.equal(form.name.readOnly,true);
  posts=[];await old.addAccount();assert.equal(new URLSearchParams(posts[1].body).get('action'),'create');
  assert.equal(await old.validateForm('Fixture','',JSON.stringify(cookies),form.proxy.value,false),true);
  assert.equal(await old.validateForm('Fixture','','',form.proxy.value,false),false);
  console.log('Cookie-only Workspace and Accounts create/edit validation and tokenless requests passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
