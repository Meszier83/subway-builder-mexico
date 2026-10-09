const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '../tools/templates/wizard.html'), 'utf8');
assert.equal((html.match(/id="cfg_demand_engine"/g) || []).length, 1);
assert.match(html, /id="cfg_demand_engine" type="checkbox" role="switch"/);
assert.ok(html.indexOf('id="cfg_demand_engine"') < html.indexOf('id="demandCompatibility"'));

function harness() {
  const controls = new Map();
  const add = (id, value = '') => {
    const element = {value, checked:false, disabled:false, textContent:'', innerText:'',
      classList:{add(){}, remove(){}, contains(){return true;}}, setAttribute(){},
      options:[], appendChild(option){this.options.push(option);}};
    controls.set(id, element); return element;
  };
  for (const id of ['cfg_demand_engine','cfg_residential_placement','cfg_residential_employment',
    'cfg_workplace_employment','cfg_demographic_reference','cfg_eic_indicators','cfg_eic_persons',
    'cfg_conapo_source_year','cfg_cohort_count','conapoYearSelect','demandEngineStatus',
    'btnAutoConapo','btnReplaceConapo','employmentCoverage','workplaceCoverage',
    'btnStartBuild','buildProgressBar','buildStepLabel','buildPercentLabel','buildStatusText','buildStatusBadge']) add(id);
  add('chkSkipMap').checked=true;
  [-90,20,-89,22].forEach((value,i)=>add(`cfg_bbox_${i}`,String(value)));
  const saved = [], drafts = new Map(), notices = [];
  const context = {document:{getElementById:id=>controls.get(id)||null, createElement:()=>({})},
    currentCityFile:'a.yaml', cityData:{city:{bbox:[-90,20,-89,22], residential_placement:'legacy'},
      macroeconomics:{projection_year:2026,residential_employment:'legacy',workplace_employment:'ce_bounded',
        min_pop_size:50,target_pop_size:50,max_pop_size:50},
      pois:[{id:'AIR_keep',jobs:100}], isolated_zones:[{id:'island'}],exclusion_zones:[{id:'water'}]},
    window:{}, densityLoadedCityFile:null, activeEditingPoiIndex:-1, isBboxLocked:false,
    autoSaveTimer:null, citySaveChain:Promise.resolve(), AbortSignal:{timeout(){}},
    setTimeout(){return 1;},clearTimeout(){}, localStorage:{setItem:(key,value)=>drafts.set(key,value),removeItem:key=>drafts.delete(key)},
    wizardLifecycle:{canSave:()=>true}, cancelConapoRequest(){},updateWorkplaceSourceStatus(){},updateDemandMethodsSummary(){},
    updateCohortTelemetry(){}, hideDraftBanner(){},renderPoiList(){},renderPoiMarkersOnMap(){},
    lucide:{createIcons(){}},refreshDataStatus(){context.refreshes=(context.refreshes||0)+1;},
    showToast:message=>notices.push(message),appendTerminalLog(){},workplaceSourceStatus:()=>'',
    refreshWorkplaceSourceStatus:async()=>({}),
    fetchStartupJson:async()=>({denue:{status:'ok'},cpv:{status:'ok'},marco:{status:'ok'},eic:{status:'ok'}}),
    fetch:async(url,options)=>{
      const body=JSON.parse(options.body); saved.push({url,body});
      return {ok:true,json:async()=>({status:'ok',demographic_reference:body.macroeconomics?.demographic_reference})};
    }};
  vm.createContext(context);
  for (const name of ['selectedDemandEngine','updateDemandEngineControls','syncDemandEngineFields',
    'onDemandEngineChange','syncStateFromInputs','syncCohortCount','triggerAutoSave','saveCurrentCity','startBuild']) {
    const match=source.match(new RegExp('    (?:async )?function '+name+'\\(.*?\\n    }','s'));
    assert.ok(match,name);vm.runInContext(match[0],context);
  }
  context.syncDemandEngineFields();
  return {context,controls,saved,drafts,notices};
}

