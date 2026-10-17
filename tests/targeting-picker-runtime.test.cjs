// Execute the shipped picker code with controlled HTTP responses, including
// responses that resolve after abort. No server, browser or Meta account used.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,'..',name),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

class Element {
  constructor(){this.children=[];this.dataset={};this.style={};this.attributes={};this.events={};this.labels=[];this.isConnected=true;this.classList={add(){},remove(){}};}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.append(node);return node;}
  replaceChildren(...nodes){this.children=nodes;this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text||'';}
  set innerHTML(value){this.textContent=value;}
  get innerHTML(){return this.textContent;}
  setAttribute(key,value){this.attributes[key]=value;}
  addEventListener(key,fn,options={}){(this.events[key]??=[]).push({fn,capture:options.capture===true});}
  dispatchEvent(event){
    let stopped=false;event.stopImmediatePropagation=()=>{stopped=true;};
    for(const {fn} of [...(this.events[event.type]||[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){fn(event);if(stopped)break;}
  }
  closest(){return null;}
  after(node){this.next=node;}
  querySelector(){return this.children.find(node=>node.type==='button')||null;}
  focus(){}
  fire(type,key){this.dispatchEvent({type,key,preventDefault(){},stopPropagation(){},stopImmediatePropagation(){}});}
}
class Input extends Element {get value(){return this._value||'';}set value(value){this._value=String(value);}}
class TextArea extends Input {}
function fixture(){
  const nodes=new Map(),timers=new Map();let timerId=0;
  const doc={head:new Element(),body:new Element(),
    createElement:tag=>tag==='input'?new Input():new Element(),
    getElementById:id=>nodes.get(id)||null,
    querySelector:()=>({content:'fixture-csrf'}),querySelectorAll:()=>[]};
  const ctx={document:doc,HTMLInputElement:Input,HTMLTextAreaElement:TextArea,
    Event:class {constructor(type){this.type=type;}},CustomEvent:class{constructor(type,options){this.type=type;Object.assign(this,options);}},
    AbortController,URLSearchParams,queueMicrotask,MutationObserver:class{observe(){}},
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(ctx);return {ctx,nodes,doc,timers};
}

(async()=>{
  const {ctx,nodes,doc}=fixture();const source=new Input();
  source.id='fixture-employers';source.dataset={metaGroup:'targeting',metaField:'work_employers'};
  nodes.set(source.id,source);doc.querySelectorAll=()=>[source];
  let pending=[],headers=[];
  ctx.fetch=async(url,options)=>{
    headers.push(options.headers);
    return new Promise(resolve=>pending.push(()=>resolve({ok:true,text:async()=>JSON.stringify({ok:true,data:{data:[{id:'12345',name:'Employer'}]}})})));
  };
  vm.runInContext(read('railway-meta-module-controls.js')+'\nremaskModuleControls.configure({field_controls:{targeting:{work_employers:{kind:"lookup",lookup:"work_employers",representation:"objects"}}}});',ctx);
  await settle();const [search,,results]=source.next.children;
  search.fire('keydown','Enter');assert.equal(pending.length,1);
  assert.equal(headers.at(-1)['X-ReMask-CSRF'],doc.querySelector().content,'all module lookups must pass the current login CSRF token');
  pending.shift()();await settle();assert.equal(results.children[0].textContent,'Employer');
  search.fire('keydown','Enter');search.fire('keydown','Escape');pending.shift()();await settle();
  assert.equal(results.children.length,0,'an aborted lookup cannot reopen a dismissed list');
  console.log('Shared module choices: authenticated POST and stale response rejection passed.');

  const creative=fixture();const requests=[];
  Object.assign(creative.ctx,{$:id=>creative.nodes.get(id),creativeTargetingTimers:{interests:0},creativeTargetingControllers:{interests:null},
    esc:String,creativeTargetLabel:()=>({main:'Interest'}),addCreativeTarget(){},api:async()=>new Promise(resolve=>requests.push(resolve))});
  const field=new Input(),box=new Element();
  creative.nodes.set('mbInterestSearch',field);creative.nodes.set('mbInterestResults',box);
  const code=read('railway-v100-creatives.js');
  vm.runInContext(code.slice(code.indexOf('function creativeTargetSearchConfig('),code.indexOf('function setMetaSelectOptions('))+'\ninstallCreativeTargetSearch("interests");',creative.ctx);
  field.fire('keydown','Enter');assert.equal(requests.length,1);
  field.fire('keydown','Escape');requests.shift()({data:[{id:'12345'}]});await settle();
  assert.equal(box.children.length,0,'Escape invalidates an in-flight Creative search');
  field.fire('keydown','Enter');field.value='new';field.fire('input');requests.shift()({data:[{id:'12345'}]});await settle();
  assert.equal(box.children.length,0,'typing invalidates the old query before debounce fires');
  field.fire('keydown','Enter');requests.shift()({data:[{id:'12345'}],truncated:true});await settle();
  assert.match(box.children.at(-1).textContent,/сузить список/,'partial catalogs retain the search hint');
  console.log('Creative choices: Escape, query changes and catalog truncation passed.');

  const launch=fixture();const replies=[];
  for(const id of ['geoQuery','geoResults','selectedGeo'])launch.nodes.set(id,id.endsWith('Query')?new Input():new Element());
  let legacyReads=0;
  launch.nodes.get('geoQuery').addEventListener('input',()=>legacyReads++);
  launch.nodes.get('geoQuery').addEventListener('keydown',()=>legacyReads++);
  Object.assign(launch.ctx,{$:id=>launch.nodes.get(id),state:{geo:[]},formPost:body=>body,
    apiJson:async()=>new Promise(resolve=>replies.push(resolve)),renderPills(){},invalidateLaunchReview(){},validateReady(){}});
  vm.runInContext(read('railway-targeting-pickers.js'),launch.ctx);
  let request=launch.ctx.searchTargeting('locations');
  launch.nodes.get('geoQuery').fire('input');replies.shift()({data:[{key:'UA',type:'country',name:'Ukraine'}]});await request;
  assert.equal(launch.nodes.get('geoResults').children.length,0,'Launch rejects old results while typing');
  launch.nodes.get('geoQuery').fire('keydown','Escape');
  assert.equal(launch.timers.size,0,'Escape cancels the pending input debounce');
  assert.equal(legacyReads,0,'new Launch adapter owns events before old search handlers');
  request=launch.ctx.searchTargeting('locations');
  replies.shift()({data:[{key:'UA',type:'country',name:'Ukraine'}]});await request;
  launch.nodes.get('geoResults').children[0].fire('click');
  assert.equal(launch.ctx.state.geo[0].key,'UA','selection keeps the original country key');
  console.log('Launch choices: query invalidation and original selection IDs passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
