const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const bootstrap = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/bootstrap.js'), 'utf8');
function fn(name) {
  const match = source.match(new RegExp('    (?:async )?function ' + name + '\\(.*?\\n    }', 's'));
  assert(match, 'Missing production function ' + name);
  return match[0];
}
function harness() {
  const controls = new Map();
  const element = id => {
    if (!controls.has(id)) controls.set(id, {textContent:'', innerText:'', disabled:false,
      hidden:false, style:{}, classList:{add(){},remove(){}}, setAttribute(){}});
    return controls.get(id);
  };
  const main = {inert:true};
  const context = {console:{warn(){},error(){}}, AbortSignal, AbortController, URLSearchParams,
    currentCityFile:'a.yaml', cityData:{}, conapoCommitPromise:null, autoSaveTimer:null,
    mapBbox:null, localStorage:{getItem(){return null;}}, location:{reload(){}},
    document:{body:{dataset:{}}, getElementById:id=>id==='citySelect'?null:element(id),
      querySelector:()=>main, querySelectorAll:()=>[], addEventListener(){}},
    addEventListener(){}, setTimeout:(...args)=>setTimeout(...args).unref(), clearTimeout,
    resetProjectSession(){}, populateFormFields(){context.populates++;}, populates:0,
    refreshDataStatus(){return new Promise(()=>{});}, showToast(){},
    initMaps(){}, initSidebarResizer(){}, initSSE(){context.sseStarts++;}, sseStarts:0,
    checkSystemHealth(){}, openProjectsModal:async()=>{}, lucide:{createIcons(){}},
    fetch:async url=>({ok:true,json:async()=>url.includes('/api/city?')?
      {city:{name:url.includes('b.yaml')?'B':'A',code:'TST'}}:{cities:[]}}),
    syncStateFromInputs(){throw Error('Must not edit unloaded data');},
  };
  context.window = context;
  context.location.search = '?city=a.yaml';
  vm.createContext(context);
  vm.runInContext(bootstrap + '\n' +
    'let startupPromise=null,mapsInitialized=false,sidebarInitialized=false,cityLoadVersion=0,cityLoadController=null;\n' +
    ['fetchStartupJson','initializeWizard','fetchCitiesList','loadCityData','saveCurrentCity','triggerAutoSave'].map(fn).join('\n'),context);
  return {context, element, main};
}
(async()=>{
  {
    const listeners = new Map();
    let closed = 0, reopened = 0;
    const context = {window:{addEventListener(name, handler){listeners.set(name,handler);}},
      sseSource:{close(){closed++;}}, autoSaveTimer:null, initSSE(){reopened++;}};
    vm.createContext(context);
    const start = source.indexOf("    window.addEventListener('beforeunload'");
    const end = source.indexOf("    window.addEventListener('pageshow'", start);
    const finish = source.indexOf('\n    });', end) + '\n    });'.length;
    vm.runInContext(source.slice(start,finish),context);
    listeners.get('beforeunload')();
    listeners.get('pagehide')();
    assert.equal(closed,1,'Unload releases the SSE socket exactly once');
    assert.equal(context.sseSource,null);
    listeners.get('pageshow')({persisted:false});
    assert.equal(reopened,0);
    listeners.get('pageshow')({persisted:true});
    assert.equal(reopened,1,'Back-forward restoration reconnects logs');
  }
  {
    const {context:c,main}=harness();
    await c.initializeWizard();
    assert.equal(c.document.body.dataset.wizardState,'ready');
    assert.equal(c.wizardLifecycle.readyFile,'a.yaml');
    assert.equal(main.inert,false);
    assert.equal(c.populates,1);
    assert.equal(c.sseStarts,1);
    // The unresolved source-status request must not hold project readiness.
  }
  {
    const {context:c,element,main}=harness();
    c.fetch=async()=>({ok:false,status:503});
    await c.loadCityData('a.yaml');
    assert.equal(c.document.body.dataset.wizardState,'failed');
    assert.equal(main.inert,true);
    assert.equal(element('btnSaveGlobal').disabled,true);
    assert.equal(await c.saveCurrentCity(),false);
    c.triggerAutoSave(); // Must not synchronize empty defaults or create a draft.
    c.fetch=async()=>({ok:true,json:async()=>({city:{name:'A',code:'TST'}})});
    await c.retryWizardStartup();
    assert.equal(c.wizardLifecycle.readyFile,'a.yaml');
    assert.equal(main.inert,false);
  }
  {
    const {context:c}=harness();
    let resolveA;
    c.fetch=async url=>({ok:true,json:()=>url.includes('a.yaml')?
      new Promise(resolve=>{resolveA=resolve;}):Promise.resolve({city:{name:'B',code:'TST'}})});
    const pending=c.loadCityData('a.yaml');
    await new Promise(resolve=>setImmediate(resolve));
    await c.loadCityData('b.yaml');
    resolveA({city:{name:'A',code:'OLD'}});
    await pending;
    assert.equal(c.cityData.city.name,'B');
    assert.equal(c.wizardLifecycle.readyFile,'b.yaml');
    assert.equal(c.populates,1);
  }
  {
    const {context:c}=harness();
    c.fetch=async()=>({ok:true,json:async()=>({city:null})});
    assert.equal(await c.loadCityData('a.yaml'),false);
    assert.equal(c.populates,0);
    assert.equal(c.wizardLifecycle.canSave('a.yaml'),false);
  }
  {
    const {context:c}=harness();
    c.fetch=(_url,options)=>new Promise((_resolve,reject)=>{
      options.signal.addEventListener('abort',()=>reject(new Error('Aborted')),{once:true});
    });
    const hold=setTimeout(()=>{},100);
    try {await assert.rejects(c.fetchStartupJson('/api/cities','Ciudades',5),/tiempo agotado/);}
    finally {clearTimeout(hold);c.wizardLifecycle.fail('test finished');}
  }
  {
    const {context:c}=harness();
    c.initMaps=()=>{throw Error('Missing Leaflet');};
    await c.initializeWizard();
    assert.equal(c.wizardLifecycle.readyFile,'a.yaml');
    assert.equal(c.document.body.dataset.wizardState,'ready');
  }
  console.log('Wizard startup: bounded failures/retry, protected saves, stale project rejection, independent source status, dependency failure: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
