'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const source = path.join(root, 'mods/sandbox-balance-support');
const controllerCode = fs.readFileSync(path.join(source, 'controller.js'), 'utf8');
const bundle = controllerCode + '\n' + fs.readFileSync(path.join(source, 'bootstrap.js'), 'utf8');
const {oracle} = JSON.parse(fs.readFileSync(0, 'utf8'));
const keys = ['trainOperational', 'trackMaintenance', 'stationMaintenance', 'gradeCrossingMaintenance',
  'other', 'construction', 'trainPurchase', 'bondPayments'];
const categories = () => Object.fromEntries(keys.map(key => [key, 0]));
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
let passed = 0, dashboardCases = 0;
const names = [];
function check(name, fn) { fn(); passed++; names.push(name); }
async function asyncCheck(name, fn) { await fn(); passed++; names.push(name); }
function freeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze); Object.freeze(value);
  }
  return value;
}
function entry(timestamp, revenue = 0, expenses = 0, extra = {}) {
  return {timestamp, balance: 1000000, hourlyRevenue: revenue, hourlyExpenses: expenses,
    expenseCategories: {...categories(), trainOperational: expenses}, ...extra};
}
function state(overrides = {}) {
  return {cityCode: 'TEST', gameMode: 'sandbox', saveCheckPending: false, challengeData: null,
    money: 1000000, timeConfig: {elapsedSeconds: 1800, paused: true},
    financialHistory: {entries: [], lastHourTimestamp: 0, currentHourRevenue: 80,
      currentHourExpenses: 100, currentHourExpenseCategories: {...categories(), trainOperational: 100}},
    routeFinancials: {currentHour: {a: {revenue: 80, expenses: 100}}, byRoute: {}, lastHourTimestamp: 0},
    transitCost: 3, fareGroups: [{id: 'custom', flatFare: 4}], routes: [{id: 'a'}],
    trains: new Map([['train', {routeId: 'a'}]]), completedCommutes: [{size: 50}],
    lifetimeStatistics: {revenueEarned: 80}, ...overrides};
}
function store(initial) {
  let current = initial;
  const listeners = new Set(), writes = [];
  const result = {
    getState: () => current,
    setState(patch) {
      const previous = current;
      const value = typeof patch === 'function' ? patch(current) : patch;
      writes.push(value); current = {...current, ...value};
      [...listeners].forEach(fn => fn(current, previous));
    },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    writes, count: () => listeners.size
  };
  return result;
}
function controller(initial, options = {}) {
  const sandbox = vm.createContext({});
  vm.runInContext(controllerCode, sandbox);
  const s = store(initial), errors = [];
  const c = sandbox.createSandboxBalanceSupport(s, {onError: error => errors.push(error), ...options});
  return {s, c, errors};
}

// Execute the inspected game's actual memoized dashboard calculations. This is
// intentionally independent of the minimod's category sums/window calculations.
function dashboard(s, frame) {
  const empty = categories;
  const sandbox = {
    useTranslation: () => ({t: text => text}), useMoney: () => s.money,
    useFinancialHistory: () => s.financialHistory, useMainStore: selector => selector(s),
    reactExports: {useState: () => [frame, () => {}], useMemo: fn => fn()},
    createEmptyExpenseCategories: empty, getExpenseCategoryKeys: () => keys,
    EXPENSE_CATEGORIES: Object.fromEntries(keys.map(key => [key, {translationKey: key}])),
    formatClockTime: timestamp => ({hours: Math.floor(timestamp / 3600) % 24, minutes: 0})
  };
  return vm.runInNewContext('(' + oracle + ')', sandbox)({open: true});
}
function green(s) {
  for (const frame of ['hourly', '48hours', 'daily', 'all-time']) {
    const data = dashboard(s, frame);
    assert.ok(data.totals.profit >= 1, `${frame} profit ${data.totals.profit}`);
    assert.ok(data.last24HoursTotals.profit >= 1, `header profit ${data.last24HoursTotals.profit}`);
    assert.ok(data.last24HoursTotals.revenue > 0);
  }
  dashboardCases++;
}
function unchangedLines(before, after) {
  for (const key of Object.keys(before).filter(key => !['money', 'financialHistory'].includes(key))) {
    assert.equal(after[key], before[key], `Unexpected mutation: ${key}`);
  }
}

