const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../tools/static/wizard/app.js'), 'utf8');
const block = source.slice(source.indexOf('    let sourceDownloadPlan = null;'), source.indexOf('    async function refreshDataStatus('));
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {value:'', children:[], textContent:'', disabled:false,
    classList:{add(){},remove(){}}, replaceChildren(...nodes){this.children=nodes;},
    append(...nodes){this.children.push(...nodes);}, add(node){this.children.push(node);},addEventListener(){}});
  return elements.get(id);
}
const calls=[];
let refreshes=0;
const plan={id:'p',folder:'data/project',geography:{states:[{code:'05',name:'Coahuila'},{code:'10',name:'Durango'}],municipalities:[{code:'05035'},{code:'10007'}]},
  sources:[{kind:'cpv',label:'CPV',conflicts:[]},{kind:'denue',label:'DENUE',conflicts:['manual.csv']},{kind:'enoe',label:'ENOE',conflicts:[]},
    {kind:'eic',label:'EIC',conflicts:[],recommended:true},{kind:'conapo',label:'CONAPO',conflicts:[],recommended:false}]};
let checked=['enoe'];
let failSave=false;
const reference={mode:'eic2025',indicators:'',persons:[],pending_download:true};
const downloaded={mode:'eic2025',indicators:'data/test/indicators.csv',persons:['data/test/personas05.csv','data/test/personas10.csv']};
let switchDuringDownload=false;
let changeReferenceDuringDownload=false;
let changeDemandDuringDownload=false;
const context=vm.createContext({currentCityFile:'cities/test.yaml',cityData:{macroeconomics:{demographic_reference:structuredClone(reference)}}, document:{getElementById:element,
  createElement:()=>element('created-'+elements.size),createTextNode:text=>({text}),
  querySelectorAll:()=>checked.map(value=>({value}))},
  Option:function(text,value){this.text=text;this.value=value;},AbortSignal,
  saveCurrentCity:async()=>!failSave,refreshDataStatus:async()=>{refreshes++;},
  setTimeout:fn=>fn(),
  fetch:async(url,options)=>{calls.push({url,body:options?.body && JSON.parse(options.body)});
    if (switchDuringDownload && url.endsWith('/start')) context.currentCityFile='cities/other.yaml';
    if (changeReferenceDuringDownload && url.endsWith('/start')) context.cityData.macroeconomics.demographic_reference={mode:'eic2025',indicators:'manual.csv',persons:['manual-persons.csv']};
    if (changeDemandDuringDownload && url.endsWith('/start')) context.cityData.demand.target_year=2026;
    return {ok:true,json:async()=>url.endsWith('/plan') ? {id:'prep',status:'running'} :
      url.includes('/plan-job?') ? {id:'prep',status:'complete',plan:structuredClone(plan)} :
      {id:'job',status:'complete',message:'finished',results:checked.includes('eic') ? [{kind:'eic',status:'ok',files:['indicators.csv','personas05.csv','personas10.csv'],demographic_reference:downloaded}] : [{kind:'enoe',status:'ok',files:['enoe.xls']}]}};}});
vm.runInContext(block,context);
(async()=>{
  await context.prepareSourceDownloads();
  assert.equal(element('sourceDownloadEnoeState').value,'');
  const choices=element('sourceDownloadChoices').children;
  assert.equal(choices[1].children[0].disabled,true);
  assert.equal(choices[1].children[0].checked,false);
  assert.equal(choices[3].children[0].checked,true,'EIC is recommended when active');
  assert.equal(choices[4].children[0].checked,false,'CONAPO is optional with EIC');
  await context.startSourceDownloads();
  assert.equal(calls.filter(c=>c.url.endsWith('/start')).length,0,'multi-state ENOE must require a choice');
  element('sourceDownloadEnoeState').value='05';
  element('sourceDownloadEnoeYear').value='2026';
  element('sourceDownloadEnoeQuarter').value='2';
  await context.startSourceDownloads();
  assert.equal(calls.find(c=>c.url.endsWith('/start')).body.enoe_state,'05');
  assert.equal(refreshes,1);
  await context.prepareSourceDownloads();
  context.currentCityFile='cities/other.yaml';
  const before=calls.length;
  await context.startSourceDownloads();
  assert.equal(calls.length,before,'must not start the previous project plan');
  failSave=true;
  await context.prepareSourceDownloads();
  assert.equal(calls.length,before,'must save the BBOX before requesting geography');
  failSave=false;
  context.currentCityFile='cities/test.yaml';
  checked=['eic'];
  await context.prepareSourceDownloads();
  assert.equal(element('sourceDownloadEnoeOptions').hidden,true,'unused ENOE must not ask for extra settings');
  await context.startSourceDownloads();
  assert.deepEqual(JSON.parse(JSON.stringify(context.cityData.macroeconomics.demographic_reference)),downloaded);
  assert.equal(element('cfg_eic_persons').value,downloaded.persons.join('\n'));
  context.cityData.macroeconomics.demographic_reference=structuredClone(reference);
  await context.prepareSourceDownloads();
  switchDuringDownload=true;
  await context.startSourceDownloads();
  assert.equal(context.cityData.macroeconomics.demographic_reference.pending_download,true,'must not attach another project download');
  switchDuringDownload=false;
  context.currentCityFile='cities/test.yaml';
  await context.prepareSourceDownloads();
  changeReferenceDuringDownload=true;
  await context.startSourceDownloads();
  assert.equal(context.cityData.macroeconomics.demographic_reference.indicators,'manual.csv','must preserve reference changed during acquisition');
  changeReferenceDuringDownload=false;
  context.cityData={demand:{engine:'v2',target_year:2025},macroeconomics:{projection_year:2026}};
  await context.prepareSourceDownloads();
  await context.startSourceDownloads();
  assert.equal(context.cityData.macroeconomics.demographic_reference.indicators,downloaded.indicators,'required v2 EIC must bind without a pre-existing reference');
  assert.equal(context.cityData.macroeconomics.demographic_reference.previous_projection_year,2026);
  assert.equal(context.cityData.macroeconomics.projection_year,2025);
  assert.equal(element('cfg_demographic_reference').value,'eic2025','autosave must retain the bound reference');
  context.cityData={demand:{engine:'v2',target_year:2025},macroeconomics:{projection_year:2026,demographic_reference:structuredClone(reference)}};
  await context.prepareSourceDownloads();
  changeDemandDuringDownload=true;
  await context.startSourceDownloads();
  assert.equal(context.cityData.macroeconomics.demographic_reference.pending_download,true,'changed target year must prevent binding even when EIC remains active');
  changeDemandDuringDownload=false;
  context.cityData={demand:{engine:'legacy'},macroeconomics:{projection_year:2026}};
  await context.prepareSourceDownloads();
  await context.startSourceDownloads();
  assert.equal(context.cityData.macroeconomics.demographic_reference,undefined,'optional legacy EIC must not activate a new method');
  console.log('Source download UI: PASS (EIC binding, optional CONAPO, explicit ENOE, conflicts, saved BBOX, project isolation)');
})().catch(error=>{console.error(error);process.exitCode=1;});
