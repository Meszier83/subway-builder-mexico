"""Reserve configured special demand, then allocate ordinary commuters.

POIs consume the same origin budgets. Reserve whole maximum-size pieces first,
then retain necessary small remainders; no geographic pooling occurs here.
"""
from collections import Counter
import copy
import math
import numpy as np
from .allocation import support,municipal_score
from .cohorts import settings,integerize
from sb_mexico.workplace_employment import bounded_fit


def allocate_with_pois(points,config,mobility,continuous):
    options=config.get('cohort_settings') or settings({'demand':config})
    maximum=options['max_pop_size']
    origins,destinations,rows,cols,distance=support(points,dict(config,_reservation_only=True))
    remaining=np.asarray([o['commuters'] for o in origins],dtype=np.int64)
    specials=sorted([j for j,d in enumerate(destinations) if d.get('is_special')],
                    key=lambda j:(not destinations[j]['id'].startswith('AIR_'),destinations[j]['id']))
    reserved=[]; quotas=[]
    for j in specials:
        poi=destinations[j]
        edges=np.flatnonzero(cols==j)
        eligible=rows[edges]
        requested=int(round(poi['attraction']))
        target=min(requested,int(remaining[eligible].sum()))
        capacities=remaining[eligible]//maximum
        full=min(target//maximum,int(capacities.sum()))
        weights=np.maximum(remaining[eligible]*np.exp(-.04*distance[edges]),1e-12)
        taken=np.zeros(len(eligible),dtype=np.int64)
        if full:
            fitted,_=bounded_fit(weights,np.zeros(len(eligible)),capacities,full)
            counts=np.floor(fitted).astype(np.int64)
            residual=full-int(counts.sum())
            order=sorted([i for i in range(len(counts)) if counts[i]<capacities[i]],
                         key=lambda i:(-(fitted[i]-counts[i]),origins[eligible[i]]['id']))
            for i in order[:residual]: counts[i]+=1
            if counts.sum()!=full: raise ValueError('POI whole-cohort reservation changed quota')
            taken=counts*maximum
        residual=target-int(taken.sum())
        for i in sorted(range(len(eligible)),key=lambda i:(-weights[i],origins[eligible[i]]['id'])):
            take=min(residual,int(remaining[eligible[i]]-taken[i]))
            taken[i]+=take; residual-=take
            if not residual: break
        if residual: raise ValueError('POI reservation could not realize its reachable quota')
        for edge,count in zip(edges,taken):
            if count:
                remaining[rows[edge]]-=count
                reserved.append((int(rows[edge]),int(j),int(count),float(distance[edge])))
        quotas.append(dict(id=poi['id'],requested=requested,realized=target,shortfall=requested-target,
                           basis='Configured attraction after DENUE capture; not observed commuters',
                           priority='airports first, then stable POI ID'))
    regular=copy.deepcopy(points)
    source_remaining={o['id']:int(b) for o,b in zip(origins,remaining)}
    for p in regular:
        p['commuters']=source_remaining.get(p['id'],0)
        if p.get('is_special'): p['attraction']=0
    parameters=dict(config,_continuous_only=True)
    if remaining.sum():
        data,report=continuous(regular,parameters,mobility)
        regular_flow,rr,rc,rd,ro,dd=data
    else:
        regular_flow=np.array([]); rr=rc=np.array([],int); rd=np.array([]); ro=dd=[]
        report=dict(beta=config.get('beta') or .12,beta_calibrated=False,support_edges=0,row_max_residual=0.)
    # Cohort counts are chosen jointly across ordinary and reserved segments,
    # so explicit totals remain exact even when POI quotas require small pieces.
    segments=[]; segment_sources={}
    destination_index={p['id']:j for j,p in enumerate(destinations)}
    for i,o in enumerate(ro):
        segment=dict(o,id='regular:'+o['id']); k=len(segments)
        segments.append(segment); segment_sources[segment['id']]=o['id']
    destination_map=np.asarray([destination_index[d['id']] for d in dd],dtype=np.int32)
    reserved_rows=[]; reserved_cols=[]; reserved_flow=[]; reserved_dist=[]
    for i,j,count,d in reserved:
        segment=dict(origins[i],id='poi:'+repr((origins[i]['id'],destinations[j]['id'])),commuters=count)
        k=len(segments); segments.append(segment); segment_sources[segment['id']]=origins[i]['id']
        reserved_flow.append(float(count)); reserved_rows.append(k); reserved_cols.append(j); reserved_dist.append(d)
    flow=np.r_[regular_flow,reserved_flow]
    r=np.r_[rr,np.asarray(reserved_rows,dtype=np.int32)]
    c=np.r_[destination_map[rc],np.asarray(reserved_cols,dtype=np.int32)]
    dist=np.r_[rd,reserved_dist]
    cells,integer_flow,cohorts=integerize(flow,r,c,segments,destinations,dist,options)
    origin_counts=Counter()
    for cell in cells:
        cell['origin']=segment_sources[cell['origin']]
        origin_counts[cell['origin']]+=len(cell['cohort_sizes'])
    cohorts['origins']=dict(origin_counts)
    budget=Counter({o['id']:o['commuters'] for o in origins})
    realized=Counter(); arrivals=Counter()
    for cell in cells:
        realized[cell['origin']]+=cell['size']; arrivals[cell['destination']]+=cell['size']
    if realized!=budget or any(arrivals[q['id']]!=q['realized'] for q in quotas):
        raise ValueError('Final cohorts changed residential or POI budgets')
    report.update(commuters=int(sum(budget.values())),cohorts=cohorts,
        poi_additional_cohorts=sum(math.ceil(s['commuters']/maximum) for s in segments)-sum(math.ceil(o['commuters']/maximum) for o in origins),
        poi_quotas=quotas,poi_policy='configured_attraction_reserved',
        validation=municipal_score(flow,r,c,segments,destinations,mobility,'validation_weight'),
        validation_integer=municipal_score(integer_flow,r,c,segments,destinations,mobility,'validation_weight'),
        rounding_l1_persons=float(np.abs(integer_flow-flow).sum()),integer_row_max_residual=0.,
        beta_basis='Ordinary entropy assignment; special reservations use declared beta 0.04')
    return cells,report
