"""Cohort resolution is an allocation constraint, before the integer OD is frozen."""
import math
from collections import Counter
import numpy as np
from sb_mexico.workplace_employment import bounded_fit


def settings(config):
    demand, macro = config.get('demand', {}), config.get('macroeconomics', {})
    fixed = demand.get('fixed_cohort_size')
    if fixed is not None and (type(fixed) is not int or fixed <= 0):
        raise ValueError('demand.fixed_cohort_size must be a positive integer')
    result = {key:demand.get(key, macro.get(key, default)) for key,default in
              [('min_pop_size',1),('target_pop_size',200),('max_pop_size',200)]}
    if 'target_pop_size' not in demand and 'target_pop_size' not in macro:
        result['target_pop_size'] = result['max_pop_size']
    if fixed is not None:
        result.update(min_pop_size=fixed,target_pop_size=fixed,max_pop_size=fixed)
    for key,value in result.items():
        if type(value) is not int or value <= 0:
            raise ValueError(key+' must be a positive integer')
    if not result['min_pop_size'] <= result['target_pop_size'] <= result['max_pop_size']:
        raise ValueError('Cohort sizes require min_pop_size <= target_pop_size <= max_pop_size')
    result['cohort_count'] = demand.get('cohort_count')
    count = result['cohort_count']
    if count is not None and (type(count) is not int or count <= 0):
        raise ValueError('demand.cohort_count must be a positive integer or null')
    exact = demand.get('exact_cohort_size')
    if exact is not None and type(exact) is not bool:
        raise ValueError('demand.exact_cohort_size must be a boolean')
    exact = bool(exact)
    result['seed'] = config.get('city', {}).get('seed',42)
    result['fixed_size'] = fixed or (result['target_pop_size'] if
        result['min_pop_size']==result['target_pop_size']==result['max_pop_size'] else None)
    if exact and result['fixed_size'] is None:
        raise ValueError('demand.exact_cohort_size requires a fixed cohort size')
    result['exact_size'] = exact
    if result['fixed_size'] and count is not None:
        raise ValueError('Fixed cohort size cannot be combined with cohort_count; clear the total count')
    return result


