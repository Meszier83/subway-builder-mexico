"""Adversarial contracts for the standalone economic diagnostic."""
import csv
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

SPEC = importlib.util.spec_from_file_location(
    'cur_economics', Path(__file__).resolve().parents[1] / 'tools/diagnose_cur_economics.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class EconomicGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (Path(__file__).resolve().parents[1] / '.cache').mkdir(exist_ok=True)

    def test_missing_exact_income_is_not_zero(self):
        self.assertEqual(gate.income_status({'ingocup': '0', 'ing7c': '7'}), 'unspecified_income')
        self.assertEqual(gate.income_status({'ingocup': '0', 'ing7c': '2'}), 'band_only_missing_exact')
        self.assertEqual(gate.income_status({'ingocup': '0', 'ing7c': '6'}), 'reported_zero')
        self.assertEqual(gate.income_status({'ingocup': '10000', 'ing7c': '2'}), 'positive_exact')

    def test_conflicting_income_classification_fails(self):
        with self.assertRaises(ValueError):
            gate.income_status({'ingocup': '10000', 'ing7c': '6'})

    def test_weighted_quantiles_do_not_use_unweighted_median(self):
        result = gate.weighted_quantiles([100, 1000, 10000], [1, 98, 1], [.1, .5, .9])
        np.testing.assert_array_equal(result, [1000, 1000, 1000])
        result = gate.stats([100, 10000], [99, 1])
        self.assertEqual(result['mean'], 199)
        self.assertEqual(result['p50'], 100)

    def test_invalid_weights_fail(self):
        for weights in ([0, 1], [-1, 1], [float('nan'), 1], [float('inf'), 1], [1]):
            with self.assertRaises(ValueError):
                gate.weighted_quantiles([10, 20], weights)

    def test_generator_final_clamp_and_determinism(self):
        params = [6000, 2500, 1000, 15000]
        a = gate.game_incomes(params, 50)
        np.testing.assert_array_equal(a, gate.game_incomes(params, 50))
        self.assertTrue(np.all((a >= 1000) & (a <= 15000)))
        # The fixed-money perturbations make simple currency scaling invalid.
        b = gate.game_incomes([v * 20 for v in params], 50) / 20
        self.assertGreater(np.max(np.abs(a - b)), 100)

    def fixture(self, path, period='425'):
        base = dict(per=period, r_def='00', c_res='1', eda='35', clase2='1',
                    fac_tri='10', cd_a='41', cve_ent='23', t_loc_tri='1',
                    ingocup='10000', ing7c='2', hrsocup='40')
        rows = [base, {**base, 'ingocup': '0', 'ing7c': '7'},
                {**base, 'ingocup': '0', 'ing7c': '6'},
                {**base, 'ingocup': '0', 'ing7c': '1'},
                {**base, 'cd_a': '81', 't_loc_tri': '3', 'ingocup': '20000'},
                {**base, 'cd_a': '81', 't_loc_tri': '4', 'ingocup': '90000'},
                {**base, 'r_def': '15'}, {**base, 'c_res': '2'},
                {**base, 'clase2': '2'}, {**base, 'eda': '99'}]
        text = io.StringIO(newline='')
        writer = csv.DictWriter(text, fieldnames=list(base))
        writer.writeheader()
        writer.writerows(rows)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(gate.SD + 'conjunto_de_datos/conjunto_de_datos_sdem_enoe_2025_4t.csv',
                             text.getvalue().encode('cp1252'))
            for name in ('cd_a.csv', 't_loc_tri.csv', 'ing7c.csv'):
                archive.writestr(gate.SD + 'catalogos/' + name, 'CVE,DESCRIP\n41,Cancún\n'.encode('utf8'))

    def test_domains_filters_and_income_denominators(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.cache') as directory:
            path = Path(directory) / 'fixture.zip'
            self.fixture(path)
            domains, _ = gate.survey_domains(path)
        city = domains['cancun_city']
        self.assertEqual(city['employed_n'], 4)
        self.assertEqual(domains['quintana_roo_urban']['employed_n'], 5)
        self.assertEqual(city['income_status']['unspecified_income']['weighted_share'], .25)
        self.assertEqual(city['positive_monthly_mxn']['mean'], 10000)
        self.assertEqual(city['known_including_zero_monthly_mxn']['mean'], 5000)

    def test_wrong_reference_period_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / '.cache') as directory:
            path = Path(directory) / 'fixture.zip'
            self.fixture(path, period='426')
            with self.assertRaisesRegex(ValueError, 'period'):
                gate.survey_domains(path)

    def test_sensitivity_detects_counterexample(self):
        base = dict(distance_m=1000, parking_mxn=0, driving_mxn_per_km=3,
                    perceived_transit_minutes=15, walking=0)
        rows = [{**base, 'fare_mxn': 5, 'transit': 10, 'driving': 40},
                {**base, 'fare_mxn': 10, 'transit': 20, 'driving': 30}]
        self.assertEqual(gate.sensitivity_checks(rows)['fare_increase_violations'], 1)
        rows[-1]['driving'] = 31
        self.assertFalse(gate.sensitivity_checks(rows)['passenger_conservation'])


if __name__ == '__main__':
    unittest.main()
