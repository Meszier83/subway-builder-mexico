"""New and existing projects resolve identically in all three entry points."""
import tempfile
import unittest
from pathlib import Path

import yaml

from sb_mexico.pipeline import load_city_config
from tools import wizard, poi_studio


class DemandDefaultTests(unittest.TestCase):
    def test_unspecified_modes_are_enabled_without_changing_source_file(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'city.yaml'
            content = yaml.safe_dump(dict(city=dict(code='TST'), macroeconomics={}))
            path.write_text(content)
            for load in (load_city_config, wizard.load_city_data, poi_studio.load_city_data):
                config = load(str(path))
                self.assertEqual(config['city']['residential_placement'], 'official_blocks')
                self.assertEqual(config['macroeconomics']['residential_employment'], 'census_employed')
                self.assertEqual(config['macroeconomics']['workplace_employment'], 'auto')
                self.assertNotIn('historical_workplace_transfer', config['macroeconomics'])
            self.assertEqual(path.read_text(), content)

    def test_explicit_legacy_selection_survives_save_and_all_loaders(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path = Path(directory) / 'city.yaml'
            config = dict(city=dict(code='TST', residential_placement='legacy'),
                          macroeconomics=dict(residential_employment='legacy', workplace_employment='legacy'))
            wizard.save_full_city_data(str(path), config)
            for load in (load_city_config, wizard.load_city_data, poi_studio.load_city_data):
                actual = load(str(path))
                self.assertEqual(actual['city']['residential_placement'], 'legacy')
                self.assertEqual(actual['macroeconomics']['residential_employment'], 'legacy')
                self.assertEqual(actual['macroeconomics']['workplace_employment'], 'legacy')
