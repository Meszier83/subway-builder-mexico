import unittest
import os
import tempfile
import json
from sb_mexico.special_demand import (
    load_special_demand_types,
    infer_poi_type_and_subtype,
    resolve_localized_name,
    generate_special_demand_points_doc,
    validate_special_demand_points,
    save_special_demand_points
)


class TestSpecialDemand(unittest.TestCase):
    def test_load_types(self):
        doc = load_special_demand_types()
        self.assertIn("types", doc)
        self.assertGreater(len(doc["types"]), 10)
        type_ids = [t["id"] for t in doc["types"]]
        self.assertIn("airport", type_ids)
        self.assertIn("university", type_ids)
        self.assertIn("shopping_center", type_ids)

    def test_infer_poi_type_and_subtype(self):
        # Prefix based
        t, sub = infer_poi_type_and_subtype("AIR_Cancún")
        self.assertEqual(t, "airport")
        self.assertEqual(sub, "international_terminal")

        t, sub = infer_poi_type_and_subtype("UNI_UNAM")
        self.assertEqual(t, "university")
        self.assertIsNone(sub)

        t, sub = infer_poi_type_and_subtype("SPO_Azteca")
        self.assertEqual(t, "sports_facility")

        # Explicit config overrides
        t, sub = infer_poi_type_and_subtype("Custom_1", {"type": "museum", "sub_type": None})
        self.assertEqual(t, "museum")

        # Mexican keyword heuristics
        t, sub = infer_poi_type_and_subtype("Plaza Galerias")
        self.assertEqual(t, "shopping_center")

    def test_resolve_localized_name(self):
        # Dict provided
        loc = resolve_localized_name("AIR_1", {"name": {"es": "Terminal 1", "en": "Terminal 1"}})
        self.assertEqual(loc["es"], "Terminal 1")
        self.assertEqual(loc["__default__"], "Terminal 1")

        # String provided
        loc = resolve_localized_name("UNI_1", {"name": "Tec de Monterrey"})
        self.assertEqual(loc["es"], "Tec de Monterrey")
        self.assertEqual(loc["__default__"], "Tec de Monterrey")

        # Auto generated from airport prefix
        loc = resolve_localized_name("AIR_Cancún")
        self.assertIn("Aeropuerto Internacional", loc["es"])
        self.assertIn("Cancún", loc["es"])

    def test_generate_and_validate_doc(self):
        pois_cfg = [
            {
                "id": "AIR_Cancún",
                "name": {"es": "Aeropuerto de Cancún", "en": "Cancun Airport"},
                "type": "airport",
                "sub_type": "international_terminal",
                "metadata": {"source": "INEGI"}
            },
            {
                "id": "UNI_Unicaribe",
                "name": "Universidad del Caribe",
                "type": "university"
            }
        ]

        demand_points = [
            {"id": "AIR_Cancún", "popIds": ["pop_000001", "pop_000002"], "is_special": True},
            {"id": "UNI_Unicaribe", "popIds": ["pop_000003"], "is_special": True}
        ]

        doc = generate_special_demand_points_doc("CUN", pois_cfg, demand_points)
        self.assertEqual(doc["map_code"], "CUN")
        self.assertEqual(doc["version"], 1)
        self.assertEqual(len(doc["points"]), 2)
        self.assertEqual(doc["points"][0]["pop_ids"], ["pop_000001", "pop_000002"])

        is_valid, errors = validate_special_demand_points(doc)
        self.assertTrue(is_valid, f"Validation errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_validation_catches_invalid_types(self):
        bad_doc = {
            "$schema": "special_demand_points.schema.json",
            "version": 1,
            "map_code": "CUN",
            "points": [
                {
                    "point_id": "POI_Fake",
                    "type": "non_existent_type_xyz",
                    "name": {"__default__": "Fake"}
                }
            ]
        }
        is_valid, errors = validate_special_demand_points(bad_doc)
        self.assertFalse(is_valid)
        self.assertTrue(any("non_existent_type_xyz" in e for e in errors))

    def test_save_special_demand_points(self):
        doc = {
            "$schema": "special_demand_points.schema.json",
            "version": 1,
            "map_code": "CUN",
            "generated_at": "2026-09-02T00:00:00Z",
            "points": []
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        try:
            save_special_demand_points(doc, tmp_path)
            with open(tmp_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded["map_code"], "CUN")
        finally:
            os.remove(tmp_path)


if __name__ == "__main__":
    unittest.main()
