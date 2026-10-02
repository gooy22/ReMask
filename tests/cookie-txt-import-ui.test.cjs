const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('railway-cookie-txt-import.js', 'utf8');
class Element {
  constructor(tag, doc) { this.tag=tag; this.doc=doc; this.children=[]; this.handlers={}; this.dataset={}; this.style={}; this.value=''; this.files=[]; }
  set id(id) { this._id=id; this.doc.ids.set(id,this); }
  get id() { return this._id; }
  set innerHTML(html) {
    this.html=html;
    for(const match of html.matchAll(/<(input|button|tbody|p|h2)[^>]*\bid="([^"]+)"[^>]*>/g)) {
      const el=new Element(match[1],this.doc); el.id=match[2]; el.disabled=match[0].includes(' disabled'); this.children.push(el);
    }
  }
  setAttribute(name,value) { this[name]=value; }
  append(el) { this.children.push(el); }
  insertAdjacentElement(_,el) { this.doc.body.append(el); }
  replaceChildren() { this.children=[]; }
  addEventListener(name,cb,options={}) { (this.handlers[name] ||= []).push({cb,once:options.once}); }
  async fire(name) {
    const handlers=[...(this.handlers[name] || [])];
    for(const item of handlers) { if(item.once)this.handlers[name]=this.handlers[name].filter(v=>v!==item); await item.cb({preventDefault(){}}); }
  }
  querySelector(selector) { return selector.startsWith('#') ? this.doc.ids.get(selector.slice(1)) : this.querySelectorAll(selector)[0]; }
  querySelectorAll(selector) {
    const all=[]; const walk=el=>{ for(const child of el.children){ if(child.dataset.txtLine && (!selector.endsWith(':checked') || child.checked)) all.push(child); walk(child); } }; walk(this); return all;
  }
  showModal() { this.open=true; }
  close() { this.open=false; return this.fire('close'); }
}
const doc={ids:new Map(),readyState:'complete',createElement(tag){return new Element(tag,this);},getElementById(id){return this.ids.get(id);},querySelector(selector){return selector==='meta[name="remask-csrf"]'?{content:'csrf-fixture'}:null;}};
doc.body=new Element('body',doc); const anchor=doc.createElement('button'); anchor.id='addProfileTop'; doc.body.append(anchor);
let requests=[],responses=[],reloads=0;
const context={document:doc,TextDecoder,window:{location:{reload(){reloads++;}}},
  async fetch(url,options){assert.equal(options.headers['X-ReMask-CSRF'],'csrf-fixture');requests.push({url,body:JSON.parse(options.body)}); const data=responses.shift(); return {ok:true,json:async()=>data};}};
vm.createContext(context);vm.runInContext(source,context);
(async()=>{
  await doc.getElementById('remaskTxtImport').fire('click'); const dialog=doc.getElementById('remaskTxtDialog'); assert.equal(dialog.open,true);
  const el=id=>doc.getElementById(id);
  await el('txtPreview').fire('click'); assert.equal(requests.length,0); assert.equal(el('txtCommit').disabled,true);
  const text='123456789\tfixture-password\t[{"name":"c_user","value":"123456789"},{"name":"xs","value":"fixture-session"}]';
  el('txtFile').files=[{size:text.length,arrayBuffer:async()=>new TextEncoder().encode(text).buffer}];
  responses.push({ok:true,total:3,ready:2,skipped:1,errors:0,records:[
    {line:1,user_id:'123456789',profile_name:'8',status:'ready'},
    {line:2,user_id:'234567890',profile_name:'9',status:'ready'},
    {line:3,user_id:'345678901',profile_name:'1',status:'already_exists'}]});
  await el('txtPreview').fire('click');
  assert.equal(requests[0].body.action,'import_preview'); assert.equal('proxy' in requests[0].body,false);
  assert.equal(dialog.querySelectorAll('input[data-txt-line]').length,2); assert.equal(el('txtCommit').disabled,false);
  assert.equal(el('txtResults').children.length,3);
  assert.equal(el('txtResults').children[0].children.some(c=>String(c.textContent).includes('fixture-session')),false);
  await el('txtCommit').fire('click'); assert.equal(requests.length,1); // Proxy required before any write.
  const boxes=dialog.querySelectorAll('input[data-txt-line]'); boxes[0].checked=false; await boxes[0].fire('change');
  el('txtProxy').value='http:127.0.0.1:8080:fixture:fixture';
  responses.push({ok:true,total:1,processed:1,imported:1,skipped:0,errors:0,records:[{line:2,user_id:'234567890',profile_name:'8',status:'imported'}]});
  await Promise.all([el('txtCommit').fire('click'),el('txtCommit').fire('click')]);
  assert.equal(requests.length,2); assert.deepEqual(requests[1].body.selected_lines,[2]);
  assert.equal(requests[1].body.action,'import_txt'); assert.equal(el('txtCommit').disabled,true);
  assert.equal(el('txtProxy').value,''); assert.equal(el('txtFile').value,''); assert.equal(reloads,0);
  assert.ok(el('txtStatus').textContent.includes('Facebook-сессии ещё не проверены'));
  await dialog.close(); assert.equal(reloads,1); assert.equal(el('txtResults').children.length,0);
  assert.equal(/localStorage|sessionStorage|console\./.test(source),false);
  console.log('TXT preview, selection, proxy validation, duplicate-click guard and secret cleanup passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
