import unittest
import math
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
from sb_mexico.gravity import (
    build_demand_grid,
    simulate_gravity_demand,
    sanitize_demand_points,
    calculate_commute_impedance
)

class TestGravity(unittest.TestCase):
    def test_calculate_commute_impedance(self):
        dist_m, sec = calculate_commute_impedance(5.0)
        self.assertGreater(dist_m, 5000)
        self.assertGreater(sec, 180)

    def test_simulate_gravity_demand_strict_conservation(self):
        demand_points = [
            {"id": "orig_01", "location": [-86.85, 21.15], "jobs": 10, "residents": 500, "pea_15ymas": 100, "popIds": []},
            {"id": "orig_02", "location": [-86.84, 21.14], "jobs": 20, "residents": 300, "pea_15ymas": 60, "popIds": []},
            {"id": "orig_03", "location": [-86.82, 21.12], "jobs": 5, "residents": 800, "pea_15ymas": 150, "popIds": []},
            {"id": "AIR_Cancun", "location": [-86.87, 21.03], "jobs": 200, "residents": 0, "pea_15ymas": 0, "popIds": [], "is_special": True},
            {"id": "dest_reg_1", "location": [-86.83, 21.15], "jobs": 300, "residents": 50, "pea_15ymas": 0, "popIds": []},
        ]
        total_pea = sum(p.get("pea_15ymas", 0) for p in demand_points)
        pops = simulate_gravity_demand(demand_points=demand_points, beta=0.12, max_pop_size=50, seed=42)
        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, total_pea)

        # Check immutability
        self.assertIn("pea_15ymas", demand_points[0])

    def test_sanitize_demand_points(self):
        demand_points = [
            {"id": "dp_1", "location": [-86.85, 21.15], "jobs": 10, "residents": 500, "pea_15ymas": 100, "is_special": True, "popIds": ["pop_001"]}
        ]
        clean = sanitize_demand_points(demand_points)
        self.assertEqual(set(clean[0].keys()), {"id", "location", "jobs", "residents", "popIds"})
        self.assertNotIn("pea_15ymas", clean[0])
        self.assertNotIn("is_special", clean[0])

    def test_build_demand_grid(self):
        df_denue = pd.DataFrame([
            {"lon": -86.85, "lat": 21.15, "calibrated_jobs": 25.0},
            {"lon": -86.8505, "lat": 21.1505, "calibrated_jobs": 15.0},
        ])
        df_cpv = pd.DataFrame([
            {"lon": -86.85, "lat": 21.15, "pobtot_adj": 120.0, "pea_real": 75.0}
        ])
        roads_gdf = gpd.GeoDataFrame({"geometry": [LineString([(-86.90, 21.10), (-86.80, 21.20)])]}, crs="EPSG:4326")
        pois = [{"id": "UNI_Test", "loc": [-86.85, 21.15], "jobs": 1000, "radius_m": 800, "mode": "MAX"}]

        points, poi_audit = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=pois,
            roads_gdf=roads_gdf,
            grid_size=0.0025,
            min_residents=5,
            min_jobs=2
        )
        self.assertGreater(len(points), 0)
        self.assertEqual(poi_audit[0]["id"], "UNI_Test")
        self.assertEqual(poi_audit[0]["absorbed"], 40)

    def test_overlapping_pois_argmin(self):
        # Dos POIs con radios solapados
        pois = [
            {"id": "POI_A", "loc": [-86.85, 21.15], "jobs": 500, "radius_m": 1000, "mode": "MAX"},
            {"id": "POI_B", "loc": [-86.86, 21.15], "jobs": 500, "radius_m": 1000, "mode": "MAX"},
        ]
        # Establecimiento más cercano a POI_B (-86.858 está a ~220m de POI_B y a ~880m de POI_A)
        df_denue = pd.DataFrame([
            {"lon": -86.858, "lat": 21.15, "calibrated_jobs": 50.0}
        ])
        df_cpv = pd.DataFrame([
            {"lon": -86.85, "lat": 21.15, "pobtot_adj": 10.0, "pea_real": 5.0}
        ])
        roads_gdf = gpd.GeoDataFrame({"geometry": [LineString([(-86.90, 21.10), (-86.80, 21.20)])]}, crs="EPSG:4326")

        _, poi_audit = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=pois,
            roads_gdf=roads_gdf
        )
        audit_dict = {p["id"]: p["absorbed"] for p in poi_audit}
        self.assertEqual(audit_dict["POI_B"], 50)
        self.assertEqual(audit_dict["POI_A"], 0)

if __name__ == "__main__":
    unittest.main()

