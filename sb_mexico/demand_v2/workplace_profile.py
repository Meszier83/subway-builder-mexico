"""Preserve estimated job proportions on complete allowed support.

The marginals are model quantities scaled to retained commuters, never observed
headcounts. Structurally inaccessible weights are explicitly reported.
"""
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components,maximum_flow


def targets_for_support(origins,destinations,rows,cols):
    nr,nd=len(origins),len(destinations)
    graph=coo_matrix((np.ones(len(rows)),(rows,nr+cols)),shape=(nr+nd,nr+nd)).tocsr()
    _,labels=connected_components(graph,directed=False)
    targets=np.zeros(nd)
    reachable=np.bincount(cols,minlength=nd)>0
    groups=[]
    for component in sorted(set(labels[:nr])):
        source=np.flatnonzero(labels[:nr]==component)
        jobs=np.flatnonzero((labels[nr:]==component)&reachable)
        mass=sum(origins[i]['commuters'] for i in source)
        if not len(jobs):raise ValueError('Origin component without workplace support')
        weights=np.asarray([destinations[j]['attraction'] for j in jobs],float)
        targets[jobs]=weights/weights.sum()*mass
        groups.append(dict(component=int(component),commuters=int(mass),attraction=float(weights.sum())))
    unavailable=[dict(id=p['id'],attraction=p['attraction'],reason='No permissible origin after hard restrictions')
                 for j,p in enumerate(destinations) if not reachable[j]]
    return targets,dict(semantics='Estimated attraction shares normalized independently on connected permitted territories; not observed headcounts',
                        support='complete_permitted',components=groups,unreachable_attraction=unavailable)


def check_feasibility(budgets,targets,rows,cols):
    """Independent compiled max-flow check at milliperson resolution."""
    nr,nd=len(budgets),len(targets);source=nr+nd;sink=source+1
    scale=min(1000,max(1,int((np.iinfo(np.int32).max-1)//max(1,budgets.sum()))))
    supplies=np.rint(budgets*scale).astype(np.int64)
    desired=targets*scale;demands=np.floor(desired).astype(np.int64)
    graph=coo_matrix((np.ones(len(rows)),(rows,nr+cols)),shape=(nr+nd,nr+nd)).tocsr()
    _,labels=connected_components(graph,directed=False)
    # Apportion numerical check units inside each disconnected territory.
    # Global apportionment can move millipersons between an island and mainland.
    for component in set(labels[:nr]):
        jobs=np.flatnonzero(labels[nr:]==component)
        remainder=int(supplies[labels[:nr]==component].sum()-demands[jobs].sum())
        order=np.argsort(-(desired[jobs]-demands[jobs]),kind='stable')
        demands[jobs[order[:remainder]]]+=1
    rr=np.r_[np.full(nr,source),rows,nr+np.arange(nd)]
    cc=np.r_[np.arange(nr),nr+cols,np.full(nd,sink)]
    capacities=np.r_[supplies,supplies[rows],demands]
    matrix=coo_matrix((capacities,(rr,cc)),shape=(sink+1,sink+1)).tocsr()
    result=maximum_flow(matrix,source,sink,method='dinic')
    if result.flow_value!=int(supplies.sum()):
        raise ValueError('Estimated workplace distribution infeasible on complete permitted support; '
                         f'deficit={(supplies.sum()-result.flow_value)/scale:.3f} commuters. No export produced')


def project_margins(flow,rows,cols,budgets,targets):
    """Finish numerical balancing before cohort rounding; retain structural zeros."""
    flow=flow.copy();nr,nd=len(budgets),len(targets)
    for iteration in range(10000):
        columns=np.bincount(cols,weights=flow,minlength=nd)
        if np.any((targets>0)&(columns<=0)):raise ValueError('Workplace target lost numerical support')
        factors=np.divide(targets,columns,out=np.zeros(nd),where=columns>0)
        flow*=factors[cols]
        sums=np.bincount(rows,weights=flow,minlength=nr)
        flow*=np.divide(budgets,sums)[rows]
        residual=np.abs(np.bincount(cols,weights=flow,minlength=nd)-targets)
        relative=float(np.max(residual/np.maximum(1.,targets),initial=0.))
        if relative<1e-8:
            return flow,dict(iterations=iteration+1,max_destination_error_persons=float(residual.max(initial=0.)),
                             max_relative_error=relative)
    raise ValueError('Workplace marginal balance did not converge; no export produced')
