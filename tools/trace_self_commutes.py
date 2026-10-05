"""Trace the pre-fix pipeline stages without exporting a map or changing city files."""
import json
import shutil
import sys
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sb_mexico import gravity, pipeline


def stage_summary(points, pops):
    loops = [p for p in pops if p.get('size',0) > 0 and p['residenceId'] == p['jobId']]
    return dict(points=len(points),cohorts=len(pops),commuters=sum(p['size'] for p in pops),
                self_cohorts=len(loops),self_commuters=sum(p['size'] for p in loops),
                examples=[dict(residenceId=p['residenceId'],jobId=p['jobId'],size=p['size']) for p in loops[:5]])


class TraceComplete(Exception):
    pass


def trace():
    output = ROOT/'reports/self-commute-fix'
    namespace = dict(vars(gravity))
    snapshot = output/'cluster_before.py'
    exec(compile(snapshot.read_text(encoding='utf-8'),str(snapshot),'exec'),namespace)
    old_cluster = namespace['cluster_demand_points']
    results = {}
    for case in ('legacy','transfer_full'):
        directory = output/'before'/case
        directory.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/'dist/cancun_riviera_maya/roads.geojson',directory/'roads.geojson')
        stages = []
        stage_points = []
        def wrap(name, fn, tuple_result=True, stop=False):
            def capture(*args, **kwargs):
                nonlocal stage_points
                result = fn(*args, **kwargs)
                if tuple_result:
                    points, pops = result
                    stage_points = points
                else:
                    pops = result
                    if name == 'gravity':
                        stage_points = kwargs.get('demand_points',args[0] if args else [])
                    points = stage_points
                stages.append(dict(stage=name,**stage_summary(points,pops)))
                if stop: raise TraceComplete()
                return result
            return capture
        with (directory/'trace.log').open('w',encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log), ExitStack() as stack:
            stack.enter_context(patch.object(pipeline,'simulate_gravity_demand',wrap('gravity',pipeline.simulate_gravity_demand,False)))
            stack.enter_context(patch.object(pipeline,'cluster_demand_points',wrap('clustering',old_cluster)))
            stack.enter_context(patch.object(pipeline,'consolidate_small_pops',wrap('consolidation',pipeline.consolidate_small_pops)))
            stack.enter_context(patch.object(pipeline,'merge_identical_commutes',wrap('identical_merge',pipeline.merge_identical_commutes,False)))
            stack.enter_context(patch.object(pipeline,'sync_demand_points_and_pops',wrap('synchronization',pipeline.sync_demand_points_and_pops,stop=True)))
            try:
                pipeline.execute_pipeline(str(ROOT/'reports/historical-transfer/cancun'/case/'effective.yaml'),skip_map=True,output_dir=str(directory))
            except TraceComplete:
                pass
        results[case] = stages
        print(case,[(s['stage'],s['self_cohorts'],s['self_commuters']) for s in stages],flush=True)
        (output/'before_stages.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__': trace()
