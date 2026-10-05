const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../tools/templates/wizard.html'), 'utf8');
function fn(name) {
  const match = source.match(new RegExp('    (?:async )?function ' + name + '\\(.*?\\n    }', 's'));
  assert(match, 'Missing production function ' + name);
  return match[0];
}
function harness() {
  const controls = new Map();
  function element(id) {
    if (!controls.has(id)) {
      let value = '';
      controls.set(id, {get value(){return value;}, set value(v){value=String(v);},
        textContent:'', innerText:'', checked:false, dataset:{}, options:[], listeners:new Map(),
        classList:{contains(){return true;}}, appendChild(option){this.options.push(option);},
        addEventListener(name, callback){this.listeners.set(name, callback);}});
    }
    return controls.get(id);
  }
  const context = {console:{warn(){},log:console.log}, currentCityFile:'city.yaml', cityData:{city:{code:'TST'}, macroeconomics:{}},
    densityRequestId:0, densityLoadedCityFile:null, densityPoints:[], conapoCommitPromise:null,
    autoSaveTimer:null, isBboxLocked:false, activeEditingPoiIndex:-1,
    document:{getElementById:element, createElement(){return {}; }},
    localStorage:{setItem(){}}, setTimeout(){return 1;}, clearTimeout(){},
    showToast(message){context.error=message;}, saves:0, fetches:0,
    async saveCurrentCity(){context.saves++; context.syncStateFromInputs(); return true;},
    async fetch(){context.fetches++; return {ok:true,json:async()=>({points:[],
      diagnostics:{workplace_mode:'auto', employment_mode:'census_employed', residential_employment:{placement:{retained:{
        blocks:2, projected_employed:96, by_source:{published_block:{blocks:1},ageb_residual:{blocks:1}}}}}}})};}};
  for (const name of ['updateBboxLayer','updateBboxDimensionsDisplay','toggleBboxLock',
    'updateCohortUIFromData','toggleModalExperimentControls','updateModalSimulatorPreview',
    'renderUrbanCorePolygon','updateZoomPreviewLayer','renderGrowthFactorsTable','renderPoiList',
    'renderPoiMarkersOnMap','renderPlacesList','renderPlacesMarkersOnMap','renderIsolatedZones',
    'renderAffluenceZones','renderExclusionZones','updatePoiPreviewFromForm']) context[name]=()=>{};
  vm.createContext(context);
  vm.runInContext(['updateDemandMethodsSummary','workplaceSourceStatus','updateWorkplaceSourceStatus','refreshWorkplaceSourceStatus','syncStateFromInputs','populateFormFields','triggerAutoSave','loadDemandDensityForPoi'].map(fn).join('\n'),context);
  return {context, element};
}
(async()=>{
  assert.match(html, /id="cfg_residential_employment"/);
  assert.match(html, /value="census_employed"/);
  assert.match(html, /id="cfg_workplace_employment"/);
  assert.match(html, /value="ce_bounded"/);
  assert.match(html, /value="auto" selected/);
  const inspection = harness();
  inspection.context.fetch = async()=>({ok:true,json:async()=>({mode:'ce_bounded',
    automatic_selection:{reason:'Missing municipal sector-and-size cells'}})});
  await inspection.context.refreshWorkplaceSourceStatus(true);
  assert.match(inspection.element('buildWorkplaceSourceStatus').textContent,/Missing municipal sector-and-size cells/);
  let finishInspection;
  inspection.context.fetch=()=>new Promise(resolve=>{finishInspection=resolve;});
  const staleInspection=inspection.context.refreshWorkplaceSourceStatus(true);
  inspection.context.currentCityFile='other-project.yaml';
  finishInspection({ok:true,json:async()=>({mode:'historical_transfer'})});
  await assert.rejects(staleInspection,/proyecto o método cambió/);
  assert.match(inspection.element('buildWorkplaceSourceStatus').textContent,/Missing municipal sector-and-size cells/);
  const status = harness();
  status.context.updateWorkplaceSourceStatus({mode:'historical_transfer',reference_year:2023,
    transferred_establishments:3,fallback_establishments:7,
    controls:[{status:'SUPPRESSED_CE_CONTROL'},{status:'MISSING_CE_CONTROL'}]});
  for (const id of ['workplaceCoverage','buildWorkplaceSourceStatus']) {
    assert.match(status.element(id).textContent,/3 establecimientos transferidos/);
    assert.match(status.element(id).textContent,/7 conservan estimaciones DENUE/);
    assert.match(status.element(id).textContent,/SUPPRESSED_CE_CONTROL/);
    assert.match(status.element(id).textContent,/MISSING_CE_CONTROL/);
  }
  const workplace = harness();
  workplace.context.populateFormFields();
  assert.equal(workplace.element('cfg_workplace_employment').value, 'auto');
  assert.equal(workplace.element('demandCompatibility').open, false);
  assert.equal(workplace.element('demandCompatibilityNotice').hidden, true);
  assert.match(workplace.element('demandMethodsSummary').textContent, /Empleo automático/);
  const legacy = harness();
  legacy.context.cityData = {city:{residential_placement:'legacy'},macroeconomics:{residential_employment:'legacy',workplace_employment:'legacy'}};
  const savedModes = JSON.stringify([legacy.context.cityData.city.residential_placement, legacy.context.cityData.macroeconomics]);
  legacy.context.populateFormFields();
  assert.equal(JSON.stringify([legacy.context.cityData.city.residential_placement, legacy.context.cityData.macroeconomics]), savedModes, 'Displaying compatibility must not migrate saved methods');
  assert.equal(legacy.element('demandCompatibility').open, true);
  assert.equal(legacy.element('demandCompatibilityNotice').hidden, false);
  assert.match(legacy.element('demandMethodsSummary').textContent, /TIL1 anterior/);
  legacy.context.cityData = {city:{},macroeconomics:{workplace_employment:'historical_transfer',historical_workplace_transfer:{strength:.5,groups:[]}}};
  legacy.context.populateFormFields();
  assert.equal(legacy.element('demandCompatibility').open, true);
  assert.equal(legacy.element('cfg_ce_transfer_strength').value, '0.5');
  assert.match(legacy.element('demandMethodsSummary').textContent, /CE manual/);
  legacy.context.cityData = {city:{},macroeconomics:{}};
  legacy.context.populateFormFields();
  assert.equal(legacy.element('demandCompatibility').open, false, 'Compatibility state must not leak into another project');

  workplace.element('cfg_workplace_employment').value = 'ce_bounded';
  workplace.element('cfg_workplace_employment').listeners.get('change')();
  assert.equal(workplace.context.cityData.macroeconomics.workplace_employment, 'ce_bounded');
  workplace.context.fetch = async()=>({ok:true,json:async()=>({points:[],diagnostics:{employment_mode:'census_employed',workplace_mode:'ce_bounded',workplace_employment:{mode:'ce_bounded',comparability:'UNVERIFIED_SOURCE_COMPARABILITY',control_gate_reasons:['Missing CE sources'],bbox_attraction:50,controls:[]}}})});
  await workplace.context.loadDemandDensityForPoi();
  assert.match(workplace.element('workplaceCoverage').textContent, /DESACTIVADA/);
  assert.match(workplace.element('buildWorkplaceSourceStatus').textContent, /Missing CE sources/);
  workplace.context.fetch = async()=>({ok:true,json:async()=>({points:[],diagnostics:{workplace_mode:'legacy'}})});
  await workplace.context.loadDemandDensityForPoi();
  assert.match(workplace.context.error, /no coincide/);
  assert.match(workplace.element('workplaceCoverage').textContent, /Vista sin validar/);
  const {context:c, element:e} = harness();
  c.populateFormFields();
  assert.equal(e('cfg_residential_employment').value,'census_employed');
  e('cfg_residential_employment').value='census_employed';
  e('cfg_residential_employment').listeners.get('change')();
  assert.equal(c.cityData.macroeconomics.residential_employment,'census_employed');
  c.populateFormFields();
  assert.equal(e('cfg_residential_employment').value,'census_employed');
  let release;
  c.saveCurrentCity=()=>new Promise(resolve=>{release=resolve;});
  const pending=c.loadDemandDensityForPoi();
  assert.equal(c.fetches,0,'Density must wait for persisted mode');
  release(true); await pending;
  assert.equal(c.fetches,1);
  assert.match(e('employmentCoverage').textContent,/1 con ocupados estimados/);
  assert.match(e('employmentCoverage').textContent,/96/);
  c.saveCurrentCity=async()=>true;
  c.fetch=async()=>({ok:true,json:async()=>({points:[],diagnostics:{employment_mode:'legacy'}})});
  await c.loadDemandDensityForPoi();
  assert.match(c.error,/no coincide/,'A stale backend must not be presented as census demand');
  assert.equal(e('employmentCoverage').textContent,'');
  c.saveCurrentCity=async()=>false;
  const fetches=c.fetches;
  await c.loadDemandDensityForPoi();
  assert.match(c.error,/Guarda la configuración/);
  assert.equal(c.fetches,fetches,'Failed persistence must prevent a density query');
  c.saveCurrentCity=()=>new Promise(resolve=>{release=resolve;});
  const other=c.loadDemandDensityForPoi();
  c.currentCityFile='other.yaml';
  release(true); await other;
  assert.equal(c.fetches,fetches,'Changing projects during save must cancel the old preview');
  c.cityData={city:{code:'OTHER'},macroeconomics:{}};
  c.populateFormFields();
  assert.equal(e('cfg_residential_employment').value,'census_employed');
  console.log('Census employment UI: mode round trip, autosave binding, save barrier, provenance and stale-mode rejection passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
