const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const fn = source.match(/    async function previewDemandV2\(.*?\n    }/s)[0];
function harness() {
  const label = {textContent:''};
  const context = {currentCityFile:'a.yaml',cityData:{city:{},macroeconomics:{},pois:[{id:'UNI_keep',jobs:12}]},
    document:{getElementById(){return label;}},syncStateFromInputs(){},
    saveCurrentCity:async()=>true,window:{},updateCohortTelemetry(){},
    fetch:async(url)=>({ok:true,json:async()=>({points:[{}],report:{target_year:2020,territory:{integer_commuters:21},
      workplaces:{coverage:{bbox:{attraction_share:.3478}}},
      allocation:{beta:.12,beta_basis:'fallback',poi_quotas:[{shortfall:2}],validation:{conditional_kl:.1},validation_integer:{conditional_kl:1.2}}}})})};
  vm.createContext(context); vm.runInContext(fn,context);
  return {context,label};
}
(async()=>{
  let {context,label}=harness();
  const original=JSON.stringify(context.cityData);
  await context.previewDemandV2();
  assert.match(label.textContent,/21 viajeros/);
  assert.match(label.textContent,/Candidato 2020/);
  assert.match(label.textContent,/faltan 2 viajeros/);
  assert.match(label.textContent,/34.78% del peso/);
  assert.match(label.textContent,/resto conserva estimaciones DENUE/);
  assert.match(label.textContent,/continuo 0\.1000 · enteros 1\.2000/);
  assert.equal(JSON.stringify(context.cityData),original,'evaluation must not activate candidate or mutate POIs');
  ({context,label}=harness());
  context.saveCurrentCity=async()=>false;
  context.fetch=async()=>assert.fail('must not query unsaved configuration');
  await context.previewDemandV2();
  assert.match(label.textContent,/guardar/);
  ({context,label}=harness());
  context.fetch=async()=>{context.currentCityFile='b.yaml';return {ok:true,json:async()=>({})};};
  await context.previewDemandV2();
  assert.equal(label.textContent,'Evaluando candidato…','stale response must not render');
  ({context,label}=harness());
  const completedFetch=context.fetch;const requests=[];
  context.setTimeout=resolve=>resolve();
  context.fetch=async url=>{requests.push(url);return requests.length===1 ?
    {ok:true,json:async()=>({status:'running',job_id:'job-1'})}:completedFetch(url);};
  await context.previewDemandV2();
  assert.match(requests[0],/async=1/);assert.match(requests[1],/job=job-1/);
  assert.match(label.textContent,/21 viajeros/);
  ({context,label}=harness());
  context.setTimeout=resolve=>{context.currentCityFile='b.yaml';resolve();};
  let polls=0;
  context.fetch=async()=>{polls++;return {ok:true,json:async()=>({status:'running',job_id:'job-1'})};};
  await context.previewDemandV2();assert.equal(polls,1,'project change stops polling');
  ({context,label}=harness());
  context.fetch=async()=>({ok:true,json:async()=>({status:'error',error:'No feasible support'})});
  await context.previewDemandV2();assert.match(label.textContent,/No feasible support/);
  console.log('Demand v2 UI: save barrier, passive evaluation, POI preservation, stale response: PASS');
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
assert.match(element('cohort_fps_badge').innerText,/pendiente/);
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
element('cfg_demand_engine').checked=false; context.updateCohortTelemetry(); assert.equal(element('cfg_cohort_count').disabled,true);
console.log('Cohort count UI: persisted total, automatic reset, verified count, engine scope: PASS');
