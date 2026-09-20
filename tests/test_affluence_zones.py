"""
tests.test_affluence_zones
===========================
Pruebas unitarias para la funcionalidad de Zonas de Alta Afluencia
(High Affluence / Attraction Zones) en Subway Builder Mexico.

Verifica:
1. Inclusion espacial (poligonos, bounding boxes, auto-intersecciones).
2. Regla canonica MAX Priority en solapamientos.
3. Modulacion de empleo DENUE con preservacion estricta e inmutable de POIs Especiales.
4. Bono de alcance (reach_bonus) y piso de retencion local en Furness IPFP.
5. Invariante estricta de conservacion de masa (sum T_ij == sum PEA) en simulate_gravity_demand.
6. Serializacion y round-trip YAML en el Wizard.
"""

import os
import unittest
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString, Polygon, Point

from sb_mexico.gravity import (
    is_point_in_zone,
    get_zone_multipliers_for_point,
    build_demand_grid,
    furness_ipfp_balance,
    simulate_gravity_demand
)
from tools.wizard import (
    load_city_data,
    save_full_city_data
)


class TestAffluenceZoneSpatialLogic(unittest.TestCase):
    """Verifica el filtrado geometrico y clasificacion espacial de puntos en zonas."""

    def setUp(self):
        self.poly_zone = {
            "id": "zone_cbd",
            "name": "Distrito Central",
            "type": "polygon",
            "multiplier": 2.0,
            "reach_bonus": 0.35,
            "enabled": True,
            "coordinates": [
                [-86.850, 21.150],
                [-86.830, 21.150],
                [-86.830, 21.130],
                [-86.850, 21.130],
                [-86.850, 21.150]
            ]
        }
        self.bbox_zone = {
            "id": "zone_bbox",
            "name": "Corredor Norte",
            "type": "bbox",
            "multiplier": 1.5,
            "reach_bonus": 0.20,
            "enabled": True,
            "bbox": [-86.900, 21.200, -86.860, 21.240]
        }

    def test_point_inside_polygon(self):
        # Punto en el centro de la zona poligonal (-86.840, 21.140)
        self.assertTrue(is_point_in_zone(-86.840, 21.140, self.poly_zone))

    def test_point_outside_polygon(self):
        # Punto fuera de la zona poligonal
        self.assertFalse(is_point_in_zone(-86.800, 21.140, self.poly_zone))
        self.assertFalse(is_point_in_zone(-86.840, 21.180, self.poly_zone))

    def test_unclosed_polygon_automatically_closed(self):
        # Coordenadas sin vertice de cierre repetido
        unclosed = {
            "id": "zone_unclosed",
            "type": "polygon",
            "enabled": True,
            "coordinates": [
                [-86.850, 21.150],
                [-86.830, 21.150],
                [-86.830, 21.130],
                [-86.850, 21.130]
            ]
        }
        self.assertTrue(is_point_in_zone(-86.840, 21.140, unclosed))

    def test_self_intersecting_polygon_buffered_safely(self):
        # Poligono en forma de moño / figura de 8 (auto-interseccion en el medio)
        bow_tie = {
            "id": "zone_bowtie",
            "type": "polygon",
            "enabled": True,
            "coordinates": [
                [0.0, 0.0],
                [2.0, 2.0],
                [0.0, 2.0],
                [2.0, 0.0],
                [0.0, 0.0]
            ]
        }
        # No debe lanzar TopologicalError al llamar buffer(0)
        res = is_point_in_zone(0.5, 1.0, bow_tie)
        self.assertIsInstance(res, bool)

    def test_point_in_bbox_zone(self):
        # Dentro del bbox [-86.900, 21.200, -86.860, 21.240]
        self.assertTrue(is_point_in_zone(-86.880, 21.220, self.bbox_zone))
        # Fuera del bbox
        self.assertFalse(is_point_in_zone(-86.950, 21.220, self.bbox_zone))

    def test_bbox_with_inverted_coordinates_order(self):
        inverted_bbox = {
            "id": "zone_inv",
            "type": "bbox",
            "enabled": True,
            "bbox": [-86.860, 21.240, -86.900, 21.200]
        }
        self.assertTrue(is_point_in_zone(-86.880, 21.220, inverted_bbox))

    def test_disabled_zone_returns_false(self):
        disabled = dict(self.poly_zone)
        disabled["enabled"] = False
        self.assertFalse(is_point_in_zone(-86.840, 21.140, disabled))

    def test_empty_or_malformed_zone_safe(self):
        self.assertFalse(is_point_in_zone(-86.840, 21.140, {}))
        self.assertFalse(is_point_in_zone(-86.840, 21.140, {"coordinates": []}))
        self.assertFalse(is_point_in_zone(-86.840, 21.140, None))


