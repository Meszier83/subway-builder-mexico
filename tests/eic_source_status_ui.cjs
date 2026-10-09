const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../tools/static/wizard/app.js'),'utf8');
const block=source.slice(source.indexOf('    async function refreshDataStatus('),source.indexOf('    // Drag & Drop',source.indexOf('    async function refreshDataStatus(')));
const elements=new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id,{innerHTML:'',children:[],classList:{add(){},remove(){}},
    appendChild(node){this.children.push(node);},querySelector(){return {addEventListener(){}};},querySelectorAll(){return [];}});
  return elements.get(id);
}
let status={eic:{status:'missing',active:true,required:true,files:[],missing_paths:['personas10.csv']},conapo:{status:'missing',files:[]}};
const context=vm.createContext({currentCityFile:'cities/test.yaml',cityData:{city:{code:'TST'}},sourceDownloadPlan:null,
  document:{getElementById:element,createElement:()=>element('card-'+elements.size)},
  fetchStartupJson:async()=>status,refreshWorkplaceSourceStatus(){},updateDemandEngineControls(){},wizardLifecycle:{clearWarning(){},warn(){throw Error('unexpected warning');}},
  escapeHtml:s=>s,lucide:{createIcons(){}},console});
vm.runInContext(block,context);
(async()=>{
  await context.refreshDataStatus();
  const cards=element('dataStatusList').children;
  const eic=cards.find(c=>c.innerHTML.includes('Encuesta Intercensal'));
  const conapo=cards.find(c=>c.innerHTML.includes('CONAPO municipal'));
  assert.match(eic.innerHTML,/Necesaria · referencia 2025/);
  assert.match(eic.innerHTML,/personas10.csv/);
  assert.match(conapo.innerHTML,/Opcional · sin uso con EIC/);
  assert.match(conapo.innerHTML,/No necesitas descargar CONAPO/);
  status={eic:{status:'inactive',required:false,active:false,files:[]},conapo:{status:'missing',files:[]}};
  element('dataStatusList').children=[];
  await context.refreshDataStatus();
  const inactive=element('dataStatusList').children.find(c=>c.innerHTML.includes('Encuesta Intercensal'));
  assert.match(inactive.innerHTML,/Sin activar/);
  console.log('EIC source status UI: PASS (required paths, optional CONAPO, inactive reference)');
})().catch(error=>{console.error(error);process.exitCode=1;});
