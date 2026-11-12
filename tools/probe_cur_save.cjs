/* Private, template-derived save experiment. Not a general network generator.
 * Reads the installed simulation worker; never modifies or distributes game code.
 * Run: node tools/probe_cur_save.cjs
 */
'use strict';
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const crypto = require('node:crypto');
const vm = require('node:vm');
const sourcePath = 'C:/Users/Keppl/Downloads/city-code-changer-main/city-code-changer-main/3_05419459f7e64ecbba3d331322b95da3.metro';
const outputDir = path.resolve('reports/cur-save-probe');
const asar = 'C:/Program Files (x86)/Steam/steamapps/common/Subway Builder/resources/app.asar';
const sha = b => crypto.createHash('sha256').update(b).digest('hex');
function assert(ok, message) { if (!ok) throw Error(message); }
function member(name) {
  const fd = fs.openSync(asar, 'r');
  try {
    const prefix = Buffer.alloc(16); fs.readSync(fd, prefix, 0, 16, 0);
    const header = Buffer.alloc(prefix.readUInt32LE(12)); fs.readSync(fd, header, 0, header.length, 16);
    let entry = JSON.parse(header); for (const part of name.split('/')) entry = entry.files[part];
    assert(!entry.unpacked, 'Unexpected unpacked member');
    const bytes = Buffer.alloc(entry.size);
    assert(fs.readSync(fd, bytes, 0, bytes.length, 8 + prefix.readUInt32LE(4) + Number(entry.offset)) === bytes.length, 'Short ASAR read');
    return bytes;
  } finally { fs.closeSync(fd); }
}
const version = JSON.parse(member('package.json')).version;
assert(version === '1.7.2', 'Review this probe for the installed game version first');
const original = fs.readFileSync(sourcePath);
assert(sha(original) === 'a5e703d9f4d28918810e51f8fd226527bb632cc3ac29002eab2577d031f6d714', 'Source save changed');
const offset = original.readUInt32LE(24), length = original.readUInt32LE(28);
const save = JSON.parse(zlib.gunzipSync(original.subarray(offset, offset + length))).mainSave;
assert(save.cityUid === 'com.railyard.maploader:CUR', 'Unexpected city identity');
const d = save.data;
const originalRoute = d.routes.find(r => r.bullet === '5');
const outbound = structuredClone(originalRoute.stCombos[6]);
const nodeById = new Map(d.stNodes.map(n => [n.id, n]));
const first = nodeById.get(outbound.startStNodeId), second = nodeById.get(outbound.endStNodeId);
const stationIds = new Set(d.stations.filter(s => s.stNodeIds.includes(first.id) || s.stNodeIds.includes(second.id)).map(s => s.id));
assert(stationIds.size === 2, 'Expected two stations');
const pathTracks = new Set(outbound.path.map(p => p.trackId));
d.trackGroups = d.trackGroups.filter(g => g.trackIds.some(id => pathTracks.has(id)));
assert(d.trackGroups.filter(g => g.type === 'station').length === 2, 'Path crosses extra stations');
const retainedTracks = new Set(d.trackGroups.flatMap(g => g.trackIds));
d.tracks = d.tracks.filter(t => retainedTracks.has(t.id));
d.stations = d.stations.filter(s => stationIds.has(s.id));
const retainedNodes = new Set(d.stations.flatMap(s => s.stNodeIds));
d.stNodes = d.stNodes.filter(n => retainedNodes.has(n.id));
d.signals = d.signals.filter(s => s.signalTracks.length && s.signalTracks.every(t => retainedTracks.has(t.trackId)));
for (const signal of d.signals) signal.status = {occupations: [], reservedBy: null};
const signalIds = new Set(d.signals.map(s => s.id));
for (const segment of outbound.path) segment.signals = segment.signals.filter(s => signalIds.has(s.signalId));
const inbound = {startStNodeId: second.id, endStNodeId: first.id, distance: outbound.distance,
  path: outbound.path.slice().reverse().map(p => ({...structuredClone(p), reversed: !p.reversed}))};
