"""
tests.test_toponymy_homogenizer
===============================
Pruebas unitarias para el motor universal de homogeneización de toponimia urbana mexicana.
"""

import unittest
from sb_mexico.toponymy_homogenizer import (
    smart_title_case,
    ToponymyAnalyzer,
    ToponymyHomogenizer,
    haversine_distance_m,
    calculate_place_prestige_score,
    cluster_places_by_proximity,
    apply_zone_thinning_selection
)


class TestSmartTitleCase(unittest.TestCase):
    def test_spanish_particles(self):
        self.assertEqual(
            smart_title_case("FRACCIONAMIENTO VILLAS DEL SOL"),
            "Fraccionamiento Villas del Sol"
        )
        self.assertEqual(
            smart_title_case("PASEOS DE LA SELVA"),
            "Paseos de la Selva"
        )
        self.assertEqual(
            smart_title_case("DEL VALLE CENTRO"),
            "Del Valle Centro"
        )

    def test_roman_numerals(self):
        self.assertEqual(
            smart_title_case("FRACCIONAMIENTO LAS AMERICAS II"),
            "Fraccionamiento Las Americas II"
        )
        self.assertEqual(
            smart_title_case("FRACC. LAS AMERICAS II"),
            "Fracc. Las Americas II"
        )
        self.assertEqual(
            smart_title_case("SIGLO XXI ETAPA IV"),
            "Siglo XXI Etapa IV"
        )
        self.assertEqual(
            smart_title_case("SECCION IX"),
            "Seccion IX"
        )

    def test_alphanumeric_codes(self):
        self.assertEqual(
            smart_title_case("SUPERMANZANA 92-A"),
            "Supermanzana 92-A"
        )
        self.assertEqual(
            smart_title_case("94"),
            "94"
        )
        self.assertEqual(
            smart_title_case("228-B"),
            "228-B"
        )

    def test_abbreviations(self):
        self.assertEqual(
            smart_title_case("U.H. MANUEL RIVERA ANAYA"),
            "U.H. Manuel Rivera Anaya"
        )
        self.assertEqual(
            smart_title_case("FRACC LAS PALMAS"),
            "Fracc. Las Palmas"
        )


class TestToponymyAnalyzer(unittest.TestCase):
    def test_classification_categories(self):
        c_sm = ToponymyAnalyzer.classify_name("SUPERMANZANA 94")
        self.assertEqual(c_sm["category"], "SUPERMANZANA")
        self.assertEqual(c_sm["number"], "94")

        c_sm_short = ToponymyAnalyzer.classify_name("SM 228")
        self.assertEqual(c_sm_short["category"], "SUPERMANZANA")
        self.assertEqual(c_sm_short["number"], "228")

        c_reg = ToponymyAnalyzer.classify_name("Región 95")
        self.assertEqual(c_reg["category"], "REGION")
        self.assertEqual(c_reg["number"], "95")

        c_num = ToponymyAnalyzer.classify_name("510")
        self.assertEqual(c_num["category"], "BARE_NUMERIC")
        self.assertEqual(c_num["number"], "510")

        c_col = ToponymyAnalyzer.classify_name("Colonia Del Valle")
        self.assertEqual(c_col["category"], "COLONIA")
        self.assertEqual(c_col["core_name"], "Del Valle")

        c_fracc = ToponymyAnalyzer.classify_name("Fraccionamiento Real del Bosque")
        self.assertEqual(c_fracc["category"], "FRACCIONAMIENTO")

        c_res = ToponymyAnalyzer.classify_name("Residencial Arbolada")
        self.assertEqual(c_res["category"], "RESIDENCIAL")

        c_uh = ToponymyAnalyzer.classify_name("U.H. Tlatelolco")
        self.assertEqual(c_uh["category"], "UNIDAD_HABITACIONAL")

        c_barrio = ToponymyAnalyzer.classify_name("Barrio de San Juan")
        self.assertEqual(c_barrio["category"], "BARRIO_PUEBLO")

    def test_analyze_collection(self):
        places = [
            {"name": "SUPERMANZANA 94"},
            {"name": "SM 100"},
            {"name": "95"},
            {"name": "Colonia Centro"},
            {"name": "Fracc. Las Palmas"}
        ]
        res = ToponymyAnalyzer.analyze_collection(places)
        self.assertEqual(res["total"], 5)
        self.assertEqual(res["categories"]["SUPERMANZANA"], 2)
        self.assertEqual(res["categories"]["BARE_NUMERIC"], 1)
        self.assertEqual(res["categories"]["COLONIA"], 1)
        self.assertEqual(res["categories"]["FRACCIONAMIENTO"], 1)
        self.assertTrue(res["has_bare_numbers"])
        self.assertTrue(res["has_supermanzanas"])
        self.assertTrue(res["has_redundant_colonias"])


