"""Contracts for opt-in residential geography, fallback identity and accounting."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon, box

from sb_mexico.inegi import load_cpv_demography, load_marco_geoestadistico_coords
from sb_mexico.pipeline import load_city_config
from sb_mexico.residential import discover_marco_layers, load_official_layers, grid_accounting
from tools.wizard import save_full_city_data
import yaml


class TestResidentialPlacement(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bbox = dict(min_lon=-87, max_lon=-86, min_lat=20, max_lat=22)

    def layer(self, polygons=None, localities=None, municipalities=None, crs=4326):
        polygons = polygons or [box(-86.86, 21.14, -86.84, 21.16)]
        n = len(polygons)
        localities = localities or ['0001'] * n
        municipalities = municipalities or ['005'] * n
        return gpd.GeoDataFrame(dict(CVE_ENT=['23'] * n, CVE_MUN=municipalities,
                                     CVE_LOC=localities, CVE_AGEB=['0001'] * n,
                                     CVE_MZA=['001'] * n), geometry=polygons, crs=crs)

    def cpv(self, rows=None, **kwargs):
        rows = rows or [dict(ENTIDAD='23', MUN='005', LOC='0001', AGEB='0001', MZA='001',
                            POBTOT=100, P_15YMAS=80)]
        path = self.root / 'cpv.csv'
        pd.DataFrame(rows).to_csv(path, index=False)
        denue = kwargs.pop('df_denue', pd.DataFrame())
        return load_cpv_demography(str(path), denue, self.bbox, .65,
                                   placement_mode='official_blocks', **kwargs)

    def denue(self, locality='0001', mza='1'):
        return pd.DataFrame([dict(cve_mun_clean='23005', cve_loc=locality,
                                 ageb_clean='0001', mza_clean=mza,
                                 lon=-86.80, lat=21.10, calibrated_jobs=10)])

    def test_actual_schema_projected_layer_and_interior_point(self):
        # Concave U: its centroid lies in the gap, outside the block.
        polygon = Polygon([(-86.86,21.14),(-86.82,21.14),(-86.82,21.18),
                           (-86.83,21.18),(-86.83,21.15),(-86.85,21.15),
                           (-86.85,21.18),(-86.86,21.18)])
        self.assertFalse(polygon.covers(polygon.centroid))
        path = self.root / '23m.shp'
        self.layer([polygon]).to_crs(32616).to_file(path)
        blocks, _ = load_marco_geoestadistico_coords(str(path), 'official_blocks')
        from shapely.geometry import Point
        self.assertTrue(polygon.covers(Point(blocks.iloc[0].lon_geo, blocks.iloc[0].lat_geo)))
        placed = self.cpv(marco_paths=str(path), df_denue=self.denue())
        self.assertEqual(placed.placement_source.tolist(), ['official_block'])
        self.assertEqual(placed.pobtot_adj.sum(), 100)
        self.assertEqual(placed.pea_real.sum(), 52)

    def test_same_block_code_in_two_localities_and_missing_locality(self):
        frame = self.layer([box(-86.86,21.14,-86.84,21.16), box(-86.66,21.14,-86.64,21.16)], ['0001','0002'])
        rows = [dict(ENTIDAD=23,MUN=5,LOC=loc,AGEB='0001',MZA=1,POBTOT=100,P_15YMAS=80)
                for loc in ['0001','0002']]
        with patch('geopandas.read_file', return_value=frame):
            placed = self.cpv(rows, marco_paths=['test.shp'])
            self.assertEqual(len(placed), 2)
            self.assertGreater(abs(placed.lon.iloc[0]-placed.lon.iloc[1]), .19)
            rows[0].pop('LOC'); rows[1].pop('LOC')
            unknown = self.cpv(rows, marco_paths=['test.shp'])
            self.assertTrue(unknown.empty)
            self.assertEqual(unknown.attrs['residential_placement']['unlocated']['population'], 200)

    def test_unique_abbreviated_key_is_supported(self):
        row = dict(ENTIDAD=23,MUN=5,AGEB='0001',MZA=1,POBTOT=100,P_15YMAS=80)
        with patch('geopandas.read_file', return_value=self.layer()):
            placed = self.cpv([row], marco_paths=['test.shp'])
        self.assertEqual(len(placed), 1)

    def test_known_locality_never_matches_another_locality(self):
        with patch('geopandas.read_file', return_value=self.layer(localities=['0002'])):
            placed = self.cpv(marco_paths=['test.shp'])
        self.assertTrue(placed.empty)

    def test_full_cvegeo_preferred(self):
        frame = self.layer(localities=['0002'])
        frame['CVEGEO'] = '2300500010001001'
        with patch('geopandas.read_file', return_value=frame):
            placed = self.cpv(marco_paths=['test.shp'])
        self.assertEqual(placed.placement_source.tolist(), ['official_block'])

    def test_residential_municipality_without_businesses(self):
        with patch('geopandas.read_file', return_value=self.layer()):
            den = self.denue(); den['cve_mun_clean'] = '23001'
            placed = self.cpv(marco_paths=['test.shp'], df_denue=den)
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed.pea_real.sum(), 52)

    def test_unknown_crs_and_invalid_polygon_are_reported(self):
        for frame in [self.layer(crs=None), self.layer([Polygon([(0,0),(1,1),(1,0),(0,1),(0,0)])])]:
            with patch('geopandas.read_file', return_value=frame):
                placed = self.cpv(marco_paths=['broken.shp'], df_denue=self.denue())
            report = placed.attrs['residential_placement']
            self.assertEqual(report['layers'][0]['status'], 'unusable')
            self.assertEqual(placed.placement_source.tolist(), ['denue_block'])

    def test_conflicting_geometry_cannot_multiply_population(self):
        frame = self.layer([box(-86.86,21.14,-86.84,21.16),box(-86.66,21.14,-86.64,21.16)])
        with patch('geopandas.read_file', return_value=frame):
            with self.assertRaisesRegex(ValueError, 'Conflicting official geometry'):
                self.cpv(marco_paths=['test.shp'])

    def test_identical_repeated_geometry_keeps_one_match(self):
        frame = self.layer([box(-86.86,21.14,-86.84,21.16)] * 2)
        with patch('geopandas.read_file', return_value=frame):
            placed = self.cpv(marco_paths=['test.shp'])
        self.assertEqual(len(placed), 1)
        self.assertEqual(placed.pobtot_adj.sum(), 100)

    def test_missing_layer_preserves_fallback_and_atomic_coordinates(self):
        den = self.denue(); den.loc[0, 'lat'] = float('nan')
        placed = self.cpv(df_denue=den)
        self.assertTrue(placed.empty)
        self.assertEqual(placed.attrs['residential_placement']['unlocated']['pea'], 52)
        placed = self.cpv(df_denue=self.denue(mza='2'))
        self.assertEqual(placed.placement_source.tolist(), ['denue_ageb'])

    def test_official_ageb_after_denue_block(self):
        area = self.layer().drop(columns='CVE_MZA')
        with patch('geopandas.read_file', return_value=area):
            block = self.cpv(df_denue=self.denue(), marco_paths=['area.shp'])
            fallback = self.cpv(df_denue=self.denue(mza='2'), marco_paths=['area.shp'])
        self.assertEqual(block.placement_source.tolist(), ['denue_block'])
        self.assertEqual(fallback.placement_source.tolist(), ['official_ageb'])

    def test_bbox_and_grid_filter_ledger(self):
        rows = [dict(ENTIDAD=23,MUN=5,LOC='0001',AGEB='0001',MZA=mza,POBTOT=100,P_15YMAS=80)
                for mza in [1,2]]
        frame = self.layer([box(-86.86,21.14,-86.84,21.16),box(-88,21.14,-87.9,21.16)])
        frame['CVE_MZA'] = ['001','002']
        with patch('geopandas.read_file', return_value=frame):
            placed = self.cpv(rows, marco_paths=['test.shp'])
        r = placed.attrs['residential_placement']
        self.assertEqual(r['input']['population'], r['retained']['population']+r['outside_bbox']['population']+r['unlocated']['population'])
        ledger = grid_accounting(placed, [], exclusion_zones=[dict(bbox=[-86.9,21.1,-86.8,21.2])])
        self.assertEqual(ledger['excluded']['population'], 100)
        self.assertEqual(ledger['rounding_delta_pea'], 0)

    def test_discovery_city_isolation_and_standard_layout(self):
        path = self.root / 'marco_2020_23' / 'conjunto_de_datos' / '23m.shp'
        path.parent.mkdir(parents=True); path.touch()
        (path.parent / '23mun.shp').touch()
        self.assertEqual(discover_marco_layers(self.root), [str(path.resolve())])

    def test_discovery_recognizes_attributes_and_rejects_roads(self):
        block = self.root / 'arbitrary_name.geojson'
        self.layer().to_file(block, driver='GeoJSON')
        from shapely.geometry import LineString
        gpd.GeoDataFrame(dict(highway=['primary']),geometry=[LineString([(0,0),(1,1)])],crs=4326).to_file(self.root/'roads.geojson',driver='GeoJSON')
        self.assertEqual(discover_marco_layers(self.root), [str(block.resolve())])

    def test_mode_validation_and_wizard_roundtrip(self):
        path = self.root / 'city.yaml'
        cfg = dict(city=dict(code='TST',name='Test',bbox=[-87,20,-86,22],residential_placement='official_blocks'),
                   macroeconomics=dict(tasa_pea=.65,til_1_state=.45))
        save_full_city_data(str(path), cfg)
        self.assertEqual(load_city_config(str(path))['city']['residential_placement'], 'official_blocks')
        cfg['city']['residential_placement'] = 'guess'
        path.write_text(yaml.safe_dump(cfg))
        with self.assertRaises(ValueError): load_city_config(str(path))
        with self.assertRaises(ValueError): save_full_city_data(str(path), cfg)

    def test_legacy_mode_is_default(self):
        path = self.root / 'cpv.csv'
        pd.DataFrame([dict(ENTIDAD=23,MUN=5,AGEB='0001',MZA=1,POBTOT=100,P_15YMAS=80)]).to_csv(path,index=False)
        default = load_cpv_demography(str(path), self.denue(), self.bbox, .65)
        legacy = load_cpv_demography(str(path), self.denue(), self.bbox, .65,placement_mode='legacy')
        pd.testing.assert_frame_equal(default, legacy)
        self.assertNotIn('placement_source', default)

    def test_production_pipeline_emits_report_without_game_metadata(self):
        from sb_mexico.pipeline import execute_pipeline
        self.layer().to_file(self.root/'23m.shp')
        pd.DataFrame([dict(ENTIDAD=23,MUN=5,LOC='0001',AGEB='0001',MZA=1,
                           POBTOT=100,P_15YMAS=80,P_12YMAS=80,POCUPADA=50)]).to_csv(self.root/'cpv.csv',index=False)
        pd.DataFrame([dict(cve_ent=23,cve_mun=5,cve_loc='0001',ageb='0001',manzana=1,
                           longitud=-86.80,latitud=21.10,per_ocu='11 a 30 personas')]).to_csv(self.root/'denue.csv',index=False)
        cfg = dict(city=dict(code='TST',name='Test',description='Test',bbox=[-87,20,-86,22],
                             residential_placement='official_blocks'),
                   data_dir=str(self.root),macroeconomics=dict(tasa_pea=.65,til_1_state=.45))
        path = self.root/'city.yaml'; path.write_text(yaml.safe_dump(cfg),encoding='utf-8')
        with patch('sb_mexico.pipeline.ROOT_DIR',str(self.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
            execute_pipeline(str(path),skip_map=True,output_dir=str(self.root/'out'))
        report = json.loads((self.root/'out/residential_placement_report.json').read_text(encoding='utf-8'))
        demand = json.loads((self.root/'out/demand_data.json').read_text(encoding='utf-8'))
        self.assertEqual(report['sources_retained']['official_block']['blocks'],1)
        self.assertEqual(report['grid']['grid_pea'],sum(p['size'] for p in demand['pops']))
        self.assertTrue(all('placement_source' not in p for p in demand['points']))


if __name__ == '__main__':
    unittest.main()
