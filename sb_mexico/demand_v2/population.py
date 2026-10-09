"""Full municipal allocation, including a nonspatial residual, before clipping."""
import numpy as np
from sb_mexico.workplace_employment import bounded_fit


def allocate_population(blocks, bases, controls, people):
    result = blocks.copy()
    reports = {}
    for code, group in result.groupby('cve_mun_clean', sort=True):
        bp, be = bases[code]
        pop, occupied = controls[code]['Valor']
        observed = people[code]
        if observed['population'] != pop or observed['occupied'] != occupied:
            raise ValueError(f'Incomplete EIC microdata/control reconciliation: {code}')
        residual_p = bp - float(group.pobtot_num.sum())
        residual_e = be - float(group.employed_2020.sum())
        if residual_p < -1e-6 or residual_e < -1e-6:
            raise ValueError(f'CPV block weights exceed complete municipal control: {code}')
        if pop > 0 and bp <= 0 or occupied > pop:
            raise ValueError(f'Incompatible population/occupied controls: {code}')
        weights = np.r_[group.pobtot_num.to_numpy(float), max(0., residual_p)]
        capacities = weights * (pop/bp if bp else 0.)
        priors = np.r_[group.employed_2020.to_numpy(float), max(0., residual_e)]
        # Positive capacity can receive new occupied residents even with a historical zero.
        priors = np.maximum(priors, capacities * 1e-9 + 1e-12)
        employed, factor = bounded_fit(priors, np.zeros(len(priors)), capacities, occupied)
        fraction = observed['commuters']/occupied if occupied else 0.
        commuters = employed * fraction
        result.loc[group.index, 'pobtot_adj'] = capacities[:-1]
        result.loc[group.index, 'occupied_residents'] = employed[:-1]
        result.loc[group.index, 'pea_real'] = commuters[:-1]
        result.loc[group.index, 'labor_commuters'] = commuters[:-1]
        result.loc[group.index, 'pob15_adj'] = group.pob15_num.to_numpy(float) * (pop/bp if bp else 0.)
        result.loc[group.index, 'growth'] = pop/bp if bp else 0.
        reports[code] = dict(population=pop, occupied=occupied, commuters=observed['commuters'],
            no_travel=observed['no_travel'], unspecified=observed['unspecified'],
            commuting_fraction=fraction, occupied_multiplier=factor,
            residual_nonspatial=dict(population=float(capacities[-1]), occupied=float(employed[-1]),
                                    commuters=float(commuters[-1])),
            population_residual=float(capacities.sum()-pop),
            occupied_residual=float(employed.sum()-occupied),
            precision=controls[code], allocation_basis='CPV 2020 weights; differing dwelling universes')
    validate_population(result)
    return result, reports


def validate_population(frame):
    a = frame[['pobtot_adj', 'occupied_residents', 'pea_real']].to_numpy(float)
    if not np.isfinite(a).all() or (a < -1e-8).any() or (a[:, 1] > a[:, 0]+1e-6).any() or (a[:, 2] > a[:, 1]+1e-6).any():
        raise ValueError('Residential invariant violated: commuters <= occupied <= population')


def integer_budgets(values, groups, ids, capacity=None):
    """Largest remainders within territories; stable IDs break ties."""
    values = np.asarray(values, float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError('Invalid integer budget inputs')
    result = np.floor(values).astype(np.int64)
    caps = np.full(len(values), np.iinfo(np.int64).max, dtype=np.int64) if capacity is None else np.asarray(capacity, np.int64)
    if (result > caps).any():
        raise ValueError('Integer budget exceeds capacity')
    for group in sorted(set(groups)):
        indices = [i for i, g in enumerate(groups) if g == group]
        target = int(np.floor(values[indices].sum()+.5))
        remaining = target - int(result[indices].sum())
        ranked = sorted(indices, key=lambda i: (-(values[i]-result[i]), str(ids[i])))
        for i in ranked:
            if remaining and result[i] < caps[i]:
                result[i] += 1
                remaining -= 1
        if remaining:
            raise ValueError(f'Integer territorial budget infeasible: {group}')
    return result