function bootstrap(options = {}) {
  const s = store(options.initial || state());
  const events = new Map(), hooks = new Map(), errors = [], notices = [];
  const window = {
    location: {href: options.href || 'file:///game/dist/renderer/index.html#/game?city=TEST'},
    addEventListener(name, fn) { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); },
    removeEventListener(name, fn) { events.get(name)?.delete(fn); },
    electron: {getVersion: () => options.versionPromise || Promise.resolve(options.version || '1.7.2')},
    SubwayBuilderAPI: {
      tutorial: {isActive: () => !!options.tutorial},
      ui: {showNotification: message => notices.push(message)},
      hooks: Object.fromEntries(['onGameInit', 'onGameLoaded', 'onCityLoad', 'onGameEnd'].map(name => [name, fn => {
        if (!hooks.has(name)) hooks.set(name, new Set()); hooks.get(name).add(fn);
        return () => hooks.get(name).delete(fn);
      }]))
    }
  };
  const context = vm.createContext({window, URL, document: {querySelectorAll: () => [
    {src: options.script || 'file:///game/dist/renderer/public/index-CuszHwSj.js'}]},
    console: {error: (...args) => errors.push(args)}});
  let imports = 0;
  const execute = () => vm.runInContext(bundle, context, {importModuleDynamically: async url => {
    imports++;
    assert.equal(url, 'file:///game/dist/renderer/public/index-CuszHwSj.js');
    if (options.importError) throw new Error('Import unavailable');
    if (options.importWait) await options.importWait;
    const module = new vm.SyntheticModule(['aJ', 'bR'], function () {
      this.setExport('aJ', options.badStore ? {} : s);
      this.setExport('bR', options.badCategories ? {} : categories());
    }, {context});
    await module.link(() => {}); await module.evaluate(); return module;
  }});
  const fire = name => [...(hooks.get(name) || [])].forEach(fn => fn());
  const event = name => [...(events.get(name) || [])].forEach(fn => fn());
  return {s, window, context, execute, fire, event, errors, notices, imports: () => imports,
    counts: () => [...hooks.values(), ...events.values()].reduce((n, set) => n + set.size, 0)};
}

