const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const names=['automaticLabelsEnabled','labelInputsSignature','toponymyOperationContext','currentToponymyOperation',
  'topFieldsSnapshot','restoreTopFields','commitToponymyChange','cancelAutomaticToponymy','prepareAutomaticToponymy',
  'updatePlaceScale','batchSetPlaceScale','getPlaceScale','scanForToponymyDuplicates','applyDuplicateResolutions',
  'applyPendingHomogenization','deduplicateNearbyPlaces','sameToponymyPlace','toponymyNameKey','checkPlacesDuplicatesCount'];
const functions=names.map(name=>{const m=source.match(new RegExp('    (?:async )?function '+name+'\\(.*?\\n    }','s'));assert(m,name);return m[0];}).join('\n');
const tick=()=>new Promise(r=>setImmediate(r));
const defer=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const old={id:'old',name:'Los Pinos',loc:[-100.5,25.5],type:'neighbourhood',feature_kind:'settlement',official_code:'1903900010001'};
function harness(){
  const controls=new Map(),toasts=[];let saves=0,requests=[];
  const element=id=>{if(!controls.has(id))controls.set(id,{disabled:false,hidden:false,innerHTML:'',innerText:'',value:'500',
    textContent:'',classList:{add(){},remove(){},contains(){return true;}},setAttribute(){}});return controls.get(id);};
  const c={console,JSON,Promise,Date,AbortController,crypto:require('node:crypto'),currentCityFile:'a.yaml',cityLoadVersion:1,
    cityData:{city:{bbox:[-101,25,-100,26]},places:[structuredClone(old)],deleted_places:[]},
    document:{getElementById:element,querySelector:()=>({value:'0'})},window:{},lucide:{createIcons(){}},
    selectedPlaceIndices:new Set([0]),placesHistorySnapshot:null,toponymyHistoryState:null,
    automaticLabelPlan:null,automaticLabelSignature:'',automaticLabelAttempt:'',toponymyCatalogView:false,
    topPreparationController:null,duplicateReviewContext:null,duplicateScanVersion:0,currentDuplicateGroups:[],
    toponymyCommitEpoch:0,
    homogenizeContext:null,pendingHomogenizeResult:null,autoSaveTimer:null,citySaveChain:Promise.resolve(),
    setInterval(){return 1;},clearInterval(){},setTimeout,clearTimeout,confirm:()=>true,
    saveCurrentCity:async()=>{saves++;return true;},triggerAutoSave(){saves++;},renderPlacesList(){},renderPlacesMarkersOnMap(){},
    renderToponymyStudioTable(){},updateUndoButtonUI(){},renderAutomaticLabelSummary(){},setToponymyScanStatus(){},
    cancelToponymyScan(){},renderDuplicateGroupsUI(){},closeDuplicateResolverModal(){},closeHomogenizerModal(){},resetHomogenizerPreview(){},
    showToast(message,type){toasts.push({message,type});},fetch:async(url,body)=>{requests.push(url);return {ok:true,json:async()=>({status:'ok',groups:[]})};},
    fetchStartupJson:async()=>({status:'complete',job_id:'job'})};
  vm.createContext(c);vm.runInContext(functions,c);
  return {c,toasts,element,get saves(){return saves;},get requests(){return requests;}};
}
(async()=>{
  {
    const h=harness(),queued=defer();
    const save=source.match(/    async function saveCurrentCity\(.*?\n    }/s)[0];
    Object.assign(h.c,{AbortSignal,syncStateFromInputs(){},localStorage:{removeItem(){}},hideDraftBanner(){},renderPoiList(){},renderPoiMarkersOnMap(){}});
    vm.runInContext(save,h.c);
    h.c.cityData.demand={year:2020};h.c.citySaveChain=queued.promise;
    let saved;h.c.fetch=async(url,options)=>{saved=JSON.parse(options.body);return {ok:true,json:async()=>({status:'ok'})};};
    const running=h.c.saveCurrentCity(true);
    h.c.cityData.places=[{...old,id:'prepared'}];h.c.cityData.toponymy={layout:{engine:'label_v1'}};
    h.c.cityData.demand.year=2025;h.c.toponymyCommitEpoch++;
    queued.resolve();assert.equal(await running,true);
    assert.equal(saved.places[0].id,'prepared');assert.equal(saved.toponymy.layout.engine,'label_v1');
    assert.equal(saved.demand.year,2020,'refreshing queued labels must retain the other saved fields');
  }
  {
    const h=harness(),identity=structuredClone(h.c.cityData.places[0]);
    h.c.updatePlaceScale(0,'large');
    assert.equal(h.c.cityData.places[0].type,identity.type);
    assert.equal(h.c.cityData.places[0].feature_kind,identity.feature_kind);
    assert.equal(h.c.cityData.places[0].official_code,identity.official_code);
    assert.equal(h.c.cityData.places[0].label_overrides.scale,'large');
    h.c.batchSetPlaceScale('small');assert.equal(h.c.getPlaceScale(h.c.cityData.places[0]),'small');
  }
  {
    const h=harness(),pending=defer();h.c.fetch=async()=>({json:()=>pending.promise});
    const run=h.c.scanForToponymyDuplicates();await tick();h.c.currentCityFile='b.yaml';h.c.cityLoadVersion++;
    pending.resolve({groups:[{group_id:'wrong'}]});await run;
    assert.equal(h.c.currentDuplicateGroups.length,0);assert.equal(h.c.duplicateReviewContext,null);
  }
  for(const mode of ['switch','edit']){
    const h=harness(),pending=defer();h.c.fetch=async()=>({json:()=>pending.promise});
    h.c.currentDuplicateGroups=[{group_id:'d',candidates:[{original_index:0}]}];
    h.c.duplicateReviewContext=h.c.toponymyOperationContext();
    const run=h.c.applyDuplicateResolutions();await tick();
    if(mode==='switch'){h.c.currentCityFile='b.yaml';h.c.cityLoadVersion++;h.c.cityData={places:[{id:'b',name:'Otra ciudad'}]};}
    else h.c.cityData.places[0].name='Edición reciente';
    const before=JSON.stringify(h.c.cityData);pending.resolve({status:'ok',places:[],removed:[old]});await run;
    assert.equal(JSON.stringify(h.c.cityData),before);assert.equal(h.saves,0);
  }
  {
    const h=harness(),before=JSON.stringify(h.c.cityData);h.c.saveCurrentCity=async()=>false;
    assert.equal(await h.c.commitToponymyChange([], [old]),false);
    assert.equal(JSON.stringify(h.c.cityData),before);assert.equal(h.c.placesHistorySnapshot,null);
  }
  {
    const h=harness();assert.equal(await h.c.commitToponymyChange([], [old]),true);
    assert.equal(h.c.cityData.deleted_places[0].id,'old');assert.equal(h.saves,1);
  }
  {
    const h=harness();h.c.pendingHomogenizeResult={places:[],diffs:[{}]};h.c.homogenizeContext=h.c.toponymyOperationContext();
    h.c.cityData.places[0].name='Edición reciente';await h.c.applyPendingHomogenization();
    assert.equal(h.c.cityData.places.length,1);assert.equal(h.saves,0);
  }
  for(const mode of ['cancel','switch','edit']){
    const h=harness(),pending=defer();h.c.fetchStartupJson=()=>pending.promise;
    const before=JSON.stringify(h.c.cityData);const run=h.c.prepareAutomaticToponymy();await tick();
    if(mode==='cancel')h.c.cancelAutomaticToponymy();
    if(mode==='switch'){h.c.currentCityFile='b.yaml';h.c.cityLoadVersion++;}
    if(mode==='edit')h.c.cityData.places[0].name='Edición reciente';
    pending.resolve({status:'complete',job_id:'job'});await run;
    assert.equal(h.requests.length,0);assert.equal(h.c.cityData.toponymy,undefined);
    if(mode!=='edit')assert.equal(JSON.stringify(h.c.cityData),before);
  }
  {
    const h=harness(),before=structuredClone(h.c.cityData);h.c.fetch=async()=>({ok:false,json:async()=>({error:'disk unavailable'})});
    await h.c.prepareAutomaticToponymy();assert.equal(JSON.stringify(h.c.cityData),JSON.stringify(before));
    assert(h.toasts.some(t=>t.type==='error'));assert.equal(h.c.placesHistorySnapshot,null);
  }
  for(const mode of ['switch','edit']){
    const h=harness(),pending=defer();h.c.fetch=async()=>({ok:true,json:()=>pending.promise});
    const run=h.c.prepareAutomaticToponymy();await tick();
    if(mode==='switch'){h.c.currentCityFile='b.yaml';h.c.cityLoadVersion++;h.c.cityData={places:[{id:'b',name:'Otra ciudad'}]};}
    else h.c.cityData.places[0].name='Edición durante el guardado';
    const before=JSON.stringify(h.c.cityData);
    pending.resolve({status:'ok',fields:{places:[]},before:{places:[]},layout:{decisions:{}},summary:{},warnings:[]});
    await run;assert.equal(JSON.stringify(h.c.cityData),before);assert.equal(h.c.toponymyHistoryState,null);
  }
  {
    const h=harness(),original=structuredClone(h.c.cityData);
    const fields={places:[{...old,id:'new'}],deleted_places:[],toponymy:{layout:{engine:'label_v1'}},toponymy_mode:'replace'};
    h.c.fetch=async()=>({ok:true,json:async()=>({status:'ok',fields,before:{places:original.places,deleted_places:[]},
      layout:{decisions:{},summary:{}},summary:{added:1,preserved:1,eligible:1,deferred:0,exceptions:0},warnings:[]})});
    await h.c.prepareAutomaticToponymy();assert.equal(h.c.cityData.places[0].id,'new');
    assert.equal(h.c.cityData.toponymy.layout.engine,'label_v1');assert.equal(h.c.toponymyHistoryState.fields.places[0].id,'old');
  }
  {
    const h=harness();h.c.cityData.places.push({...old,id:'remote',official_code:'1903900020001',cve_loc:'190390002',loc:[-100.5,25.7]});
    h.c.checkPlacesDuplicatesCount();assert.notEqual(h.element('badgeDupCount').innerText,1);
    assert.equal(h.c.sameToponymyPlace(old,{...old,official_code:'1903900010002'}),false);
  }
  console.log('Automatic labels UI: sizes preserve identity; guarded prepare/duplicate/homogenize; cancellation, edits, failed saves, tombstones and commit: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
