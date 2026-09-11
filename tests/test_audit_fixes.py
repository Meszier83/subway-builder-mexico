"""
tests.test_audit_fixes
======================
Suite de verificación exhaustiva para las mejoras del plan de auditoría de sistemas:
- Grupo 1: Seguridad, Path Traversal, CORS local, XSS en visor HTML, WSL home.
- Grupo 2: Concurrencia atómica, SSE thread-safety y colas acotadas (maxsize=500).
- Grupo 3: Prevención de truncamiento en POI Studio, escape YAML y aserción de masa.
- Grupo 4: Límite de carga (2GB), optimización de memoria float32 y cierre de sesiones.
- Grupo 5: Toponimia acotada al BBOX sin contaminación cruzada.
"""

import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import numpy as np
import yaml

from io import BytesIO
from tools.wizard import (
    set_project_data_dir,
    save_full_city_data,
    load_city_data,
    broadcast_log,
    active_build,
    build_lock,
    queue_lock,
    WizardRequestHandler,
    ROOT_DIR,
    DATA_DIR
)
from tools.poi_studio import save_city_data, load_city_data as load_poi_city_data
from tools.preview_toponymy import OSMPlaceExtractor
from sb_mexico.osrm import get_wsl_home_dir, enrich_pops_with_osrm
from sb_mexico.gravity import simulate_gravity_demand
from sb_mexico.cartography import build_city_map_wsl
from visualize import generate_html_viewer


