'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const create = require('../mods/cur-economic-profile/controller.js');
const profile = JSON.parse(fs.readFileSync(path.join(root, 'reports/cur-economic-pilot/profile.json'), 'utf8'));
const clone = value => structuredClone(value);
let passed = 0;
function test(name, fn) { fn(); passed++; process.stdout.write(`PASS ${name}\n`); }
function environment() {
  const defaults = Object.fromEntries(Object.keys(profile.rules).map((key, i) => [key, i + 100]));
  defaults.UNTARGETED = 123;
  const rules = clone(defaults), hooks = {}, messages = [], saved = [];
  let city = 'CUR', cities = [{...profile.identity}], mutations = 0;
  const api = {utils: {getCityCode: () => city, getCities: () => cities, getPathfindingRules: () => rules},
    modifyPathfindingRules(value) { Object.assign(rules, value); mutations++; }, hooks: {}};
  for (const name of ['onCityLoad', 'onGameInit', 'onGameLoaded', 'onDemandChange', 'onGameEnd', 'onHourChange', 'onPauseChanged']) {
    hooks[name] = new Set();
    api.hooks[name] = callback => {hooks[name].add(callback); return () => hooks[name].delete(callback);};
  }
  const controller = create(api, profile, {notify: (...args) => messages.push(args), persist: value => saved.push(value)});
  return {api, controller, rules, defaults, messages, saved,
    fire(name) {for (const fn of [...hooks[name]]) fn();},
    city(value) {city = value;}, cities(value) {cities = value;},
    hookCount: () => Object.values(hooks).reduce((n, set) => n + set.size, 0),
    mutations: () => mutations};
}
test('default off, one switch activates and restores detached prior values', () => {
  const e = environment(); e.controller.start(); assert.equal(e.mutations(), 0);
  e.controller.setEnabled(true); assert.equal(e.controller.getState().active, true);
  assert.equal(e.rules.INCOME_MEAN, profile.rules.INCOME_MEAN);
  e.controller.setEnabled(false); assert.deepEqual(e.rules, e.defaults);
});
test('CUR -> MID -> CUR restores then reapplies, preserving preference', () => {
  const e = environment(); e.controller.start(); e.controller.setEnabled(true);
  e.city('MID'); e.fire('onCityLoad'); assert.deepEqual(e.rules, e.defaults);
  e.city('CUR'); e.fire('onCityLoad'); assert.equal(e.controller.getState().active, true);
  assert.equal(e.saved.at(-1).enabled, true);
});
test('same code with different owner stays inactive', () => {
  const e = environment(); e.cities([{...profile.identity, modId: 'another-mod'}]);
  e.controller.start(); e.controller.setEnabled(true); assert.equal(e.mutations(), 0);
});
test('duplicate CUR registrations stay inactive even if one owner matches', () => {
  const e = environment(); e.cities([profile.identity, {...profile.identity, modId: 'other'}]);
  e.controller.start(); e.controller.setEnabled(true); assert.equal(e.mutations(), 0);
});
test('missing source registration and changed map name remain inactive', () => {
  for (const cities of [[], [{...profile.identity, name: 'Different CUR'}]]) {
    const e = environment(); e.cities(cities); e.controller.start(); e.controller.setEnabled(true);
    assert.equal(e.controller.getState().active, false);
  }
});
test('another mod owns its changed key; our remaining keys restore', () => {
  const e = environment(); e.controller.start(); e.controller.setEnabled(true);
  e.rules.INCOME_MEAN = 888; e.fire('onHourChange');
  assert.equal(e.rules.INCOME_MEAN, 888); assert.equal(e.controller.getState().enabled, false);
  assert.equal(e.rules.PARKING_COST, e.defaults.PARKING_COST);
  assert.equal(e.rules.UNTARGETED, 123);
});
test('repeated load events and start calls never accumulate hooks or rebase snapshot', () => {
  const e = environment(); e.controller.start(); const count = e.hookCount(); e.controller.start();
  e.controller.setEnabled(true); e.fire('onGameLoaded'); e.fire('onDemandChange');
  assert.equal(e.hookCount(), count); e.controller.setEnabled(false); assert.deepEqual(e.rules, e.defaults);
});
test('exit restores values, future game event reapplies saved intent', () => {
  const e = environment(); e.controller.start(); e.controller.setEnabled(true); e.fire('onGameEnd');
  assert.deepEqual(e.rules, e.defaults); e.fire('onPauseChanged'); assert.deepEqual(e.rules, e.defaults);
  e.fire('onGameInit'); assert.equal(e.controller.getState().active, true);
});
test('dispose restores rules, removes hooks, and is idempotent', () => {
  const e = environment(); e.controller.start(); e.controller.setEnabled(true); e.controller.dispose();
  assert.deepEqual(e.rules, e.defaults); assert.equal(e.hookCount(), 0);
  e.controller.dispose(); e.controller.setEnabled(true); assert.deepEqual(e.rules, e.defaults);
});
test('invalid or non-finite profiles fail before any mutation', () => {
  for (const patch of [{schemaVersion: 99}, {mxnPerGameUnit: 0},
    {rules: {...profile.rules, PARKING_COST: Infinity}}, {rules: {...profile.rules, UNRELATED: 1}}]) {
    const e = environment(); assert.throws(() => create(e.api, {...profile, ...patch})); assert.equal(e.mutations(), 0);
  }
});
test('partial application rolls back applied keys', () => {
  const e = environment();
  e.api.modifyPathfindingRules = values => {e.rules.INCOME_MEAN = values.INCOME_MEAN;};
  e.controller.start(); e.controller.setEnabled(true);
  assert.equal(e.controller.getState().active, false); assert.deepEqual(e.rules, e.defaults);
});
test('controller never touches player fares or budgets', () => {
  const e = environment(); e.api.actions = new Proxy({}, {get() {throw new Error('fare/budget API touched');}});
  e.controller.start(); e.controller.setEnabled(true); e.controller.setEnabled(false);
  assert.deepEqual(e.rules, e.defaults);
});

