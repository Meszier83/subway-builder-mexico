/* global L, MapAnalysis, MapImport */
'use strict';
const $ = id => document.getElementById(id);
const A = MapAnalysis;
const number = value => value.toLocaleString('en-US');
const percent = value => value === null ? '—' : `${(100 * value).toFixed(1)}%`;
const palettes = {residents: [[255, 193, 42], [241, 102, 24], [169, 18, 29]],
  jobs: [[187, 157, 246], [131, 66, 200], [63, 13, 113]]};
const map = L.map('map', {preferCanvas: true, zoomControl: true}).setView([21.12, -86.87], 11);
const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19}).addTo(map);
tiles.on('tileerror', () => { $('basemap-status').hidden = false; });
tiles.on('tileload', () => { $('basemap-status').hidden = true; });
for (const [pane, z] of [['analysisGrid', 410], ['analysisFlow', 430], ['analysisSelection', 440]]) map.createPane(pane).style.zIndex = z;
const gridLayer = L.layerGroup().addTo(map), flowLayer = L.layerGroup().addTo(map), selectionLayer = L.layerGroup().addTo(map), endpointLayer = L.layerGroup().addTo(map);
// SVG overlays let clicks outside their paths reach the canvas grid below.
const flowRenderer = L.svg({pane: 'analysisFlow'}), selectionRenderer = L.svg({pane: 'analysisSelection'});
let dataset = null, bins = null, selection = null, selectedLocation = null, selectedCell = null, scatterDots = [];
let layer = 'residents', importSequence = 0;
let selectionGeometry = null, polygonDraft = [], tripView = 'off', tripPage = 0;
let heatSpec = null;
const displayByLayer = {residents: 'heat', jobs: 'heat'};
let comparison = null, comparisonBins = null, viewMode = 'single', secondaryMap = null;
let comparisonSequence = 0, secondaryGrid = null, secondarySelection = null, secondaryHeat = null, synchronizing = false;
let primaryFilename = '', comparisonFilename = '', primaryBytes = 0, comparisonBytes = 0, savedLayer = 'residents';
let comparisonPage = 0;
const heatCache = new Map();
let heatOverlay = null, heatFrame = 0;

const focusPalettes = {residents:[[14,55,100],[27,143,218],[225,248,255]], jobs:[[52,28,100],[153,94,227],[251,234,255]]};
let mapTheme='dark';
function color(t, field = layer) {
  const scheme=mapTheme==='dark'?focusPalettes:palettes;
  const colors = scheme[field] || scheme.residents;
  const position = Math.max(0, Math.min(1, t)) * (colors.length - 1);
  const i = Math.min(colors.length - 2, Math.floor(position)), f = position - i;
  return colors[i].map((v, k) => Math.round(v + (colors[i + 1][k] - v) * f));
}
function cssColor(rgb) { return `rgb(${rgb.join(',')})`; }
function setTicks(id, low, high) {
  const target=$(id);target.replaceChildren();
  for(let i=0;i<=4;i++){const tick=document.createElement('span');tick.textContent=number(Math.round(low+(high-low)*i/4));target.append(tick);}
}
function textTooltip(lines) {
  const node = document.createElement('div');
  lines.forEach(line => { const row = document.createElement('div'); row.textContent = line; node.appendChild(row); });
  return node;
}
function fit() { if (dataset) map.fitBounds([...dataset.points, ...(viewMode === 'versions' && comparison ? comparison.points : [])].map(p => [p.location[1], p.location[0]]), {padding: [55, 55], maxZoom: 15}); }

function secondarySurface() {
  const other = viewMode === 'people-jobs' ? dataset : comparison;
  if (!other) return null;
  const field = viewMode === 'people-jobs' ? 'jobs' : $('version-metric').value;
  if (!['residents','jobs'].includes(field)) return null;
  if (!heatSpec) heatSpec = A.createHeatSpec(viewMode === 'versions' ? [...dataset.points,...comparison.points] : dataset.points, +$('smoothing').value);
  const key = `secondary:${viewMode}:${field}:${$('smoothing').value}`;
  if (!heatCache.has(key)) heatCache.set(key, A.heatSurface(other.points, field, +$('smoothing').value, heatSpec));
  return heatCache.get(key);
}
function heatDomain(field) {
  const other = !$('map-secondary').hidden && (viewMode === 'versions' || $('scale-mode').value === 'shared') ? secondarySurface() : null;
  return Math.max(field.peakDensity, other?.peakDensity || 0);
}

function getHeatSurface() {
  const selectedTrip = selection && (tripView === 'destinations' || tripView === 'origins');
  const key = selectedTrip ? `trip:${tripView}` : `${layer}:${$('smoothing').value}`;
  if (!heatCache.has(key)) {
    if (!heatSpec) heatSpec = A.createHeatSpec(viewMode === 'versions' && comparison ? [...dataset.points,...comparison.points] : dataset.points, +$('smoothing').value);
    let points = dataset.points, field = layer;
    if (selectedTrip) {
      const result = A.selectedTrips(dataset, selection.ids, tripView === 'destinations' ? 'out' : 'in', bins);
      points = dataset.points.map(p => ({...p, tripWeight: result.endpointWeights.get(p.id) || 0})); field = 'tripWeight';
    }
    heatCache.set(key, A.heatSurface(points, field, +$('smoothing').value, heatSpec));
  }
  return heatCache.get(key);
}
const heatLayer = {
  redraw() {
    if (heatOverlay) { heatOverlay.remove(); heatOverlay = null; }
    if (!dataset || (viewMode === 'versions' && $('version-display').value === 'difference' && $('version-metric').value !== 'trips') || ($('display').value !== 'heat' && !['destinations','origins'].includes(tripView)) || (layer === 'balance' && !['destinations','origins'].includes(tripView))) return;
    const field = getHeatSurface(), strength = +$('strength').value;
    if (!field.max) return;
    const domain = heatDomain(field);
    const small = document.createElement('canvas'); small.width = field.width; small.height = field.height;
    const ctx = small.getContext('2d'), pixels = ctx.createImageData(field.width, field.height);
    for (let i = 0; i < field.values.length; i++) {
      const t = field.values[i] / (field.cellM * field.cellM) * 1e6 / domain;
      if (t <= 0) continue;
      const tone = A.heatTone(t, strength), rgb = color(tone);
      pixels.data[i * 4] = rgb[0]; pixels.data[i * 4 + 1] = rgb[1]; pixels.data[i * 4 + 2] = rgb[2];
      pixels.data[i * 4 + 3] = Math.round(.98 * Math.pow(tone,.65) * 255);
    }
    ctx.putImageData(pixels, 0, 0);
    heatOverlay = L.imageOverlay(small.toDataURL('image/png'), field.bounds.map(p => [p[1], p[0]]),
      {interactive: false, className: 'analysis-heat'}).addTo(map);
  }
};
function scheduleHeat() {
  cancelAnimationFrame(heatFrame);
  heatFrame = requestAnimationFrame(() => { if (dataset) { drawLegend(); heatLayer.redraw(); renderSecondary(); } });
}
function balanceColor(value) {
  const neutral = [125, 132, 125], target = value >= 0 ? [105, 46, 162] : [220, 76, 23];
  const intensity = Math.pow(Math.abs(value), .7);
  return cssColor(neutral.map((v, i) => Math.round(v + (target[i] - v) * intensity)));
}

