import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sb_mexico.place_identity import merge_official_places, same_place, serialize_places
from sb_mexico.toponymy_delivery import curated_collection
from sb_mexico.toponymy_homogenizer import ToponymyHomogenizer, find_exact_and_fuzzy_duplicates, resolve_duplicate_groups
from sb_mexico.toponymy_layout import plan_labels, presentation
from sb_mexico.toponymy_tiles import compile_labels, planned_mapgen

BBOX = [-101, 25, -100, 26]
POLICY = dict(engine='official_v1', layout=dict(engine='label_v1', profile='balanced', periphery='core_only'))


def place(id='p', **kw):
    return dict(dict(id=id, name='Los Pinos', loc=[-100.5, 25.5], type='neighbourhood',
                     feature_kind='settlement', official_code='1903900010001', cve_loc='190390001'), **kw)


class LabelLayoutTests(unittest.TestCase):
    def test_thresholds_and_population_unknown_is_not_zero(self):
        cases = [
            (dict(feature_kind='locality', type='city', is_urban=True, population=100000), ('large',6)),
            (dict(feature_kind='locality', type='town', is_urban=True, population=5000), ('medium',10)),
            (dict(feature_kind='locality', type='village', is_urban=False, population=2500), ('medium',11)),
            (dict(category='REGIÓN'), ('medium',12)),
            (dict(population=None), ('small',14)),
            (dict(feature_kind='locality', population=0), ('small',15)),
            (dict(category='PRIVADA'), ('small',15))]
        for props, expected in cases:
            with self.subTest(props=props):
                self.assertEqual(presentation(place(**props))[:2], expected)

    def test_metadata_corrects_old_visual_type_without_mutating_it(self):
        old = place(type='neighbourhood', feature_kind='locality', official_type='city', is_urban=True, population=900000)
        plan = plan_labels([old], BBOX, pipeline=POLICY)
        self.assertEqual(plan['decisions']['p']['scale'], 'large')
        self.assertEqual(old['type'], 'neighbourhood')

    def test_density_delays_without_deleting_and_all_detail_survives_maxzoom(self):
        places = [place(str(i), official_code=f'190390001{i:04}', name='Un nombre territorial muy largo '+str(i)) for i in range(12)]
        before = copy.deepcopy(places)
        plan = plan_labels(places, BBOX, pipeline=POLICY)
        self.assertEqual(places, before)
        self.assertEqual(len(plan['decisions']), 12)
        self.assertEqual(plan['summary']['deferred'], 11)
        self.assertTrue(all(d['visible'] and d['min_zoom'] <= 15 for d in plan['decisions'].values()))
        self.assertEqual(plan, plan_labels(list(reversed(places)), BBOX, pipeline=POLICY))

    def test_real_contour_holes_boundaries_and_no_core_fallback(self):
        core = dict(type='Polygon', coordinates=[
            [[-101,25],[-100,25],[-101,26],[-101,25]],
            [[-100.9,25.1],[-100.85,25.1],[-100.9,25.15],[-100.9,25.1]]])
        places = [place('outside',loc=[-100.1,25.9]), place('edge',loc=[-100.5,25.5]),
                  place('hole',loc=[-100.88,25.12]),place('inside',loc=[-100.8,25.3])]
        layout = plan_labels(places, BBOX, core, POLICY)
        self.assertEqual(layout['decisions']['outside']['reason'], 'Fuera del núcleo')
        self.assertFalse(layout['decisions']['hole']['visible'])
        self.assertTrue(layout['decisions']['edge']['visible'])
        self.assertTrue(layout['decisions']['inside']['visible'])
        self.assertTrue(all(d['visible'] for d in plan_labels(places,BBOX,pipeline=POLICY)['decisions'].values()))

    def test_invalid_core_does_not_silently_use_bbox(self):
        with self.assertRaises(ValueError):
            plan_labels([place()], BBOX, [[-101,25],[-100,26],[-101,26],[-100,25]], POLICY)

    def test_overrides_cannot_escape_core_and_do_not_change_identity(self):
        p = place(label_overrides=dict(scale='large',priority=5,visible=True))
        before = copy.deepcopy(p)
        layout = plan_labels([p], BBOX, [[-101,25],[-100.9,25],[-100.9,25.1],[-101,25.1]], POLICY)
        self.assertEqual(layout['decisions']['p']['scale'],'large')
        self.assertFalse(layout['decisions']['p']['visible'])
        self.assertEqual(p,before)
        hidden = plan_labels([place(label_overrides=dict(visible=False))],BBOX,pipeline=POLICY)
        self.assertEqual(hidden['decisions']['p']['reason'],'Oculta manualmente')

    def test_invalid_overrides_are_rejected(self):
        for override in [dict(scale='huge'),dict(visible='false'),dict(priority=float('nan')),dict(priority=101),dict(extra=1)]:
            with self.subTest(override=override), self.assertRaises(ValueError):
                plan_labels([place(label_overrides=override)],BBOX,pipeline=POLICY)

    def test_priority_selects_official_city_before_colony(self):
        city=place('city',name='Ciudad importante',feature_kind='locality',type='city',is_urban=True,population=800000)
        colony=place('colony',name='Una colonia',official_code='1903900010002')
        layout=plan_labels([colony,city],BBOX,pipeline=POLICY)
        self.assertEqual(layout['decisions']['city']['min_zoom'],6)
        self.assertGreater(layout['decisions']['city']['priority'],layout['decisions']['colony']['priority'])

    def test_geojson_one_layer_identity_zoom_and_tombstone(self):
        p=place(label_overrides=dict(scale='large'))
        collection=curated_collection([p],pipeline=POLICY,bbox=BBOX)
        self.assertEqual(len(collection['features']),1)
        f=collection['features'][0]
        self.assertEqual(f['tippecanoe']['layer'],'city_labels')
        self.assertNotIn('tippecanoe',f['properties'])
        self.assertEqual(f['properties']['id'],'p')
        self.assertEqual(p['type'],'neighbourhood')
        self.assertEqual(curated_collection([p],[p],pipeline=POLICY,bbox=BBOX)['features'],[])
        with self.assertRaises(ValueError):
            curated_collection([p],mode='merge',pipeline=POLICY,bbox=BBOX)

    def test_distinct_keys_and_hierarchies_survive_every_duplicate_tool(self):
        sets = [[place(),place('q',official_code='1903900010002')],
                [place(),place('q',feature_kind='locality',official_code='190390001')],
                [place(name='Supermanzana 1',official_code=None),place('q',name='Supermanzana 10',official_code=None)]]
        for places in sets:
            with self.subTest(places=places):
                self.assertFalse(same_place(*places))
                self.assertEqual(len(ToponymyHomogenizer.spatial_deduplicate(places)[0]),2)
                self.assertEqual(find_exact_and_fuzzy_duplicates(places)['groups'],[])
                with self.assertRaises(ValueError):
                    resolve_duplicate_groups(places,[dict(selected_index=0,candidates=[dict(original_index=0),dict(original_index=1)])])

    def test_strong_identity_rename_matches_but_conflicting_keys_do_not(self):
        a,b=place(),place(name='Nombre editado')
        self.assertTrue(same_place(a,b))
        self.assertEqual(len(ToponymyHomogenizer.spatial_deduplicate([a,b])[0]),1)
        self.assertFalse(same_place(a,place(official_code='1903900010002')))

    def test_legacy_migration_unique_match_retains_id_name_position_and_type(self):
        old=dict(id='legacy',name='Nombre editado',original_name='Los Pinos',loc=[-100.5001,25.5],type='neighbourhood')
        candidate=place(feature_kind='locality',type='city',is_urban=True,population=900000)
        result, exceptions=merge_official_places([old],[candidate])
        self.assertEqual(len(result),1);self.assertEqual(exceptions,[])
        for key in ('id','name','loc','type'):
            self.assertEqual(result[0][key],old[key])
        self.assertEqual(result[0]['population'],900000)
        self.assertEqual(result[0]['official_type'],'city')
        self.assertEqual(merge_official_places(result,[candidate])[0],result)

    def test_ambiguous_migration_never_assigns_official_metadata_arbitrarily(self):
        old=dict(id='legacy',name='Los Pinos',loc=[-100.5,25.5],type='suburb')
        candidates=[place(),place('q',feature_kind='locality',official_code='190390001')]
        result, exceptions=merge_official_places([old],candidates)
        self.assertEqual(len(result),3);self.assertEqual(len(exceptions),2)
        self.assertNotIn('official_code',next(p for p in result if p['id']=='legacy'))

    def test_safe_resolution_returns_tombstones_and_rejects_changed_ids(self):
        places=[place('a',official_code=None),place('b',official_code=None)]
        res=dict(selected_index=0,candidates=[dict(original_index=0,id='a'),dict(original_index=1,id='b')])
        result=resolve_duplicate_groups(places,[res])
        self.assertEqual(result['removed'],[places[1]])
        self.assertEqual(merge_official_places(result['places'],[places[1]],result['removed'])[0],serialize_places(result['places']))
        res['candidates'][1]['id']='changed'
        with self.assertRaises(ValueError): resolve_duplicate_groups(places,[res])

    def test_legacy_class_unchanged_and_new_adapter_checks_capabilities(self):
        class Base: pass
        self.assertIs(planned_mapgen(Base,None),Base)
        with self.assertRaises(RuntimeError):
            planned_mapgen(Base,dict(layout=dict(engine='label_v1')))

    def test_adapter_replaces_legacy_label_stage_and_empty_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            class Base:
                maxzoom=15;city='TST';city_dir=directory;verb=False
                def add_labels(self): raise AssertionError('Legacy stage must never run')
                def run_all(self): self.add_labels()
                def _update_mbtiles_metadata(self,path): pass
                def _run_command(self,cmd):
                    commands.append(cmd)
                    if cmd[0] in ('tippecanoe','tile-join'): Path(cmd[cmd.index('-o')+1]).write_bytes(b'tiles')
                    else: Path(cmd[-1]).write_bytes(b'final')
            for places in ([place()],[]):
                commands=[]
                (root/'TST-nolabels.pmtiles').write_bytes(b'base')
                with patch('sb_mexico.toponymy_tiles.shutil.which',return_value='/bin/tool'):
                    cls=planned_mapgen(Base,curated_collection(places,pipeline=POLICY,bbox=BBOX))
                cls().run_all()
                self.assertEqual((root/'TST.pmtiles').read_bytes(),b'final' if places else b'base')
                self.assertTrue((root/'toponymy-layout.json').is_file())
                if places:
                    source=json.loads((root/'TST-onlylabels.geojson').read_text(encoding='utf-8'))
                    self.assertEqual(source['features'][0]['tippecanoe']['minzoom'],14)
                    self.assertIn('id',commands[0])
                    self.assertFalse(any('osmium' in cmd for cmd in commands))


if __name__=='__main__': unittest.main()