class TestZoneMultipliersAndMaxPriority(unittest.TestCase):
    """Verifica la resolucion canonica MAX Priority ante solapamientos de zonas."""

    def setUp(self):
        # Zona 1: Moderada en empleo, alto alcance (ej. Turistica)
        self.zone1 = {
            "id": "zone_turistica",
            "type": "polygon",
            "multiplier": 1.8,
            "reach_bonus": 0.45,
            "enabled": True,
            "coordinates": [
                [10.0, 10.0],
                [20.0, 10.0],
                [20.0, 20.0],
                [10.0, 20.0],
                [10.0, 10.0]
            ]
        }
        # Zona 2: Alta densidad de empleo, alcance moderado (ej. CBD)
        self.zone2 = {
            "id": "zone_cbd",
            "type": "polygon",
            "multiplier": 2.5,
            "reach_bonus": 0.20,
            "enabled": True,
            "coordinates": [
                [15.0, 15.0],
                [25.0, 15.0],
                [25.0, 25.0],
                [15.0, 25.0],
                [15.0, 15.0]
            ]
        }
        self.zones = [self.zone1, self.zone2]

    def test_point_outside_all_zones(self):
        mult, reach, zid = get_zone_multipliers_for_point(5.0, 5.0, self.zones)
        self.assertEqual(mult, 1.0)
        self.assertEqual(reach, 0.0)
        self.assertIsNone(zid)

    def test_point_inside_only_zone1(self):
        mult, reach, zid = get_zone_multipliers_for_point(12.0, 12.0, self.zones)
        self.assertEqual(mult, 1.8)
        self.assertEqual(reach, 0.45)
        self.assertEqual(zid, "zone_turistica")

    def test_point_inside_only_zone2(self):
        mult, reach, zid = get_zone_multipliers_for_point(22.0, 22.0, self.zones)
        self.assertEqual(mult, 2.5)
        self.assertEqual(reach, 0.20)
        self.assertEqual(zid, "zone_cbd")

    def test_point_in_overlapping_region_max_priority(self):
        # Punto (18.0, 18.0) esta dentro de ambas zonas.
        # MAX Priority: multiplier = max(1.8, 2.5) = 2.5
        #               reach_bonus = max(0.45, 0.20) = 0.45
        mult, reach, zid = get_zone_multipliers_for_point(18.0, 18.0, self.zones)
        self.assertEqual(mult, 2.5)
        self.assertEqual(reach, 0.45)
        # No se debe multiplicar geometricamente (1.8 * 2.5 = 4.5 seria incorrecto)
        self.assertLess(mult, 4.0)

    def test_disabled_zone_ignored_in_overlap(self):
        self.zone2["enabled"] = False
        mult, reach, zid = get_zone_multipliers_for_point(18.0, 18.0, self.zones)
        # Solo zone1 debe tener efecto
        self.assertEqual(mult, 1.8)
        self.assertEqual(reach, 0.45)
        self.assertEqual(zid, "zone_turistica")


