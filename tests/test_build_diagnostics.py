import unittest
import json
import yaml
import test_residential_employment as fixtures
from sb_mexico.pipeline import execute_pipeline
from unittest.mock import patch, MagicMock
import numpy as np
from sb_mexico.gravity import furness_ipfp_balance
from sb_mexico.osrm import enrich_pops_with_osrm, summarize_routing_provenance

class DiagnosticEvidenceTests(unittest.TestCase):
    def test_disconnected_targets_cannot_match_both_budgets(self):
        report = {}
        probabilities = furness_ipfp_balance(np.array([30.,70.]), np.array([50.,50.]),
            np.array([[1.,100.],[100.,1.]]), max_distance_km=10, diagnostics=report, correct_destination_targets=False)
        self.assertTrue(np.allclose(probabilities, np.eye(2)))
        self.assertEqual(sorted(c['unavoidable_mass_difference'] for c in report['support_components']), [-20.,20.])
        self.assertEqual(report['status'], 'iteration_limit')
        self.assertEqual(report['deficient_subset_support']['unavoidable_deficit'], 20.)

    def test_territorial_budget_survives_synchronization(self):
        fixture = fixtures.CensusEmploymentTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        path, config, _ = fixture.fixture_config()
        config['isolated_zones'] = [dict(name='Fixture', bbox=[-180,-90,180,90])]
        path.write_text(yaml.safe_dump(config), encoding='utf-8')
        with patch('sb_mexico.pipeline.ROOT_DIR', str(fixture.root)), patch('sb_mexico.pipeline.console'), patch('sb_mexico.pipeline.is_docker_available', return_value=(False,None)):
            execute_pipeline(str(path), skip_map=True, output_dir=str(fixture.root/'out'))
        report = json.loads((fixture.root/'out/demand_pipeline_report.json').read_text())
        self.assertTrue(report['territorial_accounting'])
        self.assertEqual(sum(row['origin_budget'] for row in report['territorial_accounting']), 96)
        self.assertTrue(all(row['delta'] == 0 for row in report['territorial_accounting']))

    def test_direct_evidence_and_shared_pair_travelers(self):
        pops = [dict(residenceId='a', jobId='b', size=25), dict(residenceId='a', jobId='b', size=35)]
        points = [dict(id='a',location=[-87.,20.]),dict(id='b',location=[-87.01,20.])]
        response = MagicMock(status_code=200)
        response.json.return_value = dict(code='NoRoute')
        session = MagicMock()
        session.get.return_value = response
        report = {}
        with patch('requests.Session',return_value=session):
            self.assertEqual(enrich_pops_with_osrm(pops,points,diagnostics=report),(0,1))
        self.assertEqual(report['totals']['canonical:NoRoute'],dict(pairs=1,cohorts=2,travelers=60))
        self.assertEqual(set(pops[0]),{'residenceId','jobId','size','drivingDistance','drivingSeconds'})
        accepted = summarize_routing_provenance(pops,{('a','b'):('osrm','accepted')})
        self.assertEqual(accepted['pairs'][0]['source'],'osrm')

if __name__ == '__main__': unittest.main()
