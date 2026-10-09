// Runs only isolated public source and synthetic data. Never loaded by the worker.
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto} = require('node:crypto');
const path = require('node:path');
const modules = {};
const asyncToGenerator = fn => function(...args) {
  const gen = fn.apply(this, args);
  return new Promise((resolve, reject) => {
    function step(method, arg) {
      let result; try {result = gen[method](arg);} catch(e) {reject(e); return;}
      if(result.done) resolve(result.value);
      else Promise.resolve(result.value).then(x=>step('next',x),e=>step('throw',e));
    }
    step('next');
  });
};
const encode = s=>Buffer.from(s, 'binary').toString('base64url');
const deferred = () => ({__setRef(){return this}, onReady(){}});
const fbt = {_:s=>s,_param:()=>null};
const stubs = {
  asyncToGeneratorRuntime:{asyncToGenerator}, Promise,
  requireDeferred:deferred, FBPayBase64URL:{encode}, FBPayCometBase64URL:{encode},
  react:{useContext:()=>null}, uuidv4:()=> '01234567-89ab-4cde-8123-456789abcdef',
  BillingContactFieldValidators:{}, BillingCreditCardConstants:{},
  BillingWizardRootUPLogger:{getLoggingData:()=>({session_id:'synthetic-session'})},
  BillingPTTSharedUtils:{generatePTTInputFields:csc=>({authDataFields:{csc:'$e2ee'},paymentType:'BILLING_WIZARD',secretPayload:{csc}})},
  BillingPTTUtils:{generatePTTWithRequirement:fn=>fn(),generatePTT:async()=> 'synthetic_token'},
  XPlatReactCrypto:{isHardwareBackedAvailable:async()=>false},
  enumUtils:{enumValueToKey:v=>v}, pm_capability_PaymentMethodUsabilityIntent:{},
  nullthrows:v=>{if(v==null) throw Error('missing');return v;},
};
function requireModule(name){
  if(name in modules) return modules[name];
  if(name in stubs) return stubs[name];
  throw Error('Unexpected dependency '+name);
}
let fixedEphemeral;
const subtle = new Proxy(webcrypto.subtle, {get(target, key){
  if(key==='generateKey') return async (algorithm,...args)=> {
    if(algorithm.name!=='ECDH') throw Error('Unexpected key generation');
    return fixedEphemeral;
  };
  const value = target[key]; return typeof value==='function'?value.bind(target):value;
}});
const crypto = {subtle,getRandomValues:array=>{array.set(Buffer.from('000102030405060708090a0b','hex'));return array;}};
const context = vm.createContext({Promise,Date,TextEncoder,Uint8Array,Uint32Array,
  window:{crypto,btoa:s=>Buffer.from(s,'binary').toString('base64')},
  atob:s=>Buffer.from(s,'base64').toString('binary'),babelHelpers:{extends:Object.assign},
  __d:(name,deps,factory)=> {const exports={};factory(null,requireModule,requireModule,requireModule,null,null,exports,fbt);modules[name]=exports;}
});
function load(file){vm.runInContext(fs.readFileSync(path.join(__dirname,file),'utf8'),context,{timeout:1000});}
async function run(input){
  if(input.mode==='builder') {
    load('BillingCreditCardUtils.js');
    const v=input.values, number=v.number;
    const card={expiration:`${v.month}/${String(v.year).slice(-2)}`,
      cardNumber:{getCleanValue_DO_NOT_USE:()=>number,getLastFour:()=>number.slice(-4),getBin8:()=>number.slice(0,8),getBin:()=>number.slice(0,6)},
      securityCode:{getValue_DO_NOT_USE:()=>v.cvv},firstName:v.holder,postalCode:v.postal_code,
      cardHolderEmail:v.email,cardHolderPhoneNumber:v.phone,
      verifyTokenizationCheckbox:input.network_consent,verifyRecurringCheckbox:input.recurring_consent};
    return await modules.BillingCreditCardUtils.buildSaveCardCredentialInput(card,input.payment,input.country,input.currency,
      'ADD_PM',input.usability_intent,false,input.client_info,undefined,undefined,{},undefined,false,null,{});
  }
  load('FBPayAuthLibraryUtils.current.js');load('FBPayAuthLibraryCommon.current.js');load('getPTTUtils.js');
  const privateKey = await webcrypto.subtle.importKey('pkcs8',Buffer.from(input.ephemeral,'base64'),{name:'ECDH',namedCurve:'P-256'},true,['deriveBits']);
  const publicKey = await webcrypto.subtle.importKey('spki',Buffer.from(input.ephemeral_public,'base64'),{name:'ECDH',namedCurve:'P-256'},true,[]);
  fixedEphemeral={privateKey,publicKey};
  const server = await webcrypto.subtle.importKey('spki',Buffer.from(input.server_public,'base64'),{name:'ECDH',namedCurve:'P-256'},true,[]);
  return await modules.getPTTUtils.getPTTInternal(input.auth_data,'ADD_CARD',undefined,
    {secretPayload:input.secret_payload,serverKeyObject:{key:server,validity:{notBefore:'200101000000Z',notAfter:'690101000000Z'}}});
}
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',d=>input+=d);
process.stdin.on('end',()=>run(JSON.parse(input)).then(r=>process.stdout.write(JSON.stringify(r)),e=>{process.stderr.write(e.message);process.exitCode=1}));
