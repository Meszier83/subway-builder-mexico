const assert = require('node:assert/strict');
const A = require('../tools/static/map-analyzer/core.js');
const points = [
  {id:'a',location:[-86.85,21.1],residents:100,jobs:0},
  {id:'b',location:[-86.85,21.1],residents:30,jobs:0},
  {id:'c',location:[-86.82,21.103],residents:0,jobs:110},
  {id:'d',location:[-86.79,21.108],residents:0,jobs:20},
];
const bins = A.grid(points,500);
assert.equal(bins.cells.reduce((s,c)=>s+c.residents,0),130,'coincident points retain all people');
assert.equal(bins.cells.reduce((s,c)=>s+c.jobs,0),130,'grid conserves jobs');
assert.equal(bins.byPoint.get('a'),bins.byPoint.get('b'),'coincident IDs share a cell');
const pops = [{residenceId:'a',jobId:'c',size:100},{residenceId:'b',jobId:'c',size:10},{residenceId:'b',jobId:'d',size:20}];
let flows = A.flows(pops,bins);
assert.equal(flows.total,130);assert.equal(flows.links.length,2);assert.equal(flows.links[0].size,110,'sum group sizes, not number of groups');
flows = A.flows(pops,bins,new Set(['b']),'out');
assert.equal(flows.total,30);assert.equal(flows.links[0].size,20);
flows = A.flows(pops,bins,new Set(['c']),'in');
assert.equal(flows.total,110);
flows = A.flows([...pops,{residenceId:'a',jobId:'b',size:4}],bins);
assert.equal(flows.internal,4);assert.equal(flows.total,134,'internal trips included once');
const area = A.select(points,points[0].location,800);
assert.equal(area.residents,130);assert.equal(area.jobs,0);assert.equal(area.ids.size,2);
assert.ok(Math.abs(A.distance([0,0],[0,1])-111195)<2,'radius uses ground distance');
assert.equal(A.correlation([{residents:1,jobs:3},{residents:2,jobs:6},{residents:3,jobs:9}]).r,1);
assert.equal(A.correlation([{residents:1,jobs:3},{residents:2,jobs:2},{residents:3,jobs:1}]).r,-1);
assert.equal(A.correlation([{residents:1,jobs:3},{residents:1,jobs:6},{residents:1,jobs:9}]).r,null);
assert.equal(A.correlation([{residents:0,jobs:0},{residents:1,jobs:3},{residents:2,jobs:6},{residents:3,jobs:9}]).n,3,'empty cells do not create correlation');
assert.equal(A.correlation([{residents:1,jobs:3}]).r,null);
const one = A.heatField([{x:40,y:40,weight:1}],80,80,20);
const many = A.heatField([{x:40,y:40,weight:100}],80,80,20);
assert.ok(Math.abs(many.max/one.max-100)<1e-10,'heat intensity is proportional to people');
const split = A.heatField([{x:40,y:40,weight:70},{x:40,y:40,weight:30}],80,80,20);
assert.deepEqual(split.values,many.values,'splitting a group does not change the heatmap');
const padded = A.grid([...points,{...points[1],id:'extra',residents:0}],500);
for(const point of points)assert.equal(padded.byPoint.get(point.id),bins.byPoint.get(point.id),'splitting points does not move the grid');
assert.equal(A.concentration([{residents:90,jobs:0},{residents:10,jobs:0}],'residents'),.9);
const surface = A.heatSurface(points,'residents',600);
const surfaceMass = surface.values.reduce((sum,value)=>sum+value,0);
assert.ok(Math.abs(surfaceMass-130)<1e-8,'geographic heat conserves weighted mass including its padded edges');
const unsplit = A.heatSurface([{...points[0],residents:130},...points.slice(2)],'residents',600);
assert.equal(unsplit.values.length,surface.values.length);
for(let i=0;i<surface.values.length;i++)assert.ok(Math.abs(unsplit.values[i]-surface.values[i])<1e-12,'coincident groups retain geographic heat within floating point precision');
assert.ok(Math.abs(unsplit.peakDensity-surface.peakDensity)<1e-9);
for(const lat of [0,21,60]){
  const local=A.heatSurface([{location:[0,lat],residents:100}],'residents',600);
  const width=A.distance([local.bounds[0][0],lat],[local.bounds[1][0],lat]);
  assert.ok(Math.abs(width-local.width*local.cellM)<1,'raster width follows ground metres across latitudes');
}
assert.equal(A.heatTone(0,3),0,'no intensity is added to empty space');
assert.equal(A.heatTone(1,3),1,'strength preserves the peak endpoint');
assert.ok(A.heatTone(.1,1.8)>A.heatTone(.1,1),'strong default makes intermediate demand more visible');
assert.ok(A.heatTone(.2,3)<A.heatTone(.8,3),'strong display preserves density order');
assert.equal(A.heatSurface(points,'missing',600).max,0,'empty layers have no heat');
const globalSurface = A.heatSurface([
  {location:[-120,-50],residents:10},{location:[120,50],residents:20}
],'residents',200);
assert.ok(globalSurface.values.length<1100000,'wide maps have bounded raster memory');
assert.ok(Number.isFinite(globalSurface.peakDensity));
assert.deepEqual(points.map(p=>p.residents),[100,30,0,0],'visual changes never rewrite supplied counts');
console.log('Map analyzer: weighted mass, geographic heat, fixed scale, contrast ordering, raster limits, flows, radius, and correlation PASS');

