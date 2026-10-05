"""Counterexamples for the static validator; uses real YAML and temporary files."""

import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

SCRIPT = Path(__file__).with_name("validate_city.py")
SPEC = importlib.util.spec_from_file_location("skill_city_validator", SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class CityValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "city.yaml"
        self.config = {
            "city": {
                "code": "TST", "name": "Test", "description": "Test city",
                "bbox": [-87, 20, -86, 22],
            },
            "macroeconomics": {},
            "data_dir": self.directory.name,
        }

    def validate(self, config):
        self.path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return VALIDATOR.validate_city_yaml(str(self.path))

    def test_valid_config_and_explicit_data_directory(self):
        self.assertEqual(self.validate(self.config), (True, [], []))

    def test_invalid_sections_do_not_pass_or_crash(self):
        for section in ("city", "macroeconomics", "routing"):
            for value in (None, [], "invalid", True):
                with self.subTest(section=section, value=value):
                    config = copy.deepcopy(self.config)
                    config[section] = value
                    valid, errors, _ = self.validate(config)
                    self.assertFalse(valid)
                    self.assertTrue(errors)

    def test_missing_required_city_fields(self):
        for field in ("code", "name", "description", "bbox"):
            with self.subTest(field=field):
                config = copy.deepcopy(self.config)
                del config["city"][field]
                self.assertFalse(self.validate(config)[0])

    def test_unsafe_city_code(self):
        self.config["city"]["code"] = "../CUR"
        self.assertFalse(self.validate(self.config)[0])

    def test_invalid_bboxes(self):
        for bounds in ([-87, 20, 181, 22], [-87, 20, -86, 91],
                       [-87, 20, float("inf"), 22], [-87, float("nan"), -86, 22],
                       [-86, 20, -87, 22], [-87, 22, -86, 20],
                       [True, 20, -86, 22], ["bad", 20, -86, 22], [0, 1]):
            with self.subTest(bounds=bounds):
                self.config["city"]["bbox"] = bounds
                self.assertFalse(self.validate(self.config)[0])

    def test_invalid_centers(self):
        for center in (["bad", 21], [-86.5, 999], [float("nan"), 21]):
            with self.subTest(center=center):
                self.config["city"]["initial_center"] = center
                self.assertFalse(self.validate(self.config)[0])

    def test_optional_camera_and_extended_core_format(self):
        self.config["city"]["initial_center"] = None
        self.config["city"]["urban_core_polygon"] = None
        self.assertEqual(self.validate(self.config), (True, [], []))
        self.config["city"]["urban_core_polygon"] = {
            "type": "Polygon",
            "coordinates": [[[-87, 20], [-86, 20], [-86, 21], [-87, 20]]],
        }
        valid, errors, warnings = self.validate(self.config)
        self.assertTrue(valid)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_invalid_numeric_parameters_do_not_crash(self):
        for field in ("min_pop_size", "target_pop_size", "max_pop_size"):
            for value in ("bad", None, False, 0, -1, float("inf"), 1.5):
                with self.subTest(field=field, value=value):
                    self.config["macroeconomics"] = {field: value}
                    self.assertFalse(self.validate(self.config)[0])
        for field in ("tasa_pea", "til_1_state", "furness_tol"):
            for value in (-0.1, 1.1, float("nan"), True):
                with self.subTest(field=field, value=value):
                    self.config["macroeconomics"] = {field: value}
                    self.assertFalse(self.validate(self.config)[0])

    def test_inverted_cohort_bounds(self):
        self.config["macroeconomics"] = {"min_pop_size": 201, "max_pop_size": 200}
        self.assertFalse(self.validate(self.config)[0])

    def test_auto_beta_and_rigid_cohorts_remain_accepted(self):
        for beta in ("auto", "none", "", None, 0.12):
            with self.subTest(beta=beta):
                self.config["macroeconomics"] = {
                    "gravity_beta": beta, "min_pop_size": 200,
                    "target_pop_size": 200, "max_pop_size": 200,
                }
                self.assertTrue(self.validate(self.config)[0])

    def test_invalid_collection_types_and_entries(self):
        for section in ("pois", "affluence_zones", "exclusion_zones", "isolated_zones", "places"):
            for value in (None, {}, "bad", [None]):
                with self.subTest(section=section, value=value):
                    config = copy.deepcopy(self.config)
                    config[section] = value
                    self.assertFalse(self.validate(config)[0])

    def test_invalid_poi_coordinates_and_duplicate_ids(self):
        for loc in (["bad", 999], [float("nan"), 21], [181, 21]):
            with self.subTest(loc=loc):
                self.config["pois"] = [{"id": "UNI_Test", "loc": loc}]
                self.assertFalse(self.validate(self.config)[0])
        self.config["pois"] = [{"id": "UNI_Test", "loc": [-86.5, 21]}] * 2
        self.assertFalse(self.validate(self.config)[0])

    def test_malformed_zone_parameters_and_geometry(self):
        for zone in (
            {"id": "zone", "bbox": [-87, 20, -86, 22], "reach_bonus": "bad"},
            {"id": "zone", "coordinates": [[-87, 20], [-86, 21]]},
            {"id": "zone", "coordinates": [[-87, 20], [-86, 21], [999, 21]]},
            {"id": "zone"},
        ):
            with self.subTest(zone=zone):
                self.config["affluence_zones"] = [zone]
                self.assertFalse(self.validate(self.config)[0])

    def test_driving_path_boolean_and_precedence(self):
        for section in (None, "macroeconomics", "routing"):
            with self.subTest(section=section):
                config = copy.deepcopy(self.config)
                target = config if section is None else config.setdefault(section, {})
                target["include_driving_path"] = "false"
                self.assertFalse(self.validate(config)[0])
        self.config["include_driving_path"] = True
        self.config["routing"] = {"include_driving_path": False}
        self.assertEqual(self.validate(self.config), (True, [], []))
        self.config["routing"]["include_driving_path"] = True
        valid, errors, warnings = self.validate(self.config)
        self.assertTrue(valid)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_invalid_yaml_root_and_missing_file(self):
        for config in (None, [], "bad"):
            with self.subTest(config=config):
                self.assertFalse(self.validate(config)[0])
        self.path.write_text("city: [", encoding="utf-8")
        self.assertFalse(VALIDATOR.validate_city_yaml(str(self.path))[0])
        self.path.unlink()
        self.assertFalse(VALIDATOR.validate_city_yaml(str(self.path))[0])

    def test_cli_exit_status_for_valid_and_invalid_files(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                config = copy.deepcopy(self.config)
                if not valid:
                    config["city"] = None
                self.validate(config)
                result = subprocess.run(
                    [sys.executable, "-B", str(SCRIPT), str(self.path)],
                    capture_output=True, text=True, encoding="utf-8", check=False,
                )
                self.assertEqual(result.returncode, 0 if valid else 1, result.stderr)
                self.assertEqual(result.stderr, "")

    def test_documented_yaml_examples_remain_accepted(self):
        import re

        guide = SCRIPT.parent.parent / "references" / "city_configuration_guide.md"
        config = {}
        for block in re.findall(r"```yaml\n(.*?)```", guide.read_text(encoding="utf-8"), re.S):
            config.update(yaml.safe_load(block))
        config["data_dir"] = self.directory.name
        valid, errors, _ = self.validate(config)
        self.assertTrue(valid, errors)


if __name__ == "__main__":
    unittest.main()
