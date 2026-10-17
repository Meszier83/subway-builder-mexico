"""Bounded gravity-preference screen. Never creates allocated/exportable trips."""
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import time
import uuid

import numpy as np

from .allocation import permitted_destinations

BETAS = (.06, .12, .24)
SAMPLE_SIZE = 512
SEED = 60120
BUDGET_SECONDS = 15.
BIN_KM = .25
VERSION = 'gravity_preference_screen_v1'


def compare(reference, candidate):
    """Engineering thresholds for screening, not accuracy or gameplay claims."""
    relative = {}
    for key in ('median_km', 'p90_km'):
        base = reference[key]
        relative[key] = abs(candidate[key]-base)/base if base else (0. if candidate[key] == 0 else 1.)
    tail = abs(candidate['over_20_km_share']-reference['over_20_km_share'])
    tv = .5*sum(abs(a-b) for a,b in zip(reference['destination_shares'], candidate['destination_shares']))
    return dict(relative_distance_changes=relative, over_20_km_share_change=tail,
                destination_total_variation=tv,
                potentially_sensitive=any(v >= .10-1e-12 for v in (*relative.values(), tail, tv)))


def screen(points, config, progress=None, *, clock=time.perf_counter, budget=BUDGET_SECONDS):
    started = clock()
    report = dict(version=VERSION, status='inconclusive', reason=None, comparisons=[],
                  sample_size=SAMPLE_SIZE, seed=SEED, betas=list(BETAS),
                  basis='Sampled row-normalized gravity preferences; geometric km; not final trips',
                  limitations=['Workplace totals are not balanced', 'Final POI reservations are not modeled',
                               'Does not certify demand accuracy or gameplay'],
                  thresholds=dict(relative_distance=.10, tail_share=.10, destination_total_variation=.10),
                  distance_bin_km=BIN_KM, destination_cell_km=2., cache_hit=False)
    def finish(reason=None):
        report['reason'] = reason
        report['comparison_seconds'] = clock()-started
        return report
    if budget <= 0:
        return finish('timeout')
    origins = sorted((p for p in points if p['commuters'] > 0), key=lambda p:p['id'])
    destinations = sorted((p for p in points if p['attraction'] > 0), key=lambda p:p['id'])
    total = sum(p['commuters'] for p in origins)
    report['commuters'] = total
    if not origins or not destinations:
        return finish('no_origins' if not origins else 'invalid_support')
    chosen = np.random.default_rng(SEED).choice(len(origins), SAMPLE_SIZE, replace=True,
                p=np.asarray([p['commuters'] for p in origins], float)/total)
    counts = Counter(int(i) for i in chosen)
    all_municipalities = {p['municipality'] for p in origins}
    represented = {origins[i]['municipality'] for i in counts}
    report['coverage'] = dict(origin_count=len(origins), sampled_origins=len(counts),
        residential_mass_share=sum(origins[i]['commuters'] for i in counts)/total,
        represented_municipalities=sorted(represented),
        omitted_municipalities=sorted(all_municipalities-represented))
    if all_municipalities != represented:
        return finish('incomplete_coverage')
    scale = np.array([111.32*math.cos(math.radians(np.mean([p['location'][1] for p in points]))),110.574])
    coordinates = np.asarray([p['location'] for p in destinations])
    zones = np.asarray([p['zone'] for p in destinations])
    components = np.asarray([p.get('road_component') for p in destinations], dtype=object)
    special = np.asarray([bool(p.get('is_special')) for p in destinations])
    indices = {p['id']:j for j,p in enumerate(destinations)}
    blocked = {}
    for a,b in config.get('_forbidden_pairs', set()):
        if b in indices:
            blocked.setdefault(a, []).append(indices[b])
    log_attraction = np.log(np.asarray([p['attraction'] for p in destinations], float))
    # Fixed geographic grid, with distinct cells for each municipality.
    keys = [(p['municipality'], int(math.floor(x/2)), int(math.floor(y/2)))
            for p,(x,y) in zip(destinations, coordinates*scale)]
    unique = {key:i for i,key in enumerate(sorted(set(keys)))}
    groups = np.asarray([unique[key] for key in keys])
    shares = np.zeros((len(BETAS),len(unique)))
    histograms = [Counter() for _ in BETAS]
    tails = np.zeros(len(BETAS))
    for ordinal,(i,count) in enumerate(sorted(counts.items())):
        if clock()-started >= budget:
            return finish('timeout')
        selected, distance = permitted_destinations(origins[i], config, coordinates, zones,
            components, indices, blocked, special, scale)
        if not len(selected):
            report['unsupported_origin'] = origins[i]['id']
            return finish('invalid_support')
        bins = np.floor(distance/BIN_KM).astype(int)
        # Each sampled draw represents total/SAMPLE_SIZE commuters. Store shares
        # rather than expanded cells; no origin/destination flows leave this module.
        mass_share = count/SAMPLE_SIZE
        for k,beta in enumerate(BETAS):
            log_weights = log_attraction[selected]-beta*distance
            weights = np.exp(log_weights-log_weights.max())
            weights *= mass_share/weights.sum()
            shares[k] += np.bincount(groups[selected], weights=weights, minlength=len(unique))
            tails[k] += weights[distance>20].sum()
            occupied, inverse = np.unique(bins, return_inverse=True)
            sums = np.bincount(inverse, weights=weights)
            histograms[k].update(dict(zip((int(v) for v in occupied), (float(v) for v in sums))))
        if progress and ordinal%32 == 0:
            progress(f'Comparando preferencias: origen {ordinal+1} de {len(counts)} · límite de cálculo 15 s')
    if clock()-started >= budget:
        return finish('timeout')
    summaries = []
    for k,beta in enumerate(BETAS):
        ordered = sorted(histograms[k])
        cumulative = np.cumsum([histograms[k][b] for b in ordered])
        def quantile(q):
            index = min(int(np.searchsorted(cumulative, q*cumulative[-1])),len(ordered)-1)
            return (ordered[index]+.5)*BIN_KM
        summaries.append(dict(beta=beta, median_km=quantile(.5), p90_km=quantile(.9),
            over_20_km_share=float(tails[k]), estimated_commuters=float(total*shares[k].sum()),
            destination_shares=shares[k].tolist()))
    reference = summaries[1]
    comparisons = [dict(beta=s['beta'], **compare(reference,s)) for s in (summaries[0],summaries[2])]
    for summary in summaries:
        del summary['destination_shares']
    report.update(status='sensitive' if any(c['potentially_sensitive'] for c in comparisons) else 'low_variation',
                  summaries=summaries, comparisons=comparisons)
    if clock()-started >= budget:
        report.pop('summaries', None)
        report.update(status='inconclusive', comparisons=[])
        return finish('timeout')
    return finish()


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':'),default=str).encode()).hexdigest()


