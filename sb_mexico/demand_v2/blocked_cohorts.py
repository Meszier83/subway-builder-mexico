"""Round exact-size cohorts from bounded continuous-allocation blocks."""
from collections import Counter, defaultdict
import hashlib

import numpy as np

from .municipal_cohorts import _assign_distribution
from .range_transport import RangeGraph


def municipal_parts(problem, row_masses, size, seed):
    regions = defaultdict(list)
    for i, origin in enumerate(problem.origins):
        region = (origin['municipality'], origin['zone'], origin.get('road_component'))
        regions[region].append(i)
    parts = []
    error = 0.
    for region, members in regions.items():
        distribution = {}
        origins, sizes = [], []
        for local, i in enumerate(members):
            origins.append(problem.origins[i])
            sizes.append([size]*(int(problem.budgets[i])//size))
            distribution[local] = [((region, str(problem.job_names[j])), None, float(row_masses[i, j]))
                                   for j in np.flatnonzero(row_masses[i])]
        rounded, report = _assign_distribution(distribution, origins, sizes, seed)
        parts.extend((members[i], key, count) for i, key, _, count in rounded)
        error = max(error, report[0]['max_group_count_error'])
    return sorted(parts, key=lambda part: (part[0], repr(part[1]))), error


def residual_transport(graph, supplies, targets, proposal):
    floors = np.floor(targets).astype(np.int64)
    ceilings = np.ceil(targets).astype(np.int64)
    seed_rows = np.zeros(len(supplies), dtype=np.int64)
    seed_columns = np.zeros(len(targets), dtype=np.int64)
    extra = []
    for (i, j), count in proposal.items():
        seed_rows[i] += count
        seed_columns[j] += count
        extra.append((graph.first_job+j, i, count))
    extra.extend((graph.first_job+int(j), graph.size, 1) for j in np.flatnonzero(ceilings > floors))
    balances = np.zeros(graph.size+1, dtype=np.int64)
    balances[:len(supplies)] = supplies-seed_rows
    balances[graph.first_job:graph.size] = seed_columns-floors
    balances[-1] = int(floors.sum()-supplies.sum())
    deficit, flow = graph.solve(balances, extra)
    if deficit:
        return None, deficit
    assigned = graph.realize(flow, proposal)
    rows = np.zeros(len(supplies), dtype=np.int64)
    columns = np.zeros(len(targets), dtype=np.int64)
    for (i, j), count in assigned.items():
        rows[i] += count
        columns[j] += count
    if not np.array_equal(rows, supplies) or np.any((columns < floors) | (columns > ceilings)):
        raise ValueError('Bounded cohort transport violated integer margin checks')
    return (assigned, columns), 0


def transport_parts(problem, v, w, parts, size, seed):
    """Build only proposal/nearest arcs and a compressed complete fallback."""
    by_origin = defaultdict(list)
    municipalities = {str(name): j for j, name in enumerate(problem.job_names)}
    for part, (i, key, count) in enumerate(parts):
        by_origin[i].append((part, key, count, municipalities[key[1]]))
    jobs_by_municipality = [np.flatnonzero(problem.job_mun == j) for j in range(len(municipalities))]
    supplies = np.array([count for _, _, count in parts], dtype=np.int64)
    full = RangeGraph(len(parts), problem.nd)
    targets = np.zeros(problem.nd)
    proposal = Counter()
    width = 8
    nearest = [set() for _ in parts]
    reverse_distance = np.full((problem.nd, width), np.inf, dtype=np.float32)
    reverse_parts = np.full((problem.nd, width), -1, dtype=np.int32)
    worst_slot = np.zeros(problem.nd, dtype=np.int32)
    for start, stop, flow, _, permitted in problem.flow_blocks(v, w, include_support=True):
        for i in range(start, stop):
            for part, key, count, municipality in by_origin.get(i, ()):
                jobs = jobs_by_municipality[municipality]
                jobs = jobs[permitted[i-start, jobs]]
                weights = flow[i-start, jobs]
                mass = weights.sum()
                if mass <= 0:
                    raise ValueError('Municipal part has no positive workplace distribution')
                full.add_row(part, jobs)
                targets[jobs] += count*weights/mass
                cumulative = np.cumsum(weights)
                phase = int(hashlib.sha256(repr((problem.origins[i]['id'], key, size, seed)).encode())
                            .hexdigest()[:13], 16)/16**13
                positions = (np.arange(count)+phase)*cumulative[-1]/count
                for job in jobs[np.searchsorted(cumulative, positions, side='right')]:
                    proposal[part, int(job)] += 1
                    nearest[part].add(int(job))
                delta = (problem.coordinates[jobs]-problem.origins[i]['location'])*problem.scale
                distance = np.linalg.norm(delta, axis=1).astype(np.float32)
                nearest[part].update(map(int, jobs[np.argsort(distance, kind='stable')[:width]]))
                slots = worst_slot[jobs]
                improve = distance < reverse_distance[jobs, slots]
                changed, slots = jobs[improve], slots[improve]
                reverse_distance[changed, slots] = distance[improve]
                reverse_parts[changed, slots] = part
                worst_slot[changed] = np.argmax(reverse_distance[changed], axis=1)
    for j, candidates in enumerate(reverse_parts):
        for part in candidates:
            if part >= 0:
                nearest[part].add(j)
    near = RangeGraph(len(parts), problem.nd)
    for i, jobs in enumerate(nearest):
        near.add_row(i, np.array(sorted(jobs), dtype=np.int32))
    targets *= int(supplies.sum())/targets.sum()
    attempts = []
    for candidate, width in ((near, 8), (full, None)):
        result, deficit = residual_transport(candidate, supplies, targets, proposal)
        attempts.append(dict(width=width, range_graph_arcs=len(candidate.rows), feasible=not deficit))
        if result is not None:
            return *result, targets, attempts
    raise ValueError(f'Complete support cannot realize cohort floor/ceil targets: deficit={deficit}')


def round_exact(problem, points, v, w, keys, options, mobility, config, solver_report):
    from .allocation import score_municipal_totals

    size = int(options['fixed_size'])
    if np.any(problem.budgets % size):
        raise ValueError('Exact cohort mode requires origin budgets quantized to fixed_size')
    columns, groups, _, _, _, row_masses, row_error = problem.statistics(v, w, details=True)
    parts, municipal_error = municipal_parts(problem, row_masses, size, options['seed'])
    del row_masses
    print(f'   • Asignación v2 por bloques: redondeando {len(parts):,} partes municipales', flush=True)
    assigned, arrivals, targets, attempts = transport_parts(problem, v, w, parts, size, options['seed'])
    realized = Counter()
    for (part, j), count in assigned.items():
        realized[parts[part][0], j] += count
    origin_counts = Counter()
    municipal_integer = Counter()
    by_origin = defaultdict(list)
    cells = []
    for (i, j), count in sorted(realized.items()):
        origin, job = problem.origins[i], problem.destinations[j]
        mass = count*size
        delta = (problem.coordinates[j]-origin['location'])*problem.scale
        distance = float(np.linalg.norm(delta))
        cells.append(dict(origin=origin['id'], destination=job['id'], size=mass, distance_km=distance,
                          cohort_sizes=[size]*count))
        origin_counts[origin['id']] += count
        municipal_integer[origin['municipality'], job['municipality']] += mass
        by_origin[i].append((j, mass))
    overlap = 0.
    for start, stop, flow, _ in problem.flow_blocks(v, w):
        for i in range(start, stop):
            overlap += sum(min(mass, flow[i-start, j]) for j, mass in by_origin.get(i, ()))
    if any(origin_counts[p['id']]*size != p['commuters'] for p in problem.origins):
        raise ValueError('Municipal cohort rounding changed original budgets')
    total = int(problem.budgets.sum())
    cohorts = total//size
    problem.profile['targets'] = {p['id']: float(t) for p, t in zip(problem.destinations, problem.targets)}
    represented = {p['municipality'] for p in points}
    outside = {}
    origin_masses = Counter()
    for origin in problem.origins:
        origin_masses[origin['municipality']] += origin['commuters']
    observed = defaultdict(list)
    for row in mobility.get('flows', []):
        observed[row['origin']].append(row)
    for origin, mass in origin_masses.items():
        rows = observed[origin]
        weight = sum(r['weight'] for r in rows)
        fraction = sum(r['weight'] for r in rows if r['destination'] not in represented)/weight if weight else None
        outside[origin] = dict(fraction=fraction, retained_estimate=mass*fraction if fraction is not None else None,
                               basis='Municipal fraction applied to retained residents; not exact BBOX flows')
    score_args = (problem.origins, problem.destinations, mobility, 'validation_weight')
    destination_error = float(np.abs(arrivals-targets).max())
    return cells, dict(
        solver_report, commuters=total, beta=float(config.get('beta') or .12), beta_calibrated=False,
        support_edges=problem.edges, allocation_storage='float64_prior_row_blocks',
        block_max_pairs=problem.rows_per_block*problem.nd, row_max_residual=row_error,
        destination_profile=problem.profile,
        destination_targets_semantics='Fixed normalized model weights; arrivals are not observed employment',
        redistributed_external=outside, integer_row_max_residual=0.,
        rounding_l1_persons=float(max(0., 2*(total-overlap))),
        validation=score_municipal_totals(dict(zip(keys, groups)), *score_args),
        validation_integer=score_municipal_totals(municipal_integer, *score_args),
        cohorts=dict(options=options, requested_count=None, actual_count=cohorts, minimum_feasible=cohorts,
                     maximum_feasible=cohorts, origins=dict(origin_counts),
                     method='Exact whole-cohort municipal LP and joint destination transport; zero sub-cohort remainders',
                     municipal_rounding_unit='cohort',
                     municipal_rounding=[dict(size=size, cohorts=cohorts, max_group_count_error=municipal_error)],
                     destination_rounding=[dict(size=size, cohorts=cohorts,
                         max_destination_count_error=destination_error,
                         feedback_projection=dict(iterations=0, max_goal_error_counts=0.),
                         cumulative_max_destination_error_people=destination_error*size, support_attempts=attempts)],
                     max_destination_rounding_error_people=float(np.abs(size*arrivals-columns).max()),
                     below_preferred_minimum=0))
