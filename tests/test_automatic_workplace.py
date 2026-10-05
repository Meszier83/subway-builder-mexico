"""Automatic source-content selection, independent of project names."""
import unittest
from unittest.mock import patch

import test_historical_benchmark as fixtures
from sb_mexico.workplace_employment import load_workplaces
from sb_mexico import automatic_workplace


class AutomaticWorkplaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.HistoricalBenchmarkTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def load(self):
        f = self.fixture
        return load_workplaces([f.denue], f.bbox, {'workplace_employment':'auto'},
                               ce_paths=[f.ce], source_root=f.root)

    def test_totals_only_notice_then_detail_automatically_activates(self):
        f = self.fixture
        frame, _, report = self.load()
        self.assertEqual(report['mode'], 'ce_bounded')
        self.assertIn('sector-and-size', report['automatic_selection']['reason'])
        f.rows.append(f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10'))
        f.write()
        frame, _, report = self.load()
        self.assertEqual(report['mode'], 'historical_transfer')
        self.assertEqual(report['automatic_selection']['requested_mode'], 'auto')
        self.assertEqual(report['transferred_establishments'], 2)
        self.assertEqual(report['fallback_establishments'], 2)
        self.assertAlmostEqual(frame.calibrated_jobs.sum(), 4)
        self.assertEqual(report['comparability'], 'CONDITIONAL_HISTORICAL_MODEL')

    def test_wrong_geography_does_not_activate(self):
        f = self.fixture
        f.rows.append(f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10', Entidad='31 Yucatán'))
        f.write()
        self.assertEqual(self.load()[2]['mode'], 'ce_bounded')

    def test_suppressed_cells_and_unsupported_sectors_are_not_activation_evidence(self):
        f = self.fixture
        f.rows.extend([f.row('Sector 31-33 Industrias', None, 3, Estrato='0 a 10'),
                       f.row('Sector 48-49 Transporte', 12, 3, Estrato='0 a 10')])
        f.write()
        self.assertEqual(self.load()[2]['mode'], 'ce_bounded')

    def test_multiple_activity_years_fail_instead_of_choosing_silently(self):
        f = self.fixture
        f.rows.extend([f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10'),
                       f.row('Sector 31-33 Industrias', 9, 3, Estrato='0 a 10', **{'Año Censal':2018})])
        f.write()
        with self.assertRaisesRegex(ValueError, 'multiple activity years'):
            self.load()

    def test_conflicting_cells_fail(self):
        f = self.fixture
        f.rows.extend([f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10'),
                       f.row('Sector 31-33 Industrias', 13, 3, Estrato='0 a 10')])
        f.write()
        with self.assertRaisesRegex(ValueError, 'Contradictory CE'):
            self.load()

    def test_changed_sources_during_inspection_fail(self):
        f = self.fixture
        f.rows.append(f.row('Sector 31-33 Industrias', 12, 3, Estrato='0 a 10'))
        f.write()
        read = automatic_workplace.read_saic_controls
        def changing(paths):
            result = read(paths)
            with f.denue.open('a') as source:
                source.write('\n')
            return result
        with patch.object(automatic_workplace, 'read_saic_controls', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'sources changed'):
                self.load()
