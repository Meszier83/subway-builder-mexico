"""
tests.test_cartography_resilience
==================================
Pruebas de resiliencia cartografica:
1. Validacion de conversion de RAM (MB a GB) para MapGen.
2. Validacion de patch_ram_safety contra desbordamientos.
3. Validacion de patch_resilient_buildings (eliminacion de dependencia de Mapshaper en grandes volumenes).
"""

import unittest
from tools.patch_depot_wsl import patch_ram_safety, patch_resilient_buildings


class TestCartographyResilience(unittest.TestCase):
    def test_ram_gb_conversion(self):
        ram_mb = 5057
        ram_gb = max(2.0, round(ram_mb / 1000.0, 1))
        self.assertEqual(ram_gb, 5.1)
        self.assertLessEqual(ram_gb, 100.0)

        # Para maquinas pequeñas
        small_mb = 1500
        small_gb = max(2.0, round(small_mb / 1000.0, 1))
        self.assertEqual(small_gb, 2.0)

    def test_patch_ram_safety(self):
        dummy_content = '''
    @RAM.setter
    def RAM(self, value):
        if not isinstance(value, (int, float)):
            raise TypeError(f"RAM must be an int or float. Received: {type(value).__name__}")
        if value < 1:
            raise ValueError(f"RAM limit must be at least 1 GB. Received: {value}")
        # GB uses base 10 (1000) - not to be confused with GiB (1024)
        # We store as MB for internal CLI tool flags
        self._RAM = int(value * 1000)
'''
        patched, modified = patch_ram_safety(dummy_content)
        self.assertTrue(modified)
        self.assertIn("Subway Builder Mexico: Guard against values already in MB", patched)

        # Idempotencia: no debe modificar si ya esta aplicado
        patched2, modified2 = patch_ram_safety(patched)
        self.assertFalse(modified2)
        self.assertEqual(patched, patched2)

    def test_patch_resilient_buildings(self):
        dummy_content = '''
    def process_buildings(self):
        if self.verb:
            print("***** Processing Buildings *****")
        # 2. Mapshaper Cleanup
        cleaned_json = os.path.join(self.city_dir, "buildings_cleaned.json")
        mapshaper_cmd = (
            f"node --max-old-space-size={self.RAM} $(which mapshaper) "
            f"{self.buildings_geojson} -proj {self.epsg} -snap 0.5 -clean "
            f"-filter 'this.area > {self.building_index_filter_size}' "
            f"-simplify dp interval={self.building_index_simplification} "
            f"-proj wgs84 -o precision=0.00001 {cleaned_json}"
        )
        self._run_command(mapshaper_cmd)

    def _generate_building_tiles(self):
        mapshaper_cmd = (
            f"node --max-old-space-size={self.RAM} $(which mapshaper) "
            f"{self.buildings_geojson} -proj {self.epsg} -snap 0.5 "
            f"-filter 'this.area > {self.building_index_filter_size}' -clean "
            f"-simplify dp interval={self.building_tile_simplification} "
            f"-proj wgs84 -o precision=0.00001 {self.buildings_zoom_geojson}"
        )
        self._run_command(mapshaper_cmd)
        
        # Remove any features with no geometry
        with open(self.buildings_zoom_geojson, 'r') as f:
            geojson_data = json.load(f)
        geojson_data['features'] = [f for f in geojson_data['features'] \\
                                    if 'geometry' in f.keys() and f['geometry'] is not None]
        # Save the modified data
        with open(self.buildings_zoom_geojson, 'w', encoding='utf-8') as f:
            json.dump(geojson_data, f, indent=2)
        
        # Add default building height where needed
        self._set_default_building_height()
'''
        patched, modified = patch_resilient_buildings(dummy_content)
        self.assertTrue(modified)
        self.assertIn("_cleanup_buildings_resilient", patched)
        self.assertNotIn("$(which mapshaper)", patched)

        # Idempotencia
        patched2, modified2 = patch_resilient_buildings(patched)
        self.assertFalse(modified2)
        self.assertEqual(patched, patched2)


if __name__ == "__main__":
    unittest.main()
