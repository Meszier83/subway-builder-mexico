/* Analysis works on counts, never the number of points or trip groups. */
(function (root) {
  'use strict';
  const R = 6371008.8, RAD = Math.PI / 180;
  function createGridSpec(points, size) {
    let minLat = Infinity, maxLat = -Infinity;
    for (const p of points) { minLat = Math.min(minLat, p.location[1]); maxLat = Math.max(maxLat, p.location[1]); }
    return {sizeM: size, referenceLat: points.length ? (minLat + maxLat) / 2 : 0, originXM: 0, originYM: 0};
  }
  function grid(points, size, spec = createGridSpec(points, size)) {
    if (spec.sizeM !== size) throw new Error('Grid size does not match its specification.');
    const cosine = Math.cos(spec.referenceLat * RAD);
    const project = p => [R * p[0] * RAD * cosine, R * p[1] * RAD];
    const unproject = p => [p[0] / (R * RAD * cosine), p[1] / (R * RAD)];
    const cells = new Map(), byPoint = new Map();
    for (const p of points) {
      const [x, y] = project(p.location), i = Math.floor(x / size), j = Math.floor(y / size);
      const key = `${i},${j}`;
      if (!cells.has(key)) {
        cells.set(key, {key, residents: 0, jobs: 0, points: 0,
          center: unproject([(i + .5) * size, (j + .5) * size]),
          bounds: [unproject([i * size, j * size]), unproject([(i + 1) * size, (j + 1) * size])]});
      }
      const cell = cells.get(key);
      cell.residents += p.residents; cell.jobs += p.jobs; cell.points++;
      byPoint.set(p.id, key);
    }
    return {cells: [...cells.values()], byPoint, size, spec};
  }
  function correlation(cells) {
    const used = cells.filter(c => c.residents + c.jobs > 0), n = used.length;
    if (n < 3) return {n, r: null, reason: 'At least 3 occupied cells are needed.'};
    const mx = used.reduce((s, c) => s + c.residents, 0) / n;
    const my = used.reduce((s, c) => s + c.jobs, 0) / n;
    let xx = 0, yy = 0, xy = 0;
    for (const c of used) {
      const x = c.residents - mx, y = c.jobs - my;
      xx += x * x; yy += y * y; xy += x * y;
    }
    return {n, r: xx && yy ? Math.max(-1, Math.min(1, xy / Math.sqrt(xx * yy))) : null,
      reason: xx && yy ? '' : 'One layer has no variation across cells.'};
  }
  function distance(a, b) {
    const s = Math.sin((b[1] - a[1]) * RAD / 2) ** 2 + Math.cos(a[1] * RAD) * Math.cos(b[1] * RAD) * Math.sin((b[0] - a[0]) * RAD / 2) ** 2;
    return R * 2 * Math.asin(Math.sqrt(Math.min(1, s)));
  }
  function select(points, location, radius) {
    const selected = points.filter(p => distance(p.location, location) <= radius);
    return {ids: new Set(selected.map(p => p.id)), residents: selected.reduce((s, p) => s + p.residents, 0),
      jobs: selected.reduce((s, p) => s + p.jobs, 0), points: selected.length};
  }
  function project(location, spec) {
    return [R * location[0] * RAD * Math.cos(spec.referenceLat * RAD), R * location[1] * RAD];
  }
  function cellKey(location, spec) {
    const [x, y] = project(location, spec);
    return `${Math.floor(x / spec.sizeM)},${Math.floor(y / spec.sizeM)}`;
  }
  function segmentDistance(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], length = dx * dx + dy * dy;
    const t = length ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length)) : 0;
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  }
  function polygonInfo(vertices, spec) {
    const ring = vertices.slice();
    if (ring.length > 1 && ring[0][0] === ring.at(-1)[0] && ring[0][1] === ring.at(-1)[1]) ring.pop();
    if (new Set(ring.map(p => p.join(','))).size < 3) return {error: 'Choose at least three distinct vertices.'};
    if (new Set(ring.map(p => p.join(','))).size !== ring.length) return {error: 'Polygon vertices must not repeat.'};
    const xy = ring.map(p => project(p, spec)), n = xy.length;
    const cross = (a,b,c) => (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;
      const a = xy[i], b = xy[(i+1)%n], c = xy[j], d = xy[(j+1)%n];
      const ab1 = cross(a,b,c), ab2 = cross(a,b,d), cd1 = cross(c,d,a), cd2 = cross(c,d,b);
      if ((ab1 * ab2 < 0 && cd1 * cd2 < 0) || segmentDistance(a,c,d) < .001 || segmentDistance(b,c,d) < .001 || segmentDistance(c,a,b) < .001 || segmentDistance(d,a,b) < .001)
        return {error: 'Polygon edges intersect; adjust the vertices.'};
    }
    let twiceArea = 0;
    // Translate before the shoelace sum to avoid cancellation at large projected coordinates.
    for (let i = 0; i < n; i++) {
      const a = xy[i], b = xy[(i+1)%n], o = xy[0];
      twiceArea += (a[0]-o[0])*(b[1]-o[1])-(b[0]-o[0])*(a[1]-o[1]);
    }
    const areaM2 = Math.abs(twiceArea) / 2;
    if (areaM2 < .000001) return {error: 'Polygon must enclose an area.'};
    const bounds = xy.reduce((v,p)=>[Math.min(v[0],p[0]),Math.min(v[1],p[1]),Math.max(v[2],p[0]),Math.max(v[3],p[1])],[Infinity,Infinity,-Infinity,-Infinity]);
    return {ring, xy, areaM2, bounds};
  }
  function containsPolygon(location, info, spec) {
    const p = project(location, spec), xy = info.xy;
    if(p[0]<info.bounds[0]-.001 || p[0]>info.bounds[2]+.001 || p[1]<info.bounds[1]-.001 || p[1]>info.bounds[3]+.001)return false;
    let inside = false;
    for (let i = 0, j = xy.length - 1; i < xy.length; j = i++) {
      const a = xy[j], b = xy[i];
      if (segmentDistance(p, a, b) <= .001) return true;
      if ((a[1] > p[1]) !== (b[1] > p[1]) && p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]) inside = !inside;
    }
    return inside;
  }
  function selectGeometry(points, geometry, spec) {
    if (!geometry) return null;
    const polygon = geometry.type === 'polygon' ? polygonInfo(geometry.vertices, spec) : null;
    if (polygon?.error) throw new Error(polygon.error);
    const selected = points.filter(p => geometry.type === 'circle' ? distance(p.location, geometry.center) <= geometry.radiusM :
      geometry.type === 'cell' ? cellKey(p.location, geometry.spec || spec) === geometry.key : containsPolygon(p.location, polygon, spec));
    const areaM2 = geometry.type === 'circle' ? Math.PI * geometry.radiusM ** 2 : geometry.type === 'cell' ? (geometry.spec || spec).sizeM ** 2 : polygon.areaM2;
    const residents = selected.reduce((s,p) => s+p.residents,0), jobs = selected.reduce((s,p) => s+p.jobs,0);
    const totalResidents = points.reduce((s,p)=>s+p.residents,0), totalJobs = points.reduce((s,p)=>s+p.jobs,0);
    return {ids: new Set(selected.map(p=>p.id)), points: selected.length, residents, jobs, areaM2,
      residentDensity: residents / areaM2 * 1e6, jobDensity: jobs / areaM2 * 1e6,
      balance: residents + jobs ? (jobs-residents)/(jobs+residents) : null,
      residentShare: totalResidents ? residents/totalResidents : null, jobShare: totalJobs ? jobs/totalJobs : null};
  }
  function selectedTrips(dataset, selectedIds, direction, bins) {
    const endpointWeights = new Map(), cells = new Map(bins.cells.map(c=>[c.key,c]));
    let total = 0, insideSelection = 0;
    for (const trip of dataset.pops) {
      const start = direction === 'out' ? trip.residenceId : trip.jobId;
      const end = direction === 'out' ? trip.jobId : trip.residenceId;
      if (!selectedIds.has(start)) continue;
      total += trip.size;
      if (selectedIds.has(end)) insideSelection += trip.size;
      endpointWeights.set(end, (endpointWeights.get(end) || 0) + trip.size);
    }
    const byCell = new Map();
    for (const [id, count] of endpointWeights) {
      const key = bins.byPoint.get(id); byCell.set(key, (byCell.get(key) || 0) + count);
    }
    const rankedCells = [...byCell].map(([key,count])=>({key, center: cells.get(key).center, count, share: total ? count/total : null}))
      .sort((a,b)=>b.count-a.count || a.key.localeCompare(b.key));
    return {total, insideSelection, outsideSelection: total-insideSelection, endpointWeights, rankedCells};
  }
  function compareCells(a, b) {
    const rows = new Map();
    for (const [label, bins] of [['a',a],['b',b]]) for (const cell of bins.cells) {
      if (!rows.has(cell.key)) rows.set(cell.key, {key:cell.key, center:cell.center, bounds:cell.bounds, a:{residents:0,jobs:0}, b:{residents:0,jobs:0}});
      rows.get(cell.key)[label] = {residents:cell.residents,jobs:cell.jobs};
    }
    return [...rows.values()].sort((x,y)=>x.key.localeCompare(y.key));
  }
  function compareTrips(a, b, binsA, binsB, selectedA = null, selectedB = null, filter = 'all') {
    if (!a.tripsAvailable || !b.tripsAvailable) return null;
    const rows = new Map(), centers = new Map([...binsA.cells,...binsB.cells].map(c=>[c.key,c.center]));
    for (const [label, dataset, bins, selected] of [['a',a,binsA,selectedA],['b',b,binsB,selectedB]]) for (const trip of dataset.pops) {
      if (filter !== 'all' && (!selected || !selected.has(filter === 'out' ? trip.residenceId : trip.jobId))) continue;
      const from = bins.byPoint.get(trip.residenceId), to = bins.byPoint.get(trip.jobId), key = `${from}>${to}`;
      if (!rows.has(key)) rows.set(key,{from,to,fromCenter:centers.get(from),toCenter:centers.get(to),a:0,b:0});
      rows.get(key)[label] += trip.size;
    }
    return [...rows.values()].map(r=>({...r,delta:r.b-r.a})).sort((x,y)=>Math.abs(y.delta)-Math.abs(x.delta)||x.from.localeCompare(y.from)||x.to.localeCompare(y.to));
  }
  function flows(pops, bins, selected = null, direction = 'out') {
    const pairs = new Map();
    let total = 0, internal = 0;
    for (const pop of pops) {
      if (selected && !selected.has(direction === 'out' ? pop.residenceId : pop.jobId)) continue;
      total += pop.size;
      const from = bins.byPoint.get(pop.residenceId), to = bins.byPoint.get(pop.jobId);
      if (from === to) { internal += pop.size; continue; }
      const key = `${from}>${to}`;
      if (!pairs.has(key)) pairs.set(key, {from, to, size: 0});
      pairs.get(key).size += pop.size;
    }
    return {total, internal, links: [...pairs.values()].sort((a, b) => b.size - a.size || a.from.localeCompare(b.from) || a.to.localeCompare(b.to))};
  }
  function concentration(cells, field) {
    const values = cells.filter(c => c.residents + c.jobs > 0).map(c => c[field]).sort((a, b) => b - a);
    const total = values.reduce((s, v) => s + v, 0), n = Math.ceil(values.length * .1);
    return total ? values.slice(0, n).reduce((s, v) => s + v, 0) / total : null;
  }
  function heatField(points, width, height, radius, step = 4) {
    const support = Math.ceil(radius / step), pad = support + 1;
    const w = Math.ceil(width / step) + 2 * pad, h = Math.ceil(height / step) + 2 * pad;
    const raw = new Float64Array(w * h), temp = new Float64Array(w * h), values = new Float64Array(w * h);
    for (const p of points) {
      const gx = p.x / step + pad - .5, gy = p.y / step + pad - .5;
      const x = Math.floor(gx), y = Math.floor(gy), dx = gx - x, dy = gy - y;
      for (let j = 0; j < 2; j++) for (let i = 0; i < 2; i++) {
        if (x + i >= 0 && x + i < w && y + j >= 0 && y + j < h)
          raw[(y + j) * w + x + i] += p.weight * (i ? dx : 1 - dx) * (j ? dy : 1 - dy);
      }
    }
    const sigma = Math.max(1, support / 3), kernel = [];
    for (let k = -support; k <= support; k++) kernel.push(Math.exp(-k * k / (2 * sigma * sigma)));
    const norm = kernel.reduce((s, v) => s + v, 0);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let sum = 0;
      for (let k = -support; k <= support; k++) if (x + k >= 0 && x + k < w) sum += raw[y * w + x + k] * kernel[k + support];
      temp[y * w + x] = sum / norm;
    }
    let max = 0;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let sum = 0;
      for (let k = -support; k <= support; k++) if (y + k >= 0 && y + k < h) sum += temp[(y + k) * w + x] * kernel[k + support];
      values[y * w + x] = sum / norm;
      if (x >= pad && x < w - pad && y >= pad && y < h - pad) max = Math.max(max, values[y * w + x]);
    }
    return {values, width: w, height: h, pad, step, max};
  }
  function createHeatSpec(points, radiusM) {
    // One map-wide raster in Mercator coordinates, scaled to approximate ground metres.
    // The viewport never participates in the kernel or color domain.
    let minLat = Infinity, maxLat = -Infinity;
    for (const p of points) { minLat = Math.min(minLat, p.location[1]); maxLat = Math.max(maxLat, p.location[1]); }
    const scale = R * Math.cos((minLat + maxLat) * RAD / 2);
    const project = p => [scale * p[0] * RAD, scale * Math.log(Math.tan(Math.PI / 4 + p[1] * RAD / 2))];
    const unproject = (x, y) => [x / (scale * RAD), (2 * Math.atan(Math.exp(y / scale)) - Math.PI / 2) / RAD];
    const sources = points.map(p => ({xy: project(p.location)}));
    if (!sources.length) throw new Error('Heat geometry requires at least one point.');
    let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
    for (const p of sources) {
      west = Math.min(west, p.xy[0]); east = Math.max(east, p.xy[0]);
      south = Math.min(south, p.xy[1]); north = Math.max(north, p.xy[1]);
    }
    const spanX = east - west + 2 * radiusM, spanY = north - south + 2 * radiusM;
    const cellM = Math.max(radiusM / 6, Math.sqrt(spanX * spanY / 1000000), spanX / 2048, spanY / 2048);
    west = Math.floor(west / cellM) * cellM; north = Math.ceil(north / cellM) * cellM;
    const width = Math.ceil((east - west) / cellM) + 1, height = Math.ceil((north - south) / cellM) + 1;
    const pad = Math.ceil(radiusM / cellM) + 1;
    return {scale, west, north, cellM, radiusM, width, height, pad,
      bounds: [unproject(west - pad * cellM, north - (height + pad) * cellM),
        unproject(west + (width + pad) * cellM, north + pad * cellM)]};
  }
  function heatSurface(points, field, radiusM, spec = createHeatSpec(points, radiusM)) {
    if (spec.radiusM !== radiusM) throw new Error('Heat radius does not match its specification.');
    const {scale, west, north, cellM, width, height} = spec;
    const sources = points.filter(p => p[field] > 0).map(p => ({
      x: (scale * p.location[0] * RAD - west) / cellM,
      y: (north - scale * Math.log(Math.tan(Math.PI / 4 + p.location[1] * RAD / 2))) / cellM,
      weight: p[field]}));
    const raster = heatField(sources, width, height, radiusM / cellM, 1);
    let max = 0;
    for (const value of raster.values) max = Math.max(max, value);
    return {...raster, max, cellM, radiusM, peakDensity: max / (cellM * cellM) * 1000000,
      bounds: spec.bounds, spec};
  }
  function heatTone(fraction, strength = 1) {
    // Fixed map-wide logarithmic display curve: reveal secondary concentrations
    // while retaining zero, the peak, and strict ordering without clipping.
    const normalized = Math.log1p(32 * Math.max(0, Math.min(1, fraction))) / Math.log1p(32);
    return Math.pow(normalized, 1 / strength);
  }
  function areaPairDemand(dataset, idsA, idsB) {
    const direction = () => ({count:0, originShare:null, destinationShare:null, originWeights:new Map(), destinationWeights:new Map()});
    const shared = new Set([...idsA].filter(id=>idsB.has(id)));
    const r={available:dataset.tripsAvailable!==false, sharedPointCount:shared.size, sharedDirectionalWeight:0,
      totals:{outA:0,inA:0,outB:0,inB:0},ab:direction(),ba:direction()};
    if(!r.available){for(const key in r.totals)r.totals[key]=null;r.ab.count=r.ba.count=r.sharedDirectionalWeight=null;return r;}
    const add=(d,p)=>{d.count+=p.size;d.originWeights.set(p.residenceId,(d.originWeights.get(p.residenceId)||0)+p.size);d.destinationWeights.set(p.jobId,(d.destinationWeights.get(p.jobId)||0)+p.size);};
    for(const p of dataset.pops){const a=idsA.has(p.residenceId),b=idsB.has(p.residenceId),c=idsA.has(p.jobId),d=idsB.has(p.jobId);
      if(a)r.totals.outA+=p.size;if(b)r.totals.outB+=p.size;if(c)r.totals.inA+=p.size;if(d)r.totals.inB+=p.size;
      if(a&&d)add(r.ab,p);if(b&&c)add(r.ba,p);if(a&&b&&c&&d)r.sharedDirectionalWeight+=p.size;
    }
    for(const [dir,out,into] of [[r.ab,r.totals.outA,r.totals.inB],[r.ba,r.totals.outB,r.totals.inA]]){dir.originShare=out?dir.count/out:null;dir.destinationShare=into?dir.count/into:null;}
    return r;
  }
  const api = {areaPairDemand,createGridSpec, grid, cellKey, polygonInfo, selectGeometry, selectedTrips, compareCells, compareTrips, correlation, distance, select, flows, concentration, heatField, createHeatSpec, heatSurface, heatTone};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MapAnalysis = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
