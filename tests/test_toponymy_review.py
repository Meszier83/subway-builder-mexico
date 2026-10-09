"""Adversarial review policies and actual generated depot-label code."""
import inspect
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml
from shapely.geometry import shape

from sb_mexico.toponymy_delivery import curated_collection, synchronize_labels
from sb_mexico.toponymy_homogenizer import (
    calculate_place_prestige_score, cluster_places_by_proximity, apply_zone_thinning_selection)
from tools.patch_depot_wsl import patch_polygon_neighborhoods


def place(name='Los Pinos', lon=-100.2, **kw):
    return dict(name=name, loc=[lon,25.7], type='suburb', **kw)


class TestReviewPolicy(unittest.TestCase):
    def test_trade_count_is_not_population_or_default_balanced_rank(self):
        a = place(establishments=1, source='INEGI_DENUE')
        b = place(establishments=100000, source='INEGI_DENUE')
        self.assertEqual(calculate_place_prestige_score(a,'balanced'), calculate_place_prestige_score(b,'balanced'))
        self.assertGreater(calculate_place_prestige_score(b,'density'), calculate_place_prestige_score(a,'density'))
        self.assertGreater(calculate_place_prestige_score(place('Sol'),'shortest'),
                           calculate_place_prestige_score(place('Nombre Largo', establishments=100000),'shortest'))
        from sb_mexico.place_identity import merge_places
        edited=place('Editado',id='legacy-uuid',original_name='Los Pinos')
        result=merge_places([edited],[place(id='source-id')])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name'],'Editado')

    def test_cluster_preserves_other_municipalities_and_scales(self):
        places = [place('A',cve_mun='19039'),place('B',cve_mun='05030'),
                  dict(place('Ciudad',cve_mun='19039'),type='city')]
        data = cluster_places_by_proximity(places,1000,'balanced',False)
        self.assertEqual(len(data['zones']),3)
        self.assertIn('score_components', data['zones'][0]['candidates'][0])
        self.assertEqual(len(apply_zone_thinning_selection(places,data['zones'])['places']),3)

    def test_unrepresented_points_are_not_pruned(self):
        places=[place('A'), dict(name='Invalid',loc=[math.nan,25],type='suburb')]
        data=cluster_places_by_proximity(places,1000,'balanced')
        self.assertEqual(len(data['zones']),1)
        self.assertEqual(len(apply_zone_thinning_selection(places,data['zones'])['places']),2)

    def test_ranking_ties_do_not_depend_on_list_order(self):
        places=[place('A',id='a'),place('B',id='b',lon=-100.201)]
        a=cluster_places_by_proximity(places,1000,'balanced')
        b=cluster_places_by_proximity(list(reversed(places)),1000,'balanced')
        self.assertEqual(a['zones'][0]['title'],b['zones'][0]['title'])

    def test_merge_keeps_native_coverage_and_local_tombstones(self):
        native=curated_collection([place('Los Pinos'),place('Los Pinos',lon=-101.2),place('Otro')])['features']
        local=place('Los Pinos')
        collection=curated_collection([place('Manual')],[local],'merge')
        names=synchronize_labels(native,collection,'suburbs')
        self.assertEqual(len(names),3)
        self.assertTrue(any(f['geometry']['coordinates'][0]==-101.2 for f in names))
        self.assertTrue(any(f['properties']['name']=='Otro' for f in names))

    def test_legacy_replace_and_explicit_empty_lists(self):
        native=curated_collection([place('Nativo')])['features']
        collection=curated_collection([place('Manual')])
        self.assertEqual([f['properties']['name'] for f in synchronize_labels(native,collection,'suburbs')],['Manual'])
        self.assertEqual(synchronize_labels(native,curated_collection([]),'suburbs'),native)
        with self.assertRaises(ValueError):
            curated_collection([],mode='unknown')

    def test_renamed_stable_id_replaces_only_corresponding_native(self):
        native=curated_collection([place('Viejo',id='n1'),place('Viejo',lon=-101.2,id='n2')])['features']
        result=synchronize_labels(native,curated_collection([place('Nuevo',id='n1')],mode='merge'),'suburbs')
        self.assertEqual([f['properties']['name'] for f in result],['Nuevo','Viejo'])
        # Real osmium exports lack our generated ID. It must be reconstructed
        # from the original name/position for an edited label to replace it.
        raw = dict(type='Feature', properties=dict(name='Viejo',place='suburb'),
                   geometry=dict(type='Point',coordinates=[-100.2,25.7]))
        from sb_mexico.place_identity import place_id
        result = synchronize_labels([raw],curated_collection(
            [place('Nuevo',id=place_id(place('Viejo')))],mode='merge'),'suburbs')
        self.assertEqual(len(result),1)
        result = synchronize_labels([raw],curated_collection(
            [place('Nuevo',id='legacy-uuid',original_name='Viejo')],mode='merge'),'suburbs')
        self.assertEqual(len(result),1)

    def test_actual_depot_patch_executes_merge(self):
        dummy = """            # Build the osmium filter string
            # e.g., "n/place=city n/place=borough"
            filter_cmd.extend([f"n/place{self.places_suffix}={t}" for t in tags])
            filter_cmd.extend(["-o", str(osm_pbf), "--overwrite"])
            self._run_command(filter_cmd)
            self._run_command(["osmium", "export", str(osm_pbf), "-o",
                               str(geojson), "--overwrite"])
            self._rewrite_label_geojson_names(geojson)"""
        dummy = dummy.replace('str(osm_pbf), "-o",\n', 'str(osm_pbf), "-o", \n')
        patched, modified=patch_polygon_neighborhoods(dummy)
        self.assertTrue(modified)
        code='def build(self,name,tags,filter_cmd,osm_pbf,geojson):\n    if True:\n        if True:\n'+patched
        namespace=dict(os=os,json=json,math=math,shape=shape)
        exec(compile(code,'generated-label-step','exec'),namespace)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            native=curated_collection([place('Nativo'),place('Los Pinos',lon=-101.2)])
            geo=root/'labels.geojson';geo.write_text(json.dumps(native),encoding='utf-8')
            config=root/'curated_places.geojson'
            config.write_text(json.dumps(curated_collection([place('Manual')],mode='merge')),encoding='utf-8')
            fixture=type('Fixture',(),dict(places_suffix='',verb=False,city_dir=str(root),
                        _run_command=lambda *args:None,_rewrite_label_geojson_names=lambda *args:None))()
            with patch.dict(os.environ,{'SB_CURATED_PLACES_GEOJSON':str(config)}):
                namespace['build'](fixture,'suburbs',['suburb'],[],root/'fake.pbf',geo)
            result=json.loads(geo.read_text(encoding='utf-8'))
            self.assertEqual({f['properties']['name'] for f in result['features']},{'Manual','Nativo','Los Pinos'})
            native = curated_collection([dict(place('Homónimo'),type='city'),
                                         dict(place('Homónimo',lon=-101.2),type='city')])
            geo.write_text(json.dumps(native),encoding='utf-8')
            with patch.dict(os.environ,{'SB_CURATED_PLACES_GEOJSON':str(config)}):
                namespace['build'](fixture,'cities',['city'],[],root/'fake.pbf',geo)
            self.assertEqual(len(json.loads(geo.read_text(encoding='utf-8'))['features']),2)

    def test_wizard_save_reload_policy_metadata_and_discards(self):
        from tools.wizard import save_full_city_data
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'city.yaml'
            config.write_text('city: {code: TST, name: Test, bbox: [-101, 25, -100, 26]}\n',encoding='utf-8')
            original=dict(city=dict(code='TST',name='Test',bbox=[-101,25,-100,26]),
                          places=[place(id='stable',source='INEGI_DENUE',aliases=['LOS PINOS'])],
                          deleted_places=[place('Descartado')],toponymy_mode='merge')
            with patch('tools.wizard.ROOT_DIR',str(config.parent)):
                save_full_city_data(str(config),original)
            loaded=yaml.safe_load(config.read_text(encoding='utf-8'))
            self.assertEqual(loaded['toponymy_mode'],'merge')
            self.assertEqual(loaded['places'],original['places'])
            self.assertEqual(loaded['deleted_places'],original['deleted_places'])

    def test_wsl_export_policy_and_empty_list_overwrite_stale_file(self):
        from sb_mexico.cartography import build_city_map_wsl
        class FakeProcess:
            stdout=[]
            def poll(self): return 0
            returncode=0
            def wait(self): return 0
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            target=root/'curated_places.geojson';target.write_text('stale',encoding='utf-8')
            with patch('sb_mexico.cartography.subprocess.Popen',return_value=FakeProcess()) as popen:
                build_city_map_wsl('TST',[-101,25,-100,26],str(root/'fake.pbf'),str(root),
                                  places=[],deleted_places=[place('Descartado')],toponymy_mode='merge')
            exported=json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(exported['mode'],'merge');self.assertEqual(exported['features'],[])
            self.assertEqual(exported['deleted_places'][0]['name'],'Descartado')
            self.assertIn('--curated-places',popen.call_args.args[0])
            self.assertEqual(inspect.signature(build_city_map_wsl).parameters['toponymy_mode'].default,'replace')
