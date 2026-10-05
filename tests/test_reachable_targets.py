import unittest
import numpy as np
from sb_mexico.gravity import furness_ipfp_balance

class ReachableTargetTests(unittest.TestCase):
    def solve(self, origins, jobs, distances, **options):
        report = {}
        probabilities = furness_ipfp_balance(np.array(origins, dtype=float), np.array(jobs, dtype=float),
            np.array(distances, dtype=float), max_distance_km=10, max_iter=100,
            diagnostics=report, **options)
        self.assertTrue(np.allclose(probabilities.sum(axis=1), 1))
        self.assertTrue(np.all(probabilities[np.array(distances)>10] == 0))
        expected = np.array(origins) @ probabilities
        self.assertAlmostEqual(expected.sum(), sum(origins), places=5)
        return probabilities, report

    def test_connected_bottleneck_is_corrected_without_new_trips(self):
        probabilities, report = self.solve([30,70], [50,50], [[1,2],[100,1]])
        self.assertEqual(report['status'], 'converged')
        self.assertTrue(np.allclose(report['effective_destination_targets'], [30,70]))
        self.assertTrue(np.allclose(probabilities, np.eye(2)))
        self.assertAlmostEqual(report['target_adjustments'][0]['redistributed_mass'], 20)
        self.assertAlmostEqual(report['requested_column_relative_error'], .4)
        legacy, _ = self.solve([30,70], [50,50], [[1,2],[100,1]], correct_destination_targets=False)
        self.assertTrue(np.array_equal(probabilities,legacy))
        self.assertFalse(report['flow_rebalanced'])

    def test_target_ratios_survive_within_corrected_groups(self):
        probabilities, report = self.solve([30,70], [20,30,50], [[1,1,2],[100,100,1]])
        self.assertEqual(report['status'], 'converged')
        self.assertTrue(np.allclose(report['effective_destination_targets'], [12,18,70]))
        self.assertLess(probabilities[0,2],1e-6)
        self.assertTrue(np.allclose(probabilities[1,:2],0))

    def test_nested_cuts_preserve_previously_committed_budgets(self):
        probabilities, report = self.solve([20,30,50], [30,35,35],
            [[1,2,3],[100,1,2],[100,100,1]])
        self.assertEqual(report['status'], 'converged')
        self.assertTrue(np.allclose(report['effective_destination_targets'], [20,30,50]))
        self.assertTrue(np.allclose(probabilities, np.eye(3)))
        self.assertEqual(len(report['target_adjustments']), 2)
        self.assertTrue(report['flow_rebalanced'])

    def test_feasible_case_keeps_legacy_result_exactly(self):
        current, report = self.solve([40,60], [45,55], [[1,2],[2,1]])
        legacy, old = self.solve([40,60], [45,55], [[1,2],[2,1]], correct_destination_targets=False)
        self.assertTrue(np.array_equal(current, legacy))
        self.assertFalse(report['target_adjustments'])
        self.assertEqual(report['column_relative_error'], old['column_relative_error'])

    def test_short_fit_without_proven_bottleneck_does_not_rewrite_targets(self):
        report = {}
        furness_ipfp_balance(np.array([40.,60.]), np.array([45.,55.]), np.array([[1.,9.],[9.,1.]]),
            max_distance_km=10, max_iter=1, tol=1e-9, diagnostics=report)
        self.assertEqual(report['status'],'iteration_limit')
        self.assertFalse(report['target_adjustments'])
        self.assertEqual(report['requested_destination_targets'], report['effective_destination_targets'])

    def test_no_diagnostics_runs_identical_correction(self):
        current, _ = self.solve([30,70], [50,50], [[1,2],[100,1]])
        without = furness_ipfp_balance(np.array([30.,70.]),np.array([50.,50.]),np.array([[1.,2.],[100.,1.]]),
            max_distance_km=10,max_iter=100)
        self.assertTrue(np.array_equal(current,without))

if __name__ == '__main__': unittest.main()
