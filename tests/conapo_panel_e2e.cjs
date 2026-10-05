// Run only against a disposable fixture and a running Wizard.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const file = process.argv[2];
assert(file && file.includes('.conapo-e2e-'), 'A disposable CONAPO fixture is required');
const base = process.argv[3] || 'http://127.0.0.1:8080';
const html = require('./wizard_ui_source.cjs');
const start = html.indexOf('    let conapoRequestVersion =');
const end = html.indexOf('    function renderGrowthFactorsTable()', start);
const controls = new Map();
const element = id => {
  if (!controls.has(id)) controls.set(id, {value: id === 'conapoYearSelect' ? '2026' : '', disabled: false,
    options: [], appendChild(option) {this.options.push(option);}, setAttribute() {},
    set innerHTML(value) {this.options = [];}});
  return controls.get(id);
};
(async () => {
  const cityUrl = `/api/city?file=${encodeURIComponent(file)}`;
  const read = async () => {console.log('Read fixture'); const response = await fetch(base + cityUrl, {signal: AbortSignal.timeout(10000)}); assert(response.ok); return response.json();};
  const c = {document: {getElementById: element, createElement: () => ({}), querySelectorAll: () => [...controls.values()]},
    window: {confirm: () => true}, currentCityFile: file, cityData: await read(),
    fetch: (url, options) => {console.log(options?.method || 'GET', url.split('?')[0]); return fetch(base + url, {...options, signal: options?.signal || AbortSignal.timeout(10000)});}, AbortController, AbortSignal, setTimeout, clearTimeout,
    autoSaveTimer: null, localStorage: {removeItem() {}}, lucide: {createIcons() {}},
    syncStateFromInputs() {}, renderGrowthFactorsTable() {}, hideDraftBanner() {}, renderPoiList() {}, renderPoiMarkersOnMap() {},
    toasts: [], showToast(message, kind) {c.toasts.push({message, kind});}};
  vm.createContext(c);
  vm.runInContext(html.slice(start, end), c);
  vm.runInContext(html.match(/    async function saveCurrentCity\(.*?\n    }/s)[0], c);
  await c.loadConapoYears();
  assert.equal(element('conapoYearSelect').options.length, 3, JSON.stringify(c.toasts));
  await c.autoCalculateConapo(2026);
  assert.equal((await read()).macroeconomics.growth_factors['23005'], 1.1, 'existing unknown factor remains guarded');
  await c.autoCalculateConapo(2024, true);
  let reloaded = await read();
  assert.equal(reloaded.macroeconomics.projection_year, 2024);
  assert.equal(reloaded.macroeconomics.growth_factors['23005'], 1.15);
  assert.equal(reloaded.macroeconomics.growth_factor_sources['23005'].ano, 2024);
  c.cityData = reloaded;
  await c.autoCalculateConapo(2026);
  reloaded = await read();
  assert.equal(reloaded.macroeconomics.projection_year, 2026);
  assert.equal(reloaded.macroeconomics.growth_factors['23005'], 1.5);
  assert.equal(reloaded.macroeconomics.growth_factor_sources['23005'].ano, 2026);
  assert.equal(reloaded.macroeconomics.growth_factor_sources['23005'].kind, 'conapo');
  assert.equal(c.toasts.filter(item => item.kind === 'error').length, 0, JSON.stringify(c.toasts));
  console.log('CONAPO real HTTP: inspect years, protect existing factor, replace explicitly, change year, atomic save/reload: PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
