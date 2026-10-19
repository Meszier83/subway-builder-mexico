"""
tests.test_depot_integration
============================
Pruebas de integracion y validacion de compatibilidad completa con Subway-Builder-Modded/depot:
1. Esquema canonico de config.json (bbox, country, version).
2. Generacion de ficha tecnica description.md para Railyard.
3. Generacion de metadatos .railyard_map/ (special_demand_points.json y special_demand_types.json).
4. Propagacion de parametros de MapGen (foundations, max_tile_size, military_aerodrome, suffix, labels_only).
"""

import os
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock

from sb_mexico.special_demand import (
    write_railyard_metadata_dir,
    generate_railyard_description_md,
    load_special_demand_types
)


class TestDepotIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_special_demand_types_schema_has_clean_defaults(self):
        """Verifica que el catalogo de tipos de demanda especial no contenga residuales en japones en __default__."""
        types_doc = load_special_demand_types()
        self.assertIn("types", types_doc)
        for t in types_doc["types"]:
            lbl = t.get("label", {}).get("__default__", "")
            self.assertTrue(lbl, f"Tipo {t.get('code')} carece de label.__default__")
            self.assertFalse(any(ord(c) > 0x2E80 for c in lbl), f"Tipo {t.get('code')} tiene ideogramas en __default__: {lbl}")
            for sub in (t.get("sub_types") or []):
                sub_lbl = sub.get("label", {}).get("__default__", "")
                self.assertFalse(any(ord(c) > 0x2E80 for c in sub_lbl), f"Subtipo {sub.get('id')} tiene ideogramas en __default__: {sub_lbl}")

    def test_write_railyard_metadata_dir(self):
        """Verifica que write_railyard_metadata_dir cree .railyard_map con ambos esquemas JSON."""
        sample_doc = {
            "version": 1,
            "map_code": "CUN",
            "points": [
                {
                    "point_id": "AIR_Cancun",
                    "type": "airport",
                    "sub_type": "international_terminal",
                    "name": {"__default__": "Cancun Airport"},
                    "pop_ids": ["AIR_Cancun_pop_1"]
                }
            ]
        }
        railyard_dir = write_railyard_metadata_dir(self.out_dir, sample_doc)
        self.assertTrue(os.path.isdir(railyard_dir))
        
        pts_file = os.path.join(railyard_dir, "special_demand_points.json")
        types_file = os.path.join(railyard_dir, "special_demand_types.json")
        self.assertTrue(os.path.exists(pts_file))
        self.assertTrue(os.path.exists(types_file))

        with open(pts_file, "r", encoding="utf-8") as f:
            loaded_pts = json.load(f)
        self.assertEqual(loaded_pts["map_code"], "CUN")

        with open(types_file, "r", encoding="utf-8") as f:
            loaded_types = json.load(f)
        self.assertIn("types", loaded_types)

    def test_generate_railyard_description_md(self):
        """Verifica la generacion completa de description.md con tablas de Cobertura, Poblacion, Estadisticas y POIs."""
        city_info = {
            "name": "Cancun",
            "code": "CUN",
            "version": "7.2.0",
            "description": "Mapa metropolitano de Cancun para Subway Builder.",
            "creator": "Subway Builder Mexico",
            "bbox": [-87.05, 21.0, -86.72, 21.31],
            "use_buildings": True,
            "create_building_foundations": True,
            "include_ocean": True
        }
        demand_payload = {
            "points": [
                {"id": "pt_1", "location": [-86.85, 21.15], "jobs": 120, "residents": 450, "popIds": ["pop_1"]},
                {"id": "pt_2", "location": [-86.87, 21.12], "jobs": 800, "residents": 50, "popIds": ["pop_1", "pop_2"]}
            ],
            "pops": [
                {"id": "pop_1", "residenceId": "pt_1", "jobId": "pt_2", "size": 350, "drivingDistance": 8500.0, "drivingSeconds": 720},
                {"id": "pop_2", "residenceId": "pt_1", "jobId": "AIR_Cancun", "size": 150, "drivingDistance": 16000.0, "drivingSeconds": 1300}
            ]
        }
        sp_doc = {
            "version": 1,
            "map_code": "CUN",
            "points": [
                {
                    "point_id": "AIR_Cancun",
                    "type": "airport",
                    "sub_type": "international_terminal",
                    "name": {"__default__": "Aeropuerto Internacional de Cancun"},
                    "pop_ids": ["pop_2"]
                }
            ]
        }

        md_content = generate_railyard_description_md(
            city_info=city_info,
            demand_payload=demand_payload,
            special_demand_points_doc=sp_doc,
            out_dir=self.out_dir
        )

        out_file = os.path.join(self.out_dir, "description.md")
        self.assertTrue(os.path.exists(out_file))

        # Validaciones de secciones obligatorias de Railyard
        self.assertIn("<h1>Cancun</h1>", md_content)
        self.assertIn("CUN", md_content)
        self.assertIn("7.2.0", md_content)
        self.assertIn("<h2>Coverage</h2>", md_content)
        self.assertIn("Bounding box", md_content)
        self.assertIn("Bounding Box Area", md_content)
        self.assertIn("<h2>Population Summary</h2>", md_content)
        self.assertIn("Total Modeled Demand", md_content)
        self.assertIn("Modeled Normal Demand", md_content)
        self.assertIn("Modeled Special Demand", md_content)
        self.assertIn("<h2>Map Statistics</h2>", md_content)
        self.assertIn("Median Commute Distance", md_content)
        self.assertIn("<h2>Special Demand</h2>", md_content)
        self.assertIn("Aeropuerto Internacional de Cancun", md_content)
        self.assertIn("<h2>Additional Features</h2>", md_content)
        self.assertIn("Building Collision", md_content)
        self.assertIn("underground foundations", md_content)
        self.assertIn("<h2>Methodology</h2>", md_content)
        self.assertIn("<h2>Data Sources</h2>", md_content)
        self.assertIn("INEGI", md_content)
        self.assertIn("CONAPO", md_content)
        self.assertIn("<h2>License</h2>", md_content)
        self.assertIn("GNU General Public License v3.0", md_content)

    @patch("sb_mexico.cartography.subprocess.Popen")
    @patch("sb_mexico.cartography.is_wsl_available")
    def test_build_city_map_wsl_forwards_all_depot_parameters(self, mock_wsl_avail, mock_popen):
        """Verifica que build_city_map pase los parametros create_building_foundations, max_building_tile_size, color_military_like_aerodrome, places_suffix y labels_only al comando WSL."""
        mock_wsl_avail.return_value = (True, "Ubuntu", {})
        proc_mock = MagicMock()
        proc_mock.stdout.readline.side_effect = [""]
        proc_mock.wait.return_value = 0
        mock_popen.return_value = proc_mock

        from sb_mexico.cartography import build_city_map

        sample_pbf = os.path.join(self.out_dir, "sample.osm.pbf")
        with open(sample_pbf, "wb") as f:
            f.write(b"dummy_pbf_content")

        # Simular llamada con opciones no por defecto
        build_city_map(
            city_code="CUN",
            bbox=[-87.0, 21.0, -86.7, 21.3],
            osm_pbf_path=sample_pbf,
            output_dir=self.out_dir,
            create_building_foundations=False,
            max_building_tile_size=450,
            color_military_like_aerodrome=False,
            places_suffix="Municipality",
            labels_only=True
        )

        mock_popen.assert_called_once()
        cmd = mock_popen.call_args[0][0]

        self.assertIn("--no-building-foundations", cmd)
        self.assertIn("--max-building-tile-size", cmd)
        self.assertIn("450", cmd)
        self.assertIn("--no-color-military-like-aerodrome", cmd)
        self.assertIn("--places-suffix", cmd)
        self.assertIn("Municipality", cmd)
        self.assertIn("--labels-only", cmd)

    def test_validate_city_configuration_max_building_tile_size(self):
        """Verifica que validate_city_configuration valide max_building_tile_size."""
        from tools.wizard import validate_city_configuration
        import yaml

        tmp_city = os.path.join(os.path.dirname(__file__), "tmp_test_max_bldg.yaml")
        try:
            valid_cfg = {
                "city": {
                    "code": "CUN",
                    "name": "Cancun",
                    "bbox": [-87.05, 21.0, -86.72, 21.31],
                    "max_building_tile_size": 450
                },
                "macroeconomics": {"tasa_pea": 0.62}
            }
            with open(tmp_city, "w", encoding="utf-8") as f:
                yaml.safe_dump(valid_cfg, f)

            res_valid = validate_city_configuration(tmp_city)
            self.assertTrue(res_valid["valid"])
            self.assertFalse(any("max_building_tile_size" in err for err in res_valid["errors"]))

            invalid_cfg = {
                "city": {
                    "code": "CUN",
                    "name": "Cancun",
                    "bbox": [-87.05, 21.0, -86.72, 21.31],
                    "max_building_tile_size": "not_a_number"
                },
                "macroeconomics": {"tasa_pea": 0.62}
            }
            with open(tmp_city, "w", encoding="utf-8") as f:
                yaml.safe_dump(invalid_cfg, f)

            res_invalid = validate_city_configuration(tmp_city)
            self.assertFalse(res_invalid["valid"])
            self.assertEqual(res_invalid["status"], "error")
            self.assertTrue(any("max_building_tile_size" in err for err in res_invalid["errors"]))
        finally:
            if os.path.exists(tmp_city):
                os.remove(tmp_city)

    @patch("sb_mexico.build_delivery.execute_wizard_build")
    @patch("tools.wizard.load_city_data")
    @patch("tools.wizard._resolve_city_path")
    def test_run_pipeline_task_passes_labels_only(self, mock_resolve, mock_load, mock_exec):
        """Verifica que run_pipeline_task pase labels_only a execute_wizard_build."""
        from tools.wizard import run_pipeline_task
        mock_resolve.return_value = "/dummy/cities/cun.yaml"
        mock_load.return_value = {"city": {"code": "CUN"}}
        mock_exec.return_value = {"status": "success", "package_path": "/dummy/dist/cun/CUN.zip"}

        run_pipeline_task("cun.yaml", skip_map=False, labels_only=True)

        mock_exec.assert_called_once()
        _, kwargs = mock_exec.call_args
        self.assertTrue(kwargs.get("labels_only"))

    def test_demand_v2_engine_config_json_has_bbox_and_country(self):
        """Verifica que write_result en DemandEngineV2 genere config.json con bbox, country y version 7.2.0."""
        import pandas as pd
        from sb_mexico.demand_v2.engine import write_result
        from types import SimpleNamespace

        result = {
            "game": {
                "points": [
                    {"id": "pt_1", "location": [-86.85, 21.15], "jobs": 100, "residents": 200, "popIds": ["pop_1"]}
                ],
                "pops": [
                    {"id": "pop_1", "size": 150, "residenceId": "pt_1", "jobId": "pt_1", "drivingDistance": 1000.0, "drivingSeconds": 120}
                ]
            },
            "report": {"status": "ok"},
            "points": [],
            "cells": {},
            "statistical_points": [],
            "export_point_mapping": {},
            "retained": pd.DataFrame([{
                "_full_identity": "230050001001001", "cve_mun_clean": "23005", "pobtot_adj": 200,
                "occupied_residents": 120, "pea_real": 120, "population_integer": 200,
                "occupied_integer": 120, "commuters_integer": 120, "employment_source": "test",
                "placement_source": "test"
            }]),
            "identity": "test_identity"
        }
        request = SimpleNamespace(
            config={
                "city": {
                    "code": "CUN",
                    "name": "Cancun",
                    "bbox": [-87.05, 21.0, -86.72, 21.31],
                    "country": "mx",
                    "initial_zoom": 13.0
                }
            }
        )
        write_result(result, request, self.out_dir)

        cfg_file = os.path.join(self.out_dir, "config.json")
        desc_file = os.path.join(self.out_dir, "description.md")
        self.assertTrue(os.path.exists(cfg_file))
        self.assertTrue(os.path.exists(desc_file))

        with open(cfg_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        self.assertEqual(cfg["code"], "CUN")
        self.assertEqual(cfg["bbox"], [-87.05, 21.0, -86.72, 21.31])
        self.assertEqual(cfg["country"], "MX")
        self.assertEqual(cfg["version"], "7.2.0")


if __name__ == "__main__":
    unittest.main()
