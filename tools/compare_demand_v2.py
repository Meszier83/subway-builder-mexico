"""Build isolated statistical comparisons; never activate or migrate a project."""
import argparse
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def peak_process_bytes():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in
                ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
                 'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        counters=Counters(); counters.cb=ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype=wintypes.HANDLE
        process=ctypes.windll.kernel32.GetCurrentProcess()
        query=ctypes.windll.psapi.GetProcessMemoryInfo
        query.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        if query(process,ctypes.byref(counters),counters.cb): return counters.PeakWorkingSetSize
        return None
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024)


def common_validation(legacy, candidate, model, mobility):
    """Evaluate exported flows against identical held-out municipal coverage."""
    import numpy as np
    from scipy.spatial import cKDTree
    from sb_mexico.demand_v2.allocation import municipal_score
    if not model:
        score = dict(conditional_kl=None, evaluated_weight=0., note='No represented candidate geography')
        return dict(scores={'legacy':dict(score),'candidate':dict(score)},origins=[],destinations=[],
                    basis='No common municipal coverage')
    tree = cKDTree(np.asarray([p['location'] for p in model]))
    labels = {p['id']:p['municipality'] for p in model}
    games = {'legacy':legacy, 'candidate':candidate}
    mappings = {'candidate':labels,
        'legacy':{p['id']:model[int(tree.query(p['location'])[1])]['municipality'] for p in legacy['points']}}
    origins, destinations = [], []
    for name, game in games.items():
        origins.append({mappings[name][p['residenceId']] for p in game['pops']})
        destinations.append({mappings[name][p['jobId']] for p in game['pops']})
    common_origins, common_destinations = set.intersection(*origins), set.intersection(*destinations)
    scores = {}
    for name, game in games.items():
        a = [dict(p,municipality=mappings[name][p['id']]) for p in game['points'] if mappings[name][p['id']] in common_origins]
        b = [dict(p,municipality=mappings[name][p['id']]) for p in game['points'] if mappings[name][p['id']] in common_destinations]
        ai, bi = {p['id']:i for i,p in enumerate(a)}, {p['id']:i for i,p in enumerate(b)}
        flows = [p for p in game['pops'] if p['residenceId'] in ai and p['jobId'] in bi]
        scores[name] = municipal_score(np.asarray([p['size'] for p in flows],float),
            [ai[p['residenceId']] for p in flows], [bi[p['jobId']] for p in flows], a,b,mobility,'validation_weight')
    return dict(scores=scores, origins=sorted(common_origins), destinations=sorted(common_destinations),
                basis='Exported integer flows; identical municipal coverage; legacy labels estimated from nearest candidate source reference')