def cached_screen(points, config, identity, path=None, progress=None):
    started = time.perf_counter()
    semantic = {k:v for k,v in config.items() if not k.startswith('_')}
    semantic['forbidden_pairs'] = sorted(config.get('_forbidden_pairs', set()))
    key = _digest(dict(version=VERSION,identity=identity,points=points,config=semantic))
    path = Path(path) if path else None
    if path:
        try:
            envelope = json.loads(path.read_text(encoding='utf-8'))
            report = envelope['payload']
            if (envelope['key'] == key and envelope['sha256'] == _digest(report)
                    and report['version'] == VERSION and report['status'] in ('low_variation','sensitive')):
                report.update(cache_hit=True, original_comparison_seconds=report['comparison_seconds'],
                              comparison_seconds=time.perf_counter()-started)
                if progress:
                    progress('Sensibilidad recuperada de caché verificada')
                return report
        except (OSError,ValueError,KeyError,TypeError):
            pass
    remaining = max(0.,BUDGET_SECONDS-(time.perf_counter()-started))
    report = screen(points,config,progress,budget=remaining)
    report['comparison_seconds'] = time.perf_counter()-started
    if path and report['status'] != 'inconclusive':
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary.write_text(json.dumps(dict(key=key,payload=report,sha256=_digest(report))),encoding='utf-8')
            temporary.replace(path)
        except OSError as error:
            report['cache_warning'] = str(error)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError:
                pass
    return report
