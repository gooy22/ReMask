const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('railway-workspace-state.js','utf8');
const storage=new Map();
function page(search='?tab=ad_accounts') {
  const elements=Object.fromEntries(['workspaceSearch','workspaceStatusFilter','workspaceGroupFilter',
    'workspaceSort','onlySelected'].map(id=>[id,{value:'',checked:false}]));
  const ctx={state:{activeTab:'ad_accounts',inventory:{profiles:[],businesses:[],ad_accounts:[]},
    selected:{profiles:new Set(),businesses:new Set(),ad_accounts:new Set()},businessFilter:new Set()},
    render(){},updateSelectionUi(){},$:id=>elements[id],entityKey:(tab,row)=>tab==='profiles'?row.name:row.profile+'|'+row.id,
    deliveryTabs:new Set(['campaigns','adsets','ads']),location:{search},
    document:{querySelectorAll:()=>[]},URLSearchParams,Date,Set,
    localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}};
  vm.createContext(ctx);vm.runInContext(source,ctx);
  return {ctx,elements};
}
const first=page();
first.ctx.state.inventory={profiles:[{name:'19'}],businesses:[],ad_accounts:[{profile:'19',id:'act_111'}]};
first.ctx.state.selected.ad_accounts.add('19|act_111');
first.elements.workspaceSearch.value='19';first.elements.onlySelected.checked=true;
first.ctx.updateSelectionUi(); // A checkbox change updates counters without rendering the table.
const restored=page();
assert(restored.ctx.state.selected.ad_accounts.has('19|act_111'));
assert.equal(restored.elements.workspaceSearch.value,'19');
assert.equal(restored.elements.onlySelected.checked,true);
restored.ctx.render(); // Initial empty inventory must not erase saved IDs.
assert(restored.ctx.state.selected.ad_accounts.has('19|act_111'));
restored.ctx.state.inventory={profiles:[],businesses:[],ad_accounts:[]};
restored.ctx.render();
assert.equal(restored.ctx.state.selected.ad_accounts.size,0,'removed resource cannot remain selected');
restored.ctx.localStorage.setItem=()=>{throw Error('storage unavailable')};
assert.doesNotThrow(()=>restored.ctx.render());
console.log('Workspace F5 restores identifiers and filters; fresh inventory prunes removed rows; storage failure stays nonblocking.');
