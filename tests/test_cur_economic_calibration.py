from pathlib import Path
import sys
import unittest

import numpy as np

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import calibrate_cur_economics as calibration
import diagnose_cur_economics as base


class CalibrationTests(unittest.TestCase):
    def row(self, band, income='0'):
        return dict(ing7c=str(band), ingocup=income, salario='8364')

    def test_unknown_and_zero_have_different_upper_bounds(self):
        self.assertEqual(calibration.income_interval(self.row(6)), (0, 0))
        self.assertEqual(calibration.income_interval(self.row(7)), (0, float('inf')))

    def test_income_band_limits_use_survey_salary(self):
        self.assertEqual(calibration.income_interval(self.row(3)), (16728, 25092))
        self.assertEqual(calibration.income_interval(self.row(5)), (41820, float('inf')))
        self.assertEqual(calibration.income_interval(self.row(2, '9000')), (9000, 9000))
        with self.assertRaises(ValueError):
            calibration.income_interval({**self.row(3), 'salario': '9999'})

    def test_unbounded_quantile_is_not_fabricated(self):
        bounds = calibration.quantile_bounds([100, 0], [100, float('inf')], [94, 6], [.5, .95])
        self.assertEqual(bounds[0], dict(lower=100, upper=100))
        self.assertEqual(bounds[1], dict(lower=100, upper=None))

    def test_invalid_interval_fails(self):
        with self.assertRaises(ValueError):
            calibration.quantile_bounds([20], [10], [1])

    def test_fare_and_duplicate_counterexamples_are_detected(self):
        row = dict(distance_m=8000, parking_mxn=20, driving_mxn_per_km=2.5,
                   kind='ordinary', population=50, perceived_transit_minutes=30,
                   fare_mxn=5, transit=10, driving=40, walking=0)
        expensive = {**row, 'fare_mxn': 10, 'transit': 20, 'driving': 30}
        with self.assertRaisesRegex(AssertionError, 'monotonicity'):
            calibration.check_modes([row, expensive])
        with self.assertRaisesRegex(AssertionError, 'Duplicated'):
            calibration.check_modes([row, row])

    def test_uncertainty_gate_cannot_be_hidden_by_perfect_point_fit(self):
        params = [60000, 25000, 15000, 200000]
        distributions = {kind: {str(n): base.game_incomes(params, n, multiplier).tolist()
                               for n in (25, 50, 100, 200)}
                         for kind, multiplier in [('ordinary', 1), ('airport', 1.5), ('university', .6)]}
        stats = base.stats((np.array(distributions['ordinary']['50'])/12).tolist(), [1]*50)
        fit = dict(mxn_per_game_unit=1, rules=dict(zip(calibration.INCOME_KEYS, params)),
                   generated_monthly_mxn=stats, quantile_relative_errors={'p50': 0}, max_abs_quantile_error=0)
        key = dict(distance_m=8000, perceived_transit_minutes=30, fare_mxn=10)
        uncertainty = [{**key, 'scenario': scope + '_' + endpoint, 'transit': count}
                       for scope in ('all_employed', 'specified_only')
                       for endpoint, count in [('lower', 0), ('upper', 50)]]
        modes = [{**key, 'parking_mxn': 20, 'driving_mxn_per_km': 2.5,
                  'kind': 'ordinary', 'population': n, 'transit': n//2} for n in (25, 50, 100, 200)]
        result = calibration.validate_fit(fit, dict(distributions=distributions, uncertainty=uncertainty,
                                                   modes=modes), stats)
        self.assertFalse(result['eligible'])
        self.assertFalse(result['criteria']['income_uncertainty_transit_spread_le_10pp'])
        self.assertTrue(result['criteria']['each_quantile_error_le_20pct'])


if __name__ == '__main__':
    unittest.main()
