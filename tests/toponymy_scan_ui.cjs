const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const names = ['setToponymyScanStatus','cancelToponymyScan','toponymyNameKey','sameToponymyPlace',
  'mergeScannedToponymy','toponymyScanSummary','toponymyInputsSignature','ensureToponymyPlaceIds',
  'runToponymyScan','quickScanAndPopulateToponymy','openScanToponymyDialog','executeCityToponymyScan',
  'placeSourceLabel','prepareScanReview','scanReviewFiltered','scanImportPreview','toggleScanReviewCandidate',
  'selectScanReviewFiltered','loadMoreScanReview','renderScanReview','renderScanResultsUI',
  'importScannedPlaces','undoPlacesLastAction','applyZoneThinningSelections','recalculateZoneClusters'];
const functions = names.map(name=>{
  const match=source.match(new RegExp('    (?:async )?function '+name+'\\(.*?\\n    }','s'));
  assert(match,name);return match[0];
}).join('\n');
const place={id:'stable',name:'Los Pinos',loc:[-100.2,25.7],type:'suburb',source:'INEGI_DENUE',cve_mun:'19039',
  municipality:'Monterrey',establishments:12,aliases:['Fraccionamiento Los Pinos']};
const catalog={total:1,places:[place],sources:[{kind:'DENUE',path:'data/denue19.csv',status:'ok'}],
  coverage:[{municipality:'Monterrey'}],preserved:0,partial:false};
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=()=>new Promise(r=>setImmediate(r));
function harness(){
  const controls=new Map(),timers=new Map(),toasts=[];let saves=0,requests=0,now=0,mode='merge';
  const element=id=>{
    if(!controls.has(id))controls.set(id,{value:id==='scanMinCount'?'8':id==='scanReviewStatus'?'new':'',
      hidden:true,disabled:false,checked:false,dataset:{},textContent:'',innerHTML:'',
      setAttribute(){},classList:{add(){},remove(){},contains(){return true;}}});
    return controls.get(id);
  };
  const c={console,AbortController,crypto:require('node:crypto'),currentCityFile:'monterrey.yaml',cityLoadVersion:1,
    cityData:{places:[],deleted_places:[]},document:{getElementById:element,querySelector:()=>({value:mode})},
    Date:{now:()=>now},lucide:{createIcons(){}},setInterval(fn){timers.set(1,fn);return 1;},
    clearInterval(id){timers.delete(id);},saveCurrentCity:async()=>{saves++;return true;},
    fetchStartupJson:async()=>{requests++;return {catalog};},showToast(message,type){toasts.push({message,type});},
    updateUndoButtonUI(){},renderPlacesList(){},renderPlacesMarkersOnMap(){},closeScanToponymyDialog(){},
    isMicroPlace:p=>p.is_micro,selectedPlaceIndices:new Set(),triggerAutoSave(){},renderToponymyStudioTable(){},
    closeZoneThinningModal(){},lastSelectedPlaceIndex:null,placesHistorySnapshot:null,updateZoneMetricsUI(){},
    renderZoneClustersUI(){},renderZoneClustersOnMap(){},currentDeckIndex:0,
    escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')};
  vm.createContext(c);vm.runInContext(
    'let toponymyScanController=null, scannedPlacesContext=null, scannedPlacesCatalog=null;'+
    'let scanReviewRows=[], scanReviewSelected=new Set(), scanReviewLimit=60, scanImportSaving=false;'+
    'let activeZoneClustersData=null,activeZoneClustersContext=null,zoneCalculationVersion=0;\n'+functions,c);
  return {c,element,toasts,timers,get saves(){return saves;},get requests(){return requests;},setMode(v){mode=v;},
    advance(s){now+=s*1000;for(const f of timers.values())f();},
    setZones(){vm.runInContext('activeZoneClustersData={zones:[]};activeZoneClustersContext={file:currentCityFile,'+
      'version:cityLoadVersion,snapshot:JSON.stringify(cityData.places)}',c);}};
}
(async()=>{
  {
    const h=harness();assert.notEqual(h.c.toponymyNameKey('Peña'),h.c.toponymyNameKey('Pea'));
    assert.equal(h.c.toponymyNameKey('SM 9A'),h.c.toponymyNameKey('Supermanzana 9A'));
    assert.notEqual(h.c.toponymyNameKey('Región 9A'),h.c.toponymyNameKey('Supermanzana 9A'));
    assert.notEqual(h.c.toponymyNameKey('9A'),h.c.toponymyNameKey('9B'));
    const far={...place,id:'far',loc:[-101.2,25.7]};
    assert.equal(h.c.sameToponymyPlace(place,far),false);
    assert.equal(h.c.sameToponymyPlace(place,{...place,cve_mun:'05030'}),false);
    const merged=h.c.mergeScannedToponymy([{...place,name:'Editado',establishments:0}],[place,far]);
    assert.equal(merged.length,2);assert.equal(merged[0].name,'Editado');assert.equal(merged[0].establishments,12);
    h.c.cityData.deleted_places=[place];assert.equal(h.c.mergeScannedToponymy([],[place,far]).length,1);
  }
  {
    const h=harness(),network=defer();let queries=0;h.c.fetchStartupJson=async()=>{queries++;return network.promise;};
    const pending=h.c.executeCityToponymyScan();await tick();h.advance(15);
    assert.match(h.element('toponymyScanStatus').textContent,/15 s/);assert.equal(h.element('btnScanPlaces').disabled,true);
    await h.c.executeCityToponymyScan();assert.equal(queries,1);network.resolve({catalog});await pending;
    assert.equal(h.c.cityData.places.length,0,'scan must not import');assert.equal(h.toasts.length,0);
    assert.equal(h.timers.size,0);assert.match(h.element('scanImportDelta').textContent,/1 nuevos/);
    const save=defer();h.c.saveCurrentCity=async()=>save.promise;
    const importing=h.c.importScannedPlaces();await tick();assert.equal(h.toasts.length,0,'success must await save');
    assert.equal(h.c.cityData.places[0].establishments,12);await h.c.importScannedPlaces();
    save.resolve(true);await importing;assert.equal(h.toasts[0].type,'success');
  }
  {
    const h=harness();let opened=0;h.c.openScanToponymyDialog=()=>opened++;
    await h.c.quickScanAndPopulateToponymy();assert.equal(opened,1);assert.equal(h.c.cityData.places.length,0);
  }
  for(const change of ['project','area']){
    const h=harness(),network=defer();h.c.fetchStartupJson=async()=>network.promise;
    const pending=h.c.executeCityToponymyScan();await tick();
    if(change==='project'){h.c.cancelToponymyScan();h.c.currentCityFile='saltillo.yaml';h.c.cityLoadVersion++;}
    else h.c.cityData.city={bbox:[-90,20,-89,21]};
    network.resolve({catalog});await pending;assert.equal(h.c.cityData.places.length,0);
  }
  {
    const h=harness();await h.c.executeCityToponymyScan();h.c.currentCityFile='saltillo.yaml';h.c.cityLoadVersion++;
    await h.c.importScannedPlaces();assert.equal(h.c.cityData.places.length,0);
  }
  {
    const h=harness();await h.c.executeCityToponymyScan();h.setMode('replace');
    h.c.cityData.places=[{...place,name:'Edición posterior'}];await h.c.importScannedPlaces();
    assert.equal(h.c.cityData.places[0].name,'Edición posterior');assert.match(h.element('toponymyScanStatus').textContent,/cambió/);
  }
  {
    const h=harness();h.c.fetchStartupJson=async()=>({catalog:{...catalog,partial:true,sources:[{kind:'OSM',status:'error',message:'Tiempo agotado'}]}});
    await h.c.executeCityToponymyScan();await h.c.importScannedPlaces();assert.equal(h.toasts[0].type,'warning');
    assert.match(h.element('toponymyScanStatus').textContent,/Resultado parcial/);
  }
  {
    const h=harness(),network=defer();h.c.fetchStartupJson=async()=>network.promise;
    h.c.cityData.places=[{name:place.name,loc:place.loc,type:place.type}];
    const pending=h.c.executeCityToponymyScan();await tick();const id=h.c.cityData.places[0].id;
    h.c.cityData.places[0].name='Nombre editado';network.resolve({catalog:{...catalog,places:[{...place,id}]}});
    await pending;await h.c.importScannedPlaces();assert.equal(h.c.cityData.places.length,1);
    assert.equal(h.c.cityData.places[0].name,'Nombre editado');
  }
  {
    const h=harness();await h.c.executeCityToponymyScan();h.c.saveCurrentCity=async()=>false;
    await h.c.importScannedPlaces();assert.equal(h.toasts[0].type,'error');assert.match(h.element('toponymyScanStatus').textContent,/borrador/);
  }
  {
    const h=harness();h.c.saveCurrentCity=async()=>false;await h.c.executeCityToponymyScan();assert.equal(h.requests,0);
  }
  {
    const h=harness();h.c.fetchStartupJson=async()=>({catalog:{...catalog,places:[{...place,loc:[NaN,25.7]}]}});
    await h.c.executeCityToponymyScan();assert.equal(h.toasts[0].type,'error');
  }
  {
    const h=harness(),far={...place,id:'far',loc:[-101.2,25.7],municipality:'Saltillo',cve_mun:'05030'};
    h.c.fetchStartupJson=async()=>({catalog:{...catalog,total:3,places:[place,far,{...place,id:'micro',name:'Privada Sol',aliases:[],is_micro:true}]}});
    await h.c.executeCityToponymyScan();h.element('scanReviewMunicipality').value='Monterrey';
    assert.equal(h.c.scanReviewFiltered().length,2);h.element('scanReviewSearch').value='fraccionamiento los pinos';
    assert.equal(h.c.scanReviewFiltered().length,1);h.c.selectScanReviewFiltered(false);
    h.element('scanReviewSearch').value='';h.element('scanExcludeMicro').checked=true;
    await h.c.importScannedPlaces();assert.equal(h.c.cityData.places.length,1);
    assert.equal(h.c.cityData.places[0].municipality,'Saltillo','hidden selections must survive filtering');
  }
  {
    const h=harness(),old={...place,id:'old',name:'Viejo',loc:[-100.3,25.7]};
    h.c.cityData.places=[old];await h.c.executeCityToponymyScan();h.setMode('replace');h.c.renderScanReview();
    assert.match(h.element('scanImportDelta').textContent,/1 eliminaciones/);assert.equal(h.element('btnConfirmImportScan').disabled,true);
    await h.c.importScannedPlaces();assert.equal(h.c.cityData.places[0].name,'Viejo');
    h.element('scanConfirmReplace').checked=true;await h.c.importScannedPlaces();
    assert.equal(h.c.cityData.places[0].name,'Los Pinos');assert.equal(h.c.cityData.deleted_places[0].id,'old');
    h.c.undoPlacesLastAction();assert.equal(h.c.cityData.deleted_places.length,0);
  }
  {
    const h=harness();h.element('scanInferSmall').checked=true;
    const inferred={...place,id:'inferred',type:'city',source:'INEGI_DENUE_LOCALIDAD',hierarchy_inferred:true};
    h.c.fetchStartupJson=async()=>({catalog:{...catalog,places:[inferred]}});
    await h.c.executeCityToponymyScan();await h.c.importScannedPlaces();
    assert.equal(h.c.cityData.places[0].type,'village');
    const saved={...inferred,name:'Escala elegida manualmente'};
    h.c.cityData.places=[saved];await h.c.executeCityToponymyScan();await h.c.importScannedPlaces();
    assert.equal(h.c.cityData.places[0].type,'city','existing manual scale must remain');
  }
  {
    const h=harness();h.c.cityData.places=[place,{...place,id:'other'}];h.setZones();
    const network=defer();h.c.fetch=async()=>({json:async()=>network.promise});
    const pending=h.c.applyZoneThinningSelections();await tick();h.c.currentCityFile='saltillo.yaml';
    network.resolve({status:'ok',places:[]});await pending;assert.equal(h.c.cityData.places.length,2);
  }
  {
    const h=harness();h.c.cityData.places=[place,{...place,id:'other'}];h.setZones();
    h.c.fetch=async()=>({json:async()=>({status:'ok',places:[place],total_kept:1,total_pruned:1})});
    h.c.saveCurrentCity=async()=>false;await h.c.applyZoneThinningSelections();
    assert.equal(h.c.cityData.deleted_places[0].id,'other');assert.equal(h.toasts.at(-1).type,'error');
    assert.match(h.toasts.at(-1).message,/borrador/);
  }
  console.log('Toponymy UI: review, selective import, replacement delta, identity, diagnostics, guarded saves/projects, pruning: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
