import math
import os
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from sb_mexico.gravity import (
    IPFPConvergenceError,
    ODFeasibilityError,
    build_demand_grid,
    cluster_demand_points,
    furness_ipfp_balance,
    is_point_in_exclusion_zone,
    simulate_gravity_demand,
)
from sb_mexico.osrm import (
    calculate_canonical_driving_fallback,
    compute_osrm_fingerprint,
    enrich_pops_with_osrm,
)
from sb_mexico.pipeline import validate_cohort_spatial_integrity


class TestIPFPRemediation(unittest.TestCase):
    def test_adversarial_matrix_checks_both_marginals_after_full_cycle(self):
        origins = np.array([90.0, 10.0])
        destinations = np.array([10.0, 90.0])
        # Keep all four edges in the feasible interior. Boundary-face behavior
        # has its own regression in test_od_allocation.py.
        distances = np.array([[1.0, 9.0], [9.0, 1.0]])

        with self.assertRaises(IPFPConvergenceError) as ctx:
            furness_ipfp_balance(
                origins, destinations, distances,
                max_distance_km=10.0, max_iter=1, tol=0.02,
            )
        self.assertEqual(ctx.exception.diagnostics["iterations"], 1)
        self.assertGreater(ctx.exception.diagnostics["max_row_residual"], 0.02)

        flows, diagnostics = furness_ipfp_balance(
            origins, destinations, distances,
            max_distance_km=10.0, max_iter=500, tol=0.01,
            return_diagnostics=True,
        )
        np.testing.assert_allclose(flows.sum(axis=1), origins, rtol=0.01)
        np.testing.assert_allclose(flows.sum(axis=0), destinations, rtol=0.01)
        self.assertTrue(diagnostics["converged"])
        self.assertGreater(diagnostics["iterations"], 1)
        self.assertLessEqual(diagnostics["max_row_residual"], 0.01)
        self.assertLessEqual(diagnostics["max_column_residual"], 0.01)

    def test_zero_support_row_from_max_distance_fails(self):
        with self.assertRaisesRegex(ValueError, "zero allowed degree"):
            furness_ipfp_balance(
                np.array([10.0, 10.0]),
                np.array([10.0, 10.0]),
                np.array([[1.0, 2.0], [100.0, 101.0]]),
                max_distance_km=10.0,
            )

    def test_disconnected_support_never_creates_forbidden_edge(self):
        support = np.array([[True, False], [False, True]])
        with self.assertRaises(ODFeasibilityError):
            furness_ipfp_balance(
                np.array([80.0, 20.0]),
                np.array([50.0, 50.0]),
                np.ones((2, 2)),
                allowed_support=support,
                max_iter=25,
                tol=1e-6,
            )

    def test_island_origin_without_island_destination_fails(self):
        demand_points = [
            {"id": "island_home", "location": [0.0, 0.0], "pea_15ymas": 20,
             "jobs": 0, "residents": 20, "popIds": []},
            {"id": "mainland_job", "location": [1.0, 1.0], "pea_15ymas": 0,
             "jobs": 20, "residents": 0, "popIds": []},
        ]
        island = [{"id": "island", "type": "bbox", "bbox": [-0.1, -0.1, 0.1, 0.1]}]
        with self.assertRaisesRegex(ValueError, "zero allowed degree"):
            simulate_gravity_demand(demand_points, isolated_zones=island)


class TestRouteMutationRemediation(unittest.TestCase):
    def test_mutation_invalidates_metrics_and_failure_recomputes_final_fallback(self):
        points = [
            {"id": "large", "location": [0.0, 0.0], "residents": 100, "jobs": 0, "popIds": []},
            {"id": "small", "location": [0.0005, 0.0], "residents": 10, "jobs": 0, "popIds": []},
            {"id": "dest", "location": [0.1, 0.0], "residents": 0, "jobs": 110,
             "popIds": [], "is_special": True},
        ]
        pops = [
            {"id": "p1", "residenceId": "large", "jobId": "dest", "size": 100,
             "drivingDistance": 1, "drivingSeconds": 1, "drivingPath": [[9, 9], [8, 8]]},
            {"id": "p2", "residenceId": "small", "jobId": "dest", "size": 10,
             "drivingDistance": 2, "drivingSeconds": 2, "drivingPath": [[7, 7], [6, 6]]},
        ]
        merged_points, mutated = cluster_demand_points(points, pops)
        changed = next(p for p in mutated if p["id"] == "p2")
        self.assertEqual(changed["residenceId"], "large")
        self.assertNotIn("drivingDistance", changed)
        self.assertNotIn("drivingSeconds", changed)
        self.assertNotIn("drivingPath", changed)

        with patch("requests.Session.get", side_effect=Exception("OSRM unavailable")):
            enrich_pops_with_osrm(mutated, merged_points, osrm_url="http://invalid:5000")

        final_by_id = {p["id"]: p["location"] for p in merged_points}
        origin = final_by_id[changed["residenceId"]]
        destination = final_by_id[changed["jobId"]]
        cos_lat = math.cos(math.radians((origin[1] + destination[1]) / 2.0))
        euclid_m = math.hypot(
            (destination[0] - origin[0]) * 111_320.0 * cos_lat,
            (destination[1] - origin[1]) * 110_574.0,
        )
        expected_distance, expected_seconds = calculate_canonical_driving_fallback(euclid_m)
        self.assertEqual(changed["drivingDistance"], expected_distance)
        self.assertEqual(changed["drivingSeconds"], expected_seconds)


