const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('tools/static/wizard/app.js','utf8');
const production = source.slice(source.indexOf('    function updateDemandMethodsSummary('), source.indexOf('    function syncStateFromInputs()'));
const controls = new Map();
function node() { return {value:'',textContent:'',style:{},children:[],append(...items){this.children.push(...items);},replaceChildren(){this.children=[];}}; }
function element(id) { if (!controls.has(id)) controls.set(id,node()); return controls.get(id); }
const checkboxes = () => element('historicalTransferGroups').children.map(label=>label.children[0]);
const context = {currentCityFile:'fixture.yaml',cityData:{macroeconomics:{workplace_employment:'legacy'}},densityLoadedCityFile:'fixture.yaml',
  document:{getElementById:element,createElement:node,createTextNode:text=>({text}),querySelectorAll:()=>checkboxes().filter(c=>c.checked)},
  triggerAutoSave(){context.saves++;},saves:0};
element('cfg_ce_reference_year').value='2023'; element('cfg_ce_sources').value='reports/source.csv';
element('cfg_ce_transfer_strength').value='1';
context.fetch = async (url,options) => {
  const contract = JSON.parse(options.body).contract;
  contract.source_sha256={denue:['a'.repeat(64)],ce:['b'.repeat(64)]};
  const groups=[{municipality:'23005',activity_code:'72',scian_prefixes:['72'],remaining_records:5,review_records:0},
                {municipality:'23006',activity_code:'53',scian_prefixes:['53'],remaining_records:3,review_records:0}];
  return {ok:true,json:async()=>({contract,report:{reference_year:2023,source_binding:'match',groups,
    publication_counts:{published:2,suppressed:0},scope_counts:{excluded:2,review:1},denue_edition:{label:'unknown'},
    transfer_preflight:{controls:[{...groups[0],status:'TRANSFERRED_HISTORICAL_MEAN'}, {...groups[1],status:'MISSING_CE_CONTROL'}]}}})};
};
vm.createContext(context); vm.runInContext(production,context);
(async()=>{
  await context.inspectWorkplaceBenchmark();
  assert.equal(context.cityData.macroeconomics.workplace_employment,'legacy');
  assert.equal(checkboxes().length,2); assert.equal(checkboxes()[1].disabled,true);
  assert.match(element('cfg_ce_unit_evidence').value,/INEGI CE2024/);
  element('cfg_ce_unit_evidence').value='';
  checkboxes()[0].checked=true;
  context.activateHistoricalTransfer(); assert.match(element('historicalBenchmarkStatus').textContent,/evidencia/);
  element('cfg_ce_unit_evidence').value='CE methodology: ordinary establishment';
  element('cfg_ce_reference_year').value='2018';
  context.activateHistoricalTransfer(); assert.match(element('historicalBenchmarkStatus').textContent,/cambiaron/);
  assert.equal(context.cityData.macroeconomics.workplace_employment,'legacy');
  element('cfg_ce_reference_year').value='2023'; context.activateHistoricalTransfer();
  const transfer=context.cityData.macroeconomics.historical_workplace_transfer;
  assert.equal(transfer.enabled,true); assert.equal(transfer.role,'historical_transfer');
  assert.equal(transfer.groups.length,1); assert.equal(transfer.groups[0].municipality,'23005');
  assert.equal(context.cityData.macroeconomics.historical_workplace_benchmark.enabled,false);
  assert.equal(context.cityData.macroeconomics.workplace_employment,'historical_transfer');
  assert.equal(context.densityLoadedCityFile,null);
  await context.reinspectHistoricalSources();
  assert.equal(context.cityData.macroeconomics.workplace_employment,'auto');
  assert.equal(context.cityData.macroeconomics.historical_workplace_transfer,undefined);
  assert.equal(context.cityData.macroeconomics.historical_workplace_benchmark.source_sha256.ce[0],'b'.repeat(64));
  console.log('Historical transfer UI: explicit group activation, evidence, fallback groups, changed-selection rejection and explicit rebind passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
