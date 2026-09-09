"""
tests.test_cartography_districts
=================================
Pruebas unitarias para el etiquetado canónico de campus universitarios (type: college)
y distritos comerciales (type: commercial), así como la regla espacial "Campus Wins"
(disyuntividad geométrica estricta entre colegios y comercios en mallas vectoriales 3D).
"""

import unittest
from shapely.geometry import Polygon, box, mapping, shape
from sb_mexico.cartography import process_commercial_college_features


class TestCartographyDistricts(unittest.TestCase):
    def test_tagging_college_and_commercial(self):
        """Verifica que las etiquetas canónicas 'type' y 'kind' se asignen correctamente."""
        features = [
            {
                "id": 1,
                "geometry": mapping(box(0, 0, 10, 10)),
                "properties": {"kind": "university"}
            },
            {
                "id": 2,
                "geometry": mapping(box(20, 20, 30, 30)),
                "properties": {"kind": "commercial"}
            },
            {
                "id": 3,
                "geometry": mapping(box(40, 40, 50, 50)),
                "properties": {"kind": "retail"}
            },
            {
                "id": 4,
                "geometry": mapping(box(60, 60, 70, 70)),
                "properties": {"type": "college", "sort_rank": 100}
            }
        ]

        result = process_commercial_college_features(features)
        self.assertEqual(len(result), 4)

        # 1. Universidad -> type: college, kind: college
        self.assertEqual(result[0]["properties"]["type"], "college")
        self.assertEqual(result[0]["properties"]["kind"], "college")

        # 2. Comercial -> type: commercial, kind: commercial
        self.assertEqual(result[1]["properties"]["type"], "commercial")
        self.assertEqual(result[1]["properties"]["kind"], "commercial")

        # 3. Retail -> type: commercial, kind: retail
        self.assertEqual(result[2]["properties"]["type"], "commercial")
        self.assertEqual(result[2]["properties"]["kind"], "retail")

        # 4. College explícito -> type: college, kind: college
        self.assertEqual(result[3]["properties"]["type"], "college")
        self.assertEqual(result[3]["properties"]["kind"], "college")

    def test_campus_wins_spatial_disjointness(self):
        """
        Verifica la regla canónica 'Campus Wins':
        Donde un campus universitario y un distrito comercial se superponen,
        el campus gana: la geometría comercial se recorta alrededor del campus,
        garantizando que la intersección entre ambos sea de área exactamente 0.
        """
        # Comercial: [0, 0] a [10, 10] -> Área = 100
        commercial_box = box(0, 0, 10, 10)
        # Campus: [5, 5] a [15, 15] -> Área = 100.
        # Solapamiento: [5, 5] a [10, 10] -> Área solapada = 25.
        college_box = box(5, 5, 15, 15)

        features = [
            {
                "id": 101,
                "geometry": mapping(commercial_box),
                "properties": {"kind": "commercial"}
            },
            {
                "id": 102,
                "geometry": mapping(college_box),
                "properties": {"kind": "university"}
            }
        ]

        result = process_commercial_college_features(features)
        self.assertEqual(len(result), 2)

        # Encontrar geometrías resultantes
        college_feat = next(f for f in result if f["properties"]["type"] == "college")
        commercial_feat = next(f for f in result if f["properties"]["type"] == "commercial")

        geom_college = shape(college_feat["geometry"])
        geom_commercial = shape(commercial_feat["geometry"])

        # 1. El campus universitario conserva el 100% de su área original
        self.assertAlmostEqual(geom_college.area, 100.0, places=4)

        # 2. El distrito comercial pierde exactamente el área solapada (100 - 25 = 75)
        self.assertAlmostEqual(geom_commercial.area, 75.0, places=4)

        # 3. Disyuntividad estricta: la intersección entre ambos es vacía (área = 0)
        intersection_area = geom_commercial.intersection(geom_college).area
        self.assertAlmostEqual(intersection_area, 0.0, places=6)

    def test_water_mask_subtraction(self):
        """Verifica que el agua se sustraiga de colegios y distritos comerciales."""
        water = box(0, 0, 5, 10)

        features = [
            {
                "id": 201,
                "geometry": mapping(box(0, 0, 10, 10)),
                "properties": {"kind": "university"}
            }
        ]

        result = process_commercial_college_features(features, water_mask=water)
        self.assertEqual(len(result), 1)

        geom = shape(result[0]["geometry"])
        self.assertAlmostEqual(geom.area, 50.0, places=4)


if __name__ == "__main__":
    unittest.main()
