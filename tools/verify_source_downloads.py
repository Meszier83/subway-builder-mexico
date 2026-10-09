"""Explicit live acquisition probe; uses isolated reports, never city data.

python tools/verify_source_downloads.py --live [--include-osm]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sb_mexico.source_downloads import Client, geography, acquire_source, read_manifest, marco_links


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--include-osm', action='store_true')
    parser.add_argument('--preview', action='store_true', help='Exercise production source selection and demand preview with explicit fixture rates')
    args = parser.parse_args()
    root = ROOT / 'reports/source-downloads'
    root.mkdir(parents=True, exist_ok=True)
    last = [0]
    def progress(message):
        if time.monotonic() - last[0] > 5:
            print(message, flush=True)
            last[0] = time.monotonic()
    client = Client(root / 'cache', progress)
    result = {'marco_catalog_states': len(marco_links(client)), 'cases': []}
    cases = [('cancun', [-86.92, 21.10, -86.77, 21.23]),
             ('laguna', [-103.55, 25.48, -103.32, 25.70])]
    for name, bbox in cases:
        geo = geography(client, bbox)
        print(name, json.dumps(geo, ensure_ascii=False), flush=True)
        case = dict(name=name, geography=geo, sources=[])
        for kind in ('denue', 'cpv', 'marco', 'ce', 'enoe', 'conapo'):
            folder = root / name
            try:
                files = acquire_source(client, geo, folder, kind, geo['states'][0]['code'], 2026, 2)
                first = {p: entry['sha256'] for p, entry in read_manifest(folder)['files'].items() if entry['kind'] == kind}
                again = acquire_source(client, geo, folder, kind, geo['states'][0]['code'], 2026, 2)
                second = {p: entry['sha256'] for p, entry in read_manifest(folder)['files'].items() if entry['kind'] == kind}
                assert first == second and files == again
                record = dict(kind=kind, status='ok', files=len(files), bytes=sum(Path(p).stat().st_size for p in files), cache_repeat_equal=True)
            except Exception as error:
                record = dict(kind=kind, status='error', message=str(error))
            case['sources'].append(record)
            print(name, record, flush=True)
        result['cases'].append(case)
        if args.preview:
            from unittest.mock import patch
            import yaml
            from tools import wizard, poi_studio
            config_path = root / (name + '-preview.yaml')
            config_path.write_text(yaml.safe_dump(dict(city=dict(name=name, code=name.upper(), bbox=bbox,
                residential_placement='official_blocks'), data_dir=str(root / name),
                macroeconomics=dict(workplace_employment='auto', residential_employment='census_employed',
                                    model_year=2026, tasa_pea=.62, til_1_state=.45)), allow_unicode=True), encoding='utf-8')
            with patch.object(wizard, 'DATA_DIR', str(root / 'national')):
                status = wizard.inspect_data_files(city_file=str(config_path))
                case['selected_sources'] = {kind: len(status[kind]['files']) for kind in ('denue','cpv','ce2024','enoe','conapo','marco')}
            with patch.object(poi_studio, 'ROOT_DIR', str(root)):
                diagnostics = {}
                points = poi_studio.load_demand_sample(city_file=str(config_path), diagnostics=diagnostics)
                assert points and any(p.get('residents',0) > 0 for p in points) and any(p.get('jobs',0) > 0 for p in points)
                case['preview'] = dict(points=len(points), residents=sum(p['residents'] for p in points),
                                       jobs=sum(p['jobs'] for p in points), diagnostics=diagnostics,
                                       rates='explicit fixture rates; does not attest ENOE parsing')
            print(name, 'production source selection and preview PASS', len(points), flush=True)
    if args.include_osm:
        try:
            files = acquire_source(client, geo, root / 'national', 'osm')
            result['osm'] = dict(status='ok', files=files, bytes=sum(Path(p).stat().st_size for p in files))
        except Exception as error:
            result['osm'] = dict(status='error', message=str(error))
        print('osm', result['osm'], flush=True)
    output_name = 'preview-verification.json' if args.preview else 'verification.json'
    (root / output_name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if any(s['status'] != 'ok' for case in result['cases'] for s in case['sources']) or result.get('osm', {}).get('status') == 'error':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
