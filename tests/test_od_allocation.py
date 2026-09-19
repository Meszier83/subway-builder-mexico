import itertools
import unittest

import numpy as np

from sb_mexico.gravity import (
    ODFeasibilityError,
    ODIntegerizationError,
    build_od_support,
    furness_ipfp_balance,
    integerize_od_matrix,
    pack_od_cohorts,
    simulate_gravity_demand,
    sync_demand_points_and_pops,
    validate_od_feasibility,
)


class TestODFeasibility(unittest.TestCase):
    def test_only_self_trip_is_removed_and_infeasibility_is_reported(self):
        support = build_od_support(
            ["only"], ["only"], np.array([[0.0]]), np.array([0]), np.array([0]), 55.0
        )
        np.testing.assert_array_equal(support, [[False]])
        with self.assertRaisesRegex(ODFeasibilityError, "zero allowed degree"):
            validate_od_feasibility([1], [1], support, ["only"], ["only"])

    def test_shape_validation_and_zero_mass(self):
        with self.assertRaises(ODFeasibilityError):
            validate_od_feasibility([[1]], [1], [[True]])
        result = validate_od_feasibility([0, 0], [0], [[False], [False]])
        self.assertTrue(result["feasible"])
        self.assertEqual(result["max_flow"], 0.0)

    def test_total_mass_mismatch(self):
        with self.assertRaises(ODFeasibilityError) as ctx:
            validate_od_feasibility([2], [1], [[True]])
        self.assertIn("total", str(ctx.exception).lower())

    def test_positive_zero_degree_origin_and_destination(self):
        with self.assertRaises(ODFeasibilityError) as ctx:
            validate_od_feasibility([1, 1], [1, 1], [[True, False], [False, False]])
        self.assertEqual(ctx.exception.diagnostics["unsupported_origin_ids"], ["1"])
        self.assertEqual(ctx.exception.diagnostics["unsupported_destination_ids"], ["1"])

    def test_disconnected_feasible_components(self):
        result = validate_od_feasibility([2, 3], [2, 3], [[True, False], [False, True]])
        self.assertTrue(result["feasible"])
        self.assertEqual(len(result["components"]), 2)

    def test_disconnected_unequal_components(self):
        with self.assertRaises(ODFeasibilityError) as ctx:
            validate_od_feasibility([2, 3], [3, 2], [[True, False], [False, True]])
        self.assertIn("unequal_components", ctx.exception.diagnostics)

    def test_connected_hall_infeasibility_has_cut_witness(self):
        support = np.array([
            [True, True, False],
            [True, True, False],
            [False, True, True],
        ])
        with self.assertRaises(ODFeasibilityError) as ctx:
            validate_od_feasibility([4, 4, 2], [3, 3, 4], support)
        diagnostics = ctx.exception.diagnostics
        self.assertGreater(diagnostics["max_flow_shortfall"], 0)
        self.assertIn("minimum_cut_witness", diagnostics)

    def test_sparse_feasible_and_forbidden_edges_are_not_relaxed(self):
        support = np.array([[True, False, False], [False, True, True]])
        result = validate_od_feasibility([2, 5], [2, 3, 2], support)
        self.assertEqual(result["support_edge_count"], 3)
        with self.assertRaises(ODFeasibilityError):
            validate_od_feasibility([3, 4], [2, 3, 2], support)


