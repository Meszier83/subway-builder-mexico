"""
tests.test_urban_lod
====================
Pruebas unitarias para el subsistema de Zonificación Concéntrica y Nivel
de Detalle Urbano (Urban Core AOI LOD) de Subway Builder México.
"""

import unittest
import os
import json
import tempfile
import shutil
import shapely
from unittest.mock import patch, MagicMock

from sb_mexico.cartography import build_city_map, build_city_map_wsl
from sb_mexico.cartography_runner import apply_urban_lod_filtering
from tools.wizard import save_full_city_data, load_city_data


class TestUrbanCoreLOD(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sample_polygon = [
            [-86.90, 21.10],
            [-86.80, 21.10],
            [-86.80, 21.20],
            [-86.90, 21.20],
            [-86.90, 21.10]
        ]

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_build_city_map_signature_and_wsl_delegation(self):
        """Verifica que build_city_map acepte urban_core_polygon y genere urban_core.geojson."""
        dummy_pbf = os.path.join(self.test_dir, "test.osm.pbf")
        with open(dummy_pbf, "w") as f:
            f.write("dummy")

        with patch("sb_mexico.cartography.is_wsl_available", return_value=(True, "Ubuntu", ["osmium"])):
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.stdout = None
                mock_proc.wait.return_value = 0
                mock_popen.return_value = mock_proc

                res = build_city_map(
                    city_code="TEST",
                    bbox=[-87.0, 21.0, -86.7, 21.3],
                    osm_pbf_path=dummy_pbf,
                    output_dir=self.test_dir,
                    urban_core_polygon=self.sample_polygon
                )

                # Verificar que se creó urban_core.geojson en output_dir
                core_json = os.path.join(self.test_dir, "urban_core.geojson")
                self.assertTrue(os.path.exists(core_json))
                with open(core_json, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                self.assertEqual(cdata["type"], "FeatureCollection")
                self.assertEqual(cdata["features"][0]["geometry"]["type"], "Polygon")

                # Verificar que se incluyó --urban-core-geojson en la invocación a WSL
                call_args = mock_popen.call_args[0][0]
                self.assertIn("--urban-core-geojson", call_args)

    def test_urban_core_yaml_serialization_persistence(self):
        """Verifica que save_full_city_data persista y recargue urban_core_polygon sin pérdida."""
        city_data = {
            "city": {
                "name": "Cancún Test",
                "code": "CUN",
                "bbox": [-87.0, 21.0, -86.7, 21.3],
                "urban_core_polygon": self.sample_polygon
            },
            "macroeconomics": {},
            "data_dir": "data/cancun"
        }
        yaml_path = "cities/test_lod_city.yaml"
        try:
            save_full_city_data(yaml_path, city_data)

            with open(yaml_path, "r", encoding="utf-8") as f:
                raw_content = f.read()
            self.assertIn("urban_core_polygon:", raw_content)

            loaded = load_city_data(yaml_path)
            self.assertIn("urban_core_polygon", loaded["city"])
            self.assertEqual(loaded["city"]["urban_core_polygon"], self.sample_polygon)
        finally:
            if os.path.exists(yaml_path):
                os.remove(yaml_path)

    def test_building_filter_spatial_intersection(self):
        """Verifica la lógica espacial vectorial de filtrado de centroides de edificios."""
        poly_geom = shapely.Polygon(self.sample_polygon)
        
        # Puntos: uno adentro y uno afuera
        inside_pt = shapely.Point(-86.85, 21.15)
        outside_pt = shapely.Point(-86.95, 21.25)
        
        pts = shapely.points([inside_pt.x, outside_pt.x], [inside_pt.y, outside_pt.y])
        mask = shapely.intersects(poly_geom, pts)
        
        self.assertTrue(mask[0])
        self.assertFalse(mask[1])

    def test_apply_urban_lod_filtering_fallback_when_missing(self):
        """Verifica que apply_urban_lod_filtering retorne input_pbf sin romper si faltan archivos."""
        res = apply_urban_lod_filtering(
            input_pbf="non_existent.pbf",
            bbox=[-87.0, 21.0, -86.7, 21.3],
            urban_core_geojson="non_existent.geojson",
            build_dir=self.test_dir,
            city_code="TEST"
        )
        self.assertEqual(res, "non_existent.pbf")


if __name__ == "__main__":
    unittest.main()
