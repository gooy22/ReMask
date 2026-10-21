'use strict';
// Render the actual installed Launch editor overlay against the production SDK
// schema and the reviewed Python native serializer manifest, without Meta IO.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const {join}=require('node:path');
const root=join(__dirname,'..');
const overlay=fs.readFileSync(join(root,'railway-launch-full-meta-v113-overlay.php'),'utf8');
const start="$addon=<<<'JS'\n";
const i=overlay.indexOf(start);
const j=overlay.indexOf("\nJS;",i+start.length);
assert.ok(i>=0&&j>i,'installed Meta field UI overlay must be present');
const js=overlay.slice(i+start.length,j);
const php=spawnSync('php',['-r',
    "require 'railway-v102-MetaSdkSchema.php'; echo json_encode(MetaSdkSchema::schema(), JSON_THROW_ON_ERROR);"
],{cwd:root,encoding:'utf8'});
assert.equal(php.status,0,php.stderr);
const sdk=JSON.parse(php.stdout);
const manifest=JSON.parse(fs.readFileSync(join(root,'python_backend/app/contracts/ads_manager_http_20261012.json'),'utf8'));
const createElement=tag=>({
    tag,children:[],dataset:{},value:'',innerHTML:'',style:{},
    addEventListener(){},appendChild(child){this.children.push(child);return child;},
    classList:{toggle(){}}
});
const uiRoot=createElement('root');
const status=createElement('status');
const ctx={
    $:id=>id==='launchMetaSdkFields'?uiRoot:id==='launchMetaSchemaStatus'?status:null,
    document:{createElement,querySelectorAll:()=>[],addEventListener(){}},
    Option:(label,value)=>({label,value}),
    apiJson:()=>new Promise(()=>{}),
    remaskSelectedCreativePreset:null,
};
vm.createContext(ctx);
vm.runInContext(js,ctx,{timeout:2000});
ctx.sourceSdk=sdk;
vm.runInContext('remaskLaunchMetaSchema=sourceSdk; remaskRenderLaunchMetaFields();',ctx,{timeout:2000});
const selected=vm.runInContext('({
    unsupported:Object.fromEntries(Object.entries(remaskLaunchUnsupportedNativeFields).map(([k,v])=>[k,[...v]])),
    covered:Object.fromEntries(Object.entries(remaskLaunchCoveredFields).map(([k,v])=>[k,[...v]]))
})',ctx);
const walk=element=>[element,...element.children.flatMap(walk)];
const visible=walk(uiRoot).filter(el=>el.dataset.launchMetaGroup&&el.dataset.launchMetaField);
const allNative=manifest.object_fields;
for(const group of ['campaign','adset','targeting','creative','ad']){
    const exposed=Object.keys(sdk[group]?.fields||{});
    const native=new Set(allNative[group]||[]);
    const missing=exposed.filter(field=>!native.has(field)).sort();
    assert.deepEqual([...(selected.unsupported[group]||[])].sort(),missing,
        group+' unsupported SDK fields must be explicitly excluded from Launch');
    const expected=exposed.filter(field=>native.has(field)&&!(selected.covered[group]||[]).includes(field)).sort();
    const displayed=visible.filter(input=>input.dataset.launchMetaGroup===group)
        .map(input=>input.dataset.launchMetaField).sort();
    assert.deepEqual(displayed,expected,group+' must offer all reviewed native writable fields exactly once');
}
console.log('Installed Launch UI fields match the reviewed native Ads Manager serializer across all five levels.');
