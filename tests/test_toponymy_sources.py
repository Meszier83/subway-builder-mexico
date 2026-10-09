"""Production-path regressions for multi-state topoymy and territorial identity."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

from sb_mexico import toponymy
from sb_mexico.place_identity import merge_places, place_name_key
from sb_mexico.toponymy_homogenizer import find_exact_and_fuzzy_duplicates, ToponymyHomogenizer


class TestToponymySources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'chosen'
        self.data.mkdir()
        self.city = self.root / 'fixture.yaml'
        self.config = dict(city=dict(name='Fixture', code='FX', bbox=[-102, 24, -98, 28]),
                           data_dir=str(self.data), places=[])
        self.mock_root = patch.object(toponymy, '__file__', str(self.root / 'sb_mexico' / 'toponymy.py'))
        self.mock_root.start()
        self.addCleanup(self.mock_root.stop)

    def rows(self, name='LOS PINOS', lon=-100.2, ent='19', mun='039', loc='0001', n=8, kind='COLONIA'):
        return [dict(id=f'{name}-{kind}-{lon}-{i}', nomb_asent=name, tipo_asent=kind, longitud=lon,
                     latitud=25.7, cve_ent=ent, cve_mun=mun, cve_loc=loc,
                     municipio='Monterrey' if ent == '19' else 'Saltillo', localidad='Localidad')
                for i in range(n)]

    def csv(self, name, rows, folder=None, encoding='utf-8'):
        path = (folder or self.data) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows).to_csv(path, index=False, encoding=encoding)
        return path

    def scan(self, **changes):
        self.config.update(changes)
        self.city.write_text(yaml.safe_dump(self.config, allow_unicode=True), encoding='utf-8')
        return toponymy.scan_city_settlements_catalog(str(self.city))

    def test_all_authorized_sources_custom_directory_and_exclusions(self):
        self.csv('denue_05.csv', self.rows(lon=-101, ent='05', mun='030'))
        self.csv('denue_19.csv', self.rows('INDEPENDENCIA'))
        self.csv('denue_excluded.csv', self.rows('EXCLUIDA'))
        self.csv('denue_shared.csv', self.rows('NACIONAL'), self.root / 'data')
        self.csv('denue_other_city.csv', self.rows('AJENA'), self.root / 'data' / 'another_city')
        result = self.scan(data_exclusions=['denue_excluded.csv'])
        self.assertEqual({p['name'] for p in result['places']}, {'Los Pinos', 'Independencia', 'Nacional'})
        self.assertEqual({m['municipality'] for m in result['coverage']}, {'Monterrey', 'Saltillo'})
        self.assertEqual(len([s for s in result['sources'] if s['kind'] == 'DENUE']), 3)
        self.assertTrue(result['partial'])  # OSM unavailable, clearly reported.

    def test_overlapping_sources_do_not_double_counts(self):
        rows = self.rows()
        self.csv('denue_project.csv', rows)
        self.csv('denue_national.csv', rows, self.root / 'data')
        result = self.scan()
        self.assertEqual(result['places'][0]['establishments'], 8)
        self.assertEqual(sum(s.get('duplicates', 0) for s in result['sources']), 8)

    def test_homonyms_are_separated_by_territory(self):
        self.csv('denue.csv', self.rows(ent='19') + self.rows(ent='05'))
        places = self.scan()['places']
        self.assertEqual(len(places), 2)
        self.assertEqual({p['cve_mun'] for p in places}, {'19039', '05039'})
        self.assertEqual(len({p['id'] for p in places}), 2)

    def test_distant_homonyms_same_locality_are_not_midpointed(self):
        self.csv('denue.csv', self.rows(lon=-101) + self.rows(lon=-100))
        places = self.scan()['places']
        self.assertEqual({p['loc'][0] for p in places}, {-101, -100})
        self.assertEqual(find_exact_and_fuzzy_duplicates(places)['total_duplicate_groups'], 0)

    def test_aliases_merge_before_threshold_without_losing_suffix_or_region(self):
        rows = self.rows('97', n=4, kind='SUPERMANZANA') + self.rows('SUPERMANZANA 97', n=4, kind='SUPERMANZANA')
        rows += self.rows('9A', kind='SUPERMANZANA') + self.rows('9B', kind='SUPERMANZANA')
        rows += self.rows('9A', kind='REGION')
        self.csv('denue.csv', rows)
        places = self.scan()['places']
        self.assertEqual({p['name'] for p in places}, {'Supermanzana 97', 'Supermanzana 9A', 'Supermanzana 9B', 'Región 9A'})
        self.assertTrue(all(p['establishments'] == 8 for p in places))
        combined = next(p for p in places if p['name'] == 'Supermanzana 97')
        self.assertEqual(combined['aliases'], ['97', 'SUPERMANZANA 97'])

    def test_utf8_and_legacy_encoding_preserve_names(self):
        self.csv('denue_utf8.csv', self.rows('CAÑADA'))
        self.csv('denue_legacy.csv', self.rows('PEÑA'), encoding='cp1252')
        self.assertEqual({p['name'] for p in self.scan()['places']}, {'Cañada', 'Peña'})
        self.assertNotEqual(place_name_key('Peña'), place_name_key('Pea'))
        self.assertEqual(place_name_key('José'), place_name_key('JOSE'))
        self.assertEqual(place_name_key('Regional'), 'regional')

    def test_bad_source_is_reported_without_discarding_good_sources(self):
        self.csv('denue_ok.csv', self.rows())
        (self.data / 'denue_bad.csv').write_text('wrong,header\n1,2\n')
        result = self.scan()
        self.assertEqual(result['total'], 1)
        self.assertTrue(result['partial'])
        self.assertTrue(any(s['status'] == 'error' and 'bad' in s['path'] for s in result['sources']))

    def test_legacy_manifest_is_bbox_filtered(self):
        folder = self.root / 'dist' / 'fixture'
        folder.mkdir(parents=True)
        (folder / 'toponymy_manifest.json').write_text(json.dumps(dict(osm_nodes=[
            dict(name='Fuera', lon=-86.8, lat=21.1, place='city'),
            dict(name='Dentro', lon=-100, lat=25.7, place='suburb')])))
        self.assertEqual([p['name'] for p in self.scan()['places']], ['Dentro'])

    def test_existing_edits_and_unknown_provenance_are_preserved(self):
        existing = dict(name='Los Pinos', loc=[-100.2005, 25.7], type='neighbourhood', id='manual-stable')
        self.csv('denue.csv', self.rows())
        result = self.scan(places=[existing])
        self.assertEqual(len(result['places']), 1)
        place = result['places'][0]
        self.assertEqual((place['id'], place['loc'], place['type']), ('manual-stable', existing['loc'], existing['type']))
        self.assertEqual(place['source'], 'UNKNOWN')
        self.assertEqual(place['matched_source'], 'INEGI_DENUE')
        self.assertEqual(place['establishments'], 8)
        self.assertTrue(place['source_files'])

    def test_spatial_discard_does_not_remove_remote_homonym(self):
        self.csv('denue.csv', self.rows(lon=-101) + self.rows(lon=-100))
        deleted = dict(name='Los Pinos', loc=[-101, 25.7])
        self.assertEqual([p['loc'][0] for p in self.scan(deleted_places=[deleted])['places']], [-100])

    def test_stable_id_preserves_manual_rename(self):
        candidate = dict(id='stable', name='Original', loc=[-100, 25.7], source='INEGI_DENUE')
        manual = dict(candidate, name='Nombre corregido', loc=[-100.001, 25.7])
        self.assertEqual(merge_places([manual], [candidate])[0]['name'], 'Nombre corregido')

    def test_deduplication_respects_territory_even_when_close(self):
        places = [dict(name='Centro', loc=[-100, 25.7], cve_mun='19039'),
                  dict(name='Centro', loc=[-100.0001, 25.7], cve_mun='05030')]
        self.assertEqual(find_exact_and_fuzzy_duplicates(places)['total_duplicate_groups'], 0)
        self.assertEqual(len(ToponymyHomogenizer.spatial_deduplicate(places)[0]), 2)

    def test_osm_cache_is_bound_to_bbox_source_and_uses_shared_preview(self):
        pbf = self.data / 'area.osm.pbf'
        pbf.write_bytes(b'fixture')
        self.csv('denue.csv', self.rows())
        feature = dict(type='Feature', properties=dict(name='OSM Place', place='suburb'),
                       geometry=dict(type='Point', coordinates=[-100, 25.7]))
        calls = []
        def extract(source, output, **kwargs):
            calls.append(kwargs['bbox'])
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(json.dumps(dict(type='FeatureCollection', features=[feature])))
            kwargs['diagnostics'].update(status='ok')
            return True
        with patch.object(toponymy, 'run_osmium_places_filter', side_effect=extract):
            self.assertFalse(self.scan()['partial'])
            self.assertTrue(next(s for s in self.scan()['sources'] if s['kind'] == 'OSM')['cached'])
            self.assertEqual(len(toponymy.extract_native_osm_places_preview(str(self.city))), 1)
            self.assertEqual(len(calls), 1)
            self.config['city']['bbox'][3] = 27.9
            self.scan()
            self.assertEqual(len(calls), 2)
            pbf.write_bytes(b'changed fixture')
            self.scan()
            self.assertEqual(len(calls), 3)

    def test_osm_failure_is_partial_and_successful_denue_still_returned(self):
        (self.data / 'area.osm.pbf').write_bytes(b'fixture')
        self.csv('denue.csv', self.rows())
        def fail(*args, **kwargs):
            kwargs['diagnostics'].update(status='error', message='Tiempo agotado en OSM')
            return False
        with patch.object(toponymy, 'run_osmium_places_filter', side_effect=fail):
            result = self.scan()
        self.assertTrue(result['partial'])
        self.assertIn('Tiempo agotado en OSM', result['warnings'])
        self.assertEqual(result['total'], 1)

    def test_invalid_bbox_and_threshold_are_explicit_errors(self):
        self.config['city']['bbox'] = [-98, 24, -102, 28]
        with self.assertRaises(ValueError):
            self.scan()


class TestToponymyPersistence(unittest.TestCase):
    def test_wizard_and_poi_studio_round_trip_metadata_and_discards(self):
        from tools import wizard, poi_studio
        from sb_mexico.place_identity import serialize_places
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'city.yaml'
            place = dict(id='stable', name='Peña', loc=[-100, 25.7], type='suburb', source='INEGI_DENUE',
                         cve_ent='19', cve_mun='19039', cve_loc='190390001',
                         source_files=['denue_19.csv'], establishments=42, category='COLONIA', is_micro=False)
            config = dict(city=dict(name='Test', code='TST', bbox=[-102, 24, -98, 28]),
                          places=[place], deleted_places=[dict(place, id='discarded', name='Otra')])
            with patch.object(wizard, '_resolve_city_path', return_value=str(path)):
                wizard.save_full_city_data(str(path), config)
            loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
            self.assertEqual(loaded['places'], [place])
            self.assertEqual(loaded['deleted_places'], config['deleted_places'])
            with patch.object(poi_studio, '_resolve_city_path', return_value=str(path)):
                poi_studio.save_city_data(str(path), new_pois=[], new_places=[place])
            self.assertEqual(yaml.safe_load(path.read_text(encoding='utf-8'))['places'], [place])
            self.assertEqual(serialize_places([dict(name='Legacy', loc=[-100, 25.7])])[0]['source'], 'UNKNOWN')


class TestBoundedOsmExtraction(unittest.TestCase):
    def test_bbox_is_extracted_before_filter_and_only_complete_json_is_published(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.osm.pbf', root / 'places.geojson'
            source.write_bytes(b'fixture')
            commands = []
            def run(command, **kwargs):
                commands.append(command)
                output = Path(command[command.index('-o') + 1])
                if command[1] == 'export':
                    output.write_text(json.dumps(dict(type='FeatureCollection', features=[])))
                else:
                    output.write_bytes(b'temporary')
                return SimpleNamespace(returncode=0, stderr='')
            diagnostics = {}
            with patch('shutil.which', side_effect=lambda name: '/fake/osmium' if name == 'osmium' else None), \
                 patch('subprocess.run', side_effect=run):
                self.assertTrue(toponymy.run_osmium_places_filter(str(source), str(target),
                    bbox=[-102, 24, -98, 28], diagnostics=diagnostics))
            self.assertEqual([c[1] for c in commands], ['extract', 'tags-filter', 'export'])
            self.assertEqual(commands[0][commands[0].index('-b') + 1], '-102,24,-98,28')
            self.assertEqual(diagnostics['status'], 'ok')
            self.assertEqual({p.name for p in root.iterdir()}, {'source.osm.pbf', 'places.geojson'})

    def test_failure_preserves_previous_output_and_reports_the_cause(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source.osm.pbf', root / 'places.geojson'
            source.write_bytes(b'fixture')
            target.write_text('previous complete output')
            diagnostics = {}
            with patch('shutil.which', side_effect=lambda name: '/fake/osmium' if name == 'osmium' else None), \
                 patch('subprocess.run', return_value=SimpleNamespace(returncode=2, stderr='Source unreadable')):
                self.assertFalse(toponymy.run_osmium_places_filter(str(source), str(target),
                    bbox=[-102, 24, -98, 28], diagnostics=diagnostics))
            self.assertEqual(target.read_text(), 'previous complete output')
            self.assertEqual(diagnostics['status'], 'error')
            self.assertIn('Source unreadable', diagnostics['message'])


if __name__ == '__main__':
    unittest.main()