function drawLegend(max = 0) {
  const tripHeat = selection && ['destinations','origins'].includes(tripView);
  const balance = layer === 'balance' && !tripHeat, heat = tripHeat || ($('display').value === 'heat' && !balance);
  $('legend').hidden = false;
  $('balance-key').hidden=!balance; $('legend-gradient').hidden=balance;
  $('legend-ticks').hidden=balance;
  if(balance) {
    const key=$('balance-key');key.replaceChildren();
    for(const label of ['Activity','People','Balanced','Jobs']) {const text=document.createElement('span');text.textContent=label;key.append(text);}
    for(const [label,fraction] of [['1%',.01],['10%',.1],['100%',1]]) {
      const text=document.createElement('span');text.textContent=label;key.append(text);
      for(const value of [-1,0,1]) {const swatch=document.createElement('i');swatch.style.background=balanceColor(value);swatch.style.opacity=String(.20+.73*Math.sqrt(fraction));key.append(swatch);}
    }
    const empty=document.createElement('span');empty.textContent='Empty';key.append(empty);
    const swatch=document.createElement('i');swatch.style.background='transparent';key.append(swatch);
  }
  $('legend-title').textContent = tripHeat ? (tripView === 'destinations' ? 'Selected residents: destinations' : 'Selected jobs: origins') : balance ? 'Residential ↔ employment balance' : layer === 'residents' ? 'Resident commuters' : 'Job arrivals';
  const stops = [0, .01, .025, .05, .1, .25, .5, .75, 1];
  $('legend-gradient').style.background = balance ? `linear-gradient(90deg,${[-1,-.5,0,.5,1].map(balanceColor).join(',')})` :
    `linear-gradient(90deg,${stops.map(t => `${cssColor(color(A.heatTone(t, heat ? +$('strength').value : 1)))} ${t * 100}%`).join(',')})`;
  $('legend-low').textContent = balance ? 'More people' : '0';
  $('legend-high').textContent = balance ? 'More jobs' : heat ? (heatDomain(getHeatSurface()) ? `≈${number(Math.round(heatDomain(getHeatSurface())))} / km²` : 'No demand') : number(max);
  if(!balance)setTicks('legend-ticks',0,heat?heatDomain(getHeatSurface()):max);
  $('legend-note').textContent = balance ? 'Rows show share of maximum activity. Activity is jobs + people, not unique people. Empty cells have no fill.' : heat ?
    `Smoothed density · ${number(+$('smoothing').value)} m radius. ${!$('map-secondary').hidden ? (viewMode === 'versions' || $('scale-mode').value === 'shared' ? 'Shared scale.' : 'Independent scale.') : 'Map-wide scale.'} Enhanced contrast; counts unchanged.${getHeatSurface().cellM > +$('smoothing').value / 5 ? ` Sampled at ≈${number(Math.round(getHeatSurface().cellM))} m.` : ''}` :
    `Counts per approximate ${number(bins.size)} m cell. Enhanced contrast; scale covers the whole imported map.`;
  if(viewMode==='versions' && $('version-metric').value==='trips')$('legend-note').textContent+=' Connections: red increased, blue decreased; dots mark destinations.';
}
function drawGrid() {
  gridLayer.clearLayers();
  if (viewMode === 'versions' && comparison && $('version-display').value === 'difference' && $('version-metric').value !== 'trips') {
    const field=$('version-metric').value, rows=A.compareCells(bins,comparisonBins);
    const max=rows.reduce((m,r)=>Math.max(m,Math.abs(r.b[field]-r.a[field])),0);
    $('legend').hidden=false; $('legend-title').textContent=`${field==='jobs'?'Jobs':'People'} change · B − A`;
    $('balance-key').hidden=true; $('legend-gradient').hidden=false;
    $('legend-ticks').hidden=false;setTicks('legend-ticks',-max,max);
    $('legend-gradient').style.background='linear-gradient(90deg,#3166ad,#f7f7ef,#bc3025)';
    $('legend-low').textContent=`−${number(max)}`; $('legend-high').textContent=`+${number(max)}`;
    $('legend-note').textContent='Counts per cell · blue decreases, red increases, no fill is unchanged.';
    for(const row of rows) {
      const delta=row.b[field]-row.a[field]; if (!delta) continue;
      const rect=L.rectangle(row.bounds.map(p=>[p[1],p[0]]),{pane:'analysisGrid',bubblingMouseEvents:false,color:'#fff',weight:.25,fillColor:delta>0?'#bc3025':'#3166ad',fillOpacity:.2+.73*Math.sqrt(Math.abs(delta)/max)}).addTo(gridLayer);
      rect.bindTooltip(textTooltip([`A: ${number(row.a[field])} · B: ${number(row.b[field])}`,`Change: ${delta>0?'+':''}${number(delta)}`]));
      rect.on('click',event=>handleMapClick(event.latlng));
    }
    return;
  }
  const grid = !['destinations','origins'].includes(tripView) && ($('display').value === 'grid' || layer === 'balance');
  $('smoothing-label').hidden = grid;
  $('strength-label').hidden = grid;
  let max = 0;
  let activityMax = 0;
  for (const c of bins.cells) { max = Math.max(max, c[layer] || 0); activityMax = Math.max(activityMax, c.residents + c.jobs); }
  if (viewMode !== 'single' && (viewMode === 'versions' || $('scale-mode').value === 'shared')) {
    const otherBins=viewMode==='versions'?comparisonBins:bins, otherField=viewMode==='people-jobs'?'jobs':layer;
    if(otherBins) for(const c of otherBins.cells) max=Math.max(max,c[otherField]||0);
  }
  drawLegend(max);
  if (!grid) return;
  const visible = map.getBounds().pad(.1);
  for (const cell of bins.cells) {
    const mass = cell.residents + cell.jobs;
    if (!mass) continue;
    const bounds = cell.bounds.map(p => [p[1], p[0]]);
    if (!visible.intersects(L.latLngBounds(bounds))) continue;
    const balance = (cell.jobs - cell.residents) / mass;
    const fill = layer === 'balance' ? balanceColor(balance) : cssColor(color(A.heatTone(max ? cell[layer] / max : 0)));
    const rectangle = L.rectangle(bounds, {pane: 'analysisGrid', bubblingMouseEvents: false, color: '#fff', opacity: .3, weight: .25, fillColor: fill,
      fillOpacity: layer === 'balance' ? .20 + .73 * Math.sqrt(mass / activityMax) : cell[layer] ? .9 : 0}).addTo(gridLayer);
    rectangle.bindTooltip(textTooltip([`${number(cell.residents)} resident commuters`, `${number(cell.jobs)} job arrivals`, `${number(cell.points)} demand points`]));
    rectangle.on('click', event => { if (!areaPair.enabled && $('inspect-mode').value === 'cell') selectCell(cell); else handleMapClick(event.latlng); });
  }
}
function drawScatter() {
  const canvas = $('scatter'), ratio = window.devicePixelRatio || 1, width = canvas.clientWidth;
  canvas.width = Math.round(width * ratio); canvas.height = 110 * ratio;
  const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
  const cells = bins.cells.filter(c => c.residents + c.jobs > 0);
  const maxX = cells.reduce((s, c) => Math.max(s, c.residents), 1), maxY = cells.reduce((s, c) => Math.max(s, c.jobs), 1);
  const left = 28, top = 10, right = width - 12, bottom = 84;
  ctx.strokeStyle = '#bbc7b7'; ctx.beginPath(); ctx.moveTo(left, top); ctx.lineTo(left, bottom); ctx.lineTo(right, bottom); ctx.stroke();
  ctx.fillStyle = '#687a73'; ctx.font = '9px Segoe UI'; ctx.fillText('Jobs ↑', 3, 9); ctx.fillText('0', 15, bottom + 3); ctx.fillText('Resident commuters →', Math.max(left, right - 110), 103);
  scatterDots = cells.map(cell => ({cell, x: left + cell.residents / maxX * (right - left), y: bottom - cell.jobs / maxY * (bottom - top)}));
  for (const dot of scatterDots) { ctx.beginPath(); ctx.arc(dot.x, dot.y, 2.7, 0, Math.PI * 2); ctx.fillStyle = dot.cell === selectedCell ? '#d96b37' : '#38695d88'; ctx.fill(); }
  const stat = A.correlation(bins.cells);
  $('correlation').textContent = stat.r === null ? 'N/A' : stat.r.toFixed(2);
  $('cell-count').textContent = stat.reason || `${number(stat.n)} occupied cells · ${number(bins.size)} m`;
}
function updateConcentration() {
  const people = A.concentration(bins.cells, 'residents'), jobs = A.concentration(bins.cells, 'jobs');
  $('concentration').textContent = `The top 10% of occupied cells hold ${percent(people)} of resident commuters and ${percent(jobs)} of job arrivals (ranked separately, rounded up to whole cells).`;
}
function selectionText(title) {
  endpointLayer.clearLayers();
  const box = $('selection'); box.replaceChildren();
  const heading = document.createElement('strong'); heading.textContent = title; box.appendChild(heading);
  const detail = document.createElement('p'); detail.textContent = `${number(selection.residents)} resident commuters · ${number(selection.jobs)} job arrivals · ${number(selection.points)} points`; box.appendChild(detail);
  $('clear-selection').hidden = false;
  const area = selection.areaM2 / 1e6;
  $('inspector-summary').replaceChildren(textTooltip([title,
    `${number(selection.residents)} resident commuters (${percent(selection.residentShare)} of map)`,
    `${number(selection.jobs)} job arrivals (${percent(selection.jobShare)} of map)`,
    `${number(selection.points)} points · ${area.toFixed(2)} km²`,
    `≈${number(Math.round(selection.residentDensity))} people / km² · ≈${number(Math.round(selection.jobDensity))} jobs / km²`,
    `Balance: ${selection.balance === null ? 'N/A' : selection.balance.toFixed(2)}`]));
  if(viewMode==='versions' && comparison) {
    const other=A.selectGeometry(comparison.points,selectionGeometry,bins.spec), detail=document.createElement('p');
    const change=(a,b)=>{const c=changeStatus(a,b);return `${b-a>0?'+':''}${number(b-a)} (${c.percent===null?(c.status==='new'?'New':'N/A'):`${c.percent.toFixed(1)}%`})`;};
    detail.textContent=`B: ${number(other.residents)} people · ${number(other.jobs)} jobs; change: ${change(selection.residents,other.residents)} people · ${change(selection.jobs,other.jobs)} jobs.`;
    $('inspector-summary').append(detail);
  }
  heatCache.clear(); tripPage = 0; drawTripResults();
  renderSecondary();
  if(viewMode==='versions')drawComparisonResults();
}
function selectArea(location) {
  selectedCell = null; selectedLocation = location;
  selectionGeometry = {type:'circle', center:location, radiusM:+$('radius').value};
  selection = A.selectGeometry(dataset.points, selectionGeometry, bins.spec);
  selectionLayer.clearLayers();
  L.circle([location[1], location[0]], {renderer: selectionRenderer, radius: +$('radius').value, color: '#39b6a1', weight: 2, fillOpacity: .06, dashArray: '5 4', interactive: false}).addTo(selectionLayer);
  selectionText(`${number(+$('radius').value)} m area · straight-line radius`);
  drawScatter(); drawFlows(); if (['destinations','origins'].includes(tripView)) { drawGrid(); heatLayer.redraw(); }
}
function selectCell(cell) {
  selectedCell = cell; selectedLocation = null;
  selectionGeometry = {type:'cell', key:cell.key, spec:bins.spec, bounds:cell.bounds};
  selection = A.selectGeometry(dataset.points, selectionGeometry, bins.spec);
  selectionLayer.clearLayers();
  L.rectangle(cell.bounds.map(p => [p[1], p[0]]), {renderer: selectionRenderer, color: '#39b6a1', weight: 3, fillOpacity: 0, interactive: false}).addTo(selectionLayer);
  selectionText(`${number(bins.size)} m analysis cell`);
  map.panTo([cell.center[1], cell.center[0]]);
  drawScatter(); drawFlows(); if (['destinations','origins'].includes(tripView)) { drawGrid(); heatLayer.redraw(); }
}
function selectPolygon(vertices) {
  const info = A.polygonInfo(vertices, bins.spec);
  if (info.error) { $('polygon-status').textContent = info.error; return false; }
  selectionGeometry = {type:'polygon', vertices:info.ring}; selectedCell = null; selectedLocation = null;
  selection = A.selectGeometry(dataset.points, selectionGeometry, bins.spec);
  selectionLayer.clearLayers();
  L.polygon(info.ring.map(p=>[p[1],p[0]]), {renderer:selectionRenderer, color:'#39b6a1', weight:2, fillOpacity:.06, interactive:false}).addTo(selectionLayer);
  selectionText('Selected polygon · approximate area');
  drawScatter(); drawFlows(); if (['destinations','origins'].includes(tripView)) { drawGrid(); heatLayer.redraw(); }
  return true;
}
function handleMapClick(latlng) {
  if (!dataset) return;
  if(areaPair.enabled){pairClick(latlng);return;}
  const location = [latlng.lng, latlng.lat], mode = $('inspect-mode').value;
  if (mode === 'polygon') {
    polygonDraft.push(location); $('polygon-status').textContent = `${polygonDraft.length} vertices · click Finish to select.`;
    selectionLayer.clearLayers();
    if (polygonDraft.length > 1) L.polyline(polygonDraft.map(p=>[p[1],p[0]]), {renderer:selectionRenderer, color:'#39b6a1', interactive:false}).addTo(selectionLayer);
  } else if (mode === 'cell') {
    const key = A.cellKey(location, bins.spec);
    const cell = bins.cells.find(c=>c.key===key) || A.grid([{id:'empty',location,residents:0,jobs:0}], bins.size, bins.spec).cells[0];
    selectCell(cell);
  } else selectArea(location);
}
function drawTripResults() {
  const box = $('trip-results'); box.replaceChildren();
  if (!dataset || !selection || !['destinations','origins'].includes(tripView) || !dataset.tripsAvailable) { box.hidden = true; return; }
  box.hidden = false;
  const result = A.selectedTrips(dataset, selection.ids, tripView === 'destinations' ? 'out' : 'in', bins);
  const summary = document.createElement('p'); summary.textContent = `${number(result.total)} modeled commuters · ${number(result.insideSelection)} inside selection · ${number(result.outsideSelection)} outside`; box.append(summary);
  if (!result.total) { const empty = document.createElement('p'); empty.textContent = 'No matching trips.'; box.append(empty); return; }
  const table = document.createElement('table');
  const head = document.createElement('tr'); ['Cell','Commuters','Share'].forEach(label=>{const th=document.createElement('th');th.textContent=label;head.append(th);}); table.append(head);
  const page = result.rankedCells.slice(tripPage*20,tripPage*20+20);
  for (const row of page) {
    const tr=document.createElement('tr'), td=document.createElement('td'), button=document.createElement('button');
    button.textContent=row.key; button.onclick=()=>{
      endpointLayer.clearLayers();const cell=bins.cells.find(c=>c.key===row.key);
      if(cell)L.rectangle(cell.bounds.map(p=>[p[1],p[0]]),{renderer:selectionRenderer,color:'#00a783',weight:3,fillOpacity:0,interactive:false}).addTo(endpointLayer);
      map.panTo([row.center[1],row.center[0]]);
    }; td.append(button); tr.append(td);
    for (const value of [number(row.count),percent(row.share)]) { const cell=document.createElement('td'); cell.textContent=value; tr.append(cell); } table.append(tr);
  }
  box.append(table);
  const note=document.createElement('p'); note.className='inspector-note'; const shown=page.reduce((s,r)=>s+r.count,0);
  note.textContent=`${number(result.rankedCells.length-page.length)} cells outside this page · ${percent((result.total-shown)/result.total)} of trips`; box.append(note);
  if (tripPage>0) {const b=document.createElement('button');b.textContent='Previous';b.onclick=()=>{tripPage--;drawTripResults();};box.append(b);}
  if ((tripPage+1)*20<result.rankedCells.length) {const b=document.createElement('button');b.textContent='Next';b.onclick=()=>{tripPage++;drawTripResults();};box.append(b);}
}
function clearSelection(render = true) {
  selection = null; selectionGeometry = null; selectedCell = null; selectedLocation = null; selectionLayer.clearLayers(); endpointLayer.clearLayers();
  polygonDraft=[]; if($('inspect-mode').value==='polygon')$('polygon-status').textContent='Click to add polygon vertices.';
  tripView='off'; $('trip-view').value='off'; $('inspector-summary').textContent='Click the map to inspect an area.'; $('trip-results').hidden=true; $('export-trips').hidden=true; heatCache.clear();
  $('selection').replaceChildren(textTooltip(['Click the map to inspect an area.', 'See counts and the trips connected to it.']));
  $('clear-selection').hidden = true;
  if (dataset && render) { drawScatter(); drawFlows(); drawGrid(); heatLayer.redraw(); renderSecondary(); if(viewMode==='versions')drawComparisonResults(); }
}
function addFlowArrow(from, to, color) {
  const a=map.latLngToLayerPoint(from),b=map.latLngToLayerPoint(to),dx=b.x-a.x,dy=b.y-a.y,length=Math.hypot(dx,dy);
  if(length<18)return;
  const ux=dx/length,uy=dy/length, tip=L.point(a.x+dx*.8,a.y+dy*.8);
  const left=L.point(tip.x-8*ux+4*uy,tip.y-8*uy-4*ux),right=L.point(tip.x-8*ux-4*uy,tip.y-8*uy+4*ux);
  L.polyline([left,tip,right].map(p=>map.layerPointToLatLng(p)),{renderer:flowRenderer,color,weight:2,interactive:false}).addTo(flowLayer);
}
function drawFlows() {
  if(areaPair.enabled){flowLayer.clearLayers();return;}
  if(viewMode==='versions' && $('version-metric').value==='trips') {drawComparisonResults();return;}
  flowLayer.clearLayers();
  if (!dataset.tripsAvailable) { $('flow-summary').textContent = 'Trip flows unavailable: this file has no pops array.'; return; }
  if (!$('show-flows').checked) { $('flow-summary').textContent = 'Enable trip links to explore modeled origin–destination connections.'; return; }
  const result = A.flows(dataset.pops, bins, selection?.ids, $('flow-direction').value);
  const shown = result.links.slice(0, 30), cells = new Map(bins.cells.map(c => [c.key, c]));
  for (const link of shown) {
    const from = cells.get(link.from).center, to = cells.get(link.to).center;
    const polyline = L.polyline([[from[1], from[0]], [to[1], to[0]]], {renderer: flowRenderer, color: '#65b4e8', weight: 1 + 6 * Math.sqrt(link.size / shown[0].size), opacity: .65}).addTo(flowLayer);
    polyline.bindTooltip(textTooltip([`${number(link.size)} modeled commuters`, 'Origin cell → destination cell. Straight connection, not a road route.']));
    L.circleMarker([to[1], to[0]], {renderer: flowRenderer, radius: 3, color: '#65b4e8', weight: 1, fillColor: '#fff', fillOpacity: 1}).addTo(flowLayer);
    addFlowArrow([from[1],from[0]],[to[1],to[0]],'#65b4e8');
  }
  const mass = shown.reduce((s, link) => s + link.size, 0);
  $('flow-summary').textContent = `${number(shown.length)} of ${number(result.links.length)} inter-cell links shown, representing ${percent(result.total ? mass / result.total : null)} of ${number(result.total)} selected commuters. ${number(result.internal)} trips stay within one analysis cell. Destination ends have a white dot.`;
}
function createSecondary() {
  if (secondaryMap) return;
  secondaryMap=L.map('map-secondary',{preferCanvas:true,zoomControl:false}).setView(map.getCenter(),map.getZoom());
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',maxZoom:19}).addTo(secondaryMap);
  secondaryGrid=L.layerGroup().addTo(secondaryMap); secondarySelection=L.layerGroup().addTo(secondaryMap);
  function mirror(from,to) {
    if(synchronizing || viewMode==='single' || $('map-secondary').hidden) return;
    synchronizing=true; const c=from.getCenter(), z=from.getZoom();
    if(!to.getCenter().equals(c,1e-8) || to.getZoom()!==z) to.setView(c,z,{animate:false});
    synchronizing=false;
  }
  map.on('moveend zoomend',()=>mirror(map,secondaryMap));
  secondaryMap.on('moveend zoomend',()=>{mirror(secondaryMap,map); renderSecondary();});
  secondaryMap.on('click',event=>handleMapClick(event.latlng));
}
function renderSecondary() {
  if(!secondaryMap || $('map-secondary').hidden || !dataset) return;
  secondaryGrid.clearLayers(); secondarySelection.clearLayers();
  if(secondaryHeat){secondaryHeat.remove();secondaryHeat=null;}
  const other=viewMode==='people-jobs'?dataset:comparison, otherBins=viewMode==='people-jobs'?bins:comparisonBins;
  if(!other || !otherBins) return;
  const field=viewMode==='people-jobs'?'jobs':$('version-metric').value;
  if(!['residents','jobs'].includes(field)) return;
  $('secondary-title').textContent=viewMode==='people-jobs'?'JOB ARRIVALS':`B · ${comparisonFilename}`;
  if($('display').value==='heat') {
    const surface=secondarySurface();
    const primary=getHeatSurface(), shared=viewMode==='versions'||$('scale-mode').value==='shared';
    const domain=shared?Math.max(primary.peakDensity,surface.peakDensity):surface.peakDensity;
    $('secondary-legend-text').textContent=`${shared?'Shared':'Independent'} · ≈${number(Math.round(domain))} / km²`;
    $('secondary-gradient').style.background=`linear-gradient(90deg,${[0,.01,.025,.05,.1,.25,.5,.75,1].map(t=>`${cssColor(color(A.heatTone(t,+$('strength').value),field))} ${t*100}%`).join(',')})`;
    setTicks('secondary-ticks',0,domain);
    if(domain>0) {
      const canvas=document.createElement('canvas');canvas.width=surface.width;canvas.height=surface.height;
      const ctx=canvas.getContext('2d'), pixels=ctx.createImageData(surface.width,surface.height);
      for(let i=0;i<surface.values.length;i++) {
        const t=surface.values[i]/(surface.cellM*surface.cellM)*1e6/domain;
        if(t<=0)continue; const tone=A.heatTone(t,+$('strength').value), rgb=color(tone,field);
        pixels.data[i*4]=rgb[0];pixels.data[i*4+1]=rgb[1];pixels.data[i*4+2]=rgb[2];pixels.data[i*4+3]=Math.round(.98*Math.pow(tone,.65)*255);
      }
      ctx.putImageData(pixels,0,0); secondaryHeat=L.imageOverlay(canvas.toDataURL('image/png'),surface.bounds.map(p=>[p[1],p[0]]),{interactive:false}).addTo(secondaryMap);
    }
  } else {
    let max=0;for(const c of otherBins.cells)max=Math.max(max,c[field]||0);
    if(viewMode==='versions'||$('scale-mode').value==='shared')for(const c of bins.cells)max=Math.max(max,c[layer]||0);
    $('secondary-legend-text').textContent=`${viewMode==='versions'||$('scale-mode').value==='shared'?'Shared':'Independent'} · ${number(max)} per cell`;
    $('secondary-gradient').style.background=`linear-gradient(90deg,${[0,.25,.5,.75,1].map(t=>cssColor(color(A.heatTone(t),field))).join(',')})`;
    setTicks('secondary-ticks',0,max);
    const visible=secondaryMap.getBounds().pad(.1);
    for(const c of otherBins.cells){if(!c[field]||!visible.intersects(L.latLngBounds(c.bounds.map(p=>[p[1],p[0]]))))continue;
      const rect=L.rectangle(c.bounds.map(p=>[p[1],p[0]]),{color:'#fff',opacity:.3,weight:.25,fillColor:cssColor(color(A.heatTone(max?c[field]/max:0),field)),fillOpacity:.9,bubblingMouseEvents:false}).addTo(secondaryGrid);
      rect.bindTooltip(textTooltip([`${number(c.residents)} resident commuters`,`${number(c.jobs)} job arrivals`]));
      rect.on('click',e=>handleMapClick(e.latlng));
    }
  }
  if(selectionGeometry){
    const style={color:'#39b6a1',weight:2,fillOpacity:.06,interactive:false};
    if(selectionGeometry.type==='circle') L.circle([selectionGeometry.center[1],selectionGeometry.center[0]],{...style,radius:selectionGeometry.radiusM}).addTo(secondarySelection);
    else if(selectionGeometry.type==='polygon') L.polygon(selectionGeometry.vertices.map(p=>[p[1],p[0]]),style).addTo(secondarySelection);
    else L.rectangle(selectionGeometry.bounds.map(p=>[p[1],p[0]]),style).addTo(secondarySelection);
  }
}
function refreshViews() {
  drawGrid(); heatLayer.redraw(); renderSecondary();
}
function setViewMode(mode) {
  if(areaPair.enabled)setPairMode(false);
  if(mode==='versions' && !comparison){if(viewMode!=='single')setViewMode('single');$('view-mode').value='single';$('compare-controls').hidden=false;$('comparison-status').textContent='Choose comparison B to enable this view.';return;}
  if(viewMode==='single' && mode!=='single')savedLayer=layer;
  viewMode=mode; $('view-mode').value=mode;
  $('map-label').textContent=mode==='versions'?`A · ${primaryFilename}`:mode==='people-jobs'?'RESIDENT COMMUTERS':'IMPORTED GAME DEMAND';
  $('compare-controls').hidden=mode!=='versions'; $('scale-label').hidden=mode!=='people-jobs';
  $('export-comparison').hidden=mode!=='versions'; $('export-comparison-trips').hidden=mode!=='versions'||!dataset.tripsAvailable||!comparison.tripsAvailable;
  if(mode!=='single') {
    if(['destinations','origins'].includes(tripView)){tripView='off';$('trip-view').value='off';$('trip-results').hidden=true;$('export-trips').hidden=true;}
    layer=mode==='people-jobs'?'residents':($('version-metric').value==='jobs'?'jobs':'residents');
    $('display').value=displayByLayer[layer]; $('display').disabled=false;
    const union=mode==='versions'?[...dataset.points,...comparison.points]:dataset.points;
    const spec=A.createGridSpec(union,+$('cell-size').value); bins=A.grid(dataset.points,+$('cell-size').value,spec);
    comparisonBins=mode==='versions'?A.grid(comparison.points,+$('cell-size').value,spec):null;
  } else {layer=savedLayer; $('display').value=layer==='balance'?'grid':displayByLayer[layer]; $('display').disabled=layer==='balance';bins=A.grid(dataset.points,+$('cell-size').value);}
  document.querySelectorAll('[data-layer]').forEach(b=>{const active=b.dataset.layer===layer;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});
  heatCache.clear();heatSpec=null;
  if(selectionGeometry) {
    selection=A.selectGeometry(dataset.points,selectionGeometry,bins.spec);
    selectionText(selectionGeometry.type==='circle'?`${number(selectionGeometry.radiusM)} m area · straight-line radius`:selectionGeometry.type==='cell'?`${number(selectionGeometry.spec.sizeM)} m analysis cell`:'Selected polygon · approximate area');
  }
  const paired=mode!=='single' && !(mode==='versions' && ($('version-display').value==='difference' || $('version-metric').value==='trips'));
  $('map-secondary').hidden=!paired;
  if(paired)createSecondary();
  requestAnimationFrame(()=>{map.invalidateSize();if(paired){secondaryMap.invalidateSize();secondaryMap.setView(map.getCenter(),map.getZoom(),{animate:false});}refreshViews();});
  if(mode==='versions')drawComparisonResults();
  if(mode!=='versions' || $('version-metric').value!=='trips')drawFlows();
}
function drawComparisonResults() {
  const box=$('comparison-results');box.replaceChildren(); if(!comparison || !comparisonBins)return;
  const metric=$('version-metric').value;
  $('comparison-filter-label').hidden=metric!=='trips';
  const p=document.createElement('p');
  if(metric==='trips') {
    const filter=$('comparison-filter').value;
    if(filter!=='all' && !selectionGeometry){p.textContent='Select an area to filter trips.';box.append(p);return;}
    const selectedA=selectionGeometry?A.selectGeometry(dataset.points,selectionGeometry,bins.spec).ids:null;
    const selectedB=selectionGeometry?A.selectGeometry(comparison.points,selectionGeometry,bins.spec).ids:null;
    const rows=A.compareTrips(dataset,comparison,bins,comparisonBins,selectedA,selectedB,filter);
    if(!rows){p.textContent='Trip comparison unavailable: one map has no trip data.';box.append(p);return;}
    const increased=rows.reduce((s,r)=>s+Math.max(0,r.delta),0), decreased=rows.reduce((s,r)=>s+Math.max(0,-r.delta),0), unchanged=rows.reduce((s,r)=>s+(r.delta===0?r.a:0),0);
    p.textContent=`${filter==='all'?'All trips':filter==='out'?'Origins in selection':'Destinations in selection'}: increased ${number(increased)} · decreased ${number(decreased)} · unchanged ${number(unchanged)} · net ${number(increased-decreased)}${filter==='all'?'':' (including internal trips)'}`;box.append(p);
    const changed=rows.filter(r=>r.delta), page=changed.slice(comparisonPage*20,comparisonPage*20+20);
    for(const row of page) {const button=document.createElement('button'),change=changeStatus(row.a,row.b);button.textContent=`${row.from} → ${row.to}: ${number(row.a)} → ${number(row.b)} (${row.delta>0?'+':''}${number(row.delta)}; ${change.percent===null?'New':change.percent.toFixed(1)+'%'})`;button.onclick=()=>{flowLayer.clearLayers();const coords=[row.fromCenter,row.toCenter].map(p=>[p[1],p[0]]);if(row.from===row.to)L.circleMarker(coords[0],{renderer:flowRenderer,radius:8,color:row.delta>0?'#bc3025':'#3166ad'}).addTo(flowLayer);else {L.polyline(coords,{renderer:flowRenderer,color:row.delta>0?'#bc3025':'#3166ad',weight:4}).addTo(flowLayer);L.circleMarker(coords[1],{renderer:flowRenderer,radius:4,color:'#fff',fillColor:'#17332f',fillOpacity:1}).addTo(flowLayer);}map.fitBounds(coords,{padding:[80,80],maxZoom:15});};box.append(button);}
    const pages=document.createElement('p');pages.textContent=`${number(changed.length)} changed pairs · page ${comparisonPage+1} of ${Math.max(1,Math.ceil(changed.length/20))}.`;box.append(pages);
    if(comparisonPage>0){const b=document.createElement('button');b.textContent='Previous page';b.onclick=()=>{comparisonPage--;drawComparisonResults();};box.append(b);}
    if((comparisonPage+1)*20<changed.length){const b=document.createElement('button');b.textContent='Next page';b.onclick=()=>{comparisonPage++;drawComparisonResults();};box.append(b);}
    const inter=changed.filter(r=>r.from!==r.to), totalAbs=inter.reduce((s,r)=>s+Math.abs(r.delta),0), shown=inter.slice(0,30);
    const note=document.createElement('p');note.textContent=`${number(rows.filter(r=>r.from===r.to).length)} within-cell pairs. Showing ${number(shown.length)} inter-cell changes; ${percent(totalAbs?(totalAbs-shown.reduce((s,r)=>s+Math.abs(r.delta),0))/totalAbs:null)} of absolute inter-cell change omitted.`;box.append(note);
    flowLayer.clearLayers();
    for(const row of shown){const coords=[row.fromCenter,row.toCenter].map(p=>[p[1],p[0]]),color=row.delta>0?'#bc3025':'#3166ad';L.polyline(coords,{renderer:flowRenderer,color,weight:1+4*Math.sqrt(Math.abs(row.delta)/Math.abs(shown[0].delta)),opacity:.7}).addTo(flowLayer);L.circleMarker(coords[1],{renderer:flowRenderer,radius:3,color:'#fff',fillColor:'#17332f',fillOpacity:1}).addTo(flowLayer);addFlowArrow(coords[0],coords[1],color);}
  } else {
    const rows=A.compareCells(bins,comparisonBins);const a=rows.reduce((s,r)=>s+r.a[metric],0),b=rows.reduce((s,r)=>s+r.b[metric],0);
    p.textContent=`A: ${number(a)} · B: ${number(b)} · change: ${b-a>0?'+':''}${number(b-a)}`;box.append(p);
  }
}
async function importComparison(file) {
  if(!file)return; const sequence=++comparisonSequence; $('comparison-status').textContent=`Reading ${file.name}…`;
  try {
    if(file.size>512*1048576)throw new Error('Import limit: 512 MiB. Select demand_data.json directly for larger packages.');
    const candidate=await MapImport.readLocal(file,stage=>{if(sequence===comparisonSequence)$('comparison-status').textContent=stage+' · '+file.name;});
    if(sequence!==comparisonSequence)return;
    const union=[...dataset.points,...candidate.points], spec=A.createGridSpec(union,+$('cell-size').value);
    A.grid(dataset.points,+$('cell-size').value,spec);A.grid(candidate.points,+$('cell-size').value,spec);
    const candidateHeatSpec=A.createHeatSpec(union,+$('smoothing').value);
    A.heatSurface(dataset.points,'residents',+$('smoothing').value,candidateHeatSpec);
    A.heatSurface(candidate.points,'residents',+$('smoothing').value,candidateHeatSpec);
    if(sequence!==comparisonSequence)return;
    comparison=candidate;comparisonFilename=file.name;comparisonBytes=file.size; $('comparison-status').textContent=`B: ${file.name} · ${number(candidate.points.length)} points${candidate.warnings.length?' · '+candidate.warnings.join(' '):''}`;
    setViewMode('versions');requestAnimationFrame(fit);
  } catch(error) {if(sequence===comparisonSequence)$('comparison-status').textContent=`Could not open ${file.name}: ${error.message}${comparison?' Previous B retained.':''}`;}
}
function rebuild(initialHeat = null) {
  if (!dataset) return;
  const previous = selectionGeometry;
  const union=viewMode==='versions' && comparison?[...dataset.points,...comparison.points]:dataset.points;
  const spec=A.createGridSpec(union,+$('cell-size').value);
  bins = A.grid(dataset.points, +$('cell-size').value,spec);
  comparisonBins=viewMode==='versions'&&comparison?A.grid(comparison.points,+$('cell-size').value,spec):null;
  if (previous?.type === 'circle') selectArea(previous.center);
  else if (previous?.type === 'polygon') selectPolygon(previous.vertices);
  else {clearSelection(false);if(previous?.type==='cell')$('inspector-summary').textContent='Cell selection cleared after changing cell size.';}
  if(initialHeat?.surface)heatCache.set(`${initialHeat.field}:${$('smoothing').value}`,initialHeat.surface);
  updateConcentration(); drawScatter(); drawFlows(); drawGrid(); heatLayer.redraw();renderSecondary();if(viewMode==='versions')drawComparisonResults();
}
async function importMap(file) {
  if (!file) return;
  const sequence = ++importSequence;
  let stage='Reading file directory';
  const started=Date.now();
  const showStage=()=>{if(sequence===importSequence)$('status').textContent=`${stage} · ${file.name} · ${Math.floor((Date.now()-started)/1000)} s`;};
  const setStage=value=>{stage=value;showStage();};
  $('status').classList.remove('error');showStage();
  const progressTimer=setInterval(showStage,1000);
  try {
    if (file.size > 512 * 1048576) throw new Error('Import limit: 512 MiB. For larger map packages, select demand_data.json directly.');
    const data=await MapImport.readLocal(file,setStage);
    if(sequence!==importSequence)return;
    setStage('Preparing map and heatmap');
    await new Promise(resolve=>setTimeout(resolve,0));
    if(sequence!==importSequence)return;
    A.grid(data.points,+$('cell-size').value);
    const candidateHeatSpec=A.createHeatSpec(data.points,+$('smoothing').value);
    const initialField=layer==='jobs'?'jobs':'residents';
    const initialHeat=A.heatSurface(data.points,initialField,+$('smoothing').value,candidateHeatSpec);
    if(areaPair.enabled)setPairMode(false);
    areaPair.geometries={a:null,b:null};areaPair.source=null;
    dataset = data;
    primaryFilename=file.name;primaryBytes=file.size; comparison=null;comparisonBins=null;comparisonSequence++; viewMode='single';$('view-mode').value='single';$('map-secondary').hidden=true;$('compare-controls').hidden=true;$('scale-label').hidden=true;
    heatCache.clear(); heatSpec = candidateHeatSpec; selectionGeometry = null;
    $('dataset-name').textContent = data.name === 'Imported map' ? file.name : data.name;
    $('total-residents').textContent = number(data.points.reduce((s, p) => s + p.residents, 0));
    $('total-jobs').textContent = number(data.points.reduce((s, p) => s + p.jobs, 0));
    $('trip-total').textContent = `${number(data.points.length)} points · ${data.tripsAvailable ? `${number(data.pops.reduce((s, p) => s + p.size, 0))} modeled commuters in ${number(data.pops.length)} trip groups` : 'no trip data'}`;
    $('warnings').textContent = data.warnings.join('\n'); $('warnings').hidden = !data.warnings.length;
    $('controls').disabled = false; $('fit-map').disabled = false; $('show-flows').disabled = !data.tripsAvailable;
    for(const option of $('trip-view').options) if(option.value!=='off')option.disabled=!data.tripsAvailable;
    $('trip-view').title=data.tripsAvailable?'Select an area to see origins or destinations.':'Trip data is unavailable in this map.';
    $('empty').hidden = true; $('inspector').hidden = false; $('status').textContent = `Loaded ${file.name}. Click the map to inspect an area.`;
    $('map-label').textContent = 'IMPORTED GAME DEMAND';
    rebuild({field:initialField,surface:initialHeat}); fit();
    $('status').textContent=`Loaded ${file.name} in ${((Date.now()-started)/1000).toFixed(1)} s. Click the map to inspect an area.`;
  } catch (error) {
    if (sequence !== importSequence) return;
    $('status').classList.add('error'); $('status').textContent = `Could not open ${file.name}: ${error.message}${dataset ? ' The previous map remains loaded.' : ''}`;
  } finally {clearInterval(progressTimer);}
}
$('map-file').addEventListener('change', event => { importMap(event.target.files[0]); event.target.value = ''; });
$('open-empty').onclick = () => $('map-file').click();
$('fit-map').onclick = fit;
$('layers').addEventListener('click', event => {
  const button = event.target.closest('[data-layer]'); if (!button || !dataset) return;
  if(viewMode!=='single'){savedLayer=button.dataset.layer;setViewMode('single');}
  if (layer !== 'balance') displayByLayer[layer] = $('display').value;
  layer = button.dataset.layer;
  document.querySelectorAll('[data-layer]').forEach(b => { b.classList.toggle('active', b === button); b.setAttribute('aria-pressed', b === button ? 'true' : 'false'); });
  $('display').value = layer === 'balance' ? 'grid' : displayByLayer[layer];
  $('display').disabled = layer === 'balance';
  drawGrid(); heatLayer.redraw();
});
$('map-theme').onchange=()=>{mapTheme=$('map-theme').value;document.body.dataset.mapTheme=mapTheme;if(dataset)refreshViews();};
$('display').onchange = () => { if (layer !== 'balance') displayByLayer[layer] = $('display').value; refreshViews(); };
$('smoothing').oninput = () => { $('smoothing-value').value = `${number(+$('smoothing').value)} m`; heatCache.clear(); heatSpec=null; scheduleHeat(); };
$('strength').oninput = () => { $('strength-value').value = `${(+$('strength').value).toFixed(1)}×`; scheduleHeat(); requestAnimationFrame(renderSecondary); };
$('cell-size').onchange = rebuild;
$('show-flows').onchange = drawFlows;
$('flow-direction').onchange = drawFlows;
$('radius').onchange = () => { if (selectedLocation) selectArea(selectedLocation); };
$('clear-selection').onclick = clearSelection;
map.on('click', event => handleMapClick(event.latlng));
$('inspector-clear').onclick = clearSelection;
$('inspect-mode').onchange = () => {
  const chosen=$('inspect-mode').value;
  if(polygonDraft.length && chosen!=='polygon') {$('polygon-cancel').click();$('inspect-mode').value=chosen;}
  polygonDraft=[]; $('polygon-controls').hidden=chosen!=='polygon';
};
$('polygon-finish').onclick = () => { if(selectPolygon(polygonDraft)) { polygonDraft=[]; $('inspect-mode').value='circle'; $('polygon-controls').hidden=true; } };
$('polygon-undo').onclick = () => { polygonDraft.pop(); $('polygon-status').textContent=`${polygonDraft.length} vertices`; selectionLayer.clearLayers(); if(polygonDraft.length>1)L.polyline(polygonDraft.map(p=>[p[1],p[0]]),{renderer:selectionRenderer,color:'#39b6a1',interactive:false}).addTo(selectionLayer); };
$('polygon-cancel').onclick = () => { polygonDraft=[]; $('polygon-controls').hidden=true; $('inspect-mode').value='circle'; if(selectionGeometry?.type==='circle') selectArea(selectionGeometry.center); else if(selectionGeometry?.type==='polygon') selectPolygon(selectionGeometry.vertices); else if(selectedCell) selectCell(selectedCell); else selectionLayer.clearLayers(); };
window.addEventListener('keydown', event=>{if(event.key==='Escape' && !$('polygon-controls').hidden) $('polygon-cancel').click();});
$('trip-view').onchange = () => {
  const requested=$('trip-view').value;
  if (viewMode!=='single' && requested!=='off') { $('trip-view').value='off'; $('inspector-summary').textContent='Return to Single map to explore selected trips.'; return; }
  if (requested!=='off' && !dataset.tripsAvailable) { $('trip-view').value='off'; $('inspector-summary').textContent='Trip data is unavailable in this map.'; return; }
  if (['destinations','origins'].includes(requested) && !selection) { $('trip-view').value='off'; $('inspector-summary').textContent='Select an area first.'; return; }
  tripView=requested; tripPage=0; heatCache.clear(); $('show-flows').checked=requested==='links';
  $('export-trips').hidden=!['destinations','origins'].includes(requested);
  drawTripResults(); drawFlows(); drawGrid(); heatLayer.redraw();
};
$('view-mode').onchange=()=>setViewMode($('view-mode').value);
$('scale-mode').onchange=refreshViews;
$('comparison-file').onchange=event=>{importComparison(event.target.files[0]);event.target.value='';};
$('remove-comparison').onclick=()=>{comparison=null;comparisonBins=null;comparisonSequence++;setViewMode('single');};
$('version-metric').onchange=()=>{comparisonPage=0;setViewMode('versions');};
$('version-display').onchange=()=>setViewMode('versions');
$('comparison-filter').onchange=()=>{comparisonPage=0;drawComparisonResults();};
map.on('moveend zoomend', () => { if (dataset) drawGrid(); });
window.addEventListener('resize', () => { if (dataset) drawScatter(); });
$('scatter').onclick = event => {
  const rect = $('scatter').getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top;
  let best = null, distance = 12;
  for (const dot of scatterDots) { const d = Math.hypot(x - dot.x, y - dot.y); if (d < distance) { distance = d; best = dot; } }
  if (best) selectCell(best.cell);
};
$('export-grid').onclick = () => {
  const rows = [['longitude', 'latitude', 'cell_size_m', 'resident_commuters', 'job_arrivals', 'jobs_minus_people', 'balance', 'demand_points'],
    ...bins.cells.map(c => [c.center[0], c.center[1], bins.size, c.residents, c.jobs, c.jobs - c.residents, c.jobs + c.residents ? (c.jobs - c.residents) / (c.jobs + c.residents) : '', c.points])];
  const url = URL.createObjectURL(new Blob([rows.map(row => row.join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = `map-analysis-${bins.size}m.csv`; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

function downloadBlob(blob, name) {
  const url=URL.createObjectURL(blob), anchor=document.createElement('a');
  anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),5000);
}
function csvValue(value) {
  if(value===null || value===undefined)return '';
  let text=String(value);
  if(typeof value==='string' && /^[\s]*[=+\-@]/.test(text))text="'"+text;
  return /[",\r\n]/.test(text)?`"${text.replace(/"/g,'""')}"`:text;
}
function csvDownload(rows,name) {
  downloadBlob(new Blob([rows.map(row=>row.map(csvValue).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}),name);
}
function changeStatus(a,b) {return a ? {percent:100*(b-a)/a,status:b===a?'unchanged':'changed'} : {percent:null,status:b?'new':'unchanged'};}
function selectionRecord() {
  if(!selection)return null;
  const {ids,...summary}=selection;
  return {geometry:selectionGeometry,summary};
}
$('export-analysis').onclick=()=>{
  const bounds=map.getBounds();
  const data={schemaVersion:1,createdAt:new Date().toISOString(),
    sources:{primary:{name:dataset.name,filename:primaryFilename,bytes:primaryBytes,warnings:dataset.warnings},comparison:comparison?{name:comparison.name,filename:comparisonFilename,bytes:comparisonBytes,warnings:comparison.warnings}:null},
    settings:{mapTheme:$('map-theme').value,contrastCurve:'log1p-32',viewMode,layer,display:$('display').value,cellSizeM:bins.size,smoothingM:+$('smoothing').value,contrast:+$('strength').value,scaleMode:viewMode==='single'||viewMode==='versions'&&$('version-metric').value==='trips'?'independent':viewMode==='versions'&&$('version-display').value==='difference'?'symmetric':viewMode==='versions'?'shared':$('scale-mode').value,tripView,versionMetric:$('version-metric').value,versionDisplay:$('version-display').value},
    gridSpec:bins.spec,viewBounds:[[bounds.getWest(),bounds.getSouth()],[bounds.getEast(),bounds.getNorth()]],selection:areaPair.enabled?null:selectionRecord(),areaPair:areaPair.enabled?pairRecord():null,
    totals:{residents:dataset.points.reduce((s,p)=>s+p.residents,0),jobs:dataset.points.reduce((s,p)=>s+p.jobs,0),trips:dataset.tripsAvailable?dataset.pops.reduce((s,p)=>s+p.size,0):null},
    comparisonTotals:comparison?{residents:comparison.points.reduce((s,p)=>s+p.residents,0),jobs:comparison.points.reduce((s,p)=>s+p.jobs,0),trips:comparison.tripsAvailable?comparison.pops.reduce((s,p)=>s+p.size,0):null}:null};
  if(selection && ['destinations','origins'].includes(tripView)) {
    const trips=A.selectedTrips(dataset,selection.ids,tripView==='destinations'?'out':'in',bins);
    data.selectedTrips={direction:tripView,total:trips.total,insideSelection:trips.insideSelection,outsideSelection:trips.outsideSelection};
  }
  downloadBlob(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),'map-analysis.json');
};
$('export-trips').onclick=()=>{
  if(!selection || !['destinations','origins'].includes(tripView))return;
  const result=A.selectedTrips(dataset,selection.ids,tripView==='destinations'?'out':'in',bins);
  csvDownload([['direction','cell_key','longitude','latitude','commuters','share','total'],...result.rankedCells.map(r=>[tripView,r.key,...r.center,r.count,r.share,result.total])],'selected-trips.csv');
};
$('export-comparison').onclick=()=>{
  if(!comparisonBins)return;
  const rows=A.compareCells(bins,comparisonBins);
  csvDownload([['cell_key','longitude','latitude','cell_size_m','people_a','people_b','people_delta','people_percent','people_status','jobs_a','jobs_b','jobs_delta','jobs_percent','jobs_status'],
    ...rows.map(r=>{const p=changeStatus(r.a.residents,r.b.residents),j=changeStatus(r.a.jobs,r.b.jobs);return [r.key,...r.center,bins.size,r.a.residents,r.b.residents,r.b.residents-r.a.residents,p.percent,p.status,r.a.jobs,r.b.jobs,r.b.jobs-r.a.jobs,j.percent,j.status];})],'comparison-cells.csv');
};
$('export-comparison-trips').onclick=()=>{
  if(!comparisonBins)return; const filter=$('comparison-filter').value;
  if(filter!=='all'&&!selectionGeometry){$('comparison-status').textContent='Select an area to export filtered trips.';return;}
  const idsA=selectionGeometry?A.selectGeometry(dataset.points,selectionGeometry,bins.spec).ids:null, idsB=selectionGeometry?A.selectGeometry(comparison.points,selectionGeometry,bins.spec).ids:null;
  const rows=A.compareTrips(dataset,comparison,bins,comparisonBins,idsA,idsB,filter);if(!rows)return;
  csvDownload([['origin_cell','destination_cell','origin_longitude','origin_latitude','destination_longitude','destination_latitude','a','b','delta','percent','status'],
    ...rows.map(r=>{const change=changeStatus(r.a,r.b);return [r.from,r.to,...r.fromCenter,...r.toCenter,r.a,r.b,r.delta,change.percent,change.status];})],'comparison-trips.csv');
};
// Freeze visible overlay layers before asynchronous image decoding. Street tiles are deliberately omitted.
async function exportDemandImage(includeTiles = false) {
  const button=$('export-image'), tileButton=$('export-image-tiles');button.disabled=true;tileButton.disabled=true;
  try {
    const darkBackground=$('map-theme').value==='dark', background=darkBackground?'#0b121b':'#f4f2e9';
    const tileOpacity=darkBackground?.65:.48, tileFilter=darkBackground?'grayscale(1) invert(1) brightness(.55) contrast(.85)':'grayscale(1) contrast(.75) brightness(1.12)';
    const difference=viewMode==='versions' && $('version-display').value==='difference' && $('version-metric').value!=='trips';
    const isBalance=layer==='balance' && !['destinations','origins'].includes(tripView);
    const heat=(!difference && !isBalance && ($('display').value==='heat'||['destinations','origins'].includes(tripView)));
    const makeGradient=field=>Array.from({length:101},(_,i)=>{
      const t=i/100;
      if(difference) {const a=t<.5?[49,102,173]:[247,247,239],b=t<.5?[247,247,239]:[188,48,37],f=t<.5?t*2:(t-.5)*2;return a.map((v,k)=>Math.round(v+(b[k]-v)*f));}
      return color(A.heatTone(t,heat?+$('strength').value:1),field);
    });
    const nodes=[{element:$('map'),name:viewMode==='people-jobs'?'People':viewMode==='versions'?(difference?'Change · B minus A':'Baseline A'):'Demand',legend:[$('legend-title').textContent,$('legend-low').textContent,$('legend-high').textContent],gradient:makeGradient(layer),balance:isBalance}];
    if(!$('map-secondary').hidden) {
      const ticks=[...$('secondary-ticks').children].map(node=>node.textContent);
      nodes.push({element:$('map-secondary'),name:$('secondary-title').innerText,legend:[$('secondary-legend-text').textContent,ticks[0]||'0',ticks.at(-1)||'0'],gradient:makeGradient(viewMode==='people-jobs'?'jobs':layer)});
    }
    const layout=$('map-layout').getBoundingClientRect(), scale=Math.min(1,2400/layout.width);
    const sections=nodes.map(({element,name,legend,gradient,balance})=>{
      const r=element.getBoundingClientRect(), layers=[];
      if(includeTiles) {
        const tiles=[...element.querySelectorAll('.leaflet-tile-pane img.leaflet-tile')].map(item=>{const box=item.getBoundingClientRect();return {item,box,x:box.left-r.left,y:box.top-r.top};})
          .filter(tile=>tile.x<r.width&&tile.y<r.height&&tile.x+tile.box.width>0&&tile.y+tile.box.height>0);
        if(!tiles.length || tiles.some(tile=>!tile.item.complete || !tile.item.naturalWidth))throw new Error('Street tiles are incomplete. Use the demand-only PNG.');
        for(let y=0;y<r.height;y+=32)for(let x=0;x<r.width;x+=32)if(!tiles.some(tile=>x>=tile.x&&y>=tile.y&&x<tile.x+tile.box.width&&y<tile.y+tile.box.height))throw new Error('Street tiles are incomplete. Use the demand-only PNG.');
        for(const tile of tiles)layers.push({source:tile.item.src,x:tile.x,y:tile.y,width:tile.box.width,height:tile.box.height,tile:true});
      }
      for(const item of element.querySelectorAll('.leaflet-overlay-pane img.leaflet-image-layer, .leaflet-overlay-pane canvas, .leaflet-overlay-pane svg, .leaflet-pane:not(.leaflet-tile-pane) canvas, .leaflet-pane:not(.leaflet-tile-pane) svg')) {
        const box=item.getBoundingClientRect();if(!box.width||!box.height)continue;
        let source;
        if(item.tagName.toLowerCase()==='canvas'){const copy=document.createElement('canvas');copy.width=item.width;copy.height=item.height;copy.getContext('2d').drawImage(item,0,0);source=copy.toDataURL('image/png');}
        else if(item.tagName.toLowerCase()==='svg'){const copy=item.cloneNode(true);copy.setAttribute('xmlns','http://www.w3.org/2000/svg');source='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(copy));}
        else source=item.src;
        layers.push({source,x:box.left-r.left,y:box.top-r.top,width:box.width,height:box.height});
      }
      return {name,legend,gradient,balance,x:r.left-layout.left,y:r.top-layout.top,width:r.width,height:r.height,layers};
    });
    const metadata=[`A: ${dataset.name} (${primaryFilename})${comparison && viewMode==='versions'?` | B: ${comparison.name} (${comparisonFilename})`:''}`,
      `View: ${viewMode}; layer: ${layer}; ${viewMode==='versions' && ($('version-display').value==='difference'||$('version-metric').value==='trips')?'B minus A; ':''}background: ${$('map-theme').value}; enhanced contrast; cell: ${bins.size} m; smoothing: ${$('smoothing').value} m; contrast: ${$('strength').value}; scale: ${difference?'symmetric':viewMode==='single'||viewMode==='versions'&&$('version-metric').value==='trips'?'independent':viewMode==='versions'?'shared':$('scale-mode').value}`,
      selection?`Selection: ${number(selection.residents)} resident commuters; ${number(selection.jobs)} job arrivals; ${(selection.areaM2/1e6).toFixed(2)} km²`:'No area selected',
      `${includeTiles?'Street background: © OpenStreetMap contributors':'Street background omitted'} · ${new Date().toLocaleString()}`];
    if(areaPair.enabled)metadata.push(...pairLines());
    if(viewMode==='versions' && $('version-metric').value==='trips')metadata.push('Trip changes: red increased, blue decreased; arrows show direction.');
    if(viewMode==='versions' && selectionGeometry && comparison) {const b=A.selectGeometry(comparison.points,selectionGeometry,bins.spec);metadata.push(`Selection B: ${number(b.residents)} resident commuters; ${number(b.jobs)} job arrivals.`);}
    const canvas=document.createElement('canvas');canvas.width=Math.round(layout.width*scale);canvas.height=Math.round(layout.height*scale)+Math.max(120,metadata.length*22+12);
    const ctx=canvas.getContext('2d');ctx.fillStyle='#f4f2e9';ctx.fillRect(0,0,canvas.width,canvas.height);ctx.save();ctx.scale(scale,scale);
    for(const section of sections) {
      ctx.save();ctx.beginPath();ctx.rect(section.x,section.y,section.width,section.height);ctx.clip();ctx.fillStyle=background;ctx.fillRect(section.x,section.y,section.width,section.height);
      for(const layer of section.layers) {
        const image=new Image();if(layer.tile)image.crossOrigin='anonymous';image.src=layer.source;await image.decode();
        ctx.save();if(layer.tile){ctx.globalAlpha=tileOpacity;ctx.filter=tileFilter;}
        ctx.drawImage(image,section.x+layer.x,section.y+layer.y,layer.width,layer.height);ctx.restore();
      }
      ctx.restore();ctx.fillStyle=darkBackground?'#e1f8ff':'#17332f';ctx.font='bold 14px Segoe UI';ctx.fillText(section.name,section.x+10,section.y+20);
      ctx.font='11px Segoe UI';const width=Math.max(20,Math.min(250,section.width-20)), left=section.x+10, top=section.y+section.height-64;
      ctx.fillStyle='#f7f7ef';ctx.fillRect(left-5,top-14,width+10,65);ctx.fillStyle='#17332f';ctx.fillText(section.legend[0],left,top,width);
      if(section.balance){
        for(const [index,fraction] of [.01,.1,1].entries())for(const [column,value] of [-1,0,1].entries()){ctx.globalAlpha=.20+.73*Math.sqrt(fraction);ctx.fillStyle=balanceColor(value);ctx.fillRect(left+40+column*40,top+3+index*9,35,8);}
        ctx.globalAlpha=1;ctx.fillStyle='#17332f';ctx.fillText('1%',left,top+11);ctx.fillText('10%',left,top+20);ctx.fillText('100%',left,top+29);
      } else {
        for(let i=0;i<section.gradient.length;i++){ctx.fillStyle=cssColor(section.gradient[i]);ctx.fillRect(left+i*width/101,top+7,width/101+1,8);}
        ctx.fillStyle='#17332f';if(section.legend.length>1){ctx.fillText(section.legend[1],left,top+29,width/2);ctx.textAlign='right';ctx.fillText(section.legend[2],left+width,top+29,width/2);ctx.textAlign='left';}
      }
    }
    ctx.restore();ctx.fillStyle='#17332f';ctx.font='12px Segoe UI';metadata.forEach((line,i)=>ctx.fillText(line,10,Math.round(layout.height*scale)+20+i*22,canvas.width-20));
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('Could not render the PNG.');downloadBlob(blob,'map-analysis.png');
  } catch(error) {$('status').textContent=`PNG export failed: ${error.message}${includeTiles?' Use the demand-only PNG if street tiles cannot be exported.':''}`;$('status').classList.add('error');}
  finally {button.disabled=false;tileButton.disabled=false;}
}
$('export-image').onclick=()=>exportDemandImage(false);
$('export-image-tiles').onclick=()=>exportDemandImage(true);

// Pair geometry is independent of the single-area inspector and analysis grid.
const areaPair={enabled:false,source:null,geometries:{a:null,b:null},active:'a',draft:[],result:null,memberships:{},radius:800};
const pairLayer=L.layerGroup().addTo(map);
function pairLines(){const r=areaPair.result;if(!r)return ['Select both areas'];if(!r.available)return ['Trip data unavailable in this map.'];
  const pct=x=>x===null?'N/A':percent(x);
  return [['ab','A','B','outA','inB'],['ba','B','A','outB','inA']].map(([key,a,b,out,into])=>`Area ${a} → Area ${b}: ${number(r[key].count)} modeled commuters; ${pct(r[key].originShare)} of origins (${r[key].count}/${r.totals[out]}); ${pct(r[key].destinationShare)} of arrivals (${r[key].count}/${r.totals[into]})`).concat([r.sharedPointCount?`Areas share ${r.sharedPointCount} demand points; ${r.sharedDirectionalWeight} commuters occur in both directions. Shared points remain included.`:'No shared demand points']);}
function pairRecord(){return {geometries:areaPair.geometries,highlightDirection:$('pair-direction').value,result:areaPair.result?JSON.parse(JSON.stringify(areaPair.result,(k,v)=>v instanceof Map?undefined:v)):null};}
function renderPair(recalculate=false){if(!areaPair.enabled)return;pairLayer.clearLayers();
  if(recalculate){for(const key of ['a','b'])areaPair.memberships[key]=areaPair.geometries[key]?A.selectGeometry(dataset.points,areaPair.geometries[key],areaPair.spec):null;
    areaPair.result=areaPair.memberships.a&&areaPair.memberships.b?A.areaPairDemand(dataset,areaPair.memberships.a.ids,areaPair.memberships.b.ids):null;}
  for(const key of ['a','b']){const g=areaPair.geometries[key];if(!g)continue;const options={renderer:selectionRenderer,color:key==='a'?'#39b6a1':'#80abff',dashArray:key==='b'?'7 5':null,weight:3,fillOpacity:.04,interactive:false};
    const shape=g.type==='circle'?L.circle([g.center[1],g.center[0]],{...options,radius:g.radiusM}):L.polygon(g.vertices.map(p=>[p[1],p[0]]),options);
    shape.addTo(pairLayer).bindTooltip(`Area ${key.toUpperCase()}`,{permanent:true,direction:key==='a'?'left':'right'});}
  const r=areaPair.result,dir=r?.[$('pair-direction').value];
  if(dir&&r.available){const max=Math.max(1,...dir.originWeights.values(),...dir.destinationWeights.values()),visible=map.getBounds().pad(.1);
    for(const [weights,color,role] of [[dir.originWeights,'#e86e23','home'],[dir.destinationWeights,'#7835b5','work']])for(const [id,weight] of weights){const p=areaPair.lookup.get(id),ll=[p.location[1],p.location[0]];if(!visible.contains(ll))continue;
      L.circleMarker(ll,{renderer:selectionRenderer,radius:3+9*Math.sqrt(weight/max),color,fillColor:color,fillOpacity:role==='home'?.65:.1,weight:role==='home'?1:3,interactive:false}).addTo(pairLayer);}}
  if(areaPair.draft.length>1)L.polyline(areaPair.draft.map(p=>[p[1],p[0]]),{renderer:selectionRenderer,color:'#16756d',interactive:false}).addTo(pairLayer);
  const lines=[`Editing Area ${areaPair.active.toUpperCase()} · A: ${areaPair.memberships.a?.points??0} points · B: ${areaPair.memberships.b?.points??0} points`,...pairLines(),`Highlight: ${$('pair-direction').selectedOptions[0].textContent}; orange homes, purple workplaces; marker size increases with count.`, 'Imported home-to-work groups; opposite direction is not a return trip. These are not predicted subway riders.'];
  if(areaPair.draft.length)lines.unshift(`Uncommitted polygon: ${areaPair.draft.length} vertices`);
  $('pair-results').replaceChildren(textTooltip(lines));$('export-trips').hidden=true;
}
function pairCommit(g){const key=areaPair.active;areaPair.geometries[key]=g;areaPair.draft=[];if(key==='a'&&!areaPair.geometries.b)areaPair.active='b';$('pair-target').value=areaPair.active;syncPairRadius();renderPair(true);}
function syncPairRadius(){$('pair-radius').value=areaPair.geometries[areaPair.active]?.radiusM||areaPair.radius;}
function pairClick(ll){if($('inspect-mode').value==='polygon'){areaPair.draft.push([ll.lng,ll.lat]);renderPair();}else pairCommit({type:'circle',center:[ll.lng,ll.lat],radiusM:+$('pair-radius').value});}
function setPairMode(enabled){if(enabled&&(!dataset||viewMode!=='single')){$('analysis-mode').value='single';$('status').textContent='Use Single map for area-to-area analysis.';return;}
  areaPair.enabled=enabled;areaPair.draft=[];$('analysis-mode').value=enabled?'pair':'single';$('pair-controls').hidden=!enabled;
  for(const id of ['inspector-summary','inspector-clear','trip-results','clear-selection','selection'])$(id).hidden=enabled;
  $('trip-view').parentElement.hidden=enabled;$('show-flows').disabled=enabled||!dataset?.tripsAvailable;$('flow-direction').disabled=enabled;$('scatter').style.pointerEvents=enabled?'none':'';$('scatter').title=enabled?'Single-area selection is disabled during area-to-area analysis.':'';
  $('inspect-mode').querySelector('[value="cell"]').disabled=enabled;if(enabled&&$('inspect-mode').value==='cell')$('inspect-mode').value='circle';
  if(enabled){areaPair.savedTrip=tripView;tripView='off';selectionLayer.clearLayers();endpointLayer.clearLayers();flowLayer.clearLayers();
    if(areaPair.source!==dataset){areaPair.source=dataset;areaPair.spec=A.createGridSpec(dataset.points,500);areaPair.lookup=new Map(dataset.points.map(p=>[p.id,p]));}renderPair(true);
  }else{pairLayer.clearLayers();tripView=areaPair.savedTrip||'off';if(selectionGeometry?.type==='circle')selectArea(selectionGeometry.center);else if(selectionGeometry?.type==='polygon')selectPolygon(selectionGeometry.vertices);else if(selectedCell)selectCell(selectedCell);}
  heatCache.clear();drawGrid();heatLayer.redraw();
}
$('analysis-mode').onchange=()=>setPairMode($('analysis-mode').value==='pair');
$('pair-target').onchange=()=>{areaPair.active=$('pair-target').value;areaPair.draft=[];syncPairRadius();renderPair();};
$('pair-direction').onchange=()=>renderPair();
$('pair-radius').onchange=()=>{const value=+$('pair-radius').value;if(!Number.isFinite(value)||value<50||value>50000){syncPairRadius();return;}areaPair.radius=value;const g=areaPair.geometries[areaPair.active];if(g?.type==='circle'){g.radiusM=value;renderPair(true);}};
for(const key of ['a','b'])$('pair-clear-'+key).onclick=()=>{areaPair.geometries[key]=null;areaPair.draft=[];renderPair(true);};
$('pair-swap').onclick=()=>{const g=areaPair.geometries;[g.a,g.b]=[g.b,g.a];areaPair.draft=[];syncPairRadius();renderPair(true);};
for(const id of ['polygon-finish','polygon-undo','polygon-cancel','inspect-mode']){const event=id==='inspect-mode'?'onchange':'onclick',previous=$(id)[event];$(id)[event]=()=>{if(!areaPair.enabled)return previous();
  if(id==='polygon-finish'){const info=A.polygonInfo(areaPair.draft,areaPair.spec);if(info.error){$('polygon-status').textContent=info.error;return;}pairCommit({type:'polygon',vertices:info.ring});}
  else if(id==='polygon-undo')areaPair.draft.pop();else areaPair.draft=[];
  $('polygon-controls').hidden=$('inspect-mode').value!=='polygon';renderPair();};}
map.on('moveend',()=>renderPair());
const singleRebuild=rebuild;rebuild=function(initialHeat){if(!areaPair.enabled)return singleRebuild(initialHeat);bins=A.grid(dataset.points,+$('cell-size').value,A.createGridSpec(dataset.points,+$('cell-size').value));updateConcentration();drawGrid();heatLayer.redraw();drawScatter();renderPair();};$('cell-size').onchange=rebuild;
