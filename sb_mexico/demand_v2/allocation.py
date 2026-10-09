"""Entropy assignment: exact origins/model job shares, soft municipal evidence."""
import math
import numpy as np
from scipy.optimize import minimize


def support(points, config):
    """Complete permitted support; proximity is a cost, never an access cutoff."""
    origins = [p for p in points if p['commuters'] > 0]
    destinations = [p for p in points if p['attraction'] > 0]
    if not origins:
        return origins, destinations, np.array([], int), np.array([], int), np.array([], float)
    if not destinations:
        raise ValueError('No permitted destinations for retained commuters')
    lat = np.mean([p['location'][1] for p in points])
    scale = np.array([111.32*math.cos(math.radians(lat)), 110.574])
    coordinates = np.asarray([p['location'] for p in destinations])
    zones = np.asarray([p['zone'] for p in destinations])
    components = np.asarray([p.get('road_component') for p in destinations], dtype=object)
    indices = {p['id']:j for j,p in enumerate(destinations)}
    blocked = {}
    for a,b in config.get('_forbidden_pairs', set()):
        if b in indices: blocked.setdefault(a, []).append(indices[b])
    special = np.asarray([bool(p.get('is_special')) for p in destinations])
    rows, cols, distances = [], [], []
    for i,origin in enumerate(origins):
        distance = np.linalg.norm((coordinates-origin['location'])*scale, axis=1)
        permitted = (zones == origin['zone']) & np.any(coordinates != origin['location'], axis=1)
        if origin.get('road_component') is not None:
            permitted &= components == origin['road_component']
        if config.get('max_distance_km') is not None:
            permitted &= distance <= config['max_distance_km']
        if origin['id'] in indices: permitted[indices[origin['id']]] = False
        permitted[blocked.get(origin['id'], [])] = False
        if config.get('_reservation_only'): permitted &= special
        selected = np.flatnonzero(permitted)
        if not len(selected) and not config.get('_reservation_only'):
            raise ValueError(f"Origin without support: {origin['id']}; commuters={origin['commuters']}; zone={origin['zone']}; max_distance={config.get('max_distance_km')}")
        rows.append(np.full(len(selected),i,dtype=np.int32))
        cols.append(selected.astype(np.int32))
        distances.append(distance[selected])
    return origins,destinations,np.concatenate(rows),np.concatenate(cols),np.concatenate(distances)


def row_normalize(log_weights, rows, budgets):
    maximum = np.full(len(budgets), -np.inf)
    np.maximum.at(maximum, rows, log_weights)
    weights = np.exp(log_weights-maximum[rows])
    sums = np.bincount(rows, weights=weights, minlength=len(budgets))
    flow = weights/sums[rows]*budgets[rows]
    log_sums = maximum+np.log(sums)
    return flow, log_sums


def warm_dual(log_prior, rows, cols, budgets, targets, edge_groups,
              group_targets, reliability, parameters, iterations=30):
    """Bounded iterative-scaling steps for the same convex dual objective.

    log(x) <= x-1 gives a separable majorizer. Its column minimum is
    log(current/target); its group minimum is r/(1+r)*log(current/dual).
    Clipping each step to the existing bounds still decreases that majorizer.
    This initializes the optimizer without changing constraints or tolerances.
    """
    nd=len(targets)
    v=parameters[:nd].copy();w=parameters[nd:].copy()
    for _ in range(iterations):
        flow,_=row_normalize(log_prior-v[cols]-w[edge_groups],rows,budgets)
        current=np.bincount(cols,weights=flow,minlength=nd)
        active=(targets>0)&(current>0)
        v[active]=np.clip(v[active]+np.log(current[active]/targets[active]),-15.,15.)
        flow,_=row_normalize(log_prior-v[cols]-w[edge_groups],rows,budgets)
        current=np.bincount(edge_groups,weights=flow,minlength=len(w))
        active=(group_targets>0)&(current>0)
        delta=reliability[active]/(1+reliability[active])*(
            np.log(current[active]/group_targets[active])-w[active]/reliability[active])
        w[active]=np.clip(w[active]+delta,-15.*reliability[active],15.*reliability[active])
    return np.r_[v,w]


