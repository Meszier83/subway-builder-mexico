"""Historical targets, source binding and the actual Wizard preview/build path."""
import copy
import json
import os
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

import test_historical_benchmark as benchmark_fixtures
import test_residential_employment as census_fixtures
from sb_mexico.historical_transfer import FORMULA_VERSION, validate_transfer_contract
from sb_mexico.workplace_employment import load_workplaces, source_hashes
from tools import wizard, poi_studio


class HistoricalTransferTests(unittest.TestCase):
    def setUp(self):
        self.fixture = benchmark_fixtures.HistoricalBenchmarkTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.rows.append(f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10'))
        f.write()

    def contract(self):
        f = self.fixture
        c = f.contract()
        c.update(role='historical_transfer', enabled=True, formula_version=FORMULA_VERSION,
                 strength=1, source_sha256=dict(denue=source_hashes([f.denue]), ce=source_hashes([f.ce])))
        c['groups'][0]['reporting_unit_evidence'] = 'Ordinary manufacturing establishment fixture'
        return c

    def load(self, contract=None, bbox=None):
        f = self.fixture
        return load_workplaces([f.denue], bbox or f.bbox,
            dict(workplace_employment='historical_transfer', historical_workplace_transfer=contract or self.contract()),
            source_root=f.root)

    def test_mean_transferred_not_historical_total_and_fit_before_bbox(self):
        frame, _, report = self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(), 4)
        row = report['controls'][0]
        self.assertEqual(row['ce_units'], 3)
        self.assertEqual(row['establishments'], 2)
        self.assertAlmostEqual(row['historical_target'], 8)
        self.assertAlmostEqual(row['full_scope_attraction'], 8)
        self.assertAlmostEqual(row['outside_bbox_attraction'], 4)
        self.assertEqual(report['transferred_establishments'], 2)
        self.assertEqual(report['scope_components']['excluded']['attraction'], 4.48)
        self.assertEqual(report['comparability'], 'CONDITIONAL_HISTORICAL_MODEL')

    def test_zero_strength_is_exact_band_prior_and_half_strength(self):
        f = self.fixture
        c = self.contract(); c['strength'] = 0
        prior = load_workplaces([f.denue], f.bbox, dict(workplace_employment='ce_bounded'))[0]
        actual = self.load(c)[0]
        self.assertEqual(prior.calibrated_jobs.tolist(), actual.calibrated_jobs.tolist())
        c['strength'] = .5
        self.assertAlmostEqual(self.load(c)[0].calibrated_jobs.sum(), (2.24+4)/2)

    def test_infeasible_finer_band_mix_retains_prior_even_at_half_strength(self):
        f = self.fixture
        raw = pd.read_csv(f.denue); raw.loc[:1, 'per_ocu'] = '6 a 10 personas'; raw.to_csv(f.denue, index=False)
        for strength in (1, .5):
            c = self.contract(); c['strength'] = strength
            frame, _, report = self.load(c)
            self.assertEqual(frame.calibrated_jobs.sum(), 7.75)
            self.assertEqual(report['controls'][0]['status'], 'INFEASIBLE_HISTORICAL_TARGET')

    def test_suppressed_missing_and_confidentiality_are_not_imputed(self):
        f = self.fixture
        for jobs, stratum, expected in [(None, '0 a 10', 'SUPPRESSED_CE_CONTROL'),
                                        (12, 'Agrupados por confidencialidad', 'MISSING_CE_CONTROL')]:
            f.rows[-1] = f.row('Sector 31-33 Industrias', jobs, 3, Estrato=stratum); f.write()
            frame, _, report = self.load()
            self.assertEqual(frame.calibrated_jobs.sum(), 2.24)
            self.assertEqual(report['controls'][0]['status'], expected)

    def test_unknown_bands_and_excluded_records_retain_prior(self):
        f = self.fixture
        raw = pd.read_csv(f.denue); raw.loc[0, 'per_ocu'] = 'unknown'; raw.to_csv(f.denue, index=False)
        frame, _, report = self.load()
        self.assertEqual(frame.calibrated_jobs.sum(), 2.24)
        self.assertEqual(report['unknown_band_establishments'], 1)
        self.assertEqual(report['transferred_establishments'], 1)
        self.assertEqual(report['scope_components']['excluded']['attraction'], 4.48)

    def test_open_band_preserved(self):
        f = self.fixture
        raw = pd.read_csv(f.denue); raw.loc[:1, 'per_ocu'] = '251 y más personas'; raw.to_csv(f.denue, index=False)
        f.rows[-1] = f.row('Sector 31-33 Industrias', 2400, 3, Estrato='251 y más'); f.write()
        frame, _, report = self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(), 800)
        self.assertTrue(report['controls'][-1]['open_upper_bound'])

    def test_stale_or_mutating_sources_rejected(self):
        f = self.fixture
        c = self.contract(); f.rows[-1]['H001A Personal ocupado total'] = 15; f.write()
        with self.assertRaisesRegex(ValueError, 'sources changed'): self.load(c)
        c = self.contract()
        from sb_mexico import workplace_employment
        real = workplace_employment.source_hashes
        calls = 0
        def edited(paths):
            nonlocal calls
            calls += 1
            return ['0'*64] if calls == 4 else real(paths)
        with patch.object(workplace_employment, 'source_hashes', side_effect=edited):
            with self.assertRaisesRegex(ValueError, 'changed during'): self.load(c)

    def test_contract_requires_explicit_groups_units_and_supported_sectors(self):
        for key, value in [('enabled', False), ('strength', float('nan')), ('source_sha256', None), ('groups', [])]:
            c = self.contract(); c[key] = value
            with self.assertRaises(ValueError): validate_transfer_contract(c)
        c = self.contract(); c['groups'][0]['reporting_unit'] = 'enterprise_or_special'
        with self.assertRaises(ValueError): validate_transfer_contract(c)
        c = self.contract(); c['groups'][0]['scian_prefixes'] = ['52']
        with self.assertRaises(ValueError): validate_transfer_contract(c)
        c = self.contract(); c['groups'].append(copy.deepcopy(c['groups'][0]))
        with self.assertRaisesRegex(ValueError, 'Overlapping'): validate_transfer_contract(c)

    def test_inspector_preflight_does_not_activate_or_write_city(self):
        f = self.fixture
        cfg = dict(city=dict(name='Fixture', code='fixture', bbox=[-87,20,-86,22]), data_dir='.', macroeconomics={})
        city = f.root/'fixture.yaml'
        with patch.object(wizard, 'ROOT_DIR', str(f.root)):
            wizard.save_full_city_data(str(city), cfg)
            before = city.read_bytes()
            result = wizard.inspect_workplace_benchmark(str(city), f.contract())
            self.assertEqual(result['report']['transfer_preflight']['transferred_establishments'], 2)
            self.assertFalse(result['contract']['enabled'])
            self.assertEqual(city.read_bytes(), before)

    def test_wizard_save_preview_cache_build_and_stale_same_size_edit(self):
        f = census_fixtures.CensusEmploymentTests(); f.setUp(); self.addCleanup(f.doCleanups)
        path, cfg, data = f.fixture_config()
        denue = data/'denue.csv'
        raw = pd.read_csv(denue); raw['codigo_act'] = '311110'; raw['nombre_act'] = 'Manufactura'; raw.to_csv(denue,index=False)
        # This source sits outside automatic data discovery, as Wizard selections do.
        ce = f.root/'selected.csv'
        pd.DataFrame([{'Año Censal':2023,'Entidad':'23 Quintana Roo','Municipio':'005 Benito Juarez',
                       'Actividad económica':'Sector 31-33 Industrias','Estrato':'11 a 50',
                       'H001A Personal ocupado total':25,'UE Unidades económicas':1}]).to_csv(ce,index=False)
        c = self.contract()
        c.update(ce_sources=['selected.csv'],source_sha256=dict(denue=source_hashes([denue]),ce=source_hashes([ce])))
        cfg['macroeconomics'].update(workplace_employment='historical_transfer',historical_workplace_transfer=c)
        wizard.save_full_city_data(str(path),cfg)
        self.assertEqual(yaml.safe_load(path.read_text())['macroeconomics']['historical_workplace_transfer'], c)
        diag = {}
        with patch.object(poi_studio, 'ROOT_DIR', str(f.root)):
            points = poi_studio.load_demand_sample(city_file=str(path),diagnostics=diag)
            self.assertEqual(points, poi_studio.load_demand_sample(city_file=str(path)))
        self.assertAlmostEqual(diag['workplace_employment']['bbox_attraction'],50)
        from sb_mexico.pipeline import execute_pipeline
        output = f.root/'out'
        with patch('sb_mexico.pipeline.ROOT_DIR',str(f.root)), patch('sb_mexico.pipeline.console'), patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
            execute_pipeline(str(path),skip_map=True,output_dir=str(output))
        report = json.loads((output/'workplace_employment_report.json').read_text())
        self.assertEqual(report['controls'],diag['workplace_employment']['controls'])
        self.assertEqual(report['source_sha256'],diag['workplace_employment']['source_sha256'])
        self.assertEqual(report['simulation']['exported_commuters'],96)
        demand = json.loads((output/'demand_data.json').read_text())
        self.assertTrue(all('workplace_employment' not in p for p in demand['points']))
        stat = ce.stat(); ce.write_text(ce.read_text().replace(',25,1',',26,1')); os.utime(ce,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with patch.object(poi_studio, 'ROOT_DIR', str(f.root)):
            with self.assertRaisesRegex(ValueError, 'sources changed'): poi_studio.load_demand_sample(city_file=str(path))
        with patch('sb_mexico.pipeline.ROOT_DIR',str(f.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.build_city_map') as cartography:
            with self.assertRaisesRegex(ValueError, 'sources changed'):
                execute_pipeline(str(path),skip_map=False,output_dir=str(output))
            cartography.assert_not_called()


if __name__ == '__main__': unittest.main()
