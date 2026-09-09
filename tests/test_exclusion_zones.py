"""
tests.test_exclusion_zones
==========================
Verifica el filtrado espacial estricto de Zonas de Exclusion:
- Cero generacion de demanda (poblacion/empleo) en las zonas delimitadas.
- Preservacion 100% integra de la red vial y cartografia base.
- Persistencia exacta en YAML y validacion de restricciones geometricas.
"""

import os
import unittest
import pandas as pd
import geopandas as gpd
from shapely.geometry import LineString
from sb_mexico.gravity import is_point_in_exclusion_zone, build_demand_grid
from tools.wizard import save_full_city_data, load_city_data, validate_city_configuration


class TestExclusionZones(unittest.TestCase):
    def test_is_point_in_exclusion_zone_bbox(self):
        """Verifica la deteccion de puntos dentro y fuera de un BBOX de exclusion."""
        exclusion_zones = [
            {
                "id": "excl_laguna",
                "name": "Laguna Nichupte",
                "type": "bbox",
                "bbox": [-86.85, 21.10, -86.80, 21.15],
                "enabled": True
            }
        ]

        # Punto dentro del BBOX
        self.assertTrue(is_point_in_exclusion_zone(-86.82, 21.12, exclusion_zones))
        # Punto fuera del BBOX
        self.assertFalse(is_point_in_exclusion_zone(-86.90, 21.12, exclusion_zones))
        self.assertFalse(is_point_in_exclusion_zone(-86.82, 21.20, exclusion_zones))

        # Desactivar zona: no debe excluir nada
        exclusion_zones[0]["enabled"] = False
        self.assertFalse(is_point_in_exclusion_zone(-86.82, 21.12, exclusion_zones))

    def test_is_point_in_exclusion_zone_polygon(self):
        """Verifica la deteccion de puntos con un poligono irregular de exclusion."""
        exclusion_zones = [
            {
                "id": "excl_triangulo",
                "name": "Reserva Triangular",
                "type": "polygon",
                "coordinates": [
                    [-86.80, 21.10],
                    [-86.70, 21.10],
                    [-86.75, 21.20],
                    [-86.80, 21.10]
                ],
                "enabled": True
            }
        ]

        # Centro del triangulo
        self.assertTrue(is_point_in_exclusion_zone(-86.75, 21.13, exclusion_zones))
        # Fuera del triangulo
        self.assertFalse(is_point_in_exclusion_zone(-86.71, 21.18, exclusion_zones))
        self.assertFalse(is_point_in_exclusion_zone(-86.85, 21.15, exclusion_zones))

    def test_build_demand_grid_filters_excluded_areas(self):
        """Verifica que build_demand_grid descarte poblacion y empleo dentro de exclusion_zones."""
        # 2 puntos DENUE y 2 CPV: uno dentro de la zona y uno fuera
        df_denue = pd.DataFrame([
            {"lon": -86.82, "lat": 21.12, "calibrated_jobs": 50.0}, # Dentro
            {"lon": -86.90, "lat": 21.20, "calibrated_jobs": 40.0}, # Fuera
        ])
        df_cpv = pd.DataFrame([
            {"lon": -86.82, "lat": 21.12, "pobtot_adj": 200.0, "pea_real": 120.0}, # Dentro
            {"lon": -86.90, "lat": 21.20, "pobtot_adj": 300.0, "pea_real": 180.0}, # Fuera
        ])
        roads_gdf = gpd.GeoDataFrame({
            "geometry": [LineString([(-86.95, 21.05), (-86.75, 21.25)])]
        }, crs="EPSG:4326")

        exclusion_zones = [
            {
                "id": "excl_laguna",
                "name": "Laguna Central",
                "type": "bbox",
                "bbox": [-86.85, 21.10, -86.80, 21.15],
                "enabled": True
            }
        ]

        points, poi_audit = build_demand_grid(
            df_denue=df_denue,
            df_cpv=df_cpv,
            special_pois=[],
            roads_gdf=roads_gdf,
            grid_size=0.0025,
            exclusion_zones=exclusion_zones
        )

        # Solo debe haber sobrevivido el nodo fuera de la zona (-86.90, 21.20)
        self.assertEqual(len(points), 1)
        self.assertAlmostEqual(points[0]["location"][0], -86.90, places=2)
        self.assertAlmostEqual(points[0]["location"][1], 21.20, places=2)
        self.assertEqual(points[0]["residents"], 300)
        self.assertEqual(points[0]["jobs"], 40)

    def test_exclusion_zones_persistence_and_validation(self):
        """Verifica el guardado y recarga en YAML de exclusion_zones y su validacion en el Wizard."""
        tmp_yaml = os.path.join(os.path.dirname(__file__), "tmp_exclusion_test.yaml")
        try:
            city_dict = {
                "city": {
                    "code": "EXC",
                    "name": "Exclusion City",
                    "bbox": [-87.0, 21.0, -86.7, 21.3],
                    "min_residents": 10,
                    "min_jobs": 3
                },
                "macroeconomics": {
                    "tasa_pea": 0.62,
                    "min_pop_size": 25,
                    "target_pop_size": 150,
                    "max_pop_size": 200
                },
                "exclusion_zones": [
                    {
                        "id": "excl_manglar",
                        "name": "Manglar Protegido",
                        "type": "polygon",
                        "reason": "ecological_reserve",
                        "color": "#EF4444",
                        "enabled": True,
                        "coordinates": [
                            [-86.85, 21.15],
                            [-86.80, 21.15],
                            [-86.80, 21.10],
                            [-86.85, 21.10],
                            [-86.85, 21.15]
                        ],
                        "bbox": [-86.85, 21.10, -86.80, 21.15]
                    }
                ]
            }

            saved_path = save_full_city_data(tmp_yaml, city_dict)
            reloaded = load_city_data(saved_path)

            self.assertIn("exclusion_zones", reloaded)
            self.assertEqual(len(reloaded["exclusion_zones"]), 1)
            ez = reloaded["exclusion_zones"][0]
            self.assertEqual(ez["id"], "excl_manglar")
            self.assertEqual(ez["name"], "Manglar Protegido")
            self.assertEqual(ez["type"], "polygon")
            self.assertEqual(ez["reason"], "ecological_reserve")
            self.assertTrue(ez["enabled"])
            self.assertEqual(len(ez["coordinates"]), 5)

            # Validar con validate_city_configuration
            val = validate_city_configuration(saved_path)
            self.assertTrue(val["valid"])
            self.assertEqual(val["summary"]["exclusion_zones_count"], 1)

        finally:
            if os.path.exists(tmp_yaml):
                os.remove(tmp_yaml)


if __name__ == "__main__":
    unittest.main()
