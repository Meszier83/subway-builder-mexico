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
        candidates = [
            os.path.join("dist", "cancun", "roads.geojson"),
            os.path.join("dist", "cancn_-_riviera_maya", "roads.geojson")
        ]
        roads_path = next((p for p in candidates if os.path.exists(p)), None)
        if not roads_path:
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

    def test_straight_avenue_metric_accuracy(self):
        # Avenida recta norte-sur con vertices cada 100m (~11 km)
        pts = [(-86.80, round(21.10 + i * 0.001, 5)) for i in range(101)]
        line_straight = LineString(pts)
        gdf = gpd.GeoDataFrame({"roadClass": ["major"], "geometry": [line_straight]}, crs="EPSG:4326")
        road_index = build_arterial_road_network(gdf)
        self.assertIsNotNone(road_index)

        # Puntos a 1.1 km en la misma mitad de la avenida
        p1 = [-86.80, 21.11]
        p2 = [-86.80, 21.12]
        euclid_km = 0.01 * 110574.0 / 1000.0  # ~1.1057 km
        road_m, sec = road_index.get_driving_impedance(p1, p2, euclid_km)

        # La distancia por carretera debe ser practicamente identica a la euclidiana (ratio < 1.05)
        # y no estar inflada al techo de 3.5x
        ratio = road_m / (euclid_km * 1000.0)
        self.assertAlmostEqual(ratio, 1.0, delta=0.05)
        self.assertLess(road_m, euclid_km * 1000.0 * 1.2)

    def test_straight_avenue_across_junction_boundary(self):
        # Puntos a traves del punto medio de la avenida (21.14 a 21.16)
        pts = [(-86.80, round(21.10 + i * 0.001, 5)) for i in range(101)]
        line_straight = LineString(pts)
        gdf = gpd.GeoDataFrame({"roadClass": ["major"], "geometry": [line_straight]}, crs="EPSG:4326")
        road_index = build_arterial_road_network(gdf)

        p1 = [-86.80, 21.14]
        p2 = [-86.80, 21.16]
        euclid_km = 0.02 * 110574.0 / 1000.0  # ~2.2115 km
        road_m, _ = road_index.get_driving_impedance(p1, p2, euclid_km)

        ratio = road_m / (euclid_km * 1000.0)
        self.assertAlmostEqual(ratio, 1.0, delta=0.05)

    def test_configurable_max_detour_ratio(self):
        # Red vial con desvio extremo (> 5x)
        line1 = LineString([(-86.80, 21.10), (-86.80, 21.80)])
        line2 = LineString([(-86.80, 21.80), (-86.79, 21.80)])
        line3 = LineString([(-86.79, 21.80), (-86.79, 21.10)])
        gdf_extreme = gpd.GeoDataFrame({
            "roadClass": ["major", "major", "major"],
            "geometry": [line1, line2, line3]
        }, crs="EPSG:4326")

        # Con max_detour_ratio = 2.0
        road_index = build_arterial_road_network(gdf_extreme, max_detour_ratio=2.0)
        orig = [-86.80, 21.10]
        dest = [-86.79, 21.10]
        euclid_km = 1.0
        road_m, _ = road_index.get_driving_impedance(orig, dest, euclid_km)

        # Debe acotarse estrictamente al techo de 2.0x
        self.assertLessEqual(road_m, 2.0 * euclid_km * 1000.0 + 50.0)


