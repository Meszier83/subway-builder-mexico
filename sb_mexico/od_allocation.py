"""Integer model margins on final geography; no inter-pair cohort reassignment.

The sampled gravity proposal is a preference, never the authoritative margin.
Destination targets remain estimates. This module does not calibrate source data.
"""
from collections import Counter
import math
import logging

import networkx as nx
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import maximum_flow

logger = logging.getLogger(__name__)


class ODAllocationError(ValueError):
    """No integer transport fits the frozen targets and permitted support."""


def _sparse_residual_flow(graph):
    """Integer feasibility on sparse residual arcs using compiled Dinic.

    Keep the proposal as existing flow; augment only its deficits. Candidate
    forward support starts with proposal pairs and nearest permitted neighbors.
    This is a reproducible sparse repair, not a claim of globally optimal costs.
    """
    nodes = list(graph)
    indices = {node: i for i, node in enumerate(nodes)}
    source, sink = len(nodes), len(nodes) + 1
    rows, cols, values, required = [], [], [], 0
    for origin, destination, attributes in graph.edges(data=True):
        rows.append(indices[origin])
        cols.append(indices[destination])
        values.append(attributes['capacity'])
    for node in nodes:
        demand = graph.nodes[node]['demand']
        if demand < 0:
            rows.append(source)
            cols.append(indices[node])
            values.append(-demand)
            required -= demand
        elif demand > 0:
            rows.append(indices[node])
            cols.append(sink)
            values.append(demand)
    # The current commuter budget fits signed 32-bit capacities used by SciPy.
    if max(values, default=0) >= np.iinfo(np.int32).max:
        raise ODAllocationError('Transport exceeds supported integer capacity range')
    matrix = coo_matrix((np.array(values, dtype=np.int64), (rows, cols)),
                        shape=(len(nodes) + 2, len(nodes) + 2)).tocsr()
    solution = maximum_flow(matrix, source, sink, method='dinic')
    if solution.flow_value != required:
        raise nx.NetworkXUnfeasible('Residual capacity deficit')
    flow = {node: {} for node in nodes}
    realized = solution.flow.tocsr()
    for origin, destination, attributes in graph.edges(data=True):
        value = max(0, int(realized[indices[origin], indices[destination]]))
        if value > attributes['capacity']:
            raise ODAllocationError('Transport violates capacity')
        flow[origin][destination] = value
    return flow


def margins(pops):
    origins, destinations = Counter(), Counter()
    for pop in pops:
        origins[pop['residenceId']] += pop['size']
        destinations[pop['jobId']] += pop['size']
    return origins, destinations


def validate_integer_margins(pops, authority):
    """Compare independent budgets, including zero targets, before display sync."""
    if any(type(pop['size']) is not int or pop['size'] <= 0 for pop in pops):
        raise ODAllocationError('Export contains a nonpositive or noninteger cohort')
    if any(pop['size'] > authority.get('max_pop_size', math.inf) for pop in pops):
        raise ODAllocationError('Export exceeds max_pop_size')
    origins, destinations = margins(pops)
    failures = {}
    for label, actual, expected in (
            ('origins', origins, authority['origin_budgets']),
            ('destinations', destinations, authority['destination_targets'])):
        residuals = {key: actual[key] - expected.get(key, 0)
                     for key in sorted(set(actual) | set(expected))
                     if actual[key] != expected.get(key, 0)}
        if residuals:
            failures[label] = residuals
    if failures:
        raise ODAllocationError(f'Exported integer margins changed: {failures}')
    if any(p['residenceId'] == p['jobId'] for p in pops):
        raise ODAllocationError('Integer allocation contains a self commute')
    if 'pair_targets' in authority:
        actual = Counter()
        for pop in pops:
            actual[pop['residenceId'], pop['jobId']] += pop['size']
        expected = Counter({(o, d): size for o, d, size in authority['pair_targets']})
        if actual != expected:
            raise ODAllocationError('Exported pair masses changed after integer allocation')
    return {'origin_mismatches': 0, 'destination_mismatches': 0,
            'commuters': sum(origins.values())}


