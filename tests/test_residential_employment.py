"""Production-path falsifiers for opt-in census employment and its provenance."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import yaml

from sb_mexico.inegi import load_cpv_demography
from sb_mexico.pipeline import execute_pipeline, load_city_config
from tools import poi_studio, wizard


class CensusEmploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wizard_root = patch.object(wizard, 'ROOT_DIR', str(self.root))
        self.wizard_root.start()
        self.addCleanup(self.wizard_root.stop)
        self.bbox = dict(min_lon=-87, max_lon=-86, min_lat=20, max_lat=22)

    def row(self, mza=1, occupied=10, population=100, p12=80, **extra):
        return dict(ENTIDAD=23, MUN=5, LOC='0001', AGEB='001A', MZA=mza,
                    POBTOT=population, P_12YMAS=p12, P_15YMAS=80,
                    POCUPADA=occupied, **extra)

    def load(self, rows, **kwargs):
        path = self.root / 'cpv.csv'
        pd.DataFrame(rows).to_csv(path, index=False)
        denue = pd.DataFrame([dict(cve_mun_clean='23005', cve_loc='0001',
            ageb_clean=row['AGEB'], mza_clean=str(row['MZA']),
            lon=row.get('_lon', -86.8), lat=21.1, calibrated_jobs=100)
            for row in rows if int(row['MZA']) > 0])
        options = dict(cpv_paths=str(path), df_denue=denue, bbox=self.bbox, tasa_pea=.65,
                       placement_mode='official_blocks', employment_mode='census_employed',
                       projection_year=2026)
        options.update(kwargs)
        return load_cpv_demography(**options)

    def test_published_employment_not_active_rate_and_projection_separate(self):
        result = self.load([self.row(1, 10, PEA=12), self.row(2, 70, PEA=75)],
                           tasa_pea=.1, growth_factors={'23005': 1.2})
        self.assertEqual(result.published_employed_2020.tolist(), [10, 70])
        self.assertEqual(result.employed_2020.tolist(), [10, 70])
        self.assertEqual(result.pea_real.tolist(), [12, 84])
        report = result.attrs['residential_employment']
        self.assertEqual(report['input']['published_employed_2020'], 80)
        self.assertEqual(report['input']['projected_employed'], 96)
        self.assertEqual(report['projection']['model_year'], 2026)
        self.assertEqual(len(report['sources'][0]['sha256']), 64)

    def test_age_universe_is_twelve_plus(self):
        row = self.row(occupied=60); row['P_15YMAS'] = 10
        result = self.load([row])
        self.assertEqual(result.pea_real.sum(), 60)
        row['POCUPADA'] = 81
        with self.assertRaisesRegex(ValueError, 'age-eligible'):
            self.load([row])

    def test_zero_preserved_and_parent_residual_before_bbox(self):
        rows = [self.row(1, 10), self.row(2, '*', 40, 20), self.row(3, 'N/D', 20, 10),
                self.row(4, 0), self.row(0, 25, 260, 190)]
        for row in rows:
            row['P_15YMAS'] = min(row['P_12YMAS'], 80)
        rows[2]['_lon'] = -88
        result = self.load(rows, growth_factors={'23005': 2})
        self.assertEqual(result.employed_2020.tolist(), [10, 10, 0])
        r = result.attrs['residential_employment']
        self.assertEqual(r['input']['modeled_employed_2020'], 25)
        self.assertEqual(r['published_zero_blocks'], 1)
        self.assertEqual(r['suppressed_blocks'], 1)
        self.assertEqual(r['unavailable_blocks'], 1)
        self.assertEqual(r['placement']['outside_bbox']['projected_employed'], 10)
        self.assertEqual(r['placement']['retained']['projected_employed'], 40)

    def test_suppressed_block_is_not_limited_to_two_people(self):
        result = self.load([self.row(1, 10), self.row(2, '*'), self.row(0, 40, 200, 160)])
        self.assertEqual(result.employed_2020.tolist(), [10, 30])
        self.assertEqual(result.employment_source.tolist(), ['published_block', 'ageb_residual'])

    def test_partial_coverage_cannot_absorb_parent_jobs(self):
        result = self.load([self.row(1, 10), self.row(2, '*'), self.row(0, 100, 1000, 800)])
        self.assertEqual(result.employed_2020.tolist(), [10, 10])
        r = result.attrs['residential_employment']
        self.assertEqual(r['controls'][0]['status'], 'incomplete_population_coverage')
        self.assertEqual(result.employment_source.iloc[1], 'ageb_observed_rate')

    def test_incompatible_controls_fail_without_changing_known_counts(self):
        for target in (5, 100):
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'Incompatible CPV'):
                self.load([self.row(1, 10), self.row(2, '*'), self.row(0, target, 200, 160)])

    def test_nested_controls_allocate_only_unresolved_blocks(self):
        rows = [self.row(1, 10), self.row(2, '*'), self.row(0, 30, 200, 160),
                self.row(3, '*'), self.row(0, 50, 300, 240)]
        rows[3]['AGEB'] = '002A'; rows[4]['AGEB'] = '0000'
        result = self.load(rows)
        self.assertEqual(result.employed_2020.tolist(), [10, 20, 20])
        self.assertEqual(result.employment_source.iloc[-1], 'locality_residual')

    def test_conflicting_new_fields_and_parents_are_rejected(self):
        first = self.root / 'a.csv'; second = self.root / 'b.csv'
        pd.DataFrame([self.row(occupied=10)]).to_csv(first, index=False)
        for row in [self.row(occupied=70), self.row(occupied=10, p12=90)]:
            pd.DataFrame([row]).to_csv(second, index=False)
            with self.assertRaisesRegex(ValueError, 'Conflicting CPV employment'):
                self.load([self.row()], cpv_paths=[str(first), str(second)])
        pd.DataFrame([self.row(0, 10)]).to_csv(first, index=False)
        pd.DataFrame([self.row(0, 20)]).to_csv(second, index=False)
        with self.assertRaisesRegex(ValueError, 'Conflicting CPV employment'):
            self.load([self.row()], cpv_paths=[str(first), str(second)])

    def test_duplicate_sources_do_not_duplicate_workers(self):
        path = self.root / 'other.csv'
        pd.DataFrame([self.row()]).to_csv(path, index=False)
        result = self.load([self.row()], cpv_paths=[str(path), str(self.root / 'cpv.csv')])
        self.assertEqual(result.pea_real.sum(), 10)
        self.assertEqual(result.attrs['source_identity']['duplicates_removed'], 1)

    def test_missing_age_count_is_explicit_and_no_active_rate_fallback(self):
        row = self.row(); row.pop('P_12YMAS'); row.pop('P_15YMAS')
        result = self.load([row])
        self.assertEqual(result.pea_real.sum(), 10)
        self.assertEqual(result.attrs['residential_employment']['capacity_upper_bound_blocks'], 1)
        row['POCUPADA'] = '*'
        with self.assertRaisesRegex(ValueError, 'No published employment'):
            self.load([row], tasa_pea=.9)

    def test_full_identity_overrides_missing_locality(self):
        row = self.row(CVEGEO='230050001001A001'); row.pop('LOC')
        result = self.load([row])
        self.assertEqual(result.loc_clean.tolist(), ['0001'])
        row.pop('CVEGEO')
        with self.assertRaisesRegex(ValueError, 'complete CPV'):
            self.load([row])

    def test_invalid_counts_are_not_silently_zeroed(self):
        for value in (-1, 'bad', 'inf', 1.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Invalid CPV'):
                self.load([self.row(occupied=value)])

    def test_missing_or_aggregate_codes_cannot_identify_an_inhabited_block(self):
        for changes in [dict(AGEB='nan'), dict(AGEB='NONE'), dict(AGEB='0000'), dict(LOC='0000')]:
            row = self.row(); row.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'CPV.*identity'):
                self.load([row])

    def test_legacy_placement_uses_same_points_without_ageb_relocation(self):
        rows = [self.row(1, 10), self.row(2, 70)]
        rows[1]['_lon'] = -86.6
        candidate = self.load(rows, placement_mode='legacy')
        legacy = self.load(rows, placement_mode='legacy', employment_mode='legacy')
        self.assertEqual(candidate.lon.tolist(), legacy.lon.tolist())
        self.assertEqual(candidate.lon.tolist(), [-86.8, -86.6])
        self.assertEqual(candidate.pea_real.tolist(), [10, 70])

    def test_default_and_explicit_legacy_are_identical(self):
        rows = [self.row(1, 10), self.row(2, 70)]
        legacy = self.load(rows, employment_mode='legacy')
        path = self.root / 'cpv.csv'
        default = load_cpv_demography(str(path), pd.DataFrame(), self.bbox, .65,
                                      placement_mode='official_blocks')
        self.assertEqual(legacy.attrs['residential_placement']['input']['pea'], 104)
        self.assertEqual(default.attrs['residential_placement']['input']['pea'], 104)
        self.assertNotIn('residential_employment', legacy.attrs)

    def fixture_config(self):
        data = self.root / 'data'; data.mkdir()
        pd.DataFrame([self.row(1, 10), self.row(2, 70)]).to_csv(data / 'cpv.csv', index=False)
        pd.DataFrame([dict(id=i, cve_ent=23, cve_mun=5, cve_loc=1,
            ageb='001A', manzana=i, longitud=-86.8 + .02 * i, latitud=21.1,
            per_ocu='11 a 30 personas') for i in (1, 2)]).to_csv(data / 'denue.csv', index=False)
        cfg = dict(city=dict(code='TST', name='Test', description='Test', bbox=[-87,20,-86,22],
                             grid_size=.001, min_residents=0),
                   data_dir=str(data), macroeconomics=dict(
                             tasa_pea=.65, til_1_state=.45, projection_year=2026, default_growth_factor=1.2))
        path = self.root / 'city.yaml'; wizard.save_full_city_data(str(path), cfg)
        return path, cfg, data

    def test_wizard_persistence_preview_cache_and_production_export(self):
        path, cfg, data = self.fixture_config()
        self.assertEqual(load_city_config(str(path))['macroeconomics']['residential_employment'], 'census_employed')
        self.assertEqual(load_city_config(str(path))['macroeconomics']['workplace_employment'], 'auto')
        status = wizard.automatic_workplace_status(str(path))
        self.assertEqual(status['mode'], 'ce_bounded')
        self.assertEqual(status['automatic_selection']['reason'], 'No selected CE files')
        diagnostics = {}
        with patch.object(poi_studio, 'ROOT_DIR', str(self.root)):
            points = poi_studio.load_demand_sample(city_file=str(path), diagnostics=diagnostics)
            cached = poi_studio.load_demand_sample(city_file=str(path))
        self.assertEqual(points, cached)
        self.assertEqual(diagnostics['placement_mode'], 'official_blocks')
        self.assertEqual(diagnostics['workplace_mode'], 'auto')
        self.assertEqual(sum(p.get('employed_residents', 0) for p in points), 96)
        with patch('sb_mexico.pipeline.ROOT_DIR', str(self.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.is_docker_available', return_value=(False, None)):
            execute_pipeline(str(path), skip_map=True, output_dir=str(self.root / 'out'))
        report = json.loads((self.root / 'out/residential_employment_report.json').read_text())
        demand = json.loads((self.root / 'out/demand_data.json').read_text())
        self.assertEqual(report['input'], diagnostics['residential_employment']['input'])
        self.assertEqual(report['source_config_sha256'], diagnostics['residential_employment']['source_config_sha256'])
        self.assertEqual(report['simulation']['exported_commuters'], sum(p['size'] for p in demand['pops']))
        self.assertEqual(report['simulation']['exported_commuters'], 96)
        pipeline_report = json.loads((self.root / 'out/demand_pipeline_report.json').read_text())
        self.assertTrue(all(row['target_policy'] == 'reachable_budget_correction' for row in pipeline_report['employment_balancing']))
        self.assertTrue(all(row['status'] == 'converged' for row in pipeline_report['employment_balancing']))
        routing = pipeline_report['routing']
        self.assertEqual(sum(row['travelers'] for row in routing['pairs']), 96)
        self.assertTrue(all(row['source'] == 'canonical' and row['reason'] == 'missing_pbf' for row in routing['pairs']))
        self.assertTrue(all('source' not in pop and 'reason' not in pop for pop in demand['pops']))
        self.assertTrue(all('employment_source' not in p and 'employed_residents' not in p for p in demand['points']))
        cfg['macroeconomics']['residential_employment'] = 'legacy'
        wizard.save_full_city_data(str(path), cfg)
        with patch.object(poi_studio, 'ROOT_DIR', str(self.root)):
            points = poi_studio.load_demand_sample(city_file=str(path), diagnostics=diagnostics)
        self.assertNotIn('employed_residents', points[-1])
        self.assertEqual(diagnostics['employment_mode'], 'legacy')
        self.assertIsNone(diagnostics['residential_employment'])
        with patch('sb_mexico.pipeline.ROOT_DIR', str(self.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.is_docker_available', return_value=(False, None)):
            execute_pipeline(str(path), skip_map=True, output_dir=str(self.root / 'out'))
        self.assertEqual(json.loads((self.root / 'out/residential_employment_report.json').read_text())['mode'], 'legacy')
        cfg['macroeconomics']['residential_employment'] = 'invalid'
        with self.assertRaisesRegex(ValueError, 'residential_employment'):
            wizard.save_full_city_data(str(path), cfg)
        path.write_text(yaml.safe_dump(cfg))
        with self.assertRaisesRegex(ValueError, 'residential_employment'):
            load_city_config(str(path))


if __name__ == '__main__':
    unittest.main()
