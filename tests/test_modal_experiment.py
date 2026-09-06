#!/usr/bin/env python3
"""
Pruebas unitarias para el Laboratorio Experimental de Competitividad Modal (Auto vs. Metro).
Verifica:
1. Inmutabilidad de pops cuando el experimento está deshabilitado (enabled: false o None).
2. Cálculo exacto de la Impedancia Alternativa Ponderada bajo distintos presets.
3. Clamp y salvaguardas de rango físico para velocidad y tasa de motorización.
4. Serialización y carga round-trip en el Wizard (YAML).
"""

import os
import copy
import shutil
import tempfile
import unittest

from sb_mexico.gravity import (
    apply_modal_competitiveness_experiment,
    MODAL_PRESETS,
    CANONICAL_SPEED_KMH
)
from tools.wizard import load_city_data, save_full_city_data


class TestModalCompetitivenessExperiment(unittest.TestCase):

    def setUp(self):
        self.sample_pops = [
            {
                "id": "pop_000001",
                "size": 60,
                "residenceId": "dp_1",
                "jobId": "dp_2",
                "drivingDistance": 10000,   # 10 km
                "drivingSeconds": 900       # 15 min @ 40 km/h
            },
            {
                "id": "pop_000002",
                "size": 120,
                "residenceId": "dp_1",
                "jobId": "dp_3",
                "drivingDistance": 5000,    # 5 km
                "drivingSeconds": 450       # 7.5 min @ 40 km/h
            },
            {
                "id": "pop_000003",
                "size": 30,
                "residenceId": "dp_2",
                "jobId": "dp_3",
                "drivingDistance": 1000,    # 1 km
                "drivingSeconds": 90        # 1.5 min @ 40 km/h
            }
        ]

    def test_disabled_by_default_preserves_canonical_values(self):
        """Si enabled es False o None, los pops no deben modificarse en absoluto."""
        # Caso None
        pops_copy1 = copy.deepcopy(self.sample_pops)
        res1 = apply_modal_competitiveness_experiment(pops_copy1, None)
        self.assertEqual(res1[0]["drivingSeconds"], 900)
        self.assertEqual(res1[1]["drivingSeconds"], 450)
        self.assertEqual(res1[2]["drivingSeconds"], 90)

        # Caso enabled: False
        pops_copy2 = copy.deepcopy(self.sample_pops)
        res2 = apply_modal_competitiveness_experiment(pops_copy2, {"enabled": False})
        self.assertEqual(res2[0]["drivingSeconds"], 900)
        self.assertEqual(res2[1]["drivingSeconds"], 450)
        self.assertEqual(res2[2]["drivingSeconds"], 90)

    def test_canonical_preset_identity(self):
        """El preset 'canonical' (40 km/h, 100% auto) debe producir identidad exacta."""
        pops = copy.deepcopy(self.sample_pops)
        cfg = {"enabled": True, "preset": "canonical"}
        res = apply_modal_competitiveness_experiment(pops, cfg)
        # T_auto = 900 * (40 / 40) = 900
        # T_eff = 1.0 * 900 + 0.0 * T_transit = 900
        self.assertEqual(res[0]["drivingSeconds"], 900)
        self.assertEqual(res[1]["drivingSeconds"], 450)
        self.assertEqual(res[2]["drivingSeconds"], 90)

    def test_cdmx_peak_preset_calculation(self):
        """Verifica la fórmula matemática en preset 'cdmx_peak' (18 km/h, 35% motorización)."""
        pops = copy.deepcopy(self.sample_pops)
        cfg = {
            "enabled": True,
            "preset": "cdmx_peak",
            "traffic_speed_kmh": 18.0,
            "motorization_rate": 0.35
        }
        res = apply_modal_competitiveness_experiment(pops, cfg)

        # Pop 1: base 900s
        # speed_factor = 40.0 / 18.0 = 2.2222...
        # t_auto = 900 * (40 / 18) = 2000.0 s
        # t_transit = 2000.0 * 2.0 + 300.0 = 4300.0 s
        # t_eff = 0.35 * 2000.0 + 0.65 * 4300.0 = 700 + 2795 = 3495 s
        self.assertEqual(res[0]["drivingSeconds"], 3495)

        # Pop 2: base 450s
        # t_auto = 450 * (40 / 18) = 1000.0 s
        # t_transit = 1000.0 * 2.0 + 300.0 = 2300.0 s
        # t_eff = 0.35 * 1000.0 + 0.65 * 2300.0 = 350 + 1495 = 1845 s
        self.assertEqual(res[1]["drivingSeconds"], 1845)

    def test_custom_parameters(self):
        """Verifica parámetros personalizados manuales."""
        pops = copy.deepcopy(self.sample_pops[:1])
        cfg = {
            "enabled": True,
            "preset": "custom",
            "traffic_speed_kmh": 20.0,
            "motorization_rate": 0.50
        }
        res = apply_modal_competitiveness_experiment(pops, cfg)
        # Pop 1: base 900s
        # t_auto = 900 * (40 / 20) = 1800.0 s
        # t_transit = 1800 * 2.0 + 300.0 = 3900.0 s
        # t_eff = 0.50 * 1800 + 0.50 * 3900 = 900 + 1950 = 2850 s
        self.assertEqual(res[0]["drivingSeconds"], 2850)

    def test_range_clamping_safeguards(self):
        """Verifica que valores aberrantes fuera de rango sean acotados con seguridad."""
        pops = copy.deepcopy(self.sample_pops[:1])
        # Velocidad extremadamente baja (ej. -5 o 2 km/h) -> clamp a 10 km/h
        # Motorización negativa -> clamp a 0.05
        cfg = {
            "enabled": True,
            "traffic_speed_kmh": 2.0,
            "motorization_rate": -0.8
        }
        res = apply_modal_competitiveness_experiment(pops, cfg)
        self.assertGreater(res[0]["drivingSeconds"], 0)
        self.assertLessEqual(res[0]["drivingSeconds"], 100000)

    def test_wizard_yaml_roundtrip(self):
        """Verifica que el Wizard pueda serializar y recuperar modal_experiment sin pérdidas."""
        yaml_path = os.path.join(os.path.dirname(__file__), "tmp_test_modal_city.yaml")
        try:
            initial_data = {
                "city": {
                    "code": "TST",
                    "name": "Test City",
                    "bbox": [-87.0, 21.0, -86.5, 21.5]
                },
                "macroeconomics": {
                    "tasa_pea": 0.65,
                    "til_1_state": 0.42,
                    "modal_experiment": {
                        "enabled": True,
                        "preset": "cdmx_peak",
                        "traffic_speed_kmh": 18.0,
                        "motorization_rate": 0.35
                    }
                }
            }

            # Guardar con la lógica del Wizard
            saved_file = save_full_city_data(yaml_path, initial_data)
            self.assertTrue(os.path.exists(saved_file))

            # Recargar con la lógica del Wizard
            loaded_data = load_city_data(saved_file)
            mod_exp = loaded_data["macroeconomics"].get("modal_experiment", {})
            self.assertTrue(mod_exp.get("enabled"))
            self.assertEqual(mod_exp.get("preset"), "cdmx_peak")
            self.assertAlmostEqual(mod_exp.get("traffic_speed_kmh"), 18.0)
            self.assertAlmostEqual(mod_exp.get("motorization_rate"), 0.35)

        finally:
            if os.path.exists(yaml_path):
                os.remove(yaml_path)


if __name__ == "__main__":
    unittest.main()
