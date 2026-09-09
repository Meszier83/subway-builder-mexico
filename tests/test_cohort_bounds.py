"""
tests.test_cohort_bounds
========================
Verifica la calibracion estricta de limites de cohortes demograficas (min_pop_size y max_pop_size),
la particion balanceada de flujos grandes y la conservacion invariante de masa PEA.
"""

import math
import unittest
from sb_mexico.gravity import (
    simulate_gravity_demand,
    merge_identical_commutes,
    consolidate_small_pops
)


class TestCohortBounds(unittest.TestCase):
    def test_balanced_partitioning_formula(self):
        """Verifica que el algoritmo de particion divida flujos grandes sin colas residuales pequenas."""
        test_cases = [
            (210, 200, 25),   # 105 y 105 (no 200 y 10)
            (205, 200, 25),   # 103 y 102 (no 200 y 5)
            (450, 200, 25),   # 150, 150, 150
            (410, 200, 25),   # 137, 137, 136
            (50, 200, 25),    # 50 cabe directo en un solo chunk
            (25, 200, 25),    # Exactamente min_pop_size
        ]

        for total_size, max_pop, min_pop in test_cases:
            if total_size > max_pop:
                k = math.ceil(total_size / max_pop)
                base = total_size // k
                rem = total_size % k
                chunks = [(base + 1) if i < rem else base for i in range(k)]
            else:
                chunks = [total_size]

            self.assertEqual(sum(chunks), total_size, f"Fallo de suma para total={total_size}")
            for c in chunks:
                self.assertLessEqual(c, max_pop, f"Chunk {c} supera max_pop {max_pop}")
                self.assertGreaterEqual(c, min_pop, f"Chunk {c} es menor que min_pop {min_pop}")

    def test_simulate_gravity_demand_cohort_bounds(self):
        """Verifica que simulate_gravity_demand respete min_pop_size y conserve la masa PEA."""
        origins = [
            {"id": "orig_0", "location": [-86.85, 21.15], "residents": 500, "jobs": 0, "pea_15ymas": 300, "popIds": []},
            {"id": "orig_1", "location": [-86.86, 21.16], "residents": 300, "jobs": 0, "pea_15ymas": 180, "popIds": []},
        ]
        dests = [
            {"id": "dest_0", "location": [-86.84, 21.14], "residents": 0, "jobs": 250, "pea_15ymas": 0, "popIds": []},
            {"id": "dest_1", "location": [-86.83, 21.13], "residents": 0, "jobs": 250, "pea_15ymas": 0, "popIds": []},
        ]
        demand_points = origins + dests

        min_pop = 25
        max_pop = 150
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            beta=0.12,
            max_pop_size=max_pop,
            min_pop_size=min_pop,
            target_pop_size=100,
            max_distance_km=25.0,
            seed=42
        )

        total_pea_expected = 300 + 180  # 480
        total_pax_generated = sum(p["size"] for p in pops)

        self.assertEqual(total_pax_generated, total_pea_expected, "Conservacion estricta de masa PEA fallida")

        for p in pops:
            self.assertLessEqual(p["size"], max_pop, f"Pop {p} supera max_pop {max_pop}")
            self.assertGreaterEqual(p["size"], min_pop, f"Pop {p} es menor que min_pop {min_pop}")

    def test_merge_identical_commutes_cohort_bounds(self):
        """Verifica que al fusionar trayectos identicos no se generen micro-colas menores a min_pop_size."""
        sample_pops = [
            {"id": "pop_001", "residenceId": "orig_0", "jobId": "dest_0", "size": 100, "drivingSeconds": 600, "drivingDistance": 5000},
            {"id": "pop_002", "residenceId": "orig_0", "jobId": "dest_0", "size": 110, "drivingSeconds": 600, "drivingDistance": 5000},
        ]

        merged = merge_identical_commutes(sample_pops, max_pop_size=200, min_pop_size=25)
        total_merged = sum(p["size"] for p in merged)
        self.assertEqual(total_merged, 210)
        self.assertEqual(len(merged), 2)
        for p in merged:
            self.assertEqual(p["size"], 105)

    def test_consolidate_small_pops_dynamic_threshold(self):
        """Verifica que consolidate_small_pops use el umbral dinamico basado en min_pop_size."""
        d_points = [
            {"id": "orig_0", "location": [-86.850, 21.150], "residents": 50, "jobs": 0, "popIds": ["pop_001", "pop_002"]},
            {"id": "dest_0", "location": [-86.840, 21.140], "residents": 0, "jobs": 50, "popIds": ["pop_001", "pop_002"]},
        ]
        sample_pops = [
            {"id": "pop_001", "residenceId": "orig_0", "jobId": "dest_0", "size": 10, "drivingSeconds": 300, "drivingDistance": 2000},
            {"id": "pop_002", "residenceId": "orig_0", "jobId": "dest_0", "size": 15, "drivingSeconds": 300, "drivingDistance": 2000},
        ]

        _, consolidated = consolidate_small_pops(d_points, sample_pops, min_pop_size=25, max_pop_size=200)
        total_before = sum(p["size"] for p in sample_pops)
        total_after = sum(p["size"] for p in consolidated)
        self.assertEqual(total_before, total_after, "La consolidacion debe conservar la masa")


if __name__ == "__main__":
    unittest.main()
