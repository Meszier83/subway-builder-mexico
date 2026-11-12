/* Offline evidence harness. Executes the locally installed game's worker in a
 * fresh VM, without starting the game, modifying saves, or distributing its code. */
'use strict';
const fs = require('node:fs');
const vm = require('node:vm');
const crypto = require('node:crypto');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const source = fs.readFileSync(input.worker, 'utf8');
const context = vm.createContext({self: {}, console: {log(){}, info(){}, warn(){}, error(){}},
  performance, structuredClone, TextEncoder, TextDecoder, URL, setTimeout, clearTimeout});
vm.runInContext(source, context, {timeout: 15000});
const defaults = JSON.parse(vm.runInContext('JSON.stringify(PATHFINDING_RULES)', context));
function setRules(overrides) {
  context.overrides = structuredClone({...defaults, ...overrides});
  vm.runInContext('applyPathfindingRules(overrides)', context, {timeout: 1000});
}
function incomes(n, multiplier = 1) {
  return Array.from({length: n}, (_, i) => Math.max(context.overrides.MINIMUM_INCOME,
    Math.min(context.overrides.MAXIMUM_INCOME, context.getIncomeForPerson(i, n, multiplier))));
}
if (input.calibration) {
  const results = [];
  const originalIncome = context.getIncomeForPerson;
  for (const profile of input.profiles) {
    setRules(profile.rules);
    const distributions = {};
    for (const [kind, multiplier] of [['ordinary', 1], ['airport', defaults.AIRPORT_INCOME_MULTIPLIER],
      ['university', defaults.COLLEGE_INCOME_MULTIPLIER]]) {
      distributions[kind] = {};
      for (const n of [25, 50, 100, 200]) distributions[kind][n] = incomes(n, multiplier);
    }
    const modes = [];
    const parkingValues = profile.local_costs ? input.parking : [defaults.PARKING_COST * profile.mxn_per_game_unit];
    const drivingValues = profile.local_costs ? input.driving : [defaults.DRIVING_COST_PER_KM * profile.mxn_per_game_unit];
    for (const distance of [1000, 8000, 20000]) for (const minutes of [15, 30, 45, 60])
      for (const fare of [5, 10, 15, 20, 40, 80]) for (const parking of parkingValues)
        for (const driving of drivingValues) for (const kind of ['ordinary', 'airport', 'university'])
          for (const n of [25, 50, 100, 200]) {
            setRules({...profile.rules, PARKING_COST: parking / profile.mxn_per_game_unit,
              DRIVING_COST_PER_KM: driving / profile.mxn_per_game_unit});
            const args = {population: n, drivingTime: distance / (30 / 3.6), drivingDistance: distance,
              drivingTimeMultiplier: 1.25, transitTime: minutes * 60, transitCost: fare / profile.mxn_per_game_unit,
              walkTime: distance / 1.4, isAirportPop: kind === 'airport', isCollegePop: kind === 'university'};
            modes.push({distance_m: distance, perceived_transit_minutes: minutes, fare_mxn: fare,
              parking_mxn: parking, driving_mxn_per_km: driving, kind, population: n, ...context.getModeChoice(args)});
          }
    const uncertainty = [];
    if (profile.income_scenarios) {
      for (const [scenario, annualMxn] of Object.entries(profile.income_scenarios)) {
        // Diagnostic substitution inside this disposable VM only. This is not a
        // supported live per-pop income API or part of a distributable mod.
        context.getIncomeForPerson = i => annualMxn[i] === null ? profile.rules.MAXIMUM_INCOME
          : annualMxn[i] / profile.mxn_per_game_unit;
        for (const distance of [1000, 8000, 20000]) for (const minutes of [15, 30, 45, 60])
          for (const fare of [5, 10, 15, 20]) {
            setRules({...profile.rules, PARKING_COST: 20 / profile.mxn_per_game_unit,
              DRIVING_COST_PER_KM: input.central_driving / profile.mxn_per_game_unit});
            uncertainty.push({scenario, distance_m: distance, perceived_transit_minutes: minutes, fare_mxn: fare,
              population: 50, ...context.getModeChoice({population: 50, drivingTime: distance / (30 / 3.6),
                drivingDistance: distance, drivingTimeMultiplier: 1.25, transitTime: minutes * 60,
                transitCost: fare / profile.mxn_per_game_unit, walkTime: distance / 1.4})});
          }
        context.getIncomeForPerson = originalIncome;
      }
    }
    results.push({name: profile.name, distributions, modes, uncertainty});
  }
  setRules({});
  const budgetRules = JSON.parse(vm.runInContext('JSON.stringify({startingMoney:RULES.STARTING_MONEY,construction:RULES.CONSTRUCTION_COSTS,trainTypes:typeof TRAIN_TYPES!=="undefined"?TRAIN_TYPES:null})', context));
  process.stdout.write(JSON.stringify({defaults, results, budgetRules,
    worker_sha256: crypto.createHash('sha256').update(source).digest('hex')}));
  return;
}
const profiles = input.profiles || [];
const results = [];
for (const profile of [{name: 'unmodified', rules: {}}, ...profiles]) {
  setRules(profile.rules);
  const distributions = {};
  for (const n of [25, 50, 100, 200, 10000]) distributions[n] = incomes(n);
  const modes = [];
  // Fixed thought-experiment commutes, not a forecast of CUR network ridership.
  for (const distance of [1000, 8000, 20000]) {
    for (const parkingMxn of [0, 20, 60]) {
      for (const drivingMxnPerKm of [1.5, 3, 5]) {
        for (const minutes of [15, 30, 45, 60]) {
          for (const fareMxn of [5, 10, 20, 40, 80]) {
            const units = profile.mxn_per_game_unit || 1;
            // Leave all defaults intact for the unmodified baseline.
            setRules(profile.name === 'unmodified' ? {} : {...profile.rules,
              PARKING_COST: parkingMxn / units, DRIVING_COST_PER_KM: drivingMxnPerKm / units});
            const args = {population: 50, drivingTime: distance / (30 / 3.6),
              drivingDistance: distance, drivingTimeMultiplier: 1.25,
              transitTime: minutes * 60, transitCost: fareMxn / units,
              walkTime: distance / 1.4};
            modes.push({distance_m: distance, parking_mxn: parkingMxn,
              driving_mxn_per_km: drivingMxnPerKm, perceived_transit_minutes: minutes,
              fare_mxn: fareMxn, ...context.getModeChoice(args)});
          }
        }
      }
    }
  }
  setRules(profile.rules);
  const before = JSON.stringify(vm.runInContext('PATHFINDING_RULES', context));
  setRules({});
  const restored = JSON.stringify(vm.runInContext('PATHFINDING_RULES', context)) === JSON.stringify(defaults);
  setRules(profile.rules);
  const reapplied = before === JSON.stringify(vm.runInContext('PATHFINDING_RULES', context));
  results.push({name: profile.name, distributions, modes, offline_restore: restored, offline_reapply: reapplied});
}
// Exercise the actual worker message/batch path with an empty network and no
// commutes. This verifies receipt/merge, not route finding or live UI behavior.
(async () => {
  const batchProfile = profiles[0]?.rules || {INCOME_MEAN: 123456};
  await context.processPopCommuteBatch({network: {}, popCommutes: [], pops: [], demandPoints: [],
    elapsedSeconds: 0, transitCost: 1, featureFlags: {}, pathfindingRules: batchProfile});
  const received = JSON.parse(vm.runInContext('JSON.stringify(PATHFINDING_RULES)', context));
  const batchPropagation = Object.entries(batchProfile).every(([key, value]) => received[key] === value);
  setRules({});
  process.stdout.write(JSON.stringify({worker_sha256: crypto.createHash('sha256').update(source).digest('hex'),
    defaults, empty_batch_rules_propagation: batchPropagation, results}));
})().catch(error => {console.error(error); process.exitCode = 1;});