class TestODIntegerization(unittest.TestCase):
    def assert_marginals(self, result, rows, columns):
        np.testing.assert_array_equal(result.sum(axis=1), rows)
        np.testing.assert_array_equal(result.sum(axis=0), columns)

    def test_one_origin_two_equal_destinations(self):
        result, _ = integerize_od_matrix(
            np.array([[50.0, 50.0]]), [100], [50, 50], [[True, True]]
        )
        np.testing.assert_array_equal(result, [[50, 50]])

    def test_tiny_fractional_flows_and_determinism(self):
        table = np.array([[0.2, 0.8], [0.8, 0.2]])
        outputs = [integerize_od_matrix(table, [1, 1], [1, 1], np.ones((2, 2), bool))[0]
                   for _ in range(5)]
        for result in outputs[1:]:
            np.testing.assert_array_equal(result, outputs[0])
        self.assert_marginals(outputs[0], [1, 1], [1, 1])

    def test_near_integer_cells(self):
        table = np.array([[0.999999, 0.000001], [0.000001, 0.999999]])
        result, _ = integerize_od_matrix(table, [1, 1], [1, 1], np.ones((2, 2), bool))
        np.testing.assert_array_equal(result, np.eye(2, dtype=int))

    def test_multiple_origins_one_destination(self):
        result, _ = integerize_od_matrix(np.array([[2.0], [3.0]]), [2, 3], [5], [[1], [1]])
        self.assert_marginals(result, [2, 3], [5])

    def test_disconnected_components_and_forbidden_zeroes(self):
        table = np.array([[1.4, 0.6, 0.0], [0.0, 0.0, 2.0]])
        support = np.array([[True, True, False], [False, False, True]])
        result, _ = integerize_od_matrix(table, [2, 2], [1.4, 0.6, 2], support)
        self.assertTrue(np.all(result[~support] == 0))
        self.assert_marginals(result, [2, 2], [1, 1, 2])

    def test_fractional_destination_apportionment_is_support_aware(self):
        table = np.array([[0.6, 0.4], [0.0, 1.0]])
        support = np.array([[True, True], [False, True]])
        result, _ = integerize_od_matrix(table, [1, 1], [0.6, 1.4], support)
        np.testing.assert_array_equal(result, [[1, 0], [0, 1]])

    def test_small_bruteforce_minimum_absolute_error(self):
        table = np.array([[0.45, 1.55], [1.55, 0.45]])
        result, diagnostics = integerize_od_matrix(table, [2, 2], [2, 2], np.ones((2, 2), bool))
        candidates = []
        floors = np.floor(table).astype(int)
        for bits in itertools.product([0, 1], repeat=4):
            candidate = floors + np.array(bits).reshape(2, 2)
            if np.array_equal(candidate.sum(axis=1), [2, 2]) and np.array_equal(candidate.sum(axis=0), [2, 2]):
                candidates.append(float(np.abs(candidate - table).sum()))
        self.assertAlmostEqual(diagnostics["absolute_rounding_error"], min(candidates))

    def test_rejects_nonfinite_and_materially_negative_continuous_values(self):
        invalid_tables = [
            np.array([[np.nan]]),
            np.array([[np.inf]]),
            np.array([[-np.inf]]),
            np.array([[2.0, -1.0], [-1.0, 2.0]]),
        ]
        for table in invalid_tables:
            marginals = [1] if table.shape == (1, 1) else [1, 1]
            with self.subTest(table=table):
                with self.assertRaises(ODIntegerizationError):
                    integerize_od_matrix(
                        table, marginals, marginals, np.ones(table.shape, dtype=bool)
                    )

    def test_normalizes_only_tiny_negative_zero_and_returns_nonnegative(self):
        table = np.array([[1.0, -5e-9], [0.0, 1.0]])
        result, _ = integerize_od_matrix(
            table, [1, 1], [1, 1], np.ones((2, 2), dtype=bool), tolerance=1e-8
        )
        np.testing.assert_array_equal(result, np.eye(2, dtype=int))
        self.assertTrue(np.all(result >= 0))

    def test_dense_thousand_worker_ipfp_uses_integerizer_tolerance_contract(self):
        origins = np.array([613.0, 387.0])
        destinations = np.array([421.0, 579.0])
        distances = np.array([[1.0, 7.0], [4.0, 2.0]])
        support = np.ones((2, 2), dtype=bool)
        continuous, diagnostics = furness_ipfp_balance(
            origins,
            destinations,
            distances,
            allowed_support=support,
            tol=1e-10,
            absolute_tolerance=1e-8,
            return_diagnostics=True,
        )
        result, _ = integerize_od_matrix(
            continuous,
            origins,
            destinations,
            support,
            tolerance=1e-8,
            relative_tolerance=1e-10,
        )
        self.assertTrue(diagnostics["converged"])
        self.assert_marginals(result, origins.astype(int), destinations.astype(int))

    def test_boundary_feasible_face_forces_nominal_edge_to_zero(self):
        origins = np.array([1.0, 1.0])
        destinations = np.array([1.0, 1.0])
        support = np.array([[True, True], [True, False]])
        continuous, diagnostics = furness_ipfp_balance(
            origins,
            destinations,
            np.ones((2, 2)),
            allowed_support=support,
            max_iter=25,
            return_diagnostics=True,
        )
        np.testing.assert_allclose(continuous, [[0.0, 1.0], [1.0, 0.0]])
        self.assertEqual(diagnostics["structurally_forced_zero_edges"], 1)
        result, _ = integerize_od_matrix(continuous, origins, destinations, support)
        np.testing.assert_array_equal(result, [[0, 1], [1, 0]])