async function bootstrapTests() {
  const bundle = fs.readFileSync(path.join(root, 'reports/cur-economic-pilot/index.js'), 'utf8');
  const tick = () => new Promise(resolve => setImmediate(resolve));
  function setup(version = '1.7.2', preference = {enabled: false, profileVersion: profile.profileVersion}) {
    const e = environment(), events = {}, components = new Map(), writes = [];
    e.api.storage = {scoped: () => ({get: async () => preference, set: async (key, value) => writes.push(value)})};
    e.api.ui = {registerComponent: (placement, component) => components.set(component.id, component),
      unregisterComponent: (placement, id) => components.delete(id), showNotification() {}};
    e.api.utils.React = {createElement() {}, useEffect() {}, useState: () => [0, () => {}]};
    const window = {SubwayBuilderAPI: e.api, electron: {getVersion: async () => version},
      addEventListener: (name, fn) => (events[name] ||= new Set()).add(fn),
      removeEventListener: (name, fn) => events[name]?.delete(fn)};
    const sandbox = vm.createContext({window, console: {error() {}, log() {}}, setTimeout, clearTimeout});
    return {e, window, sandbox, components, writes, events};
  }
  {
    const b = setup('1.8.0'); vm.runInContext(bundle, b.sandbox); await tick(); await tick();
    assert.equal(b.e.mutations(), 0); assert.match(b.window.__meszier83CurEconomicPilot.getState().versionError, /requires/);
    passed++; process.stdout.write('PASS unsupported version remains inactive\n');
  }
  {
    const b = setup('1.7.2', {enabled: true, profileVersion: 'old'});
    vm.runInContext(bundle, b.sandbox); await tick(); await tick();
    assert.equal(b.window.__meszier83CurEconomicPilot.getState().enabled, false);
    assert.equal(b.writes.at(-1).enabled, false);
    passed++; process.stdout.write('PASS incompatible preference resets off without extra user controls\n');
  }
  {
    const b = setup('1.7.2', {enabled: true, profileVersion: profile.profileVersion});
    vm.runInContext(bundle, b.sandbox); await tick(); await tick();
    assert.equal(b.window.__meszier83CurEconomicPilot.getState().active, true);
    for (const fn of [...b.events['modding-api-reload-request']]) fn();
    assert.deepEqual(b.e.rules, b.e.defaults); assert.equal(b.e.hookCount(), 0); assert.equal(b.components.size, 0);
    vm.runInContext(bundle, b.sandbox); await tick(); await tick();
    assert.equal(b.window.__meszier83CurEconomicPilot.getState().active, true);
    b.window.__meszier83CurEconomicPilot.dispose();
    passed++; process.stdout.write('PASS reload cleanup and reinstallation preserve restoration\n');
  }
  {
    const b = setup(); vm.runInContext(bundle, b.sandbox);
    b.window.__meszier83CurEconomicPilot.dispose(); await tick(); await tick();
    assert.equal(b.e.mutations(), 0); assert.equal(b.e.hookCount(), 0); assert.equal(b.components.size, 0);
    passed++; process.stdout.write('PASS disposal during async startup cannot reactivate\n');
  }
  process.stdout.write(`${passed} pilot tests passed\n`);
}
bootstrapTests().catch(error => {console.error(error); process.exitCode = 1;});
