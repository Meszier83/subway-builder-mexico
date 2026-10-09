"""Round cohort counts on permitted support before selecting individual jobs.

Municipal counts in each size/territory bucket are floor/ceil roundings of the
continuous solution. A capacitated network LP has integer margins and bounds;
its integral solution preserves origins without fitting held-out observations.
"""
from collections import Counter
import hashlib
import math
import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix


def assign(flow, rows, cols, origins, destinations, sizes_by_origin, seed, select_jobs=True):
    offsets=np.r_[0,np.cumsum(np.bincount(rows,minlength=len(origins)))]
    local={}
    municipal_names=sorted({d.get('municipality','__all__') for d in destinations})
    municipal_index={m:j for j,m in enumerate(municipal_names)}
    destination_municipal=np.asarray([municipal_index[d.get('municipality','__all__')] for d in destinations])
    for i,o in enumerate(origins):
        start,end=offsets[i],offsets[i+1]
        municipal=destination_municipal[cols[start:end]]
        region=(o.get('municipality','__all__'),o.get('zone',0),o.get('road_component'))
        for m in np.unique(municipal):
            key=(region,municipal_names[m])
            local[i,key]=start+np.flatnonzero(municipal==m)
    distribution={i:[] for i in range(len(origins))}
    for (i,key),edges in local.items():
        distribution[i].append((key,edges,float(flow[edges].sum())))
    integer_flow=np.zeros(len(flow),dtype=np.int64)
    chunks={}
    diagnostics=[]
    parts=[]
    for size in sorted({s for ss in sizes_by_origin for s in ss},reverse=True):
        counts={i:Counter(ss)[size] for i,ss in enumerate(sizes_by_origin) if size in ss}
        active=sorted(counts)
        row_index={i:k for k,i in enumerate(active)}
        groups=sorted({key for i in active for key,_,_ in distribution[i]},key=repr)
        group_index={key:len(active)+k for k,key in enumerate(groups)}
        expected,targets={},Counter()
        for i in active:
            total=sum(x for _,_,x in distribution[i])
            if total<=0: raise ValueError('Cohort origin has no positive continuous support')
            for key,edges,mass in distribution[i]:
                value=counts[i]*mass/total
                expected[i,key]=value
                targets[key]+=value
        rr,cc,data,cost,bounds,arcs=[],[],[],[],[],[]
        def arc(i,key,cap,price):
            if not cap: return
            v=len(cost); cost.append(price); bounds.append((0,int(cap))); arcs.append((i,key))
            rr.extend((row_index[i],group_index[key])); cc.extend((v,v)); data.extend((1.,1.))
        for (i,key),value in expected.items():
            base=min(counts[i],int(math.floor(value+1e-9)))
            fraction=max(0.,value-base)
            arc(i,key,base,-1.)
            if base<counts[i]:
                arc(i,key,1,1.-2.*fraction)
                arc(i,key,counts[i]-base-1,1.)
        rhs=[counts[i] for i in active]
        for key in groups:
            value=targets[key]; base=int(math.floor(value+1e-9)); rhs.append(base)
            if value-base>1e-9:
                v=len(cost); cost.append(-1e-7*(value-base)); bounds.append((0,1)); arcs.append(None)
                rr.append(group_index[key]); cc.append(v); data.append(-1.)
        matrix=coo_matrix((data,(rr,cc)),shape=(len(rhs),len(cost))).tocsr()
        solution=linprog(cost,A_eq=matrix,b_eq=rhs,bounds=bounds,method='highs')
        if not solution.success:
            raise ValueError('Municipal cohort rounding infeasible: '+solution.message)
        rounded=np.rint(solution.x).astype(np.int64)
        if np.max(np.abs(rounded-solution.x))>1e-6 or np.max(np.abs(matrix@rounded-rhs))>1e-6:
            raise ValueError('Cohort network did not produce an integral feasible assignment')
        assigned=Counter()
        for count,edge in zip(rounded,arcs):
            if edge is not None: assigned[edge]+=int(count)
        realized=Counter()
        for (i,key),count in assigned.items():
            if not count: continue
            realized[key]+=count
            edges=local[i,key]
            if not select_jobs:
                parts.append((i,key,edges,int(count)))
                continue
            cumulative=np.cumsum(flow[edges])
            phase=int(hashlib.sha256(repr((origins[i]['id'],key,size,seed)).encode()).hexdigest()[:13],16)/16**13
            positions=(np.arange(count)+phase)*cumulative[-1]/count
            selected=np.searchsorted(cumulative,positions,side='right')
            for e in np.asarray(edges)[selected]:
                integer_flow[e]+=size
                chunks.setdefault(int(e),[]).append(int(size))
        if sum(realized.values())!=sum(counts.values()):
            raise ValueError('Cohort network changed cohort count')
        error=max((abs(realized[key]-targets[key]) for key in groups),default=0.)
        if error>1.+1e-7: raise ValueError('Municipal bucket rounding exceeded one cohort')
        diagnostics.append(dict(size=int(size),cohorts=sum(counts.values()),max_group_count_error=float(error)))
    return (parts,diagnostics) if not select_jobs else (integer_flow,chunks,diagnostics)
