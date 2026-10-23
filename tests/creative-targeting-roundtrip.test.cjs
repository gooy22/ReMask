const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'railway-v100-creatives.js'), 'utf8');
function section(start, end) {
  const first = source.indexOf(start);
  const last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `missing targeting section ${start}`);
  return source.slice(first, last);
}
const inputs = {
  mbGeo: {value: JSON.stringify({countries:['US'], zips:[{key:'90001'}], geo_markets:[{key:'803'}]})},
  mbExcludedGeo: {value: JSON.stringify({zips:[{key:'10001'}]})},
  mbInterests: {value:'[]'}, mbBehaviors: {value:'[]'}, mbLocales: {value:''}
};
const context = {
  creativeTargetingSelections: {geo:[], excludedGeo:[], interests:[], behaviors:[], languages:[]},
  $: id => inputs[id],
  parseJsonField: (id, fallback) => inputs[id]?.value ? JSON.parse(inputs[id].value) : fallback,
  csvInts: () => [],
  renderCreativeTargetPills: () => {}
};
vm.createContext(context);
vm.runInContext(section('function geoSpecFromRows(', 'function syncCreativeTargetingRaw(')
  + section('function hydrateCreativeTargetingSelections(', 'function addCreativeTarget('), context);
context.hydrateCreativeTargetingSelections();
const included = context.creativeTargetingSelections.geo;
assert.deepEqual(Array.from(included, row => row.type), ['country','zip','geo_market']);
included.push({type:'city',key:'2420605'});
const saved = JSON.parse(JSON.stringify(context.geoSpecFromRows(included)));
assert.deepEqual(saved, {countries:['US'], cities:[{key:'2420605'}],
  zips:[{key:'90001'}], geo_markets:[{key:'803'}]});
assert.equal(context.creativeTargetingSelections.excludedGeo[0].key, '10001');
console.log('Saved ZIP and geo market targeting survive editor restoration and changes.');
