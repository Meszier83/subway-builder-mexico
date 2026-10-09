"""Join approximate road components only when OSRM proves both directions."""
import numpy as np


def reconcile(request, points):
    provider=request.route_provider
    if provider is None:
        return dict(status='not_probed',reason='No authoritative routing provider')
    jobs=[p for p in points if p['attraction']>0]
    if not jobs:return dict(status='no_jobs',unions=[])
    parents={p['road_component']:p['road_component'] for p in points if p.get('road_component') is not None}
    def find(c):
        if c is None:return None
        while parents[c]!=c:
            parents[c]=parents[parents[c]];c=parents[c]
        return c
    max_distance=request.config.get('demand',{}).get('max_distance_km',
        request.config.get('macroeconomics',{}).get('max_distance_km'))
    scale=np.array([111.32*np.cos(np.deg2rad(np.mean([p['location'][1] for p in points]))),110.574])
    coordinates=np.asarray([p['location'] for p in jobs])
    evidence=[];queried=0
    for origin in points:
        component=find(origin.get('road_component'))
        if not origin['commuters'] or component is None:continue
        distances=np.linalg.norm((coordinates-origin['location'])*scale,axis=1)
        candidates=[j for j,q in enumerate(jobs) if q['zone']==origin['zone'] and
            q['id']!=origin['id'] and q['location']!=origin['location'] and
            (max_distance is None or distances[j]<=max_distance)]
        if any(find(jobs[j].get('road_component'))==component for j in candidates):continue
        candidates=sorted(candidates,key=lambda j:(distances[j],jobs[j]['id']))[:5]
        probes=[]
        for j in candidates:
            target=jobs[j]
            for a,b in ((origin,target),(target,origin)):
                probes.append(dict(id='connectivity_'+str(len(probes)),residenceId=a['id'],jobId=b['id'],size=1))
        if not probes:continue
        records=provider(probes,points,False);queried+=len(probes)
        for j in candidates:
            target=jobs[j];other=find(target.get('road_component'))
            if other is None or other==component:continue
            forward=records.get((origin['id'],target['id']))
            reverse=records.get((target['id'],origin['id']))
            if tuple(forward or ())!=('osrm','accepted') or tuple(reverse or ())!=('osrm','accepted'):continue
            canonical=min(component,other);parents[max(component,other)]=canonical
            evidence.append(dict(origin=origin['id'],destination=target['id'],components=[component,other],
                merged_component=canonical,forward=list(forward),reverse=list(reverse)))
            break
    for point in points:
        if point.get('road_component') is not None:point['road_component']=find(point['road_component'])
    return dict(status='probed',queried_pairs=queried,unions=evidence,
                basis='Both directions accepted by the configured OSRM network; no fallback joins')
