"""
tests.test_toponymy_pipeline
============================
Verifica la integridad del motor de toponimia v2:
1. Descarte de centroides administrativos (nunca convertir fronteras en ciudades).
2. Deduplicación metropolitana de ciudades (solo 1 Cancún en city_labels).
3. Transmisión del parámetro places en build_city_map y build_city_map_wsl.
4. Escaneo integral de toponimia en scan_city_settlements_catalog.
"""

import unittest
import os
import inspect
from sb_mexico.cartography import build_city_map, build_city_map_wsl
from sb_mexico.cartography_runner import run_cartography
from sb_mexico.toponymy import scan_city_settlements_catalog
from tools.patch_depot_wsl import patch_polygon_neighborhoods


class TestToponymyPipeline(unittest.TestCase):

    def test_signatures_accept_places(self):
        """Verifica que build_city_map y build_city_map_wsl acepten places."""
        sig_wsl = inspect.signature(build_city_map_wsl)
        self.assertIn("places", sig_wsl.parameters)
        self.assertIn("curated_places_geojson", sig_wsl.parameters)

        sig_main = inspect.signature(build_city_map)
        self.assertIn("places", sig_main.parameters)

        sig_runner = inspect.signature(run_cartography)
        self.assertIn("curated_places", sig_runner.parameters)

    def test_patch_polygon_neighborhoods_admin_boundary_filter(self):
        """Verifica que el parche contenga el filtro de fronteras y n/place para cities."""
        dummy_content = """            # Build the osmium filter string
            # e.g., "n/place=city n/place=borough"
            filter_cmd.extend([f"n/place{self.places_suffix}={t}" for t in tags])
            filter_cmd.extend(["-o", str(osm_pbf), "--overwrite"])
            self._run_command(filter_cmd)
            self._run_command(["osmium", "export", str(osm_pbf), "-o", 
                               str(geojson), "--overwrite"])
            self._rewrite_label_geojson_names(geojson)"""

        patched, modified = patch_polygon_neighborhoods(dummy_content)
        self.assertTrue(modified)
        self.assertIn('if name == "cities":', patched)
        self.assertIn('filter_cmd.extend([f"n/place', patched)
        self.assertIn('props.get("boundary") == "administrative"', patched)
        self.assertIn('SB_CURATED_PLACES_GEOJSON', patched)

    def test_cancun_catalog_single_city_node(self):
        """Verifica que el catálogo escaneado de Cancún solo tenga 1 nodo de ciudad Cancún."""
        cancun_yaml = "cities/cancun_riviera_maya.yaml"
        if not os.path.exists(cancun_yaml):
            self.skipTest("cancun_riviera_maya.yaml no disponible")

        cat = scan_city_settlements_catalog(cancun_yaml, min_count=8)
        self.assertGreater(cat["total"], 0)

        # Buscar nodos de ciudad con nombre 'Cancún'
        city_cancuns = [
            p for p in cat["places"]
            if p["type"] == "city" and "canc" in p["name"].lower()
        ]
        self.assertEqual(len(city_cancuns), 1, f"Debe existir exactamente 1 nodo city Cancún, encontrados: {city_cancuns}")
        c = city_cancuns[0]
        # El nodo debe estar en el centro de Cancún (lat ~21.15, lon ~-86.84), nunca en el sur (21.10)
        self.assertAlmostEqual(c["loc"][0], -86.84258, delta=0.01)
        self.assertAlmostEqual(c["loc"][1], 21.15275, delta=0.01)


if __name__ == "__main__":
    unittest.main()
