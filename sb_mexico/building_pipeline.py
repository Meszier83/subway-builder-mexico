"""Disk-backed Overture building acquisition and geometry preparation."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from collections import Counter
import gc
import sys

from .building_storage import write_features


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def memory_headroom():
    import psutil
    available = psutil.virtual_memory().available
    try:
        limit = int(Path('/sys/fs/cgroup/memory.max').read_text())
        used = int(Path('/sys/fs/cgroup/memory.current').read_text())
        available = min(available, limit-used)
    except (OSError, ValueError):
        pass
    return available


def release_building_buffers():
    """Return unused native buffers before the tile subprocesses start."""
    gc.collect()
    import pyarrow
    pyarrow.default_memory_pool().release_unused()
    if sys.platform.startswith('linux'):
        import ctypes
        try:
            trim = ctypes.CDLL(None).malloc_trim
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
        except (AttributeError, OSError):
            pass  # Non-glibc Linux; the bounded stages still apply.


def acquire(engine):
    import duckdb
    import pyarrow.parquet as pq
    root = Path(getattr(engine, '_bounded_cache_root', Path(engine.city_dir) / 'bounded-cache'))
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / 'raw.json'
    raw = root / 'raw.parquet'
    if manifest.exists() and not engine.REFETCH_BUILDINGS:
        info = json.loads(manifest.read_text())
        filename = info.get('file', 'raw.parquet')
        if Path(filename).name != filename:
            raise RuntimeError('Invalid raw building cache filename')
        raw = root / filename
        if raw.is_file() and info.get('version') == 1 and info['bbox'] == list(engine.bbox) and info['sha256'] == digest(raw):
            print('[Buildings] Reusing validated raw Parquet', flush=True)
            return raw
    release = engine._get_latest_overture_release()
    if not isinstance(release, str) or not release or any(c not in '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-_' for c in release):
        raise ValueError('Invalid Overture release tag')
    available = memory_headroom()
    if available < 1536 * 1024**2:
        raise RuntimeError('Insufficient memory headroom for building acquisition (need 1.5 GiB)')
    budget = min(2048, int(available / 1024**2 * .35))
    free = shutil.disk_usage(root).free
    if free < 1024**3:
        raise RuntimeError('Insufficient free disk for building acquisition (need at least 1 GiB)')
    w,s,e,n = map(float, engine.bbox)
    source = f's3://overturemaps-us-west-2/release/{release}/theme=buildings/type=building/*'
    query = f"SELECT id, geometry, names.primary AS name, height FROM read_parquet('{source}', hive_partitioning=1) WHERE bbox.xmin >= {w} AND bbox.xmax <= {e} AND bbox.ymin >= {s} AND bbox.ymax <= {n}"
    partial = root / 'raw.parquet.partial'
    partial.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(dir=root, prefix='spill-') as spill:
        con = duckdb.connect(config={'memory_limit': f'{budget}MiB', 'threads': '2', 'temp_directory': spill,
                                     'max_temp_directory_size': f'{max(512, int(free/1024**2)-512)}MiB'})
        try:
            print(f'[Buildings] Acquiring release {release}; DuckDB {budget} MiB, 2 threads', flush=True)
            con.execute('INSTALL spatial; LOAD spatial;')
            dest = str(partial).replace("'", "''")
            con.execute(f"COPY ({query}) TO '{dest}' (FORMAT PARQUET, ROW_GROUP_SIZE 25000)")
        finally:
            con.close()
    metadata = pq.ParquetFile(partial)
    if not {'id', 'geometry', 'name', 'height'}.issubset(metadata.schema_arrow.names):
        raise RuntimeError('Acquired building Parquet has an incompatible schema')
    info = dict(version=1, release=release, bbox=list(engine.bbox), rows=metadata.metadata.num_rows, sha256=digest(partial))
    raw = root / f'raw-{info["sha256"]}.parquet'
    info['file'] = raw.name
    os.replace(partial, raw)
    staged = manifest.with_suffix('.partial')
    staged.write_text(json.dumps(info), encoding='utf-8')
    os.replace(staged, manifest)
    return raw


def prepare(engine, raw, max_buildings=850000):
    import pyarrow.parquet as pq
    import shapely
    from shapely.geometry import shape, mapping
    core = None
    core_path = os.environ.get('SB_URBAN_CORE_GEOJSON')
    core_data = getattr(engine, '_bounded_core', None)
    if core_data is not None or core_path:
        data = core_data if core_data is not None else json.loads(Path(core_path).read_text(encoding='utf-8'))
        if isinstance(data, list):
            data = dict(type='Polygon', coordinates=[data])
        if data['type'] == 'FeatureCollection':
            core = shapely.union_all([shape(f['geometry']) for f in data['features']])
        else:
            core = shape(data.get('geometry', data))
        if not core.is_valid:
            core = core.buffer(0)
    mode = getattr(engine, '_bounded_lod', os.environ.get('SB_LOD_PERIPHERAL_BUILDINGS', 'none'))
    if mode not in ('none', 'large_only', 'all'):
        raise ValueError('Invalid peripheral building mode')
    lat = (engine.bbox[1]+engine.bbox[3])/2
    factor = 111320 * math.cos(math.radians(lat)) * 110574
    root = Path(engine.city_dir)
    with tempfile.TemporaryDirectory(dir=root, prefix='selection-') as tmp:
        con = sqlite3.connect(str(Path(tmp)/'selected.sqlite'))
        try:
            con.execute('PRAGMA cache_size=-8192')
            con.execute('PRAGMA temp_store=FILE')
            con.execute('CREATE TABLE buildings(seq INTEGER PRIMARY KEY, id TEXT, area REAL, wkb BLOB, height REAL, name TEXT)')
            count = total = 0
            rejected = Counter()
            print('[Buildings] Converting and filtering WKB in 1024-row batches', flush=True)
            for batch in pq.ParquetFile(raw).iter_batches(batch_size=1024):
                for row in batch.to_pylist():
                    total += 1
                    blob = row['geometry']
                    if blob is None:
                        rejected['missing_geometry'] += 1
                        continue
                    if len(blob) > 32*1024**2:
                        raise RuntimeError(f'Building {row["id"]} exceeds WKB memory budget')
                    geom = shapely.from_wkb(bytes(blob))
                    area = geom.area * factor
                    if geom.is_empty or not geom.is_valid:
                        rejected['empty_or_invalid_geometry'] += 1
                        continue
                    if area <= engine.building_index_filter_size:
                        rejected['area'] += 1
                        continue
                    if core is not None and not core.is_empty and mode != 'all':
                        if not core.intersects(geom.centroid) and not (mode == 'large_only' and area > 1000):
                            rejected['outside_core'] += 1
                            continue
                    try:
                        height = float(row['height'])
                        if not math.isfinite(height): height = 4.0
                    except (ValueError, TypeError):
                        height = 4.0
                    con.execute('INSERT INTO buildings VALUES (?,?,?,?,?,?)', (count,row['id'],area,bytes(blob),height,row['name']))
                    count += 1
                con.commit()
                if total % 25600 < 1024:
                    print(f'[Buildings] Scanned {total:,}; retained {count:,}', flush=True)
            if not count:
                raise RuntimeError('No valid buildings after filtering; refusing to reuse stale indexes')
            con.execute('CREATE INDEX priority ON buildings(area DESC, seq)')
            order = 'area DESC, seq' if count > max_buildings else 'seq'
            cutoff = None
            cutoff_ties = cutoff_selected = 0
            if count > max_buildings:
                print('[Buildings] Global area cap; equal-area ties use raw-cache scan order', flush=True)
                cutoff = con.execute('SELECT area FROM buildings ORDER BY area DESC,seq LIMIT 1 OFFSET ?', (max_buildings-1,)).fetchone()[0]
                cutoff_ties = con.execute('SELECT COUNT(*) FROM buildings WHERE area=?',(cutoff,)).fetchone()[0]
                cutoff_selected = max_buildings-con.execute('SELECT COUNT(*) FROM buildings WHERE area>?',(cutoff,)).fetchone()[0]
            def features(tolerance, foundations=False):
                for source_id, blob, height, name in con.execute(f'SELECT id,wkb,height,name FROM buildings ORDER BY {order} LIMIT ?', (max_buildings,)):
                    geom = shapely.simplify(shapely.from_wkb(blob), tolerance=tolerance/(111000*math.cos(math.radians(lat))), preserve_topology=True)
                    props = dict(id=source_id, height=height, name=name)
                    if foundations and geom.geom_type == 'Polygon':
                        props.pop('name', None)
                        props['foundationDepth'] = engine._calculate_building_foundation(list(geom.exterior.coords),height)
                    yield dict(type='Feature', geometry=mapping(geom), properties=props)
            for name, tolerance, foundations in (
                ('buildings_cleaned.json',engine.building_index_simplification,False),
                ('buildings_zoom.geojson',engine.building_tile_simplification,False),
                ('buildings_foundations.geojson',engine.building_tile_simplification,True)):
                dst=root/name
                print(f'[Buildings] Writing {name}', flush=True)
                partial=dst.with_suffix(dst.suffix+'.partial')
                write_features(partial, features(tolerance,foundations))
                os.replace(partial,dst)
            print(f'[Buildings] Exported {min(count,max_buildings):,}; cap exclusions {max(0,count-max_buildings):,}',flush=True)
            summary = dict(raw=total, retained_before_cap=count, exported=min(count,max_buildings),
                           cap_exclusions=max(0,count-max_buildings), rejected=dict(rejected),
                           cutoff_area_m2=cutoff,cutoff_ties=cutoff_ties,cutoff_selected=cutoff_selected)
            (root/'buildings-selection.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        finally:
            con.close()


class BoundedBuildingStage:
    def __init__(self, *args, **kwargs):
        self._bounded_core = kwargs.pop('bounded_core', None)
        self._bounded_lod = kwargs.pop('bounded_lod', os.environ.get('SB_LOD_PERIPHERAL_BUILDINGS', 'none'))
        super().__init__(*args, **kwargs)
        self._bounded_cache_root = Path.home() / f'build_{self.city.lower()}' / 'bounded-cache'

    def _run_command(self, command, *args, **kwargs):
        # depot 1.2.7 hardcodes -Xmx16g independently of MapGen.RAM.
        if isinstance(command, list) and command and command[0] == 'java' and self.planetiler_path in command:
            available = memory_headroom()
            if available < 1536 * 1024**2:
                raise RuntimeError('Insufficient memory headroom to start Planetiler (need 1.5 GiB)')
            heap = max(512, min(3072, int(available / 1024**2 * .5)))
            command = [f'-Xmx{heap}m' if str(arg).startswith('-Xmx') else arg for arg in command]
            command += ['--threads=2']
            sources = Path.home() / f'build_{self.city.lower()}' / 'data' / 'sources'
            for flag, filename in (('water-polygons', 'water-polygons-split-3857.zip'),
                                   ('natural-earth', 'natural_earth_vector.sqlite.zip'),
                                   ('lake-centerlines', 'lake_centerline.shp.zip')):
                path = sources / filename
                if path.is_file():
                    command.append(f'--{flag}-path={path}')
            print(f'[Buildings] Planetiler heap {heap} MiB; 2 threads', flush=True)
        return super()._run_command(command, *args, **kwargs)

    def process_buildings(self):
        from .building_index import _convert_to_game_format, create_buildings_index_binary
        root = Path(self.city_dir)
        self._bounded_cache_root.mkdir(parents=True, exist_ok=True)
        lock = self._bounded_cache_root / '.bounded-build.lock'
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError(f'Building writer lock exists: {lock}; verify no build is running before removing it') from None
        try:
            os.write(fd, str(os.getpid()).encode('ascii'))
            os.close(fd)
            prepare(self, acquire(self))
            self.buildings_geojson = str(root/'buildings_cleaned.json')
            self.buildings_zoom_geojson = str(root/'buildings_zoom.geojson')
            _convert_to_game_format(self,self.buildings_geojson)
            create_buildings_index_binary(self,self.buildings_geojson)
            outputs = ('buildings_cleaned.json', 'buildings_zoom.geojson', 'buildings_foundations.geojson',
                       'buildings_index.json', 'buildings_index.json.gz', 'buildings_index.bin', 'buildings_index.bin.gz')
            info = dict(version=1, backend='bounded', bbox=list(self.bbox),
                        core_sha256=digest(os.environ['SB_URBAN_CORE_GEOJSON']) if os.environ.get('SB_URBAN_CORE_GEOJSON') else None,
                        lod=os.environ.get('SB_LOD_PERIPHERAL_BUILDINGS', 'none'),
                        tie_policy='area descending, raw-cache scan order',
                        artifacts={name:dict(bytes=(root/name).stat().st_size, sha256=digest(root/name)) for name in outputs})
            info['source'] = json.loads((self._bounded_cache_root/'raw.json').read_text(encoding='utf-8'))
            info['options'] = {name:getattr(self,name) for name in (
                'building_index_filter_size','building_index_simplification','building_tile_simplification',
                'create_building_foundations','max_building_tile_size')}
            if self._bounded_core is not None:
                info['core_sha256'] = hashlib.sha256(json.dumps(self._bounded_core,sort_keys=True).encode('utf-8')).hexdigest()
            staged=root/'buildings-manifest.json.partial'
            staged.write_text(json.dumps(info,indent=2),encoding='utf-8')
            os.replace(staged,root/'buildings-manifest.json')
        finally:
            lock.unlink(missing_ok=True)
            release_building_buffers()

    def _create_building_foundation_files(self, alpha=.25, default_height=4.0):
        self.buildings_foundations_geojson = str(Path(self.city_dir)/'buildings_foundations.geojson')
        self.buildings_foundations_mbtiles = str(Path(self.city_dir)/'buildings_foundations.mbtiles')
        params = ['--no-tile-size-limit'] if self.max_building_tile_size is None else ['--drop-smallest-as-needed',f'--maximum-tile-bytes={self.max_building_tile_size}']
        self._run_command(['tippecanoe','-o',self.buildings_foundations_mbtiles,'--layer=foundations','--include=foundationDepth',*params,'-Z12',f'-z{self.maxzoom}',self.buildings_foundations_geojson,'--force'])
