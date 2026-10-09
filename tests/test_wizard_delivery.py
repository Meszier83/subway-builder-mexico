import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import zipfile

import pandas as pd
import yaml

from sb_mexico.build_delivery import execute_wizard_build, resolve_download, resolve_preview_roads
from sb_mexico.population_projection import resolve_population_factors
from tools import wizard, poi_studio


class WizardDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data' / 'test'
        self.data.mkdir(parents=True)
        self.out = self.root / 'dist' / 'test'
        self.config = self.root / 'test.yaml'
        self.cfg = dict(city=dict(code='TST', name='Test', description='Test',
                                  bbox=[-87, 20, -86, 22], grid_size=.0025),
                        data_dir=str(self.data), macroeconomics=dict(tasa_pea=.65, til_1_state=.45,
                                                                    projection_year=2024))
        self.save()
        self.patches = [patch.object(wizard, 'ROOT_DIR', str(self.root)),
                        patch.object(wizard, 'DATA_DIR', str(self.root / 'data')),
                        patch.object(wizard, 'DIST_DIR', str(self.root / 'dist')),
                        patch.object(poi_studio, 'ROOT_DIR', str(self.root))]
        for item in self.patches:
            item.start()
        wizard.active_build.update(running=False, result=None)

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def save(self):
        self.config.write_text(yaml.safe_dump(self.cfg), encoding='utf-8')

    def sources(self):
        pd.DataFrame([dict(ENTIDAD=23, MUN=5, LOC='0001', AGEB='001A', MZA=1,
                           POBTOT=100, P_15YMAS=80, P_12YMAS=80, POCUPADA=50)]).to_csv(self.data / 'cpv.csv', index=False)
        pd.DataFrame([dict(id=100, cve_ent=23, cve_mun=5, ageb='001A', manzana=1,
                           longitud=-86.8, latitud=21.1, per_ocu='11 a 30 personas')]).to_csv(self.data / 'denue.csv', index=False)
        pd.DataFrame([dict(CLAVE=23005, ANO=year, POB_TOTAL=population)
                      for year, population in [(2020, 100), (2024, 115), (2026, 150)]]).to_csv(self.data / 'conapo.csv', index=False)

    def fake_pipeline(self, config_path, output_dir, **kwargs):
        output = Path(output_dir)
        demand = dict(points=[dict(id='r', location=[-86.8, 21.1], residents=10, jobs=10, popIds=['p'])],
                      pops=[dict(id='p', size=10, residenceId='r', jobId='r')])
        (output / 'demand_data.json').write_text(json.dumps(demand))
        (output / 'config.json').write_text('{}')
        if not (output / 'TST.pmtiles').exists():
            return str(output / 'demand_data.json')
        package = output / 'TST.zip'
        with zipfile.ZipFile(package, 'w') as archive:
            for name in ['config.json', 'demand_data.json', 'TST.pmtiles', 'roads.geojson']:
                archive.write(output / name, name)
        return str(package)

    def assets(self):
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / 'TST.pmtiles').write_bytes(b'test cartography')
        (self.out / 'roads.geojson').write_text('{"type":"FeatureCollection","features":[]}')

    def test_old_zip_missing_map_and_failure_cannot_download(self):
        self.out.mkdir(parents=True)
        (self.out / 'old.zip').write_bytes(b'old')
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
            result = execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        self.assertEqual(result['status'], 'demand_only')
        self.assertEqual(set(result['missing_map_files']), {'TST.pmtiles', 'roads.geojson'})
        with self.assertRaises(ValueError):
            resolve_download(self.config, self.out)
        self.assets()
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
            result = execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        self.assertEqual(resolve_download(self.config, self.out)[0], Path(result['package_path']))
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=RuntimeError('packaging failed')):
            with self.assertRaises(RuntimeError):
                execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        with self.assertRaises(ValueError):
            resolve_download(self.config, self.out)

    def test_config_change_project_mismatch_and_tampering(self):
        self.assets()
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
            result = execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        another = self.root / 'other.yaml'
        another.write_bytes(self.config.read_bytes())
        with self.assertRaisesRegex(ValueError, 'another project'):
            resolve_download(another, self.out)
        self.cfg['city']['name'] = 'Changed'
        self.save()
        with self.assertRaises(ValueError):
            resolve_download(self.config, self.out)
        self.cfg['city']['name'] = 'Test'
        self.save()
        Path(result['package_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since'):
            resolve_download(self.config, self.out)

    def test_candidate_preview_uses_built_roads_and_route_cache_is_reused(self):
        self.assets()
        self.cfg['demand'] = {'engine': 'v2', 'target_year': 2020}
        self.cfg['routing'] = {'osrm_url': 'http://127.0.0.1:9999',
                               'network_identity': 'fixture', 'include_driving_path': False}
        self.save()
        wizard.save_full_city_data(str(self.config), self.cfg)
        self.assertEqual(wizard.load_city_data(str(self.config))['routing'], self.cfg['routing'])
        cache = {'network-coordinate-key': {'drivingDistance': 123, 'drivingSeconds': 45}}
        (self.out / '.demand-v2-routes.json').write_text(json.dumps(cache))
        def run(config_path, output_dir, **kwargs):
            self.assertEqual(json.loads((Path(output_dir)/'.demand-v2-routes.json').read_text()), cache)
            return self.fake_pipeline(config_path, output_dir, **kwargs)
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=run):
            result = execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        built = Path(result['output_dir'])/'roads.geojson'
        self.assertEqual(resolve_preview_roads(self.config, self.out), built)
        # A project with the same contents must not inherit this build's roads.
        other=self.root/'other-preview.yaml'; other.write_bytes(self.config.read_bytes())
        self.assertEqual(resolve_preview_roads(other,self.out),self.out/'roads.geojson')
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=run):
            execute_wizard_build(self.config, self.out, self.data, skip_map=True)

    def test_http_status_download_and_demand_only(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), wizard.WizardRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        import http.client
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        def read(path, expected=200):
            client.request('GET', path, headers={'Connection': 'keep-alive'})
            with client.getresponse() as response:
                self.assertEqual(response.status, expected)
                return response.read()
        try:
            with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
                wizard.run_pipeline_task(str(self.config), skip_map=True)
            status = json.loads(read('/api/build/status'))
            self.assertEqual(status['status'], 'demand_only')
            preview = json.loads(read('/api/demand-preview?file=test.yaml'))
            self.assertFalse(preview['metadata']['package_available'])
            read('/api/download?file=test.yaml', 409)
            self.assets()
            with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
                wizard.run_pipeline_task(str(self.config), skip_map=True)
            result = wizard.active_build['result']
            self.assertEqual(read('/api/download?file=test.yaml'), Path(result['package_path']).read_bytes())
            preview = json.loads(read('/api/demand-preview?file=test.yaml'))
            self.assertEqual(sum(pop['size'] for pop in preview['pops']), 10)
            self.assertTrue(preview['metadata']['package_available'])
            wizard.active_build['running'] = True
            read('/api/download?file=test.yaml', 409)
        finally:
            wizard.active_build['running'] = False
            client.close()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_shared_factors_census_total_fallback_manual_and_yearless(self):
        self.sources()
        report = {}
        cpv = self.data / 'cpv.csv'
        conapo = self.data / 'conapo.csv'
        factors = resolve_population_factors(conapo, [cpv], self.cfg['macroeconomics'], report)
        self.assertEqual(factors['23005'], 1.15)
        self.assertIn('fallback', report['municipalities']['23005']['denominator'])
        with cpv.open('a') as stream:
            stream.write('23,5,0000,0000,0,110,80\n')
        factors = resolve_population_factors(conapo, [cpv, cpv], self.cfg['macroeconomics'], report)
        self.assertEqual(factors['23005'], 1.0455)
        self.assertEqual(wizard.calculate_conapo_factors(str(self.config))['factors'][0]['factor'], factors['23005'])
        self.cfg['macroeconomics']['growth_factors'] = {'23005': 1.23}
        self.save()
        self.assertEqual(wizard.calculate_conapo_factors(str(self.config))['factors'][0]['factor'], 1.23)
        conapo.write_text('CLAVE,POB_TOTAL\n23005,115\n')
        self.assertEqual(wizard.calculate_conapo_factors(str(self.config))['status'], 'needs_source_year')
        self.cfg['macroeconomics']['conapo_source_year'] = 2024
        self.save()
        self.assertEqual(wizard.calculate_conapo_factors(str(self.config))['projection_year'], 2024)

    def test_preview_matches_source_placement_and_cache_invalidates(self):
        self.sources()
        self.cfg['city']['residential_placement'] = 'official_blocks'
        self.save()
        diagnostics = {}
        points = poi_studio.load_demand_sample(city_file=str(self.config), diagnostics=diagnostics)
        residents = [point for point in points if point['residents']]
        self.assertEqual(len(residents), 1)
        self.assertEqual(residents[0]['location'], [-86.8, 21.1])
        self.assertAlmostEqual(residents[0]['residents'], 115.)
        self.cfg['macroeconomics']['growth_factors'] = {'23005': 1.3}
        self.save()
        new = poi_studio.load_demand_sample(city_file=str(self.config))
        self.assertAlmostEqual(sum(point['residents'] for point in new), 130.)
        self.assertEqual(diagnostics['placement_mode'], 'official_blocks')
        self.cfg['city']['residential_placement'] = 'legacy'
        self.save()
        new = poi_studio.load_demand_sample(city_file=str(self.config), diagnostics=diagnostics)
        self.assertEqual(diagnostics['placement_mode'], 'legacy')
        self.assertAlmostEqual(sum(point['residents'] for point in new), 130.)

    def test_nearest_year_missing_base_and_census_conflict(self):
        self.sources()
        self.cfg['macroeconomics']['projection_year'] = 2025
        report = {}
        factors = resolve_population_factors(self.data / 'conapo.csv', [self.data / 'cpv.csv'],
                                             self.cfg['macroeconomics'], report)
        self.assertEqual(report['effective_year'], 2024)
        self.assertEqual(factors['23005'], 1.15)
        (self.data / 'conapo.csv').write_text('CLAVE,ANO,POB_TOTAL\n23005,2024,115\n')
        factors = resolve_population_factors(self.data / 'conapo.csv', [self.data / 'cpv.csv'],
                                             self.cfg['macroeconomics'], report)
        self.assertEqual(factors['23005'], 1.0)
        self.assertIn('unavailable', report['municipalities']['23005']['denominator'])
        with (self.data / 'cpv.csv').open('a') as stream:
            stream.write('23,5,0000,0000,0,110,80\n23,5,0000,0000,0,111,80\n')
        with self.assertRaisesRegex(ValueError, 'Conflicting municipal'):
            resolve_population_factors(self.data / 'conapo.csv', [self.data / 'cpv.csv'],
                                       self.cfg['macroeconomics'], report)

    def test_changed_bbox_cannot_reuse_prior_cartography(self):
        self.assets()
        with patch('sb_mexico.pipeline.execute_pipeline', side_effect=self.fake_pipeline):
            execute_wizard_build(self.config, self.out, self.data, skip_map=True)
            self.cfg['city']['bbox'] = [-88, 20, -86, 22]
            self.save()
            result = execute_wizard_build(self.config, self.out, self.data, skip_map=True)
        self.assertEqual(result['status'], 'demand_only')
        with self.assertRaises(ValueError):
            resolve_download(self.config, self.out)


if __name__ == '__main__':
    unittest.main()
