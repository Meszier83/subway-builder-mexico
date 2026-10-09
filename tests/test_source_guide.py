"""Source preparation and selection contracts exposed by the Wizard."""
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import yaml

from sb_mexico.demand_sources import select_sources
from sb_mexico.inegi import parse_enoe_indicators
from tools import wizard


class SourceGuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.national = self.root / 'data'
        self.project = self.national / 'test'
        self.project.mkdir(parents=True)
        self.config = self.root / 'test.yaml'
        self.cfg = dict(city=dict(code='TST', name='Test', bbox=[-87, 20, -86, 22],
                                  residential_placement='official_blocks'),
                        data_dir=str(self.project), macroeconomics={})
        self.save()
        self.patches = [patch.object(wizard, 'ROOT_DIR', str(self.root)),
                        patch.object(wizard, 'DATA_DIR', str(self.national))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def save(self):
        self.config.write_text(yaml.safe_dump(self.cfg), encoding='utf-8')

    def inspect(self):
        return wizard.inspect_data_files(city_file=str(self.config))

    def test_zip_only_is_not_a_ready_denue_source(self):
        (self.project / 'denue_inegi_23.zip').write_bytes(b'PK archive')
        (self.project / 'RESAGEBURB_23.csv').write_text('header\n')
        report = self.inspect()
        self.assertEqual(report['denue']['status'], 'archive')
        self.assertEqual(report['denue']['files'], [])
        self.assertFalse(report['all_ready'])
        (self.project / 'denue_inegi_23.csv').write_text('header\n')
        report = self.inspect()
        self.assertEqual(report['denue']['status'], 'ok')
        self.assertEqual(len(report['denue']['archives']), 1)
        self.assertTrue(report['all_ready'])
        self.assertTrue(report['presence_only'])

    def test_shared_inputs_match_build_selection_without_other_cities(self):
        (self.project / 'denue_inegi_23.csv').write_text('header\n')
        (self.project / 'denue_inegi_09.csv').write_text('header\n')
        (self.national / 'conapo.csv').write_text('CLAVE,ANO,POB_TOTAL\n')
        other = self.national / 'other_city'
        other.mkdir()
        (other / 'denue_inegi_15.csv').write_text('header\n')
        self.cfg['data_exclusions'] = ['denue_inegi_09.csv']
        self.save()
        report = self.inspect()
        for key, kind in [('denue', 'denue'), ('cpv', 'cpv'), ('ce2024', 'ce'),
                          ('conapo', 'conapo'), ('enoe', 'enoe')]:
            expected = select_sources(self.project, self.national, kind, self.cfg['data_exclusions'])
            self.assertEqual([f['abs_path'] for f in report[key]['files']], expected)
        self.assertEqual(len(report['denue']['files']), 1)
        self.assertTrue(report['conapo']['files'][0]['shared'])

    def test_first_file_and_exclusion_are_explicit(self):
        for name in ('conapo_a.csv', 'conapo_b.csv'):
            (self.project / name).write_text('CLAVE,ANO,POB_TOTAL\n')
        files = self.inspect()['conapo']['files']
        self.assertEqual([f['selected'] for f in files], [True, False])
        self.cfg['data_exclusions'] = ['conapo_a.csv']
        self.save()
        files = self.inspect()['conapo']['files']
        self.assertEqual(files[0]['filename'], 'conapo_b.csv')
        self.assertTrue(files[0]['selected'])

    def test_official_marco_is_discovered_in_extracted_subdirectories(self):
        folder = self.project / 'marco_2020_23' / 'conjunto_de_datos'
        folder.mkdir(parents=True)
        (folder / '23m.shp').write_bytes(b'candidate')
        report = self.inspect()['marco']
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['files'][0]['filename'], '23m.shp')
        # Presence does not claim that this deliberately invalid shape is readable.
        self.assertTrue(self.inspect()['presence_only'])

    def test_csv_precedence_is_preserved_when_excel_is_added(self):
        (self.project / '2026_trim_2.csv').write_text('header\n')
        (self.project / '2026_trim_1.xls').write_bytes(b'candidate')
        paths = select_sources(self.project, self.national, 'enoe')
        self.assertTrue(paths[0].endswith('.csv'))
        self.assertEqual([f['abs_path'] for f in self.inspect()['enoe']['files']], paths)

    @unittest.skipUnless(importlib.util.find_spec('openpyxl'), 'openpyxl is a project dependency')
    def test_xlsx_is_read_and_selected_by_wizard_and_build(self):
        from openpyxl import Workbook
        path = self.project / 'enoe_2026_trim2.xlsx'
        workbook = Workbook()
        workbook.active.append(['Indicador', 'Total'])
        workbook.active.append(['Tasa de participación', 66.1])
        workbook.active.append(['Tasa de informalidad laboral 1 (TIL1)', 42.8])
        workbook.save(path)
        workbook.close()
        expected = dict(tasa_pea=.661, til_1=.428)
        self.assertEqual(parse_enoe_indicators(str(path)), expected)
        self.assertEqual(select_sources(self.project, self.national, 'enoe'), [str(path.resolve())])
        self.assertEqual(self.inspect()['enoe']['files'][0]['abs_path'], str(path.resolve()))
        result = wizard.detect_macro_parameters(str(self.config))
        self.assertEqual(result['method'], 'enoe_file')
        self.assertEqual(result['parameters']['tasa_pea'], expected['tasa_pea'])
        self.assertEqual(result['parameters']['til_1_state'], expected['til_1'])

    def test_bad_excel_does_not_masquerade_as_reference_rates(self):
        path = self.project / 'enoe_invalid.xls'
        path.write_bytes(b'not a workbook')
        with self.assertRaisesRegex(ValueError, 'No se pudo leer'):
            parse_enoe_indicators(str(path))

    def test_http_zip_upload_preserves_archive_and_reports_preparation(self):
        server = wizard.WizardHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        archive = b'PK raw archive bytes'
        body = (b'--source-test\r\nContent-Disposition: form-data; name="file"; '
                b'filename="denue_23.zip"\r\nContent-Type: application/zip\r\n\r\n' +
                archive + b'\r\n--source-test--\r\n')
        try:
            from urllib.parse import quote
            client.request('POST', '/api/upload?file=' + quote(str(self.config)), body=body,
                           headers={'Content-Type': 'multipart/form-data; boundary=source-test'})
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())['uploaded_count'], 1)
            self.assertEqual((self.project / 'denue_23.zip').read_bytes(), archive)
            client.request('GET', '/api/data-status?file=' + quote(str(self.config)))
            response = client.getresponse()
            self.assertEqual(response.status, 200)
            report = json.loads(response.read())
            self.assertEqual(report['denue']['status'], 'archive')
            self.assertFalse(report['all_ready'])
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