class TestBuildDemandGridWithAffluenceZones(unittest.TestCase):
    """
    Verifica que build_demand_grid escale el empleo DENUE dentro de zonas
    y que los POIs Especiales permanezcan 100% intactos con sus empleos manuales.
    """

    def setUp(self):
        # 1 establecimiento dentro de la zona, 1 fuera de la zona
        self.df_denue = pd.DataFrame([
            {"lon": 10.05, "lat": 20.05, "calibrated_jobs": 10.0}, # Dentro
            {"lon": 10.50, "lat": 20.50, "calibrated_jobs": 20.0}, # Fuera
        ])
        self.df_cpv = pd.DataFrame([
            {"lon": 10.05, "lat": 20.05, "pobtot_adj": 100.0, "pea_real": 60.0},
            {"lon": 10.50, "lat": 20.50, "pobtot_adj": 200.0, "pea_real": 120.0}
        ])
        # Red vial minima
        self.roads_gdf = gpd.GeoDataFrame({
            "geometry": [LineString([(9.9, 19.9), (10.6, 20.6)])]
        }, crs="EPSG:4326")

        # POI Especial ubicado DENTRO de la zona
        self.special_pois = [
            {
                "id": "UNI_Campus_Central",
                "name": "Universidad Central",
                "loc": [10.05, 20.05],
                "jobs": 5000,
                "radius_m": 300,
                "mode": "MAX"
            }
        ]

        self.affluence_zones = [
            {
                "id": "zone_test",
                "name": "Zona Potenciada",
                "type": "polygon",
                "multiplier": 2.5,
                "reach_bonus": 0.30,
                "enabled": True,
                "coordinates": [
                    [10.00, 20.00],
                    [10.10, 20.00],
                    [10.10, 20.10],
                    [10.00, 20.10],
                    [10.00, 20.00]
                ]
            }
        ]

    def test_special_poi_jobs_remain_strictly_unmodified(self):
        points, audit = build_demand_grid(
            df_denue=self.df_denue,
            df_cpv=self.df_cpv,
            special_pois=self.special_pois,
            roads_gdf=self.roads_gdf,
            grid_size=0.01,
            min_residents=5,
            min_jobs=2,
            affluence_zones=self.affluence_zones
        )

        # Buscar el punto correspondiente al POI Especial
        poi_pts = [p for p in points if p["id"] == "UNI_Campus_Central"]
        self.assertEqual(len(poi_pts), 1)
        poi_pt = poi_pts[0]

        # Regla de Oro: El POI Especial debe conservar exactamente 5000 empleos
        # NO debe ser multiplicado por 2.5x (lo cual daria 12500)
        self.assertEqual(poi_pt["jobs"], 5000)
        self.assertTrue(poi_pt.get("is_special", False))

    def test_denue_outside_zone_not_boosted(self):
        # Sin zonas
        pts_baseline, _ = build_demand_grid(
            df_denue=self.df_denue,
            df_cpv=self.df_cpv,
            special_pois=[],
            roads_gdf=self.roads_gdf,
            grid_size=0.01,
            min_residents=5,
            min_jobs=2,
            affluence_zones=[]
        )

        # Con zona para el punto interior
        pts_boosted, _ = build_demand_grid(
            df_denue=self.df_denue,
            df_cpv=self.df_cpv,
            special_pois=[],
            roads_gdf=self.roads_gdf,
            grid_size=0.01,
            min_residents=5,
            min_jobs=2,
            affluence_zones=self.affluence_zones
        )

        # Localizar el punto exterior (10.50, 20.50)
        pt_out_base = min(pts_baseline, key=lambda p: abs(p["location"][0] - 10.50))
        pt_out_boost = min(pts_boosted, key=lambda p: abs(p["location"][0] - 10.50))
        self.assertEqual(pt_out_base["jobs"], pt_out_boost["jobs"])

        # Localizar el punto interior (10.05, 20.05)
        pt_in_base = min(pts_baseline, key=lambda p: abs(p["location"][0] - 10.05))
        pt_in_boost = min(pts_boosted, key=lambda p: abs(p["location"][0] - 10.05))
        self.assertGreater(pt_in_boost["jobs"], pt_in_base["jobs"])
        # Multiplicador 2.5x
        self.assertAlmostEqual(pt_in_boost["jobs"] / pt_in_base["jobs"], 2.5, delta=0.1)


