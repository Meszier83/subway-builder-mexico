import math
import unittest
import numpy as np
from sb_mexico.gravity import (
    calculate_commute_distance_distribution,
    recommend_gravity_beta,
    sanitize_max_distance_km,
    simulate_gravity_demand
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

    def test_od_universe_isolated_zones(self):
        # Dos localidades separadas por ~95 km (Town Sur a lat 20.00, Town Norte a lat 20.85).
        # Ambas tienen orígenes y destinos a escala local (< 2 km).
        demand_points = [
            # Localidad Sur (~lat 20.00)
            {"id": "s1", "location": [-87.00, 20.00], "pea_15ymas": 500, "jobs": 400},
            {"id": "s2", "location": [-87.01, 20.01], "pea_15ymas": 600, "jobs": 500},
            # Localidad Norte (~lat 20.85, a ~94 km de distancia)
            {"id": "n1", "location": [-87.00, 20.85], "pea_15ymas": 700, "jobs": 600},
            {"id": "n2", "location": [-87.01, 20.86], "pea_15ymas": 800, "jobs": 700},
        ]
        # Zonas aisladas que encierran a cada localidad por separado
        isolated_zones = [
            {
                "name": "Zona_Sur",
                "polygon": [[-87.1, 19.9], [-86.9, 19.9], [-86.9, 20.1], [-87.1, 20.1], [-87.1, 19.9]]
            },
            {
                "name": "Zona_Norte",
                "polygon": [[-87.1, 20.7], [-86.9, 20.7], [-86.9, 20.95], [-87.1, 20.95], [-87.1, 20.7]]
            }
        ]

        # Sin zonas aisladas y con distancia grande (120 km): los pares inter-pueblos a 94 km son admitidos
        rec_open = recommend_gravity_beta(
            demand_points=demand_points,
            isolated_zones=None,
            max_distance_km=120.0
        )
        self.assertIsNotNone(rec_open)
        self.assertGreater(rec_open["metrics"]["opportunity_mean_km"], 20.0)

        # Con zonas aisladas y max_distance_km=55.0 (restricciones Furness):
        # Los viajes entre Sur y Norte están estrictamente prohibidos (diferente zona Y d > 55 km).
        # Sólo son admisibles pares intra-zona (distancia ~1.5 km).
        rec_isolated = recommend_gravity_beta(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0
        )
        self.assertIsNotNone(rec_isolated)
        self.assertLess(rec_isolated["metrics"]["admissible_pairs"], rec_open["metrics"]["admissible_pairs"])
        self.assertLess(rec_isolated["metrics"]["opportunity_mean_km"], 5.0)
        self.assertLess(rec_isolated["metrics"]["opportunity_median_km"], 5.0)
        self.assertNotEqual(rec_isolated["archetype"], "megaciudad")
        self.assertGreaterEqual(rec_isolated["recommended_beta"], 0.130)

    def test_representation_invariance_ess(self):
        # Verificación de invarianza representacional:
        # Dividir un mismo centroide físico en N registros idénticos (sub-manzanas o desagregación)
        # no debe alterar el ESS espacial, la confianza de calibración ni el beta resultante.
        dp_original = [
            {"id": "p1", "location": [-99.15, 19.40], "pea_15ymas": 1000, "jobs": 800},
            {"id": "p2", "location": [-99.20, 19.42], "pea_15ymas": 2000, "jobs": 1200},
            {"id": "p3", "location": [-99.10, 19.38], "pea_15ymas": 1500, "jobs": 2200},
            {"id": "p4", "location": [-99.18, 19.35], "pea_15ymas": 3000, "jobs": 1800},
            {"id": "p5", "location": [-99.12, 19.45], "pea_15ymas": 2500, "jobs": 4000},
        ]

        # Cada punto se divide en 5 sub-registros con 1/5 de la masa en las mismas coordenadas
        dp_split = []
        for p in dp_original:
            for sub_i in range(5):
                dp_split.append({
                    "id": p["id"],
                    "location": list(p["location"]),
                    "pea_15ymas": p["pea_15ymas"] / 5.0,
                    "jobs": p["jobs"] / 5.0,
                })

        rec_orig = recommend_gravity_beta(demand_points=dp_original)
        rec_split = recommend_gravity_beta(demand_points=dp_split)

        self.assertIsNotNone(rec_orig)
        self.assertIsNotNone(rec_split)

        # Invarianza estricta en ESS espacial
        self.assertAlmostEqual(
            rec_orig["metrics"]["effective_origins"],
            rec_split["metrics"]["effective_origins"],
            places=1
        )
        self.assertAlmostEqual(
            rec_orig["metrics"]["effective_destinations"],
            rec_split["metrics"]["effective_destinations"],
            places=1
        )
        # Invarianza en factor de confianza
        self.assertAlmostEqual(
            rec_orig["metrics"]["calibration_confidence"],
            rec_split["metrics"]["calibration_confidence"],
            places=3
        )
        # Invarianza en recomendación de beta
        self.assertEqual(rec_orig["recommended_beta"], rec_split["recommended_beta"])
        self.assertEqual(rec_orig["archetype"], rec_split["archetype"])
        # El número de nodos espaciales únicos debe coincidir (5), aunque los registros crudos sean 25
        self.assertEqual(rec_split["metrics"]["origin_count"], 5)
        self.assertEqual(rec_split["metrics"]["destination_count"], 5)
        self.assertEqual(rec_split["metrics"]["raw_origin_records"], 25)
        self.assertEqual(rec_split["metrics"]["raw_destination_records"], 25)

    def test_histogram_dynamic_ceiling_no_clipping(self):
        # Techo dinámico: si max_distance_km=80, la resolución y el rango cubren 80 km sin censura previa.
        # Creamos dos polos separados por ~70 km:
        demand_points = [
            {"id": "a1", "location": [-99.00, 19.00], "pea_15ymas": 2000, "jobs": 1500},
            {"id": "a2", "location": [-99.01, 19.01], "pea_15ymas": 1800, "jobs": 1400},
            {"id": "b1", "location": [-99.00, 19.63], "pea_15ymas": 2500, "jobs": 2000},
            {"id": "b2", "location": [-99.01, 19.64], "pea_15ymas": 2200, "jobs": 1900},
        ]
        rec_80 = recommend_gravity_beta(demand_points=demand_points, max_distance_km=80.0)
        self.assertIsNotNone(rec_80)
        self.assertEqual(rec_80["metrics"]["max_distance_km"], 80.0)
        self.assertGreater(rec_80["metrics"]["admissible_pairs"], 4)

        # Si se reduce a max_distance_km=40.0, los pares a ~70 km son excluidos
        rec_40 = recommend_gravity_beta(demand_points=demand_points, max_distance_km=40.0)
        self.assertIsNotNone(rec_40)
        self.assertEqual(rec_40["metrics"]["max_distance_km"], 40.0)
        self.assertLess(rec_40["metrics"]["admissible_pairs"], rec_80["metrics"]["admissible_pairs"])

        # Para distancias mayores a 160 km (ej. 190 km): no hay truncamiento a 160 km
        dp_distant = [
            {"id": "c1", "location": [-99.00, 19.00], "pea_15ymas": 3000, "jobs": 2000},
            {"id": "c2", "location": [-99.01, 19.01], "pea_15ymas": 2500, "jobs": 1800},
            {"id": "d1", "location": [-99.00, 20.65], "pea_15ymas": 3500, "jobs": 2200},  # ~182 km
            {"id": "d2", "location": [-99.01, 20.66], "pea_15ymas": 2800, "jobs": 2100},
        ]
        rec_200 = recommend_gravity_beta(demand_points=dp_distant, max_distance_km=200.0)
        self.assertIsNotNone(rec_200)
        self.assertEqual(rec_200["metrics"]["max_distance_km"], 200.0)
        self.assertGreater(rec_200["metrics"]["opportunity_p75_km"], 50.0)

    def test_calibrated_median_matches_final_beta(self):
        # Coherencia interna: metrics.calibrated_median_km debe estar calculada con
        # el beta final redondeado y regularizado.
        demand_points = [
            {"id": f"p_{i}", "location": [-100.30 + 0.05 * i, 20.50 + 0.04 * i], "pea_15ymas": 500 + 50 * i, "jobs": 400 + 60 * i}
            for i in range(12)
        ]
        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        calib_med = m.get("calibrated_median_km")
        self.assertIsNotNone(calib_med)
        self.assertTrue(math.isfinite(calib_med))
        self.assertGreater(calib_med, 0.0)
        self.assertLessEqual(calib_med, m["opportunity_median_km"] + 0.5)
        self.assertGreater(rec["recommended_beta"], 0.0)

    def test_malformed_and_infinite_masses(self):
        # Robustez: entradas con strings, nan, inf, valores negativos
        # deben ser saneadas sin excepción ni retornar nan.
        demand_points = [
            {"id": "p1", "location": [-99.15, 19.40], "pea_15ymas": 1000, "jobs": 800},
            {"id": "p2", "location": [-99.20, 19.42], "pea_15ymas": float("inf"), "jobs": 1200},
            {"id": "p3", "location": [-99.10, 19.38], "pea_15ymas": 1500, "jobs": float("-inf")},
            {"id": "p4", "location": [-99.18, 19.35], "pea_15ymas": float("nan"), "jobs": 1800},
            {"id": "p5", "location": [-99.12, 19.45], "pea_15ymas": 2500, "jobs": "invalido"},
            {"id": "p6", "location": [-99.16, 19.39], "pea_15ymas": -500, "jobs": -200},
            {"id": "p7", "location": [-99.14, 19.41], "pea_15ymas": 2000, "jobs": 2500},
            {"id": "p8", "location": ["no_es_coord", 19.41], "pea_15ymas": 2000, "jobs": 2500},
        ]

        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertIsNotNone(rec)
        self.assertTrue(math.isfinite(rec["recommended_beta"]))
        m = rec["metrics"]
        for k, v in m.items():
            if isinstance(v, float):
                self.assertTrue(math.isfinite(v), f"Métrica {k} no es finita: {v}")

    def test_distinct_ids_close_proximity_admitted(self):
        # Contraejemplo Sol: IDs distintos separados por ~10 metros.
        # Furness y el calibrador los admiten (no son auto-viajes idénticos).
        demand_points = [
            {"id": "res_1", "location": [-87.00000, 20.00000], "pea_15ymas": 100, "jobs": 0},
            {"id": "denue_1", "location": [-87.00009, 20.00000], "pea_15ymas": 0, "jobs": 100},
            {"id": "denue_2", "location": [-87.00000, 20.09000], "pea_15ymas": 0, "jobs": 100},
            {"id": "res_2", "location": [-87.00000, 20.09000], "pea_15ymas": 100, "jobs": 0},
        ]
        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertGreater(m["admissible_pairs"], 0)
        self.assertLess(m["opportunity_median_km"], 6.0)

    def test_same_id_displaced_suppressed_if_alternatives_exist(self):
        # Contraejemplo Sol: El mismo ID desplazado ~1.25 km.
        # Furness lo prohíbe si existen alternativas en la zona (orig_id == dest_id).
        demand_points = [
            {"id": "node_1", "location": [-87.0000, 20.0000], "pea_15ymas": 200, "jobs": 0},
            {"id": "node_1", "location": [-87.0120, 20.0000], "pea_15ymas": 0, "jobs": 200},
            {"id": "node_2", "location": [-87.0000, 20.0900], "pea_15ymas": 200, "jobs": 200},
        ]
        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertGreaterEqual(m["opportunity_median_km"], 8.0)

    def test_single_destination_per_isolated_zone_retained(self):
        # Contraejemplo Sol: Una única alternativa por cada zona aislada.
        # Furness conserva ese destino porque cada zona tiene uno solo (len(dest_indices) == 1).
        isolated_zones = [
            {"name": "Zona_Sur", "bbox": [-87.1, 19.9, -86.9, 20.1]},
            {"name": "Zona_Norte", "bbox": [-87.1, 20.7, -86.9, 20.95]},
        ]
        demand_points = [
            # Zona Sur: 1 centroide mixto (PEA y Empleo con mismo ID)
            {"id": "sur_hub", "location": [-87.00, 20.00], "pea_15ymas": 500, "jobs": 500},
            # Zona Norte: 1 centroide mixto a 95 km (PEA y Empleo con mismo ID)
            {"id": "norte_hub", "location": [-87.00, 20.85], "pea_15ymas": 600, "jobs": 600},
        ]
        rec = recommend_gravity_beta(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0
        )
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        self.assertEqual(m["admissible_pairs"], 2)
        self.assertNotEqual(rec["archetype"], "megaciudad")
        self.assertGreaterEqual(rec["recommended_beta"], 0.130)

    def test_large_scale_compact_nodes_no_grid_collapse(self):
        # Contraejemplo Sol: 2,025 nodos compactos (4,100,625 pares Furness válidos).
        # El calibrador exacto en streaming NO debe colapsar todo a 1 celda ni forzar bbox_fallback.
        demand_points = []
        node_idx = 0
        for i in range(45):
            for j in range(45):
                demand_points.append({
                    "id": f"nd_{node_idx}",
                    "location": [round(-86.85 + i * 0.00015, 5), round(21.10 + j * 0.00015, 5)],
                    "pea_15ymas": 50,
                    "jobs": 50
                })
                node_idx += 1

        self.assertEqual(len(demand_points), 2025)
        rec = recommend_gravity_beta(demand_points=demand_points)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        self.assertEqual(m["pair_mode"], "exact_chunked")
        self.assertEqual(m["admissible_pairs"], 4098600)
        self.assertEqual(rec["archetype"], "compacta")
        self.assertGreaterEqual(rec["recommended_beta"], 0.140)

    def test_morphology_per_admissible_topological_component(self):
        # Contraejemplo Sol: Dos localidades radiales independientes separadas por 95 km.
        # No deben crear una elongación artificial de 7.5x ni un eje mayor de 53 km.
        isolated_zones = [
            {"name": "Zona_Sur", "bbox": [-87.15, 19.85, -86.85, 20.15]},
            {"name": "Zona_Norte", "bbox": [-87.15, 20.70, -86.85, 21.00]},
        ]
        demand_points = []
        rng = np.random.default_rng(777)
        for i in range(25):
            r = rng.uniform(0.2, 2.5)
            theta = rng.uniform(0, 2 * np.pi)
            demand_points.append({
                "id": f"s_{i}",
                "location": [round(-87.00 + (r * np.cos(theta)) / 104.0, 5), round(20.00 + (r * np.sin(theta)) / 110.5, 5)],
                "pea_15ymas": int(rng.integers(100, 500)),
                "jobs": int(rng.integers(100, 500))
            })
        for i in range(25):
            r = rng.uniform(0.2, 2.5)
            theta = rng.uniform(0, 2 * np.pi)
            demand_points.append({
                "id": f"n_{i}",
                "location": [round(-87.00 + (r * np.cos(theta)) / 104.0, 5), round(20.85 + (r * np.sin(theta)) / 110.5, 5)],
                "pea_15ymas": int(rng.integers(100, 500)),
                "jobs": int(rng.integers(100, 500))
            })

        # 1. Con isolated_zones declaradas
        rec = recommend_gravity_beta(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0
        )
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertLess(m["elongation_ratio"], 1.6)
        self.assertLess(m["major_axis_km"], 6.0)
        self.assertNotEqual(rec["archetype"], "corredor_lineal")
        self.assertIn(rec["archetype"], ["compacta", "intermedia"])

        # 2. Sin isolated_zones (ambas en la misma zona base pero separadas 95 km)
        # La conectividad por distancia en el grafo de soporte efectivo evita el falso corredor
        rec_nz = recommend_gravity_beta(
            demand_points=demand_points,
            isolated_zones=None,
            max_distance_km=55.0
        )
        self.assertIsNotNone(rec_nz)
        m_nz = rec_nz["metrics"]
        self.assertLess(m_nz["elongation_ratio"], 1.6)
        self.assertLess(m_nz["major_axis_km"], 6.0)
        self.assertNotEqual(rec_nz["archetype"], "corredor_lineal")
        self.assertIn(rec_nz["archetype"], ["compacta", "intermedia"])

    def test_colocated_distinct_ids_furness_equivalence(self):
        # Contraejemplo Sol: Dos centroides separados ~10.38 km, con dos IDs distintos en cada uno.
        # Furness anula el auto-viaje del mismo ID pero permite viajes cruzados colocados (distancia 0 km).
        # El calibrador a nivel de masa ID-celda debe admitir esa masa a 0 km y reportar mediana en bin 0 (< 0.25 km).
        c1 = [-86.85, 21.10]
        c2 = [-86.75, 21.10]
        demand_points = [
            {"id": "A", "location": c1, "pea_15ymas": 100, "jobs": 100},
            {"id": "B", "location": c1, "pea_15ymas": 100, "jobs": 100},
            {"id": "C", "location": c2, "pea_15ymas": 100, "jobs": 100},
            {"id": "D", "location": c2, "pea_15ymas": 100, "jobs": 100},
        ]
        rec = recommend_gravity_beta(demand_points=demand_points, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["admissible_pairs"], 4)
        self.assertLessEqual(m["calibrated_median_km"], 0.25)

    def test_sanitize_max_distance_km_robustness(self):
        # Contraejemplo Sol: Saneamiento de max_distance_km ante None, strings, inf, nan, negativos
        self.assertEqual(sanitize_max_distance_km(None), 55.0)
        self.assertEqual(sanitize_max_distance_km("bad"), 55.0)
        self.assertEqual(sanitize_max_distance_km(float("inf")), 55.0)
        self.assertEqual(sanitize_max_distance_km(float("-inf")), 55.0)
        self.assertEqual(sanitize_max_distance_km(float("nan")), 55.0)
        self.assertEqual(sanitize_max_distance_km(-10.0), 55.0)
        self.assertEqual(sanitize_max_distance_km(0.0), 55.0)
        self.assertEqual(sanitize_max_distance_km(80.0), 80.0)

        dp = [
            {"id": "p1", "location": [-99.15, 19.40], "pea_15ymas": 500, "jobs": 400},
            {"id": "p2", "location": [-99.16, 19.41], "pea_15ymas": 600, "jobs": 500},
        ]
        for bad_val in [None, "bad", float("inf"), float("nan"), -25]:
            rec = recommend_gravity_beta(demand_points=dp, max_distance_km=bad_val)
            self.assertIsNotNone(rec)
            self.assertEqual(rec["metrics"]["max_distance_km"], 55.0)

    def test_simulate_gravity_demand_sanitizes_max_distance_km(self):
        # Contraejemplo Sol: Propagación de saneamiento a simulate_gravity_demand
        dp = [
            {"id": "p1", "location": [-99.15, 19.40], "pea_15ymas": 500, "jobs": 400},
            {"id": "p2", "location": [-99.16, 19.41], "pea_15ymas": 600, "jobs": 500},
        ]
        for bad_val in [None, "bad", float("inf"), float("nan"), -10.0]:
            pops = simulate_gravity_demand(demand_points=dp, max_distance_km=bad_val, seed=42)
            self.assertIsInstance(pops, list)
            self.assertGreater(len(pops), 0)

    def test_bipartite_demand_graph_prevents_oo_bridge(self):
        # Contraejemplo Sol: Dos localidades radiales independientes separadas por 95 km.
        # Dos orígenes fantasma de masa PEA=0.01 colocados a medio camino.
        # En un grafo puramente bipartito de interacción OD admisible, los orígenes no se conectan
        # entre sí (O-O), por lo que las dos ciudades permanecen desconectadas y no forman
        # un falso corredor lineal masivo (elongación < 1.6, eje mayor < 6.0 km).
        rng = np.random.default_rng(777)
        demand_points = []
        for i in range(25):
            r = rng.uniform(0.2, 2.5)
            theta = rng.uniform(0, 2 * np.pi)
            demand_points.append({
                "id": f"s_{i}",
                "location": [round(-87.00 + (r * np.cos(theta)) / 104.0, 5), round(20.00 + (r * np.sin(theta)) / 110.5, 5)],
                "pea_15ymas": int(rng.integers(100, 500)),
                "jobs": int(rng.integers(100, 500))
            })
        for i in range(25):
            r = rng.uniform(0.2, 2.5)
            theta = rng.uniform(0, 2 * np.pi)
            demand_points.append({
                "id": f"n_{i}",
                "location": [round(-87.00 + (r * np.cos(theta)) / 104.0, 5), round(20.85 + (r * np.sin(theta)) / 110.5, 5)],
                "pea_15ymas": int(rng.integers(100, 500)),
                "jobs": int(rng.integers(100, 500))
            })

        # Insertar orígenes puente sin puestos de trabajo
        demand_points.append({"id": "ghost_1", "location": [-87.00, 20.30], "pea_15ymas": 0.01, "jobs": 0})
        demand_points.append({"id": "ghost_2", "location": [-87.00, 20.55], "pea_15ymas": 0.01, "jobs": 0})

        rec = recommend_gravity_beta(demand_points=demand_points, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertLess(m["elongation_ratio"], 1.6)
        self.assertLess(m["major_axis_km"], 6.0)
        self.assertNotEqual(rec["archetype"], "corredor_lineal")
        self.assertIn(rec["archetype"], ["compacta", "intermedia"])

    def test_furness_orphan_fallback_equivalence(self):
        # Contraejemplo Sol: 4 celdas donde todos los pares exceden max_distance_km (> 55 km).
        # Furness no descarta la masa ni aborta: conecta cada fila/columna huérfana a sus <= 5
        # contrapartes más cercanas en la misma zona.
        # El calibrador replica exactamente este respaldo en lugar de caer en bbox_fallback.
        dp_distant = [
            {"id": "p1", "location": [-87.00, 20.00], "pea_15ymas": 100, "jobs": 0},
            {"id": "p2", "location": [-87.00, 20.60], "pea_15ymas": 0, "jobs": 100},
            {"id": "p3", "location": [-87.00, 21.20], "pea_15ymas": 100, "jobs": 0},
            {"id": "p4", "location": [-87.00, 21.80], "pea_15ymas": 0, "jobs": 100},
        ]
        rec = recommend_gravity_beta(demand_points=dp_distant, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        self.assertGreaterEqual(m["admissible_pairs"], 2)
        self.assertGreaterEqual(m["opportunity_median_km"], 60.0)

        # Verificar que simulate_gravity_demand genera cohortes válidas con el beta calibrado
        pops = simulate_gravity_demand(demand_points=dp_distant, max_distance_km=55.0, beta=rec["recommended_beta"], seed=42)
        self.assertEqual(len(pops), 2)
        total_commuters = sum(p["size"] for p in pops)
        self.assertEqual(total_commuters, 200)

    def test_streaming_low_memory_and_runtime(self):
        # Auditoría Sol: Eficiencia de memoria < 25 MB y tiempo < 2.5 s en 2,025 nodos (4.1M pares).
        import time, tracemalloc
        dp_grid = []
        node_idx = 0
        for r in range(45):
            for c in range(45):
                dp_grid.append({
                    "id": f"node_{node_idx}",
                    "location": [-99.15 + c * 0.005, 19.40 + r * 0.005],
                    "pea_15ymas": 50,
                    "jobs": 50
                })
                node_idx += 1

        tracemalloc.start()
        t0 = time.time()
        rec = recommend_gravity_beta(demand_points=dp_grid, max_distance_km=55.0)
        elapsed = time.time() - t0
        _, peak_bytes = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        self.assertIsNotNone(rec)
        self.assertLess(elapsed, 2.5, f"Tiempo excesivo: {elapsed:.2f}s (meta < 2.5s)")
        peak_mb = peak_bytes / (1024 * 1024)
        self.assertLess(peak_mb, 30.0, f"Pico de memoria excesivo: {peak_mb:.1f} MB (meta < 30 MB incremental)")

    def test_purely_residential_zone_global_fallback(self):
        # Contraejemplo Sol: Zona exclusivamente residencial sin empleos locales.
        # simulate_gravity_demand() conecta los residentes a los <= 5 destinos globales más cercanos.
        # El calibrador debe replicar este respaldo global en lugar de abortar hacia bbox_fallback.
        isolated_zones = [
            {"name": "Zone_Res", "bbox": [-87.10, 19.95, -86.90, 20.05]},
            {"name": "Zone_Jobs", "bbox": [-87.10, 20.15, -86.90, 20.25]},
        ]
        demand_points = [
            {"id": "res_1", "location": [-87.00, 20.00], "pea_15ymas": 100, "jobs": 0},
            {"id": "res_2", "location": [-87.01, 20.01], "pea_15ymas": 100, "jobs": 0},
            {"id": "job_1", "location": [-87.00, 20.20], "pea_15ymas": 0, "jobs": 100},
            {"id": "job_2", "location": [-87.01, 20.21], "pea_15ymas": 0, "jobs": 100},
        ]
        rec = recommend_gravity_beta(demand_points=demand_points, isolated_zones=isolated_zones, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        self.assertGreaterEqual(m["admissible_pairs"], 2)
        self.assertGreater(rec["recommended_beta"], 0.0)

        # Verificar concordancia estricta con simulate_gravity_demand()
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0,
            beta=rec["recommended_beta"],
            seed=42
        )
        self.assertEqual(len(pops), 2)
        total_commuters = sum(p["size"] for p in pops)
        self.assertEqual(total_commuters, 200)
        for p in pops:
            self.assertIn(p["residenceId"], ["res_1", "res_2"])
            self.assertIn(p["jobId"], ["job_1", "job_2"])

    def test_global_orphan_fallback_exact_support_and_probabilities(self):
        # Contraejemplo Sol: Zona residencial sin empleo local frente a 6 destinos globales (A..F).
        # Los orígenes tienen IDs 'A' y 'B'. Los 6 destinos están ordenados por distancia creciente:
        # dist(A) < dist(B) < dist(C) < dist(D) < dist(E) < dist(F).
        # simulate_gravity_demand() toma los 5 destinos más cercanos por distancia cruda (A..E)
        # sin penalizar por ID en el fallback global.
        # El calibrador debe admitir exactamente los mismos 5 destinos (A..E) y excluir F,
        # garantizando identidad estricta de soporte y probabilidades de viaje.
        isolated_zones = [
            {"name": "Zone_Res", "bbox": [-87.10, 19.95, -86.90, 20.05]},
            {"name": "Zone_Jobs", "bbox": [-87.10, 20.15, -86.90, 20.35]},
        ]
        demand_points = [
            {"id": "A", "location": [-87.00, 20.00], "pea_15ymas": 100, "jobs": 0},
            {"id": "B", "location": [-87.01, 20.00], "pea_15ymas": 100, "jobs": 0},
            {"id": "A", "location": [-87.00, 20.16], "pea_15ymas": 0, "jobs": 100},  # ~17.7 km (1er más cercano a A)
            {"id": "B", "location": [-87.00, 20.18], "pea_15ymas": 0, "jobs": 100},  # ~19.9 km (2do)
            {"id": "C", "location": [-87.00, 20.20], "pea_15ymas": 0, "jobs": 100},  # ~22.1 km (3ro)
            {"id": "D", "location": [-87.00, 20.22], "pea_15ymas": 0, "jobs": 100},  # ~24.3 km (4to)
            {"id": "E", "location": [-87.00, 20.24], "pea_15ymas": 0, "jobs": 100},  # ~26.5 km (5to)
            {"id": "F", "location": [-87.00, 20.32], "pea_15ymas": 0, "jobs": 100},  # ~35.4 km (6to - debe quedar EXCLUIDO)
        ]

        rec = recommend_gravity_beta(demand_points=demand_points, isolated_zones=isolated_zones, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        # 2 orígenes x 5 destinos más cercanos = exactamente 10 pares admisibles en el calibrador
        self.assertEqual(m["admissible_pairs"], 10)

        # En simulate_gravity_demand(), los 5 destinos con probabilidad positiva son A, B, C, D, E
        # Destino F tiene probabilidad estrictamente cero y nunca recibe viajes.
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0,
            beta=rec["recommended_beta"],
            target_pop_size=10,
            seed=42
        )
        self.assertGreater(len(pops), 0)
        job_ids_reached = set(p["jobId"] for p in pops)
        self.assertNotIn("F", job_ids_reached, "Destino F (6to más lejano) no debe recibir viajes en el fallback global")
        self.assertTrue(job_ids_reached.issubset({"A", "B", "C", "D", "E"}))

        # Verificar que el origen 'A' viaja a su destino coincidente 'A' (no fue penalizado espuriamente)
        trips_from_A = [p for p in pops if p["residenceId"] == "A"]
        self.assertGreater(len(trips_from_A), 0)
        dest_for_A = set(p["jobId"] for p in trips_from_A)
        self.assertIn("A", dest_for_A, "Origen A debe poder viajar a Destino A en el fallback global")

    def test_global_orphan_fallback_colocated_destinations_multiplicity(self):
        # Contraejemplo Sol: Dos destinos colocados (A y G) en un conjunto de 6 destinos (A, G, B, C, D, F).
        # simulate_gravity_demand() selecciona los 5 destinos más cercanos por fila de regular_dests (A, G, B, C, D).
        # El calibrador debe seleccionar los 5 destinos sobre las filas originales preservando su multiplicidad,
        # agregando sus pesos en la celda colocada (2/5) y excluyendo estrictamente a F (0/5).
        isolated_zones = [
            {"name": "Zone_Res", "bbox": [-87.10, 19.95, -86.90, 20.05]},
            {"name": "Zone_Jobs", "bbox": [-87.10, 20.15, -86.90, 20.35]},
        ]
        demand_points = [
            {"id": "A", "location": [-87.00, 20.00], "pea_15ymas": 1000, "jobs": 0},
            {"id": "B", "location": [-87.01, 20.00], "pea_15ymas": 1000, "jobs": 0},
            {"id": "A", "location": [-87.00, 20.16], "pea_15ymas": 0, "jobs": 100},  # ~17.7 km (1er más cercano)
            {"id": "G", "location": [-87.00, 20.16], "pea_15ymas": 0, "jobs": 100},  # ~17.7 km (colocado con A)
            {"id": "B", "location": [-87.00, 20.18], "pea_15ymas": 0, "jobs": 100},  # ~19.9 km (3er)
            {"id": "C", "location": [-87.00, 20.20], "pea_15ymas": 0, "jobs": 100},  # ~22.1 km (4to)
            {"id": "D", "location": [-87.00, 20.22], "pea_15ymas": 0, "jobs": 100},  # ~24.3 km (5to)
            {"id": "F", "location": [-87.00, 20.32], "pea_15ymas": 0, "jobs": 100},  # ~35.4 km (6to - excluido)
        ]

        rec = recommend_gravity_beta(demand_points=demand_points, isolated_zones=isolated_zones, max_distance_km=55.0)
        self.assertIsNotNone(rec)
        m = rec["metrics"]
        self.assertEqual(m["method"], "demand_points")
        # 2 orígenes x 4 celdas espaciales alcanzadas (A+G colapsados, B, C, D) = 8 pares espaciales
        self.assertEqual(m["admissible_pairs"], 8)

        # En simulate_gravity_demand(), los 5 destinos con probabilidad positiva son A, G, B, C, D
        pops = simulate_gravity_demand(
            demand_points=demand_points,
            isolated_zones=isolated_zones,
            max_distance_km=55.0,
            beta=rec["recommended_beta"],
            target_pop_size=50,
            seed=42
        )
        self.assertGreater(len(pops), 0)
        job_ids_reached = set(p["jobId"] for p in pops)
        self.assertNotIn("F", job_ids_reached, "Destino F (6to más lejano) debe recibir 0 viajes")
        self.assertTrue(job_ids_reached.issubset({"A", "G", "B", "C", "D"}))


if __name__ == "__main__":
    unittest.main()
