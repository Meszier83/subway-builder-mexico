"""Verify Phase 3 against saved production-source frames on both fixture cities."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd

from sb_mexico import inegi
from sb_mexico.residential import discover_marco_layers


def run():
    baseline = Path('reports/demand-roadmap/baseline')
    spec = importlib.util.spec_from_file_location('ingestion_baseline', baseline/'inegi.py')
    before = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(before)
    rows = []
    for city in ['cancun_riviera_maya', 'merida']:
        report = json.loads((baseline/city/'official_blocks_report.json').read_text())
        cfg = report['config']
        bbox = dict(zip(['min_lon','min_lat','max_lon','max_lat'], cfg['city']['bbox']))
        inputs = [Path(p) for p in report['inputs']]
        denue = [str(p) for p in inputs if p.suffix == '.csv' and 'denue' in p.name.lower()]
        cpv = [str(p) for p in inputs if p.suffix in ('.csv','.xlsx') and
               any(s in p.name.lower() for s in ('resageburb','censo','cpv'))]
        a, b = before.load_denue(denue, bbox), inegi.load_denue(denue, bbox)
        pd.testing.assert_frame_equal(a, b)
        assert a.attrs['mun_totals_global'] == b.attrs['mun_totals_global']
        for mode in ['legacy', 'official_blocks']:
            kw = dict(cpv_paths=cpv, df_denue=b, bbox=bbox,
                      tasa_pea=cfg['macroeconomics']['tasa_pea'],
                      growth_factors=cfg['macroeconomics'].get('growth_factors'),
                      default_growth=cfg['macroeconomics'].get('default_growth_factor',1.),
                      placement_mode=mode,
                      marco_paths=discover_marco_layers(Path(cfg.get('data_dir',f'data/{city}')))
                      if mode == 'official_blocks' else [])
            old = before.load_cpv_demography(**kw)
            new = inegi.load_cpv_demography(**kw)
            pd.testing.assert_frame_equal(old, new)
            rows.append(dict(city=city, mode=mode, census_rows=len(new),
                             population=float(new.pobtot_adj.sum()), pea=float(new.pea_real.sum()),
                             source_frames_equal=True, denue_identity=b.attrs['source_identity'],
                             census_identity=new.attrs.get('source_identity')))
    output = Path('reports/demand-roadmap/phase3')
    output.mkdir(parents=True, exist_ok=True)
    (output/'source-check.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(json.dumps(rows, indent=2))


if __name__ == '__main__':
    run()
