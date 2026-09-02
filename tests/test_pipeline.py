import unittest
import os
import tempfile
import yaml
from sb_mexico.pipeline import load_city_config, _dedup_glob

class TestPipeline(unittest.TestCase):
    def test_load_city_config(self):
        sample = {
            "city": {"code": "CUN", "name": "Cancun", "bbox": [-87.0, 21.0, -86.7, 21.3]},
            "macroeconomics": {"tasa_pea": 0.65, "til_1_state": 0.45}
        }
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False, encoding='utf-8') as f:
            yaml.dump(sample, f)
            tmp_name = f.name
        try:
            cfg = load_city_config(tmp_name)
            self.assertEqual(cfg["city"]["code"], "CUN")
        finally:
            os.remove(tmp_name)

    def test_dedup_glob(self):
        with tempfile.NamedTemporaryFile(suffix='_censo_1.csv', delete=False) as f1:
            pass
        with tempfile.NamedTemporaryFile(suffix='_censo_2.csv', delete=False) as f2:
            pass
        try:
            dir_name = os.path.dirname(f1.name)
            pattern1 = os.path.join(dir_name, "*censo*.csv")
            pattern2 = os.path.join(dir_name, "*_1.csv")
            files = _dedup_glob([pattern1, pattern2])
            f1_count = sum(1 for f in files if os.path.abspath(f) == os.path.abspath(f1.name))
            self.assertEqual(f1_count, 1)
        finally:
            os.remove(f1.name)
            os.remove(f2.name)

if __name__ == "__main__":
    unittest.main()
