"""
tests.test_wizard_persistence
=============================
Verifica la persistencia exhaustiva de parametros de ciudad y macroeconomia
en el Wizard, garantizando cero perdida de datos entre guardado y recarga.
"""

import os
import unittest
from tools.wizard import save_full_city_data, load_city_data, create_new_project


class TestWizardPersistence(unittest.TestCase):
    def test_full_parameter_roundtrip(self):
        """Verifica que todos los parametros nuevos y avanzados se serialicen y recuperen fielmente."""
        tmp_yaml = os.path.join(os.path.dirname(__file__), "tmp_persist_test.yaml")
        try:
            sample_data = {
                "city": {
                    "code": "PRS",
                    "name": "Persist City",
                    "description": "Ciudad de prueba de persistencia",
                    "creator": "Tester",
                    "bbox": [-87.10, 21.05, -86.75, 21.35],
                    "grid_size": 0.0022,
                    "initial_zoom": 12.5,
                    "include_ocean": True,
                    "min_residents": 15,
                    "min_jobs": 7,
                    "building_filter_size": 22.5,
                    "building_simplification": 0.35,
                    "seed": 999
                },
                "data_dir": "data/prs_test",
                "data_exclusions": ["archivo_desvinculado.csv"],
                "macroeconomics": {
                    "tasa_pea": 0.645,
                    "til_1_state": 0.485,
                    "gravity_beta": 0.135,
                    "max_distance_km": 65.0,
                    "min_pop_size": 35,
                    "target_pop_size": 160,
                    "max_pop_size": 220,
                    "sample_threshold": 750,
                    "default_growth_factor": 1.08,
                    "furness_iterations": 25,
                    "furness_tol": 0.015,
                    "modal_experiment": {
                        "enabled": True,
                        "preset": "custom",
                        "traffic_speed_kmh": 28.5,
                        "motorization_rate": 0.52
                    }
                },
                "growth_factors": {
                    "23005": 1.12
                },
                "exclusion_zones": [
                    {
                        "id": "excl_test",
                        "name": "Zona Excluida Test",
                        "type": "bbox",
                        "reason": "water_body",
                        "color": "#EF4444",
                        "enabled": True,
                        "bbox": [-86.90, 21.10, -86.85, 21.15]
                    }
                ],
                "affluence_zones": [
                    {
                        "id": "zone_test",
                        "name": "Zona Afluencia Test",
                        "type": "polygon",
                        "archetype": "cbd",
                        "multiplier": 2.5,
                        "reach_bonus": 0.40,
                        "target_mode": "MULTIPLIER",
                        "color": "#8B5CF6",
                        "enabled": True,
                        "coordinates": [
                            [-86.85, 21.15],
                            [-86.80, 21.15],
                            [-86.80, 21.10],
                            [-86.85, 21.10],
                            [-86.85, 21.15]
                        ]
                    }
                ],
                "pois": [
                    {
                        "id": "AIR_Cancun",
                        "name": "Aeropuerto",
                        "type": "airport",
                        "loc": [-86.87, 21.03],
                        "jobs": 30000,
                        "radius_m": 2500,
                        "mode": "MAX"
                    }
                ]
            }

            saved_path = save_full_city_data(tmp_yaml, sample_data)
            reloaded = load_city_data(saved_path)

            c = reloaded["city"]
            self.assertEqual(c["code"], "PRS")
            self.assertEqual(c["min_residents"], 15)
            self.assertEqual(c["min_jobs"], 7)
            self.assertEqual(c["building_filter_size"], 22.5)
            self.assertEqual(c["building_simplification"], 0.35)
            self.assertEqual(c["seed"], 999)

            m = reloaded["macroeconomics"]
            self.assertEqual(m["min_pop_size"], 35)
            self.assertEqual(m["target_pop_size"], 160)
            self.assertEqual(m["max_pop_size"], 220)
            self.assertEqual(m["sample_threshold"], 750)
            self.assertEqual(m["furness_iterations"], 25)
            self.assertEqual(m["furness_tol"], 0.015)

            # Exclusion zones
            self.assertIn("exclusion_zones", reloaded)
            self.assertEqual(len(reloaded["exclusion_zones"]), 1)
            self.assertEqual(reloaded["exclusion_zones"][0]["id"], "excl_test")

            # Data exclusions
            self.assertIn("data_exclusions", reloaded)
            self.assertIn("archivo_desvinculado.csv", reloaded["data_exclusions"])

        finally:
            if os.path.exists(tmp_yaml):
                os.remove(tmp_yaml)

    def test_create_new_project_initializes_cohort_and_exclusion_defaults(self):
        """Verifica que un nuevo proyecto se inicialice con valores canónicos de cohortes y exclusiones vacías."""
        res = create_new_project(
            name="Ciudad Nueva Persist",
            code="CNP",
            creator="CreadorTest"
        )
        self.assertEqual(res["status"], "ok")
        created_path = res["path"]

        try:
            data = load_city_data(created_path)
            m = data.get("macroeconomics", {})
            self.assertEqual(m.get("min_pop_size"), 25)
            self.assertEqual(m.get("target_pop_size"), 150)
            self.assertEqual(m.get("max_pop_size"), 200)

            c = data.get("city", {})
            self.assertEqual(c.get("min_residents"), 10)
            self.assertEqual(c.get("min_jobs"), 3)

            self.assertIn("exclusion_zones", data)
            self.assertEqual(data["exclusion_zones"], [])

        finally:
            if os.path.exists(created_path):
                os.remove(created_path)


if __name__ == "__main__":
    unittest.main()
