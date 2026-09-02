import os
import tempfile
import unittest
import yaml
from sb_mexico.toponymy import (
    format_clean_place_name,
    generate_osm_patch,
    extract_settlement_suggestions
)
from tools.poi_studio import load_city_data, save_city_data, save_city_pois


class TestToponymy(unittest.TestCase):

    def test_format_clean_place_name(self):
        # Case 1: Pure number
        name, ptype = format_clean_place_name("94", "REGION")
        self.assertEqual(name, "Región 94")
        self.assertEqual(ptype, "suburb")

        name, ptype = format_clean_place_name("100", "SUPERMANZANA")
        self.assertEqual(name, "Supermanzana 100")
        self.assertEqual(ptype, "suburb")

        # Case 2: Fraccionamiento prefix
        name, ptype = format_clean_place_name("FRACCIONAMIENTO PASEOS DEL MAR", "FRACCIONAMIENTO")
        self.assertEqual(name, "Fracc. Paseos Del Mar")
        self.assertEqual(ptype, "neighbourhood")

        # Case 3: Standard text
        name, ptype = format_clean_place_name("ALFREDO V. BONFIL", "COLONIA")
        self.assertEqual(name, "Alfredo V. Bonfil")
        self.assertEqual(ptype, "suburb")

    def test_generate_osm_patch(self):
        places = [
            {"name": "Supermanzana 94", "loc": [-86.8653, 21.1610], "type": "suburb"},
            {"name": "Fracc. Paseos Del Mar", "loc": [-86.9099, 21.1736], "type": "neighbourhood"}
        ]
        with tempfile.NamedTemporaryFile(suffix=".osm", delete=False) as f:
            temp_path = f.name

        try:
            generate_osm_patch(places, temp_path)
            self.assertTrue(os.path.exists(temp_path))
            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            self.assertIn('<osm version="0.6"', content)
            self.assertIn('node id="-1"', content)
            self.assertIn('lat="21.161000" lon="-86.865300"', content)
            self.assertIn('k="name" v="Supermanzana 94"', content)
            self.assertIn('k="place" v="suburb"', content)
            self.assertIn('k="name" v="Fracc. Paseos Del Mar"', content)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def test_poi_studio_places_persistence(self):
        sample_yaml = """
city:
  name: "Ciudad Prueba"
  code: "PRB"
pois:
  - id: "AIR_Prueba"
    loc: [-86.874, 21.036]
    jobs: 10000
    radius_m: 2000
    mode: "MAX"
"""
        temp_path = os.path.join(os.path.dirname(__file__), "tmp_test_places.yaml")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(sample_yaml)

        try:
            # 1. Load city data (should have empty places list by default)
            data = load_city_data(temp_path)
            self.assertEqual(len(data["pois"]), 1)
            self.assertEqual(len(data["places"]), 0)

            # 2. Save both POIs and Places
            new_pois = [
                {"id": "AIR_Prueba", "loc": [-86.874, 21.036], "jobs": 12000, "radius_m": 2500, "mode": "MAX"}
            ]
            new_places = [
                {"name": "Supermanzana 94", "loc": [-86.8653, 21.1610], "type": "suburb"},
                {"name": "Supermanzana 100", "loc": [-86.8723, 21.1566], "type": "suburb"}
            ]
            save_city_data(temp_path, new_pois=new_pois, new_places=new_places)

            # 3. Reload and verify
            reloaded = load_city_data(temp_path)
            self.assertEqual(len(reloaded["pois"]), 1)
            self.assertEqual(reloaded["pois"][0]["jobs"], 12000)
            self.assertEqual(len(reloaded["places"]), 2)
            self.assertEqual(reloaded["places"][0]["name"], "Supermanzana 94")
            self.assertEqual(reloaded["places"][1]["name"], "Supermanzana 100")

            # 4. Test backwards compatibility wrapper
            save_city_pois(temp_path, new_pois=new_pois)
            reloaded2 = load_city_data(temp_path)
            self.assertEqual(len(reloaded2["pois"]), 1)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


if __name__ == "__main__":
    unittest.main()
