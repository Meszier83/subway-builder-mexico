const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = require('./wizard_ui_source.cjs');
const begin = html.indexOf('    let conapoRequestVersion =');
const end = html.indexOf('    function renderGrowthFactorsTable()', begin);
assert(begin > 0 && end > begin);
const code = html.slice(begin, end);

function harness() {
  const controls = new Map();
  const element = id => {
    if (!controls.has(id)) controls.set(id, {value: id === 'conapoYearSelect' ? '2026' : '', disabled: false,
      options: [], appendChild(option) {this.options.push(option);},
      set innerHTML(value) {this.options = []; this.html = value;}, get innerHTML() {return this.html;}});
    return controls.get(id);
  };
  const context = {
    document: {getElementById: element, createElement: () => ({}), querySelectorAll: () => [...controls.values()]},
    window: {confirm: () => true}, currentCityFile: 'cities/test.yaml',
    cityData: {city: {code: 'TST'}, macroeconomics: {projection_year: 2024, growth_factors: {'23005': 1.23}}},
    AbortController, AbortSignal, setTimeout, clearTimeout, console,
    syncStateFromInputs() {}, renderGrowthFactorsTable() {}, saved: [], toasts: [],
    showToast(message, kind) {context.toasts.push({message, kind});},
    saveCurrentCity: async () => {context.saved.push(JSON.parse(JSON.stringify(context.cityData))); return true;},
    response: {status: 'ok', conapo_file: 'conapo.csv', requested_year: 2026, projection_year: 2026,
      available_years: [2020, 2024, 2026], result_token: 'stable', total_factor_count: 2, next_cursor: null,
      factors: [{cve_mun: '23005', factor: 1.5, ano: 2026}, {cve_mun: '23008', factor: 1.11, ano: 2026}]},
    fetch: async () => ({ok: true, json: async () => JSON.parse(JSON.stringify(context.response))})
  };
  vm.createContext(context); vm.runInContext(code, context);
  return {context, element};
}