class TestToponymyHomogenizer(unittest.TestCase):
    def test_unify_supermanzanas(self):
        places = [
            {"name": "SUPERMANZANA 94", "type": "suburb"},
            {"name": "SM 95", "type": "neighbourhood"},
            {"name": "Región 100", "type": "suburb"},
            {"name": "510", "type": "quarter"},
            {"name": "Colonia Roma", "type": "suburb"}
        ]
        out, count = ToponymyHomogenizer.unify_supermanzanas(places, "Supermanzana {num}")
        self.assertEqual(count, 4)
        self.assertEqual(out[0]["name"], "Supermanzana 94")
        self.assertEqual(out[1]["name"], "Supermanzana 95")
        self.assertEqual(out[2]["name"], "Supermanzana 100")
        self.assertEqual(out[3]["name"], "Supermanzana 510")
        self.assertEqual(out[4]["name"], "Colonia Roma")

        # Probar formato corto SM
        out_sm, _ = ToponymyHomogenizer.unify_supermanzanas(places, "SM {num}")
        self.assertEqual(out_sm[0]["name"], "SM 94")
        self.assertEqual(out_sm[3]["name"], "SM 510")

    def test_strip_redundant_prefixes(self):
        places = [
            {"name": "Colonia Roma Norte"},
            {"name": "COL. DEL VALLE"},
            {"name": "FRACCIONAMIENTO PRADO NORTE"},
            {"name": "Residencial Cumbres"}
        ]
        out, count = ToponymyHomogenizer.strip_redundant_prefixes(
            places,
            strip_colonia=True,
            fracc_mode="Fracc.",
            strip_residencial=True
        )
        self.assertEqual(count, 4)
        self.assertEqual(out[0]["name"], "Roma Norte")
        self.assertEqual(out[1]["name"], "Del Valle")
        self.assertEqual(out[2]["name"], "Fracc. Prado Norte")
        self.assertEqual(out[3]["name"], "Cumbres")

    def test_batch_regex(self):
        places = [
            {"name": "R-94"},
            {"name": "R-100"},
            {"name": "Centro"}
        ]
        out, diffs = ToponymyHomogenizer.apply_batch_regex(
            places,
            pattern=r"^R-(\d+)",
            replacement=r"Región \1"
        )
        self.assertEqual(len(diffs), 2)
        self.assertEqual(out[0]["name"], "Región 94")
        self.assertEqual(out[1]["name"], "Región 100")
        self.assertEqual(out[2]["name"], "Centro")

    def test_spatial_deduplicate(self):
        # Dos supermanzanas muy cercanas (~50m de diferencia)
        places = [
            {
                "name": "SUPERMANZANA 94",
                "loc": [-86.8500, 21.1600],
                "source": "OSM_POLYGON",
                "type": "suburb"
            },
            {
                "name": "SM 94",
                "loc": [-86.8502, 21.1601],
                "source": "INEGI_DENUE",
                "type": "suburb"
            },
            {
                "name": "SUPERMANZANA 94",  # Mismo nombre pero a 10 km de distancia (otra ciudad o zona)
                "loc": [-86.9500, 21.2500],
                "source": "INEGI_DENUE",
                "type": "suburb"
            }
        ]
        kept, removed = ToponymyHomogenizer.spatial_deduplicate(places, distance_threshold_m=500.0)
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(removed), 1)
        # Se debió conservar la fuente de mayor peso OSM_POLYGON
        self.assertEqual(kept[0]["source"], "OSM_POLYGON")
        self.assertEqual(removed[0]["dropped"]["source"], "INEGI_DENUE")

    def test_pipeline_integration(self):
        places = [
            {"name": "SUPERMANZANA 94", "loc": [-86.8500, 21.1600], "source": "OSM_POLYGON"},
            {"name": "SM 94", "loc": [-86.8501, 21.1601], "source": "INEGI_DENUE"},
            {"name": "COLONIA DEL VALLE", "loc": [-86.8600, 21.1700]},
            {"name": "FRACCIONAMIENTO VILLAS DEL SOL II", "loc": [-86.8700, 21.1800]},
            {"name": "95", "loc": [-86.8800, 21.1900]}
        ]
        options = {
            "unify_supermanzanas": "Supermanzana {num}",
            "strip_prefixes": {
                "strip_colonia": True,
                "fracc_mode": "Fracc."
            },
            "smart_casing": True,
            "spatial_deduplicate": True,
            "spatial_distance_m": 500.0
        }
        res = ToponymyHomogenizer.apply_pipeline(places, options)
        out_places = res["places"]
        self.assertEqual(len(out_places), 4)  # 1 duplicado eliminado
        names = [p["name"] for p in out_places]
        self.assertIn("Supermanzana 94", names)
        self.assertIn("Del Valle", names)
        self.assertIn("Fracc. Villas del Sol II", names)
        self.assertIn("Supermanzana 95", names)