const routeId = crypto.randomUUID();
const rideTime = originalRoute.stComboTimings[7].arrivalTime - originalRoute.stComboTimings[6].departureTime;
assert(rideTime > 0, 'Invalid source travel time');
const dwell = 35;
const times = [0, dwell + rideTime, 2 * (dwell + rideTime)];
const route = {...originalRoute, id: routeId, bullet: 'P', fullName: 'CUR two-station save probe',
  stNodes: [first, second, first].map(structuredCloneSafe), stCombos: [outbound, inbound],
  stComboTimings: [first, second, first].map((n, i) => ({stNodeId: n.id, stNodeIndex: i, arrivalTime: times[i], departureTime: times[i] + dwell})),
  terminusPlatformAlternates: [], disruption: null, tempParentId: null, idealTrainCount: 1,
  trainSchedule: {highDemand: 1, mediumDemand: 1, lowDemand: 1, veryLowDemand: 1}};
function structuredCloneSafe(value) { return structuredClone(value); }
d.routes = [route];
for (const station of d.stations) { station.routeIds = [routeId]; station.nearbyStations = []; }
d.stationGroups = d.stationGroups.filter(g => g.stationIds.some(id => stationIds.has(id))).map(g => ({...g, stationIds: g.stationIds.filter(id => stationIds.has(id))}));
const startTime = 8 * 3600;
const worker = member('dist/renderer/public/simEngine.worker-DbZeIGI4.js');
const context = vm.createContext({self: {}, console: {log(){}, info(){}, warn(){}, error(){}}, performance,
  structuredClone, TextEncoder, TextDecoder, URL, setTimeout, clearTimeout, crypto: crypto.webcrypto});
vm.runInContext(worker.toString('utf8'), context, {timeout: 15000});
context.probeRoute = route; context.probeTracks = d.tracks; context.probeTrainId = crypto.randomUUID(); context.probeStart = startTime;
const train = JSON.parse(vm.runInContext(`JSON.stringify(generateTrain({route: probeRoute,
  tracksMap: new Map(probeTracks.map(t => [t.id,t])), stComboIndex: 0,
  coordsToShow: {warning:false,warningExtra:false}, elapsedSeconds:probeStart,id:probeTrainId}))`, context, {timeout: 10000}));
d.trains = [train];
d.timeConfig = {paused: true, timeSpeed: 'normal', elapsedSeconds: startTime};
d.elapsedSeconds = startTime; d.playTimeSeconds = 0; d.totalLifetimeRidership = 0;
delete d.compressedDemandData;
d.ownedCarsByType = Object.fromEntries(Object.keys(d.ownedCarsByType).map(k => [k, k === route.trainType ? route.carsPerTrain : 0]));
d.ownedTrainCount = route.carsPerTrain;
d.fareGroups = [{...d.fareGroups[0], id: crypto.randomUUID(), routeIds: [routeId], routeFares: {}}];
d.financialHistory = {entries: [], lastHourTimestamp: startTime, currentHourRevenue: 0, currentHourExpenses: 0,
  currentHourExpenseCategories: Object.fromEntries(Object.keys(d.financialHistory.currentHourExpenseCategories).map(k => [k, 0]))};
