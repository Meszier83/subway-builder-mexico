"""Build isolated candidate packages using existing cartography and static checks.

OSRM services are not started. Game import/simulation/save/reload remain separate.
"""
import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import yaml
from sb_mexico.pipeline import execute_pipeline, load_city_config, validate_cohort_spatial_integrity
from sb_mexico.gravity import assign_zones, is_point_in_exclusion_zone
from sb_mexico.special_demand import validate_special_demand_points


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()


def run(config, output):
    config = config.resolve()
    output = output.resolve()
    if not output.is_relative_to(ROOT/'reports'/'demand-roadmap'):
        raise ValueError('Candidate packages must be isolated under reports/demand-roadmap')
    cfg = load_city_config(str(config))
    code = cfg['city']['code']
    original = ROOT/'dist'/config.stem
    original_hashes = {str(p):sha(p) for p in original.iterdir() if p.is_file()}
    output.mkdir(parents=True, exist_ok=True)
    if (output/f'{code}.zip').exists():
        raise ValueError('Use a fresh package output directory; an old ZIP is not a new delivery')
    assets = [f'{code}.pmtiles','roads.geojson','buildings_index.bin.gz',
              'runways_taxiways.geojson','ocean_depth_index.json.gz']
    for name in assets:
        if (original/name).exists():
            shutil.copy2(original/name, output/name)
    cfg['city']['residential_placement'] = 'official_blocks'
    cfg['data_dir'] = str((ROOT/cfg.get('data_dir',f'data/{config.stem}')).resolve())
    effective = output/'effective-city.yaml'
    effective.write_text(yaml.safe_dump(cfg,allow_unicode=True),encoding='utf-8')
    with patch('sb_mexico.pipeline.is_docker_available', return_value=(False,None)):
        execute_pipeline(str(effective),skip_map=True,output_dir=str(output),include_driving_path=False)
    demand = json.loads((output/'demand_data.json').read_text())
    report = json.loads((output/'demand_pipeline_report.json').read_text())
    points, pops = demand['points'], demand['pops']
    by_id = {p['id']:p for p in points}
    assert len(by_id) == len(points)
    assert len({p['id'] for p in pops}) == len(pops)
    assert sum(p['size'] for p in pops) == report['commuters']
    membership = {key:set() for key in by_id}
    for point in points:
        assert set(point) == {'id','location','residents','jobs','popIds'}
        lon, lat = point['location']
        assert np.isfinite([lon, lat]).all() and -180 <= lon <= 180 and -90 <= lat <= 90
        assert not is_point_in_exclusion_zone(lon,lat,cfg.get('exclusion_zones'))
    for pop in pops:
        assert isinstance(pop['size'],int) and 0 < pop['size'] <= cfg['macroeconomics'].get('max_pop_size',200)
        assert 'drivingPath' not in pop
        for key in ('residenceId','jobId'):
            assert pop[key] in by_id
            membership[pop[key]].add(pop['id'])
    for key, expected in membership.items():
        assert set(by_id[key]['popIds']) == expected
    validate_cohort_spatial_integrity(pops,points)
    zones = dict(zip(by_id, map(int,assign_zones(np.array([p['location'] for p in points]),cfg.get('isolated_zones')))))
    assert all(zones[p['residenceId']] == zones[p['jobId']] for p in pops)
    assert {p['id'] for p in cfg.get('pois',[])} <= set(by_id)
    special = output/'special_demand_points.json'
    if special.exists():
        valid, errors = validate_special_demand_points(json.loads(special.read_text()))
        assert valid, errors
    with zipfile.ZipFile(output/f'{code}.zip') as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert {'config.json','demand_data.json',f'{code}.pmtiles','roads.geojson'} <= set(names)
        for name in names:
            assert '/' not in name and '\\' not in name
            assert hashlib.sha256(archive.read(name)).hexdigest() == sha(output/name)
    assert all(sha(Path(p)) == digest for p,digest in original_hashes.items())
    result = dict(static_package_checks='passed', city=code, points=len(points),cohorts=len(pops),
                  commuters=report['commuters'], zip_sha256=sha(output/f'{code}.zip'),
                  zip_bytes=(output/f'{code}.zip').stat().st_size,
                  existing_dist_unchanged=True, reused_cartography={name:sha(output/name) for name in assets if (output/name).exists()},
                  export='Inspect build log for depot sanitizer success or native fallback',
                  limits=['Existing cartography reused; freshness against current OSM not established',
                          'Canonical driving fallback used; live OSRM not exercised',
                          'Game loading, performance, save/reload not observed'])
    (output/'package-validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    run(args.config,args.output)