class TestZoneClustering(unittest.TestCase):
    def test_cluster_proximity_and_prestige(self):
        places = [
            {
                "name": "Cancún Centro",
                "loc": [-86.8500, 21.1600],
                "source": "YAML_CURATED",
                "type": "suburb",
                "denue_count": 500
            },
            {
                "name": "Supermanzana 22",
                "loc": [-86.8520, 21.1610],  # ~250m de Cancún Centro
                "source": "INEGI_DENUE",
                "type": "quarter",
                "denue_count": 80
            },
            {
                "name": "Fracc. El Sol",
                "loc": [-86.8530, 21.1620],  # ~400m de Cancún Centro
                "source": "INEGI_DENUE",
                "type": "neighbourhood",
                "denue_count": 10
            },
            {
                "name": "Puerto Morelos",
                "loc": [-86.8750, 20.8500],  # ~35 km al sur (completamente aislado)
                "source": "OSM_POLYGON",
                "type": "suburb",
                "denue_count": 250
            }
        ]
        res = cluster_places_by_proximity(places, radius_m=1000.0, rank_heuristic="density")
        self.assertEqual(res["total_places"], 4)
        self.assertEqual(res["anchors_count"], 2)
        self.assertEqual(res["conflict_zones_count"], 1)
        self.assertEqual(res["isolated_zones_count"], 1)
        self.assertEqual(res["summary"]["projected_pruned"], 2)

        conflict_z = [z for z in res["zones"] if z["is_conflict"]][0]
        self.assertEqual(len(conflict_z["candidates"]), 3)
        # El anchor sugerido debe ser Cancún Centro por tener mayor prestigio (YAML_CURATED y 500 denue)
        recommended = [c for c in conflict_z["candidates"] if c["recommended"]][0]
        self.assertEqual(recommended["name"], "Cancún Centro")
        self.assertEqual(conflict_z["selected_index"], 0)

    def test_apply_thinning_selection_and_exception(self):
        places = [
            {"name": "A1", "loc": [-86.8500, 21.1600], "denue_count": 100},
            {"name": "A2", "loc": [-86.8510, 21.1610], "denue_count": 50},
            {"name": "B1", "loc": [-86.8900, 21.2000], "denue_count": 80},
            {"name": "B2", "loc": [-86.8910, 21.2010], "denue_count": 40}
        ]
        # Zone A: winner is index 1 (A2, anulación manual del usuario)
        # Zone B: marked as exception (ambos deben mantenerse)
        zones = [
            {
                "zone_id": "zone_1",
                "is_conflict": True,
                "is_exception": False,
                "selected_index": 1,
                "candidates": [{"original_index": 0}, {"original_index": 1}]
            },
            {
                "zone_id": "zone_2",
                "is_conflict": True,
                "is_exception": True,  # Excepción
                "selected_index": 2,
                "candidates": [{"original_index": 2}, {"original_index": 3}]
            }
        ]
        out = apply_zone_thinning_selection(places, zones)
        self.assertEqual(out["total_pruned"], 1)  # Solo A1 podada
        self.assertEqual(out["total_kept"], 3)
        kept_names = [p["name"] for p in out["places"]]
        self.assertIn("A2", kept_names)
        self.assertNotIn("A1", kept_names)
        self.assertIn("B1", kept_names)
        self.assertIn("B2", kept_names)


class TestScanCatalog(unittest.TestCase):
    def test_scan_catalog_cancun(self):
        from sb_mexico.toponymy import scan_city_settlements_catalog
        import os
        city_path = os.path.join("cities", "cancun_riviera_maya.yaml")
        if os.path.exists(city_path):
            cat = scan_city_settlements_catalog(city_path, min_count=20)
            self.assertIn("city", cat)
            self.assertGreater(cat["total"], 0)
            self.assertIn("diagnosis", cat)
            self.assertTrue(cat["diagnosis"]["has_supermanzanas"])


if __name__ == "__main__":
    unittest.main()
