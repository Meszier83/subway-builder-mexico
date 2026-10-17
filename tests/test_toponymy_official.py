"""Boundary, identity, source isolation and delivery regressions for official names."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import yaml
from shapely.geometry import shape, Point, Polygon, mapping

from sb_mexico import toponymy
from sb_mexico.toponymy_official import read_source, scan_official_catalog, import_source, acquire_catalogs
from sb_mexico.toponymy_delivery import curated_collection, synchronize_labels
from sb_mexico.place_identity import same_place


BBOX = [-101, 25, -100, 26]


def locality(code='190390001', name='Ciudad', lon=-100.5, **changes):
    return dict(cvegeo=code, nomgeo=name, longitud=str(lon), latitud='25.5',
                pob_total='150000', ambito='URBANO', estatus=1, **changes)


def settlement(code='1903900010001', name='Los Pinos', geometry=None):
    return dict(type='Feature', properties=dict(CVEGEO=code[:9], CVE_ASEN=code[9:],
                NOM_ASEN=name, TIPO='COLONIA'), geometry=geometry or mapping(
                Polygon([(-100.7,25.3),(-100.3,25.3),(-100.3,25.7),(-100.7,25.7)])))


class TestOfficialToponymy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'chosen'
        self.sources = self.project / 'toponymy' / 'sources'
        self.sources.mkdir(parents=True)
        self.city = self.root / 'city.yaml'
        self.config = dict(city=dict(code='TST', name='Fixture', bbox=BBOX),
                           data_dir=str(self.project), places=[], toponymy=dict(engine='official_v1'))
        self.city.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        self.mock = patch.object(toponymy, '__file__', str(self.root / 'sb_mexico' / 'toponymy.py'))
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def write(self, name, doc, folder=None):
        path = (folder or self.sources) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        return path

    def scan(self, **config):
        self.config.update(config)
        self.city.write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding='utf-8')
        return scan_official_catalog(str(self.city))

    def test_official_locations_and_no_trade_threshold(self):
        self.write('localities_a.json', dict(datos=[locality(name='Peña'), locality('190390002','Pueblo',
            lon=-100.6, periodo='11/2025')]))
        result = self.scan()
        self.assertEqual([p['loc'] for p in result['places']], [[-100.5,25.5],[-100.6,25.5]])
        self.assertEqual(result['places'][0]['id'], 'inegi_locality_190390001')
        self.assertTrue(result['partial'])  # DCAH absent, never reported as complete.
        self.assertNotIn('establishments', result['places'][0])

    def test_different_codes_and_hierarchy_do_not_collapse_homonyms(self):
        self.write('localities_a.json', dict(datos=[locality(name='Los Pinos')]))
        self.write('dcah_a.geojson', dict(type='FeatureCollection', features=[
            settlement(), settlement('1903900010002')]))
        result = self.scan()
        self.assertEqual(len(result['places']),3)
        self.assertEqual(len({p['id'] for p in result['places']}),3)
        self.assertFalse(same_place(result['places'][0], result['places'][1]))
        self.assertFalse(result['partial'])
        self.assertFalse(result['coverage_complete'])

    def test_polygon_interior_and_bbox_clipping_not_centroid(self):
        poly = Polygon([(-101.2,25.1),(-100.2,25.1),(-100.2,25.9),(-100.4,25.9),
                        (-100.4,25.3),(-101.2,25.3)])
        path = self.write('dcah_a.geojson', dict(type='FeatureCollection',features=[settlement(geometry=mapping(poly))]))
        places, _, counts, _ = read_source(path,'dcah',BBOX)
        self.assertEqual(counts['invalid'],0)
        self.assertTrue(poly.covers(Point(places[0]['loc'])))
        self.assertGreaterEqual(places[0]['loc'][0],BBOX[0])
        self.assertEqual(places[0]['position_method'],'polygon_interior')
        self.assertEqual(places[0]['boundary']['type'],'Polygon')

    def test_stable_identity_survives_geometry_update_rename_and_tombstone(self):
        path = self.write('localities_a.json', dict(datos=[locality()]))
        original = self.scan()['places'][0]
        edit = dict(original,name='Mi nombre',loc=[-100.2,25.6])
        path.write_text(json.dumps(dict(datos=[locality(name='Nombre nuevo',lon=-100.8)])))
        result = self.scan(places=[edit])
        self.assertEqual(result['places'][0]['name'],'Mi nombre')
        self.assertEqual(result['places'][0]['loc'],edit['loc'])
        self.assertEqual(self.scan(places=[],deleted_places=[edit])['places'],[])

    def test_inactive_outside_invalid_coordinates_are_visible(self):
        inactive = dict(locality('190390002'),estatus=0)
        wrong = dict(locality('190390003'),longitud='nan')
        self.write('localities_a.json',dict(datos=[locality(),inactive,wrong,locality('190390004',lon=-99)]))
        result = self.scan()
        counts = result['sources'][0]['counts']
        self.assertEqual((counts['inactive'],counts['outside'],counts['invalid']),(1,1,1))
        self.assertEqual(result['total'],1)

    def test_tabular_settlements_are_unlocated_never_locality_coordinates(self):
        self.write('localities_a.json',dict(datos=[locality()]))
        self.write('settlement_catalog_a.json',dict(datos=[
            dict(cvegeo='1903900010001',nom_asen='Centro',tipo_asen='COLONIA'),
            dict(cvegeo='1903900090001',nom_asen='Fuera',tipo_asen='COLONIA')]))
        result = self.scan()
        self.assertEqual(result['total'],1)
        self.assertEqual([p['name'] for p in result['unlocated']],['Centro'])
        self.assertNotIn('loc',result['unlocated'][0])

    def test_dcah_points_or_missing_polygons_are_rejected(self):
        path = self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[
            settlement(geometry=mapping(Point(-100.5,25.5)))]))
        self.assertEqual(read_source(path,'dcah',BBOX)[2]['invalid'],1)
        with self.assertRaises(ValueError):
            import_source(str(self.city),'dcah','bad.geojson',path.read_bytes())

    def test_project_isolation_no_other_city_or_shared_denue(self):
        self.write('localities_a.json',dict(datos=[locality('190390002')]),self.root/'other'/'toponymy'/'sources')
        self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[settlement()]),self.root/'data')
        self.write('denue.csv',dict(datos=[locality()]),self.project)
        result = self.scan()
        self.assertEqual(result['total'],0)
        self.assertEqual({s['kind'] for s in result['sources']},{'localities','dcah'})

    def test_edition_conflicts_are_excluded_instead_of_ordered_winner(self):
        self.write('localities_a.json',dict(datos=[locality()]))
        self.write('localities_b.json',dict(datos=[locality(name='Contradictorio')]))
        result = self.scan()
        self.assertEqual(result['total'],0)
        self.assertEqual(len(result['conflicts']),1)

    def test_import_provenance_and_modified_file_detection(self):
        content = json.dumps(dict(datos=[locality()])).encode()
        result = import_source(str(self.city),'localities','original.json',content)
        place = self.scan()['places'][0]
        self.assertEqual(place['source_hash'],result['sha256'])
        Path(result['path']).write_text(json.dumps(dict(datos=[locality(name='Changed')])))
        scanned = self.scan()
        self.assertEqual(scanned['total'],0)
        self.assertEqual(scanned['sources'][0]['status'],'error')

    def test_fingerprint_tracks_source_and_policy_and_report_is_current(self):
        path = self.write('localities_a.json',dict(datos=[locality()]))
        before = self.scan()['fingerprint']
        path.write_text(json.dumps(dict(datos=[locality(lon=-100.6)])))
        after = self.scan()
        self.assertNotEqual(before,after['fingerprint'])
        report = json.loads((self.project/'.toponymy'/'official-catalog.json').read_text())
        self.assertEqual(report['fingerprint'],after['fingerprint'])

    def test_opt_in_osm_complement_official_candidate_wins(self):
        self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[settlement()]))
        native = dict(name='Los Pinos',loc=[-100.5,25.5],type='suburb',source='OSM_NODE')
        with patch.object(toponymy,'_load_osm_places',return_value=[native]) as osm:
            self.scan()
            osm.assert_not_called()
            result = self.scan(toponymy=dict(engine='official_v1',osm_fallback=True))
            osm.assert_called_once()
            self.assertEqual(result['total'],1)
            self.assertEqual(result['places'][0]['source'],'INEGI_DCAH')

    def test_numbered_places_preserve_context_not_universal_supermanzana(self):
        f = settlement(name='12')
        self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[f]))
        self.assertEqual(self.scan()['places'][0]['name'],'12')
        f['properties']['TIPO']='REGION'
        self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[f]))
        self.assertEqual(self.scan()['places'][0]['name'],'Región 12')

    def test_zip_shapefile_crs_transformation_and_missing_prj(self):
        import geopandas as gpd
        folder = self.root/'shape'; folder.mkdir()
        f = settlement()
        frame = gpd.GeoDataFrame([f['properties']],geometry=[shape(f['geometry'])],crs=4326).to_crs(32614)
        frame.to_file(folder/'as.shp',encoding='UTF-8')
        path = self.sources/'dcah_a.zip'
        with zipfile.ZipFile(path,'w') as archive:
            for item in folder.iterdir(): archive.write(item,item.name)
        found,_,counts,_ = read_source(path,'dcah',BBOX)
        self.assertEqual(counts['invalid'],0)
        self.assertTrue(shape(f['geometry']).covers(Point(found[0]['loc'])))
        with zipfile.ZipFile(path,'w') as archive:
            for item in folder.iterdir():
                if item.suffix != '.prj': archive.write(item,item.name)
        with self.assertRaises(ValueError): read_source(path,'dcah',BBOX)

    def test_unsafe_zip_and_geographical_bbox_rejected(self):
        path=self.sources/'dcah_a.zip'
        with zipfile.ZipFile(path,'w') as archive: archive.writestr('../bad.shp',b'x')
        with self.assertRaises(ValueError): read_source(path,'dcah',BBOX)
        with self.assertRaises(ValueError): read_source(path,'dcah',[-181,25,-100,26])

    def test_inegi_national_wrapper_reads_integrated_layer_once(self):
        import geopandas as gpd
        folder=self.root/'vector';folder.mkdir()
        feature=settlement()
        gpd.GeoDataFrame([feature['properties']],geometry=[shape(feature['geometry'])],crs=4326).to_file(folder/'00as.shp')
        inner=self.root/'inner.zip'
        with zipfile.ZipFile(inner,'w') as archive:
            for item in folder.iterdir(): archive.write(item,item.name)
        outer=self.sources/'dcah_national.zip'
        with zipfile.ZipFile(outer,'w') as archive:
            archive.write(inner,'00_integrado.zip');archive.write(inner,'19_state.zip')
        found,_,counts,_=read_source(outer,'dcah',BBOX)
        self.assertEqual(len(found),1)
        self.assertEqual(counts['rows'],1)

    def test_authoritative_empty_layers_and_legacy_behavior(self):
        native=curated_collection([dict(name='Nativo',loc=[-100.5,25.5],type='city')])['features']
        modern=curated_collection([],mode='replace',pipeline=dict(engine='official_v1'))
        self.assertEqual(synchronize_labels(native,modern,'cities'),[])
        self.assertEqual(synchronize_labels(native,curated_collection([]),'cities'),native)
        self.assertEqual(synchronize_labels(native,dict(modern,mode='merge'),'cities'),native)

    def test_enlarged_bbox_reports_materialization_coverage(self):
        from sb_mexico.source_downloads import sha256
        path=self.write('dcah_a.geojson',dict(type='FeatureCollection',features=[settlement()]))
        path.with_name(path.name+'.source.json').write_text(json.dumps(dict(sha256=sha256(path),bbox=[-100.9,25.1,-100.1,25.9])))
        result=self.scan()
        self.assertTrue(result['partial'])
        self.assertTrue(any('creció' in warning for warning in result['warnings']))

    def test_official_policy_crosses_actual_wsl_export(self):
        from sb_mexico.cartography import build_city_map_wsl
        class Process:
            stdout=[]
            returncode=0
            def poll(self): return 0
            def wait(self): return 0
        with patch('sb_mexico.cartography.subprocess.Popen',return_value=Process()):
            build_city_map_wsl('TST',BBOX,str(self.root/'fake.pbf'),str(self.root/'out'),
                               places=[],toponymy_pipeline=dict(engine='official_v1'))
        doc=json.loads((self.root/'out'/'curated_places.geojson').read_text())
        self.assertTrue(doc['authoritative'])
        self.assertEqual(doc['pipeline'],'official_v1')

    def test_acquire_dcah_uses_published_product_and_materializes_owned_source(self):
        from sb_mexico.toponymy_official import acquire_dcah
        raw=self.write('dcah_fixture.geojson',dict(type='FeatureCollection',features=[settlement()]),self.root)
        urls=[]
        class Client:
            def __init__(self,*args,**kwargs): self.progress=lambda m:None
            def json(self,url,**kwargs):
                self.query=kwargs['payload']
                return dict(success=True,list=dict(mapas=[dict(key='published',titulo='Delimitación 2025',edicion=2025,
                    formatos=[dict(extension='SHP',url=dict(valor='/verified/source.zip'),peso=530,siglas='MB')])])),{}
            def fetch(self,url,**kwargs):
                urls.append(url)
                return raw,dict(sha256='raw-hash',url=url)
        with patch('sb_mexico.source_downloads.Client',Client):
            result=acquire_dcah(str(self.city))
        self.assertEqual(urls,['https://www.inegi.org.mx/verified/source.zip'])
        self.assertEqual(result['labels'],1)
        self.assertEqual(Path(result['files'][0]).name,'dcah_automatic.geojson')
        self.assertEqual(self.scan()['places'][0]['id'],'inegi_settlement_1903900010001')

    def test_acquisition_uses_intersecting_municipalities_and_verified_api_shape(self):
        from sb_mexico.source_downloads import sha256
        calls=[]
        class Client:
            def __init__(self,*args,**kwargs): self.progress=lambda m:None
            def json(self,url,**kwargs):
                calls.append(url)
                data=[locality()] if '/localidades/' in url else [dict(cvegeo='1903900010001',nom_asen='Centro')]
                return dict(datos=data,numReg=len(data)),dict(sha256='raw',url=url)
        geo=dict(states=[dict(code='19')],municipalities=[dict(code='19039',name='Monterrey')])
        with patch('sb_mexico.source_downloads.Client',Client),patch('sb_mexico.source_downloads.geography',return_value=geo):
            result=acquire_catalogs(str(self.city))
        self.assertEqual(len(result['files']),2)
        self.assertTrue(all(url.endswith('/19/039') for url in calls))
        path=Path(result['files'][0])
        record=json.loads(path.with_name(path.name+'.source.json').read_text())
        self.assertEqual(record['raw_sha256'],'raw')
        self.assertEqual(record['sha256'],sha256(path))
        self.assertEqual(json.loads(path.read_text())['datos'][0]['nom_mun'],'Monterrey')


if __name__ == '__main__': unittest.main()
