"""Compare opt-in census employment with a frozen ingestion baseline.

This does not build packages, change city YAML, or establish game playability.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sb_mexico.demand_sources import select_sources
from sb_mexico.inegi import load_denue, load_cpv_demography, resolve_projection_year
from sb_mexico.pipeline import load_city_config
from sb_mexico.population_projection import resolve_population_factors
from sb_mexico.residential import discover_marco_layers


def fingerprint(frame):
    fields = ['cve_mun_clean', 'ageb_clean', 'mza_clean', 'pobtot_adj',
              'pob15_adj', 'growth', 'pea_real', 'lon', 'lat']
    payload = frame[fields].to_csv(index=False, float_format='%.17g').encode()
    return dict(blocks=len(frame), population=float(frame.pobtot_adj.sum()),
                workers=float(frame.pea_real.sum()), sha256=hashlib.sha256(payload).hexdigest())


def compare(config_path, candidates=True):
    cfg = load_city_config(str(config_path))
    root = Path(__file__).resolve().parents[1]
    project = root / cfg.get('data_dir', 'data/' + config_path.stem)
    excluded = cfg.get('data_exclusions', [])
    sources = {kind: select_sources(project, root / 'data', kind, excluded)
               for kind in ('cpv', 'denue', 'conapo', 'marco')}
    bbox = dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), cfg['city']['bbox']))
    macro = cfg['macroeconomics']
    denue = load_denue(sources['denue'], bbox)
    projection = {}
    factors = (resolve_population_factors(sources['conapo'][0], sources['cpv'], macro, projection)
               if sources['conapo'] else {})
    source_paths = set(sources['cpv'] + sources['denue'] + sources['conapo'])
    result = dict(config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                  sources={path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                           for path in sorted(source_paths)}, placement={})
    for placement in ('legacy', 'official_blocks'):
        kwargs = dict(cpv_paths=sources['cpv'], df_denue=denue, bbox=bbox,
                      tasa_pea=macro.get('tasa_pea', .62),
                      growth_factors={**factors, **macro.get('growth_factors', {})},
                      default_growth=macro.get('default_growth_factor', 1.0),
                      placement_mode=placement,
                      marco_paths=(discover_marco_layers(project) if placement == 'official_blocks'
                                   else sources['marco']))
        legacy = load_cpv_demography(**kwargs)
        entry = dict(legacy=fingerprint(legacy))
        if candidates:
            candidate = load_cpv_demography(**kwargs, employment_mode='census_employed',
                                           projection_year=resolve_projection_year(macro))
            geometry = ['lon', 'lat', 'pobtot_adj']
            if not legacy[geometry].reset_index(drop=True).equals(candidate[geometry].reset_index(drop=True)):
                raise ValueError('Employment mode changed population/placement: ' + str(config_path))
            entry.update(candidate=fingerprint(candidate),
                         report=candidate.attrs['residential_employment'],
                         population_and_placement_unchanged=True,
                         worker_delta=float(candidate.pea_real.sum() - legacy.pea_real.sum()))
        result['placement'][placement] = entry
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-only', action='store_true')
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('configs', nargs='+', type=Path)
    args = parser.parse_args()
    result = {str(path): compare(path, not args.baseline_only) for path in args.configs}
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding='utf-8'))
        for name, current in result.items():
            previous = baseline[name]
            if current['config_sha256'] != previous['config_sha256'] or current['sources'] != previous['sources']:
                raise ValueError('Baseline inputs changed: ' + name)
            for placement, entry in current['placement'].items():
                if entry['legacy'] != previous['placement'][placement]['legacy']:
                    raise ValueError('Legacy ingestion changed: ' + name + '/' + placement)
        result['legacy_baseline_unchanged'] = True
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for name, current in result.items():
        if isinstance(current, dict):
            print(name, {mode: {key: value for key, value in entry.items() if key != 'report'}
                         for mode, entry in current['placement'].items()})


if __name__ == '__main__':
    main()
