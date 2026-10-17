"""Bounded automatic beta selection on complete integer allocations.

The empirical record bootstrap is a stability diagnostic, NOT a survey-design
confidence interval. Selection and guard records never enter municipal fitting.
"""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time
import uuid
import numpy as np

BASE = .12
CANDIDATES = (.06, .09, .12, .18, .24)
REPLICATES = 200
MIN_EFFECTIVE = 30


def distance_summary(points, cells):
    locations = {p['id']: p['location'] for p in points}
    values = []
    for cell in cells:
        a, b = locations[cell['origin']], locations[cell['destination']]
        lat = math.radians((a[1]+b[1])/2)
        distance = math.hypot((a[0]-b[0])*111.32*math.cos(lat), (a[1]-b[1])*110.574)
        values.append((distance, cell['size']))
    values.sort()
    total = sum(n for _, n in values)
    def quantile(q):
        accumulated = 0
        for distance, count in values:
            accumulated += count
            if accumulated >= q*total:
                return float(distance)
        return None
    return dict(median_km=quantile(.5), p90_km=quantile(.9), commuters=total,
                basis='Final OD endpoints, approximate geometric km; not road distance or survey time')


def probabilities(points, cells):
    municipalities = {p['id']: p['municipality'] for p in points}
    flows, totals = Counter(), Counter()
    for cell in cells:
        key = municipalities[cell['origin']], municipalities[cell['destination']]
        flows[key] += cell['size']; totals[key[0]] += cell['size']
    return {key: count/totals[key[0]] for key, count in flows.items()}


def sample_rows(points, mobility, partition):
    origins = {p['municipality'] for p in points if p['commuters'] > 0}
    destinations = {p['municipality'] for p in points if p['attraction'] > 0}
    samples = []
    outside = 0.
    for row in sorted(mobility.get('flows', []), key=lambda r: (r['origin'], r['destination'])):
        if row['origin'] not in origins or row['destination'] not in destinations:
            outside += row.get(partition+'_weight', 0.)
            continue
        for weight, count in sorted(row.get(partition+'_weights', {}).items(), key=lambda x: int(x[0])):
            if int(weight) > 0 and count > 0:
                samples.append(((row['origin'], row['destination']), int(weight), int(count)))
    return samples, outside


def evaluate(probability, samples):
    weight = sum(w*n for _, w, n in samples)
    if not weight:
        return None
    return sum(-w*n*math.log(max(probability.get(key, 0.), 1e-12))
               for key, w, n in samples)/weight


def improvement(reference, candidate, samples):
    """Paired Poisson record bootstrap, retaining individual expansion weights.

    Histograms of identical weights are sufficient because loss is constant
    within each municipal pair. No person identifiers are retained in reports.
    """
    weight = sum(w*n for _, w, n in samples)
    squares = sum(w*w*n for _, w, n in samples)
    effective = weight*weight/squares if squares else 0.
    result = dict(effective_sample_size=effective, records=sum(n for _, _, n in samples),
                  evaluated_weight=weight, gain=None, lower95=None, standard_error=None)
    if not weight:
        return result
    delta = np.array([math.log(max(candidate.get(k, 0.), 1e-12)/max(reference.get(k, 0.), 1e-12))
                      for k, _, _ in samples])
    weights = np.array([w for _, w, _ in samples], float)
    counts = np.array([n for _, _, n in samples], int)
    rng = np.random.default_rng(60120)
    gains = []
    # Bound memory independently of the number of distinct expansion weights.
    for _ in range(REPLICATES):
        draw = rng.poisson(counts)
        mass = float(np.dot(draw, weights))
        if mass:
            gains.append(float(np.dot(draw, weights*delta)/mass))
    result.update(gain=float(np.dot(counts, weights*delta)/weight),
                  lower95=float(np.quantile(gains, .05)) if gains else None,
                  standard_error=float(np.std(gains, ddof=1)) if len(gains)>1 else None)
    return result


