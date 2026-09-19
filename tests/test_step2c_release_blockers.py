import json
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr
from unittest import mock

import pandas as pd
import yaml

from sb_mexico.pipeline import (
    BuildResult, _config_identity, _execute_pipeline_run, _prepare_depot_demand,
    execute_pipeline, load_city_config, validate_package_integrity,
)
from sb_mexico.special_demand import validate_special_demand_points
from tools.wizard import (
    active_build, build_lock, create_new_project, load_city_data,
    resolve_current_build_package, run_pipeline_task, save_full_city_data,
)


class Step2CReleaseBlockerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.mkdtemp(dir=os.path.dirname(__file__))
        self.config = os.path.join(self.tempdir, "city.yaml")
        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump({
                "city": {"code": "NEW", "name": "New City", "description": "test", "bbox": [0, 0, 1, 1]},
                "macroeconomics": {},
                "temporal": {"model_year": 2025, "cpv_base_year": 2020},
            }, stream)
        self.output = os.path.join(self.tempdir, "dist")

    def tearDown(self):
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def test_success_promotes_only_current_structured_result(self):
        def fake_run(**kwargs):
            stage = kwargs["output_dir"]
            package = os.path.join(stage, "NEW.zip")
            demand = os.path.join(stage, "demand_data.json")
            with open(package, "wb") as stream:
                stream.write(b"current")
            with open(demand, "w", encoding="utf-8") as stream:
                stream.write("{}")
            with open(os.path.join(stage, "source_manifest.json"), "w", encoding="utf-8") as stream:
                json.dump({"sources": {}}, stream)
            return BuildResult(
                "package_created", kwargs["build_id"], "NEW", kwargs["config_identity"],
                package_path=package, demand_path=demand, staging_dir=stage,
                package_sha256=__import__("hashlib").sha256(b"current").hexdigest(),
            )

        os.makedirs(self.output)
        with open(os.path.join(self.output, "OLD.zip"), "wb") as stream:
            stream.write(b"old")
        with mock.patch("sb_mexico.pipeline._execute_pipeline_run", side_effect=fake_run):
            result = execute_pipeline(self.config, output_dir=self.output)
        self.assertTrue(result.package_created)
        self.assertEqual(os.path.realpath(result.package_path), os.path.realpath(os.path.join(self.output, "NEW.zip")))
        self.assertFalse(os.path.exists(os.path.join(self.output, "OLD.zip")))

    def test_failed_rebuild_keeps_previous_success(self):
        os.makedirs(self.output)
        old_zip = os.path.join(self.output, "NEW.zip")
        with open(old_zip, "wb") as stream:
            stream.write(b"old-good")
        with mock.patch("sb_mexico.pipeline._execute_pipeline_run", side_effect=RuntimeError("boom")):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                execute_pipeline(self.config, output_dir=self.output)
        with open(old_zip, "rb") as stream:
            self.assertEqual(stream.read(), b"old-good")

    def test_wizard_failed_rebuild_clears_old_download_identity(self):
        os.makedirs(self.output)
        old_zip = os.path.join(self.output, "NEW.zip")
        with open(old_zip, "wb") as stream:
            stream.write(b"old-good")
        with build_lock:
            active_build.update({"status": "success", "package_path": old_zip, "package_sha256": "old"})
        with mock.patch("sb_mexico.pipeline.execute_pipeline", side_effect=RuntimeError("current failed")):
            with redirect_stderr(io.StringIO()):
                run_pipeline_task(self.config)
        with build_lock:
            self.assertEqual(active_build["status"], "error")
            self.assertIsNone(active_build["package_path"])
        self.assertTrue(os.path.isfile(old_zip))

    def test_same_path_city_rename_invalidates_old_code_zip(self):
        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump({"city": {"code": "OLD"}}, stream)
        old_zip = os.path.join(self.tempdir, "OLD.zip")
        with open(old_zip, "wb") as stream:
            stream.write(b"old-package")
        digest = __import__("hashlib").sha256(b"old-package").hexdigest()
        with build_lock:
            active_build.update({
                "status": "success",
                "city_code": "OLD",
                "config_file": os.path.normcase(os.path.realpath(self.config)),
                "build_id": "old-build",
                "config_identity": _config_identity(self.config),
                "package_path": old_zip,
                "package_sha256": digest,
            })
        self.assertEqual(resolve_current_build_package(self.config), old_zip)

        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump({"city": {"code": "NEW"}}, stream)
        self.assertIsNone(resolve_current_build_package(self.config))
        self.assertTrue(os.path.isfile(old_zip))
        with build_lock:
            self.assertIsNone(active_build["package_path"])
            self.assertIsNone(active_build["build_id"])

    def test_same_path_config_content_change_invalidates_package(self):
        package = os.path.join(self.tempdir, "NEW.zip")
        with open(package, "wb") as stream:
            stream.write(b"current-package")
        digest = __import__("hashlib").sha256(b"current-package").hexdigest()
        with build_lock:
            active_build.update({
                "status": "success",
                "city_code": "NEW",
                "config_file": os.path.normcase(os.path.realpath(self.config)),
                "build_id": "current-build",
                "config_identity": _config_identity(self.config),
                "package_path": package,
                "package_sha256": digest,
            })
        self.assertEqual(resolve_current_build_package(self.config), package)

        with open(self.config, "a", encoding="utf-8") as stream:
            stream.write("routing:\n  include_driving_path: true\n")
        self.assertIsNone(resolve_current_build_package(self.config))
        self.assertTrue(os.path.isfile(package))

    def test_successful_rebuild_after_rename_establishes_new_zip(self):
        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump({"city": {"code": "OLD"}}, stream)
        old_zip = os.path.join(self.tempdir, "OLD.zip")
        with open(old_zip, "wb") as stream:
            stream.write(b"old-package")
        with build_lock:
            active_build.update({
                "status": "success",
                "city_code": "OLD",
                "config_file": os.path.normcase(os.path.realpath(self.config)),
                "build_id": "old-build",
                "config_identity": _config_identity(self.config),
                "package_path": old_zip,
                "package_sha256": __import__("hashlib").sha256(b"old-package").hexdigest(),
            })
        self.assertEqual(resolve_current_build_package(self.config), old_zip)

        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump({"city": {"code": "NEW"}}, stream)
        self.assertIsNone(resolve_current_build_package(self.config))

        new_zip = os.path.join(self.tempdir, "NEW.zip")
        with open(new_zip, "wb") as stream:
            stream.write(b"new-package")
        digest = __import__("hashlib").sha256(b"new-package").hexdigest()
        with build_lock:
            active_build.update({
                "status": "success",
                "city_code": "NEW",
                "config_file": os.path.normcase(os.path.realpath(self.config)),
                "build_id": "new-build",
                "config_identity": _config_identity(self.config),
                "package_path": new_zip,
                "package_sha256": digest,
            })
        self.assertEqual(resolve_current_build_package(self.config), new_zip)

    def test_skip_map_rejects_unverifiable_old_artifacts(self):
        os.makedirs(self.output)
        with open(os.path.join(self.output, "NEW.pmtiles"), "wb") as stream:
            stream.write(b"old")
        with open(os.path.join(self.output, "roads.geojson"), "w") as stream:
            stream.write("{}")
        with mock.patch("sb_mexico.pipeline.resolve_source_manifest", return_value={"sources": {"osm": []}}):
            with self.assertRaisesRegex(ValueError, "unverifiable"):
                execute_pipeline(self.config, skip_map=True, output_dir=self.output)

    def test_skip_map_rejects_bbox_or_source_fingerprint_change(self):
        os.makedirs(self.output)
        with open(os.path.join(self.output, "cartography_manifest.json"), "w") as stream:
            json.dump({"city_code": "NEW", "fingerprint": "previous-inputs"}, stream)
        with mock.patch("sb_mexico.pipeline.resolve_source_manifest", return_value={"sources": {"osm": []}}):
            with self.assertRaisesRegex(ValueError, "stale/incompatible"):
                execute_pipeline(self.config, skip_map=True, output_dir=self.output)

    def test_package_validation_rejects_duplicate_and_dangling_references(self):
        os.makedirs(self.output)
        with open(os.path.join(self.output, "config.json"), "w") as stream:
            json.dump({"code": "NEW"}, stream)
        with open(os.path.join(self.output, "demand_data.json"), "w") as stream:
            json.dump({
                "points": [{"id": "p", "popIds": ["missing"]}, {"id": "p"}],
                "pops": [{"id": "x", "residenceId": "p", "jobId": "missing"}],
            }, stream)
        with open(os.path.join(self.output, "NEW.pmtiles"), "wb") as stream:
            stream.write(b"map")
        with open(os.path.join(self.output, "roads.geojson"), "w") as stream:
            stream.write("{}")
        with open(os.path.join(self.output, "cartography_manifest.json"), "w") as stream:
            json.dump({"city_code": "NEW"}, stream)
        with self.assertRaisesRegex(ValueError, "duplicate entity IDs"):
            validate_package_integrity(self.output, "NEW")

    def test_special_demand_cross_file_validation_is_fatal_capable(self):
        doc = {"version": 1, "map_code": "OLD", "points": [
            {"point_id": "poi", "type": "airport", "name": {"__default__": "A"}, "pop_ids": ["nope"]},
            {"point_id": "poi", "type": "airport", "name": {"__default__": "B"}, "pop_ids": []},
        ]}
        valid, errors = validate_special_demand_points(
            doc,
            demand_data={"points": [{"id": "poi"}], "pops": [{"id": "pop"}]},
            expected_map_code="NEW",
        )
        self.assertFalse(valid)
        self.assertTrue(any("map_code" in error for error in errors))
        self.assertTrue(any("duplicado" in error for error in errors))
        self.assertTrue(any("inexistentes" in error for error in errors))

    def test_depot_sanitizer_rejection_is_fatal(self):
        class RejectingDemand:
            def __init__(self, payload):
                self.payload = payload

            def sanitize(self):
                raise ValueError("sanitizer rejected payload")

        depot_package = types.ModuleType("depot")
        depot_demand = types.ModuleType("depot.demand")
        depot_demand.DemandData = RejectingDemand
        with mock.patch.dict(sys.modules, {"depot": depot_package, "depot.demand": depot_demand}):
            with self.assertRaisesRegex(ValueError, "sanitizer rejected"):
                _prepare_depot_demand([], [])

    def test_wizard_roundtrip_preserves_runtime_sections(self):
        original = {
            "city": {"code": "NEW", "name": "Before", "bbox": [0, 0, 1, 1]},
            "macroeconomics": {},
            "temporal": {"model_year": 2030, "source_vintages": {"denue": 2029}},
            "routing": {"include_driving_path": True},
            "data_integrity": {"strict": True},
            "data_sources": {"osm": "data/source.osm.pbf"},
        }
        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump(original, stream)
        save_full_city_data(self.config, {"city": {"name": "After"}})
        saved = load_city_data(self.config)
        self.assertEqual(saved["city"]["name"], "After")
        for key in ("temporal", "routing", "data_integrity", "data_sources"):
            self.assertEqual(saved[key], original[key])

    def test_wizard_created_project_meets_runtime_temporal_requirement(self):
        project_data = os.path.join(self.tempdir, "project-data")
        with mock.patch("tools.wizard.CITIES_DIR", self.tempdir):
            created = create_new_project("Runtime Valid", "RTV", data_dir=project_data)
        runtime_cfg = load_city_config(created["path"])
        self.assertEqual(runtime_cfg["temporal"]["model_year"], 2025)
        self.assertEqual(runtime_cfg["temporal"]["cpv_base_year"], 2020)

    def test_full_mocked_pipeline_reaches_routing_export_and_package(self):
        osm_path = os.path.join(self.tempdir, "source.osm.pbf")
        with open(osm_path, "wb") as stream:
            stream.write(b"osm-content")
        cfg = {
            "city": {"code": "NEW", "name": "New City", "description": "test", "bbox": [0, 0, 1, 1]},
            "macroeconomics": {"tasa_pea": .6, "til_1_state": .4},
            "temporal": {"model_year": 2025, "cpv_base_year": 2020},
            "pois": [],
        }
        with open(self.config, "w", encoding="utf-8") as stream:
            yaml.safe_dump(cfg, stream)
        manifest = {"project_dir": self.tempdir, "sources": {
            "denue": [{"path": "denue.csv", "year": 2025}],
            "cpv": [{"path": "cpv.csv", "year": 2020}],
            "osm": [{"path": osm_path, "year": None}],
            "roads": [], "ce": [], "enoe": [], "conapo": [], "marco": [],
        }}
        points = [
            {"id": "o", "location": [0.1, 0.1], "residents": 20, "pea_15ymas": 10, "jobs": 0, "popIds": ["pop_1"]},
            {"id": "d", "location": [0.2, 0.2], "residents": 0, "pea_15ymas": 0, "jobs": 10, "popIds": ["pop_1"]},
        ]
        pops = [{"id": "pop_1", "size": 10, "residenceId": "o", "jobId": "d"}]
        diag = {
            "feasibility": {"origin_count": 1, "destination_count": 1, "support_vertex_count": 2,
                "support_edge_count": 1, "components": [], "max_flow_shortfall": 0,
                "validation_seconds": 0.0, "max_flow_seconds": 0.0},
            "integerization": {"integerization_seconds": 0.0},
            "ipfp": {"ipfp_seconds": 0.0}, "undersized_cohorts": [],
        }
        distribution = {"brackets": [], "median_km": 1.0, "p25_km": .5, "p75_km": 1.5,
            "p95_km": 2.0, "mean_km": 1.0, "mean_minutes": 2.0, "profile_label": "test"}

        def fake_map(**kwargs):
            with open(os.path.join(kwargs["output_dir"], "NEW.pmtiles"), "wb") as stream:
                stream.write(b"map")
            with open(os.path.join(kwargs["output_dir"], "roads.geojson"), "w") as stream:
                stream.write('{"type":"FeatureCollection","features":[]}')

        def fake_route(**kwargs):
            kwargs["pops"][0]["drivingDistance"] = 1000
            kwargs["pops"][0]["drivingSeconds"] = 120
            return 1, 0

        denue = pd.DataFrame([{"calibrated_jobs": 10.0}])
        cpv = pd.DataFrame([{"lon": .1, "lat": .1, "pobtot_adj": 20.0, "pea_real": 10.0}])
        patches = {
            "resolve_source_manifest": mock.DEFAULT,
            "build_city_map": mock.DEFAULT,
            "load_denue": mock.DEFAULT,
            "calibrate_denue_employment": mock.DEFAULT,
            "load_cpv_demography": mock.DEFAULT,
            "build_demand_grid": mock.DEFAULT,
            "build_gravity_input_contract": mock.DEFAULT,
            "simulate_gravity_demand": mock.DEFAULT,
            "merge_identical_commutes": mock.DEFAULT,
            "sync_demand_points_and_pops": mock.DEFAULT,
            "is_docker_available": mock.DEFAULT,
            "prepare_osrm_network_wsl": mock.DEFAULT,
            "start_osrm_daemon_wsl": mock.DEFAULT,
            "enrich_pops_with_osrm": mock.DEFAULT,
            "stop_osrm_daemon_wsl": mock.DEFAULT,
            "calculate_commute_distance_distribution": mock.DEFAULT,
            "validate_cohort_spatial_integrity": mock.DEFAULT,
            "sanitize_demand_points": mock.DEFAULT,
        }
        with mock.patch.multiple("sb_mexico.pipeline", **patches) as mocked:
            mocked["resolve_source_manifest"].return_value = manifest
            mocked["build_city_map"].side_effect = fake_map
            mocked["load_denue"].return_value = denue
            mocked["calibrate_denue_employment"].return_value = (denue, {})
            mocked["load_cpv_demography"].return_value = cpv
            mocked["build_demand_grid"].return_value = (points, [], {})
            mocked["build_gravity_input_contract"].return_value = {"authoritative_marginals": {"workers_origins_pea": 10}}
            mocked["simulate_gravity_demand"].return_value = (pops, diag)
            mocked["merge_identical_commutes"].return_value = pops
            mocked["sync_demand_points_and_pops"].return_value = (points, pops)
            mocked["is_docker_available"].return_value = (True, "mock")
            mocked["prepare_osrm_network_wsl"].return_value = (True, self.tempdir)
            mocked["start_osrm_daemon_wsl"].return_value = True
            mocked["enrich_pops_with_osrm"].side_effect = fake_route
            mocked["calculate_commute_distance_distribution"].return_value = distribution
            mocked["sanitize_demand_points"].return_value = points
            result = _execute_pipeline_run(self.config, output_dir=self.output, build_id="build", config_identity="cfg")
        self.assertTrue(result.package_created)
        self.assertTrue(os.path.isfile(result.package_path))
        mocked["enrich_pops_with_osrm"].assert_called_once()


if __name__ == "__main__":
    unittest.main()
