import unittest
import numpy as np
from sb_mexico.gravity import (
    calculate_commute_distance_distribution,
    recommend_gravity_beta
)


class TestTripDistribution(unittest.TestCase):
    def test_empty_pops(self):
        res = calculate_commute_distance_distribution([])
        self.assertEqual(res["total_pops"], 0)
        self.assertEqual(res["total_commuters"], 0)
        self.assertEqual(res["mean_km"], 0.0)
        self.assertEqual(res["median_km"], 0.0)
        self.assertEqual(res["profile"], "sin_datos")

    def test_weighted_statistics(self):
        # 100 viajeros a 4.0 km (4000 m)
        # 200 viajeros a 8.0 km (8000 m)
        # 300 viajeros a 12.0 km (12000 m)
        # 400 viajeros a 20.0 km (20000 m)
        # Total = 1000 viajeros
        pops = [
            {"id": "p1", "size": 100, "drivingDistance": 4000, "drivingSeconds": 360},
            {"id": "p2", "size": 200, "drivingDistance": 8000, "drivingSeconds": 720},
            {"id": "p3", "size": 300, "drivingDistance": 12000, "drivingSeconds": 1080},
            {"id": "p4", "size": 400, "drivingDistance": 20000, "drivingSeconds": 1800},
        ]
        res = calculate_commute_distance_distribution(pops)
        self.assertEqual(res["total_commuters"], 1000)
        self.assertEqual(res["total_pops"], 4)

        # Promedio ponderado: (100*4 + 200*8 + 300*12 + 400*20) / 1000 = (400 + 1600 + 3600 + 8000) / 1000 = 13.6 km
        self.assertAlmostEqual(res["mean_km"], 13.6, places=1)

        # Mediana (50% de 1000 = 500 viajeros):
        # Cumulativos: p1=100, p2=300, p3=600 -> El viajero 500 está en p3 (12.0 km)
        self.assertEqual(res["median_km"], 12.0)

        # Percentil 25 (250 viajeros): está en p2 (8.0 km)
        self.assertEqual(res["p25_km"], 8.0)

        # Percentil 75 (750 viajeros): está en p4 (20.0 km)
        self.assertEqual(res["p75_km"], 20.0)

        self.assertEqual(res["min_km"], 4.0)
        self.assertEqual(res["max_km"], 20.0)
        self.assertEqual(res["profile"], "intermedia")

    def test_brackets_classification(self):
        pops = [
            {"id": "p1", "size": 50, "drivingDistance": 3000, "drivingSeconds": 270},   # < 5 km
            {"id": "p2", "size": 150, "drivingDistance": 7500, "drivingSeconds": 675},  # 5-10 km
            {"id": "p3", "size": 100, "drivingDistance": 13000, "drivingSeconds": 1170}, # 10-15 km
            {"id": "p4", "size": 100, "drivingDistance": 18000, "drivingSeconds": 1620}, # 15-25 km
            {"id": "p5", "size": 100, "drivingDistance": 32000, "drivingSeconds": 2880}, # > 25 km
        ]
        res = calculate_commute_distance_distribution(pops)
        brackets = {b["id"]: b for b in res["brackets"]}

        self.assertEqual(brackets["micro"]["commuters"], 50)
        self.assertEqual(brackets["short"]["commuters"], 150)
        self.assertEqual(brackets["medium"]["commuters"], 100)
        self.assertEqual(brackets["suburban"]["commuters"], 100)
        self.assertEqual(brackets["regional"]["commuters"], 100)

        # Total 500 viajeros -> micro = 10%, short = 30%, etc.
        self.assertAlmostEqual(brackets["micro"]["percentage"], 10.0, places=1)
        self.assertAlmostEqual(brackets["short"]["percentage"], 30.0, places=1)

    def test_recommend_gravity_beta_linear_corridor(self):
        # Corredor costero lineal sintético (50 puntos a lo largo de 90 km, longitud fija y latitud variable)
        # Emula el corredor Cancún - Puerto Morelos - Playa del Carmen
        demand_points = []
        lats = np.linspace(20.30, 21.15, 40)  # ~94 km de extensión norte-sur
        for idx, lat in enumerate(lats):
            lon = -86.90 + 0.02 * np.sin(idx)  # variabilidad transversal mínima
            demand_points.append({
                "id": f"dp_{idx}",
                "location": [round(lon, 5), round(lat, 5)],
                "pea_15ymas": 500 + int(200 * np.cos(idx)),
                "jobs": 400 + int(300 * np.sin(idx)),
                "is_special": False
            })

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertEqual(rec["archetype"], "corredor_lineal")
        self.assertIn("corredor", rec["label"].lower())
        self.assertEqual(rec["metrics"]["method"], "demand_points")
        self.assertGreaterEqual(rec["metrics"]["elongation_ratio"], 2.25)
        self.assertGreaterEqual(rec["metrics"]["major_axis_km"], 8.0)
        # El beta para corredor debe ser menor a 0.110 para permitir flujos a lo largo del eje
        self.assertLessEqual(rec["recommended_beta"], 0.110)
        self.assertGreaterEqual(rec["recommended_beta"], 0.065)

    def test_recommend_gravity_beta_compact_city(self):
        # Ciudad radial concentrada sintética (radio ~3 km, ej. Toluca o Campeche)
        demand_points = []
        rng = np.random.default_rng(42)
        center_lon, center_lat = -99.15, 19.40
        for idx in range(30):
            r_km = rng.uniform(0.2, 2.8)
            theta = rng.uniform(0, 2 * np.pi)
            dlon = (r_km * np.cos(theta)) / (111.32 * np.cos(np.radians(center_lat)))
            dlat = (r_km * np.sin(theta)) / 110.57
            demand_points.append({
                "id": f"dp_{idx}",
                "location": [round(center_lon + dlon, 5), round(center_lat + dlat, 5)],
                "pea_15ymas": int(rng.integers(200, 800)),
                "jobs": int(rng.integers(150, 700)),
                "is_special": False
            })

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertEqual(rec["archetype"], "compacta")
        self.assertLessEqual(rec["metrics"]["combined_rg_km"], 8.0)
        self.assertGreaterEqual(rec["recommended_beta"], 0.135)

    def test_recommend_gravity_beta_ultra_compact_edge_case(self):
        # Caso borde: ciudad diminuta (radio < 1 km).
        # La mediana no penalizada es muy corta (~0.8 km < 7.5 km).
        # El algoritmo NO debe colapsar beta al piso de 0.065, sino mantenerlo alto (>= 0.140).
        demand_points = []
        center_lon, center_lat = -98.20, 19.05
        # 10 puntos a menos de 600m
        for idx in range(10):
            dlon = 0.002 * np.cos(idx)
            dlat = 0.002 * np.sin(idx)
            demand_points.append({
                "id": f"dp_{idx}",
                "location": [round(center_lon + dlon, 5), round(center_lat + dlat, 5)],
                "pea_15ymas": 300,
                "jobs": 250,
                "is_special": False
            })

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertEqual(rec["archetype"], "compacta")
        # Verificar salvaguarda: beta debe ser alto (típico de ciudad compacta)
        self.assertGreaterEqual(rec["recommended_beta"], 0.140)
        self.assertLessEqual(rec["metrics"]["target_median_km"], 7.5)

    def test_recommend_gravity_beta_megacity(self):
        # Megaciudad radial extensa (dispersión masiva de ~35 km radial, ej. ZMVM)
        demand_points = []
        rng = np.random.default_rng(123)
        center_lon, center_lat = -99.10, 19.40
        for idx in range(60):
            r_km = rng.uniform(2.0, 38.0)
            theta = rng.uniform(0, 2 * np.pi)
            dlon = (r_km * np.cos(theta)) / (111.32 * np.cos(np.radians(center_lat)))
            dlat = (r_km * np.sin(theta)) / 110.57
            demand_points.append({
                "id": f"dp_{idx}",
                "location": [round(center_lon + dlon, 5), round(center_lat + dlat, 5)],
                "pea_15ymas": int(rng.integers(1000, 5000)),
                "jobs": int(rng.integers(800, 4500)),
                "is_special": False
            })

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertEqual(rec["archetype"], "megaciudad")
        self.assertGreaterEqual(rec["metrics"]["combined_rg_km"], 15.0)
        self.assertLessEqual(rec["recommended_beta"], 0.095)

    def test_recommend_gravity_beta_intermediate(self):
        # Metrópoli policéntrica intermedia (dispersión media ~12 km, ej. Querétaro o Mérida)
        demand_points = []
        rng = np.random.default_rng(999)
        center_lon, center_lat = -100.39, 20.59
        for idx in range(40):
            r_km = rng.uniform(1.0, 18.0)
            theta = rng.uniform(0, 2 * np.pi)
            dlon = (r_km * np.cos(theta)) / (111.32 * np.cos(np.radians(center_lat)))
            dlat = (r_km * np.sin(theta)) / 110.57
            demand_points.append({
                "id": f"dp_{idx}",
                "location": [round(center_lon + dlon, 5), round(center_lat + dlat, 5)],
                "pea_15ymas": int(rng.integers(400, 1500)),
                "jobs": int(rng.integers(300, 1200)),
                "is_special": False
            })

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertEqual(rec["archetype"], "intermedia")
        self.assertGreaterEqual(rec["recommended_beta"], 0.070)
        self.assertLessEqual(rec["recommended_beta"], 0.135)

    def test_recommend_gravity_beta_degenerates_and_fallbacks(self):
        bbox_small = [-86.95, 21.05, -86.75, 21.20]

        # 1. Lista vacía -> cae a BBOX
        rec_empty = recommend_gravity_beta(demand_points=[], bbox=bbox_small)
        self.assertEqual(rec_empty["metrics"]["method"], "bbox_fallback")
        self.assertEqual(rec_empty["archetype"], "compacta")

        # 2. Un solo punto -> insuficiente para covarianzas -> cae a BBOX
        rec_single = recommend_gravity_beta(
            demand_points=[{"location": [-86.85, 21.10], "pea_15ymas": 100, "jobs": 100}],
            bbox=bbox_small
        )
        self.assertEqual(rec_single["metrics"]["method"], "bbox_fallback")

        # 3. Puntos sin PEA residencial -> cae a BBOX
        rec_no_pea = recommend_gravity_beta(
            demand_points=[
                {"location": [-86.85, 21.10], "pea_15ymas": 0, "jobs": 100},
                {"location": [-86.86, 21.11], "pea_15ymas": 0, "jobs": 200}
            ],
            bbox=bbox_small
        )
        self.assertEqual(rec_no_pea["metrics"]["method"], "bbox_fallback")

        # 4. Todos los empleos son especiales (is_special=True) -> sin empleo regular -> cae a BBOX
        rec_special = recommend_gravity_beta(
            demand_points=[
                {"location": [-86.85, 21.10], "pea_15ymas": 100, "jobs": 100, "is_special": True},
                {"location": [-86.86, 21.11], "pea_15ymas": 200, "jobs": 200, "is_special": True}
            ],
            bbox=bbox_small
        )
        self.assertEqual(rec_special["metrics"]["method"], "bbox_fallback")

        # 5. Coordenadas no finitas o None -> filtradas sin excepción
        rec_invalid = recommend_gravity_beta(
            demand_points=[
                {"location": [None, 21.10], "pea_15ymas": 100, "jobs": 100},
                {"location": [-86.85, float("nan")], "pea_15ymas": 200, "jobs": 200}
            ],
            bbox=bbox_small
        )
        self.assertEqual(rec_invalid["metrics"]["method"], "bbox_fallback")

    def test_recommend_gravity_beta_archetypes(self):
        rec_comp = recommend_gravity_beta(city_archetype="compacta")
        self.assertEqual(rec_comp["recommended_beta"], 0.150)
        self.assertEqual(rec_comp["archetype"], "compacta")

        rec_inter = recommend_gravity_beta(city_archetype="intermedia")
        self.assertEqual(rec_inter["recommended_beta"], 0.120)

        rec_mega = recommend_gravity_beta(city_archetype="megaciudad")
        self.assertEqual(rec_mega["recommended_beta"], 0.085)

    def test_recommend_gravity_beta_bbox(self):
        # BBOX pequeño (Cancún urbano ~20 km diagonal)
        # [-86.95, 21.05, -86.75, 21.20]
        bbox_small = [-86.95, 21.05, -86.75, 21.20]
        rec_s = recommend_gravity_beta(bbox=bbox_small)
        self.assertEqual(rec_s["archetype"], "compacta")
        self.assertEqual(rec_s["recommended_beta"], 0.150)

        # BBOX gigante (Valle de México / ZMVM ~90 km diagonal)
        # [-99.40, 19.10, -98.80, 19.85]
        bbox_huge = [-99.40, 19.10, -98.80, 19.85]
        rec_h = recommend_gravity_beta(bbox=bbox_huge)
        self.assertEqual(rec_h["archetype"], "megaciudad")
        self.assertEqual(rec_h["recommended_beta"], 0.085)


if __name__ == "__main__":
    unittest.main()