def compare(config_path, output, roads_path=None, reuse_baseline=False):
    from sb_mexico.pipeline import load_city_config, execute_pipeline
    from sb_mexico.demand_v2 import prepare_request, build_demand
    from sb_mexico.demand_v2.engine import write_result
    from sb_mexico.demand_v2.integration import load_roads
    from sb_mexico.gravity import calculate_commute_distance_distribution
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cfg = load_city_config(str(config_path))
    baseline = copy.deepcopy(cfg)
    baseline.setdefault('demand', {})['engine'] = 'legacy'
    baseline_path = output/'baseline.yaml'
    if reuse_baseline and (not baseline_path.exists() or yaml.safe_load(baseline_path.read_text(encoding='utf-8')) != baseline):
        raise ValueError('Saved baseline configuration does not match')
    baseline_path.write_text(yaml.safe_dump(baseline, allow_unicode=True), encoding='utf-8')
    old_output = output/'legacy'
    old_output.mkdir(exist_ok=True)
    if roads_path:
        import shutil
        shutil.copyfile(roads_path, old_output/'roads.geojson')
    started = time.perf_counter()
    if not reuse_baseline:
        with (output/'legacy.log').open('w', encoding='utf-8') as log:
            # Only service availability is overridden, not ingestion or demand mathematics.
            with (patch('sb_mexico.pipeline.is_docker_available', return_value=(False, 'comparison_canonical')),
                  contextlib.redirect_stdout(log)):
                execute_pipeline(str(baseline_path), skip_map=True, output_dir=str(old_output))
    old_seconds = None if reuse_baseline else time.perf_counter()-started
    legacy = json.loads((old_output/'demand_data.json').read_text(encoding='utf-8'))
    candidate = copy.deepcopy(cfg)
    candidate.setdefault('demand', {}).update(engine='v2', target_year=2025, beta=None)
    request = prepare_request(candidate, ROOT, roads=load_roads(roads_path) if roads_path else None)
    if reuse_baseline:
        from sb_mexico.residential_employment import file_sha256
        residential = json.loads((old_output/'residential_employment_report.json').read_text(encoding='utf-8'))
        workplace = json.loads((old_output/'workplace_employment_report.json').read_text(encoding='utf-8'))
        bindings = dict(cpv=residential['sources'], eic=residential.get('demographic_reference', {}).get('sources', []))
        for role, rows in bindings.items():
            selected = request.sources['cpv'] if role == 'cpv' else request.sources['eic_indicators']+request.sources['eic_persons']
            if sorted(file_sha256(p) for p in set(selected)) != sorted(r['sha256'] for r in rows):
                raise ValueError('Baseline '+role+' sources changed; regenerate baseline')
        for role in ('denue', 'ce'):
            if sorted(file_sha256(p) for p in set(request.sources[role])) != sorted(workplace['source_sha256'][role]):
                raise ValueError('Baseline '+role+' source hashes changed')
        baseline_timestamp = (old_output/'demand_data.json').stat().st_mtime
        if any(Path(p).stat().st_mtime > baseline_timestamp for p in request.sources['marco']):
            raise ValueError('Geometry newer than baseline; regenerate baseline')
    started = time.perf_counter()
    result = build_demand(request)
    candidate_seconds = time.perf_counter()-started
    write_result(result, request, output/'candidate')
    import numpy as np
    from scipy.spatial import cKDTree
    from sb_mexico.demand_v2.allocation import municipal_score
    model = result['points']
    old_score = dict(conditional_kl=None,evaluated_weight=0.,note='No represented candidate geography')
    if model:
        tree = cKDTree(np.asarray([p['location'] for p in model]))
        labeled = [dict(p, municipality=model[int(tree.query(p['location'])[1])]['municipality']) for p in legacy['points']]
        index = {p['id']:i for i,p in enumerate(labeled)}
        old_score = municipal_score(np.asarray([p['size'] for p in legacy['pops']], float),
            [index[p['residenceId']] for p in legacy['pops']], [index[p['jobId']] for p in legacy['pops']],
            labeled, labeled, result['report']['mobility'], 'validation_weight')
    metrics = {}
    for label, game, seconds in [('legacy', legacy, old_seconds),('candidate', result['game'], candidate_seconds)]:
        metrics[label] = dict(points=len(game['points']), cohorts=len(game['pops']),
            commuters=sum(p['size'] for p in game['pops']), seconds=seconds,
            bytes=len(json.dumps(game, separators=(',', ':')).encode()),
            distance_distribution=calculate_commute_distance_distribution(game['pops'], game['points']))
    metrics['process_peak_bytes'] = peak_process_bytes()
    metrics['memory_measurement'] = 'Process peak includes imports, retained baseline JSON and any baseline generation'
    metrics['legacy']['validation'] = old_score
    metrics['common_validation'] = common_validation(legacy,result['game'],result['points'],result['report']['mobility'])
    metrics.update(config_sha256=hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
                   candidate_identity=result['identity'], sources=result['report']['sources'],
                   routing='Same canonical fallback for comparison; no OSRM or game validation claimed',
                   validation=result['report']['allocation'].get('validation',dict(conditional_kl=None,evaluated_weight=0.)),
                   status='candidate_only', migration=False)
    (output/'comparison.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding='utf-8')
    return metrics


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('config')
    parser.add_argument('--output', required=True)
    parser.add_argument('--roads')
    parser.add_argument('--reuse-baseline', action='store_true', help='Requires identical saved configuration; source identity still reported')
    args=parser.parse_args()
    print(json.dumps(compare(args.config,args.output,args.roads,args.reuse_baseline),ensure_ascii=False,indent=2))
