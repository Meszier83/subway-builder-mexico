/* Native HTTP acceptance using the same persistent fetch transport as the UI. */
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const base = process.argv[2] || 'http://127.0.0.1:8080';
const count = Number(process.argv[3] || 30);
const root = path.resolve(__dirname, '..');
const get = async route => {
  const response = await fetch(base + route, {signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw Error(route + ': HTTP ' + response.status);
  return response;
};
(async()=>{
  let identity = null;
  for(let i=0;i<count;i++) {
    const html = await(await get('/')).text();
    if(!html.trim().endsWith('</html>') || !html.includes('/static/wizard/app.js?v='))
      throw Error('Incomplete HTML at trial ' + i);
    const current = await(await get('/api/instance')).json();
    if(identity && identity.instance_id !== current.instance_id) throw Error('Instance changed');
    identity = current;
    const cities = await(await get('/api/cities')).json();
    if(!Array.isArray(cities.cities)) throw Error('Invalid cities response');
  }
  const manifest = JSON.parse(fs.readFileSync(path.join(root,'tools/static/wizard/manifest.json')));
  for(const [name, expected] of Object.entries(manifest.files)) {
    const body = Buffer.from(await(await get('/static/wizard/'+name)).arrayBuffer());
    if(crypto.createHash('sha256').update(body).digest('hex') !== expected)
      throw Error('Incomplete or stale asset: ' + name);
  }
  const sourceHash = crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'tools/wizard.py'))).digest('hex');
  if(identity.code_sha256 !== sourceHash) throw Error('Server still runs stale Python code');
  console.log(JSON.stringify({base,completeHtml:count,completeCities:count,completeIdentity:count,
    verifiedAssets:Object.keys(manifest.files).length,identity},null,2));
  process.exit(0);
})().catch(error=>{console.error(error);process.exit(1);});