class TestFurnessIpfpReachBonusAndLocalRetention(unittest.TestCase):
    """Verifica el comportamiento matematico del algoritmo Furness IPFP con reach_bonus."""

    def test_reach_bonus_flattens_friction_and_conserves_rows(self):
        orig_pea = np.array([100.0, 100.0])
        dest_jobs = np.array([200.0, 200.0]) # Dos destinos con igual masa de empleo

        # Matriz de distancia: origen 0 esta cerca de destino 0 (2 km) y lejos de destino 1 (30 km)
        # Origen 1 esta a 30 km de destino 0 y a 30 km de destino 1
        dist_mat = np.array([
            [2.0, 30.0],
            [30.0, 30.0]
        ])

        # Destino 1 tiene un reach_bonus de 0.40 (expande su alcance metropolitano)
        reach_bonuses = np.array([0.0, 0.40])

        P_base = furness_ipfp_balance(orig_pea, dest_jobs, dist_mat, reach_bonuses=None, beta=0.12)
        P_boost = furness_ipfp_balance(orig_pea, dest_jobs, dist_mat, reach_bonuses=reach_bonuses, beta=0.12)

        # Invariante 1: Cada fila debe sumar 1.0 (conservacion de probabilidades)
        np.testing.assert_allclose(P_boost.sum(axis=1), np.array([1.0, 1.0]), atol=1e-5)

        # Invariante 2: En el origen 0 a 30 km, el destino 1 con reach_bonus debe capturar
        # una proporcion significativamente mayor de viajes que en la linea base.
        self.assertGreater(P_boost[0, 1], P_base[0, 1])

        # Invariante 3: El destino 1 atrae mas demanda de origen 1 a igualdad de distancia (30 km)
        self.assertGreater(P_boost[1, 1], P_boost[1, 0])


class TestSimulateGravityDemandMassConservation(unittest.TestCase):
    """Verifica la invariante de conservacion estricta de masa sum T_ij == sum PEA."""

    def test_mass_conservation_with_affluence_zones(self):
        demand_points = [
            {"id": "res_1", "location": [-86.85, 21.15], "jobs": 5, "residents": 300, "pea_15ymas": 120, "popIds": []},
            {"id": "res_2", "location": [-86.84, 21.14], "jobs": 10, "residents": 400, "pea_15ymas": 180, "popIds": []},
            {"id": "res_3", "location": [-86.80, 21.10], "jobs": 15, "residents": 200, "pea_15ymas": 80, "popIds": []},
            {"id": "cbd_dest_1", "location": [-86.83, 21.15], "jobs": 250, "residents": 20, "pea_15ymas": 0, "popIds": []},
            {"id": "cbd_dest_2", "location": [-86.82, 21.14], "jobs": 350, "residents": 10, "pea_15ymas": 0, "popIds": []},
            {"id": "AIR_Cancun", "location": [-86.87, 21.03], "jobs": 500, "residents": 0, "pea_15ymas": 0, "popIds": [], "is_special": True},
        ]
        total_pea = sum(p["pea_15ymas"] for p in demand_points)
        self.assertEqual(total_pea, 380)

        affluence_zones = [
            {
                "id": "zone_cbd",
                "name": "CBD Central",
                "type": "polygon",
                "multiplier": 2.2,
                "reach_bonus": 0.40,
                "enabled": True,
                "coordinates": [
                    [-86.840, 21.160],
                    [-86.810, 21.160],
                    [-86.810, 21.130],
                    [-86.840, 21.130],
                    [-86.840, 21.160]
                ]
            }
        ]

        pops = simulate_gravity_demand(
            demand_points=demand_points,
            affluence_zones=affluence_zones,
            beta=0.12,
            target_pop_size=25,
            max_pop_size=50,
            seed=42
        )

        total_viajeros = sum(p["size"] for p in pops)
        # Regla de Oro: sum T_ij == sum PEA
        self.assertEqual(total_viajeros, total_pea)

        # Verificar integridad de los pops generados
        for pop in pops:
            self.assertIn("id", pop)
            self.assertIn("residenceId", pop)
            self.assertIn("jobId", pop)
            self.assertGreater(pop["size"], 0)
            self.assertGreater(pop["drivingDistance"], 0)
            self.assertGreater(pop["drivingSeconds"], 0)


