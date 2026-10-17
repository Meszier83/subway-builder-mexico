"""Reproduce preparation and decode real label tiles without editing the input city."""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml
from sb_mexico.place_identity import serialize_places
from sb_mexico.toponymy_delivery import curated_collection
from sb_mexico.toponymy_prepare import prepare_labels


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def compile_existing(collection_path, output, bbox):
    from depot.maps import MapGen
    from sb_mexico.toponymy_tiles import planned_mapgen
    collection = json.loads(Path(collection_path).read_text(encoding='utf-8'))
    root = Path(output).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cls = planned_mapgen(MapGen, collection)
    # Exercise the real add_labels adapter and actual installed command/metadata
    # methods over a tiny base. Geometry/demand compilation is outside this probe.
    instance = object.__new__(cls.func)
    instance._label_collection = collection
    instance.city = 'TST'
    instance.city_dir = str(root)
    instance.maxzoom = collection['layout']['maxzoom']
    instance.bbox = bbox
    instance.verb = False
    import pickle
    payload = root/'adapter.pickle'
    payload.write_bytes(pickle.dumps(instance))
    subprocess.run([sys.executable,'-c',
        'import pickle,sys; p=pickle.load(open(sys.argv[1],"rb")); assert p.city=="TST"; assert p._label_collection["layout"]["engine"]=="label_v1"',
        str(payload)],check=True)
    x, y = (bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2
    source = root/'base.geojson'
    write(source, dict(type='FeatureCollection',features=[dict(type='Feature',
        properties=dict(name='Verification base'),geometry=dict(type='LineString',coordinates=[[x,y],[x+.001,y+.001]]))]))
    instance._run_command(['tippecanoe','-Z','0','-z',str(instance.maxzoom),'-l','roads',
        '--force','-o',str(root/'TST-nolabels.pmtiles'),str(source)])
    instance.add_labels()
    # Decode the label overlay to a real MBTiles file so every tile can be checked.
    mbtiles = root/'decoded-labels.mbtiles'
    instance._run_command(['tile-join','--force','--no-tile-size-limit','-o',str(mbtiles),str(root/'TST-onlylabels.pmtiles')])
    decoded = subprocess.run(['tippecanoe-decode',str(mbtiles)],check=True,capture_output=True,text=True)
    tiles = json.loads(decoded.stdout)
    expected = {f['properties']['id']: f for f in collection['features']}
    seen = {z: set() for z in range(6,instance.maxzoom+1)}
    tile_count = 0
    for tile in tiles['features']:
        z = tile['properties']['zoom']
        layers = tile.get('features', [])
        tile_count += 1
        for layer in layers:
            name = layer['properties']['layer']
            for feature in layer['features']:
                props = feature['properties']; key = props['id']
                if key not in expected: raise AssertionError('Unplanned tile identity: '+key)
                options = expected[key]['tippecanoe']
                if name != options['layer'] or not options['minzoom'] <= z <= options['maxzoom']:
                    raise AssertionError('Wrong layer/zoom: '+key)
                if props['name'] != expected[key]['properties']['name']:
                    raise AssertionError('Changed tile name: '+key)
                if props['priority'] != expected[key]['properties']['priority']:
                    raise AssertionError('Changed tile priority: '+key)
                seen[z].add(key)
    for z, ids in seen.items():
        wanted = {key for key, f in expected.items() if f['tippecanoe']['minzoom'] <= z}
        if ids != wanted: raise AssertionError(f'Zoom {z}: missing {len(wanted-ids)}, extra {len(ids-wanted)}')
    report = dict(status='PASS',adapter='installed depot.maps subclass',synthetic_base=True,worker_serialization=True,
        tile_count=tile_count,unique_labels_by_zoom={str(z):len(ids) for z,ids in seen.items()},
        layers=dict(Counter(f['tippecanoe']['layer'] for f in expected.values())),
        pmtiles_sha256=hashlib.sha256((root/'TST.pmtiles').read_bytes()).hexdigest())
    write(root/'tile-verification.json',report)
    print(json.dumps(report,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city')
    parser.add_argument('--sources-city')
    parser.add_argument('--output',required=True)
    parser.add_argument('--compile-existing')
    parser.add_argument('--bbox',nargs=4,type=float)
    args=parser.parse_args()
    if args.compile_existing:
        return compile_existing(args.compile_existing,args.output,args.bbox)
    config_path=Path(args.city).resolve()
    original_bytes=config_path.read_bytes()
    config=yaml.safe_load(original_bytes.decode('utf-8'))
    if args.sources_city:
        sources=yaml.safe_load(Path(args.sources_city).read_text(encoding='utf-8'))
        config['data_dir']=sources['data_dir']
        # Source choices are explicit; retain the user's original places/policy.
        config['toponymy']=copy.deepcopy(sources.get('toponymy') or dict(engine='official_v1'))
    root=Path(args.output).resolve();root.mkdir(parents=True,exist_ok=True)
    isolated=root/'effective-city.yaml'
    isolated.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')
    prepared=prepare_labels(isolated,progress=print,download=False)
    preserved={p['id']:p for p in prepared['places']}
    for old in serialize_places(config.get('places',[])):
        for key in ('id','name','loc','type'):
            if old.get(key)!=preserved[old['id']].get(key):raise AssertionError('Changed original '+key)
    config.update({key:prepared[key] for key in ('places','toponymy','toponymy_mode','deleted_places')})
    repeated=prepare_labels(isolated,download=False)
    if prepared['layout']!=repeated['layout']:raise AssertionError('Non-deterministic preparation')
    isolated.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False),encoding='utf-8')
    repeated_after=prepare_labels(isolated,download=False)
    if prepared['places']!=repeated_after['places']:raise AssertionError('Rescan changed identities or edits')
    collection=curated_collection(config['places'],config['deleted_places'],config['toponymy_mode'],
                                  config['toponymy'],config['city']['bbox'],config['city'].get('urban_core_polygon'))
    if prepared['layout']['decisions']!=collection['layout']['decisions']:raise AssertionError('Preview/export disagreement')
    if original_bytes!=config_path.read_bytes():raise AssertionError('Original city was edited')
    write(root/'curated_places.geojson',collection);write(root/'layout.json',collection['layout'])
    write(root/'prepared.json',prepared)
    decisions=collection['layout']['decisions']
    report=dict(status='PASS',summary=prepared['summary'],preserved_original=len(config.get('places',[]))-prepared['summary']['added'],
        source_files=[dict(path=s['path'],sha256=s.get('sha256'),status=s['status']) for s in config['toponymy']['review']['sources']],
        by_zoom={str(z):dict(before_density=sum(d['visible'] and d['base_zoom']<=z for d in decisions.values()),
                            prepared=sum(d['visible'] and d['min_zoom']<=z for d in decisions.values())) for z in (6,10,12,14,15)},
        major_labels=[dict(id=p['id'],name=p['name'],population=p.get('population'),visible=decisions[p['id']]['visible'])
                      for p in config['places'] if decisions[p['id']]['scale']=='large'],
        original_sha256=hashlib.sha256(original_bytes).hexdigest(),game_verified=False)
    write(root/'verification.json',report)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
