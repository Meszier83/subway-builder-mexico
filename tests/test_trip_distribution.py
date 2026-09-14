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