class TestWizardAffluenceZoneRoundTrip(unittest.TestCase):
    """Verifica que el Wizard serialice y recupere zonas de alta afluencia sin perdidas."""

    def test_yaml_save_and_reload_affluence_zones(self):
        tmp_path = os.path.join(os.path.dirname(__file__), "tmp_test_affluence_city.yaml")
        saved_path = None

        test_data = {
            "city": {
                "code": "AFFL",
                "name": "Ciudad Afluente",
                "bbox": [-86.9, 21.1, -86.8, 21.2]
            },
            "macroeconomics": {
                "tasa_pea": 0.60
            },
            "pois": [
                {
                    "id": "UNI_Prueba",
                    "name": "Universidad de Prueba",
                    "loc": [-86.85, 21.15],
                    "jobs": 1500,
                    "radius_m": 500,
                    "mode": "MAX"
                }
            ],
            "affluence_zones": [
                {
                    "id": "zone_costa",
                    "name": "Corredor Costero",
                    "type": "polygon",
                    "archetype": "tourism",
                    "multiplier": 2.2,
                    "reach_bonus": 0.45,
                    "target_mode": "MULTIPLIER",
                    "color": "#F59E0B",
                    "enabled": True,
                    "coordinates": [
                        [-86.850, 21.150],
                        [-86.830, 21.150],
                        [-86.830, 21.130],
                        [-86.850, 21.130],
                        [-86.850, 21.150]
                    ]
                },
                {
                    "id": "zone_parque_industrial",
                    "name": "Parque Industrial",
                    "type": "bbox",
                    "archetype": "industrial",
                    "multiplier": 1.8,
                    "reach_bonus": 0.25,
                    "target_mode": "TARGET_CAPACITY",
                    "target_jobs": 35000,
                    "color": "#EF4444",
                    "enabled": False,
                    "bbox": [-86.900, 21.100, -86.880, 21.120]
                }
            ]
        }

        try:
            saved_path = save_full_city_data(tmp_path, test_data)
            reloaded = load_city_data(saved_path)

            self.assertIn("affluence_zones", reloaded)
            self.assertEqual(len(reloaded["affluence_zones"]), 2)

            z1 = reloaded["affluence_zones"][0]
            self.assertEqual(z1["id"], "zone_costa")
            self.assertEqual(z1["archetype"], "tourism")
            self.assertEqual(z1["multiplier"], 2.2)
            self.assertEqual(z1["reach_bonus"], 0.45)
            self.assertEqual(z1["color"], "#F59E0B")
            self.assertTrue(z1["enabled"])
            self.assertEqual(len(z1["coordinates"]), 5)

            z2 = reloaded["affluence_zones"][1]
            self.assertEqual(z2["id"], "zone_parque_industrial")
            self.assertEqual(z2["archetype"], "industrial")
            self.assertEqual(z2["multiplier"], 1.8)
            self.assertEqual(z2["target_mode"], "TARGET_CAPACITY")
            self.assertEqual(z2["target_jobs"], 35000)
            self.assertFalse(z2["enabled"])
            self.assertEqual(z2["bbox"], [-86.900, 21.100, -86.880, 21.120])
        finally:
            if saved_path and os.path.exists(saved_path):
                os.remove(saved_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
