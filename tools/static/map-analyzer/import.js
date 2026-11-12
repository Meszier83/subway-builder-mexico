/* Read ZIP metadata and copy selected compressed members without decompressing them. */
(function(root) {
  'use strict';
  const MAX_DEMAND = 512 * 1024 * 1024, MAX_CONFIG = 4 * 1024 * 1024;
  async function prepareUpload(file, membersOnly = false) {
    const read = async (start, length) => {
      if (start < 0 || length < 0 || start + length > file.size) throw new Error('Invalid ZIP bounds.');
      const bytes = new Uint8Array(await file.slice(start, start + length).arrayBuffer());
      if (bytes.length !== length) throw new Error('The selected file could not be read completely.');
      return bytes;
    };
    if (file.size < 4) return file;
    const signature = new DataView((await read(0, 4)).buffer).getUint32(0, true);
    if (signature !== 0x04034b50 && signature !== 0x06054b50) return file;
    const tailStart = Math.max(0, file.size - 65557), tail = await read(tailStart, file.size - tailStart);
    const tv = new DataView(tail.buffer);
    let end = -1;
    for (let i = tail.length - 22; i >= 0; i--) {
      if (tv.getUint32(i, true) === 0x06054b50 && i + 22 + tv.getUint16(i + 20, true) === tail.length) { end = i; break; }
    }
    if (end < 0) throw new Error('The ZIP directory is missing or incomplete.');
    const entries = tv.getUint16(end + 10, true), directorySize = tv.getUint32(end + 12, true), directoryOffset = tv.getUint32(end + 16, true);
    // Rare ZIP64/multidisk/oversized directories use the existing server reader.
    if (tv.getUint16(end + 4, true) || tv.getUint16(end + 6, true) || entries === 65535 || directorySize === 0xffffffff || directoryOffset === 0xffffffff || directorySize > 16 * 1024 * 1024) return file;
    if (tv.getUint16(end + 8, true) !== entries || directoryOffset + directorySize > tailStart + end) throw new Error('Invalid ZIP directory.');
    const directory = await read(directoryOffset, directorySize), dv = new DataView(directory.buffer), members = [];
    let offset = 0;
    for (let i = 0; i < entries; i++) {
      if (offset + 46 > directory.length || dv.getUint32(offset, true) !== 0x02014b50) throw new Error('Invalid ZIP directory entry.');
      const nameLength = dv.getUint16(offset + 28, true), extraLength = dv.getUint16(offset + 30, true), commentLength = dv.getUint16(offset + 32, true);
      const length = 46 + nameLength + extraLength + commentLength;
      if (offset + length > directory.length) throw new Error('Truncated ZIP directory entry.');
      const nameBytes = directory.subarray(offset + 46, offset + 46 + nameLength), flags = dv.getUint16(offset + 8, true);
      if (!(flags & 0x800) && nameBytes.some(b => b >= 128)) return file;
      const name = new TextDecoder('utf-8', {fatal:true}).decode(nameBytes);
      if (name.split('/').some(part => part === '.' || part === '..') || name.includes('//')) return file;
      const size = dv.getUint32(offset + 24, true), compressedSize = dv.getUint32(offset + 20, true), localOffset = dv.getUint32(offset + 42, true);
      if ([size, compressedSize, localOffset].includes(0xffffffff) || dv.getUint16(offset + 34, true)) return file;
      members.push({name, size, compressedSize, localOffset, central:directory.slice(offset, offset + length)});
      offset += length;
    }
    const demands = members.filter(m => m.name.split('/').at(-1) === 'demand_data.json');
    if (demands.length !== 1) throw new Error('The ZIP must contain exactly one demand_data.json. Import separate maps individually.');
    const demand = demands[0], parent = demand.name.slice(0, -'demand_data.json'.length);
    const configs = members.filter(m => m.name === parent + 'config.json');
    if (configs.length > 1) throw new Error('The ZIP contains duplicate config.json members.');
    if (demand.size > MAX_DEMAND) throw new Error('The uncompressed demand JSON exceeds the 512 MiB import limit.');
    if (configs[0]?.size > MAX_CONFIG) throw new Error('config.json exceeds the 4 MiB limit.');
    if(membersOnly)return {file,members:[demand,...configs]};
    const locals = [], centrals = [];
    let position = 0;
    for (const member of [demand, ...configs]) {
      const header = await read(member.localOffset, 30), hv = new DataView(header.buffer);
      if (hv.getUint32(0, true) !== 0x04034b50) throw new Error('Invalid ZIP member header.');
      const length = 30 + hv.getUint16(26, true) + hv.getUint16(28, true) + member.compressedSize;
      if (member.localOffset + length > directoryOffset) throw new Error('Invalid ZIP member bounds.');
      locals.push(file.slice(member.localOffset, member.localOffset + length));
      new DataView(member.central.buffer).setUint32(42, position, true);
      centrals.push(member.central);
      position += length;
    }
    const footer = new Uint8Array(22), fv = new DataView(footer.buffer);
    fv.setUint32(0, 0x06054b50, true);
    fv.setUint16(8, centrals.length, true); fv.setUint16(10, centrals.length, true);
    fv.setUint32(12, centrals.reduce((n, c) => n + c.length, 0), true); fv.setUint32(16, position, true);
    return new Blob([...locals, ...centrals, footer], {type:'application/zip'});
  }
  const crcTable=Array.from({length:256},(_,n)=>{for(let i=0;i<8;i++)n=(n&1)?0xedb88320^(n>>>1):n>>>1;return n>>>0;});
  function normalizeLocal(demand,config) {
    const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
    const count=v=>{if(!Number.isSafeInteger(v)||v<0)throw new Error('Demand counts must be nonnegative safe whole numbers.');return v;};
    if(!object(demand)||!Array.isArray(demand.points)||!demand.points.length)throw new Error('Expected demand JSON with a nonempty points array.');
    const ids=new Set(),out=new Map(),into=new Map();
    const points=demand.points.map(p=>{
      if(!object(p)||typeof p.id!=='string'||!p.id||ids.has(p.id))throw new Error('Missing or duplicate demand-point ID.');
      if(!Array.isArray(p.location)||p.location.length!==2||!p.location.every(Number.isFinite)||Math.abs(p.location[0])>180||Math.abs(p.location[1])>85)throw new Error('Invalid demand-point coordinates.');
      ids.add(p.id);out.set(p.id,0);into.set(p.id,0);
      return {id:p.id,location:p.location,residents:count(p.residents),jobs:count(p.jobs)};
    });
    const tripsAvailable=Object.hasOwn(demand,'pops'),raw=tripsAvailable?demand.pops:[];
    if(!Array.isArray(raw))throw new Error('pops must be an array.');
    const pops=raw.map(p=>{
      if(!object(p)||!ids.has(p.residenceId)||!ids.has(p.jobId))throw new Error('Trip group references a missing point.');
      const size=count(p.size);if(!size)throw new Error('Trip group size must be positive.');
      out.set(p.residenceId,count(out.get(p.residenceId)+size));into.set(p.jobId,count(into.get(p.jobId)+size));
      return {residenceId:p.residenceId,jobId:p.jobId,size};
    });
    for(const field of ['residents','jobs'])count(points.reduce((n,p)=>count(n+p[field]),0));
    count(pops.reduce((n,p)=>count(n+p.size),0));
    const warnings=[];
    if(tripsAvailable){const mismatch=points.filter(p=>p.residents!==out.get(p.id)||p.jobs!==into.get(p.id)).length;
      if(mismatch)warnings.push(`${mismatch.toLocaleString('en-US')} points have display counts that differ from trip totals. Heatmaps use the supplied point counts; flows use trip group sizes.`);
    }else warnings.push('No pops array: heatmaps are available, but trip flows are unavailable.');
    return {name:object(config)&&typeof config.name==='string'&&config.name?config.name:'Imported map',points,pops,tripsAvailable,warnings};
  }
  async function readLocal(file,progress=()=>{}) {
    progress('Reading ZIP directory');
    const selected=await prepareUpload(file,true);
    async function parse(blob){return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await blob.arrayBuffer()).replace(/^\uFEFF/,''));}
    if(!selected.members){
      const signature=new Uint8Array(await file.slice(0,2).arrayBuffer());
      if(signature[0]===80&&signature[1]===75)throw new Error('This ZIP layout is unsupported locally. Select its demand_data.json directly.');
      progress('Validating demand JSON');return normalizeLocal(await parse(file));
    }
    const decoded=[];
    for(const member of selected.members){
      progress('Reading '+member.name.split('/').at(-1));
      const cv=new DataView(member.central.buffer),method=cv.getUint16(10,true),flags=cv.getUint16(8,true);
      if(flags&1)throw new Error('Encrypted ZIP members are unsupported.');
      const header=new DataView(await file.slice(member.localOffset,member.localOffset+30).arrayBuffer());
      if(header.getUint32(0,true)!==0x04034b50)throw new Error('Invalid ZIP member header.');
      const start=member.localOffset+30+header.getUint16(26,true)+header.getUint16(28,true);
      if(start+member.compressedSize>file.size)throw new Error('Truncated ZIP member.');
      let stream=file.slice(start,start+member.compressedSize).stream();
      if(method===8){try{stream=stream.pipeThrough(new DecompressionStream('deflate-raw'));}catch(_){throw new Error('This browser cannot decompress ZIPs. Select demand_data.json directly.');}}
      else if(method!==0)throw new Error('Unsupported ZIP compression. Select demand_data.json directly.');
      const reader=stream.getReader(),chunks=[];let size=0,crc=0xffffffff;
      try{while(true){const {done,value}=await reader.read();if(done)break;size+=value.length;if(size>member.size)throw new Error('ZIP member exceeds its declared size.');for(const b of value)crc=crcTable[(crc^b)&255]^(crc>>>8);chunks.push(value);}}
      catch(error){await reader.cancel().catch(()=>{});throw error;}
      if(size!==member.size||((crc^0xffffffff)>>>0)!==cv.getUint32(16,true))throw new Error('ZIP member is damaged (size or checksum mismatch).');
      decoded.push(await parse(new Blob(chunks)));
    }
    progress('Validating points and trip groups');return normalizeLocal(decoded[0],decoded[1]);
  }
  async function receiveDemand(upload, progress = () => {}, request = fetch) {
    const headers={'X-Map-Analyzer':'1'};
    // Include body consumption in the timeout, not just receipt of headers.
    async function read(url, options, binary=false, timeout=30000) {
      const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),timeout);
      try {
        const response=await request(url,{...options,signal:controller.signal});
        if(!response.ok) {const text=await response.json();throw new Error(text.error||'Import failed.');}
        return binary?new Uint8Array(await response.arrayBuffer()):await response.json();
      } catch(error) {
        if(controller.signal.aborted)throw new Error('Local demand transfer timed out. Please reopen the map.');
        throw error;
      } finally {clearTimeout(timer);}
    }
    const manifest=await read('/api/import?response=parts',{method:'POST',headers:{...headers,'Content-Type':'application/octet-stream'},body:upload});
    if(!/^[A-Za-z0-9_-]+$/.test(manifest.token)||!Number.isSafeInteger(manifest.bytes)||manifest.bytes<1||manifest.bytes>512*1024*1024||manifest.partBytes!==16384)throw new Error('Invalid import response. Restart the analyzer.');
    const path='/api/import-result/'+manifest.token;
    try {
      const bytes=new Uint8Array(manifest.bytes), parts=Math.ceil(bytes.length/manifest.partBytes);
      let next=0,received=0;
      await Promise.all(Array.from({length:1},async()=>{
        while(next<parts){const index=next++;let part;
          for(let attempt=0;attempt<3;attempt++){try{part=await read(path+'/'+index,{headers},true);break;}catch(error){if(attempt===2||error.message.includes('timed out'))throw error;}}
          const offset=index*manifest.partBytes;
          if(part.length!==Math.min(manifest.partBytes,bytes.length-offset))throw new Error('Incomplete demand transfer. Please reopen the map.');
          bytes.set(part,offset);received+=part.length;progress(received,bytes.length);
        }
      }));
      return JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(bytes));
    } finally {
      try {await read(path,{method:'DELETE',headers},false,3000);} catch (_) { /* Server also expires abandoned results. */ }
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {prepareUpload,receiveDemand,readLocal,normalizeLocal};
  else root.MapImport = {prepareUpload,receiveDemand,readLocal,normalizeLocal};
})(typeof globalThis !== 'undefined' ? globalThis : this);
