"""
tests.test_ocean_cache
======================
Pruebas unitarias para la infraestructura de aceleracion y cache incremental
del paso de calculo de oceano (batimetria submarina) en Subway Builder Mexico.
"""

import os
import unittest
from tools.patch_depot_wsl import patch_campus_wins, patch_ocean_cpu, patch_ocean_water_zoom


class TestOceanOptimization(unittest.TestCase):
    def test_patch_ocean_cpu_unthrottling(self):
        """Verifica que el parche reemplace el throttling de 50% de CPU por 100%."""
        dummy_content = """        # Select optimal number of parallel workers
        # Never use more than 1/2 the available cores
        ncores = max(1, min(self.ncores, os.cpu_count() // 2))
        
        if self.verb:
            print("parallel")"""

        patched, modified = patch_ocean_cpu(dummy_content)
        self.assertTrue(modified)
        self.assertIn("Subway Builder Mexico: use all allocated cores", patched)
        self.assertIn("ncores = max(1, self.ncores)", patched)

        # Idempotencia
        patched2, modified2 = patch_ocean_cpu(patched)
        self.assertFalse(modified2)
        self.assertEqual(patched, patched2)

    def test_patch_ocean_water_zoom_z14(self):
        """Verifica que el zoom de decodificacion de mascara de agua se optimice a z14."""
        dummy_content = """        if self.verb:
            print(f"  Decoding water & ocean polygons directly from {self.raw_mbtiles} at zoom {self.maxzoom}")
            
        water_polygons = []
        # Target high resolution Zoom level `self.maxzoom`
        tiles = list(mercantile.tiles(self.bbox[0], self.bbox[1], 
                                      self.bbox[2], self.bbox[3], 
                                      self.maxzoom))
        
        conn = sqlite3.connect(self.raw_mbtiles)
        cursor = conn.cursor()
        
        for t in tiles:
            tms_y = (1 << self.maxzoom) - 1 - t.y # Invert Y for standard TMS lookup scheme
            cursor.execute(
                f"SELECT tile_data FROM tiles WHERE zoom_level={self.maxzoom} AND tile_column=? AND tile_row=?",
                (t.x, tms_y)
            )"""

        patched, modified = patch_ocean_water_zoom(dummy_content)
        self.assertTrue(modified)
        self.assertIn("water_zoom = min(14, self.maxzoom)", patched)
        self.assertIn("zoom_level={water_zoom}", patched)

        # Idempotencia
        patched2, modified2 = patch_ocean_water_zoom(patched)
        self.assertFalse(modified2)
        self.assertEqual(patched, patched2)

    def test_cartography_runner_has_ocean_cache_logic(self):
        """Verifica que cartography_runner.py incluya la sincronizacion de cache y contornos."""
        runner_path = os.path.join(os.path.dirname(__file__), "..", "sb_mexico", "cartography_runner.py")
        with open(runner_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ocean_depth_index_contours.json.gz", content)
        self.assertIn("Restaurando batimetría previamente calculada", content)

    def test_cartography_has_ocean_cache_logic(self):
        """Verifica que cartography.py reconozca y transfiera los contornos auxiliares."""
        cart_path = os.path.join(os.path.dirname(__file__), "..", "sb_mexico", "cartography.py")
        with open(cart_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("ocean_depth_index_contours.json.gz", content)
        self.assertIn("Restaurando batimetría previamente calculada", content)


if __name__ == "__main__":
    unittest.main()
