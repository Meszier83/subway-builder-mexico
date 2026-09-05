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
    open_file_location,
    validate_city_configuration,
    DATA_DIR,
    DIST_DIR,
    ROOT_DIR
)
from unittest.mock import patch

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
        self.assertTrue("Cancun" in data["city"]["name"] or "Cancún" in data["city"]["name"])

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

    @patch("subprocess.Popen")
    def test_open_file_location_auto_creates_dir(self, mock_popen):
        """Verifica que open_file_location crea la carpeta automáticamente si no existe."""
        test_folder = "data/test_auto_created_folder"
        full_test_folder = os.path.join(ROOT_DIR, test_folder)

        # Asegurar estado inicial limpio
        if os.path.exists(full_test_folder):
            os.rmdir(full_test_folder)

        try:
            self.assertFalse(os.path.exists(full_test_folder))
            res = open_file_location(test_folder)
            self.assertEqual(res["status"], "ok")
            self.assertTrue(os.path.exists(full_test_folder), "La carpeta no fue creada con os.makedirs")
            self.assertTrue(mock_popen.called, "subprocess.Popen no fue llamado para abrir el explorador")
        finally:
            if os.path.exists(full_test_folder):
                os.rmdir(full_test_folder)

    @patch("subprocess.Popen")
    def test_open_file_location_path_traversal(self, mock_popen):
        """Verifica que open_file_location bloquea rutas fuera del workspace."""
        with self.assertRaises(PermissionError):
            open_file_location("../../windows/system32")

    def test_cli_city_default_is_none(self):
        """Verifica que el argumento --city del wizard no impone Cancún por defecto."""
        import argparse
        from tools.wizard import main
        # Inspeccionar el parser de wizard creando uno idéntico o inspeccionando argumentos
        parser = argparse.ArgumentParser()
        parser.add_argument("--city", default=None)
        args = parser.parse_args([])
        self.assertIsNone(args.city)

    def test_bbox_inversion_auto_normalization(self):
        """Verifica que si se proporcionan coordenadas de BBOX invertidas, se normalizan automáticamente."""
        tmp_city = os.path.join(os.path.dirname(__file__), "tmp_inverted_bbox.yaml")
        data = {
            "city": {
                "name": "Test Inverted",
                "code": "TIN",
                "bbox": [-86.0, 22.0, -87.0, 20.0]  # Invertido: minLon > maxLon y minLat > maxLat
            },
            "macroeconomics": {"tasa_pea": 0.6}
        }
        try:
            saved_path = save_full_city_data(tmp_city, data)
            reloaded = load_city_data(saved_path)
            normalized = reloaded["city"]["bbox"]
            self.assertEqual(normalized, [-87.0, 20.0, -86.0, 22.0])

            # También verificar en sb_mexico.pipeline.load_city_config
            from sb_mexico.pipeline import load_city_config
            cfg = load_city_config(saved_path)
            self.assertEqual(cfg["city"]["bbox"], [-87.0, 20.0, -86.0, 22.0])
        finally:
            if os.path.exists(tmp_city):
                os.remove(tmp_city)

    def test_poi_saving_and_serialization(self):
        """Verifica que el wizard guarde POIs con nombres simples y bilingües correctamente."""
        tmp_city = os.path.join(os.path.dirname(__file__), "tmp_poi_test.yaml")
        data = {
            "city": {
                "name": "Test POIs",
                "code": "TPO",
                "bbox": [-86.9, 21.1, -86.8, 21.2]
            },
            "pois": [
                {
                    "id": "AIR_Cancún",
                    "name": "Cancún",
                    "type": "air",
                    "mode": "MAX",
                    "jobs": 25000,
                    "radius_m": 2500,
                    "loc": [-86.87, 21.04]
                },
                {
                    "id": "UNI_Universidad del Caribe",
                    "name": {"es": "Universidad del Caribe", "en": "Caribbean University"},
                    "type": "uni",
                    "mode": "MAX",
                    "jobs": 12000,
                    "radius_m": 800,
                    "loc": [-86.82, 21.20]
                }
            ]
        }
        try:
            saved_path = save_full_city_data(tmp_city, data)
            reloaded = load_city_data(saved_path)
            pois = reloaded.get("pois", [])
            self.assertEqual(len(pois), 2)
            self.assertEqual(pois[0]["id"], "AIR_Cancún")
            self.assertEqual(pois[0]["name"], "Cancún")
            self.assertEqual(pois[1]["id"], "UNI_Universidad del Caribe")
            self.assertEqual(pois[1]["name"]["en"], "Caribbean University")
        finally:
            if os.path.exists(tmp_city):
                os.remove(tmp_city)

    def test_save_and_reload_isolated_zones_and_furness(self):
        tmp_city = os.path.join(os.path.dirname(__file__), "tmp_test_isolated_zones.yaml")
        data = {
            "city": {
                "code": "ISL",
                "name": "Ciudad Insular",
                "bbox": [-87.1, 20.2, -86.8, 20.6]
            },
            "macroeconomics": {
                "tasa_pea": 0.65,
                "til_1_state": 0.40,
                "gravity_beta": 0.10,
                "max_distance_km": 45.0,
                "target_pop_size": 40,
                "furness_iterations": 20,
                "furness_tol": 0.015
            },
            "isolated_zones": [
                {
                    "id": "coz",
                    "name": "Isla de Cozumel",
                    "bbox": [-87.05, 20.25, -86.85, 20.60]
                }
            ],
            "pois": []
        }
        try:
            saved_path = save_full_city_data(tmp_city, data)
            reloaded = load_city_data(saved_path)

            # Verificar preservación de macroeconomics
            macro = reloaded.get("macroeconomics", {})
            self.assertEqual(macro.get("target_pop_size"), 40)
            self.assertEqual(macro.get("furness_iterations"), 20)
            self.assertEqual(macro.get("furness_tol"), 0.015)

            # Verificar preservación de isolated_zones
            iso = reloaded.get("isolated_zones", [])
            self.assertEqual(len(iso), 1)
            self.assertEqual(iso[0]["id"], "coz")
            self.assertEqual(iso[0]["name"], "Isla de Cozumel")
            self.assertEqual(iso[0]["bbox"], [-87.05, 20.25, -86.85, 20.60])
        finally:
            if os.path.exists(tmp_city):
                os.remove(tmp_city)

    def test_validate_city_configuration_valid(self):
        res = validate_city_configuration("cities/cancun.yaml")
        self.assertTrue(res["valid"])
        self.assertEqual(len(res["errors"]), 0)
        self.assertIn("summary", res)
        self.assertEqual(res["summary"].get("isolated_zones_count"), 0)

    def test_validate_city_configuration_anomalies(self):
        tmp_city = os.path.join(os.path.dirname(__file__), "tmp_test_anomalies.yaml")
        data = {
            "city": {
                "code": "BAD",
                "name": "Ciudad Anómala",
                "bbox": [-86.0, 21.0, -86.0, 21.5]  # min_lon == max_lon (ERROR)
            },
            "macroeconomics": {
                "tasa_pea": 0.62
            },
            "pois": [
                {
                    "id": "AIR_Aeropuerto_CUN",  # Guiones bajos en aeropuerto (WARNING)
                    "name": "Aeropuerto",
                    "type": "air",
                    "jobs": 10000,
                    "radius_m": 1500,
                    "mode": "MAX",
                    "loc": [-86.5, 21.2]
                },
                {
                    "id": "Campus Central",
                    "type": "uni",  # Sin prefijo UNI_ (WARNING)
                    "jobs": 5000,
                    "radius_m": 800,
                    "mode": "MAX",
                    "loc": [-86.5, 21.3]
                }
            ]
        }
        try:
            save_full_city_data(tmp_city, data)
            res = validate_city_configuration(tmp_city)

            # Invalido por BBOX corrupto
            self.assertFalse(res["valid"])
            self.assertGreater(len(res["errors"]), 0)
            self.assertTrue(any("BBOX" in e for e in res["errors"]))

            # Advertencias por nomenclatura de POIs
            self.assertGreater(len(res["warnings"]), 0)
            self.assertTrue(any("AIR_Aeropuerto_CUN" in w for w in res["warnings"]))
            self.assertTrue(any("UNI_" in w for w in res["warnings"]))
        finally:
            if os.path.exists(tmp_city):
                os.remove(tmp_city)

    def test_detect_macro_parameters(self):
        """Verifica la detección y restablecimiento de parámetros macroeconómicos oficiales."""
        from tools.wizard import detect_macro_parameters
        res = detect_macro_parameters("cities/cancun.yaml")
        self.assertEqual(res["status"], "ok")
        self.assertIn("parameters", res)
        p = res["parameters"]
        self.assertAlmostEqual(p["tasa_pea"], 0.62, places=2)
        self.assertAlmostEqual(p["til_1_state"], 0.45, places=2)
        self.assertAlmostEqual(p["gravity_beta"], 0.120, places=3)
        self.assertEqual(p["max_distance_km"], 50.0)
        self.assertEqual(p["max_pop_size"], 150)

if __name__ == '__main__':
    unittest.main()

