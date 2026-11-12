"""Bounded entropy allocation for large, ordinary exact-cohort builds."""
from collections import Counter, defaultdict
import math
import shutil
import tempfile

import numpy as np
from scipy.optimize import minimize

from .range_transport import RangeGraph
from .workplace_profile import quantized_margins, require_feasible, targets_for_components


MAX_DENSE_PAIRS = 5_000_000
BLOCK_PAIRS = 500_000


def use_blocked_allocation(points):
    origins = sum(p['commuters'] > 0 for p in points)
    destinations = sum(p['attraction'] > 0 for p in points)
    return origins * destinations > MAX_DENSE_PAIRS


class BlockProblem:
    """Keep the float64 distance prior on disk and reduce one row block at a time."""

    def __init__(self, points, config, prior):
        from .allocation import permitted_destinations

        self.origins = [p for p in points if p['commuters'] > 0]
        self.destinations = sorted((p for p in points if p['attraction'] > 0),
                                   key=lambda p: (p['zone'], repr(p.get('road_component')), p['municipality']))
        self.nr, self.nd = len(self.origins), len(self.destinations)
        self.budgets = np.asarray([p['commuters'] for p in self.origins], dtype=float)
        self.source_names, self.source_mun = np.unique([p['municipality'] for p in self.origins],
                                                       return_inverse=True)
        self.job_names, self.job_mun = np.unique([p['municipality'] for p in self.destinations],
                                                 return_inverse=True)
        self.group_shape = len(self.source_names), len(self.job_names)
        self.run_starts = np.r_[0, np.flatnonzero(np.diff(self.job_mun))+1]
        self.run_municipalities = self.job_mun[self.run_starts]
        self.rows_per_block = max(1, BLOCK_PAIRS // self.nd)
        self.prior = prior
        self.edges = 0
        self.kernel_blocks = []
        self.prior_maxima = np.empty(self.nr)
        active = np.zeros(self.group_shape, dtype=bool)
        topology = RangeGraph(self.nr, self.nd)
        latitude = float(np.mean([p['location'][1] for p in points]))
        self.scale = np.asarray([111.32*math.cos(math.radians(latitude)), 110.574])
        self.coordinates = np.asarray([p['location'] for p in self.destinations])
        zones = np.asarray([p['zone'] for p in self.destinations])
        components = np.asarray([p.get('road_component') for p in self.destinations], dtype=object)
        indices = {p['id']: j for j, p in enumerate(self.destinations)}
        blocked = defaultdict(list)
        for origin, destination in config.get('_forbidden_pairs', ()):
            if destination in indices:
                blocked[origin].append(indices[destination])
        special = np.zeros(self.nd, dtype=bool)
        log_attraction = np.log([p['attraction'] for p in self.destinations])
        beta = config.get('beta') or .12
        for start in range(0, self.nr, self.rows_per_block):
            stop = min(start+self.rows_per_block, self.nr)
            block = np.full((stop-start, self.nd), -np.inf)
            for i in range(start, stop):
                origin = self.origins[i]
                columns, distance = permitted_destinations(origin, config, self.coordinates, zones,
                                                            components, indices, blocked, special, self.scale)
                if not len(columns):
                    raise ValueError(f"Origin without support: {origin['id']}; commuters={origin['commuters']}; "
                                     f"zone={origin['zone']}; max_distance={config.get('max_distance_km')}")
                topology.add_row(i, columns)
                self.edges += len(columns)
                active[self.source_mun[i], self.job_mun[columns]] = True
                block[i-start, columns] = log_attraction[columns]-beta*distance
            maxima = block.max(axis=1)
            self.prior_maxima[start:stop] = maxima
            # Use separable exponential factors when the full range of weights
            # stays away from underflow. Keep log weights for extreme cases.
            finite = np.isfinite(block)
            kernel = bool(np.all((block-maxima[:, None] >= -350.) | ~finite))
            self.kernel_blocks.append(kernel)
            if kernel:
                block -= maxima[:, None]
                np.exp(block, out=block)
            block.tofile(prior)
        prior.flush()
        self.active = active
        labels = topology.components()
        self.targets, self.profile = targets_for_components(self.origins, self.destinations, *labels)
        supplies, demands, scale = quantized_margins(self.budgets, self.targets, *labels)
        balances = np.zeros(topology.size, dtype=np.int64)
        balances[:self.nr] = supplies
        balances[topology.first_job:] = -demands
        deficit, _ = topology.solve(balances)
        require_feasible(deficit, scale)
        self.profile['range_graph_arcs'] = len(topology.rows)
        print(f'   • Asignación v2 por bloques: {self.nr:,} orígenes, {self.nd:,} destinos, '
              f'{self.edges:,} pares permitidos', flush=True)

    def flow_blocks(self, v, w, *, include_support=False):
        group = np.zeros(self.group_shape)
        group[self.active] = w
        v_min, w_min = v.min(), group.min()
        fast = v.max()-v_min+group.max()-w_min <= 350.
        if fast:
            column_factors = np.exp(v_min-v)
            group_factors = np.exp(w_min-group)
        self.prior.seek(0)
        for block_index, start in enumerate(range(0, self.nr, self.rows_per_block)):
            stop = min(start+self.rows_per_block, self.nr)
            shape = stop-start, self.nd
            weights = np.fromfile(self.prior, dtype=np.float64, count=shape[0]*shape[1]).reshape(shape)
            kernel = self.kernel_blocks[block_index]
            permitted = (weights > 0 if kernel else np.isfinite(weights)) if include_support else None
            if kernel and fast:
                weights *= column_factors
                weights *= group_factors[self.source_mun[start:stop]][:, self.job_mun]
                maxima = self.prior_maxima[start:stop]-v_min-w_min
            else:
                if kernel:
                    with np.errstate(divide='ignore'):
                        np.log(weights, out=weights)
                    weights += self.prior_maxima[start:stop, None]
                weights -= v
                weights -= group[self.source_mun[start:stop]][:, self.job_mun]
                maxima = weights.max(axis=1)
                weights -= maxima[:, None]
                np.exp(weights, out=weights)
            sums = weights.sum(axis=1)
            weights *= (self.budgets[start:stop]/sums)[:, None]
            values = start, stop, weights, maxima+np.log(sums)
            yield (*values, permitted) if include_support else values

    def municipal_rows(self, flow):
        runs = np.add.reduceat(flow, self.run_starts, axis=1)
        sums = np.zeros((len(flow), len(self.job_names)))
        for run, municipality in enumerate(self.run_municipalities):
            sums[:, municipality] += runs[:, run]
        return sums

    def statistics(self, v, w, *, curvature=False, details=False, columns_only=False):
        columns = np.zeros(self.nd)
        groups = np.zeros(self.group_shape)
        column_curve = np.zeros(self.nd)
        group_curve = np.zeros(self.group_shape)
        row_masses = np.zeros((self.nr, len(self.job_names))) if details else None
        loss = 0.
        row_error = 0.
        for start, stop, flow, logs in self.flow_blocks(v, w):
            budgets = self.budgets[start:stop]
            columns += flow.sum(axis=0)
            if columns_only:
                continue
            loss += np.dot(budgets, logs)
            row_groups = self.municipal_rows(flow)
            np.add.at(groups, self.source_mun[start:stop], row_groups)
            if curvature:
                column_curve += (flow*(1-flow/budgets[:, None])).sum(axis=0)
                np.add.at(group_curve, self.source_mun[start:stop],
                          row_groups*(1-row_groups/budgets[:, None]))
            if details:
                row_masses[start:stop] = row_groups
                row_error = max(row_error, float(np.abs(flow.sum(axis=1)-budgets).max()))
        return columns, groups[self.active], loss, column_curve, group_curve[self.active], row_masses, row_error

    def group_targets(self, prior_groups, mobility):
        keys = [(str(self.source_names[i]), str(self.job_names[j])) for i, j in zip(*np.nonzero(self.active))]
        group_targets = np.empty(len(keys))
        reliability = np.empty(len(keys))
        observed = {(r['origin'], r['destination']): r for r in mobility.get('flows', [])}
        training_totals = Counter()
        for (origin, _), row in observed.items():
            training_totals[origin] += row.get('training_weight', 0.)
        origin_masses = np.bincount(self.source_mun, weights=self.budgets, minlength=len(self.source_names))
        start = 0
        for i, name in enumerate(self.source_names):
            stop = start+int(self.active[i].sum())
            total = training_totals[name]
            records = [observed.get(key, {}) for key in keys[start:stop]]
            training = np.array([r.get('training_weight', 0.) for r in records])
            prior = prior_groups[start:stop]
            estimated = (training/total*origin_masses[i]+(1-training.sum()/total)*prior) if total else prior
            estimated = np.maximum(estimated, prior*1e-6)
            group_targets[start:stop] = estimated*(origin_masses[i]/estimated.sum())
            reliability[start:stop] = ([min(10., max(.1, r.get('effective_sample_size', 0.)/100.))
                                       for r in records] if total else .1)
            start = stop
        return keys, group_targets, reliability


def fit(problem, mobility):
    """Use the same dual, bounds and final marginal projection as the dense path."""
    nd = problem.nd
    budgets, targets = problem.budgets, problem.targets
    v, w = np.zeros(nd), np.zeros(problem.active.sum())
    initial_columns, prior_groups, *_ = problem.statistics(v, w)
    keys, group_targets, reliability = problem.group_targets(prior_groups, mobility)
    mass = max(1., budgets.sum())
    v = np.clip(.5*np.log(np.maximum(initial_columns, 1e-12)/np.maximum(targets, 1e-12)), -15., 15.)
    w = np.clip(reliability/(1+reliability)*np.log(np.maximum(prior_groups, 1e-12)/
                np.maximum(group_targets, 1e-12)), -15.*reliability, 15.*reliability)

    def objective(parameters):
        v, w = parameters[:nd], parameters[nd:]
        columns, groups, loss, *_ = problem.statistics(v, w)
        group_exp = group_targets*np.exp(w/reliability)
        loss = (loss+np.dot(targets, v)+np.dot(reliability, group_exp))/mass
        return loss, np.r_[targets-columns, group_exp-groups]/mass

    loss_before = objective(np.r_[v, w])[0]
    for iteration in range(30):
        columns = problem.statistics(v, w, columns_only=True)[0]
        active = (targets > 0) & (columns > 0)
        v[active] = np.clip(v[active]+np.log(columns[active]/targets[active]), -15., 15.)
        groups = problem.statistics(v, w)[1]
        active = (group_targets > 0) & (groups > 0)
        r = reliability[active]
        delta = r/(1+r)*(np.log(groups[active]/group_targets[active])-w[active]/r)
        w[active] = np.clip(w[active]+delta, -15.*r, 15.*r)
        if (iteration+1) % 5 == 0:
            print(f'   • Asignación v2 por bloques: inicialización {iteration+1}/30', flush=True)
    warm = np.r_[v, w]
    loss_after = objective(warm)[0]
    if loss_after > loss_before+1e-9:
        raise ValueError('Dual initialization increased the original objective')
    _, _, _, column_curve, group_curve, *_ = problem.statistics(v, w, curvature=True)
    group_curve += group_targets*np.exp(w/reliability)/reliability
    variable_scale = np.sqrt(np.maximum(np.r_[column_curve, group_curve]/mass, 1e-12))
    limits = np.r_[np.full(nd, 15.), 15.*reliability]*variable_scale

    def scaled_objective(parameters):
        loss, gradient = objective(parameters/variable_scale)
        return loss, gradient/variable_scale

    iteration = 0

    def progress(_):
        nonlocal iteration
        iteration += 1
        if iteration % 10 == 0:
            print(f'   • Asignación v2 por bloques: iteración {iteration}/500', flush=True)

    solution = minimize(scaled_objective, warm*variable_scale, jac=True, method='L-BFGS-B',
                        bounds=list(zip(-limits, limits)), callback=progress,
                        options=dict(maxiter=500, maxcor=30, ftol=1e-10, gtol=1e-6))
    if not solution.success:
        raise ValueError('Entropy assignment did not converge: '+str(solution.message)+
                         '; scaled max gradient='+str(float(np.max(np.abs(solution.jac)))))
    parameters = solution.x/variable_scale
    v, w = parameters[:nd].copy(), parameters[nd:]
    normalized_gradient = float(np.max(np.abs(solution.jac*variable_scale)))
    columns = problem.statistics(v, w, columns_only=True)[0]
    active = targets > 0
    for iteration in range(1, 10001):
        if np.any(columns[active] <= 0):
            raise ValueError('Workplace target lost numerical support')
        v[active] -= np.log(targets[active]/columns[active])
        columns = problem.statistics(v, w, columns_only=True)[0]
        residual = np.abs(columns-targets)
        relative = float(np.max(residual/np.maximum(1., targets)))
        if relative < 1e-8:
            break
    else:
        raise ValueError('Workplace marginal balance did not converge; no export produced')
    problem.profile['balance'] = dict(iterations=iteration, max_destination_error_persons=float(residual.max()),
                                      max_relative_error=relative)
    return v, w, keys, dict(
        solver=str(solution.message), objective=float(solution.fun), solver_iterations=int(solution.nit),
        scaled_max_gradient=float(np.max(np.abs(solution.jac))), normalized_max_gradient=normalized_gradient,
        warm_start=dict(method='Bounded iterative scaling of the same dual', iterations=30,
                        loss_before=float(loss_before), loss_after=float(loss_after),
                        preconditioner='Actual dual Hessian diagonal at initialization'))


def allocate_exact(points, config, mobility, options):
    from .blocked_cohorts import round_exact

    with tempfile.TemporaryFile(prefix='demand-v2-prior-') as prior:
        origins = sum(p['commuters'] > 0 for p in points)
        destinations = sum(p['attraction'] > 0 for p in points)
        required = origins*destinations*8
        free = shutil.disk_usage(tempfile.gettempdir()).free
        if required+512*1024**2 > free:
            raise ValueError(f'Large demand allocation requires {required/1024**3:.2f} GiB of temporary storage; '
                             f'{free/1024**3:.2f} GiB available (0.5 GiB reserve)')
        print(f'   • Asignación v2 por bloques: preparando {required/1024**3:.2f} GiB de datos temporales', flush=True)
        problem = BlockProblem(points, config, prior)
        v, w, keys, solver_report = fit(problem, mobility)
        return round_exact(problem, points, v, w, keys, options, mobility, config, solver_report)
