/* Installed-code integration fixture. This constructs a tiny timetable in RAM;
 * it does not fabricate or write a .metro save or mutate the game installation. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const create = require('../mods/cur-economic-profile/controller.js');
const metadata = JSON.parse(fs.readFileSync(path.join(root, 'reports/cur-economic-pilot/calibration.json')));
const profile = metadata.profile;
const workerPath = path.join(root, '.cache/cur-economics', path.basename(metadata.game.worker_member));
const context = vm.createContext({self: {}, console, performance, structuredClone, TextEncoder, TextDecoder,
  URL, setTimeout, clearTimeout});
vm.runInContext(fs.readFileSync(workerPath, 'utf8'), context, {timeout: 15000});
const rules = vm.runInContext('PATHFINDING_RULES', context);
const defaults = structuredClone(rules);
const stops = [{stationId: 'A', stationCoords: [-86.90, 21.10]},
  {stationId: 'B', stationCoords: [-86.82, 21.10]}];
const network = {stopCount: 2, stops, stopIndexById: new Map([['A', 0], ['B', 1]]),
  routes: [{routeId: 'fixture-out'}, {routeId: 'fixture-return'}],
  routeStopOffsets: new Int32Array([0, 2, 4]), routeStops: new Int32Array([0, 1, 1, 0]),
  stopRouteOffsets: new Int32Array([0, 2, 4]), stopRoutePairs: new Int32Array([0, 0, 1, 1, 0, 1, 1, 0]),
  tripCounts: new Int32Array([1, 1]), tripTimeOffsets: new Int32Array([0, 2]),
  tripDep: new Float64Array([28860, 31500, 61260, 63900]),
  tripArr: new Float64Array([28860, 31500, 61260, 63900]),
  transferOffsets: new Int32Array([0, 0, 0]), transferTargets: new Int32Array([]), transferTimes: new Float64Array([])};
const hooks = {};
const api = {utils: {getPathfindingRules: () => rules, getCityCode: () => 'CUR', getCities: () => [profile.identity]},
  hooks, modifyPathfindingRules(overrides) {context.applyPathfindingRules(overrides);}};
for (const name of ['onCityLoad', 'onGameInit', 'onGameLoaded', 'onDemandChange', 'onGameEnd']) hooks[name] = () => () => {};
const controller = create(api, profile);
controller.start();
async function batch(direction) {
  const input = {network, popCommutes: [{popId: 'fixture-pop', direction}],
    pops: [{id: 'fixture-pop', residenceId: 'home', jobId: 'job', size: 50,
      drivingSeconds: 960, drivingDistance: 8000, homeDepartureTime: 28800, workDepartureTime: 61200}],
    demandPoints: [{id: 'home', location: stops[0].stationCoords}, {id: 'job', location: stops[1].stationCoords}],
    elapsedSeconds: direction === 'homeToWork' ? 28800 : 61200, transitCost: 3,
    featureFlags: {driveToStationAccess: false}, pathfindingRules: structuredClone(rules)};
  const response = await context.processPopCommuteBatch(input);
  assert.equal(response.processedPops.length, 1);
  const pop = response.processedPops[0].updatedPop;
  assert.ok(pop.lastCommute.transitPaths.length > 0, 'Installed RAPTOR must find an actual train journey');
  assert.ok(pop.lastCommute.transitPaths[0].segments.some(s => !s.isWalking), 'Journey must contain a train');
  assert.equal(pop.commutes[direction].transitCost, 3, 'Player fare is preserved');
  const choice = pop.lastCommute.modeChoice;
  assert.equal(choice.transit + choice.driving + choice.walking, 50);
  return {choice: JSON.parse(JSON.stringify(choice)), transitTime: pop.commutes[direction].transitTime};
}
(async () => {
  const baseline = {out: await batch('homeToWork'), back: await batch('workToHome')};
  controller.setEnabled(true);
  assert.equal(controller.getState().active, true);
  const enabled = {out: await batch('homeToWork'), back: await batch('workToHome')};
  assert.equal(rules.INCOME_MEAN, profile.rules.INCOME_MEAN);
  assert.notDeepEqual(enabled.out.choice, baseline.out.choice, 'Profile must affect a real computed commute');
  controller.setEnabled(false);
  assert.deepEqual(structuredClone(rules), defaults);
  const restored = {out: await batch('homeToWork'), back: await batch('workToHome')};
  assert.deepEqual(restored, baseline);
  console.log(JSON.stringify({status: 'PASS', scope: 'Installed worker + actual RAPTOR + runtime controller, offline only',
    directional_batches: 6, baseline, enabled, restored}, null, 2));
})().catch(error => {console.error(error); process.exitCode = 1;});
