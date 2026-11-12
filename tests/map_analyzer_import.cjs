'use strict';
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const {prepareUpload,receiveDemand,readLocal,normalizeLocal} = require('../tools/static/map-analyzer/import.js');

function fixture(mode = 'normal') {
  return execFileSync('python', ['-c', `
import io,json,sys,zipfile
mode=sys.argv[1]
class NonSeekable(io.BytesIO):
 def seekable(self): return False
 def seek(self,*args): raise io.UnsupportedOperation()
b=NonSeekable() if mode=='streamed' else io.BytesIO()
with zipfile.ZipFile(b,'w',zipfile.ZIP_STORED if mode=='stored' else zipfile.ZIP_DEFLATED) as z:
 z.writestr('map/tiles.pmtiles',b'x'*3000000)
 demand={'points':[{'id':'a','location':[0,0],'residents':7,'jobs':7}], 'pops':[{'residenceId':'a','jobId':'a','size':7}]}
 if mode!='missing': z.writestr('map/demand_data.json',json.dumps(demand))
 if mode=='duplicate': z.writestr('other/demand_data.json',json.dumps(demand))
 z.writestr('map/config.json',json.dumps({'name':'Test map'}))
 z.writestr('other/config.json','{}')
 z.comment=b'Map package comment'
sys.stdout.buffer.write(b.getvalue())
`, mode], {maxBuffer:8*1024*1024});
}
function normalize(bytes) {
  return JSON.parse(execFileSync('python', ['-c', "import sys,io,json;from tools.map_analyzer import load_map;print(json.dumps(load_map(io.BytesIO(sys.stdin.buffer.read()))))"], {input:bytes}));
}
(async () => {
  const json = new Blob(['{"points":[]}']);
  assert.equal(await prepareUpload(json), json, 'plain JSON uses existing validation');
  class SlicedFile extends Blob { async arrayBuffer() { throw new Error('Do not read the entire map archive'); } }
  const source = fixture(), selected = await prepareUpload(new SlicedFile([source]));
  const bytes = Buffer.from(await selected.arrayBuffer());
  assert.ok(bytes.length < source.length / 3, 'cartography excluded from upload');
  assert.deepEqual(normalize(bytes), normalize(source), 'production importer returns exactly the original counts, IDs and metadata');
  for(const mode of ['stored','streamed']) {
    const original=fixture(mode), reduced=await prepareUpload(new Blob([original]));
    assert.deepEqual(normalize(Buffer.from(await reduced.arrayBuffer())),normalize(original),mode+' ZIP parity');
  }
  const members = execFileSync('python', ['-c', "import io,sys,zipfile;print(','.join(zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())).namelist()))"], {input:bytes}).toString().trim();
  assert.equal(members, 'map/demand_data.json,map/config.json');
  await assert.rejects(prepareUpload(new Blob([fixture('missing')])), /exactly one/);
  await assert.rejects(prepareUpload(new Blob([fixture('duplicate')])), /exactly one/);
  await assert.rejects(prepareUpload(new Blob([source.subarray(0, -10)])), /directory/);
  const broken = Buffer.from(source);broken.writeUInt32LE(0,0);
  // A non-ZIP signature falls back to the server, which rejects invalid JSON.
  assert.equal((await prepareUpload(new Blob([broken]))).size, broken.length);
  for(const mode of ['normal','stored','streamed']) {
    const bytes=fixture(mode);
    assert.deepEqual(await readLocal(new Blob([bytes])),normalize(bytes),'local browser reader matches Python: '+mode);
  }
  await assert.rejects(readLocal(new Blob(['{"points":[],"pops":[]}'])),/nonempty/);
  const bad={points:[{id:'a',location:[0,0],residents:1,jobs:1}],pops:[{residenceId:'missing',jobId:'a',size:1}]};
  assert.throws(()=>normalizeLocal(bad),/missing point/);
  bad.pops=[];bad.points[0].residents=true;assert.throws(()=>normalizeLocal(bad),/whole numbers/);
  const encoded=new TextEncoder().encode(JSON.stringify({name:'Mérida',points:[],padding:'x'.repeat(40000)}));
  let released=false, reset=false, received=0;
  const mock=async(url,options)=>{
    if(options.method==='POST')return new Response(JSON.stringify({token:'test',bytes:encoded.length,partBytes:16384}));
    if(options.method==='DELETE'){released=true;return new Response('{}');}
    const index=Number(url.split('/').at(-1));
    if(index===1&&!reset){reset=true;throw new Error('connection reset');}
    return new Response(encoded.slice(index*16384,(index+1)*16384));
  };
  const loaded=await receiveDemand(new Blob(),(n,total)=>{assert.ok(n>received&&n<=total);received=n;},mock);
  assert.equal(loaded.name,'Mérida');assert.equal(received,encoded.length);assert.ok(reset&&released);
  released=false;
  await assert.rejects(receiveDemand(new Blob(),()=>{},async(url,o)=>{
    if(o.method==='POST')return new Response(JSON.stringify({token:'test',bytes:100,partBytes:16384}));
    if(o.method==='DELETE'){released=true;return new Response('{}');}
    return new Response(new Uint8Array(99));
  }),/Incomplete/);
  assert.ok(released,'failed transfers release temporary server data');
  console.log('Map analyzer import: selected ZIP members, normalized parity, JSON, duplicate/missing demand and truncation PASS');
})().catch(error => { console.error(error); process.exitCode=1; });
