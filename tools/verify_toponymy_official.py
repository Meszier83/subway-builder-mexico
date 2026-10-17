"""Reproduce official-source evidence without changing a city's edited names."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
from sb_mexico.toponymy_official import acquire_catalogs, acquire_dcah, scan_official_catalog
from sb_mexico.toponymy_delivery import curated_collection
from sb_mexico.source_downloads import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--city', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.city).read_text(encoding='utf-8'))
    output = Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    isolated = dict(city=config['city'], data_dir=str(output/'data'),places=[],
                    toponymy=dict(engine='official_v1',osm_fallback=False),toponymy_mode='replace')
    fixture = output/'effective-city.yaml'
    fixture.write_text(yaml.safe_dump(isolated,allow_unicode=True),encoding='utf-8')
    if args.download:
        atomic_json(output/'acquisition-localities.json',acquire_catalogs(str(fixture),progress=lambda m:print(m,flush=True)))
        atomic_json(output/'acquisition-dcah.json',acquire_dcah(str(fixture),progress=lambda m:print(m,flush=True)))
    catalog = scan_official_catalog(str(fixture),progress=lambda m:print(m,flush=True))
    atomic_json(output/'catalog.json',catalog)
    atomic_json(output/'labels.geojson',curated_collection(catalog['places'],mode='replace',pipeline=isolated['toponymy']))
    print(json.dumps(dict(total=catalog['total'],partial=catalog['partial'],warnings=catalog['warnings'],
                         sources=[dict(kind=s['kind'],labels=s.get('labels'),status=s['status']) for s in catalog['sources']],
                         unlocated=len(catalog['unlocated']),fingerprint=catalog['fingerprint']),ensure_ascii=False),flush=True)


if __name__ == '__main__': main()