const fixturePoints = [
  {id:'h1',location:[0,0],residents:100,jobs:0}, {id:'h2',location:[.02,0],residents:50,jobs:0},
  {id:'w1',location:[0,.02],residents:0,jobs:90}, {id:'w2',location:[.02,.02],residents:0,jobs:60},
];
const fixtureTrips = [{residenceId:'h1',jobId:'w1',size:70},{residenceId:'h1',jobId:'w2',size:30},
  {residenceId:'h2',jobId:'w1',size:20},{residenceId:'h2',jobId:'w2',size:30}];
const fixture = {points:fixturePoints,pops:fixtureTrips,tripsAvailable:true};
const fixedGrid = A.createGridSpec(fixturePoints,500), fixedBins=A.grid(fixturePoints,500,fixedGrid);
const circle = A.selectGeometry(fixturePoints,{type:'circle',center:[0,0],radiusM:200},fixedGrid);
assert.equal(circle.residents,100); assert.equal(circle.residentShare,2/3);
const outgoing=A.selectedTrips(fixture,circle.ids,'out',fixedBins);
assert.equal(outgoing.total,100); assert.equal(outgoing.endpointWeights.get('w1'),70); assert.equal(outgoing.endpointWeights.get('w2'),30);
const both=A.selectedTrips(fixture,new Set(['h1','w1']),'in',fixedBins);
assert.equal(both.total,90);assert.equal(both.insideSelection,70);assert.equal(both.outsideSelection,20);
const ring=[[-.01,-.01],[.01,-.01],[.01,.01],[-.01,.01]];
assert.equal(A.selectGeometry(fixturePoints,{type:'polygon',vertices:ring},fixedGrid).residents,100);
assert.equal(A.selectGeometry([{...fixturePoints[0],location:ring[0]}],{type:'polygon',vertices:ring},fixedGrid).residents,100,'polygon includes vertices');
assert.ok(A.polygonInfo([[-.01,-.01],[.01,.01],[-.01,.01],[.01,-.01]],fixedGrid).error,'self-intersection rejected');
const fixtureB=structuredClone(fixture);fixtureB.pops[0].size-=10;fixtureB.pops[1].size+=10;fixtureB.points[2].jobs-=10;fixtureB.points[3].jobs+=10;
const unionSpec=A.createGridSpec([...fixture.points,...fixtureB.points],500), binsA=A.grid(fixture.points,500,unionSpec), binsB=A.grid(fixtureB.points,500,unionSpec);
const diff=A.compareTrips(fixture,fixtureB,binsA,binsB);
assert.equal(diff.reduce((s,r)=>s+r.delta,0),0);assert.equal(diff.reduce((s,r)=>s+Math.abs(r.delta),0),20);
assert.equal(A.compareCells(binsA,binsB).reduce((s,r)=>s+r.b.jobs-r.a.jobs,0),0);
assert.equal(A.compareTrips({...fixture,tripsAvailable:false},fixtureB,binsA,binsB),null);
const extendedPoints=[...fixtureB.points,{id:'far',location:[.01,.3],residents:0,jobs:0}];
const expanded=A.createGridSpec([...fixture.points,...extendedPoints],500);
const alignedA=A.grid(fixture.points,500,expanded), alignedB=A.grid(extendedPoints,500,expanded);
for(const p of fixture.points)assert.equal(alignedA.byPoint.get(p.id),alignedB.byPoint.get(p.id),'union extent aligns cells');
const reversed=A.compareTrips(fixtureB,fixture,binsB,binsA);
for(const r of diff){const opposite=reversed.find(v=>v.from===r.from&&v.to===r.to);assert.equal(opposite.delta,-r.delta || 0);}
const internal=A.compareTrips(fixture,{...fixture,pops:[]},binsA,binsA,new Set(['h1']),new Set(['h1']),'out');
assert.equal(internal.reduce((s,r)=>s+r.a,0),100,'comparison filters original endpoint identities');
const zero=A.selectGeometry([{id:'z',location:[0,0],residents:0,jobs:0}],{type:'circle',center:[0,0],radiusM:200},fixedGrid);
assert.equal(zero.balance,null);assert.equal(zero.residentShare,null);
const concave=[[-.01,-.01],[.03,-.01],[.03,.01],[.01,.01],[.01,.03],[-.01,.03]];
const polygonSelection=A.selectGeometry(fixturePoints,{type:'polygon',vertices:concave},fixedGrid);
assert.ok(polygonSelection.ids.has('h1') && polygonSelection.ids.has('w1') && !polygonSelection.ids.has('w2'),'concave polygon membership');
const renamed={points:fixture.points.map(p=>({...p,id:`new-${p.id}`})).reverse(),pops:fixture.pops.flatMap(p=>[
  {...p,residenceId:`new-${p.residenceId}`,jobId:`new-${p.jobId}`,size:1},
  {...p,residenceId:`new-${p.residenceId}`,jobId:`new-${p.jobId}`,size:p.size-1}]),tripsAvailable:true};