def integer_transport(origin_ids, budgets, destination_ids, targets, proposal,
                      allowed, distances, beta=0.12):
    """Joint floor/ceil rounding and sparse residual integer transport.

    Reverse arcs can undo sampled flow, while forward arcs add admissible flow.
    Destination demands encode floors; unit arcs to a sink choose their ceilings.
    The solver cannot change a row budget or exceed either destination bound.
    Support grows only within the precomputed allowed mask. An infeasibility
    claim is made only after checking the complete permitted support.
    """
    budgets = np.asarray(budgets, dtype=np.int64)
    targets = np.asarray(targets, dtype=np.float64)
    if (not np.all(np.isfinite(targets)) or np.any(targets < 0)
            or np.any(budgets < 0)):
        raise ODAllocationError('Nonfinite or negative model margins')
    total = int(budgets.sum())
    if abs(float(targets.sum()) - total) > 1e-6:
        raise ODAllocationError('Fractional destination total differs from origin budget')
    floors = np.floor(targets).astype(np.int64)
    ceilings = np.ceil(targets).astype(np.int64)
    oi = {key: i for i, key in enumerate(origin_ids)}
    dj = {key: j for j, key in enumerate(destination_ids)}
    seed = Counter({(oi[o], dj[d]): int(value) for (o, d), value in proposal.items()
                    if o in oi and d in dj and value > 0 and allowed[oi[o], dj[d]]})
    seed_rows, seed_cols = Counter(), Counter()
    for (i, j), value in seed.items():
        seed_rows[i] += value
        seed_cols[j] += value
    if any(seed_rows[i] > budgets[i] for i in range(len(origin_ids))):
        raise ODAllocationError('Proposal consumes more than an origin budget')
    pairs = set(seed)
    ordered = [np.flatnonzero(row)[np.argsort(distances[i, row], kind='stable')]
               for i, row in enumerate(allowed)]
    ordered_origins = [np.flatnonzero(allowed[:, j])[
        np.argsort(distances[allowed[:, j], j], kind='stable')]
        for j in range(len(destination_ids))]
    attempts = []
    for width in (8, 32, 128, None):
        for i, indices in enumerate(ordered):
            pairs.update((i, int(j)) for j in indices[:width])
        for j, indices in enumerate(ordered_origins):
            pairs.update((int(i), j) for i in indices[:width])
        graph = nx.DiGraph()
        sink = ('sink',)
        graph.add_node(sink, demand=total - int(floors.sum()))
        for i, amount in enumerate(budgets):
            graph.add_node(('o', i), demand=seed_rows[i] - int(amount))
        for j, floor in enumerate(floors):
            node = ('d', j)
            graph.add_node(node, demand=int(floor) - seed_cols[j])
            if ceilings[j] > floor:
                graph.add_edge(node, sink, capacity=1)
        for i, j in sorted(pairs):
            graph.add_edge(('o', i), ('d', j), capacity=int(budgets[i]))
            if seed[i, j]:
                graph.add_edge(('d', j), ('o', i), capacity=seed[i, j])
        logger.info('Integer O/D support: width=%s, pairs=%s', width, len(pairs))
        try:
            flow = _sparse_residual_flow(graph)
        except nx.NetworkXUnfeasible:
            logger.info('Sparse support insufficient; expanding within permitted support')
            attempts.append({'width': width, 'edges': len(pairs), 'feasible': False})
            if width is not None:
                continue
            # A complete-support max-flow cut is an independent infeasibility witness.
            witness = nx.DiGraph()
            required = 0
            for node, attributes in graph.nodes(data=True):
                demand = attributes['demand']
                if demand < 0:
                    witness.add_edge('source', node, capacity=-demand)
                    required -= demand
                elif demand > 0:
                    witness.add_edge(node, 'sink', capacity=demand)
            for origin, destination, attributes in graph.edges(data=True):
                witness.add_edge(origin, destination, capacity=attributes['capacity'])
            capacity, (left, right) = nx.minimum_cut(witness, 'source', 'sink')
            detail = {'origin_ids': [origin_ids[node[1]] for node in left
                                    if isinstance(node, tuple) and node[0] == 'o'],
                      'destination_ids': [destination_ids[node[1]] for node in right
                                          if isinstance(node, tuple) and node[0] == 'd']}
            raise ODAllocationError(
                f'Complete support cannot realize floor/ceil targets: '
                f'zone_total={total}, residual_required={required}, cut_capacity={capacity}, '
                f'deficit={required-capacity}, affected={detail}')
        result = Counter()
        for i, j in sorted(pairs):
            value = (seed[i, j] + flow[('o', i)].get(('d', j), 0)
                     - flow[('d', j)].get(('o', i), 0))
            if value:
                result[origin_ids[i], destination_ids[j]] = int(value)
        row, col = Counter(), Counter()
        for (o, d), value in result.items():
            row[o] += value
            col[d] += value
        if any(row[key] != int(budgets[i]) for i, key in enumerate(origin_ids)):
            raise ODAllocationError('Integer transport failed its independent row check')
        if any(not floors[j] <= col[key] <= ceilings[j]
               for j, key in enumerate(destination_ids)):
            raise ODAllocationError('Integer transport exceeded a destination rounding bound')
        attempts.append({'width': width, 'edges': len(pairs), 'feasible': True})
        return result, {key: col[key] for key in destination_ids}, attempts
    raise AssertionError('Unreachable transport state')