(async () => {
  check('80 revenue / 100 operating expenses receives exactly 21, with only global writes', () => {
    const initial = state(); freeze(initial); const {s, c} = controller(initial); c.start();
    assert.equal(s.getState().money, initial.money + 21);
    assert.equal(s.getState().financialHistory.currentHourRevenue, 101);
    assert.equal(s.writes.length, 1); assert.deepEqual(Object.keys(s.writes[0]).sort(), ['financialHistory', 'money']);
    unchangedLines(initial, s.getState()); assert.equal(initial.routeFinancials.currentHour.a.revenue -
      initial.routeFinancials.currentHour.a.expenses, -20); green(s.getState());
  });
  check('profitable networks and unrelated state changes receive no grants', () => {
    const initial = state(); initial.financialHistory.currentHourRevenue = 200;
    const {s, c} = controller(initial); c.start(); s.setState({camera: {zoom: 12}});
    assert.equal(s.writes.length, 1); assert.equal(c.getState().injections, 0); green(s.getState());
  });
  check('normal, career, challenge, loading, empty-city and inactive screens cannot be subsidized', () => {
    for (const patch of [{gameMode: 'normal'}, {gameMode: 'career'}, {challengeData: {}},
      {saveCheckPending: true}, {saveCheckPending: undefined}, {cityCode: ''}]) {
      const {s, c} = controller(state(patch)); c.start(); assert.equal(s.writes.length, 0);
    }
    const {s, c} = controller(state(), {isActive: () => false}); c.start(); assert.equal(s.writes.length, 0);
  });
  check('no recursion or duplicate grants when the subsidy changes financial state', () => {
    const {s, c} = controller(state()); c.start(); c.start();
    assert.equal(s.count(), 1); assert.equal(c.getState().injections, 1);
    s.setState({financialHistory: {...s.getState().financialHistory}});
    assert.equal(c.getState().injections, 1);
    const h = s.getState().financialHistory;
    s.setState({financialHistory: {...h, currentHourExpenses: 105,
      currentHourExpenseCategories: {...h.currentHourExpenseCategories, trainOperational: 105}}});
    assert.equal(c.getState().injections, 2); assert.equal(c.getState().totalInjected, 26); green(s.getState());
  });
  check('each distinct header/25/49-record window and old all-time losses is covered', () => {
    for (const lossIndex of [0, 10, 49, 50, 51, 70]) {
      const entries = Array.from({length: 75}, (_, i) => entry(i * 3600, i === lossIndex ? 0 : 20,
        i === lossIndex ? 5000 : 0));
      const initial = state({timeConfig: {elapsedSeconds: 75 * 3600 + 30}});
      initial.financialHistory = {entries, lastHourTimestamp: 75 * 3600, currentHourRevenue: 2,
        currentHourExpenses: 0, currentHourExpenseCategories: categories()};
      freeze(initial); const {s, c} = controller(initial); c.start(); green(s.getState());
      assert.equal(s.getState().financialHistory.entries, entries); unchangedLines(initial, s.getState());
    }
  });
  check('14-day groups follow the first record offset, with non-operating costs excluded', () => {
    const entries = Array.from({length: 24 * 17}, (_, i) => entry(1800 + i * 3600, 50,
      i === 24 * 4 ? 90000 : 5));
    const initial = state({timeConfig: {elapsedSeconds: 24 * 17 * 3600 + 3500}});
    initial.financialHistory = {entries, lastHourTimestamp: 24 * 17 * 3600, currentHourRevenue: 5,
      currentHourExpenses: 100000000, currentHourExpenseCategories: {...categories(), construction: 100000000}};
    freeze(initial); const {s, c} = controller(initial); c.start(); green(s.getState());
    assert.equal(s.getState().financialHistory.currentHourExpenses, 100000000);
    assert.equal(s.getState().financialHistory.currentHourExpenseCategories.construction, 100000000);
    assert.equal(s.getState().financialHistory.entries, entries);
  });
  check('an initially hidden empty current bucket is included when calculating the new grant', () => {
    const initial = state({timeConfig: {elapsedSeconds: 24 * 3600 + 20}});
    initial.financialHistory = {entries: Array.from({length: 24}, (_, i) => entry(i * 3600, i === 0 ? 0 : 10,
      i === 0 ? 1000 : 0)), lastHourTimestamp: 24 * 3600, currentHourRevenue: 0,
      currentHourExpenses: 0, currentHourExpenseCategories: categories()};
    const {s, c} = controller(initial); c.start(); green(s.getState());
  });
  check('empty sandbox receives one dollar, then idle ticks cannot repeat it', () => {
    const initial = state(); Object.assign(initial.financialHistory, {currentHourRevenue: 0,
      currentHourExpenses: 0, currentHourExpenseCategories: categories()});
    const {s, c} = controller(initial); c.start();
    for (let i = 0; i < 10; i++) s.setState({timeConfig: {elapsedSeconds: 1801 + i}});
    assert.equal(c.getState().totalInjected, 1); green(s.getState());
  });
  check('fractional costs and sandbox MAX_SAFE_INTEGER cash remain finite', () => {
    const initial = state({money: Number.MAX_SAFE_INTEGER});
    initial.financialHistory.currentHourRevenue = 99.999999999;
    const {s, c} = controller(initial); c.start(); green(s.getState());
    assert.equal(c.getState().totalInjected, 2); assert.ok(Number.isFinite(s.getState().money));
  });
  check('hour rollover preserves the old bucket and credits only the new hour', () => {
    const initial = state({timeConfig: {elapsedSeconds: 7201}}); freeze(initial);
    const {s, c} = controller(initial); c.start(); const h = s.getState().financialHistory;
    assert.equal(h.lastHourTimestamp, 7200); assert.equal(h.entries.length, 1);
    assert.equal(h.entries[0].hourlyRevenue, 80); assert.equal(h.entries[0].hourlyExpenses, 100);
    assert.equal(h.entries[0].balance, initial.money); assert.equal(h.currentHourRevenue, 21);
    unchangedLines(initial, s.getState()); green(s.getState());
  });
  check('hour rollover never appends a duplicate closed timestamp', () => {
    const initial = state({timeConfig: {elapsedSeconds: 7201}});
    initial.financialHistory.entries = [entry(0, 80, 100)];
    const {s, c} = controller(initial); c.start();
    assert.equal(s.getState().financialHistory.entries.length, 1); green(s.getState());
  });
  check('new sandbox becomes eligible after loading; mode reversal stops injections', () => {
    const {s, c} = controller(state({saveCheckPending: true})); c.start();
    assert.equal(c.getState().injections, 0); s.setState({saveCheckPending: false});
    assert.equal(c.getState().injections, 1); s.setState({gameMode: 'normal'});
    s.setState({financialHistory: {...s.getState().financialHistory, currentHourRevenue: 0}});
    assert.equal(c.getState().injections, 1);
  });
  check('bad financial data fails once, detaches, and does not mutate the save', () => {
    for (const bad of [NaN, Infinity, undefined]) {
      const initial = state(); initial.financialHistory.currentHourRevenue = bad;
      const {s, c, errors} = controller(initial); c.start(); c.start();
      assert.equal(s.writes.length, 0); assert.equal(s.count(), 0); assert.equal(errors.length, 1);
      assert.equal(c.getState().failed, true);
    }
    const initial = state(); initial.timeConfig.elapsedSeconds = -1;
    const {s, c} = controller(initial); c.start(); assert.equal(s.writes.length, 0);
  });
  check('stop/restart/dispose detach subscriptions and never reverse credited money', () => {
    const {s, c} = controller(state()); c.start(); const paid = s.getState(); c.stop();
    assert.equal(s.count(), 0); c.start(); assert.equal(s.count(), 1);
    assert.equal(c.getState().injections, 1); c.dispose(); c.start();
    assert.equal(s.count(), 0); assert.equal(s.getState(), paid);
  });
  check('serialized money/history round trip keeps the grant without another payment', () => {
    const {s, c} = controller(state()); c.start(); const saved = clone(s.getState()); c.dispose();
    const reloaded = controller(saved); reloaded.c.start();
    assert.equal(reloaded.s.writes.length, 0); green(reloaded.s.getState());
    assert.deepEqual(reloaded.s.getState().routeFinancials, saved.routeFinancials);
  });
  check('60 deterministic adversarial histories match every native dashboard calculation', () => {
    let seed = 94731;
    const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
    for (let trial = 0; trial < 60; trial++) {
      const count = 1 + Math.floor(random() * 900), offset = trial % 2 ? 1800 : 0;
      const entries = Array.from({length: count}, (_, i) => {
        const costs = categories(); keys.forEach(key => { costs[key] = Math.floor(random() * 1000); });
        return entry(offset + i * 3600, Math.floor(random() * 4000), Object.values(costs).reduce((a, b) => a + b),
          {expenseCategories: costs});
      });
      const timestamp = Math.ceil((offset + count * 3600) / 3600) * 3600;
      const initial = state({timeConfig: {elapsedSeconds: timestamp + 30}});
      const costs = categories(); keys.forEach(key => { costs[key] = Math.floor(random() * 1000); });
      initial.financialHistory = {entries, lastHourTimestamp: timestamp,
        currentHourRevenue: Math.floor(random() * 4000), currentHourExpenses: Object.values(costs).reduce((a, b) => a + b),
        currentHourExpenseCategories: costs};
      freeze(initial); const {s, c} = controller(initial); c.start();
      assert.equal(c.getState().failed, false); green(s.getState()); unchangedLines(initial, s.getState());
      assert.equal(s.getState().financialHistory.entries, entries); c.dispose();
    }
  });
  check('60 fractional histories preserve the native header and daily rounding order', () => {
    let seed = 87199;
    const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
    for (let trial = 0; trial < 60; trial++) {
      const count = 25 + Math.floor(random() * 450);
      const entries = Array.from({length: count}, (_, i) => {
        const costs = categories(); keys.forEach(key => { costs[key] = Math.floor(random() * 100000) / 100; });
        return entry(i * 3600, Math.floor(random() * 100000) / 100,
          Object.values(costs).reduce((a, b) => a + b), {expenseCategories: costs});
      });
      const initial = state({timeConfig: {elapsedSeconds: count * 3600 + 10}});
      initial.financialHistory = {entries, lastHourTimestamp: count * 3600,
        currentHourRevenue: .1, currentHourExpenses: .2,
        currentHourExpenseCategories: {...categories(), trainOperational: .2}};
      freeze(initial); const {s, c} = controller(initial); c.start();
      assert.equal(c.getState().failed, false); green(s.getState()); unchangedLines(initial, s.getState()); c.dispose();
    }
  });

  await asyncCheck('packaged bootstrap imports the loaded module and cleans up on reload', async () => {
    const b = bootstrap(); b.execute(); await tick(); await tick();
    assert.equal(b.imports(), 1); assert.equal(b.s.count(), 1); green(b.s.getState());
    b.event('modding-api-reload-request'); assert.equal(b.s.count(), 0); assert.equal(b.counts(), 0);
    b.execute(); await tick(); await tick();
    assert.equal(b.s.count(), 1); assert.equal(b.window.__meszier83SandboxBalanceSupport.getState().injections, 0);
    b.window.__meszier83SandboxBalanceSupport.dispose();
  });
  await asyncCheck('game exit stops the store subscription; a later game restarts it', async () => {
    const b = bootstrap(); b.execute(); await tick(); await tick(); b.fire('onGameEnd');
    assert.equal(b.s.count(), 0); b.s.setState({financialHistory: {...b.s.getState().financialHistory, currentHourRevenue: 0}});
    const cash = b.s.getState().money; assert.equal(b.s.getState().money, cash);
    b.fire('onGameInit'); assert.equal(b.s.count(), 1); assert.ok(b.s.getState().money > cash); green(b.s.getState());
    b.window.__meszier83SandboxBalanceSupport.dispose(); assert.equal(b.counts(), 0);
  });
  await asyncCheck('unsupported game version or bundle cannot import or grant money', async () => {
    for (const options of [{version: '1.7.3'}, {script: 'file:///game/public/index-other.js'}]) {
      const b = bootstrap(options); b.execute(); await tick(); await tick();
      assert.equal(b.imports(), 0); assert.equal(b.s.writes.length, 0); assert.equal(b.notices.length, 1);
      b.window.__meszier83SandboxBalanceSupport.dispose(); assert.equal(b.counts(), 0);
    }
  });
  await asyncCheck('missing exports or failed module import leave the mod inactive', async () => {
    for (const options of [{badStore: true}, {badCategories: true}, {importError: true}]) {
      const b = bootstrap(options); b.execute(); await tick(); await tick();
      assert.equal(b.s.writes.length, 0); assert.equal(b.s.count(), 0); assert.equal(b.notices.length, 1);
      b.window.__meszier83SandboxBalanceSupport.dispose();
    }
  });
  await asyncCheck('tutorial and menu screens receive no grants; game entry starts normally', async () => {
    for (const options of [{tutorial: true}, {href: 'file:///game/index.html#/'}]) {
      const b = bootstrap(options); b.execute(); await tick(); await tick();
      assert.equal(b.s.writes.length, 0); b.window.__meszier83SandboxBalanceSupport.dispose();
    }
    const b = bootstrap({href: 'file:///game/index.html#/'}); b.execute(); await tick(); await tick();
    b.window.location.href = 'file:///game/index.html#/game'; b.fire('onGameInit');
    green(b.s.getState()); b.window.__meszier83SandboxBalanceSupport.dispose();
  });
  await asyncCheck('dispose during asynchronous version lookup cannot reactivate', async () => {
    let resolve; const versionPromise = new Promise(fn => { resolve = fn; });
    const b = bootstrap({versionPromise}); b.execute(); b.event('modding-api-reload-request');
    resolve('1.7.2'); await tick(); await tick();
    assert.equal(b.s.writes.length, 0); assert.equal(b.imports(), 0); assert.equal(b.counts(), 0);
  });
  await asyncCheck('dispose or game exit during asynchronous import cannot subscribe later', async () => {
    for (const end of ['dispose', 'exit']) {
      let resolve; const importWait = new Promise(fn => { resolve = fn; });
      const b = bootstrap({importWait}); b.execute(); await tick();
      if (end === 'dispose') b.event('beforeunload'); else b.fire('onGameEnd');
      resolve(); await tick(); await tick(); assert.equal(b.s.count(), 0); assert.equal(b.s.writes.length, 0);
      if (end === 'exit') b.window.__meszier83SandboxBalanceSupport.dispose();
      assert.equal(b.counts(), 0);
    }
  });
  process.stdout.write(JSON.stringify({passed, dashboard_cases: dashboardCases, cases: names}));
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
