"""
Pruebas unitarias para tools/poi_studio.py
==========================================
Verifica el escaneo de ciudades, la carga y validación de YAML y la serialización de POIs.
"""

import os
import tempfile
import unittest
import yaml
from tools.poi_studio import get_available_cities, load_city_data, save_city_pois

class TestPoiStudio(unittest.TestCase):
    def test_get_available_cities(self):
        cities = get_available_cities()
        self.assertIsInstance(cities, list)
        self.assertGreater(len(cities), 0)
        codes = [c["code"] for c in cities]
        self.assertIn("CUN", codes)

    def test_load_city_data(self):
        data = load_city_data("cities/cancun.yaml")
        self.assertIn("city", data)
        self.assertIn("macroeconomics", data)
        self.assertIn("pois", data)
        self.assertGreaterEqual(len(data["pois"]), 5)

    def test_save_and_reload_pois(self):
        data = load_city_data("cities/cancun.yaml")
        tmp_path = os.path.join(os.path.dirname(__file__), "tmp_test_city.yaml")
        
        with open(tmp_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f)

        try:
            new_test_pois = [
                {
                    "id": "AIR_Test_Airport",
                    "loc": [-86.874, 21.036],
                    "jobs": 35000,
                    "radius_m": 2500,
                    "mode": "MAX"
                },
                {
                    "id": "MED_Test_Hospital",
                    "loc": [-86.820, 21.160],
                    "jobs": 6000,
                    "radius_m": 700,
                    "mode": "BOOST"
                }
            ]

            save_city_pois(tmp_path, new_test_pois)

            # Recargar y verificar
            reloaded = load_city_data(tmp_path)
            self.assertEqual(len(reloaded["pois"]), 2)
            self.assertEqual(reloaded["pois"][0]["id"], "AIR_Test_Airport")
            self.assertEqual(reloaded["pois"][0]["jobs"], 35000)
            self.assertEqual(reloaded["pois"][1]["id"], "MED_Test_Hospital")
            self.assertEqual(reloaded["city"]["code"], "CUN")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_path_traversal_security(self):
        with self.assertRaises(PermissionError):
            load_city_data("../../../etc/shadow.yaml")
        with self.assertRaises(PermissionError):
            load_city_data("cities/../../../windows/system32/cmd.exe")

if __name__ == "__main__":
    unittest.main()

