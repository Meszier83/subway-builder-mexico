"""Replay automatic beta on a saved spatial model; never modify city sources.

Example: python tools/verify_beta_policy.py --snapshot <build-directory> --output <directory>
The snapshot fixes population, workplace weights, POIs and spatial grouping.
Fresh EIC partitioning is required; old mobility aggregates cannot prove a guard.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def verify(snapshot, output):
    from sb_mexico.demand_v2.sources import read_mobility
    from sb_mexico.demand_v2.allocation import _allocate_at_beta
    from sb_mexico.demand_v2.beta_policy import select_beta, probabilities, evaluate, sample_rows
    snapshot, output = Path(snapshot).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = output/'tmp'; scratch.mkdir(exist_ok=True); tempfile.tempdir=str(scratch)
    model_path, report_path = snapshot/'demand_model.json', snapshot/'demand_pipeline_report.json'
    model=json.loads(model_path.read_text(encoding='utf-8'))
    old=json.loads(report_path.read_text(encoding='utf-8'))
    points=model['points']
    source_paths=[r['path'] for r in old['sources'] if r['role']=='eic_persons']
    mobility=read_mobility(source_paths, set(old['municipalities']))
    config=dict(beta_selection='automatic',cohort_settings=old['allocation']['cohorts']['options'])
    options=config['cohort_settings']
    # Old adaptive snapshots predate the fixed_size key.
    options.setdefault('fixed_size', None)
    forbidden=old.get('routing',{}).get('excluded_no_route_pairs',[])
    if forbidden: config['_forbidden_pairs']={tuple(pair) for pair in forbidden}
    budget=Counter({p['id']:p['commuters'] for p in points if p['commuters']})
    runs=[]; reference=None
    def solve(points, config, evidence):
        nonlocal reference
        started=time.perf_counter()
        cells, report=_allocate_at_beta(points,config,evidence)
        realized=Counter()
        for cell in cells:
            realized[cell['origin']]+=cell['size']
            assert sum(cell['cohort_sizes'])==cell['size']
            assert all(0<size<=options['max_pop_size'] for size in cell['cohort_sizes'])
            assert (cell['origin'],cell['destination']) not in config.get('_forbidden_pairs',set())
        assert realized==budget
        for quota in report.get('poi_quotas',[]):
            assert sum(c['size'] for c in cells if c['destination']==quota['id'])==quota['realized']
        summary=dict(beta=config['beta'],seconds=time.perf_counter()-started,
                     commuters=sum(realized.values()),cohorts=sum(len(c['cohort_sizes']) for c in cells),
                     validation_integer=report['validation_integer'],
                     cells_sha256=hashlib.sha256(json.dumps(cells,sort_keys=True).encode()).hexdigest())
        runs.append(summary)
        if config['beta']==.12: reference=cells
        return cells,report
    started=time.perf_counter()
    cells,report=select_beta(points,config,mobility,solve)
    guard,_=sample_rows(points,mobility,'guard')
    baseline_loss=evaluate(probabilities(points,reference),guard)
    final_loss=evaluate(probabilities(points,cells),guard)
    if baseline_loss is not None: assert final_loss<=baseline_loss+1e-12
    result=dict(snapshot=str(snapshot),seconds=time.perf_counter()-started,runs=runs,
                report=report,guard_reference_loss=baseline_loss,guard_selected_loss=final_loss,
                snapshot_sha256=hashlib.sha256(model_path.read_bytes()).hexdigest(),
                code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT/'sb_mexico/demand_v2').glob('*.py'))},
                sources_sha256={str(p):hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in source_paths},
                verification='Spatial snapshot replay; not fresh ingestion, HTTP delivery or game validation')
    (output/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'cells.json').write_text(json.dumps(cells,separators=(',',':')),encoding='utf-8')
    print(json.dumps(dict(output=str(output),beta=report['beta'],reason=report['beta_policy']['reason'],
                         seconds=result['seconds'],runs=len(runs),commuters=sum(c['size'] for c in cells)),ensure_ascii=False))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();verify(args.snapshot,args.output)
