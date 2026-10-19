const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const fn = source.match(/    async function previewDemandV2\(.*?\n    }/s)[0];
const format = source.match(/    function formatDemandSensitivity\(.*?\n    }/s)[0];
const diagnostic = status => ({status,source_preparation_seconds:2,comparison_seconds:.03,
  sample_size:512,coverage:{sampled_origins:2,origin_count:10,residential_mass_share:.4,omitted_municipalities:[]},
  summaries:[{beta:.12,median_km:5,p90_km:15,over_20_km_share:.1}],comparisons:[]});
function harness() {
  const label = {textContent:''};
  const context = {currentCityFile:'a.yaml',cityData:{city:{},macroeconomics:{},pois:[{id:'UNI_keep',jobs:12}]},
    document:{getElementById(){return label;}},syncStateFromInputs(){},
    saveCurrentCity:async()=>true,window:{},updateCohortTelemetry(){},updateDemandEngineControls(){},
    fetch:async(url)=>({ok:true,json:async()=>({points:[],report:{sensitivity:diagnostic('low_variation')}})})};
  vm.createContext(context); vm.runInContext(format+'\n'+fn,context);
  return {context,label};
}
(async()=>{
  let {context,label}=harness();
  const original=JSON.stringify(context.cityData);
  await context.previewDemandV2();
  assert.match(label.textContent,/Poca variación estimada/);
  assert.match(label.textContent,/mediana 5\.00 km · P90 15\.00 km/);
  assert.match(label.textContent,/fuentes y puntos: 2\.00 s · comparación: 0\.03 s/);
  assert.equal(context.window.demandV2CohortMetadata,undefined,'screen must not publish final cohorts');
  assert.equal(context.window.demandV2SensitivityMetadata.file,'a.yaml');
  assert.equal(JSON.stringify(context.cityData),original,'evaluation must not activate candidate or mutate POIs');
  for (const [status,labelText] of [['sensitive',/Variación importante/],['inconclusive',/Comprobación inconclusa/]]) {
    const result=diagnostic(status);
    if (status==='inconclusive') {result.reason='timeout';delete result.summaries;}
    context.fetch=async()=>({ok:true,json:async()=>({report:{sensitivity:result}})});
    await context.previewDemandV2();assert.match(label.textContent,labelText);
    if (status==='inconclusive') assert.match(label.textContent,/descartaron comparaciones parciales/);
  }
  ({context,label}=harness());
  context.saveCurrentCity=async()=>false;
  context.fetch=async()=>assert.fail('must not query unsaved configuration');
  await context.previewDemandV2();
  assert.match(label.textContent,/guardar/);
  ({context,label}=harness());
  context.fetch=async()=>{context.currentCityFile='b.yaml';return {ok:true,json:async()=>({})};};
  await context.previewDemandV2();
  assert.equal(label.textContent,'Preparando fuentes y puntos para comprobar sensibilidad…','stale response must not render');
  assert.equal(context.window.demandV2SensitivityMetadata,null);
  ({context,label}=harness());
  context.fetch=async()=>{context.cityData.city.bbox=[0,0,1,1];return {ok:true,json:async()=>({})};};
  await context.previewDemandV2();assert.equal(context.window.demandV2SensitivityMetadata,null,'changed configuration must not publish');
  ({context,label}=harness());
  const completedFetch=context.fetch;const requests=[];
  context.setTimeout=resolve=>resolve();
  context.fetch=async url=>{requests.push(url);return requests.length===1 ?
    {ok:true,json:async()=>({status:'running',job_id:'job-1'})}:completedFetch(url);};
  await context.previewDemandV2();
  assert.match(requests[0],/async=1/);assert.match(requests[1],/job=job-1/);
  assert.match(requests[0],/stage=sensitivity/);
  assert.match(label.textContent,/Poca variación estimada/);
  ({context,label}=harness());
  context.setTimeout=resolve=>{context.currentCityFile='b.yaml';resolve();};
  let polls=0;
  context.fetch=async()=>{polls++;return {ok:true,json:async()=>({status:'running',job_id:'job-1'})};};
  await context.previewDemandV2();assert.equal(polls,1,'project change stops polling');
  ({context,label}=harness());
  context.fetch=async()=>({ok:true,json:async()=>({status:'error',error:'No feasible support'})});
  await context.previewDemandV2();assert.match(label.textContent,/No feasible support/);
  console.log('Demand v2 sensitivity UI: all outcomes, timings, no final cohorts, save barrier, stale configuration/project and polling: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});

const controls = new Map();
const element = id => {if (!controls.has(id)) controls.set(id,{value:'',innerText:'',className:''});return controls.get(id);};
for (const [id,value] of Object.entries({cfg_demand_engine:'v2',cfg_min_pop_size:'25',cfg_target_pop_size:'150',cfg_max_pop_size:'200',cfg_cohort_count:'4000'})) element(id).value=value;
element('cfg_demand_engine').checked=true;
const context={document:{getElementById:element},cityData:{macroeconomics:{},demand:{engine:'v2'}},currentCityFile:'a.yaml',window:{}};
vm.createContext(context);
vm.runInContext(source.match(/    function workplaceSourceStatus\(.*?\n    }/s)[0],context);
const employment=context.workplaceSourceStatus({mode:'historical_fine_transfer',reference_year:2023,
  coverage:{bbox:{attraction_share:.3478,establishments:100,transferred_establishments:40}},fallback_reasons:{suppressed:60}});
assert.match(employment,/34.78% del peso/);
assert.match(employment,/60 establecimientos conservan estimaciones DENUE/);
assert.match(employment,/Antes del recorte urbano y POIs/);
for (const name of ['selectedDemandEngine','syncCohortCount','updateCohortTelemetry','setCohortValues']) vm.runInContext(source.match(new RegExp('    function '+name+'\\(.*?\\n    }','s'))[0],context);
context.syncCohortCount(); assert.equal(context.cityData.demand.cohort_count,4000);
context.updateCohortTelemetry(); assert.match(element('cohort_estimated_pops').innerText,/4,000 cohortes solicitadas/);
context.window.demandV2CohortMetadata={file:'a.yaml',state:JSON.stringify(context.cityData),commuters:600000,report:{actual_count:4000,minimum_feasible:3000,options:{cohort_count:4000,min_pop_size:25,target_pop_size:150,max_pop_size:200}}};
context.updateCohortTelemetry(); assert.match(element('cohort_estimated_pops').innerText,/calculadas/);
context.cityData.city={bbox:[0,0,1,1]}; context.updateCohortTelemetry(); assert.doesNotMatch(element('cohort_estimated_pops').innerText,/calculadas/);
element('cfg_cohort_count').value=''; context.syncCohortCount(); assert.equal(context.cityData.demand.cohort_count,null);
context.cityData.macroeconomics={min_pop_size:100,target_pop_size:100,max_pop_size:100,cohort_mode:'rigid'};
for (const id of ['cfg_min_pop_size','cfg_target_pop_size','cfg_max_pop_size']) element(id).value='100';
element('cfg_cohort_count').value='4000'; context.syncCohortCount(); assert.equal(context.cityData.demand.cohort_count,null);
context.updateCohortTelemetry(); assert.equal(element('cfg_cohort_count').disabled,true);
assert.match(element('cohort_estimated_pops').innerText,/Al menos/);
assert.match(element('cohort_dynamics_text').innerText,/Hasta 100 personas/);
assert.match(element('cohort_recommendation_text').innerText,/500 m/);
assert.match(element('cohort_recommendation_text').innerText,/restos locales adicionales/);
context.cityData.demand.fixed_cohort_size=200;
context.setCohortValues(50,50,50,false); assert.equal(context.cityData.demand.fixed_cohort_size,50);
assert.match(element('cohort_dynamics_text').innerText,/Hasta 50 personas/);
element('cfg_demand_engine').checked=true;
context.cityData.demand.exact_cohort_size=true;
context.updateCohortTelemetry();
assert.match(element('cohort_dynamics_text').innerText,/Exactamente 50 personas por cohorte/);
assert.match(element('cohort_recommendation_text').innerText,/Modo Exacto Oficial/);
element('cfg_demand_engine').checked=false; context.updateCohortTelemetry(); assert.equal(element('cfg_cohort_count').disabled,true);
console.log('Cohort count UI: persisted total, automatic reset, verified count, engine scope: PASS');
