"""Joint job selection before freezing OD, preserving cohort inventories.

Each size bucket carries forward prior destination rounding errors. Municipal
person parts and original budgets stay fixed; no extra cohorts are introduced.
"""
from collections import Counter
import hashlib
import numpy as np
from sb_mexico.od_allocation import integer_transport
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def feedback_targets(expected,debt,size,allowed,supplies,kernel):
    if np.max(np.abs(debt),initial=0.)<1e-9:return expected.copy(),dict(iterations=0,max_goal_error_counts=0.)
    rows,cols=np.nonzero(allowed);nr,nd=allowed.shape
    graph=coo_matrix((np.ones(len(rows)),(rows,nr+cols)),shape=(nr+nd,nr+nd)).tocsr()
    _,labels=connected_components(graph,directed=False)
    goal=np.where(expected>0,np.maximum(0.,expected+debt/size),0.)
    for component in set(labels[:nr]):
        jobs=np.flatnonzero(labels[nr:]==component)
        mass=int(supplies[labels[:nr]==component].sum())
        if goal[jobs].sum()<=0:goal[jobs]=expected[jobs]
        goal[jobs]*=mass/goal[jobs].sum()
    # Project the feedback through actual row counts and permitted edges.
    # The last row-normalized matrix is always feasible, even when the desired
    # correction cannot be reached (e.g. a part with only one available job).
    original=kernel.copy()
    for iteration in range(50):
        columns=kernel.sum(axis=0)
        kernel*=np.divide(goal,columns,out=np.zeros(nd),where=columns>0)
        row_sums=kernel.sum(axis=1)
        empty=row_sums<=0
        # Some parts may be forced to an already over-rounded workplace.
        # Restore those rows instead of dropping their cohorts or discarding
        # feedback for every other destination in the bucket.
        kernel[empty]=original[empty];row_sums[empty]=kernel[empty].sum(axis=1)
        if np.any(row_sums<=0):raise ValueError('Rounding feedback lost a cohort origin')
        kernel*=np.divide(supplies,row_sums)[:,None]
        adjusted=kernel.sum(axis=0)
        residual=float(np.abs(adjusted-goal).max(initial=0.))
        if residual<1e-8:break
    adjusted*=int(supplies.sum())/adjusted.sum()
    return adjusted,dict(iterations=iteration+1,max_goal_error_counts=residual)


def round_jobs(flow,rows,cols,origins,destinations,distance,parts,inventories,seed):
    integer=np.zeros(len(flow),dtype=np.int64);chunks={};origin_counts=Counter();checks=[]
    nd=len(destinations);destination_ids=[p['id'] for p in destinations]
    indices={p['id']:j for j,p in enumerate(destinations)}
    expected_people=np.zeros(nd);realized_people=np.zeros(nd)
    for size in sorted({s for inventory in inventories for s in inventory},reverse=True):
        active=[p for p,inventory in enumerate(inventories) if inventory[size]]
        supplies=np.asarray([inventories[p][size] for p in active],dtype=np.int64)
        allowed=np.zeros((len(active),nd),dtype=bool)
        distances=np.full((len(active),nd),np.inf,dtype=np.float32)
        kernel=np.zeros((len(active),nd))
        targets=np.zeros(nd);proposal=Counter()
        for local,p in enumerate(active):
            i,key,edges,budget=parts[p];edges=np.asarray(edges,int)
            jobs=cols[edges];weights=flow[edges];mass=weights.sum()
            if mass<=0:raise ValueError('Municipal part has no positive workplace distribution')
            allowed[local,jobs]=True;distances[local,jobs]=distance[edges]
            kernel[local,jobs]=supplies[local]*weights/mass
            targets[jobs]+=supplies[local]*weights/mass
            cumulative=np.cumsum(weights)
            phase=int(hashlib.sha256(repr((origins[i]['id'],key,size,seed)).encode()).hexdigest()[:13],16)/16**13
            selected=np.searchsorted(cumulative,(np.arange(supplies[local])+phase)*mass/supplies[local],side='right')
            for j in jobs[selected]:proposal[str(p),destination_ids[int(j)]]+=1
        # Remove accumulated floating-point error without changing proportions.
        targets*=int(supplies.sum())/targets.sum()
        expected_people+=size*targets
        adjusted,projection=feedback_targets(targets,expected_people-size*targets-realized_people,size,allowed,supplies,kernel)
        assigned,realized,attempts=integer_transport([str(p) for p in active],supplies,destination_ids,
                                                    adjusted,proposal,allowed,distances)
        for (part_id,job_id),count in assigned.items():
            p=int(part_id);i,key,edges,budget=parts[p]
            j=indices[job_id];job_indices=cols[edges]
            position=int(np.searchsorted(job_indices,j))
            if position>=len(edges) or job_indices[position]!=j:raise ValueError('Cohort transport escaped municipal support')
            edge=int(edges[position]);integer[edge]+=size*count
            chunks.setdefault(edge,[]).extend([int(size)]*count)
            origin_counts[origins[i]['id']]+=count
        realized_people+=size*np.asarray([realized[job] for job in destination_ids])
        error=max((abs(realized[job]-adjusted[j]) for j,job in enumerate(destination_ids)),default=0.)
        checks.append(dict(size=int(size),cohorts=int(supplies.sum()),max_destination_count_error=float(error),
                           feedback_projection=projection,
                           cumulative_max_destination_error_people=float(np.abs(realized_people-expected_people).max(initial=0.)),
                           support_attempts=attempts))
    return integer,chunks,origin_counts,checks
