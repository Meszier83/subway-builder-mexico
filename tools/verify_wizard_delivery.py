"""Build isolated city candidates through Wizard and verify HTTP downloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen
from urllib.parse import quote
from unittest.mock import patch
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import wizard


def run(city, final=False):
    report_root = ROOT / 'reports' / 'wizard-fixes'
    if final:
        report_root = report_root / 'final'
    config_dir = report_root / 'cities'
    config_dir.mkdir(parents=True, exist_ok=True)
    config = yaml.safe_load((ROOT / 'cities' / f'{city}.yaml').read_text())
    config['city']['residential_placement'] = 'official_blocks'
    config['data_dir'] = str((ROOT / config.get('data_dir', f'data/{city}')).resolve())
    snapshot = config_dir / f'{city}.yaml'
    snapshot.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    output = report_root / 'packages' / city
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'wizard-build.json').exists():
        raise ValueError('Use fresh verification outputs; this candidate already has a build manifest')
    code = config['city']['code']
    for name in [f'{code}.pmtiles', 'roads.geojson', 'buildings_index.bin.gz',
                 'runways_taxiways.geojson', 'ocean_depth_index.json.gz']:
        source = ROOT / 'dist' / city / name
        if source.exists():
            shutil.copy2(source, output / name)
    with patch.object(wizard, 'DIST_DIR', str(report_root / 'packages')), \
         patch('sb_mexico.pipeline.is_docker_available', return_value=(False, None)):
        wizard.run_pipeline_task(str(snapshot), skip_map=True)
        if wizard.active_build['status'] != 'success':
            raise RuntimeError(wizard.active_build.get('error') or wizard.active_build['status'])
        result = wizard.active_build['result']
        server = ThreadingHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/api/download?file=' + quote(str(snapshot))) as response:
                downloaded = response.read()
            assert hashlib.sha256(downloaded).hexdigest() == result['package_hash']
            status = json.load(urlopen(base + '/api/build/status'))
            assert status['result']['build_id'] == result['build_id']
            preview = json.load(urlopen(base + '/api/demand-preview?file=' + quote(str(snapshot))))
            assert sum(pop['size'] for pop in preview['pops']) == result['commuters']
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    stage = Path(result['output_dir'])
    demand = json.loads((stage / 'demand_data.json').read_text())
    baseline = ROOT / 'reports' / 'demand-roadmap' / 'packages' / city / 'demand_data.json'
    comparison = None
    if baseline.exists():
        old = json.loads(baseline.read_text())
        comparison = dict(old_commuters=sum(pop['size'] for pop in old['pops']),
                          new_commuters=result['commuters'], old_cohorts=len(old['pops']),
                          new_cohorts=len(demand['pops']), old_points=len(old['points']),
                          new_points=len(demand['points']), identical_demand=old == demand)
    report = dict(build=result, bytes=Path(result['package_path']).stat().st_size,
                  points=len(demand['points']), cohorts=len(demand['pops']),
                  http_download_hash_verified=True, http_preview_budget_verified=True,
                  comparison=comparison, game_test='pending user',
                  routing='existing static fallback; no OSRM service started')
    report['code_hashes'] = {source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest()
                            for source in ('sb_mexico/build_delivery.py', 'sb_mexico/pipeline.py',
                                           'sb_mexico/population_projection.py', 'sb_mexico/demand_sources.py',
                                           'sb_mexico/inegi.py', 'sb_mexico/gravity.py',
                                           'tools/wizard.py', 'tools/poi_studio.py', 'tools/templates/wizard.html')}
    (output / 'verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('city')
    parser.add_argument('--final', action='store_true')
    args = parser.parse_args()
    run(args.city, args.final)
