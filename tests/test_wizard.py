"""
Pruebas unitarias para tools/wizard.py
======================================
Verifica el escaneo de ciudades, la carga y validación de YAML, la inspección
de archivos multi-fuente y la prevención de vulnerabilidades Path Traversal.
"""

import os
import unittest
import yaml
from tools.wizard import (

get_available_cities,
    load_city_data,
    save_full_city_data,
    inspect_data_files
)

class TestWizard(unittest.TestCase):
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
        self.assertEqual(data["city"]["code"], "CUN")
        self.assertEqual(data["city"]["name"], "Cancún y Riviera Norte")

    def test_save_and_reload_full_city_data(self):
        data = load_city_data("cities/cancun.yaml")
        tmp_path = os.path.join(os.path.dirname(__file__), "tmp_test_wizard_city.yaml")

        saved_path = None
        try:
            data["city"]["name"] = "Cancun Modificado"
            data["pois"].append({
                "id": "UNI_Test_Campus",
                "loc": [-86.85, 21.15],
                "jobs": 12000,
                "radius_m": 1200,
                "mode": "MAX"
            })
            saved_path = save_full_city_data(tmp_path, data)

            reloaded = load_city_data(saved_path)
            self.assertEqual(reloaded["city"]["name"], "Cancun Modificado")
            poi_ids = [p["id"] for p in reloaded["pois"]]
            self.assertIn("UNI_Test_Campus", poi_ids)
        finally:
            if saved_path and os.path.exists(saved_path):
                os.remove(saved_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_inspect_data_files_cancun(self):
        report = inspect_data_files(city_name="Cancun", city_code="CUN", city_file="cities/cancun.yaml")
        self.assertIn("denue", report)
        self.assertIn("cpv", report)
        self.assertIn("all_ready", report)
        self.assertIn("conapo", report)

    def test_path_traversal_security(self):
        with self.assertRaises(PermissionError):
            load_city_data("../../../etc/shadow.yaml")
        with self.assertRaises(PermissionError):
            load_city_data("cities/../../../windows/system32/cmd.exe")

if __name__ == '__main__':
    unittest.main()