d.routeFinancials = {byRoute: {}, lastHourTimestamp: startTime, currentHour: {}};
d.dailyStats = Object.fromEntries(Object.entries(d.dailyStats).map(([k,v]) => [k, Array.isArray(v) ? [] : typeof v === 'number' ? 0 : null]));
d.dailyStats.lastSampleAt = startTime;
for (const key of ['bonds','annotations','savedBlueprints','newspaperArticles','demolishedOsmIds']) d[key] = [];
d.trackEditSession = null;
for (const key of ['stationsDemolishedAllTime','buildingDemolitionSpendAllTime','routesDeletedAllTime']) d[key] = 0;
d.firstTransferMadeAt = null; d.everDemolishedBuilding = false; d.hasGoneBankrupt = false; d.rockefellerPaidOut = false;
save.id = crypto.randomUUID(); save.gameSessionId = crypto.randomUUID(); save.name = 'CUR isolated two-station probe'; save.timestamp = Date.now();
save.metadata = {...save.metadata, stations: 2, routes: 1, trains: 1, money: d.money, elapsedSeconds: startTime};
delete save.timelapse; delete save.routeThumbnail;
save.viewport = {longitude: (first.center[0]+second.center[0])/2, latitude:(first.center[1]+second.center[1])/2, zoom:15.5, pitch:0, bearing:0};
// Validate references rather than just array counts.
const groupIds = new Set(d.trackGroups.map(g => g.id));
for (const station of d.stations) {
  assert(groupIds.has(station.trackGroupId), 'Missing station track group');
  assert(station.trackIds.every(id => retainedTracks.has(id)), 'Missing station track');
}
for (const group of d.trackGroups) assert(group.trackIds.every(id => d.tracks.some(t => t.id === id)), 'Missing group track');
for (const combo of route.stCombos) {
  assert(retainedNodes.has(combo.startStNodeId) && retainedNodes.has(combo.endStNodeId), 'Missing route node');
  for (const segment of combo.path) assert(retainedTracks.has(segment.trackId), 'Missing route track');
}
assert(train.routeId === routeId && train.timings.length === 3, 'Invalid generated train');
// New framing: header, empty autosave index, no thumbnail, complete gzip bundle.
const index = Buffer.from('[]');
const payload = zlib.gzipSync(JSON.stringify({mainSave: save, autosaves: []}));
const header = Buffer.alloc(4096); header.write('METR');
header.writeUInt32LE(4096,8); header.writeUInt32LE(index.length,12);
header.writeUInt32LE(4096+index.length,16); header.writeUInt32LE(0,20);
header.writeUInt32LE(4096+index.length,24); header.writeUInt32LE(payload.length,28);
header.writeBigUInt64LE(BigInt(save.timestamp),32);
function str(offset, length, value) { const b=Buffer.from(value,'utf8');assert(b.length<length,'Header string too long');b.copy(header,offset); }
str(40,256,save.name); str(296,32,save.cityCode); str(328,64,save.gameSessionId);
str(392,512,JSON.stringify(save.metadata));
const output = Buffer.concat([header,index,payload]);
assert(JSON.parse(zlib.gunzipSync(output.subarray(output.readUInt32LE(24),output.readUInt32LE(24)+output.readUInt32LE(28)))).mainSave.id === save.id, 'Framing round trip failed');
fs.mkdirSync(outputDir,{recursive:true});
const outputPath = path.join(outputDir,'cur-two-station-probe.metro');
fs.writeFileSync(outputPath,output,{flag:'wx'});
const evidence = {gameVersion:version,sourcePath,sourceSha256:sha(original),outputPath,outputSha256:sha(output),workerSha256:sha(worker),
  stations:d.stations.map(s=>s.name),counts:{stations:2,routes:1,trains:1,tracks:d.tracks.length,trackGroups:d.trackGroups.length,signals:d.signals.length},
  routeDistanceEachDirection:outbound.distance,travelTimeAssumption:'Original outbound timing used both ways; not independently calibrated.',
  construction:'Template-derived two-station shuttle; reverse traversal of the same rail. Train generated by isolated installed simulation worker.',
  sourceUnchanged:sha(fs.readFileSync(sourcePath))===sha(original),staticChecks:'PASS',liveLoad:'PENDING',operation:'PENDING',saveReload:'PENDING'};
fs.writeFileSync(path.join(outputDir,'generation.json'),JSON.stringify(evidence,null,2)+'\n');
console.log(JSON.stringify(evidence,null,2));
