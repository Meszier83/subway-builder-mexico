"""Draft demand must survive location review without silently becoming zero."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from tools.wizard import save_full_city_data, load_city_data, validate_city_configuration
from tools.poi_studio import save_city_pois
from sb_mexico.special_demand import require_resolved_poi_demand
from sb_mexico.pipeline import load_city_config
from sb_mexico.gravity import build_demand_grid


class PendingPoiDemandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'city.yaml'
        self.poi = dict(id='UNI_Review', name='Review', type='uni', jobs=None,
                        loc=[-103.4, 25.5], radius_m=100, mode='MAX',
                        metadata={'enrollment_school_based': '1200', 'review_notes': 'Verify campus'})
        self.config = dict(city=dict(code='TST', name='Test', bbox=[-104, 25, -103, 26]),
                           macroeconomics={}, pois=[self.poi])

    def test_wizard_and_studio_roundtrip(self):
        save_full_city_data(str(self.path), self.config)
        self.assertEqual(load_city_data(str(self.path))['pois'], [self.poi])
        save_city_pois(str(self.path), [self.poi])
        self.assertEqual(load_city_data(str(self.path))['pois'], [self.poi])

    def test_all_build_entry_points_reject_pending(self):
        self.path.write_text(yaml.safe_dump(self.config), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Demanda pendiente.*UNI_Review'):
            load_city_config(str(self.path))
        # Empty source arguments prove the guard runs before spatial processing.
        with self.assertRaisesRegex(ValueError, 'Demanda pendiente.*UNI_Review'):
            build_demand_grid(None, None, [self.poi], None)
        with patch('tools.wizard.inspect_data_files', return_value={}):
            result = validate_city_configuration(str(self.path))
        self.assertTrue(any('Demanda pendiente' in e for e in result['errors']))

    def test_explicit_zero_and_legacy_missing_demand_remain_supported(self):
        require_resolved_poi_demand([dict(id='AIR_Zero', jobs=0), dict(id='Legacy')])
        self.poi['jobs'] = 0
        save_full_city_data(str(self.path), self.config)
        self.assertEqual(load_city_data(str(self.path))['pois'][0]['jobs'], 0)

    def test_windows_reader_does_not_lose_pending_save(self):
        import os
        if os.name != 'nt':
            self.skipTest('Windows file sharing contract')
        replace = os.replace
        attempts = []
        def temporarily_busy(source, destination):
            attempts.append(source)
            if len(attempts) == 1:
                raise PermissionError('Concurrent reader')
            return replace(source, destination)
        with patch('tools.wizard.os.replace', side_effect=temporarily_busy), patch('tools.wizard.time.sleep'):
            save_full_city_data(str(self.path), self.config)
        self.assertEqual(len(attempts), 2)
        self.assertIsNone(load_city_data(str(self.path))['pois'][0]['jobs'])


if __name__ == '__main__':
    unittest.main()
