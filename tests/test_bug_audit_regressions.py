"""Regression tests for bugs identified and fixed during the repository audit."""
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import yaml

from sb_mexico import build_delivery, inegi, toponymy_official
from sb_mexico.demand_v2 import blocked_cohorts, destination_cohorts, municipal_cohorts
from tools import poi_studio, stop_wizard, wizard


class BugAuditRegressionTests(unittest.TestCase):
    def test_searchsorted_clamping_prevents_oob_when_phase_hits_upper_bound(self):
        class _MaxPhaseDigest:
            def hexdigest(self):
                return 'f' * 64

        with patch('hashlib.sha256', return_value=_MaxPhaseDigest()):
            origins = [{'id': 'o0', 'municipality': '23005', 'commuters': 3}]
            destinations = [
                {'id': 'd0', 'municipality': '23005'},
                {'id': 'd1', 'municipality': '23005'},
                {'id': 'd2', 'municipality': '23005'},
            ]
            rows = np.array([0, 0, 0], dtype=np.int64)
            cols = np.array([0, 1, 2], dtype=np.int64)
            flow = np.array([1.0, 2.0, 3.0], dtype=np.float64)
            distance = np.array([1.0, 2.0, 3.0], dtype=np.float64)

            parts, _ = municipal_cohorts.assign(
                flow, rows, cols, origins, destinations, [[1, 1, 1]], seed=42, select_jobs=False
            )
            integer_flow, _, _ = municipal_cohorts.assign(
                flow, rows, cols, origins, destinations, [[1, 1, 1]], seed=42, select_jobs=True
            )
            self.assertEqual(int(integer_flow.sum()), 3)

            rounded_jobs, _, _, _ = destination_cohorts.round_jobs(
                flow, rows, cols, origins, destinations, distance, parts, [{1: parts[0][3]}], seed=42
            )
            self.assertEqual(int(rounded_jobs.sum()), 3)

            dests = destinations

            class _FakeProblem:
                no = 1
                nd = 3
                scale = np.array([1.0, 1.0])
                coordinates = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])
                job_names = ['23005']
                job_mun = np.array([0, 0, 0], dtype=np.int32)
                origins = [{'id': 'o0', 'municipality': '23005', 'location': np.array([0.0, 0.0])}]
                destinations = dests

                def flow_blocks(self, v, w, include_support=False):
                    yield 0, 1, np.array([[1.0, 2.0, 3.0]]), None, np.array([[True, True, True]])

            blocked_parts = [(0, (('23005', 0, None), '23005'), 3)]
            assigned, arrivals, _, _ = blocked_cohorts.transport_parts(
                _FakeProblem(),
                np.zeros(3),
                np.zeros(1),
                blocked_parts,
                size=1,
                seed=42,
            )
            self.assertEqual(int(sum(assigned.values())), 3)
            self.assertEqual(int(arrivals.sum()), 3)

    def test_build_delivery_rejects_stale_cartography_and_stale_demand_after_config_change(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            city_yaml = tmp / 'tst.yaml'
            out_root = tmp / 'dist' / 'tst'
            data_dir = tmp / 'data' / 'tst'
            out_root.mkdir(parents=True)
            data_dir.mkdir(parents=True)

            def write_cfg(bbox):
                city_yaml.write_text(
                    yaml.safe_dump({'city': {'name': 'Test', 'code': 'TST', 'bbox': bbox}}),
                    encoding='utf-8',
                )

            def fake_execute(cfg_path, output_dir, data_dir, skip_map=False, labels_only=False, skip_demand=False, skip_packaging=False):
                cfg = yaml.safe_load(Path(cfg_path).read_text(encoding='utf-8'))
                stage = Path(output_dir)
                if not skip_map:
                    (stage / 'TST.pmtiles').write_bytes(f"MAP_{cfg['city']['bbox']}".encode())
                    (stage / 'roads.geojson').write_text('{"type":"FeatureCollection","features":[]}', encoding='utf-8')
                if not (stage / 'demand_data.json').exists():
                    (stage / 'demand_data.json').write_text('{"points":[],"pops":[]}', encoding='utf-8')
                (stage / 'config.json').write_text('{}', encoding='utf-8')
                if not (stage / 'TST.pmtiles').exists():
                    return str(stage / 'demand_data.json')
                pkg = stage / 'TST.zip'
                with zipfile.ZipFile(pkg, 'w') as zf:
                    for name in ('config.json', 'demand_data.json', 'TST.pmtiles', 'roads.geojson'):
                        zf.write(stage / name, name)
                return str(pkg)

            with patch('sb_mexico.pipeline.execute_pipeline', side_effect=fake_execute):
                write_cfg([0, 0, 1, 1])
                r1 = build_delivery.execute_wizard_build(city_yaml, out_root, data_dir)
                self.assertEqual(r1['status'], 'success')

                write_cfg([2, 2, 3, 3])
                with patch('sb_mexico.pipeline.execute_pipeline', side_effect=RuntimeError('transient failure')):
                    with self.assertRaises(RuntimeError):
                        build_delivery.execute_wizard_build(city_yaml, out_root, data_dir)

                r3 = build_delivery.execute_wizard_build(city_yaml, out_root, data_dir, skip_map=True)
                self.assertEqual(r3['status'], 'demand_only')
                self.assertEqual(r3['reused_cartography'], {})

                write_cfg([9, 9, 10, 10])
                r4 = build_delivery.execute_wizard_build(city_yaml, out_root, data_dir, skip_demand=True)
                self.assertEqual(r4['reused_demand'], {})

    def test_upload_preserves_trailing_crlf_and_handles_quoted_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / 'data'
            city_data = data_dir / 'tst'
            cities = root / 'cities'
            city_data.mkdir(parents=True)
            cities.mkdir(parents=True)
            cfg = cities / 'tst.yaml'
            cfg.write_text(yaml.safe_dump({'city': {'code': 'TST', 'name': 'Test'}, 'data_dir': str(city_data)}), encoding='utf-8')

            payload = b'\x89PNG\r\n\x1a\nBINARY_PAYLOAD_WITH_CRLF\r\n'
            boundary = '----TestBoundary123'
            body = (
                f'--{boundary}\r\n'
                'Content-Disposition: form-data; name="file"; filename="sample.bin"\r\n'
                'Content-Type: application/octet-stream\r\n\r\n'
            ).encode('utf-8') + payload + f'\r\n--{boundary}--\r\n'.encode('utf-8')

            with patch.object(wizard, 'ROOT_DIR', str(root)), \
                 patch.object(wizard, 'DATA_DIR', str(data_dir)), \
                 patch.object(wizard, 'CITIES_DIR', str(cities)):
                server = wizard.WizardHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
                try:
                    from urllib.parse import quote
                    client.request(
                        'POST',
                        '/api/upload?file=' + quote(str(cfg)),
                        body=body,
                        headers={'Content-Type': f'multipart/form-data; boundary="{boundary}"'},
                    )
                    resp = client.getresponse()
                    self.assertEqual(resp.status, 200)
                    info = json.loads(resp.read())
                    self.assertEqual(info['uploaded_count'], 1)
                    self.assertEqual((city_data / 'sample.bin').read_bytes(), payload)
                finally:
                    client.close()
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=5)

    def test_path_containment_cancun_alias_and_create_project_guards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / 'repo'
            cities = root / 'cities'
            data_dir = root / 'data'
            evil = Path(td) / 'repo-evil'
            cities.mkdir(parents=True)
            data_dir.mkdir(parents=True)
            evil.mkdir(parents=True)
            (cities / 'cancun_riviera_maya.yaml').write_text('city: {code: CUR}\n', encoding='utf-8')
            evil_yaml = evil / 'outside.yaml'
            evil_yaml.write_text('city: {code: EVIL}\n', encoding='utf-8')

            with patch.object(wizard, 'ROOT_DIR', str(root)), \
                 patch.object(wizard, 'CITIES_DIR', str(cities)), \
                 patch.object(wizard, 'DATA_DIR', str(data_dir)), \
                 patch.object(poi_studio, 'ROOT_DIR', str(root)):
                with self.assertRaises(PermissionError):
                    wizard._resolve_city_path(str(evil_yaml))
                with self.assertRaises(PermissionError):
                    wizard.open_file_location(str(evil_yaml))
                with self.assertRaises(PermissionError):
                    poi_studio._resolve_city_path(str(evil_yaml))

                # Legacy exact alias remains supported, substring false alias does not.
                self.assertEqual(Path(wizard._resolve_city_path('cities/cancun.yaml')).name, 'cancun_riviera_maya.yaml')
                self.assertEqual(Path(wizard._resolve_city_path('cities/other_cancun.yaml')).name, 'other_cancun.yaml')

                wizard.create_new_project('Mi Ciudad', 'MCD')
                with self.assertRaises(ValueError):
                    wizard.create_new_project('Mi Ciudad', 'MCD')
                with self.assertRaises(PermissionError):
                    wizard.create_new_project('Evil Dir', 'EVL', data_dir=str(evil))

    def test_toponymy_official_and_conapo_csv_encoding(self):
        with tempfile.TemporaryDirectory() as td:
            bad_csv = Path(td) / 'bad.csv'
            bad_csv.write_bytes(b'NOMBRE,ENT\r\nTest\x81,\xff\r\n')
            rows = list(toponymy_official._records(bad_csv, 'inegi'))
            self.assertEqual(len(rows), 1)

            conapo_csv = Path(td) / 'conapo.csv'
            conapo_csv.write_bytes('ANO,CLAVE,NOM_MUN,POB_MIT_MUN\n2025,23005,José “Demo”,950000\n'.encode('cp1252'))
            diag = {}
            res = inegi.parse_conapo_projections(str(conapo_csv), target_year=2025, diagnostics=diag)
            self.assertEqual(res['23005'], 950000.0)
            self.assertEqual(diag['municipality_names']['23005'], 'José “Demo”')

    def test_yaml_serialization_validation_and_exclusions(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as td:
            city_yaml = Path(td) / 'test_city.yaml'
            wizard.save_full_city_data(str(city_yaml), {
                'city': {'name': 'Test', 'code': 'TST', 'bbox': [-99.2, 19.3, -99.0, 19.5]},
                'data_exclusions': None,
                'pois': [{
                    'id': 'UNI_Demo',
                    'name': {'__default__': 'Campus Default'},
                    'loc': [-99.1, 19.4],
                    'jobs': 5000,
                    'radius_m': 800,
                    'source': 'manual_special',
                    'sibling_point_ids': ['UNI_Demo_B'],
                }],
            })
            loaded = wizard.load_city_data(str(city_yaml))
            self.assertEqual(loaded['pois'][0]['name'], {'__default__': 'Campus Default'})
            self.assertEqual(loaded['pois'][0]['source'], 'manual_special')
            self.assertEqual(loaded['pois'][0]['sibling_point_ids'], ['UNI_Demo_B'])

            wizard.exclude_data_file(str(city_yaml), 'denue_inegi_09.csv')
            self.assertEqual(wizard.load_city_data(str(city_yaml))['data_exclusions'], ['denue_inegi_09.csv'])

            zones_yaml = poi_studio._format_affluence_zones_yaml([
                {'id': 'AZ_1', 'name': 'Zona "Sur" \\ Centro', 'type': 'polygon'}
            ])
            parsed_zones = yaml.safe_load(zones_yaml)
            self.assertEqual(parsed_zones['affluence_zones'][0]['name'], 'Zona "Sur" \\ Centro')

            bad_jobs_yaml = Path(td) / 'bad_jobs.yaml'
            bad_jobs_yaml.write_text(yaml.safe_dump({
                'city': {'name': 'Test', 'code': 'TST', 'bbox': [-99.2, 19.3, -99.0, 19.5]},
                'pois': [{'id': 'COM_Test', 'loc': [-99.1, 19.4], 'jobs': ''}],
            }), encoding='utf-8')
            with patch('tools.wizard.inspect_data_files', return_value={}):
                validation = wizard.validate_city_configuration(str(bad_jobs_yaml))
            self.assertFalse(validation['valid'])
            self.assertTrue(any('Valor inválido' in err for err in validation['errors']))

    def test_stop_wizard_patterns_do_not_match_generic_substrings(self):
        self.assertNotIn('sb_mexico', stop_wizard.TARGET_PATTERNS)
        self.assertNotIn('wizard.py', stop_wizard.TARGET_PATTERNS)
        self.assertNotIn('build.py', stop_wizard.TARGET_PATTERNS)


if __name__ == '__main__':
    unittest.main()