def _select_beta(points, config, mobility, solve):
    started = time.perf_counter()
    cache = {}
    support_cache = {}
    completed = [0]
    def run(beta):
        completed[0] += 1
        if config.get('_beta_progress'):
            message = (f'Fricción manual: calculando {beta:.3f}' if mode == 'manual' else
                       f'Fricción de referencia: calculando {beta:.3f}' if mode == 'reference' else
                       f'Fricción automática: comparando {beta:.3f} · opción {completed[0]} de hasta 5')
            config['_beta_progress'](message)
        print(f'   • Fricción v2 ({mode}): evaluando beta {beta:.3f}', flush=True)
        # Explicit beta bypasses selection in the ordinary solver, including POIs.
        return solve(points, dict(config, beta=beta, _support_cache=support_cache), mobility)
    explicit = config.get('beta')
    mode = 'manual' if explicit is not None else config.get('beta_selection', 'reference')
    baseline = run(explicit if explicit is not None else BASE)
    cells, report = baseline
    policy = dict(version='final_integer_60_20_20_v1', reference_beta=BASE,
                  candidates=list(CANDIDATES) if mode == 'automatic' else [explicit if explicit is not None else BASE], mode=mode,
                  reason='explicit_parameter' if explicit is not None else 'reference_requested' if mode == 'reference' else 'insufficient_evidence',
                  bootstrap=dict(method='paired_poisson_records', replicates=REPLICATES,
                                 seed=60120, semantics='Empirical stability diagnostic; not survey-design confidence'),
                  comparisons=[], routing_support_rechecked=bool(config.get('_forbidden_pairs')))
    selected = explicit if explicit is not None else BASE
    if mode == 'automatic':
        selection, outside_select = sample_rows(points, mobility, 'selection')
        guard, outside_guard = sample_rows(points, mobility, 'guard')
        reference = probabilities(points, cells)
        select_info = improvement(reference, reference, selection)
        guard_info = improvement(reference, reference, guard)
        policy.update(selection_sample=select_info, guard_sample=guard_info,
                      excluded_weight=dict(selection=outside_select, guard=outside_guard),
                      coverage='Whole-municipal EIC, conditional on represented origins/destinations; retained area is estimated')
        enough = min(select_info['effective_sample_size'], guard_info['effective_sample_size']) >= MIN_EFFECTIVE
        informative = any(len({k[1] for k in reference if k[0] == origin}) > 1
                          for origin, _ in reference)
        if not informative:
            policy['reason'] = 'municipal_flows_uninformative'
        elif enough:
            cache[BASE] = (cells, report, reference)
            failures = []
            for beta in CANDIDATES:
                if beta == BASE:
                    continue
                try:
                    candidate_cells, candidate_report = run(beta)
                except ValueError as error:
                    failures.append(dict(beta=beta, error=str(error)))
                    continue
                probability = probabilities(points, candidate_cells)
                comparison = improvement(reference, probability, selection)
                comparison.update(beta=beta, loss=evaluate(probability, selection))
                policy['comparisons'].append(comparison)
                cache[beta] = (candidate_cells, candidate_report, probability)
            if len(cache)>1:
                best = min(cache, key=lambda b: (evaluate(cache[b][2], selection), abs(b-BASE), b))
                best_probability = cache[best][2]
                # Paired one-standard-error rule prefers the reference, or the
                # smallest departure from it, when candidate losses are indistinguishable.
                eligible = []
                for beta, (_, _, probability) in cache.items():
                    difference = improvement(probability, best_probability, selection)
                    if difference['gain'] <= (difference['standard_error'] or 0.) + 1e-12:
                        eligible.append(beta)
                chosen = min(eligible, key=lambda b: (abs(b-BASE), b))
                check = improvement(reference, cache[chosen][2], guard)
                policy.update(proposed_beta=chosen, guard_check=check, candidate_failures=failures)
                if chosen != BASE and check['lower95'] is not None and check['lower95']>0:
                    cells, report, _ = cache[chosen]
                    selected = chosen; policy['reason'] = 'stable_guard_improvement'
                else:
                    policy['reason'] = 'no_stable_improvement'
            else:
                policy.update(reason='candidate_solver_failures', candidate_failures=failures)
    policy.update(selected_beta=selected, elapsed_seconds=time.perf_counter()-started)
    report.update(beta=selected, beta_calibrated=selected != BASE and explicit is None,
                  beta_basis='explicit parameter' if explicit is not None else
                             'EIC selection and independent decision guard' if selected != BASE else 'standard reference',
                  beta_policy=policy, distance_summary=distance_summary(points, cells))
    return cells, report


def select_beta(points, config, mobility, solve):
    """Reuse only exact source/code/configuration/points/evidence/support identities."""
    path = config.get('_beta_cache_path')
    source_identity = config.get('_beta_cache_identity')
    if not path or not source_identity or config.get('beta') is not None:
        return _select_beta(points, config, mobility, solve)
    started = time.perf_counter()
    # Private runtime objects are excluded; route exclusions remain semantic inputs.
    semantic = {k:v for k,v in config.items() if not k.startswith('_')}
    semantic.setdefault('beta_selection', 'reference')
    semantic['forbidden_pairs'] = sorted(config.get('_forbidden_pairs', set()))
    key = hashlib.sha256(json.dumps(dict(version='beta_cache_v1', identity=source_identity,
                    config=semantic,points=points,mobility=mobility),
                    sort_keys=True,separators=(',', ':'),default=str).encode()).hexdigest()
    path = Path(path)
    def digest(payload):
        return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    try:
        envelope = json.loads(path.read_text(encoding='utf-8'))
        payload = envelope['payload']
        if envelope['key']==key and envelope['sha256']==digest(payload):
            cells, report = payload['cells'], payload['report']
            # Summaries from another code/source revision never reach this branch.
            if report['beta_policy']['version']=='final_integer_60_20_20_v1':
                report['beta_policy'].update(cache_hit=True,
                    computation_seconds=report['beta_policy']['elapsed_seconds'],
                    elapsed_seconds=time.perf_counter()-started)
                print('   • Fricción v2: resultado final reutilizado; identidad y checksum verificados',flush=True)
                if config.get('_beta_progress'):
                    config['_beta_progress']('Resultado final recuperado de caché verificada')
                return cells,report
    except (OSError, ValueError, KeyError, TypeError):
        pass
    cells,report=_select_beta(points,config,mobility,solve)
    report['beta_policy']['cache_hit']=False
    payload=dict(cells=cells,report=report)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary.write_text(json.dumps(dict(key=key,sha256=digest(payload),payload=payload),
                                       separators=(',', ':')),encoding='utf-8')
        temporary.replace(path)
    except OSError as error:
        report['beta_policy']['cache_warning']=str(error)
    finally:
        try:
            if temporary.is_file():
                temporary.unlink()
        except OSError:
            pass
    return cells,report
