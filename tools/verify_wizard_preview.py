"""Compare real-city previews at the census-placement stage in both modes."""
import json
import math
from pathlib import Path
import sys
import time
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import poi_studio
from sb_mexico.demand_sources import select_sources
from sb_mexico.inegi import load_denue, load_cpv_demography
from sb_mexico.population_projection import resolve_population_factors
from sb_mexico.residential import discover_marco_layers
from sb_mexico.gravity import is_point_in_exclusion_zone, prepare_polygon_geom, is_point_in_prepared_polygon
from sb_mexico.build_delivery import validate_package, file_hash, resolve_download


def verify(city):
    report_root = ROOT / 'reports/wizard-fixes'
    config_path = report_root / 'cities' / f'{city}.yaml'
    config = yaml.safe_load(config_path.read_text())
    data = Path(config['data_dir'])
    macro = config.get('macroeconomics', {})
    bbox = config['city']['bbox']
    box = dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), bbox))
    excluded = config.get('data_exclusions', [])
    sources = {kind: select_sources(data, ROOT / 'data', kind, excluded)
               for kind in ('denue', 'cpv', 'conapo', 'marco')}
    denue = load_denue(sources['denue'], box)
    projection_report = {}
    factors = resolve_population_factors(sources['conapo'][0], sources['cpv'], macro, projection_report) if sources['conapo'] else None
    rows = []
    for mode in ('legacy', 'official_blocks'):
        effective = dict(config, city=dict(config['city'], residential_placement=mode))
        temporary = report_root / 'cities' / f'{city}-{mode}-preview.yaml'
        temporary.write_text(yaml.safe_dump(effective, allow_unicode=True), encoding='utf-8')
        expected = load_cpv_demography(sources['cpv'], denue, box,
                                      tasa_pea=macro.get('tasa_pea', .62),
                                      growth_factors={**(factors or {}), **macro.get('growth_factors', {})},
                                      conapo_projections=None,
                                      default_growth=macro.get('default_growth_factor', 1.0),
                                      marco_paths=discover_marco_layers(data) if mode == 'official_blocks' else sources['marco'],
                                      placement_mode=mode)
        core = config['city'].get('urban_core_polygon') or config.get('urban_core_polygon')
        prep = prepare_polygon_geom(core) if core and config['city'].get('restrict_demand_to_urban_core', True) else None
        masses = {}
        for row in expected.itertuples():
            if is_point_in_exclusion_zone(row.lon, row.lat, config.get('exclusion_zones')):
                continue
            if prep and not is_point_in_prepared_polygon(row.lon, row.lat, prep):
                continue
            key = (float(row.lon), float(row.lat))
            masses[key] = masses.get(key, 0.) + float(row.pobtot_adj)
        diagnostics = {}
        start = time.perf_counter()
        actual = poi_studio.load_demand_sample(city_file=str(temporary), diagnostics=diagnostics)
        elapsed = time.perf_counter() - start
        measured = {}
        for point in actual:
            if point['residents']:
                key = tuple(point['location'])
                measured[key] = measured.get(key, 0.) + point['residents']
        assert measured.keys() == masses.keys(), (city, mode, 'coordinates differ')
        assert all(math.isclose(measured[key], value, rel_tol=1e-12, abs_tol=1e-7)
                   for key, value in masses.items()), (city, mode, 'population differs')
        # A repeat must use the same config/source/geometry identity and retain diagnostics.
        cached_diagnostics = {}
        start = time.perf_counter()
        cached = poi_studio.load_demand_sample(city_file=str(temporary), diagnostics=cached_diagnostics)
        cached_elapsed = time.perf_counter() - start
        assert cached == actual and cached_diagnostics == diagnostics
        rows.append(dict(mode=mode, residential_locations=len(measured),
                         residents=sum(measured.values()), preview_seconds=elapsed,
                         cached_seconds=cached_elapsed, matching_stage_parity=True,
                         coverage=diagnostics.get('residential_placement')))
    output = report_root / 'packages' / city
    package, result = resolve_download(config_path, output)
    assert validate_package(package, config['city']['code']) == result['commuters']
    report = dict(city=city, previews=rows, final_package_validation=True,
                  package_hash=file_hash(package), projection=projection_report)
    (output / 'preview-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(dict(city=city, modes=[{key: value for key, value in row.items() if key != 'coverage'}
                                          for row in rows], final_package_validation=True), indent=2))


if __name__ == '__main__':
    verify(sys.argv[1])
