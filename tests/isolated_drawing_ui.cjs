const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const events={},winEvents={},container={style:{}},classes=new Set(['hidden']);let completed=null;
const map={on:(n,f)=>events[n]=f,dragging:{enable(){}},doubleClickZoom:{disable(){this.disabled=true},enable(){this.disabled=false}},getContainer:()=>container,removeLayer(){}};
const ctx={mapBbox:map,currentCityFile:'test.yaml',isDrawingUrbanCore:false,isDrawingExclusion:false,document:{getElementById:()=>({classList:{remove:n=>classes.delete(n),add:n=>classes.add(n)}})},window:{addEventListener:(n,f)=>winEvents[n]=f},showToast(){},setMapBboxDrawingInteractivity(){},openIsolatedZoneModal:(i,b,p)=>completed=p,L:{DomEvent:{stop(){}},polygon:()=>({addTo(){return this},setLatLngs(){}})}};
vm.createContext(ctx);
for(const name of ['isDrawingIsolated','drawTempRect','isolatedPolygonPoints']) {
 const declaration=source.match(new RegExp('    let '+name+' = [^;]+;'));assert(declaration);vm.runInContext(declaration[0],ctx);
}
for(const name of ['startDrawingIsolatedZone','cancelDrawingIsolatedZone','setupIsolatedDrawingEvents','finishIsolatedPolygonDrawing','isolatedZoneBounds','validIsolatedPolygon']) vm.runInContext(source.match(new RegExp('    function '+name+'\\(.*?\\n    }','s'))[0],ctx);
ctx.setupIsolatedDrawingEvents();ctx.startDrawingIsolatedZone();assert(map.doubleClickZoom.disabled);assert.equal(container.style.cursor,'crosshair');assert(!classes.has('hidden'));
const click=(lng,lat)=>events.click({latlng:{lng,lat}});
click(-87,20);click(-86.9,20);winEvents.keydown({key:'Enter'});assert.equal(completed,null,'two vertices cannot finish');
click(-86.9,20.1);winEvents.keydown({key:'Backspace',preventDefault(){}});winEvents.keydown({key:'Enter'});assert.equal(completed,null);
click(-87,20.1);winEvents.keydown({key:'Enter'});assert.equal(JSON.stringify(completed),JSON.stringify([[-87,20],[-86.9,20],[-87,20.1],[-87,20]]));assert(!map.doubleClickZoom.disabled);
completed=null;ctx.startDrawingIsolatedZone();click(0,0);click(1,0);click(0,1);click(0,1);events.dblclick({});assert.equal(completed.length,4,'double-click duplicate suppressed');
assert.equal(JSON.stringify(ctx.isolatedZoneBounds({polygon:completed})),JSON.stringify([0,0,1,1]));
ctx.startDrawingIsolatedZone();winEvents.keydown({key:'Escape'});assert(!map.doubleClickZoom.disabled);assert(classes.has('hidden'));
console.log('Isolated polygon: vertices, minimum, undo, Enter/double click, bounds, Escape: PASS');

assert(!ctx.validIsolatedPolygon([[0,0],[1,1],[0,1],[1,0]]));
assert(ctx.validIsolatedPolygon([[0,0],[2,0],[2,1],[1,1],[1,2],[0,2]]));
const controls=new Map();
const element=id=>{if(!controls.has(id))controls.set(id,{value:'',classList:{toggle(){},remove(){},add(){}},focus(){}});return controls.get(id)};
ctx.document.getElementById=element;ctx.cityData={isolated_zones:[]};ctx.lucide={createIcons(){}};ctx.setTimeout=f=>f();ctx.triggerAutoSave=()=>{};ctx.renderIsolatedZones=()=>{};ctx.selectedIsolatedZoneIndex=-1;
vm.runInContext(source.match(/    let isolatedPolygonDraft = [^;]+;/)[0],ctx);
for(const name of ['openIsolatedZoneModal','submitIsolatedZoneModal','closeIsolatedZoneModal'])vm.runInContext(source.match(new RegExp('    function '+name+'\\(.*?\\n    }','s'))[0],ctx);
const polygon=[[0,0],[2,0],[0,2],[0,0]];
map.getCenter=()=>({lng:0,lat:0});
ctx.openIsolatedZoneModal(-1,null,polygon);ctx.submitIsolatedZoneModal();assert.equal(ctx.cityData.isolated_zones.length,1);assert(!('bbox' in ctx.cityData.isolated_zones[0]));assert.equal(JSON.stringify(ctx.cityData.isolated_zones[0].polygon),JSON.stringify(polygon));
ctx.openIsolatedZoneModal(0);element('iso_name').value='Renamed';ctx.submitIsolatedZoneModal();assert.equal(ctx.cityData.isolated_zones[0].name,'Renamed');assert(!('bbox' in ctx.cityData.isolated_zones[0]));
ctx.openIsolatedZoneModal(0);element('iso_polygon').value='[[0,0],[1,1],[0,1],[1,0]]';ctx.submitIsolatedZoneModal();assert.equal(JSON.stringify(ctx.cityData.isolated_zones[0].polygon),JSON.stringify(polygon));
console.log('Isolated polygon modal: save, rename, geometry preservation, crossed-contour rejection: PASS');

ctx.cityData.isolated_zones[0].enabled=false;ctx.openIsolatedZoneModal(0);ctx.submitIsolatedZoneModal();assert.equal(ctx.cityData.isolated_zones[0].enabled,false);