class TestExclusionRemediation(unittest.TestCase):
    def setUp(self):
        self.zone = [{
            "id": "excluded", "coordinates": [
                [0.0005, -0.001], [0.0015, -0.001],
                [0.0015, 0.001], [0.0005, 0.001],
            ]
        }]

    def test_polygon_boundary_is_excluded(self):
        self.assertTrue(is_point_in_exclusion_zone(0.0005, 0.0, self.zone))

    def test_road_snap_cannot_move_point_into_exclusion(self):
        denue = pd.DataFrame([{"lon": 0.0, "lat": 0.0, "calibrated_jobs": 10.0}])
        cpv = pd.DataFrame([{"lon": 0.0, "lat": 0.0, "pobtot_adj": 20.0, "pea_real": 10.0}])
        roads = gpd.GeoDataFrame(
            {"geometry": [LineString([(0.001, -0.01), (0.001, 0.01)])], "highway": ["primary"]},
            crs="EPSG:4326",
        )
        points, _ = build_demand_grid(
            denue, cpv, special_pois=[], roads_gdf=roads, exclusion_zones=self.zone,
            min_residents=1, min_jobs=1,
        )
        self.assertEqual(points[0]["location"], [0.0, 0.0])
        self.assertFalse(is_point_in_exclusion_zone(*points[0]["location"], self.zone))

    def test_cluster_rejects_merge_whose_centroid_is_excluded(self):
        zone = [{"id": "middle", "coordinates": [
            [-0.0002, -0.001], [0.0002, -0.001],
            [0.0002, 0.001], [-0.0002, 0.001],
        ]}]
        points = [
            {"id": "left", "location": [-0.001, 0.0], "residents": 10, "jobs": 0, "popIds": []},
            {"id": "right", "location": [0.001, 0.0], "residents": 10, "jobs": 0, "popIds": []},
            {"id": "dest", "location": [0.01, 0.0], "residents": 0, "jobs": 20,
             "popIds": [], "is_special": True},
        ]
        pops = [
            {"id": "p1", "residenceId": "left", "jobId": "dest", "size": 10},
            {"id": "p2", "residenceId": "right", "jobId": "dest", "size": 10},
        ]
        final_points, _ = cluster_demand_points(points, pops, exclusion_zones=zone)
        regular = [p for p in final_points if not p.get("is_special")]
        self.assertEqual(len(regular), 2)
        self.assertTrue(all(
            not is_point_in_exclusion_zone(p["location"][0], p["location"][1], zone)
            for p in regular
        ))


class TestOSRMFingerprintRemediation(unittest.TestCase):
    def test_equal_metadata_different_pbf_content_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "first.pbf")
            second = os.path.join(tmp, "second.pbf")
            with open(first, "wb") as handle:
                handle.write(b"AAAA")
            with open(second, "wb") as handle:
                handle.write(b"BBBB")
            timestamp = 1_700_000_000
            os.utime(first, (timestamp, timestamp))
            os.utime(second, (timestamp, timestamp))
            self.assertNotEqual(
                compute_osrm_fingerprint("TST", [0, 0, 1, 1], first),
                compute_osrm_fingerprint("TST", [0, 0, 1, 1], second),
            )

    def test_profile_content_change_invalidates_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            pbf = os.path.join(tmp, "map.pbf")
            profile = os.path.join(tmp, "car.lua")
            with open(pbf, "wb") as handle:
                handle.write(b"map")
            with open(profile, "wb") as handle:
                handle.write(b"profile one")
            first = compute_osrm_fingerprint("TST", [0, 0, 1, 1], pbf, profile)
            with open(profile, "wb") as handle:
                handle.write(b"profile two")
            second = compute_osrm_fingerprint("TST", [0, 0, 1, 1], pbf, profile)
            self.assertNotEqual(first, second)


class TestFinalRouteValidationRemediation(unittest.TestCase):
    def setUp(self):
        self.points = [
            {"id": "a", "location": [0.0, 0.0]},
            {"id": "b", "location": [0.01, 0.0]},
        ]

    def test_rejects_unknown_ids_nonfinite_duration_and_impossible_speed(self):
        invalid_cases = [
            {"id": "unknown_o", "residenceId": "missing", "jobId": "b",
             "drivingDistance": 1000, "drivingSeconds": 100},
            {"id": "unknown_d", "residenceId": "a", "jobId": "missing",
             "drivingDistance": 1000, "drivingSeconds": 100},
            {"id": "nan", "residenceId": "a", "jobId": "b",
             "drivingDistance": math.nan, "drivingSeconds": 100},
            {"id": "zero_time", "residenceId": "a", "jobId": "b",
             "drivingDistance": 1000, "drivingSeconds": 0},
            {"id": "warp", "residenceId": "a", "jobId": "b",
             "drivingDistance": 10_000, "drivingSeconds": 10},
        ]
        with self.assertRaises(ValueError) as ctx:
            validate_cohort_spatial_integrity(invalid_cases, self.points)
        message = str(ctx.exception)
        self.assertIn("unknown residenceId", message)
        self.assertIn("unknown jobId", message)
        self.assertIn("non-finite", message)
        self.assertIn("drivingSeconds=0", message)
        self.assertIn("implied speed", message)

    def test_preserves_short_trip_duration_floor(self):
        validate_cohort_spatial_integrity([
            {"id": "short", "residenceId": "a", "jobId": "a",
             "drivingDistance": 195, "drivingSeconds": 45},
        ], self.points)


if __name__ == "__main__":
    unittest.main()