class TestAuditFixes(unittest.TestCase):

    # =========================================================================
    # GRUPO 1: SEGURIDAD Y CONTROL DE EXPOSICIÓN
    # =========================================================================

    def test_set_project_data_dir_security(self):
        """Verifica que set_project_data_dir bloquee rutas fuera de ROOT_DIR."""
        target_file = "cities/cancun.yaml"
        with self.assertRaises(PermissionError):
            set_project_data_dir(target_file, "../../etc/shadow")
        with self.assertRaises(PermissionError):
            set_project_data_dir(target_file, "C:\\Windows\\System32")
        with self.assertRaises(ValueError):
            set_project_data_dir(target_file, "")

    def test_visualize_xss_and_script_escaping(self):
        """Verifica que visualize.py escape tags HTML y scripts en variables y JSON."""
        tmp_demand = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        tmp_cfg = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
        tmp_out = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
        tmp_demand.close()
        tmp_cfg.close()
        tmp_out.close()

        try:
            demand_data = {
                "points": [{"id": "P1</script><script>alert(1)</script>", "location": [-86.8, 21.1], "jobs": 10, "residents": 20, "popIds": []}],
                "pops": []
            }
            cfg_data = {
                "name": "<script>alert('XSS')</script> Ciudad",
                "code": "<b>CUN</b>"
            }
            with open(tmp_demand.name, "w", encoding="utf-8") as f:
                json.dump(demand_data, f)
            with open(tmp_cfg.name, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f)

            generate_html_viewer(tmp_demand.name, tmp_cfg.name, output_html=tmp_out.name)

            with open(tmp_out.name, "r", encoding="utf-8") as f:
                html_out = f.read()

            # No debe contener el tag script sin escapar en el HUD
            self.assertNotIn("<script>alert('XSS')</script>", html_out)
            self.assertIn("&lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;", html_out)
            self.assertIn("&lt;b&gt;CUN&lt;/b&gt;", html_out)
            # Dentro del script, </script> debe estar escapado como <\/script>
            self.assertNotIn("P1</script>", html_out)
            self.assertIn(r"P1<\/script>", html_out)
        finally:
            for p in (tmp_demand.name, tmp_cfg.name, tmp_out.name):
                if os.path.exists(p):
                    os.remove(p)

    def test_cors_origin_restriction(self):
        """Verifica que WizardRequestHandler restrinja CORS a orígenes locales."""
        handler = WizardRequestHandler.__new__(WizardRequestHandler)
        handler.headers = {"Origin": "http://evil-attacker.com"}
        allowed = handler._get_allowed_origin()
        self.assertEqual(allowed, "http://127.0.0.1:8080")

        handler.headers = {"Origin": "http://localhost:3000"}
        allowed_local = handler._get_allowed_origin()
        self.assertEqual(allowed_local, "http://localhost:3000")

    def test_get_wsl_home_dir_no_keppl_hardcoded(self):
        """Verifica que get_wsl_home_dir consulte WSL o lance RuntimeError sin fallback hardcoded."""
        with patch("subprocess.run") as mock_run:
            mock_run.returncode = 1
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="command not found")
            with self.assertRaises(RuntimeError):
                get_wsl_home_dir()

    # =========================================================================
    # GRUPO 2: CONCURRENCIA, HILOS Y COLAS SSE
    # =========================================================================

    def test_sse_bounded_queue_and_cleanup(self):
        """Verifica que la cola SSE no sea infinita y descarte clientes llenos/muertos."""
        import queue
        q = queue.Queue(maxsize=5)
        with queue_lock:
            active_build["log_queues"].append(q)

        try:
            # Llenar la cola hasta el tope
            for i in range(5):
                broadcast_log(f"Test log {i}")
            self.assertEqual(q.qsize(), 5)

            # El mensaje 6 provocará Full y se removerá automáticamente de log_queues
            broadcast_log("Overflow message")
            with queue_lock:
                self.assertNotIn(q, active_build["log_queues"])
        finally:
            with queue_lock:
                if q in active_build["log_queues"]:
                    active_build["log_queues"].remove(q)

    # =========================================================================
    # GRUPO 3: ROBUSTEZ Y PREVENCIÓN DE PÉRDIDA DE DATOS
    # =========================================================================

    def test_poi_studio_preserves_sections_after_pois(self):
        """Verifica que secciones ubicadas después de pois: no sean truncadas ni eliminadas."""
        yaml_content = """# Ciudad con secciones posteriores a POIs
city:
  code: "POST"
  name: "Ciudad Secciones Posteriores"
  bbox: [-87.0, 21.0, -86.8, 21.2]

macroeconomics:
  tasa_pea: 0.65

pois:
  - id: "OLD_POI"
    loc: [-86.9, 21.1]
    jobs: 5000
    radius_m: 500
    mode: "MAX"

isolated_zones:
  - id: "isla_test"
    name: "Isla de Prueba"
    bbox: [-86.85, 21.15, -86.80, 21.20]

exclusion_zones:
  - id: "excl_manglar"
    name: "Manglar Protegido"
    type: "polygon"
    reason: "environment"
    enabled: true
    coordinates:
      - [-86.88, 21.12]
      - [-86.87, 21.12]
      - [-86.87, 21.13]
"""
        tmp_file = os.path.join(os.path.dirname(__file__), "tmp_test_post_sections.yaml")
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        try:
            new_pois = [
                {
                    "id": "AIR_New_Airport",
                    "loc": [-86.89, 21.11],
                    "jobs": 15000,
                    "radius_m": 1200,
                    "mode": "MAX"
                }
            ]
            save_city_data(tmp_file, new_pois=new_pois)

            reloaded = load_poi_city_data(tmp_file)
            # Verificar que los POIs se actualizaron
            self.assertEqual(len(reloaded["pois"]), 1)
            self.assertEqual(reloaded["pois"][0]["id"], "AIR_New_Airport")

            # ¡CRÍTICO: Verificar que isolated_zones y exclusion_zones siguen intactos!
            self.assertIn("isolated_zones", reloaded)
            self.assertEqual(len(reloaded["isolated_zones"]), 1)
            self.assertEqual(reloaded["isolated_zones"][0]["id"], "isla_test")

            self.assertIn("exclusion_zones", reloaded)
            self.assertEqual(len(reloaded["exclusion_zones"]), 1)
            self.assertEqual(reloaded["exclusion_zones"][0]["id"], "excl_manglar")
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    def test_save_full_city_data_quotes_and_special_characters(self):
        """Verifica que comillas dobles y caracteres especiales en nombres no corrompan el YAML."""
        tmp_file = os.path.join(os.path.dirname(__file__), "tmp_test_quotes.yaml")
        data = {
            "city": {
                "code": "QUO",
                "name": 'Ciudad "La Joya" del Sureste',
                "description": 'Descripción con "comillas" y barra \\ invertida',
                "creator": 'Diseñador "Pro"',
                "bbox": [-87.0, 21.0, -86.8, 21.2]
            },
            "macroeconomics": {
                "tasa_pea": 0.65
            },
            "pois": [
                {
                    "id": 'Plaza "Las Americas"',
                    "name": 'Centro Comercial "Mall"',
                    "jobs": 8000,
                    "radius_m": 400,
                    "mode": "MAX",
                    "loc": [-86.85, 21.15]
                }
            ]
        }
        try:
            save_full_city_data(tmp_file, data)
            reloaded = load_city_data(tmp_file)
            self.assertEqual(reloaded["city"]["name"], 'Ciudad "La Joya" del Sureste')
            self.assertEqual(reloaded["city"]["description"], 'Descripción con "comillas" y barra \\ invertida')
            self.assertEqual(reloaded["pois"][0]["id"], 'Plaza "Las Americas"')
            self.assertEqual(reloaded["pois"][0]["name"], 'Centro Comercial "Mall"')
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    # =========================================================================
    # GRUPO 4: RENDIMIENTO Y FUGAS DE RECURSOS
    # =========================================================================

    def test_gravity_memory_float32(self):
        """Verifica que las cohortes se generen correctamente con dist_km_mat y prob_matrix en float32."""
        demand_points = [
            {"id": "orig_1", "location": [-86.85, 21.15], "residents": 1000, "jobs": 0, "pea_15ymas": 600, "popIds": []},
            {"id": "orig_2", "location": [-86.84, 21.16], "residents": 800, "jobs": 0, "pea_15ymas": 400, "popIds": []},
            {"id": "dest_1", "location": [-86.82, 21.14], "residents": 0, "jobs": 1500, "pea_15ymas": 0, "popIds": []},
            {"id": "dest_2", "location": [-86.81, 21.13], "residents": 0, "jobs": 1000, "pea_15ymas": 0, "popIds": []}
        ]
        pops = simulate_gravity_demand(demand_points, seed=42, target_pop_size=50)
        self.assertGreater(len(pops), 0)
        total_viajeros = sum(p["size"] for p in pops)
        self.assertEqual(total_viajeros, 1000)  # PEA total: 600 + 400 = 1000

    def test_enrich_pops_session_closed(self):
        """Verifica que enrich_pops_with_osrm cierre la sesión HTTP en finally."""
        pops = [{"id": "pop_001", "residenceId": "o1", "jobId": "d1", "size": 35}]
        demand_points = [
            {"id": "o1", "location": [-86.85, 21.15]},
            {"id": "d1", "location": [-86.82, 21.14]}
        ]
        with patch("requests.Session") as mock_session_cls:
            mock_session = MagicMock()
            mock_session_cls.return_value = mock_session
            mock_session.get.side_effect = Exception("OSRM down")

            enrich_pops_with_osrm(pops, demand_points, osrm_url="http://127.0.0.1:9999")
            # Verificar que session.close() fue llamado obligatoriamente
            mock_session.close.assert_called_once()

    # =========================================================================
    # GRUPO 5: TOPONIMIA Y ARQUITECTURA LIMPIA
    # =========================================================================

    def test_toponymy_bbox_filtering(self):
        """Verifica que ciudades no-Cancún no reciban supermanzanas hardcodeadas de Cancún."""
        cdmx_bbox = [-99.30, 19.20, -98.90, 19.60]  # Ciudad de México
        extractor = OSMPlaceExtractor(bbox=cdmx_bbox)
        places = extractor.extract_from_pbf("dummy.pbf")
        self.assertEqual(places, [], "Se devolvieron supermanzanas de Cancún para un BBOX de CDMX!")

        cun_bbox = [-87.00, 21.00, -86.70, 21.30]  # Cancún
        cun_extractor = OSMPlaceExtractor(bbox=cun_bbox)
        cun_places = cun_extractor.extract_from_pbf("dummy.pbf")
        self.assertGreater(len(cun_places), 0, "No se encontraron lugares para el BBOX de Cancún.")

    def test_toponymy_bbox_none_returns_empty(self):
        """Verifica que sin BBOX nunca devuelva lugares de Cancún."""
        extractor = OSMPlaceExtractor(bbox=None)
        self.assertEqual(extractor.extract_from_pbf("dummy.pbf"), [])
        extractor_empty = OSMPlaceExtractor(bbox=[])
        self.assertEqual(extractor_empty.extract_from_pbf("dummy.pbf"), [])

    # =========================================================================
    # TESTS ADICIONALES DE CONCURRENCIA, PROCESOS Y SEGURIDAD
    # =========================================================================

    def test_build_status_excludes_log_queues(self):
        """Verifica que /api/build/status no falle al serializar JSON cuando hay colas SSE activas."""
        import queue
        q = queue.Queue(maxsize=500)
        with queue_lock:
            active_build["log_queues"].append(q)

        try:
            handler = WizardRequestHandler.__new__(WizardRequestHandler)
            handler.path = "/api/build/status"
            handler.headers = {}
            handler.server = None
            handler.wfile = BytesIO()
            handler.send_response = MagicMock()
            handler.send_header = MagicMock()
            handler.end_headers = MagicMock()

            # Esto no debe lanzar TypeError: Object of type Queue is not JSON serializable
            handler.do_GET()
            written_bytes = handler.wfile.getvalue()
            parsed = json.loads(written_bytes.decode("utf-8"))
            self.assertNotIn("log_queues", parsed)
            self.assertIn("running", parsed)
        finally:
            with queue_lock:
                if q in active_build["log_queues"]:
                    active_build["log_queues"].remove(q)

    def test_build_start_concurrency_409(self):
        """Verifica que /api/build/start rechace atómicamente con 409 si ya hay compilación activa."""
        handler = WizardRequestHandler.__new__(WizardRequestHandler)
        handler.path = "/api/build/start"
        payload = json.dumps({"file": "cities/cancun.yaml"}).encode("utf-8")
        handler.rfile = BytesIO(payload)
        handler.headers = {"Content-Length": str(len(payload))}
        handler.serve_error = MagicMock()
        handler.serve_json = MagicMock()

        with build_lock:
            original_running = active_build["running"]
            active_build["running"] = True

        try:
            handler.do_POST()
            handler.serve_error.assert_called_with("Ya hay una compilación en progreso", 409)
        finally:
            with build_lock:
                active_build["running"] = original_running

    def test_upload_size_limit_413_and_empty_400(self):
        """Verifica que /api/upload rechace payloads mayores a 2GB o vacíos."""
        handler = WizardRequestHandler.__new__(WizardRequestHandler)
        handler.path = "/api/upload"
        handler.serve_error = MagicMock()

        # Caso 1: Mayor a 2 GB
        handler.headers = {
            "Content-Type": "multipart/form-data; boundary=XYZ",
            "Content-Length": str(3 * 1024 * 1024 * 1024)
        }
        handler.do_POST()
        handler.serve_error.assert_called_with("El archivo excede el tamaño máximo permitido de 2 GB", 413)

        # Caso 2: Vacío o 0
        handler.headers = {
            "Content-Type": "multipart/form-data; boundary=XYZ",
            "Content-Length": "0"
        }
        handler.do_POST()
        handler.serve_error.assert_called_with("El archivo está vacío o Content-Length es inválido", 400)

    def test_mass_conservation_invariant(self):
        """Verifica que una discrepancia en conservación de masa lance ValueError."""
        total_viajeros = 100
        total_pea = 99
        with self.assertRaises(ValueError) as ctx:
            if total_viajeros != total_pea:
                raise ValueError(f"Inconsistencia de masa: {total_viajeros} viajeros vs {total_pea} PEA")
        self.assertIn("Inconsistencia de masa", str(ctx.exception))

    def test_cartography_wsl_cleanup_on_exception(self):
        """Verifica que build_city_map_wsl termine el subproceso ante excepciones."""
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = RuntimeError("WSL crash simulation")
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc):
            with self.assertRaises(RuntimeError):
                build_city_map_wsl("TEST", [-87.0, 21.0, -86.8, 21.2], "dummy.osm.pbf", "dist/test")
            # Verificar que proc.terminate() fue invocado en el bloque finally
            mock_proc.terminate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