const renamedBins=A.grid(renamed.points,500,unionSpec);
assert.ok(A.compareTrips(fixture,renamed,binsA,renamedBins).every(row=>row.delta===0),'IDs and trip grouping do not change comparison');
const cellChanges=A.compareCells(binsA,A.grid(fixture.points.slice(1),500,unionSpec));
assert.ok(cellChanges.some(row=>row.a.residents===100&&row.b.residents===0),'removed cells remain in comparison');
const emptyDataset={...fixture,pops:[]};
assert.equal(A.compareTrips(fixture,emptyDataset,binsA,binsA).reduce((s,r)=>s+r.delta,0),-150,'empty trips are available and compare as zero');
const inconsistent={...fixture,points:fixture.points.map(p=>({...p,residents:p.id==='h1'?999:p.residents}))};
assert.equal(A.selectedTrips(inconsistent,new Set(['h1']),'out',binsA).total,100,'trip results do not silently repair mismatched point counts');
const coincident=[fixture.points[0],{...fixture.points[0],id:'extra',residents:20}];
assert.equal(A.selectGeometry(coincident,{type:'circle',center:[0,0],radiusM:200},fixedGrid).residents,120,'coincident point IDs remain distinct');
assert.ok(A.polygonInfo([[0,0],[1,0],[1,0],[0,1]],fixedGrid).error,'repeated polygon vertices rejected');
const nearby=[{id:'inside',location:[.001,.001],residents:5,jobs:0},{id:'outside',location:[.0011,.0011],residents:0,jobs:5}];
const nearbyBins=A.grid(nearby,500,A.createGridSpec(nearby,500));
assert.equal(nearbyBins.byPoint.get('inside'),nearbyBins.byPoint.get('outside'));
const crossing=A.selectedTrips({pops:[{residenceId:'inside',jobId:'outside',size:5}]},new Set(['inside']),'out',nearbyBins);
assert.equal(crossing.insideSelection,0,'same-cell trips can still leave a selection');assert.equal(crossing.outsideSelection,5);
assert.deepEqual(fixture.points,fixturePoints,'analysis does not mutate input');
const heatSpec=A.createHeatSpec(fixturePoints,600), sa=A.heatSurface(fixturePoints,'residents',600,heatSpec), sb=A.heatSurface(fixturePoints,'jobs',600,heatSpec);
assert.deepEqual(sa.bounds,sb.bounds);assert.equal(sa.cellM,sb.cellM);assert.ok(Math.abs(sa.values.reduce((s,v)=>s+v,0)-150)<1e-8);
console.log('Map analyzer: selections, weighted trip endpoints, aligned comparisons, and shared heat geometry PASS');

{
 const core=require('../tools/static/map-analyzer/core.js');
 const data={tripsAvailable:true,pops:[['h1','w1',70],['h1','w2',30],['h2','w1',20],['h2','w2',30]].map(([residenceId,jobId,size])=>({residenceId,jobId,size}))};
 const r=core.areaPairDemand(data,new Set(['h1']),new Set(['w1']));
 assert.equal(r.ab.count,70);assert.equal(r.ab.originShare,.7);assert.equal(r.ab.destinationShare,7/9);assert.equal(r.ba.originShare,null);
 const overlap={tripsAvailable:true,pops:[['x','y',10],['y','x',4],['s','s',3],['x','s',2],['s','y',5]].map(([residenceId,jobId,size])=>({residenceId,jobId,size}))};
 const o=core.areaPairDemand(overlap,new Set(['x','s']),new Set(['y','s']));
 assert.equal(o.ab.count,20);assert.equal(o.ba.count,7);assert.equal(o.sharedDirectionalWeight,3);assert.deepEqual(o.totals,{outA:20,inA:9,outB:12,inB:20});
 for(const d of [o.ab,o.ba]){assert.equal([...d.originWeights.values()].reduce((a,b)=>a+b,0),d.count);assert.equal([...d.destinationWeights.values()].reduce((a,b)=>a+b,0),d.count);}
 assert.equal(core.areaPairDemand({tripsAvailable:false},new Set(),new Set()).ab.count,null);
 console.log('Map analyzer: area-pair counts, denominators, overlap and unavailable trips PASS');
}

// Default display contrast must reveal weaker concentrations without clipping peaks.
assert.ok(A.heatTone(.1)>.35, '10% of peak has useful default visual intensity');
assert.ok(A.heatTone(.01)>.07, 'secondary concentrations remain visible without slider adjustment');
let previousTone=-1;
for(let i=0;i<=1000;i++) {const tone=A.heatTone(i/1000);assert.ok(tone>previousTone);previousTone=tone;}
assert.equal(A.heatTone(-1),0);assert.equal(A.heatTone(2),1);
assert.equal(A.heatTone(0),0);assert.equal(A.heatTone(1),1);
console.log('Map analyzer: default enhanced contrast preserves zero, peaks and strict ordering PASS');
