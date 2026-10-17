const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const controls = new Map();
for (const id of ['cfg_demand_engine','legacyMacroHeader','legacyMacroRates','legacyConapoPanel',
  'v2DemandPanel','v2BetaSummary','cfg_demand_beta','cfg_demand_beta_selection','cfg_tasa_pea','cfg_til_1','cfg_gravity_beta','btnResetMacro']) {
  const classes = new Set();
  controls.set(id,{checked:true,value:'',disabled:false,textContent:'',checkValidity:()=>true,
    classList:{add:n=>classes.add(n),remove:n=>classes.delete(n),contains:n=>classes.has(n)}});
}
const context = {currentCityFile:'a.yaml',cityData:{demand:{engine:'v2'},macroeconomics:{gravity_beta:.18}},
  document:{getElementById:id=>controls.get(id)||null},window:{},triggerAutoSave(){this.saves=(this.saves||0)+1;}};
vm.createContext(context);
for (const name of ['selectedDemandEngine','updateDemandEngineControls','onDemandBetaChanged','onDemandBetaSelectionChanged','formatDemandSensitivity'])
  vm.runInContext(source.match(new RegExp('    function '+name+'\\(.*?\\n    }','s'))[0],context);
context.updateDemandEngineControls();
for (const id of ['legacyMacroHeader','legacyMacroRates','legacyConapoPanel']) assert(controls.get(id).classList.contains('hidden'));
assert(!controls.get('v2DemandPanel').classList.contains('hidden'));
assert(controls.get('cfg_gravity_beta').disabled);
assert.match(controls.get('v2BetaSummary').textContent,/Pendiente/);
assert.equal(controls.get('cfg_demand_beta_selection').value,'reference');
assert.match(controls.get('v2BetaSummary').textContent,/Referencia 0,120/);
context.window.demandV2CohortMetadata={file:'a.yaml',state:JSON.stringify(context.cityData),allocation:{
  beta:.09,beta_calibrated:true,beta_policy:{mode:'automatic',reason:'stable_guard_improvement'},
  distance_summary:{median_km:4.5,p90_km:12.25}}};
context.updateDemandEngineControls();
assert.match(controls.get('v2BetaSummary').textContent,/0\.090 · Ajustada con EIC/);
assert.match(controls.get('v2BetaSummary').textContent,/4\.50 km · percentil 90 12\.25 km/);
const input=controls.get('cfg_demand_beta');input.value='.18';context.onDemandBetaChanged(input);
assert.equal(context.cityData.demand.beta,.18);
assert.equal(context.cityData.macroeconomics.gravity_beta,.18);
assert.match(controls.get('v2BetaSummary').textContent,/Pendiente/,'old state must not appear calibrated');
input.value='';context.onDemandBetaChanged(input);assert.equal(context.cityData.demand.beta,undefined);
const selection=controls.get('cfg_demand_beta_selection');selection.value='automatic';context.onDemandBetaSelectionChanged(selection);
assert.equal(context.cityData.demand.beta_selection,'automatic');
assert.match(controls.get('v2BetaSummary').textContent,/automática al compilar/);
context.window.demandV2SensitivityMetadata={file:'a.yaml',state:JSON.stringify(context.cityData),report:{status:'sensitive',comparisons:[]}};
context.updateDemandEngineControls();assert.match(controls.get('v2BetaSummary').textContent,/Variación importante/);
context.cityData.city={bbox:[0,0,1,1]};context.updateDemandEngineControls();
assert.doesNotMatch(controls.get('v2BetaSummary').textContent,/Variación importante/,'configuration changes invalidate sensitivity');
context.window.demandV2SensitivityMetadata={file:'a.yaml',state:JSON.stringify(context.cityData),report:{status:'sensitive',comparisons:[]}};
context.currentCityFile='b.yaml';context.updateDemandEngineControls();
assert.match(controls.get('v2BetaSummary').textContent,/Pendiente/,'another project must not inherit results');
controls.get('cfg_demand_engine').checked=false;context.updateDemandEngineControls();
for (const id of ['legacyMacroHeader','legacyMacroRates','legacyConapoPanel']) assert(!controls.get(id).classList.contains('hidden'));
assert(controls.get('v2DemandPanel').classList.contains('hidden'));
assert(!controls.get('cfg_gravity_beta').disabled);
console.log('Beta UI: engine scope, final metrics, stale state/project, manual/auto and legacy restoration: PASS');
