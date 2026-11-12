"""Behavioral parity against the installed depot on small local fixtures."""
import gzip
import json
import os
from pathlib import Path
import tempfile
import shutil
import pickle
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import unittest
from unittest.mock import patch

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    from depot.maps import MapGen
    from shapely.geometry import Polygon, MultiPolygon, mapping
    from sb_mexico.building_pipeline import prepare
    from sb_mexico.building_index import _convert_to_game_format, create_buildings_index_binary
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'requires WSL cartography dependencies')
class BuildingPipelineTests(unittest.TestCase):
    def engine(self, root):
        e = object.__new__(MapGen)
        e.city_dir = str(root)
        e.city = 'FIX'
        e.verb = False
        e._bbox = [-100.,18.,-98.,21.]
        e.building_index_filter_size = 15.
        e.building_index_simplification = .2
        e.building_tile_simplification = .2
        e.create_building_foundations = False
        return e

    def fixture(self, root):
        a = Polygon([(-99,19),(-98.999,19),(-98.999,19.001),(-99,19.001)])
        b = Polygon([(-99.01,19.01),(-99.009,19.01),(-99.009,19.011),(-99.01,19.011)])
        hole = Polygon([(-99.02,19.02),(-99.018,19.02),(-99.018,19.022),(-99.02,19.022)],
                       [[(-99.0198,19.0202),(-99.0192,19.0202),(-99.0192,19.0208),(-99.0198,19.0208)]])
        rows = [dict(id=str(i), geometry=g.wkb, name='fixture', height=None) for i,g in enumerate((a,MultiPolygon([a,b]),hole))]
        raw=root/'raw.parquet'
        pq.write_table(pa.Table.from_pylist(rows),raw)
        return raw

    def test_exporters_match_legacy(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'SB_LOD_PERIPHERAL_BUILDINGS':'all'}):
            root=Path(tmp); e=self.engine(root)
            prepare(e,self.fixture(root))
            path=str(root/'buildings_cleaned.json')
            e._convert_to_game_format(path)
            e.create_buildings_index_binary(path)
            expected_json=json.loads((root/'buildings_index.json').read_text())
            expected_bin=(root/'buildings_index.bin').read_bytes()
            _convert_to_game_format(e,path)
            create_buildings_index_binary(e,path)
            self.assertEqual(expected_json,json.loads((root/'buildings_index.json').read_text()))
            self.assertEqual(expected_json,json.loads(gzip.decompress((root/'buildings_index.json.gz').read_bytes())))
            self.assertEqual(expected_bin,(root/'buildings_index.bin').read_bytes())
            from tools.check_building_index import validate
            self.assertEqual(validate(root/'buildings_index.bin')['buildings'],3)
            self.assertEqual(validate(root/'buildings_index.bin.gz')['buildings'],3)
            corrupt=bytearray(expected_bin)
            corrupt[-4:]=(3).to_bytes(4,'little')
            (root/'corrupt.bin').write_bytes(corrupt)
            with self.assertRaisesRegex(ValueError,'reference outside'):validate(root/'corrupt.bin')
            self.assertEqual(expected_bin,gzip.decompress((root/'buildings_index.bin.gz').read_bytes()))
            e.create_building_foundations=True
            e._convert_to_game_format(path)
            e.create_buildings_index_binary(path)
            expected_json=json.loads((root/'buildings_index.json').read_text())
            expected_bin=(root/'buildings_index.bin').read_bytes()
            _convert_to_game_format(e,path)
            create_buildings_index_binary(e,path)
            self.assertEqual(expected_json,json.loads((root/'buildings_index.json').read_text()))
            self.assertEqual(expected_bin,(root/'buildings_index.bin').read_bytes())

    def test_global_cap_and_empty(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'SB_LOD_PERIPHERAL_BUILDINGS':'all'}):
            root=Path(tmp);e=self.engine(root);raw=self.fixture(root)
            prepare(e,raw,max_buildings=1)
            features=json.loads((root/'buildings_cleaned.json').read_text())['features']
            self.assertEqual(len(features),1)
            self.assertEqual(features[0]['geometry']['type'],'Polygon')
            e.building_index_filter_size=1e12
            with self.assertRaisesRegex(RuntimeError,'No valid buildings'):
                prepare(e,raw)

    def test_raw_cache_reuse_and_failed_refresh(self):
        from sb_mexico.building_pipeline import acquire
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); e=self.engine(root); source=self.fixture(root)
            e.REFETCH_BUILDINGS=False
            e._get_latest_overture_release=lambda: '2026-10-21.0'
            class LocalConnection:
                def execute(self, sql):
                    if sql.startswith('COPY'):
                        shutil.copyfile(source,root/'bounded-cache/raw.parquet.partial')
                def close(self): pass
            with patch('duckdb.connect', return_value=LocalConnection()) as connect:
                raw=acquire(e)
                first=raw.read_bytes()
                self.assertEqual(acquire(e),raw)
                self.assertEqual(connect.call_count,1)
            manifest=(root/'bounded-cache/raw.json').read_bytes()
            e.REFETCH_BUILDINGS=True
            class BrokenConnection(LocalConnection):
                def execute(self, sql): raise OSError('disk full')
            with patch('duckdb.connect', return_value=BrokenConnection()):
                with self.assertRaisesRegex(OSError,'disk full'): acquire(e)
            self.assertEqual(first,raw.read_bytes())
            self.assertEqual(manifest,(root/'bounded-cache/raw.json').read_bytes())
            pq.write_table(pa.Table.from_pylist([dict(id='changed',geometry=Polygon([(-99,19),(-98.99,19),(-98.99,19.01),(-99,19.01)]).wkb,name=None,height=9.)]),source)
            with patch('duckdb.connect',return_value=LocalConnection()), patch.object(Path,'write_text',side_effect=OSError('manifest disk full')):
                with self.assertRaisesRegex(OSError,'manifest disk full'): acquire(e)
            self.assertEqual(first,raw.read_bytes())
            self.assertEqual(manifest,(root/'bounded-cache/raw.json').read_bytes())

    def test_backend_pickle_identity(self):
        from sb_mexico.cartography_backend import BoundedMapGen, BoundedPlannedMapGen
        for cls in (BoundedMapGen,BoundedPlannedMapGen):
            obj=object.__new__(cls)
            self.assertIs(type(pickle.loads(pickle.dumps(obj))),cls)
            with ProcessPoolExecutor(max_workers=1,mp_context=multiprocessing.get_context('spawn')) as pool:
                self.assertIs(pool.submit(type,obj).result(timeout=45),cls)

    def test_centroid_boundary_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); e=self.engine(root); raw=self.fixture(root)
            core=root/'core.json'
            core.write_text(json.dumps(mapping(Polygon([(-99.0005,18.999),(-98.9995,18.999),(-98.9995,19.002),(-99.0005,19.002)]))))
            with patch.dict(os.environ,{'SB_URBAN_CORE_GEOJSON':str(core),'SB_LOD_PERIPHERAL_BUILDINGS':'none'}):
                prepare(e,raw)
                self.assertEqual(len(json.loads((root/'buildings_cleaned.json').read_text())['features']),1)

    def test_planetiler_budget_and_other_commands(self):
        from sb_mexico.building_pipeline import BoundedBuildingStage
        class Capture:
            def _run_command(self, command, *args, **kwargs): return command
        class Backend(BoundedBuildingStage,Capture): pass
        e=object.__new__(Backend);e.city='FIX';e.planetiler_path='/tools/planetiler.jar'
        cmd=['java','-Xmx16g','-jar',e.planetiler_path,'--force']
        with patch('sb_mexico.building_pipeline.memory_headroom',return_value=6*1024**3):
            actual=e._run_command(cmd)
        self.assertIn('-Xmx3072m',actual)
        self.assertIn('--threads=2',actual)
        self.assertNotIn('-Xmx16g',actual)
        self.assertEqual(e._run_command(['jq','.']),['jq','.'])
        with patch('sb_mexico.building_pipeline.memory_headroom',return_value=512*1024**2):
            with self.assertRaisesRegex(RuntimeError,'headroom'):e._run_command(cmd)

    def test_cap_selects_late_records_across_batches(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'SB_LOD_PERIPHERAL_BUILDINGS':'all'}):
            root=Path(tmp);e=self.engine(root)
            small=Polygon([(-99,19),(-98.9999,19),(-98.9999,19.0001),(-99,19.0001)])
            large=Polygon([(-99,19),(-98.999,19),(-98.999,19.001),(-99,19.001)])
            rows=[dict(id=str(i),geometry=small.wkb,height=4.,name=None) for i in range(1024)]
            rows += [dict(id=f'late-{i}',geometry=large.wkb,height=4.,name=None) for i in range(2)]
            raw=root/'raw.parquet';pq.write_table(pa.Table.from_pylist(rows),raw)
            prepare(e,raw,max_buildings=2)
            features=json.loads((root/'buildings_cleaned.json').read_text())['features']
            self.assertEqual({f['properties']['id'] for f in features},{'late-0','late-1'})

    def test_merida_geometry_index_regression(self):
        import yaml
        from tools.check_building_index import validate
        cfg=yaml.safe_load(Path('cities/merida.yaml').read_text(encoding='utf-8'))['city']
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'SB_LOD_PERIPHERAL_BUILDINGS':'all'}):
            root=Path(tmp);e=self.engine(root);e.city='MID';e._bbox=cfg['bbox']
            w,s,east,n=e.bbox;x=(w+east)/2;y=(s+n)/2
            geom=Polygon([(x,y),(x+.001,y),(x+.001,y+.001),(x,y+.001)])
            raw=root/'raw.parquet';pq.write_table(pa.Table.from_pylist([dict(id='MID-fixture',geometry=geom.wkb,height=4.,name=None)]),raw)
            prepare(e,raw)
            path=str(root/'buildings_cleaned.json');create_buildings_index_binary(e,path)
            result=validate(root/'buildings_index.bin')
            self.assertEqual(result['buildings'],1)
            self.assertAlmostEqual(result['bounds'][0],x,places=5)

    def test_unsupported_backend_requires_explicit_legacy(self):
        from sb_mexico.cartography_backend import mapgen_for_build
        with patch('sb_mexico.cartography_backend.version',return_value='9.0'), patch.dict(os.environ,SB_BUILDINGS_BACKEND='bounded'):
            with self.assertRaisesRegex(RuntimeError,'depot 1.2.7'):mapgen_for_build(MapGen,None)
        with patch.dict(os.environ,SB_BUILDINGS_BACKEND='legacy'):
            self.assertIs(mapgen_for_build(MapGen,None),MapGen)

    def test_windows_backend_and_refresh_are_forwarded_to_wsl(self):
        from sb_mexico.cartography import build_city_map_wsl
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as tmp:
            pbf=Path(tmp)/'source.osm.pbf';pbf.write_bytes(b'fixture')
            for backend in ('bounded','legacy'):
                proc=MagicMock();proc.stdout=None;proc.wait.return_value=0;proc.poll.return_value=0
                with patch.dict(os.environ,SB_BUILDINGS_BACKEND=backend,SB_BUILDINGS_REFRESH='1'), patch('subprocess.Popen',return_value=proc) as spawn:
                    build_city_map_wsl('FIX',[-100,18,-98,21],str(pbf),tmp)
                args=spawn.call_args.args[0]
                self.assertEqual(args[args.index('--buildings-backend')+1],backend)
                self.assertIn('--refresh-buildings',args)


if __name__ == '__main__':
    unittest.main()
