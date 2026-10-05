"""Source-level workplace comparison; does not establish game playability."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sb_mexico.demand_sources import select_sources
from sb_mexico.inegi import load_denue, calibrate_denue_employment, parse_ce2024_municipal
from sb_mexico.pipeline import load_city_config

def compare(path, candidate=False):
    cfg = load_city_config(str(path))
    root = Path(__file__).resolve().parents[1]
    project = root / cfg.get('data_dir', 'data/' + path.stem)
    sources = {k: select_sources(project, root / 'data', k, cfg.get('data_exclusions', [])) for k in ('denue', 'ce')}
    bbox = dict(zip(('min_lon','min_lat','max_lon','max_lat'), cfg['city']['bbox']))
    macro = cfg['macroeconomics']
    ce = macro.get('ce_2024_benchmarks', {})
    if not ce:
        for source in sources['ce']:
            ce = parse_ce2024_municipal(source)
            if ce:
                break
    raw = load_denue(sources['denue'], bbox)
    frame, audit = calibrate_denue_employment(raw, ce, macro.get('til_1_state', .45), macro.get('sample_threshold', 500))
    result = dict(config_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  sources={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources['denue'] + sources['ce']},
                  legacy=dict(establishments=len(frame), jobs=float(frame.calibrated_jobs.sum()),
                              sha256=hashlib.sha256(frame[['cve_mun_clean','lat','lon','calibrated_jobs']].to_csv(index=False,float_format='%.17g').encode()).hexdigest(), audit=audit))
    if candidate:
        from sb_mexico.workplace_employment import load_workplaces
        frame, _, report = load_workplaces(sources['denue'], bbox, {**macro, 'workplace_employment':'ce_bounded'}, ce, sources['ce'])
        result['candidate'] = dict(establishments=len(frame), jobs=float(frame.calibrated_jobs.sum()), report=report)
    return result

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('configs', nargs='+', type=Path)
    a = p.parse_args()
    result = {str(path):compare(path, bool(a.baseline)) for path in a.configs}
    if a.baseline:
        old = json.loads(a.baseline.read_text())
        for key, value in result.items():
            assert all(value[k] == old[key][k] for k in ('config_sha256','sources','legacy')), key
        result['legacy_baseline_unchanged'] = True
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print({k: {'legacy':v['legacy']['jobs'], 'candidate':v.get('candidate',{}).get('jobs')} for k,v in result.items() if isinstance(v,dict)})