class TestODCohorts(unittest.TestCase):
    def test_exact_conservation_bounds_and_minimum(self):
        sizes, diagnostics = pack_od_cohorts(450, 25, 180, 200)
        self.assertEqual(sum(sizes), 450)
        self.assertTrue(all(25 <= size <= 200 for size in sizes))
        self.assertFalse(diagnostics["undersized"])

    def test_subminimum_cell_is_preserved_and_diagnosed(self):
        sizes, diagnostics = pack_od_cohorts(7, 25, 180, 200)
        self.assertEqual(sizes, [7])
        self.assertTrue(diagnostics["undersized"])

    def test_minimum_above_hard_maximum_does_not_drop_flow(self):
        sizes, diagnostics = pack_od_cohorts(450, 250, 180, 200)
        self.assertEqual(sizes, [200, 200, 50])
        self.assertEqual(sum(sizes), 450)
        self.assertTrue(diagnostics["undersized"])

    def test_target_larger_than_origin_keeps_multiple_destinations(self):
        points = [
            {"id": "o", "location": [0, 0], "pea_15ymas": 100, "jobs": 0, "popIds": []},
            {"id": "a", "location": [0.01, 0], "pea_15ymas": 0, "jobs": 50, "popIds": []},
            {"id": "b", "location": [-0.01, 0], "pea_15ymas": 0, "jobs": 50, "popIds": []},
        ]
        pops, diagnostics = simulate_gravity_demand(
            points, target_pop_size=180, max_pop_size=200, return_diagnostics=True
        )
        by_destination = {job_id: sum(p["size"] for p in pops if p["jobId"] == job_id)
                          for job_id in ("a", "b")}
        self.assertEqual(by_destination, {"a": 50, "b": 50})
        np.testing.assert_array_equal(diagnostics["integer_od"], [[50, 50]])

    def test_sync_detects_post_allocation_destination_corruption(self):
        points = [{
            "id": "d", "location": [0, 0], "jobs": 5, "residents": 0,
            "_authoritative_od_jobs": 5, "_authoritative_od_residents": 0,
        }]
        with self.assertRaisesRegex(ValueError, "destination marginal changed"):
            sync_demand_points_and_pops(points, [], remove_orphans=False)

    def test_sync_validates_authoritative_orphan_before_removal(self):
        points = [{
            "id": "lost_destination", "location": [0, 0], "jobs": 5, "residents": 0,
            "_authoritative_od_jobs": 5, "_authoritative_od_residents": 0,
        }]
        with self.assertRaisesRegex(ValueError, "destination marginal changed"):
            sync_demand_points_and_pops(points, [], remove_orphans=True)

    def test_sync_rejects_unknown_endpoints(self):
        points = [{"id": "known", "location": [0, 0], "jobs": 0, "residents": 1}]
        pops = [{"id": "p", "residenceId": "known", "jobId": "missing", "size": 1}]
        with self.assertRaisesRegex(ValueError, "unknown OD endpoint"):
            sync_demand_points_and_pops(points, pops, remove_orphans=True)


if __name__ == "__main__":
    unittest.main()