def finalize_integer_od(initial_points, final_points, proposal_pops, point_mapping,
                        balancing_reports, max_distance_km, beta=0.12,
                        isolated_zones=None, max_pop_size=60, min_pop_size=10):
    """Freeze rolled-up targets and pack the repaired final-ID transport."""
    from .gravity import assign_zones, calculate_commute_impedance
    if max_pop_size < 1:
        raise ODAllocationError('max_pop_size must be positive')
    points = {p['id']: p for p in final_points}
    missing = sorted({p['id'] for p in initial_points} - set(point_mapping))
    unknown = sorted(set(point_mapping.values()) - set(points))
    if missing or unknown:
        raise ODAllocationError(f'Incomplete clustering map: missing original IDs={missing}, '
                                f'unknown final IDs={unknown}')
    budgets, fractional, requested_fractional = Counter(), Counter(), Counter()
    for point in initial_points:
        budgets[point_mapping[point['id']]] += int(point.get('pea_15ymas', 0))
    for report in balancing_reports:
        for key, amount in zip(report['destination_ids'], report['effective_destination_targets']):
            fractional[point_mapping[key]] += float(amount)
        for key, amount in zip(report['destination_ids'], report.get(
                'requested_destination_targets', report['effective_destination_targets'])):
            requested_fractional[point_mapping[key]] += float(amount)
    special = {p['id'] for p in initial_points if p.get('is_special')}
    special_pops = [dict(p) for p in proposal_pops if p['jobId'] in special]
    consumed, special_targets = margins(special_pops)
    remaining = {key: value - consumed[key] for key, value in budgets.items()}
    if any(value < 0 for value in remaining.values()):
        raise ODAllocationError('Special demand exceeds a final origin budget')
    ordered_points = sorted(points)
    zone_by_id = dict(zip(ordered_points, assign_zones(
        np.array([points[key]['location'] for key in ordered_points]), isolated_zones)))
    if any(zone_by_id[p['residenceId']] != zone_by_id[p['jobId']] for p in special_pops):
        raise ODAllocationError('Special commute crosses an isolated-zone boundary')
    proposal = Counter()
    for pop in proposal_pops:
        if pop['jobId'] not in special:
            proposal[pop['residenceId'], pop['jobId']] += pop['size']
    output, integer_targets, zone_reports, pair_distances = Counter(), dict(special_targets), [], {}
    for zone in sorted(set(zone_by_id.values())):
        origins = sorted(key for key, value in remaining.items()
                         if value > 0 and zone_by_id[key] == zone)
        if not origins:
            continue
        destinations = sorted(key for key, value in fractional.items()
                              if value > 0 and zone_by_id[key] == zone)
        if not destinations:
            raise ODAllocationError(f'Zone {zone} has remaining origins but no model destinations')
        total = sum(remaining[key] for key in origins)
        requested = np.array([fractional[key] for key in destinations], dtype=float)
        # Correct only accumulated float32 total noise before freezing targets.
        if abs(float(requested.sum()) - total) > max(0.1, total * 1e-6):
            raise ODAllocationError(f'Zone {zone}: rolled-up target budget mismatch')
        effective = requested * (total / float(requested.sum()))
        orig = np.radians([points[key]['location'] for key in origins])
        dest = np.radians([points[key]['location'] for key in destinations])
        a = (np.sin((dest[None, :, 1] - orig[:, None, 1]) / 2) ** 2
             + np.cos(orig[:, None, 1]) * np.cos(dest[None, :, 1])
             * np.sin((dest[None, :, 0] - orig[:, None, 0]) / 2) ** 2)
        distances = 6371 * 2 * np.arcsin(np.clip(np.sqrt(a), 0, 1))
        distinct = np.array(origins)[:, None] != np.array(destinations)[None, :]
        allowed = (distances <= max_distance_km) & distinct
        fallbacks = []
        # Preserve the existing zero-row/column nearest-five policy, with the
        # self prohibition enforced even when there is only one destination.
        for i in range(len(origins)):
            if not allowed[i].any():
                choices = np.flatnonzero(distinct[i])
                for j in choices[np.argsort(distances[i, choices], kind='stable')[:5]]:
                    allowed[i, j] = True
                    fallbacks.append([origins[i], destinations[int(j)]])
        for j in range(len(destinations)):
            if not allowed[:, j].any():
                choices = np.flatnonzero(distinct[:, j])
                for i in choices[np.argsort(distances[choices, j], kind='stable')[:5]]:
                    allowed[i, j] = True
                    fallbacks.append([origins[int(i)], destinations[j]])
        try:
            pairs, targets, attempts = integer_transport(
                origins, [remaining[key] for key in origins], destinations, effective,
                proposal, allowed, distances, beta)
        except ODAllocationError as error:
            raise ODAllocationError(f'Zone {int(zone)}: {error}') from error
        output.update(pairs)
        oi, dj = {key: i for i, key in enumerate(origins)}, {key: j for j, key in enumerate(destinations)}
        pair_distances.update({pair: float(distances[oi[pair[0]], dj[pair[1]]]) for pair in pairs})
        integer_targets.update(targets)
        zone_reports.append(dict(zone=int(zone), origin_total=total,
            fractional_targets=dict(zip(destinations, effective.tolist())),
            requested_targets={key: requested_fractional[key] for key in destinations},
            pre_normalization_effective_targets=dict(zip(destinations, requested.tolist())),
            integer_targets=targets, support_attempts=attempts,
            distance_fallback_pairs=fallbacks,
            normalization_delta=float(total - requested.sum())))
    pops = list(special_pops)
    for (origin, destination), amount in sorted(output.items()):
        driving_distance, driving_seconds = calculate_commute_impedance(pair_distances[origin, destination])
        chunks = math.ceil(amount / max_pop_size)
        base, residual = divmod(amount, chunks)
        for index in range(chunks):
            pops.append(dict(id='', size=base + (index < residual),
                residenceId=origin, jobId=destination,
                drivingSeconds=driving_seconds, drivingDistance=driving_distance))
    for index, pop in enumerate(pops, 1):
        pop['id'] = f'pop_{index:06d}'
    authority = dict(schema_version=1, mode='balanced_integer_v1',
        semantics='Exact integer model margins; destination targets are estimates, not observed CE totals.',
        origin_budgets=dict(budgets), destination_targets=integer_targets,
        point_mapping=point_mapping, zones=zone_reports,
        max_pop_size=int(max_pop_size),
        special_quotas={p['id']: dict(requested=p.get('jobs', 0), realized=special_targets[p['id']])
                        for p in initial_points if p.get('is_special')},
        pair_targets=[[o, d, amount] for (o, d), amount in sorted(output.items())],
        solver='sparse_residual_dinic',
        allocation_preference='Preserve sampled flow; expand proposal support with nearest admissible neighbors. No global cost optimality claim.',
        small_cohorts=sum(p['size'] < min_pop_size for p in pops),
        cohorts=len(pops), commuters=sum(budgets.values()))
    special_pairs = Counter()
    for pop in special_pops:
        special_pairs[pop['residenceId'], pop['jobId']] += pop['size']
    authority['pair_targets'].extend([[o, d, amount] for (o, d), amount in sorted(special_pairs.items())])
    authority['validation'] = validate_integer_margins(pops, authority)
    return pops, authority
