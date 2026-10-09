const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const names = ['setAutoUrbanCoreState', 'cancelAutoUrbanCore', 'autoDetectUrbanCore'];
const functions = names.map(name => {
  const match = source.match(new RegExp('    (?:async )?function ' + name + '\\(.*?\\n    }', 's'));
  assert(match, name);
  return match[0];
}).join('\n');
const polygon = [[-100.4,25.6],[-100.3,25.6],[-100.3,25.7],[-100.4,25.6]];
const result = {status:'ok',polygon,points_count:100,reach_km:15};
const deferred = () => { let resolve; const promise = new Promise(r=>{resolve=r;}); return {promise,resolve}; };
const tick = () => new Promise(resolve=>setImmediate(resolve));
function harness() {
  const controls = new Map(), intervals = new Map(), calls = [], toasts = [];
  const element = id => {
    if (!controls.has(id)) controls.set(id,{textContent:'',disabled:false,hidden:true,dataset:{},value:'15',setAttribute(){}});
    return controls.get(id);
  };
  let now = 0, saves = 0;
  const context = {console,AbortController,Date:{now:()=>now},
    currentCityFile:'a.yaml',cityLoadVersion:1,cityData:{city:{urban_core_polygon:null}},
    urbanCoreReachPreviewCircle:null,urbanCoreLayer:{getBounds:()=>polygon},
    mapBbox:{fitBounds(){calls.push('fit');}},document:{getElementById:element},
    setInterval(fn){intervals.set(1,fn);return 1;},clearInterval(id){intervals.delete(id);},
    showToast(message,type){toasts.push({message,type});},
    saveCurrentCity:async()=>{saves++;return true;},
    fetchStartupJson:async(url,label,timeout,signal)=>{calls.push({url,label,timeout,signal});return result;},
    setUrbanCorePolygon(coords){context.cityData.city.urban_core_polygon=coords;calls.push('apply');},
  };
  vm.createContext(context);
  vm.runInContext('let autoUrbanCoreController=null;\n'+functions,context);
  return {c:context,element,calls,toasts,intervals,get saves(){return saves;},advance(seconds){now+=seconds*1000;for(const fn of intervals.values())fn();}};
}
(async()=>{
  {
    const h=harness(), request=deferred(), save=deferred();
    let saves=0, requests=0;
    h.c.saveCurrentCity=async()=>++saves===1?true:save.promise;
    h.c.fetchStartupJson=async()=>{requests++;return request.promise;};
    const pending=h.c.autoDetectUrbanCore();
    await tick();
    assert.equal(h.element('btnAutoUrbanCore').disabled,true);
    assert.equal(h.element('autoUrbanCoreStatus').hidden,false);
    h.advance(12);
    assert.match(h.element('autoUrbanCoreStatus').textContent,/12 s/);
    await h.c.autoDetectUrbanCore();
    assert.equal(requests,1,'repeated click does not start another census');
    request.resolve(result);
    await tick();
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'running');
    assert.equal(h.toasts.some(t=>t.type==='success'),false,'success waits for YAML persistence');
    save.resolve(true);
    await pending;
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'success');
    assert.equal(h.element('btnAutoUrbanCore').disabled,false);
    assert.deepEqual(h.calls,['apply','fit']);
    assert.equal(h.intervals.size,0);
  }
  {
    const h=harness();
    h.c.fetchStartupJson=async()=>{throw Error('No se pudo leer el censo');};
    await h.c.autoDetectUrbanCore();
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'error');
    assert.match(h.element('autoUrbanCoreStatus').textContent,/No se pudo leer/);
    assert.equal(h.c.cityData.city.urban_core_polygon,null);
    assert.equal(h.element('btnAutoUrbanCore').disabled,false);
  }
  {
    const h=harness();let saves=0;
    h.c.saveCurrentCity=async()=>++saves===1;
    await h.c.autoDetectUrbanCore();
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'error');
    assert.match(h.element('autoUrbanCoreStatus').textContent,/borrador/);
    assert.equal(h.toasts.some(t=>t.type==='success'),false);
  }
  {
    const h=harness(),request=deferred();
    h.c.fetchStartupJson=async()=>request.promise;
    const pending=h.c.autoDetectUrbanCore();await tick();
    h.c.currentCityFile='b.yaml';h.c.cityLoadVersion++;
    h.c.cancelAutoUrbanCore();request.resolve(result);await pending;
    assert.equal(h.c.cityData.city.urban_core_polygon,null,'late result cannot affect another project');
    assert.equal(h.element('autoUrbanCoreStatus').hidden,true);
  }
  {
    const h=harness(),request=deferred();
    h.c.fetchStartupJson=async()=>request.promise;
    const pending=h.c.autoDetectUrbanCore();await tick();
    const manual=[[1,1],[2,1],[2,2],[1,1]];
    h.c.cityData.city.urban_core_polygon=manual;
    request.resolve(result);await pending;
    assert.equal(h.c.cityData.city.urban_core_polygon,manual,'manual edits survive an older census result');
    assert.match(h.element('autoUrbanCoreStatus').textContent,/conservó tu edición/);
  }
  {
    const h=harness();h.c.saveCurrentCity=async()=>false;
    await h.c.autoDetectUrbanCore();
    assert.equal(h.calls.length,0,'failed initial save does not calculate from stale configuration');
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'error');
  }
  {
    const h=harness();h.c.fetchStartupJson=async()=>({...result,polygon:[[1,2],[3,4],[NaN,5],[1,2]]});
    await h.c.autoDetectUrbanCore();
    assert.equal(h.c.cityData.city.urban_core_polygon,null);
    assert.equal(h.element('autoUrbanCoreStatus').dataset.state,'error');
  }
  console.log('Auto Censo UI: persistent progress, repeated clicks, confirmed saves, errors, stale projects and manual edits: PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