(async()=>{
  let h=harness(); const original=JSON.parse(JSON.stringify(h.context.cityData));
  h.controls.get('cfg_demand_engine').checked=true;
  await h.context.onDemandEngineChange();
  const active=h.saved.at(-1).body;
  assert.equal(active.demand.engine,'v2');assert.equal(active.demand.target_year,2025);
  assert.equal(active.city.residential_placement,'official_blocks');
  assert.equal(active.macroeconomics.workplace_employment,'auto');
  assert.equal(active.macroeconomics.residential_employment,'census_employed');
  assert.equal(active.macroeconomics.demographic_reference.mode,'eic2025');
  assert.equal(active.macroeconomics.demographic_reference.pending_download,true);
  assert.equal(active.macroeconomics.projection_year,2025);
  for (const field of ['pois','isolated_zones','exclusion_zones']) assert.deepEqual(active[field],original[field]);
  assert.deepEqual(active.city.bbox,original.city.bbox);
  assert.equal(active.macroeconomics.target_pop_size,50);
  assert.equal(h.controls.get('cfg_workplace_employment').disabled,true);
  // Reload serialized state, then switch off: recover original methods and year.
  h.context.cityData=JSON.parse(JSON.stringify(active));
  h.controls.get('cfg_demand_engine').checked=false;
  await h.context.onDemandEngineChange();
  const restored=h.saved.at(-1).body;
  assert.equal(restored.demand.engine,'legacy');assert.equal(restored.demand.wizard_legacy_settings,undefined);
  for (const key of ['projection_year','residential_employment','workplace_employment']) assert.equal(restored.macroeconomics[key],original.macroeconomics[key]);
  assert.equal(restored.macroeconomics.demographic_reference,undefined);
  assert.equal(restored.city.residential_placement,'legacy');
  assert.equal(h.controls.get('cfg_workplace_employment').disabled,false);
  // Explicit candidate inputs must move into the reference, avoiding stale overrides after download.
  h=harness();h.context.cityData.demand={engine:'legacy',sources:{eic_indicators:['i.csv'],eic_persons:['personas31.csv'],ce:['ce.csv']}};
  h.controls.get('cfg_demand_engine').checked=true;await h.context.onDemandEngineChange();
  assert.equal(h.saved.at(-1).body.macroeconomics.demographic_reference.indicators,'i.csv');
  assert.equal(h.context.cityData.demand.sources.eic_persons,undefined);
  assert.deepEqual(Array.from(h.context.cityData.demand.sources.ce),['ce.csv']);
  h.controls.get('cfg_demand_engine').checked=false;await h.context.onDemandEngineChange();
  assert.deepEqual(Array.from(h.context.cityData.demand.sources.eic_persons),['personas31.csv']);
  // A failed save leaves an honest status and a recoverable draft.
  h=harness();h.context.fetch=async()=>({ok:false,json:async()=>({error:'offline'})});
  h.controls.get('cfg_demand_engine').checked=true;await h.context.onDemandEngineChange();
  assert.match(h.controls.get('demandEngineStatus').textContent,/No se pudo guardar/);
  assert.ok(h.drafts.has('sb_draft_a.yaml'));assert.equal(h.context.refreshes,undefined);
  // No source-status refresh or normalized reference from another project.
  h=harness();let finish;
  h.context.fetch=()=>new Promise(resolve=>{finish=resolve;});
  h.controls.get('cfg_demand_engine').checked=true;
  const pending=h.context.onDemandEngineChange();await Promise.resolve();await Promise.resolve();
  h.context.currentCityFile='b.yaml';h.context.cityData={city:{},macroeconomics:{}};
  finish({ok:true,json:async()=>({status:'ok',demographic_reference:{mode:'eic2025',indicators:'other.csv',persons:['personas31.csv']}})});
  await pending;assert.equal(h.context.refreshes,undefined);assert.equal(h.context.cityData.macroeconomics.demographic_reference,undefined);
  // Missing EIC blocks start before any build request, with actionable feedback.
  h=harness();h.controls.get('cfg_demand_engine').checked=true;await h.context.onDemandEngineChange();
  h.context.fetchStartupJson=async()=>({denue:{status:'ok'},cpv:{status:'ok'},marco:{status:'ok'},eic:{status:'missing'}});
  await h.context.startBuild();assert.equal(h.saved.some(r=>r.url==='/api/build/start'),false);
  assert.match(h.notices.at(-1),/EIC 2025.*Preparar descargas/);
  assert.equal(h.context.cityData.demand.engine,'v2');
  h.context.fetchStartupJson=async()=>({denue:{status:'ok'},cpv:{status:'ok'},marco:{status:'ok'},eic:{status:'ok'}});
  await h.context.startBuild();assert.equal(h.saved.at(-1).url,'/api/build/start');assert.equal(h.saved.at(-1).body.file,'a.yaml');
  console.log('Demand engine toggle: activation, reload/reversal, preserved map settings, drafts, stale project and build preflight: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
