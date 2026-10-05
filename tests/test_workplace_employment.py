import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import yaml

from sb_mexico.workplace_employment import bounded_fit, load_workplaces, read_ce_controls, source_hashes
from tools import wizard


class WorkplaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.denue = self.root / 'denue.csv'
        self.ce = self.root / 'ce.csv'
        self.bbox = dict(min_lon=-87, max_lon=-86, min_lat=20, max_lat=22)
        self.rows = [dict(id=str(i), cve_ent='23', cve_mun='005', per_ocu='0 a 5 personas',
                          codigo_act='461110', latitud=21, longitud=-86.5 if i == 1 else -88)
                     for i in (1,2)]
        self.write()

    def write(self, target=8, count=2, activity='Total municipal'):
        pd.DataFrame(self.rows).to_csv(self.denue,index=False)
        pd.DataFrame([{'Año Censal':2023,'Entidad':'23 Quintana Roo','Municipio':'005 Benito Juarez',
                       'Actividad económica':activity,'H001A Personal ocupado total':target,
                       'UE Unidades económicas':count}]).to_csv(self.ce,index=False)

    def macro(self, **extra):
        contract = dict(reference_year=2023, denue_reference_year=2023,
                        source_sha256=dict(denue=source_hashes([self.denue]),ce=source_hashes([self.ce])),
                        coverage_evidence='Isolated fixture: two establishments, identical universe and reference year',
                        groups=[dict(municipality='23005',scian_prefix='')])
        return dict(workplace_employment='ce_bounded', workplace_control_contract=contract, **extra)

    def load(self, macro=None):
        return load_workplaces([str(self.denue)],self.bbox,macro or self.macro(),ce_paths=[str(self.ce)])

    def test_upward_full_scope_then_clip(self):
        frame, _, report = self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(),4)
        self.assertAlmostEqual(report['outside_bbox_attraction'],4)
        self.assertAlmostEqual(report['full_scope_attraction'],8)
        self.assertEqual(report['controls'][0]['status'],'FITTED_DECLARED_COMPARABLE')

    def test_downward_including_large_and_open_band(self):
        result, factor = bounded_fit([2.24,71.41,450],[0,51,251],[5,100,np.inf],310)
        self.assertAlmostEqual(sum(result),310,places=6)
        self.assertLess(factor,1)
        self.assertLess(result[1],71.41)
        self.assertLess(result[2],450)
        self.assertGreaterEqual(result[2],251)

    def test_zero_and_boundary_controls(self):
        self.assertEqual(bounded_fit([2.24],[0],[5],0)[0][0],0)
        self.assertEqual(bounded_fit([2.24],[0],[5],5)[0][0],5)
        for target in (-1,6,float('nan')):
            with self.assertRaises(ValueError): bounded_fit([2.24],[0],[5],target)

    def test_infeasible_control_is_reported_not_forced(self):
        self.write(target=11)
        frame, _, report = self.load()
        self.assertEqual(report['controls'][0]['status'],'INFEASIBLE_CONTROL')
        self.assertAlmostEqual(frame.calibrated_jobs.sum(),2.24)

    def test_count_mismatch_does_not_calibrate(self):
        self.write(count=3)
        _, _, report = self.load()
        self.assertEqual(report['controls'][0]['status'],'COVERAGE_COUNT_MISMATCH')

    def test_unknown_band_no_false_control_closure(self):
        self.rows[0]['per_ocu'] = '*'
        self.write()
        _, _, report = self.load()
        self.assertEqual(report['unknown_band_establishments'],1)
        self.assertEqual(report['controls'][0]['status'],'UNKNOWN_SIZE_BAND')

    def test_no_contract_or_source_year_hash_match_no_til_expansion(self):
        macros = [dict(workplace_employment='ce_bounded',til_1_state=.9), self.macro(), self.macro()]
        macros[1]['workplace_control_contract']['denue_reference_year'] = 2024
        macros[2]['workplace_control_contract']['source_sha256']['denue'] = ['stale']
        for macro in macros:
            frame, _, report = self.load(macro)
            self.assertAlmostEqual(frame.calibrated_jobs.sum(),2.24)
            self.assertEqual(report['comparability'],'UNVERIFIED_SOURCE_COMPARABILITY')

    def test_unlocated_mass_is_not_redistributed_into_bbox(self):
        self.rows[1]['latitud'] = None
        self.write()
        frame, _, report = self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(),4)
        self.assertAlmostEqual(report['unlocated_attraction'],4)

    def test_scian_control_and_overlapping_groups(self):
        self.write(activity='46 Comercio al por menor')
        macro = self.macro()
        macro['workplace_control_contract']['groups'][0]['scian_prefix'] = '46'
        self.assertEqual(self.load(macro)[2]['controls'][0]['status'],'FITTED_DECLARED_COMPARABLE')
        macro['workplace_control_contract']['groups'].append(dict(municipality='23005',scian_prefix='461'))
        with self.assertRaisesRegex(ValueError,'Overlapping'): self.load(macro)

    def test_conflicting_industry_duplicate_fails_before_clipping(self):
        duplicate = dict(self.rows[1],codigo_act='722511')
        self.rows.append(duplicate)
        self.write()
        with self.assertRaises(ValueError): self.load()

    def test_ce_conflicts_and_year_selection(self):
        self.assertEqual(read_ce_controls([self.ce],2024),{})
        other = self.root / 'other.csv'
        text = self.ce.read_text().replace(',8,2',',9,2')
        other.write_text(text)
        with self.assertRaisesRegex(ValueError,'Contradictory'): read_ce_controls([self.ce,other],2023)

    def test_legacy_default_unchanged(self):
        from sb_mexico.inegi import load_denue, calibrate_denue_employment
        expected, _ = calibrate_denue_employment(load_denue([str(self.denue)],self.bbox),{},.45)
        actual, _, report = self.load(dict(til_1_state=.45))
        pd.testing.assert_frame_equal(expected,actual)
        self.assertEqual(report['mode'],'legacy')

    def test_empty_source_has_explicit_zero_mass_and_resolved_model_year(self):
        pd.DataFrame(columns=list(self.rows[0])).to_csv(self.denue,index=False)
        frame, _, report = self.load(dict(workplace_employment='ce_bounded',target_year=2027))
        self.assertEqual(len(frame),0)
        self.assertEqual(report['full_scope_attraction'],0)
        self.assertEqual(report['bbox_attraction'],0)
        self.assertEqual(report['model_year'],2027)

    def test_wizard_preserves_contract_and_rejects_invalid_mode(self):
        # Use the production save schema, with no mutable city input.
        with patch.object(wizard,'ROOT_DIR',str(self.root)):
            config = dict(city=dict(name='Fixture',code='fixture',bbox=[-87,20,-86,22]),
                          macroeconomics=self.macro())
            path = self.root / 'cities' / 'fixture.yaml'
            path.parent.mkdir()
            wizard.save_full_city_data(str(path),config)
            saved = yaml.safe_load(path.read_text())
            self.assertEqual(saved['macroeconomics']['workplace_control_contract'],config['macroeconomics']['workplace_control_contract'])
            config['macroeconomics']['workplace_employment'] = 'invalid'
            with self.assertRaises(ValueError): wizard.save_full_city_data(str(path),config)

    def test_preview_cache_build_export_and_legacy_sidecar_replacement(self):
        from test_residential_employment import CensusEmploymentTests
        from tools import poi_studio
        from sb_mexico.pipeline import execute_pipeline
        fixture = CensusEmploymentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path, cfg, data = fixture.fixture_config()
        ce = data / 'SAIC_fixture.csv'
        pd.DataFrame([{'Año Censal':2023,'Entidad':'23 Quintana Roo','Municipio':'005 Benito Juarez',
                       'Actividad económica':'Total municipal','H001A Personal ocupado total':50,
                       'UE Unidades económicas':2}]).to_csv(ce,index=False)
        cfg['macroeconomics'].update(workplace_employment='ce_bounded',workplace_control_contract=dict(
            reference_year=2023,denue_reference_year=2023,coverage_evidence='Isolated matching fixture universe',
            source_sha256=dict(denue=source_hashes([data/'denue.csv']),ce=source_hashes([ce])),
            groups=[dict(municipality='23005',scian_prefix='')]))
        wizard.save_full_city_data(str(path),cfg)
        diag = {}
        with patch.object(poi_studio,'ROOT_DIR',str(fixture.root)):
            points = poi_studio.load_demand_sample(city_file=str(path),diagnostics=diag)
            cached = poi_studio.load_demand_sample(city_file=str(path))
        self.assertEqual(points,cached)
        self.assertAlmostEqual(diag['workplace_employment']['bbox_attraction'],50,places=6)
        output = fixture.root/'out'
        with patch('sb_mexico.pipeline.ROOT_DIR',str(fixture.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
            execute_pipeline(str(path),skip_map=True,output_dir=str(output))
        report = json.loads((output/'workplace_employment_report.json').read_text())
        self.assertEqual(report['controls'],diag['workplace_employment']['controls'])
        self.assertEqual(report['source_sha256'],diag['workplace_employment']['source_sha256'])
        self.assertEqual(report['effective_macro_sha256'],diag['workplace_employment']['effective_macro_sha256'])
        self.assertEqual(report['simulation']['exported_commuters'],96)
        demand = json.loads((output/'demand_data.json').read_text())
        self.assertTrue(all('workplace_employment' not in row for row in demand['points']))
        # Even an equal-size source edit with restored timestamp must break cache
        # and invalidate the source-bound declaration, rather than show stale fit.
        stat = ce.stat()
        ce.write_text(ce.read_text().replace(',50,2',',52,2'))
        os.utime(ce,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with patch.object(poi_studio,'ROOT_DIR',str(fixture.root)):
            poi_studio.load_demand_sample(city_file=str(path),diagnostics=diag)
        self.assertEqual(diag['workplace_employment']['comparability'],'UNVERIFIED_SOURCE_COMPARABILITY')
        cfg['macroeconomics']['workplace_employment'] = 'legacy'
        wizard.save_full_city_data(str(path),cfg)
        with patch('sb_mexico.pipeline.ROOT_DIR',str(fixture.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
            execute_pipeline(str(path),skip_map=True,output_dir=str(output))
        self.assertEqual(json.loads((output/'workplace_employment_report.json').read_text())['mode'],'legacy')

if __name__ == '__main__':
    unittest.main()
