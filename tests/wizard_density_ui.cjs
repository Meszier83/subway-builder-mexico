const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const controls = new Map(['btnToggleDensityJobs', 'btnToggleDensityPop'].map(id => [id, {
  disabled: false, innerHTML: id, classList: {add(){}, remove(){}}
}]));
const notices = [], layers = [];
const points = [
  {id:'job', location:[-103.4,25.5], jobs:100, residents:0},
  {id:'home', location:[-103.4,25.5], jobs:0, residents:100},
  {id:'UNI_manual', location:[-103.4,25.5], jobs:5000, residents:0, is_special:true}
];
let diagnostics = {engine:'v2', identity:'fixture', candidate_report:{target_year:2025}};
let requests = 0, fail = false;
const context = {
  cityData:{demand:{engine:'v2'}, macroeconomics:{residential_employment:'census_employed',
    workplace_employment:'auto', demographic_reference:{mode:'eic2025'}}, pois:[{id:'UNI_manual', jobs:null}]},
  currentCityFile:'test.yaml', densityRequestId:0, conapoCommitPromise:null,
  densityPoints:[], densityLayers:{jobs:null,pop:null},
  mapPoi:{removeLayer(layer){ layers.splice(layers.indexOf(layer),1); }},
  document:{getElementById:id=>controls.get(id)||null}, console,
  saveCurrentCity:async()=>true,
  fetch:async()=>{ requests++; return {ok:!fail, json:async()=>({points,diagnostics,error:'Source unavailable'})}; },
  updateWorkplaceSourceStatus(){}, updatePoiPreviewFromForm(){},renderPoiMarkersOnMap(){},renderPoiList(){},
  showToast:message=>notices.push(message),
  L:{canvas:()=>({}),layerGroup:()=>({markers:[],addTo(){layers.push(this);}}),
    circleMarker:()=>({bindTooltip(){},on(){},addTo(group){group.markers.push(this);}})}
};
vm.createContext(context);
for (const name of ['validateDensityResponse','loadDemandDensityForPoi','toggleDensityLayer']) {
  const match = source.match(new RegExp('    (?:async )?function '+name+'\\(.*?\\n    }','s'));
  assert.ok(match, name); vm.runInContext(match[0],context);
}
const settle = () => new Promise(resolve=>setImmediate(resolve));
(async()=>{
  // The actual v2 API schema lacks legacy employment_mode; both layers must render.
  context.toggleDensityLayer('jobs');
  assert.equal(controls.get('btnToggleDensityPop').disabled,true);
  context.toggleDensityLayer('pop'); // No duplicate request during loading.
  await settle();
  assert.equal(requests,1); assert.equal(layers.length,1); assert.equal(layers[0].markers.length,1);
  assert.equal(notices.length,0); assert.equal(context.densityPoints.length,2);
  context.toggleDensityLayer('pop'); await settle();
  assert.equal(layers.length,2); assert.equal(layers[1].markers.length,1);
  const before = requests;
  context.toggleDensityLayer('jobs'); context.toggleDensityLayer('pop');
  assert.equal(layers.length,0); assert.equal(requests,before);
  // Errors must not turn an empty layer on or leave controls disabled.
  fail=true; context.toggleDensityLayer('jobs'); await settle();
  assert.equal(context.densityLayers.jobs,null); assert.equal(layers.length,0);
  assert.equal(controls.get('btnToggleDensityJobs').disabled,false);
  assert.match(notices.at(-1),/Source unavailable/);
  fail=false; diagnostics={employment_mode:'census_employed', workplace_mode:'auto',
    residential_employment:{demographic_reference:{mode:'eic2025'}}};
  assert.throws(()=>context.validateDensityResponse({diagnostics}),/V2/);
  context.cityData.demand.engine='legacy';
  context.validateDensityResponse({diagnostics});
  assert.throws(()=>context.validateDensityResponse({diagnostics:{...diagnostics,workplace_mode:'legacy'}}),/empleo/);
  assert.throws(()=>context.validateDensityResponse({diagnostics:{engine:'v2'}}),/motor/);
  console.log('Wizard density: v2/legacy validation, both layers, pending POIs, errors, and toggles PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
