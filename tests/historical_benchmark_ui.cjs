const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/static/wizard/app.js','utf8');
const fn = source.match(/    async function inspectWorkplaceBenchmark\(\).*?\n    }/s)[0];
const controls = new Map();
const element = id => {if (!controls.has(id)) controls.set(id,{value:'',textContent:''}); return controls.get(id);};
const exact = {reference_year:2023, groups:[{municipality:'23005',scian_prefix:'46'}]};
const context = {currentCityFile:'fixture.yaml',cityData:{macroeconomics:{workplace_employment:'ce_bounded',workplace_control_contract:exact}},
  document:{getElementById:element},saves:0,triggerAutoSave(){context.saves++;}};
element('cfg_ce_reference_year').value='2023'; element('cfg_ce_sources').value='reports/staged.csv';
let request;
context.fetch = async (url, options) => {
  assert.equal(url,'/api/workplace/inspect'); request=JSON.parse(options.body);
  return {ok:true,json:async()=>({contract:request.contract,report:{reference_year:2023,source_binding:'unbound',
    publication_counts:{published:2,suppressed:1},scope_counts:{excluded:3,review:4},denue_edition:{label:'unknown'},
    groups:[{municipality:'23005',activity_code:'72',remaining_records:5,review_records:0,status:'CONDITIONAL_HISTORICAL_BENCHMARK',hard_fit_within_bounds:true}]}})};
};
vm.createContext(context); vm.runInContext(fn,context);
(async()=>{
  await context.inspectWorkplaceBenchmark();
  assert.equal(context.saves,1);
  assert.equal(context.cityData.macroeconomics.workplace_control_contract,exact,'Inspection cannot replace existing employment contract');
  assert.equal(context.cityData.macroeconomics.workplace_employment,'ce_bounded');
  assert.equal(context.cityData.macroeconomics.historical_workplace_benchmark.enabled,false);
  assert.match(element('historicalBenchmarkStatus').textContent,/inactiva/);
  assert.match(element('historicalBenchmarkGroups').textContent,/23005 · 72/);
  const before=JSON.stringify(context.cityData);
  context.fetch=async()=>({ok:true,json:async()=>({report:{source_binding:'mismatch'}})});
  await context.inspectWorkplaceBenchmark();
  assert.equal(JSON.stringify(context.cityData),before); assert.equal(context.saves,1);
  assert.match(element('historicalBenchmarkStatus').textContent,/fuentes cambiaron/);
  let release;
  context.fetch=()=>new Promise(resolve=>{release=resolve;});
  const pending=context.inspectWorkplaceBenchmark(); context.currentCityFile='other.yaml';
  release({ok:true,json:async()=>({report:{source_binding:'unbound'}})}); await pending;
  assert.equal(JSON.stringify(context.cityData),before); assert.equal(context.saves,1);
  console.log('Historical benchmark UI: inactive persistence, preserved employment contract, group diagnostics, stale-source and stale-city protection passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