(async () => {
  {
    const {context: c, element} = harness();
    await c.loadConapoYears();
    assert.equal(c.saved.length, 0, 'year inspection writes no configuration');
    assert.equal(element('conapoYearSelect').options.length, 3);
    await c.onConapoYearSelectChange('2026');
    assert.equal(c.cityData.macroeconomics.projection_year, 2024);
    assert.equal(c.saved.length, 0, 'selecting year does not save old factors under a new year');
    await c.autoCalculateConapo(2026);
    assert.equal(c.cityData.macroeconomics.growth_factors['23005'], 1.23, 'legacy values remain protected');
    assert.equal(c.cityData.macroeconomics.growth_factors['23008'], 1.11);
    assert.equal(c.cityData.macroeconomics.growth_factor_sources['23008'].ano, 2026);
    assert.equal(c.saved.length, 2, 'success is persisted immediately');
    assert.equal(element('btnAutoConapo').disabled, false);
    await c.autoCalculateConapo(2026, true);
    assert.equal(c.cityData.macroeconomics.growth_factors['23005'], 1.5, 'explicit replacement recalculates guarded values');
    c.response.factors[0].factor = 1.55;
    await c.autoCalculateConapo(2026);
    assert.equal(c.cityData.macroeconomics.growth_factors['23005'], 1.55, 'automatic values are refreshed');
    const edit = html.match(/    function updateGrowthFactor\(.*?\n    }/s)[0];
    c.triggerAutoSave = () => {};
    vm.runInContext(edit, c);
    c.updateGrowthFactor('23005', '1.4');
    assert.equal(c.cityData.macroeconomics.growth_factor_sources['23005'].kind, 'manual');
    await c.autoCalculateConapo(2026);
    assert.equal(c.cityData.macroeconomics.growth_factors['23005'], 1.4, 'editing an automatic factor protects it as manual');
  }
  {
    const {context: c, element} = harness();
    c.fetch = async () => ({ok: true, json: async () => ({status: 'needs_source_year', message: 'Confirma el año'})});
    await c.loadConapoYears();
    assert.equal(c.saved.length, 0);
    assert.equal(element('conapoYearSelect').disabled, false);
    assert.equal(c.window.conapoAvailableYears, null);
    assert.equal(element('conapoSubtitleLabel').textContent, 'Confirma el año');
    c.window.confirm = () => false;
    await c.autoCalculateConapo(2026, true);
    assert.equal(c.saved.length, 0, 'declining replacement leaves values intact');
  }
  for (const failure of ['pre-save exception', 'save refused', 'HTTP error', 'invalid JSON', 'partial page', 'zero rows', 'commit refused']) {
    const {context: c, element} = harness();
    const before = JSON.stringify(c.cityData);
    if (failure === 'pre-save exception') c.saveCurrentCity = async () => {throw new Error(failure);};
    if (failure === 'save refused') c.saveCurrentCity = async () => false;
    if (failure === 'HTTP error') c.fetch = async () => ({ok: false, json: async () => ({error: 'HTTP failed'})});
    if (failure === 'invalid JSON') c.fetch = async () => ({ok: true, json: async () => {throw new SyntaxError('JSON cut');}});
    if (failure === 'partial page') c.response.total_factor_count = 3;
    if (failure === 'zero rows') {c.response.factors = []; c.response.total_factor_count = 0;}
    if (failure === 'commit refused') {let count = 0; c.saveCurrentCity = async () => ++count === 1;}
    await c.autoCalculateConapo(2026);
    assert.equal(JSON.stringify(c.cityData), before, failure + ': no partial mutation');
    assert.equal(element('btnAutoConapo').disabled, false, failure + ': button recovers');
    assert.equal(element('conapoYearSelect').disabled, false, failure + ': selector recovers');
    assert(c.toasts.some(item => item.kind === 'error'), failure + ': error visible');
    assert(!c.toasts.some(item => item.kind === 'success'), failure + ': no false success');
  }
  {
    const {context: c} = harness();
    let saves = 0;
    c.saveCurrentCity = async () => ++saves === 1;
    c.fetch = async url => ({ok: true, json: async () => url.startsWith('/api/city?') ? structuredClone(c.cityData) : c.response});
    await c.autoCalculateConapo(2026);
    assert.equal(c.cityData.macroeconomics.growth_factors['23008'], 1.11, 'lost POST response is recovered by persisted-state readback');
    assert(c.toasts.some(item => item.kind === 'success'));
  }
  {
    const {context: c} = harness();
    const realSave = html.match(/    async function saveCurrentCity\(.*?\n    }/s)[0];
    Object.assign(c, {autoSaveTimer: null, hideDraftBanner() {}, renderPoiList() {}, renderPoiMarkersOnMap() {},
      localStorage: {removeItem() {}}, lucide: {createIcons() {}}});
    vm.runInContext(realSave, c);
    const requests = [];
    const completions = [];
    c.fetch = (_, options) => {requests.push(JSON.parse(options.body)); return new Promise(done => completions.push(done));};
    const first = c.saveCurrentCity(true);
    c.cityData.macroeconomics.projection_year = 2026;
    const second = c.saveCurrentCity(true);
    c.currentCityFile = 'cities/other.yaml';
    await new Promise(done => setImmediate(done));
    assert.equal(requests.length, 1, 'configuration saves are serialized');
    completions[0]({ok: true, json: async () => ({status: 'ok'})});
    await first;
    await new Promise(done => setImmediate(done));
    assert.equal(requests.length, 2);
    assert.equal(requests[0].macroeconomics.projection_year, 2024);
    assert.equal(requests[1].macroeconomics.projection_year, 2026);
    assert.equal(requests[1].file, 'cities/test.yaml', 'queued save retains its original project');
    completions[1]({ok: true, json: async () => ({status: 'ok'})});
    await second;
  }
  {
    const {context: c} = harness();
    const whole = structuredClone(c.response);
    let pages = 0;
    c.fetch = async () => {
      pages++;
      return {ok: true, json: async () => ({...whole, factors: [whole.factors[pages - 1]], next_cursor: pages === 1 ? 1 : null})};
    };
    await c.autoCalculateConapo(2026, true);
    assert.equal(pages, 2);
    assert.equal(Object.keys(c.cityData.macroeconomics.growth_factors).length, 2);
    assert.equal(c.saved.length, 2, 'pages are applied only once, after completion');
  }
  {
    const {context: c} = harness();
    let resolve;
    c.fetch = () => new Promise(done => {resolve = done;});
    const pending = c.autoCalculateConapo(2026);
    await new Promise(done => setImmediate(done));
    c.currentCityFile = 'cities/other.yaml';
    c.cityData = {city: {code: 'OTHER'}, macroeconomics: {projection_year: 2024, growth_factors: {}}};
    const other = JSON.stringify(c.cityData);
    c.cancelConapoRequest();
    resolve({ok: true, json: async () => c.response});
    await pending;
    assert.equal(JSON.stringify(c.cityData), other, 'late response cannot mutate another project');
    assert.equal(c.saved.length, 1);
  }
  {
    const {context: c} = harness();
    c.fetch = async () => {c.cityData.city.code = 'EDITED'; return {ok: true, json: async () => c.response};};
    await c.autoCalculateConapo(2026);
    assert.equal(c.cityData.city.code, 'EDITED');
    assert.equal(c.cityData.macroeconomics.projection_year, 2024);
    assert(c.toasts.some(item => item.message.includes('cambió')));
  }
  console.log('CONAPO UI: years, guarded/manual and automatic factors, pagination, rollback, errors, stale results: PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
