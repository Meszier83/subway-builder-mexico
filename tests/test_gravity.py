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
    calculate_commute_impedance,
    furness_ipfp_balance
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

    def test_simulate_gravity_demand_quantized_cohorts(self):
        # 10 orígenes residenciales con 100 PEA cada uno (1000 total) y 50 destinos
        rng = np.random.default_rng(123)
        origins = [
            {"id": f"orig_{i}", "location": [-86.85 + (i * 0.005), 21.15], "jobs": 0, "residents": 200, "pea_15ymas": 100, "popIds": []}
            for i in range(10)
        ]
        dests = [
            {"id": f"dest_{j}", "location": [-86.80, 21.10 + (j * 0.002)], "jobs": 50, "residents": 0, "pea_15ymas": 0, "popIds": []}
            for j in range(50)
        ]
        pts = origins + dests
        total_pea = 1000

        # Con target_pop_size=35, cada origen de 100 PEA debe generar round(100/35) = 3 cohortes
        # Total esperado de pops: ~30 pops (en lugar de cientos de micro-pops de tamaño 1)
        pops = simulate_gravity_demand(pts, target_pop_size=35, max_pop_size=150, seed=42)
        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, total_pea)
        self.assertLessEqual(len(pops), 35)
        # Ningún pop de tamaño 1 artificial si el origen tiene masa suficiente
        for p in pops:
            self.assertGreaterEqual(p["size"], 30)

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

    def test_simulate_gravity_demand_single_origin_beyond_distance(self):
        pts = [
            {"id": "orig_1", "location": [-86.85, 21.15], "jobs": 0, "residents": 100, "pea_15ymas": 50, "popIds": []},
            {"id": "dest_1", "location": [-85.00, 21.15], "jobs": 100, "residents": 0, "pea_15ymas": 0, "popIds": []}
        ]
        pops = simulate_gravity_demand(pts, max_distance_km=50.0, seed=42)
        self.assertEqual(sum(p["size"] for p in pops), 50)

    def test_simulate_gravity_demand_no_self_loops_special_poi(self):
        pts = [
            {"id": "AIR_CUN", "location": [-86.87, 21.03], "jobs": 100, "residents": 100, "pea_15ymas": 50, "popIds": [], "is_special": True},
            {"id": "dest_reg", "location": [-86.85, 21.15], "jobs": 200, "residents": 0, "pea_15ymas": 0, "popIds": []},
            {"id": "orig_normal", "location": [-86.84, 21.14], "jobs": 0, "residents": 200, "pea_15ymas": 100, "popIds": []}
        ]
        pops = simulate_gravity_demand(pts, seed=42)
        self_loops = [p for p in pops if p["residenceId"] == p["jobId"]]
        self.assertEqual(len(self_loops), 0)
        for p in pts:
            self.assertEqual(len(p["popIds"]), len(set(p["popIds"])))

    def test_build_demand_grid_mass_weighted_centroid(self):
        # 1 empleo en -86.851 y 999 residentes en -86.853 dentro de la misma celda
        df_denue = pd.DataFrame([
            {"lon": -86.851, "lat": 21.150, "calibrated_jobs": 1.0}
        ])
        df_cpv = pd.DataFrame([
            {"lon": -86.853, "lat": 21.150, "pobtot_adj": 999.0, "pea_real": 600.0}
        ])
        roads_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        points, _ = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=[],
            roads_gdf=roads_gdf,
            grid_size=0.01,
            min_residents=5,
            min_jobs=1
        )
        self.assertEqual(len(points), 1)
        # El centroide ponderado debe estar muy cerca de -86.853 (no en el promedio no ponderado -86.852)
        self.assertAlmostEqual(points[0]["location"][0], -86.853, places=3)

    def test_build_demand_grid_subthreshold_mass_conservation(self):
        # Celda 1 (válida): 100 residentes, 50 PEA, 20 empleos
        # Celda 2 (sub-umbral): 4 residentes, 2 PEA, 1 empleo
        df_denue = pd.DataFrame([
            {"lon": -86.850, "lat": 21.150, "calibrated_jobs": 20.0},
            {"lon": -86.854, "lat": 21.150, "calibrated_jobs": 1.0}
        ])
        df_cpv = pd.DataFrame([
            {"lon": -86.850, "lat": 21.150, "pobtot_adj": 100.0, "pea_real": 50.0},
            {"lon": -86.854, "lat": 21.150, "pobtot_adj": 4.0, "pea_real": 2.0}
        ])
        roads_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        points, _ = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=[],
            roads_gdf=roads_gdf,
            grid_size=0.0025,
            min_residents=10,
            min_jobs=5
        )
        total_pob_input = 104
        total_pea_input = 52
        total_jobs_input = 21

        total_pob_output = sum(p["residents"] for p in points)
        total_pea_output = sum(p["pea_15ymas"] for p in points)
        total_jobs_output = sum(p["jobs"] for p in points)

        # Conservación estricta de masa: delta = 0
        self.assertEqual(total_pob_output, total_pob_input)
        self.assertEqual(total_pea_output, total_pea_input)
        self.assertEqual(total_jobs_output, total_jobs_input)

    def test_build_demand_grid_bounded_snapping(self):
        # Vía en lat = 21.150
        road_line = LineString([(-86.90, 21.150), (-86.80, 21.150)])
        roads_gdf = gpd.GeoDataFrame({"geometry": [road_line], "highway": ["residential"]}, crs="EPSG:4326")

        # Punto 1: ~40 metros al norte de la vía (lat 21.15036) -> Debe snappear a 21.150
        # Punto 2: ~1000 metros al norte de la vía (lat 21.15900) -> NO debe snappear (> 300m)
        df_denue = pd.DataFrame([
            {"lon": -86.850, "lat": 21.15036, "calibrated_jobs": 50.0},
            {"lon": -86.870, "lat": 21.15900, "calibrated_jobs": 50.0}
        ])
        df_cpv = pd.DataFrame([])
        points, _ = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=[],
            roads_gdf=roads_gdf,
            grid_size=0.0025,
            min_jobs=5,
            min_residents=5
        )
        p_close = [p for p in points if abs(p["location"][0] - (-86.850)) < 0.002][0]
        p_far = [p for p in points if abs(p["location"][0] - (-86.870)) < 0.002][0]

        # El punto cercano se proyectó sobre la vía (lat ~ 21.150)
        self.assertAlmostEqual(p_close["location"][1], 21.150, places=4)
        # El punto lejano mantuvo su posición original sin proyectarse a la lejana carretera
        self.assertAlmostEqual(p_far["location"][1], 21.159, places=3)

    def test_calculate_commute_impedance_continuous(self):
        # Para distancias cortas (200m = 0.2km), no debe existir el clamp rígido de 800m / 180s
        dist_m, sec = calculate_commute_impedance(0.2)
        self.assertLess(dist_m, 500)
        self.assertLess(sec, 180)
        self.assertGreaterEqual(dist_m, 150)
        self.assertGreaterEqual(sec, 45)

        # Distancia cero
        dist_zero, sec_zero = calculate_commute_impedance(0.0)
        self.assertEqual(dist_zero, 150)
        self.assertEqual(sec_zero, 81)
        self.assertGreaterEqual(sec_zero, 45)

        # Para viajes largos (5 km)
        dist_m5, sec5 = calculate_commute_impedance(5.0)
        self.assertGreater(dist_m5, 5000)
        self.assertGreater(sec5, 300)

    def test_simulate_gravity_demand_isolated_zones_barrier(self):
        # Orígenes: 2 en tierra firme (Cancún) y 1 en la isla (Cozumel)
        # Destinos: 2 en tierra firme (1 POI especial + 1 regular) y 1 regular en la isla
        isolated_zones = [
            {"id": "cozumel", "bbox": [-87.05, 20.25, -86.85, 20.60]}
        ]
        demand_points = [
            # Continente
            {"id": "orig_main_1", "location": [-86.85, 21.15], "jobs": 0, "residents": 500, "pea_15ymas": 100, "popIds": []},
            {"id": "dest_main_reg", "location": [-86.83, 21.15], "jobs": 300, "residents": 0, "pea_15ymas": 0, "popIds": []},
            {"id": "AIR_Cancun", "location": [-86.87, 21.03], "jobs": 50, "residents": 0, "pea_15ymas": 0, "popIds": [], "is_special": True},
            # Isla de Cozumel
            {"id": "orig_island_1", "location": [-86.95, 20.50], "jobs": 0, "residents": 400, "pea_15ymas": 80, "popIds": []},
            {"id": "dest_island_reg", "location": [-86.94, 20.51], "jobs": 150, "residents": 0, "pea_15ymas": 0, "popIds": []},
        ]
        total_pea = 180  # 100 mainland + 80 island

        pops = simulate_gravity_demand(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            seed=42
        )

        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, total_pea)

        # Comprobar barrera insular estricta:
        # Todos los viajes originados en la isla deben tener como destino la isla
        island_pops = [p for p in pops if p["residenceId"] == "orig_island_1"]
        self.assertGreater(len(island_pops), 0)
        for p in island_pops:
            self.assertEqual(p["jobId"], "dest_island_reg", "Viajero insular cruzó el mar al continente!")

        # Ningún viaje del continente debe terminar en la isla
        mainland_pops = [p for p in pops if p["residenceId"] == "orig_main_1"]
        self.assertGreater(len(mainland_pops), 0)
        for p in mainland_pops:
            self.assertNotEqual(p["jobId"], "dest_island_reg", "Viajero continental cruzó el mar a la isla!")

    def test_simulate_gravity_demand_capa_1_max_distance(self):
        # Origen cercano (10 km de distancia) y origen lejano (~120 km de distancia)
        # POI especial con cuota
        demand_points = [
            {"id": "orig_close", "location": [-86.85, 21.15], "jobs": 0, "residents": 500, "pea_15ymas": 100, "popIds": []},
            {"id": "orig_far", "location": [-87.45, 20.21], "jobs": 0, "residents": 500, "pea_15ymas": 100, "popIds": []}, # ~120 km al sur (Tulum)
            {"id": "dest_reg_close", "location": [-86.84, 21.14], "jobs": 200, "residents": 0, "pea_15ymas": 0, "popIds": []},
            {"id": "dest_reg_far", "location": [-87.44, 20.20], "jobs": 200, "residents": 0, "pea_15ymas": 0, "popIds": []},
            {"id": "AIR_Special", "location": [-86.87, 21.04], "jobs": 50, "residents": 0, "pea_15ymas": 0, "popIds": [], "is_special": True},
        ]
        # Con max_distance_km = 50.0, AIR_Special no debe tomar viajeros de orig_far
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            max_distance_km=50.0,
            seed=42
        )
        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, 200)

        # Verificar que ningún habitante de orig_far fue asignado a AIR_Special
        far_to_air = [p for p in pops if p["residenceId"] == "orig_far" and p["jobId"] == "AIR_Special"]
        self.assertEqual(len(far_to_air), 0)

    def test_furness_ipfp_convergence(self):
        # 3 orígenes con PEA 100, 200, 300 (Total = 600)
        orig_pea = np.array([100, 200, 300], dtype=np.float64)
        # 3 destinos con empleos 150, 250, 200 (Total = 600)
        dest_jobs = np.array([150, 250, 200], dtype=np.float64)
        # Matriz de distancias en km
        dist_km = np.array([
            [2.0, 5.0, 10.0],
            [6.0, 1.5, 8.0],
            [12.0, 7.0, 3.0]
        ], dtype=np.float64)

        prob_mat = furness_ipfp_balance(
            orig_pea=orig_pea,
            dest_jobs=dest_jobs,
            dist_km_mat=dist_km,
            beta=0.12,
            max_distance_km=50.0,
            max_iter=25,
            tol=0.01
        )

        # 1. Cada fila de prob_mat debe sumar exactamente 1.0
        for i in range(len(orig_pea)):
            self.assertAlmostEqual(prob_mat[i].sum(), 1.0, places=5)

        # 2. Reconstruir matriz de flujos T_ij = O_i * P_ij
        t_mat = orig_pea[:, np.newaxis] * prob_mat

        # Sumas por fila coinciden con O_i
        row_sums = t_mat.sum(axis=1)
        np.testing.assert_allclose(row_sums, orig_pea, rtol=1e-4)

        # Sumas por columna deben converger a dest_jobs con error relativo < 2%
        col_sums = t_mat.sum(axis=0)
        np.testing.assert_allclose(col_sums, dest_jobs, rtol=0.02)

    def test_simulate_gravity_demand_doubly_constrained_balancing(self):
        # Escenario donde hay 2 destinos: dest_local (100 empleos) y dest_regional (900 empleos, 9x más grande)
        # Un origen residencial masivo cerca de dest_local
        demand_points = [
            {"id": "orig_mass", "location": [-86.85, 21.15], "jobs": 0, "residents": 2000, "pea_15ymas": 1000, "popIds": []},
            {"id": "dest_local", "location": [-86.84, 21.15], "jobs": 100, "residents": 0, "pea_15ymas": 0, "popIds": []},
            {"id": "dest_regional", "location": [-86.80, 21.15], "jobs": 900, "residents": 0, "pea_15ymas": 0, "popIds": []},
        ]
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            beta=0.08,
            max_pop_size=50,
            target_pop_size=25,
            seed=42
        )
        total_pax = sum(p["size"] for p in pops)
        self.assertEqual(total_pax, 1000)

        # Contar pasajeros que llegan a cada destino
        pax_local = sum(p["size"] for p in pops if p["jobId"] == "dest_local")
        pax_regional = sum(p["size"] for p in pops if p["jobId"] == "dest_regional")

        # Gracias al balanceo de Furness, dest_local NO puede absorber a toda la población
        # a pesar de estar a solo ~1 km, porque solo tiene el 10% del empleo total (100 / 1000).
        # El empleo regional (90%) absorbe la gran mayoría de los viajes (~900).
        self.assertLess(pax_local, 250)
        self.assertGreater(pax_regional, 750)

    def test_build_demand_grid_rejects_duplicate_poi_ids(self):
        df_denue = pd.DataFrame([{"lon": -86.85, "lat": 21.15, "calibrated_jobs": 10.0}])
        df_cpv = pd.DataFrame([{"lon": -86.85, "lat": 21.15, "pobtot_adj": 50.0, "pea_real": 25.0}])
        roads_gdf = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        duplicate_pois = [
            {"id": "UNI_Duplicate", "loc": [-86.85, 21.15], "jobs": 1000, "radius_m": 500, "mode": "MAX"},
            {"id": "UNI_Duplicate", "loc": [-86.86, 21.16], "jobs": 2000, "radius_m": 500, "mode": "MAX"}
        ]
        with self.assertRaises(ValueError) as ctx:
            build_demand_grid(
                df_denue=df_denue,
                df_cpv=df_cpv,
                special_pois=duplicate_pois,
                roads_gdf=roads_gdf
            )
        self.assertIn("IDs duplicados", str(ctx.exception))

    def test_build_demand_grid_preserves_exact_poi_coordinates(self):
        df_denue = pd.DataFrame([{"lon": -86.85, "lat": 21.15, "calibrated_jobs": 10.0}])
        df_cpv = pd.DataFrame([{"lon": -86.85, "lat": 21.15, "pobtot_adj": 50.0, "pea_real": 25.0}])
        # Carretera a ~150 metros del POI
        road_geom = LineString([(-86.8480, 21.0490), (-86.8480, 21.0550)])
        roads_gdf = gpd.GeoDataFrame({"geometry": [road_geom], "highway": ["primary"]}, crs="EPSG:4326")
        exact_loc = [-86.84688, 21.04904]
        pois = [
            {"id": "UNI_Exact", "loc": exact_loc, "jobs": 3400, "radius_m": 200, "mode": "BOOST"}
        ]
        pts, audit = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=pois,
            roads_gdf=roads_gdf
        )
        poi_pt = next(p for p in pts if p["id"] == "UNI_Exact")
        # Las coordenadas deben conservarse exactamente iguales a las ingresadas
        self.assertEqual(poi_pt["location"], [round(exact_loc[0], 5), round(exact_loc[1], 5)])

if __name__ == "__main__":
    unittest.main()



