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

  // The same shared picker must use Launch's verified scope adapter when it is
  // installed, while Creative's unbound workflow above retains its own fetch.
  let scoped=[];
  ctx.remaskPrivateTargetingScope=()=>({profile:'38',account_id:'111111'});
  ctx.apiJson=async(url,options)=>{scoped.push({url,options});return {data:[{id:'23456',name:'Scoped employer'}]};};
  search.fire('keydown','Enter');await settle();
  assert.equal(scoped.length,1);assert.equal(scoped[0].url,'ajax/metaTemplateTargetingSearch.php');
  assert.equal(scoped[0].options.headers['X-ReMask-CSRF'],doc.querySelector().content);
  assert.equal(results.children[0].textContent,'Scoped employer');
  console.log('Shared Launch choices: exact-scope adapter and CSRF preserved.');

  const creative=fixture();const requests=[],creativeRequests=[],creativeSelected=[];
  Object.assign(creative.ctx,{$:id=>creative.nodes.get(id),creativeTargetingTimers:{interests:0},creativeTargetingControllers:{interests:null},
    creativeTargetingPages:{},esc:String,creativeTargetLabel:()=>({main:'Interest'}),
    addCreativeTarget:(kind,row)=>creativeSelected.push({kind,row}),
    api:async(url,options)=>{creativeRequests.push({url,options});return new Promise(resolve=>requests.push(resolve));}});
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
  field.fire('keydown','Enter');requests.shift()({data:[{id:'12345'}],source:'private_meta_template_reference',reference_checked_at:1000,reference_stale:true});await settle();
  assert.match(box.children.at(-1).textContent,/Сохранённые данные Meta.*актуальность не проверена/,
    'offline choices show their saved date and do not claim a fresh Meta read');
  console.log('Creative choices: Escape, query changes and catalog truncation passed.');
  field.value='';
  field.fire('keydown','Enter');
  requests.shift()({data:[{id:'100001',name:'First'}],offset:0,next_offset:100,truncated:true});
  await settle();
  const more=box.children.find(node=>node.dataset.remaskTargetMore==='1');
  assert.ok(more,'Creative editor offers the next saved reference page');
  more.fire('click');
  assert.match(creativeRequests.at(-1).options.body,/offset=100/);
  requests.shift()({data:[{id:'100002',name:'Second'}],offset:100,next_offset:null});
  await settle();
  assert.equal(box.children.filter(node=>node.dataset.remaskTargetChoice==='1').length,2,
    'Creative editor retains previous page while adding later results');
  box.children.filter(node=>node.dataset.remaskTargetChoice==='1')[1].fire('click');
  assert.equal(creativeSelected.length,1,'Creative editor selection remains functional after loading page 2');
  assert.equal(creativeSelected[0].row.id,'100002','the later-page option keeps its exact ID');
  console.log('Creative template: full catalog page navigation and selection passed.');


  const launch=fixture();const replies=[],launchRequests=[];
  for(const id of ['geoQuery','geoResults','selectedGeo','geoSearch','interestSearch'])
    launch.nodes.set(id,id.endsWith('Query')?new Input():new Element());
  let legacyReads=0;
  launch.nodes.get('geoQuery').addEventListener('input',()=>legacyReads++);
  launch.nodes.get('geoQuery').addEventListener('keydown',()=>legacyReads++);
  let launchFailure=null;
  Object.assign(launch.ctx,{$:id=>launch.nodes.get(id),state:{geo:[]},formPost:body=>body,
    apiJson:async(url,options)=>{launchRequests.push({url,options});if(launchFailure)throw launchFailure;return new Promise(resolve=>replies.push(resolve));},
    renderPills(){},invalidateLaunchReview(){},validateReady(){}});
  vm.runInContext(read('railway-targeting-pickers.js'),launch.ctx);
  assert.equal(launch.nodes.get('geoSearch').hidden,true,'the obsolete GEO search button is hidden');
  assert.equal(launch.nodes.get('interestSearch').hidden,true,'the obsolete interest search button is hidden');
  launch.nodes.get('geoQuery').fire('focus');
  assert.equal(launchRequests.length,0,'focusing an empty field does not request the full Meta catalog');
  launch.nodes.get('geoQuery').value='Ukraine';
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
  launch.nodes.get('geoQuery').value='Ukraine';
  launchFailure=Object.assign(new Error('PRIVATE_ADS_DRAFT_RESOURCE_READ_INCOMPLETE'),{code:'PRIVATE_ADS_DRAFT_RESOURCE_READ_INCOMPLETE'});
  await launch.ctx.searchTargeting('locations');
  assert.equal(launch.nodes.get('geoResults').textContent,'Список сейчас недоступен. Выбранные настройки не изменены.');
  assert.doesNotMatch(launch.nodes.get('geoResults').textContent,/PRIVATE_ADS_DRAFT/,'internal Meta codes stay out of the UI');
  console.log('Launch choices: no extra search buttons, no blank catalog fetch, friendly errors and exact selection IDs passed.');
  for(const id of ['interestQuery','interestResults','selectedInterests'])launch.nodes.set(id,id.endsWith('Query')?new Input():new Element());
  launch.ctx.state.interests=[];
  // Installation has already occurred; create a new fixture with its elements
  // present at initialization so that the same shipped handlers own focus/input.
  const paged=fixture(),pageReplies=[],pageRequests=[];
  for(const id of ['interestQuery','interestResults','selectedInterests'])
    paged.nodes.set(id,id.endsWith('Query')?new Input():new Element());
  Object.assign(paged.ctx,{$:id=>paged.nodes.get(id),state:{interests:[]},formPost:body=>body,
    apiJson:async(url,options)=>{pageRequests.push({url,options});return new Promise(resolve=>pageReplies.push(resolve));},
    renderPills(){},invalidateLaunchReview(){},validateReady(){}});
  vm.runInContext(read('railway-targeting-pickers.js'),paged.ctx);
  paged.nodes.get('interestQuery').value='Travel';
  let first=paged.ctx.searchTargeting('interests');
  pageReplies.shift()({data:[{id:'100001',name:'First interest',path:['Category']}],
    next_offset:100,offset:0,truncated:true,source:'private_meta_template_reference'});
  await first;
  const resultBox=paged.nodes.get('interestResults');
  assert.equal(resultBox.children.at(-1).textContent,'Показать ещё 100 вариантов',
    'template picker exposes next-page control instead of hiding all but 100 IDs');
  resultBox.children.at(-1).fire('click');
  assert.equal(pageRequests.at(-1).options.offset,100,'later page requests retain a bounded offset');
  pageReplies.shift()({data:[{id:'100002',name:'Second interest',path:['Category','Second']}],
    next_offset:null,offset:100,truncated:false,source:'private_meta_template_reference'});
  await settle();
  assert.equal(resultBox.children.length,2,'later options are appended without losing previous choices');
  resultBox.children[1].fire('click');
  assert.equal(paged.ctx.state.interests[0].id,'100002','a later-page interest can be selected and saved');
  assert.equal(paged.ctx.state.interests[0].path[1],'Second','category hierarchy is preserved for the chosen ID');
  assert.equal(resultBox.children.length,0,'selection dismisses all remaining options');
  console.log('Launch category browser: paged discovery, exact ID choice and saved taxonomy passed.');
  // Installed Launch API wrapper must retain the page offset. Previously it
  // forwarded type/query/limit but silently dropped offset from a template.
  const completion=fixture(),bridged=[];
  Object.assign(completion.ctx,{
    selectedAccountIds:()=>[],targetMode:()=>false,state:{profile:'38'},
    $:()=>null,URLSearchParams,
    apiJson:async(url,options)=>{bridged.push({url,options});return {data:[]};}
  });
  const completionSource=read('railway-private-launch-completion.js');
  vm.runInContext(completionSource.slice(0,completionSource.indexOf('// A selected RK needs')),completion.ctx);
  await completion.ctx.apiJson('ajax/metaTemplateTargetingSearch.php',{
    body:'type=interests&q=&limit=100&offset=100'
  });
  assert.equal(new URLSearchParams(bridged[0].options.body).get('offset'),'100',
    'Launch preserves page offsets through its installed account/template bridge');
  assert.equal(bridged[0].url,'ajax/metaTemplateTargetingSearch.php');
  const scopedResponse={data:[],checked_live:true,account_scope_verified:true,
    profile:'38',account_id:'111111',resource:'interests',offset:100,next_offset:200};
  // The wrapped source function is a closure over the original mock: replace
  // its returned value by constructing a fresh selected-account fixture.
  const selected=fixture(),selectedCalls=[];
  Object.assign(selected.ctx,{$:()=>null,URLSearchParams,state:{profile:'38'},
    selectedAccountIds:()=>['111111'],targetMode:()=>false,
    apiJson:async(url,options)=>{selectedCalls.push({url,options});return scopedResponse;}});
  vm.runInContext(completionSource.slice(0,completionSource.indexOf('// A selected RK needs')),selected.ctx);
  const selectedPage=await selected.ctx.apiJson('ajax/metaTemplateTargetingSearch.php',{
    body:'type=interests&q=&limit=100&offset=100'
  });
  assert.equal(selectedCalls[0].url,'ajax/metaTargetingSearch.php',
    'selected account routes paging through its own private Meta resource reader');
  assert.equal(new URLSearchParams(selectedCalls[0].options.body).get('offset'),'100');
  assert.equal(selectedPage.next_offset,200);
  console.log('Installed targeting API bridge: later page offset survives request routing.');



  // A partially pre-rendered Launch form must still receive the missing
  // behavior results and selected-item containers. This is the regression
  // that used to be skipped by the early return when behaviorQuery existed.
  const behaviorOverlay=read('railway-behaviors-ui-overlay.php');
  const behaviorUi=behaviorOverlay.match(/\$ui=<<<'JS'\n([\s\S]*?)\nJS;/)?.[1];
  assert.ok(behaviorUi,'behavior UI installer must remain embedded in the runtime overlay');
  const partial=fixture(), behaviorInput=new Input(), behaviorBlock=new Element();
  partial.doc.createElement=tag=>{
    const node=tag==='input'?new Input():new Element();
    Object.defineProperty(node,'id',{set(value){this._id=value;partial.nodes.set(value,node);},get(){return this._id||'';}});
    node.insertAdjacentElement=(_position,child)=>{
      const parent=node.parentElement;
      if(!parent)throw new Error('detached targeting element');
      const index=parent.children.indexOf(node);
      parent.children.splice(index+1,0,child);child.parentElement=parent;
    };
    return node;
  };
  behaviorInput.id='behaviorQuery';behaviorInput.placeholder='Поведение';
  behaviorInput.parentElement=behaviorBlock;behaviorBlock.children.push(behaviorInput);
  behaviorInput.insertAdjacentElement=(position,node)=>{
    const index=behaviorBlock.children.indexOf(behaviorInput);
    behaviorBlock.children.splice(index+1,0,node);node.parentElement=behaviorBlock;
  };
  partial.nodes.set('behaviorQuery',behaviorInput);
  partial.nodes.set('selectedInterests',new Element());
  partial.nodes.get('selectedInterests').className='pills';
  partial.nodes.set('interestResults',new Element());
  partial.ctx.$=id=>partial.nodes.get(id)||null;
  partial.ctx.state={behaviors:[{id:'654321',name:'Fixture behavior'}]};
  partial.ctx.renderPills=(box,rows)=>{box.rendered=rows.slice();};
  vm.runInContext(behaviorUi+String.fromCharCode(10)+'ensureBehaviorTargetingUi();',partial.ctx);
  assert.ok(partial.nodes.get('selectedBehaviors'),'partial Launch markup restores selected behavior container');
  assert.ok(partial.nodes.get('behaviorResults'),'partial Launch markup restores behavior results container');
  assert.equal(partial.nodes.get('selectedBehaviors').className,'pills');
  assert.equal(partial.nodes.get('selectedBehaviors').rendered[0].id,'654321');
  console.log('Launch behavior controls: partial DOM is repaired and selected values render.');

})().catch(error=>{console.error(error);process.exitCode=1;});
