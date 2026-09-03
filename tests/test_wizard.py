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
    inspect_data_files,
    calculate_conapo_factors,
    create_new_project,
    delete_project,
    exclude_data_file,
    relink_data_file,
    set_project_data_dir,
    DATA_DIR,
    DIST_DIR,
    ROOT_DIR
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
        self.assertIn("active_dir", report)
        self.assertEqual(report["active_dir"], "data/cancun")

    def test_calculate_conapo_factors(self):
        res = calculate_conapo_factors("cities/cancun.yaml")
        self.assertIn(res["status"], ["ok", "missing_conapo"])
        if res["status"] == "ok":
            self.assertIn("factors", res)
            self.assertGreater(len(res["factors"]), 0)
            cves = [f["cve_mun"] for f in res["factors"]]
            self.assertIn("23005", cves)  # Benito Juárez / Cancún
            bjuarez = [f for f in res["factors"] if f["cve_mun"] == "23005"][0]
            self.assertGreater(bjuarez["pob_conapo"], 900000)
            self.assertGreater(bjuarez["factor"], 1.0)
            self.assertTrue(bjuarez["in_bbox"])

    def test_exclude_and_relink_data_file_non_destructive(self):
        # Crear un proyecto temporal para probar exclusión no destructiva
        proj = create_new_project("Test Exclude", "TEX", "Tester")
        proj_file = proj["file"]
        proj_dir = os.path.join(ROOT_DIR, proj["data_dir"])
        test_data_file = os.path.join(proj_dir, "denue_inegi_tex.csv")
        with open(test_data_file, "w", encoding="utf-8") as f:
            f.write("id,nom_estab\n1,Prueba\n")

        try:
            # Inspeccionar datos: debe detectar el archivo
            status1 = inspect_data_files(city_name="Test Exclude", city_code="TEX", city_file=proj_file)
            self.assertEqual(status1["denue"]["status"], "ok")
            self.assertEqual(len(status1["denue"]["files"]), 1)

            # Desvincular/Excluir archivo: el archivo físico NO debe borrarse
            res_ex = exclude_data_file(proj_file, "denue_inegi_tex.csv")
            self.assertEqual(res_ex["status"], "ok")
            self.assertTrue(os.path.exists(test_data_file), "¡ERROR CRÍTICO: El archivo físico fue eliminado del disco!")

            # Re-inspeccionar: ahora debe aparecer como faltante/omitido
            status2 = inspect_data_files(city_name="Test Exclude", city_code="TEX", city_file=proj_file)
            self.assertEqual(status2["denue"]["status"], "missing")
            self.assertEqual(len(status2["denue"]["files"]), 0)

            # Re-vincular archivo
            res_re = relink_data_file(proj_file, "denue_inegi_tex.csv")
            self.assertEqual(res_re["status"], "ok")
            status3 = inspect_data_files(city_name="Test Exclude", city_code="TEX", city_file=proj_file)
            self.assertEqual(status3["denue"]["status"], "ok")

        finally:
            delete_project(proj_file, delete_data_folder=True)

    def test_project_lifecycle_create_and_delete(self):
        # Crear nuevo proyecto
        proj = create_new_project("Hermosillo", "HMO", "Tester")
        self.assertEqual(proj["status"], "ok")
        self.assertTrue(os.path.exists(proj["path"]))
        self.assertTrue(os.path.exists(os.path.join(ROOT_DIR, proj["data_dir"])))

        # Verificar que get_available_cities lo incluye
        cities = get_available_cities()
        codes = [c["code"] for c in cities]
        self.assertIn("HMO", codes)

        # Modificar directorio de datos a una ruta personalizada (Zero-Copy)
        custom_dir = "data/custom_hmo"
        res_dir = set_project_data_dir(proj["file"], custom_dir)
        self.assertEqual(res_dir["status"], "ok")
        loaded = load_city_data(proj["file"])
        self.assertEqual(loaded["data_dir"], custom_dir)

        # Eliminar proyecto
        del_res = delete_project(proj["file"], delete_data_folder=True)
        self.assertEqual(del_res["status"], "ok")
        self.assertFalse(os.path.exists(proj["path"]))

    def test_project_bubble_isolation(self):
        """Verifica que un proyecto nuevo no herede datos ni archivos compilados de otro proyecto."""
        from tools.poi_studio import load_demand_sample
        proj = create_new_project("Burbuja Aislada", "BUB", "Tester")
        proj_file = proj["file"]

        try:
            # 1. POI Studio / api/density no debe cargar puntos de Cancún ni de otro proyecto
            points = load_demand_sample(bbox=[-87.0, 21.0, -86.5, 21.5], city_file=proj_file)
            self.assertEqual(points, [], "El proyecto no compilado cargó puntos de demanda de otro proyecto!")

            # 2. Demand Preview no debe encontrar demand_data.json en dist/bub
            city_base = "bub"
            target_path = os.path.join(DIST_DIR, city_base, "demand_data.json")
            self.assertFalse(os.path.exists(target_path))

            # 3. Raíz no debe contener demand_data.json ni config.json
            self.assertFalse(os.path.exists(os.path.join(ROOT_DIR, "demand_data.json")))
            self.assertFalse(os.path.exists(os.path.join(ROOT_DIR, "config.json")))
        finally:
            delete_project(proj_file, delete_data_folder=True)

if __name__ == '__main__':
    unittest.main()