def dual_scale(log_prior,rows,cols,budgets,nd,edge_groups,group_targets,reliability,parameters):
    """Square root of the actual dual Hessian diagonal, including row coupling."""
    from scipy.sparse import coo_matrix
    v,w=parameters[:nd],parameters[nd:]
    flow,_=row_normalize(log_prior-v[cols]-w[edge_groups],rows,budgets)
    column_curvature=np.bincount(cols,weights=flow*(1-flow/budgets[rows]),minlength=nd)
    # Several destinations in one municipal group share the same row-normalizer.
    grouped=coo_matrix((flow,(rows,edge_groups)),shape=(len(budgets),len(w))).tocsr()
    group_rows=np.repeat(np.arange(len(budgets)),np.diff(grouped.indptr))
    group_curvature=np.bincount(grouped.indices,
        weights=grouped.data*(1-grouped.data/budgets[group_rows]),minlength=len(w))
    group_curvature+=group_targets*np.exp(w/reliability)/reliability
    return np.sqrt(np.maximum(np.r_[column_curvature,group_curvature]/max(1.,budgets.sum()),1e-12))


def municipal_groups(rows, cols, origins, destinations):
    source = sorted({p['municipality'] for p in origins})
    target = sorted({p['municipality'] for p in destinations})
    si, ti = {m:i for i,m in enumerate(source)}, {m:i for i,m in enumerate(target)}
    oi = np.asarray([si[p['municipality']] for p in origins])
    dj = np.asarray([ti[p['municipality']] for p in destinations])
    values, groups = np.unique(oi[rows]*len(target)+dj[cols], return_inverse=True)
    return [(source[v//len(target)],target[v%len(target)]) for v in values], groups


def municipal_score(flow, rows, cols, origins, destinations, mobility, field):
    keys, groups = municipal_groups(rows, cols, origins, destinations)
    modeled = dict(zip(keys, np.bincount(groups, weights=flow, minlength=len(keys))))
    represented_origins = {p['municipality'] for p in origins}
    represented_destinations = {p['municipality'] for p in destinations}
    observations = [r for r in mobility.get('flows', []) if r.get(field, 0) > 0 and
                    r['origin'] in represented_origins and r['destination'] in represented_destinations]
    observed_totals, model_totals = {}, {}
    permitted = represented_destinations
    for r in observations:
        observed_totals[r['origin']] = observed_totals.get(r['origin'], 0.)+r[field]
    for (o, d), x in modeled.items():
        if d in permitted:
            model_totals[o] = model_totals.get(o, 0.)+x
    error, mass = 0., 0.
    for r in observations:
        o, d = r['origin'], r['destination']
        a = r[field]/observed_totals[o]
        b = modeled.get((o, d), 0.)/max(model_totals.get(o, 0.), 1e-12)
        error += r[field]*math.log(max(a, 1e-12)/max(b, 1e-12))
        mass += r[field]
    return dict(conditional_kl=error/mass if mass else None, evaluated_weight=mass,
                note='Whole-municipal survey observations versus retained-area model; conditional on represented destinations')


def _allocate_regular(points, config, mobility):
    from .cohorts import settings
    cohort_options = config.get('cohort_settings') or settings({'demand':config})
    origins, destinations, rows, cols, distance = support(points, config)
    budgets = np.asarray([p['commuters'] for p in origins], float)
    if not origins:
        if cohort_options['cohort_count'] is not None:
            raise ValueError('Requested cohorts infeasible: no retained travelers; feasible count is 0')
        return [], dict(commuters=0, beta=config.get('beta') or .12, support_edges=0,
            cohorts=dict(options=cohort_options,requested_count=None,actual_count=0,minimum_feasible=0,maximum_feasible=0))
    attraction = np.asarray([p['attraction'] for p in destinations], float)
    beta = config.get('beta') or .12
    calibrated = False
    if config.get('beta') is None and sum(r.get('training_records', r.get('records', 0)) for r in mobility.get('flows', [])) >= 30:
        candidates = []
        for value in (.03, .06, .09, .12, .18, .24, .36):
            proposal, _ = row_normalize(np.log(attraction[cols])-value*distance, rows, budgets)
            score = municipal_score(proposal, rows, cols, origins, destinations, mobility, 'training_weight')
            if score['conditional_kl'] is not None:
                candidates.append((score['conditional_kl'], abs(value-.12), value))
        if candidates:
            beta = min(candidates)[2]
            calibrated = True
    log_prior = np.log(attraction[cols])-beta*distance
    from .workplace_profile import targets_for_support, check_feasibility, project_margins
    targets, profile = targets_for_support(origins,destinations,rows,cols)
    check_feasibility(budgets,targets,rows,cols)
    # Soft municipal observations apply only to compatible, represented pairs.
    keys, edge_groups = municipal_groups(rows,cols,origins,destinations)
    group_targets = np.zeros(len(keys))
    reliability = np.ones(len(keys))
    observed = {(r['origin'], r['destination']):r for r in mobility.get('flows', [])}
    origin_masses = {}
    for o in origins:
        origin_masses[o['municipality']] = origin_masses.get(o['municipality'], 0.)+o['commuters']
    initial, _ = row_normalize(log_prior, rows, budgets)
    prior_groups = np.bincount(edge_groups, weights=initial, minlength=len(keys))
    for origin in sorted(origin_masses):
        indices = [i for i, (o, _) in enumerate(keys) if o == origin]
        inside = sum(observed.get(keys[i], {}).get('training_weight', 0.) for i in indices)
        total = sum(r.get('training_weight', 0.) for (o,d),r in observed.items() if o == origin)
        for i in indices:
            r = observed.get(keys[i], {})
            # Exterior and unspecified shares follow the within-map gravity prior.
            measured = r.get('training_weight', 0.)/total if total else 0.
            redistributed = (1-inside/total) if total else 1.
            group_targets[i] = measured*origin_masses[origin]+redistributed*prior_groups[i]
            reliability[i] = min(10., max(.1, r.get('effective_sample_size', 0.)/100.)) if total else .1
    # Small positive priors preserve support for survey zero cells, not invented observed flows.
    group_targets = np.maximum(group_targets, prior_groups*1e-6)
    for origin in origin_masses:
        indices = [i for i, (o,d) in enumerate(keys) if o == origin]
        group_targets[indices] *= origin_masses[origin]/group_targets[indices].sum()
    nd = len(destinations)
    scale = max(1., budgets.sum())
    def objective(parameters):
        v, w = parameters[:nd], parameters[nd:]
        flow, log_sums = row_normalize(log_prior-v[cols]-w[edge_groups], rows, budgets)
        dest_term = np.dot(targets,v)
        group_exp = group_targets*np.exp(w/reliability)
        loss = (np.dot(budgets, log_sums)+dest_term+np.dot(reliability, group_exp))/scale
        gradient = np.r_[targets-np.bincount(cols, weights=flow, minlength=nd),
                         group_exp-np.bincount(edge_groups, weights=flow, minlength=len(keys))]/scale
        return loss, gradient
    # Warm-start column potentials; positive targets can differ greatly in scale.
    initial_columns = np.bincount(cols, weights=initial, minlength=nd)
    warm = np.r_[.5*np.log(np.maximum(initial_columns, 1e-12)/np.maximum(targets, 1e-12)),
                 reliability/(1+reliability)*np.log(np.maximum(prior_groups, 1e-12)/np.maximum(group_targets, 1e-12))]
    warm[:nd] = np.clip(warm[:nd], -15., 15.)
    warm[nd:] = np.clip(warm[nd:], -15.*reliability, 15.*reliability)
    warm_loss_before=objective(warm)[0]
    warm = warm_dual(log_prior,rows,cols,budgets,targets,edge_groups,
                     group_targets,reliability,warm)
    warm_report=dict(method='Bounded iterative scaling of the same dual',iterations=30,
                     loss_before=float(warm_loss_before),loss_after=float(objective(warm)[0]))
    if warm_report['loss_after']>warm_loss_before+1e-9:
        raise ValueError('Dual initialization increased the original objective')
    print('   • Asignación v2: inicialización de márgenes completada',flush=True)
    # Diagonal Hessian scaling removes the orders-of-magnitude spread between
    # tiny destinations and regional centers. This does not change the objective.
    variable_scale = dual_scale(log_prior,rows,cols,budgets,nd,edge_groups,
                               group_targets,reliability,warm)
    warm_report['preconditioner']='Actual dual Hessian diagonal at initialization'
    bounds = [(-15.*s,15.*s) for s in variable_scale[:nd]] + [(-15.*r*s,15.*r*s) for r,s in zip(reliability, variable_scale[nd:])]
    def scaled_objective(parameters):
        loss, gradient = objective(parameters/variable_scale)
        return loss, gradient/variable_scale
    iteration=[0]
    def progress(_):
        iteration[0]+=1
        if iteration[0]%25==0:
            print(f'   • Asignación v2: iteración {iteration[0]} / 500',flush=True)
    solution = minimize(scaled_objective, warm*variable_scale, jac=True, method='L-BFGS-B',
                        bounds=bounds,
                        callback=progress,
                        options=dict(maxiter=500, maxcor=30, ftol=1e-10, gtol=1e-6))
    if not solution.success:
        raise ValueError('Entropy assignment did not converge: '+str(solution.message)+
                         '; scaled max gradient='+str(float(np.max(np.abs(solution.jac)))))
    parameters = solution.x/variable_scale
    v, w = parameters[:nd], parameters[nd:]
    flow, _ = row_normalize(log_prior-v[cols]-w[edge_groups], rows, budgets)
    flow, profile['balance'] = project_margins(flow,rows,cols,budgets,targets)
    profile['targets'] = {p['id']:float(t) for p,t in zip(destinations,targets)}
    if config.get('_continuous_only'):
        return (flow,rows,cols,distance,origins,destinations),dict(
            beta=float(beta),beta_calibrated=calibrated,solver=solution.message,
            warm_start=warm_report,
            support_edges=len(rows),solver_iterations=int(solution.nit),
            row_max_residual=float(np.max(np.abs(np.bincount(rows,weights=flow)-budgets))),
            destination_profile=profile,
            destination_targets_semantics='Fixed model weights normalized to retained ordinary budgets; not measured jobs; POIs reserved separately')
    from .cohorts import integerize
    cells, integer_flow, cohort_report = integerize(flow,rows,cols,origins,destinations,distance,
        cohort_options)
    outside = {}
    represented = {p['municipality'] for p in points}
    for o in origins:
        if o['municipality'] not in outside:
            obs = [r for r in mobility.get('flows', []) if r['origin'] == o['municipality']]
            total = sum(r['weight'] for r in obs)
            fraction = sum(r['weight'] for r in obs if r['destination'] not in represented)/total if total else None
            origin_mass = sum(x['commuters'] for x in origins if x['municipality'] == o['municipality'])
            outside[o['municipality']] = dict(fraction=fraction, retained_estimate=origin_mass*fraction if fraction is not None else None,
                                            basis='Municipal fraction applied to retained residents; not exact BBOX flows')
    report = dict(commuters=int(budgets.sum()), beta=float(beta), beta_calibrated=calibrated,
        warm_start=warm_report,
        beta_basis='municipal training flows' if calibrated else 'explicit parameter' if config.get('beta') is not None else 'declared fallback',
        support_edges=len(rows), solver=solution.message, objective=float(solution.fun),
        solver_iterations=int(solution.nit), scaled_max_gradient=float(np.max(np.abs(solution.jac))),
        normalized_max_gradient=float(np.max(np.abs(objective(parameters)[1]))),
        row_max_residual=float(np.max(np.abs(np.bincount(rows, weights=flow)-budgets))),
        destination_profile=profile,
        destination_targets_semantics='Fixed normalized model weights; arrivals are not observed employment',
        redistributed_external=outside,
        cohorts=cohort_report,
        rounding_l1_persons=float(np.abs(integer_flow-flow).sum()),
        integer_row_max_residual=float(np.max(np.abs(np.bincount(rows,weights=integer_flow)-budgets))),
        validation_integer=municipal_score(integer_flow, rows, cols, origins, destinations, mobility, 'validation_weight'),
        validation=municipal_score(flow, rows, cols, origins, destinations, mobility, 'validation_weight'))
    return cells, report


def allocate(points, config, mobility):
    if not any(p.get('is_special') and p['attraction']>0 for p in points):
        return _allocate_regular(points,config,mobility)
    from .poi_assignment import allocate_with_pois
    return allocate_with_pois(points,config,mobility,_allocate_regular)
