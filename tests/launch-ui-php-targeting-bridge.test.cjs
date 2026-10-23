'use strict';
// The installed Launch overlays are the source of the UI functions below.
// Feed their selected builder through the real PHP job payload normalizer.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const {join}=require('node:path');

const root=join(__dirname,'..');
const read=name=>fs.readFileSync(join(root,name),'utf8');
function heredoc(source, variable, marker) {
    const start=variable+"=<<<'"+marker+"'\n";
    const first=source.indexOf(start);
    assert.notEqual(first,-1,'missing installed overlay source '+variable);
    const from=first+start.length;
    const end=source.indexOf('\n'+marker+';',from);
    assert.notEqual(end,-1,'unterminated installed overlay '+variable);
    return source.slice(from,end);
}
const helper=heredoc(read('railway-meta-builder-v102-overlay.php'),'$helper','JS_CODE');
const installedHelper=helper.slice(0,helper.indexOf('/* REMASK_PLACEMENT_PREFLIGHT_V1 */'));
const full=heredoc(read('railway-launch-full-meta-v113-overlay.php'),'$addon','JS');

const preset={
    targeting:{
        exclusions:{interests:[{id:'101010'}],behaviors:[{id:'202020'}]},
        flexible_spec:[{interests:[{id:'111111'}]},{behaviors:[{id:'222222'}]}],
        geo_locations:{countries:['UA']},
    },
};
const inputs=[
    {dataset:{launchMetaGroup:'targeting',launchMetaField:'exclusions',launchMetaType:'JSON'},
     value:JSON.stringify({interests:[{id:'303030'}]})},
    {dataset:{launchMetaGroup:'targeting',launchMetaField:'flexible_spec',launchMetaType:'list<AdTargetingFlexibleSpec>'},
     value:JSON.stringify([{interests:[{id:'404040'}]}])},
];
const ctx={
    remaskSelectedCreativePreset:{meta_builder:preset},
    document:{querySelectorAll:selector=>selector==='[data-launch-meta-group][data-launch-meta-field]'?inputs:[],
              addEventListener(){}},
    $:()=>null,
    apiJson:()=>new Promise(()=>{}), // no network or Meta access
};
vm.createContext(ctx);
vm.runInContext(installedHelper+'\n'+full,ctx,{timeout:2000});

assert.deepEqual(JSON.parse(JSON.stringify(ctx.remaskReadLaunchMetaSdkFields().targeting)),
    {exclusions:{interests:[{id:'303030'}]},flexible_spec:[{interests:[{id:'404040'}]}]});
const initial={
    campaign:{name:'UI campaign',objective:'OUTCOME_TRAFFIC'},
    adset:{name:'UI adset',optimization_goal:'LINK_CLICKS',billing_event:'IMPRESSIONS',
        targeting:JSON.parse(JSON.stringify(preset.targeting))},
    creative:{name:'UI creative',message:'text',link:'https://example.com/'},
    ad:{name:'UI ad'},
};
const effective=ctx.remaskApplyLaunchMetaFields(ctx.remaskApplySavedMetaBuilderToPayload(initial));
const builder=ctx.remaskCurrentMetaBuilderForJob();
for(const value of [effective.adset.targeting, builder.targeting]) {
    const actual=JSON.parse(JSON.stringify(value));
    assert.deepEqual(actual.exclusions,{interests:[{id:'303030'}]},
        'editing a complex UI targeting field must not retain old exclusion behaviors');
    assert.deepEqual(actual.flexible_spec,[{interests:[{id:'404040'}]}],
        'editing a list must not retain old OR groups');
    assert.deepEqual(actual.geo_locations,{countries:['UA']},
        'unselected independent targeting values remain intact');
}

// Exercise the actual PHP job-creation bridge, not a duplicate JS/Python mock.
const bridgePhp=`
    require 'railway-meta-official-fields.php';
    $input=json_decode(stream_get_contents(STDIN),true,512,JSON_THROW_ON_ERROR);
    $out=MetaOfficialFields::applyBuilderToPayload($input['payload'],$input['builder']);
    echo json_encode($out,JSON_THROW_ON_ERROR|JSON_UNESCAPED_SLASHES);
`;
const runPhp=(payload,selection)=>{
    const process=spawnSync('php',['-r',bridgePhp],{
        cwd:root,input:JSON.stringify({payload,builder:selection}),encoding:'utf8'
    });
    assert.equal(process.status,0,process.stderr);
    return JSON.parse(process.stdout);
};
// Include a stale prior payload to prove that PHP cannot reintroduce nested
// values after the user has selected a replacement in the installed UI.
const fromPhp=runPhp(initial,JSON.parse(JSON.stringify(builder)));
assert.deepEqual(fromPhp.adset.targeting.exclusions,{interests:[{id:'303030'}]});
assert.deepEqual(fromPhp.adset.targeting.flexible_spec,[{interests:[{id:'404040'}]}]);
assert.deepEqual(fromPhp.adset.targeting.geo_locations,{countries:['UA']});
assert.equal(fromPhp.campaign.name,'UI campaign');
assert.equal(fromPhp.creative.name,'UI creative');

const serialized=JSON.parse(JSON.stringify(runPhp(JSON.parse(JSON.stringify(effective)),
    JSON.parse(JSON.stringify(builder)))));
assert.deepEqual(serialized.adset.targeting.exclusions,{interests:[{id:'303030'}]});
assert.deepEqual(serialized.adset.targeting.flexible_spec,[{interests:[{id:'404040'}]}]);
assert.equal(serialized._meta_official_v1,true);
assert.throws(()=>ctx.remaskLaunchMetaParse('52,broken,1000','list<unsigned int>','locales'),
    /каждый элемент/, 'the installed UI must not silently drop an invalid language ID');
assert.throws(()=>ctx.remaskLaunchMetaParse('-2','unsigned int','max_spend'),/>= 0/);
assert.throws(()=>ctx.remaskLaunchMetaParse('invalid','bool','is_dynamic_creative'),/true или false/);
assert.deepEqual(JSON.parse(JSON.stringify(ctx.remaskLaunchMetaParse('52,1000','list<unsigned int>','locales'))),[52,1000]);

// A draft validation rejection must carry the safe Python-authored reason
// through the PHP bridge; returning only the code made the UI un-actionable.
const workerJobsOverlay=read('railway-python-worker-jobs-overlay.php');
assert.match(workerJobsOverlay,/\$code === 'PRIVATE_ADS_DRAFT_INVALID_INPUT'/);
assert.match(workerJobsOverlay,/\$safe\['message'\] = \$validationMessage/);
assert.match(workerJobsOverlay,/'message'=>\$e->detail\['message'\] \?\? \$e->detail\['code'\]/);
assert.match(read('railway-launch-private-catalog.js'),/data\?\.message\|\|data\?\.error\|\|data\?\.detail/);

console.log('Installed Launch UI -> selected meta_builder -> PHP job payload preserves exact nested targeting and rejects truncated IDs.');
