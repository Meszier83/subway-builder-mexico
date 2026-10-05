"""Read-only production replay and exported routing impact; isolated audit artifacts."""
import copy
import hashlib
import inspect
import json
import math
from pathlib import Path
import shutil
import sys
from collections import Counter, defaultdict
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sb_mexico import gravity, pipeline
from sb_mexico.osrm import calculate_canonical_driving_fallback


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_path(value):
    if sys.platform != 'win32' and len(value) > 2 and value[1:3] == ':\\':
        return Path('/mnt/' + value[0].lower() + '/' + value[3:].replace('\\', '/'))
    return Path(value)


def distance(a, b):
    dx = (b[0]-a[0])*111320*math.cos(math.radians((a[1]+b[1])/2))
    dy = (b[1]-a[1])*110574
    return math.hypot(dx, dy)


def audit():
    candidate = '--candidate' in sys.argv
    manifest_path = ROOT/'dist/cancun_riviera_maya/wizard-build.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    built = native_path(manifest['output_dir'])
    out = ROOT/'reports/build-impact'/manifest['build_id']
    baseline_out = out
    if candidate:
        out = out/'reachable-budget-candidate'
    out.mkdir(parents=True, exist_ok=True)
    inputs = [manifest_path, built/'effective-city.yaml', built/'demand_data.json', built/'CUR.zip',
              built/'demand_pipeline_report.json', built/'workplace_employment_report.json']
    before = {str(p):sha(p) for p in inputs}
    saved_balancing = json.loads((built/'demand_pipeline_report.json').read_text(encoding='utf-8'))['employment_balancing']
    recorded_target_correction = any(row.get('target_policy') == 'reachable_budget_correction' for row in saved_balancing)
    cfg = yaml.safe_load((built/'effective-city.yaml').read_text(encoding='utf-8'))
    cfg['data_dir'] = str(native_path(cfg['data_dir']))
    places = cfg.get('places', [])
    def label(loc):
        candidates = [(distance(loc,p['loc'])/1000,p.get('name','')) for p in places if isinstance(p,dict) and 'loc' in p]
        d,n = min(candidates) if candidates else (None,None)
        return dict(nearest_place=n, nearest_place_distance_km=d)
    demand = json.loads((built/'demand_data.json').read_text(encoding='utf-8'))
    points = {p['id']:p for p in demand['points']}
    total = sum(p['size'] for p in demand['pops'])
    pair_pops = defaultdict(list)
    for pop in demand['pops']:
        pair_pops[pop['residenceId'],pop['jobId']].append(pop)
    inferred = []
    endpoint_mass = Counter()
    for (a,b),pops in pair_pops.items():
        loc_a,loc_b = points[a]['location'],points[b]['location']
        euclid = distance(loc_a,loc_b)
        meters,seconds = calculate_canonical_driving_fallback(euclid)
        if all((p.get('drivingDistance'),p.get('drivingSeconds')) == (meters,seconds) for p in pops):
            mass = sum(p['size'] for p in pops)
            endpoint_mass[a]+=mass; endpoint_mass[b]+=mass
            inferred.append(dict(origin=a,destination=b,origin_location=loc_a,destination_location=loc_b,
                travelers=mass,cohorts=len(pops),distance_m=meters,seconds=seconds,
                origin_area=label(loc_a),destination_area=label(loc_b)))
    routing = dict(method='Reconstruction from exact exported canonical fallback distance and time; no per-pair source flag was persisted',
        queried_pairs=len(pair_pops), inferred_pairs=len(inferred), logged_fallback_pairs=601,
        matches_logged_count=len(inferred)==601, travelers=sum(p['travelers'] for p in inferred),
        cohorts=sum(p['cohorts'] for p in inferred), total_travelers=total,
        failure_reasons='not persisted; cannot distinguish NoRoute, snapping, physical guard or request errors',
        top_pairs=sorted(inferred,key=lambda p:-p['travelers'])[:20],
        top_endpoints=[dict(id=k,traveler_incidence=v,location=points[k]['location'],**label(points[k]['location']))
                       for k,v in endpoint_mass.most_common(20)])
    routing['traveler_percent'] = routing['travelers']/total*100
    (out/'inferred_fallback_pairs.json').write_text(json.dumps(inferred,ensure_ascii=False,indent=2)+'\n')
    (out/'routing.json').write_text(json.dumps(routing,ensure_ascii=False,indent=2)+'\n')
    print('Fallback impact:',routing['inferred_pairs'],'pairs,',routing['travelers'],'travelers',flush=True)

    # Only the transport spelling of the saved absolute data directory changes.
    replay_dir = out/'replay'; replay_dir.mkdir(exist_ok=True)
    config_path = replay_dir/'effective.yaml'
    config_path.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False),encoding='utf-8')
    shutil.copyfile(built/'roads.geojson',replay_dir/'roads.geojson')
    balance_original = gravity.furness_ipfp_balance
    sync_original = pipeline.sync_demand_points_and_pops
    results=[]
    class ReplayComplete(Exception): pass
    def balance(*args,**kwargs):
        parent=inspect.currentframe().f_back.f_locals
        ids=[parent['regular_dests'][int(i)] for i in parent['dest_indices']]
        zone=int(parent['z'])
        origin=np.asarray(kwargs['orig_pea']).copy()
        jobs=np.asarray(kwargs['dest_jobs']).copy()
        kwargs['correct_destination_targets'] = candidate or recorded_target_correction
        captured_support = {}
        def trace_support(frame, event, arg):
            if frame.f_code is balance_original.__code__ and event == 'line' and 'eps' in frame.f_locals and not captured_support:
                captured_support['matrix'] = frame.f_locals['t_mat'] > 0
                sys.settrace(None)
            return trace_support
        sys.settrace(trace_support)
        try:
            prob=balance_original(*args,**kwargs)
        finally:
            sys.settrace(None)
        target=((jobs.astype(np.float64)/float(jobs.sum()))*float(origin.sum())).astype(np.float32).astype(np.float64)
        expected=np.einsum('i,ij->j',origin.astype(np.float32),prob,dtype=np.float64,optimize=False)
        requested_target = target.copy()
        target = np.asarray(kwargs['diagnostics']['effective_destination_targets'])
        delta=expected-target
        relative=np.divide(abs(delta),target,out=np.zeros_like(delta),where=target>0)
        rows=[dict(id=p['id'],location=p['location'],**label(p['location']),raw_attraction=float(jobs[i]),
                   requested_target=float(requested_target[i]),target=float(target[i]),expected=float(expected[i]),delta=float(delta[i]),relative_error=float(relative[i]))
              for i,p in enumerate(ids)]
        if candidate:
            baseline = json.loads((baseline_out/f'destinations_zone_{zone}.json').read_text())
            baseline_expected = {row['id']:row['expected'] for row in baseline}
            comparison = dict(expected_arrival_half_l1=float(sum(abs(row['expected']-baseline_expected[row['id']]) for row in rows)/2),
                requested_target_half_l1=float(abs(target-requested_target).sum()/2),
                unsupported_probability_cells=int(np.count_nonzero(prob[~captured_support['matrix']])),
                origin_probability_max_error=float(abs(prob[origin>0].sum(axis=1)-1).max()))
        else:
            comparison = None
        affected=relative>float(kwargs['tol'])
        deficient = (target - expected) > float(kwargs['tol']) * target
        reachable = captured_support['matrix'][:, deficient].any(axis=1)
        bottleneck = dict(destinations=int(deficient.sum()), reachable_origins=int(reachable.sum()),
            reachable_origin_budget=float(origin[reachable].sum()), destination_target=float(target[deficient].sum()),
            unavoidable_deficit=max(0., float(target[deficient].sum()-origin[reachable].sum())))
        result=dict(zone=zone, baseline_comparison=comparison, deficient_subset_support=bottleneck, report=dict(kwargs['diagnostics']),destinations=len(ids),regular_commuter_budget=int(origin.sum()),
                    above_tolerance_destinations=int(affected.sum()),above_tolerance_target_mass=float(target[affected].sum()),
                    above_tolerance_target_percent=float(target[affected].sum()/target.sum()*100),
                    half_l1_expected_mass=float(abs(delta).sum()/2),half_l1_percent=float(abs(delta).sum()/2/target.sum()*100),
                    top_absolute=sorted(rows,key=lambda r:-abs(r['delta']))[:20],top_relative=sorted(rows,key=lambda r:-r['relative_error'])[:20])
        (out/f'destinations_zone_{zone}.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
        results.append(result)
        print('Captured balancing zone',zone,file=sys.__stdout__,flush=True)
        return prob
    replay_match={}
    def sync(*args,**kwargs):
        pts,pops=sync_original(*args,**kwargs)
        columns=lambda ps:Counter((p['residenceId'],p['jobId'],p['size']) for p in ps)
        def masses(ps):
            result = Counter()
            for p in ps:
                result[p['residenceId'],p['jobId']] += p['size']
            return result
        candidate_mass, baseline_mass = masses(pops), masses(demand['pops'])
        replay_match.update(exported_cohort_multiset_equal=columns(pops)==columns(demand['pops']),
            replay_cohorts=len(pops),exported_cohorts=len(demand['pops']),replay_commuters=sum(p['size'] for p in pops),
            exported_commuters=total,self_commute_cohorts=sum(p['residenceId']==p['jobId'] for p in pops),
            final_pair_mass_half_l1=sum(abs(candidate_mass[k]-baseline_mass[k]) for k in set(candidate_mass)|set(baseline_mass))/2,point_locations_equal={p['id']:p['location'] for p in pts}=={p['id']:p['location'] for p in demand['points']})
        raise ReplayComplete()
    print('Replaying source ingestion and allocation, stopping before OSRM/export.',flush=True)
    with (out/'replay.log').open('w',encoding='utf-8') as log,redirect_stdout(log),redirect_stderr(log), \
         patch.object(gravity,'furness_ipfp_balance',new=balance), \
         patch.object(pipeline,'sync_demand_points_and_pops',side_effect=sync):
        try:
            pipeline.execute_pipeline(str(config_path),skip_map=True,output_dir=str(replay_dir))
        except ReplayComplete:
            pass
    saved=saved_balancing
    if not candidate:
        assert all(abs(a['report']['column_relative_error']-b['column_relative_error'])<1e-6 for a,b in zip(results,saved))
        assert replay_match['exported_cohort_multiset_equal'] and replay_match['point_locations_equal'], replay_match
    assert replay_match['replay_commuters'] == total and not replay_match['self_commute_cohorts'], replay_match
    assert before=={str(p):sha(p) for p in inputs}, 'Original build artifacts changed during audit'
    summary=dict(build_id=manifest['build_id'],candidate=candidate,source_package_sha256=before[str(built/'CUR.zip')],
                 original_artifacts_unchanged=True,routing=routing,balancing=results,replay_identity=replay_match)
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(routing_travelers=routing['travelers'],routing_percent=routing['traveler_percent'],
                         balancing=[dict(zone=z['zone'], deficient_subset_support=z['deficient_subset_support'], status=z['report']['status'], residual=z['report']['column_relative_error'],comparison=z['baseline_comparison']) for z in results],
                         replay_identity=replay_match),indent=2),flush=True)


if __name__=='__main__': audit()
