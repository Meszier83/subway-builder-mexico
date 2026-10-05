"""Regression checks for source years, compact HTTP, and saved factor provenance."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler
from http.server import ThreadingHTTPServer

import yaml

from tools import wizard


class ConapoPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data' / 'test'
        self.data.mkdir(parents=True)
        self.city = self.root / 'test.yaml'
        self.cfg = dict(city=dict(code='TST', name='Test', bbox=[-87, 20, -86, 22]),
                        data_dir=str(self.data), macroeconomics=dict(projection_year=2024))
        self.write_config()
        self.source = self.data / 'conapo.csv'
        self.source.write_text('CLAVE,ANO,POB_TOTAL\n23005,2020,100\n23005,2024,115\n23005,2026,150\n', encoding='utf-8')
        self.patches = [patch.object(wizard, 'ROOT_DIR', str(self.root)),
                        patch.object(wizard, 'DATA_DIR', str(self.root / 'data'))]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def write_config(self):
        self.city.write_text(yaml.safe_dump(self.cfg), encoding='utf-8')

    def test_years_do_not_load_business_or_census_or_write_config(self):
        before = self.city.read_bytes()
        with patch('sb_mexico.inegi.load_denue', side_effect=AssertionError('loaded DENUE')), \
                patch('sb_mexico.population_projection.municipal_census_totals', side_effect=AssertionError('loaded CPV')):
            result = wizard.inspect_conapo_years(str(self.city))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['available_years'], [2020, 2024, 2026])
        self.assertEqual(before, self.city.read_bytes())

    def test_yearless_confirmation_invalid_years_and_exclusion(self):
        self.source.write_text('CLAVE,POB_TOTAL\n23005,150\n')
        self.assertEqual(wizard.inspect_conapo_years(str(self.city))['status'], 'needs_source_year')
        self.assertEqual(wizard.inspect_conapo_years(str(self.city), source_year='2024')['available_years'], [2024])
        self.cfg['macroeconomics']['conapo_source_year'] = 2026
        self.write_config()
        self.assertEqual(wizard.inspect_conapo_years(str(self.city))['available_years'], [2026])
        self.assertEqual(wizard.inspect_conapo_years(str(self.city), source_year='')['status'], 'needs_source_year')
        for year in ('', 'NaN', '2024.5', 'inf', '1800'):
            self.source.write_text(f'CLAVE,ANO,POB_TOTAL\n23005,{year},150\n')
            self.assertEqual(wizard.inspect_conapo_years(str(self.city))['status'], 'error', year)
        self.cfg['data_exclusions'] = ['conapo.csv']
        self.write_config()
        self.assertEqual(wizard.inspect_conapo_years(str(self.city))['status'], 'missing_conapo')

    def test_latin1_source_and_empty_year_column(self):
        self.source.write_bytes('CLAVE,AÑO,POB_TOTAL,NOM_MUN\n23005,2026,150,Mérida\n'.encode('latin1'))
        self.assertEqual(wizard.inspect_conapo_years(str(self.city))['available_years'], [2026])
        self.source.write_text('CLAVE,ANO,POB_TOTAL\n')
        self.assertEqual(wizard.inspect_conapo_years(str(self.city))['status'], 'error')

    def test_automatic_proposal_preserves_manual_build_contract_and_compacts_diagnostics(self):
        self.cfg['macroeconomics']['growth_factors'] = {'23005': 1.23}
        self.write_config()
        saved = self.city.read_bytes()
        manual = wizard.calculate_conapo_factors(str(self.city), 2026)
        automatic = wizard.calculate_conapo_factors(str(self.city), 2026, automatic=True)
        self.assertEqual(manual['factors'][0]['factor'], 1.23)
        self.assertEqual(automatic['factors'][0]['factor'], 1.5)
        self.assertNotIn('municipalities', automatic['diagnostics'])
        self.assertNotIn('municipality_names', automatic['diagnostics'])
        self.assertEqual(self.city.read_bytes(), saved)

    def test_provenance_survives_save_reload(self):
        self.cfg['macroeconomics'].update(growth_factors={'23005': 1.15}, growth_factor_sources={
            '23005': dict(kind='conapo', factor=1.15, ano=2024, requested_year=2024,
                          name='Mérida', source_file='conapo.csv', denominator='CONAPO population 2020 fallback')})
        wizard.save_full_city_data(str(self.city), self.cfg)
        reloaded = wizard.load_city_data(str(self.city))
        self.assertEqual(reloaded['macroeconomics']['growth_factor_sources'], self.cfg['macroeconomics']['growth_factor_sources'])
        self.assertEqual(reloaded['macroeconomics']['growth_factors'], {'23005': 1.15})

    def test_failed_atomic_save_preserves_previous_configuration(self):
        before = self.city.read_bytes()
        self.cfg['macroeconomics']['projection_year'] = 2026
        with patch.object(wizard.os, 'replace', side_effect=OSError('write refused')):
            with self.assertRaises(OSError):
                wizard.save_full_city_data(str(self.city), self.cfg)
        self.assertEqual(self.city.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.wizard-*.tmp')), [])

    def test_http_pages_are_complete_small_and_reject_changed_results(self):
        rows = [dict(cve_mun=f'{index:05d}', name='Municipio Mérida ' + 'x' * 200,
                     ano=2024, factor=1.15, pob_2020=100, pob_conapo=115,
                     denominator='CONAPO population 2020 fallback') for index in range(1, 451)]
        result = dict(status='ok', requested_year=2024, projection_year=2024,
                      available_years=[2020, 2024], conapo_file='conapo.csv', factors=rows, diagnostics={})
        server = ThreadingHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        # Match the browser's persistent HTTP/1.1 transport. urllib unconditionally
        # forces Connection: close, the filtered Windows path that caused the bug.
        import http.client
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        base = f'http://127.0.0.1:{server.server_port}'
        endpoint = base + '/api/conapo/calculate?file=test.yaml&mode=automatic'
        try:
            with patch.object(wizard, 'calculate_conapo_factors', return_value=result) as calculate:
                cursor, token, received, pages = 0, None, [], 0
                while cursor is not None:
                    url = endpoint + f'&cursor={cursor}' + (f'&result_token={token}' if token else '')
                    client.request('GET', url[len(base):], headers={'Connection': 'keep-alive'})
                    with client.getresponse() as response:
                        self.assertEqual(response.status, 200)
                        raw = response.read()
                        self.assertEqual(len(raw), int(response.headers['Content-Length']))
                        self.assertEqual(response.headers['Connection'], 'keep-alive')
                        self.assertLessEqual(len(raw), 24000)
                        page = json.loads(raw)
                    received.extend(page['factors'])
                    token = page['result_token']
                    cursor = page['next_cursor']
                    pages += 1
                self.assertGreater(pages, 1)
                self.assertEqual(received, rows)
                self.assertEqual(page['total_factor_count'], len(rows))
                self.assertTrue(calculate.call_args.kwargs['automatic'])
                rows[0]['factor'] = 1.2
                client.request('GET', endpoint[len(base):] + f'&cursor=1&result_token={token}')
                with client.getresponse() as changed:
                    self.assertEqual(changed.status, 409)
                    changed.read()
                client.request('GET', endpoint[len(base):] + '&cursor=-1')
                with client.getresponse() as invalid:
                    self.assertEqual(invalid.status, 400)
                    invalid.read()
            client.request('GET', '/api/conapo/years?file=test.yaml')
            with client.getresponse() as response:
                self.assertEqual(json.load(response)['available_years'], [2020, 2024, 2026])
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