class TestCanonicalOSRMIntegration(unittest.TestCase):
    """Pruebas unitarias para el fallback canónico y enriquecimiento con OSRM."""

    def test_canonical_driving_fallback_math(self):
        from sb_mexico.osrm import calculate_canonical_driving_fallback

        # Distancia euclidiana de 10,000 m (10 km)
        road_m, sec = calculate_canonical_driving_fallback(10_000.0)
        self.assertEqual(road_m, 13_000)
        # 13,000 / (40 / 3.6) = 1170 segundos
        self.assertEqual(sec, 1170)

        # Distancia cero o ultra-corta debe respetar los pisos de seguridad
        zero_m, zero_sec = calculate_canonical_driving_fallback(0.0)
        self.assertEqual(zero_m, 195)  # 150 * 1.3 = 195m
        self.assertEqual(zero_sec, 45)

    def test_enrich_pops_with_osrm_mocked(self):
        from unittest.mock import patch, MagicMock
        from sb_mexico.osrm import enrich_pops_with_osrm

        pops = [
            {"id": "pop_001", "residenceId": "dp_1", "jobId": "dp_2", "size": 150}
        ]
        demand_points = [
            {"id": "dp_1", "location": [-86.85, 21.15]},
            {"id": "dp_2", "location": [-86.80, 21.15]}
        ]

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": "Ok",
            "routes": [{
                "distance": 6421.0,
                "duration": 578.0,
                "geometry": {
                    "coordinates": [[-86.85, 21.15], [-86.82, 21.15], [-86.80, 21.15]]
                }
            }]
        }

        with patch("requests.Session.get", return_value=mock_resp):
            osrm_ok, osrm_fb = enrich_pops_with_osrm(pops, demand_points, osrm_url="http://mocked:5000")

        self.assertEqual(osrm_ok, 1)
        self.assertEqual(osrm_fb, 0)
        self.assertEqual(pops[0]["drivingDistance"], 6421)
        self.assertEqual(pops[0]["drivingSeconds"], 578)
        self.assertEqual(pops[0]["drivingPath"], [[-86.85, 21.15], [-86.82, 21.15], [-86.80, 21.15]])

    def test_enrich_pops_fallback_on_osrm_failure(self):
        from unittest.mock import patch
        from sb_mexico.osrm import enrich_pops_with_osrm

        pops = [
            {"id": "pop_001", "residenceId": "dp_1", "jobId": "dp_2", "size": 100}
        ]
        demand_points = [
            {"id": "dp_1", "location": [-86.85, 21.15]},
            {"id": "dp_2", "location": [-86.80, 21.15]}
        ]

        with patch("requests.Session.get", side_effect=Exception("Connection refused")):
            osrm_ok, osrm_fb = enrich_pops_with_osrm(pops, demand_points, osrm_url="http://invalid:5000")

        self.assertEqual(osrm_ok, 0)
        self.assertEqual(osrm_fb, 1)
        self.assertGreater(pops[0]["drivingDistance"], 4000)
        self.assertGreater(pops[0]["drivingSeconds"], 300)
        self.assertNotIn("drivingPath", pops[0])

    def test_enrich_pops_consecutive_errors_fail_fast(self):
        import requests
        from unittest.mock import patch
        from sb_mexico.osrm import enrich_pops_with_osrm

        # 10 pares únicos con fallo de conexión
        pops = [
            {"id": f"pop_{i}", "residenceId": f"dp_{i}", "jobId": f"dp_{i+10}", "size": 100}
            for i in range(10)
        ]
        demand_points = [
            {"id": f"dp_{i}", "location": [-86.85 + i * 0.005, 21.15]}
            for i in range(20)
        ]

        # Simular ConnectTimeout en cada llamada
        call_count = 0
        def fake_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise requests.exceptions.ConnectTimeout("Connection timed out")

        with patch("requests.Session.get", side_effect=fake_get):
            osrm_ok, osrm_fb = enrich_pops_with_osrm(pops, demand_points, osrm_url="http://invalid:5000")

        self.assertEqual(osrm_ok, 0)
        self.assertEqual(osrm_fb, 10)
        # Solo debió intentar 5 llamadas antes de cortar por fail-fast
        self.assertEqual(call_count, 5)
        # Todos los pops deben tener drivingDistance y drivingSeconds calculados
        for p in pops:
            self.assertGreater(p["drivingDistance"], 0)
            self.assertGreater(p["drivingSeconds"], 0)

    def test_merge_and_sync_preserves_driving_path(self):
        from sb_mexico.gravity import merge_identical_commutes, sync_demand_points_and_pops

        coords = [[-86.85, 21.15], [-86.82, 21.15], [-86.80, 21.15]]
        pops = [
            {
                "id": "pop_1",
                "residenceId": "dp_1",
                "jobId": "dp_2",
                "size": 80,
                "drivingDistance": 6400,
                "drivingSeconds": 570,
                "drivingPath": coords
            },
            {
                "id": "pop_2",
                "residenceId": "dp_1",
                "jobId": "dp_2",
                "size": 90,
                "drivingDistance": 6400,
                "drivingSeconds": 570,
                "drivingPath": coords
            }
        ]

        # 1. merge_identical_commutes debe preservar drivingPath
        merged = merge_identical_commutes(pops, max_pop_size=200)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["size"], 170)
        self.assertEqual(merged[0]["drivingPath"], coords)

        # 2. sync_demand_points_and_pops debe preservar drivingPath
        dps = [
            {"id": "dp_1", "location": [-86.85, 21.15], "residents": 500, "jobs": 0, "popIds": []},
            {"id": "dp_2", "location": [-86.80, 21.15], "residents": 0, "jobs": 500, "popIds": []}
        ]
        synced_dps, synced_pops = sync_demand_points_and_pops(dps, merged)
        self.assertEqual(len(synced_pops), 1)
        self.assertEqual(synced_pops[0]["drivingPath"], coords)


if __name__ == "__main__":
    unittest.main()
