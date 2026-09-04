"""
tests.test_road_routing
=======================
Pruebas unitarias para el indice de ruteo vial arterial (ArterialRoadIndex),
las tres salvaguardas fisicas (piso, techo 3.5x, fallback seguro) y la integracion
con simulate_gravity_demand en sb_mexico.
"""

import unittest
import math
import numpy as np
import geopandas as gpd
from shapely.geometry import LineString

from sb_mexico.gravity import (
    build_arterial_road_network,
    ArterialRoadIndex,
    calculate_commute_impedance,
    simulate_gravity_demand,
    build_demand_grid
)


class TestArterialRoadRouting(unittest.TestCase):
    def setUp(self):
        # Red vial sintetica en forma de 'U' alrededor de un obstaculo (laguna ficticia)
        # Origen A en (-86.80, 21.10)
        # Esquina N1 en (-86.80, 21.20) (~11 km al norte)
        # Esquina N2 en (-86.70, 21.20) (~10 km al este)
        # Destino B en (-86.70, 21.10) (~11 km al sur)
        # Distancia en linea recta A -> B: ~10 km
        # Distancia por carretera A -> N1 -> N2 -> B: ~32 km (~3.2x linea recta)
        line_w = LineString([(-86.80, 21.10), (-86.80, 21.20)])
        line_n = LineString([(-86.80, 21.20), (-86.70, 21.20)])
        line_e = LineString([(-86.70, 21.20), (-86.70, 21.10)])

        self.u_roads_gdf = gpd.GeoDataFrame({
            "roadClass": ["major", "major", "major"],
            "geometry": [line_w, line_n, line_e]
        }, crs="EPSG:4326")

    def test_build_arterial_road_network_empty(self):
        self.assertIsNone(build_arterial_road_network(None))
        empty_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        self.assertIsNone(build_arterial_road_network(empty_gdf))

    def test_u_shape_detour_around_obstacle(self):
        road_index = build_arterial_road_network(self.u_roads_gdf)
        self.assertIsNotNone(road_index)

        orig = [-86.80, 21.10]
        dest = [-86.70, 21.10]
        # Distancia euclidiana aproximada: ~10.3 km
        cos_lat = math.cos(math.radians(21.10))
        euclid_m = math.hypot(0.10 * 111320.0 * cos_lat, 0.0)
        euclid_km = euclid_m / 1000.0

        road_m, driving_sec = road_index.get_driving_impedance(orig, dest, euclid_km)

        # La distancia por carretera debe reflejar el rodeo de la U (alrededor de 32 km)
        self.assertGreater(road_m, euclid_m * 2.0)
        self.assertLessEqual(road_m, euclid_m * 3.5)
        # El tiempo de manejo debe ser mayor al del calculo euclidiano directo
        _, base_sec = calculate_commute_impedance(euclid_km)
        self.assertGreater(driving_sec, base_sec)

    def test_safeguard_1_physical_floor(self):
        road_index = build_arterial_road_network(self.u_roads_gdf)
        orig = [-86.80, 21.10]
        dest = [-86.80, 21.11]
        euclid_km = 1.1
        road_m, _ = road_index.get_driving_impedance(orig, dest, euclid_km)
        # Nunca puede ser menor a la distancia en linea recta
        self.assertGreaterEqual(road_m, euclid_km * 1000.0)

    def test_safeguard_2_clamped_ceiling(self):
        # Crear carretera con desvio artificial extremo (> 5x)
        line1 = LineString([(-86.80, 21.10), (-86.80, 21.80)]) # 77 km al norte
        line2 = LineString([(-86.80, 21.80), (-86.79, 21.80)])
        line3 = LineString([(-86.79, 21.80), (-86.79, 21.10)]) # 77 km al sur
        gdf_extreme = gpd.GeoDataFrame({
            "roadClass": ["major", "major", "major"],
            "geometry": [line1, line2, line3]
        }, crs="EPSG:4326")
        road_index = build_arterial_road_network(gdf_extreme)

        orig = [-86.80, 21.10]
        dest = [-86.79, 21.10] # solo 1 km de separacion este-oeste
        euclid_km = 1.0
        road_m, _ = road_index.get_driving_impedance(orig, dest, euclid_km)

        # El techo acotado debe limitar el desvio maximo a 3.5x
        self.assertLessEqual(road_m, 3.5 * euclid_km * 1000.0 + 50.0)

    def test_safeguard_3_disconnected_fallback(self):
        # Dos segmentos viales aislados sin conexion entre si
        line_a = LineString([(-86.80, 21.10), (-86.80, 21.12)])
        line_b = LineString([(-86.60, 21.10), (-86.60, 21.12)])
        gdf_split = gpd.GeoDataFrame({
            "roadClass": ["major", "major"],
            "geometry": [line_a, line_b]
        }, crs="EPSG:4326")
        road_index = build_arterial_road_network(gdf_split)

        orig = [-86.80, 21.10]
        dest = [-86.60, 21.10]
        euclid_km = 20.0

        road_m, driving_sec = road_index.get_driving_impedance(orig, dest, euclid_km)
        base_m, base_sec = calculate_commute_impedance(euclid_km)

        # Debe recurrir suavemente al calculo de impedancia continuo
        self.assertEqual(road_m, base_m)
        self.assertEqual(driving_sec, base_sec)

    def test_simulate_gravity_demand_with_road_index(self):
        road_index = build_arterial_road_network(self.u_roads_gdf)
        demand_points = [
            {
                "id": "dp_0001",
                "location": [-86.80, 21.10],
                "jobs": 0,
                "residents": 100,
                "pea_15ymas": 60,
                "popIds": []
            },
            {
                "id": "dp_0002",
                "location": [-86.70, 21.10],
                "jobs": 80,
                "residents": 0,
                "pea_15ymas": 0,
                "popIds": []
            }
        ]

        pops = simulate_gravity_demand(
            demand_points=demand_points,
            road_index=road_index,
            target_pop_size=20,
            max_pop_size=50
        )

        self.assertGreater(len(pops), 0)
        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, 60, "Conservacion estricta de masa violada")

        for p in pops:
            self.assertIn("drivingSeconds", p)
            self.assertIn("drivingDistance", p)
            self.assertGreater(p["drivingSeconds"], 0)
            self.assertGreater(p["drivingDistance"], 0)
            # La distancia debe reflejar el rodeo de la red vial (> 10 km)
            self.assertGreater(p["drivingDistance"], 15000)

    def test_real_cancun_road_geojson_if_available(self):
        import os
        roads_path = os.path.join("dist", "cancn_-_riviera_maya", "roads.geojson")
        if not os.path.exists(roads_path):
            self.skipTest("roads.geojson de Cancun no disponible en dist")

        gdf = gpd.read_file(roads_path)
        road_index = build_arterial_road_network(gdf)
        self.assertIsNotNone(road_index)
        self.assertGreater(len(road_index.all_nodes_coords), 1000)

        # Prueba de viaje Centro -> Punta Nizuc (alrededor de la laguna)
        p_centro = [-86.827, 21.165]
        p_nizuc = [-86.784, 21.042]
        euclid_km = 14.3

        road_m, driving_sec = road_index.get_driving_impedance(p_centro, p_nizuc, euclid_km)
        base_m, base_sec = calculate_commute_impedance(euclid_km)

        # La distancia y tiempo vial deben reflejar el rodeo costero
        self.assertGreater(road_m, base_m)
        self.assertGreater(driving_sec, base_sec)
        self.assertGreater(road_m, 18000)  # > 18 km reales


if __name__ == "__main__":
    unittest.main()
