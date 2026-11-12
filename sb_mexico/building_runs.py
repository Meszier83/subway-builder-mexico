"""Own artifacts by run; reuse a base for labels only when geometry matches."""
import hashlib
import json
import os
from pathlib import Path
import uuid
from functools import wraps


def file_identity(path):
    digest=hashlib.sha256()
    with open(path,'rb') as src:
        for block in iter(lambda:src.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def exclusive_city_run(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if os.name == 'nt' or os.environ.get('SB_BUILDINGS_BACKEND','bounded') != 'bounded':
            return function(*args, **kwargs)
        city = kwargs.get('city_code', args[0] if args else None)
        if not city or not str(city).isalnum():
            raise ValueError('Invalid city code for cartography lock')
        root = Path.home() / f'build_{city.lower()}'
        root.mkdir(parents=True,exist_ok=True)
        lock = root / '.cartography.lock'
        try:
            fd = os.open(lock,os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f'Cartography writer lock exists: {lock}; verify its build has stopped before removing it') from None
        try:
            os.write(fd,str(os.getpid()).encode('ascii'))
            os.close(fd)
            return function(*args,**kwargs)
        finally:
            lock.unlink(missing_ok=True)
    return wrapped


def select_run(base, labels_only, settings):
    base = Path(base).resolve()
    identity = dict(settings)
    source = identity.pop('osm_source',None)
    if source:
        identity['osm_sha256'] = file_identity(source)
    identity['backend_code'] = {name:file_identity(Path(__file__).with_name(name)) for name in
                                ('building_pipeline.py','building_index.py','building_storage.py')}
    core_path = identity.pop('urban_core_geojson', None)
    if core_path:
        identity['core_contents'] = Path(core_path).read_text(encoding='utf-8')
    fingerprint = hashlib.sha256(json.dumps(identity,sort_keys=True).encode('utf-8')).hexdigest()
    pointer = base / 'last-building-run.json'
    if labels_only and pointer.is_file() and os.environ.get('SB_BUILDINGS_REFRESH') != '1':
        previous = json.loads(pointer.read_text(encoding='utf-8'))
        run = Path(previous['run']).resolve()
        if run.is_dir() and run.parent == base / 'runs' and previous['fingerprint'] == fingerprint:
            return str(run), fingerprint
    return str(base / 'runs' / uuid.uuid4().hex), fingerprint


def record_run(base, run, fingerprint):
    base = Path(base).resolve()
    run = Path(run).resolve()
    if run.parent != base / 'runs':
        raise ValueError('Building run is outside its city run directory')
    pointer = base / 'last-building-run.json'
    partial = pointer.with_suffix(f'.{uuid.uuid4().hex}.partial')
    partial.write_text(json.dumps(dict(run=str(run),fingerprint=fingerprint)),encoding='utf-8')
    os.replace(partial,pointer)


def reuse_reference_sources(base, run):
    """Share existing immutable Planetiler inputs, while keeping temporary data per run."""
    base,run=Path(base),Path(run)
    for relative in ('data/sources/water-polygons-split-3857.zip',
                     'data/sources/natural_earth_vector.sqlite.zip',
                     'data/sources/lake_centerline.shp.zip','data/tile_weights.tsv.gz'):
        source=base/relative
        target=run/relative
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            target.symlink_to(source.resolve())