def integerize(flow, rows, cols, origins, destinations, distance, options):
    from .municipal_cohorts import assign
    original_budgets=np.asarray([p['commuters'] for p in origins],dtype=np.int64)
    if options.get('exact_size'):
        size = int(options['fixed_size'])
        if any(int(b) % size != 0 for b in original_budgets if b > 0):
            raise ValueError('Exact cohort mode requires origin budgets quantized to fixed_size')
        sizes_by_origin = [[size] * (int(b) // size) for b in original_budgets]
        parts, buckets = assign(flow, rows, cols, origins, destinations,
                                sizes_by_origin, options['seed'], select_jobs=False)
        parts_with_person_budgets = []
        inventories = []
        for i, key, edges, k in parts:
            k = int(k)
            if not k:
                continue
            parts_with_person_budgets.append((i, key, edges, k * size))
            inventories.append(Counter({size: k}))
        parts = parts_with_person_budgets
        counts = np.asarray([inv[size] for inv in inventories], dtype=np.int64)
        from .destination_cohorts import round_jobs
        integer_flow, chunks, origin_counts, destination_checks = round_jobs(
            flow, rows, cols, origins, destinations, distance, parts, inventories, options['seed']
        )
        if not np.array_equal(np.bincount(rows, weights=integer_flow, minlength=len(origins)), original_budgets):
            raise ValueError('Municipal cohort rounding changed original budgets')
        cells = []
        for edge, pieces in sorted(chunks.items()):
            cells.append(dict(origin=origins[rows[edge]]['id'], destination=destinations[cols[edge]]['id'],
                              size=sum(pieces), distance_km=float(distance[edge]), cohort_sizes=pieces))
        total_cohorts = int(original_budgets.sum() // size)
        return cells, integer_flow, dict(
            options=options, requested_count=None, actual_count=total_cohorts,
            minimum_feasible=total_cohorts, maximum_feasible=total_cohorts,
            origins=dict(origin_counts),
            method='Exact whole-cohort municipal LP and joint destination transport; zero sub-cohort remainders',
            municipal_rounding_unit='cohort',
            municipal_rounding=buckets,
            destination_rounding=destination_checks,
            max_destination_rounding_error_people=float(np.max(np.abs(
                np.bincount(cols, weights=integer_flow, minlength=len(destinations)) -
                np.bincount(cols, weights=flow, minlength=len(destinations))
            ), initial=0.)),
            below_preferred_minimum=0
        )
    parts,buckets=assign(flow,rows,cols,origins,destinations,
                         [[1]*int(b) for b in original_budgets],options['seed'],select_jobs=False)
    # Quantize people first. A regional movement smaller than the preferred
    # cohort survives as a small piece rather than disappearing into local trips.
    budgets=np.asarray([part[3] for part in parts],dtype=np.int64)
    maximum, target, minimum = (options[k] for k in ('max_pop_size','target_pop_size','min_pop_size'))
    lower = np.asarray([math.ceil(int(b)/maximum) for b in budgets],dtype=np.int64)
    # The minimum is a preference; small residential budgets always survive.
    upper = np.asarray([max(int(lo),int(b)//minimum) for b,lo in zip(budgets,lower)],dtype=np.int64)
    requested = options['cohort_count']
    if requested is None:
        counts = np.clip(np.rint(budgets/target).astype(np.int64),lower,upper)
    else:
        if not int(lower.sum()) <= requested <= int(budgets.sum()):
            raise ValueError(f'Requested {requested} cohorts infeasible: minimum {int(lower.sum())} '
                             f'(origins and max_pop_size={maximum}), maximum {int(budgets.sum())} (one person per cohort)')
        # Explicit total takes precedence over the preferred minimum, never over maximum.
        capacities = budgets-lower
        if requested == int(lower.sum()):
            counts = lower.copy()
        else:
            fitted,_ = bounded_fit(np.maximum(budgets/target-lower,1e-9),np.zeros(len(budgets)),capacities,
                                   requested-int(lower.sum()))
            extra = np.floor(fitted).astype(np.int64)
            remainder = requested-int(lower.sum())-int(extra.sum())
            order = sorted(range(len(budgets)),key=lambda i:(-(fitted[i]-extra[i]),repr(parts[i][:2])))
            for i in [i for i in order if extra[i]<capacities[i]][:remainder]: extra[i]+=1
            counts = lower+extra
        if int(counts.sum()) != requested:
            raise ValueError('Could not allocate the exact requested cohort count')
    inventories=[]
    for part,k in zip(parts,counts):
        i,key,edges,budget=part
        k=int(k)
        if not k:
            continue
        base, remainder = divmod(budget,k)
        sizes = np.asarray([base+(j<remainder) for j in range(k)],dtype=np.int64)
        if requested is None and minimum == target == maximum:
            sizes = np.asarray([maximum]*(k-1)+[budget-maximum*(k-1)],dtype=np.int64)
        inventories.append(Counter(int(s) for s in sizes))
    from .destination_cohorts import round_jobs
    integer_flow,chunks,origin_counts,destination_checks=round_jobs(flow,rows,cols,origins,destinations,distance,
                                                                   parts,inventories,options['seed'])
    if not np.array_equal(np.bincount(rows,weights=integer_flow,minlength=len(origins)),original_budgets):
        raise ValueError('Municipal person rounding changed original budgets')
    cells=[]
    for edge,pieces in sorted(chunks.items()):
        cells.append(dict(origin=origins[rows[edge]]['id'],destination=destinations[cols[edge]]['id'],
                              size=sum(pieces),distance_km=float(distance[edge]),cohort_sizes=pieces))
    return cells,integer_flow,dict(options=options,requested_count=requested,actual_count=int(counts.sum()),
        minimum_feasible=int(lower.sum()),maximum_feasible=int(budgets.sum()),
        origins=dict(origin_counts),
        method='Municipal person parts, then joint destination counts with accumulated rounding feedback; exact budgets and inventory',
        municipal_rounding_unit='person',
        municipal_rounding=buckets,
        destination_rounding=destination_checks,
        max_destination_rounding_error_people=float(np.max(np.abs(np.bincount(cols,weights=integer_flow,minlength=len(destinations))-
                                                          np.bincount(cols,weights=flow,minlength=len(destinations))),initial=0.)),
        below_preferred_minimum=sum(size<minimum for c in cells for size in c['cohort_sizes']))
