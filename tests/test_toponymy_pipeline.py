"""
tests.test_toponymy_pipeline
=============================
Pruebas unitarias para el enriquecimiento toponimico universal:
1. Normalizacion de nombres al estandar mexicano.
2. Generacion de GeoJSON desde microdatos DENUE con medianas de coordenadas.
3. Idempotencia y logica de parche en patch_depot_wsl.
"""

import os
import tempfile
import json
import unittest
import pandas as pd
from shapely.geometry import shape

from sb_mexico.toponymy import (
    format_clean_place_name,
    extract_settlement_suggestions,
    generate_denue_neighborhoods_geojson
)
from tools.patch_depot_wsl import patch_polygon_neighborhoods


class TestToponymyPipeline(unittest.TestCase):
    def test_format_clean_place_name(self):
        # Casos numericos puros
        name, ptype = format_clean_place_name("94", "REGION")
        self.assertEqual(name, "Región 94")
        self.assertEqual(ptype, "suburb")

        name, ptype = format_clean_place_name("228", "SUPERMANZANA")
        self.assertEqual(name, "Supermanzana 228")
        self.assertEqual(ptype, "suburb")

        # Casos con prefijo
        name, ptype = format_clean_place_name("FRACCIONAMIENTO PASEOS DEL MAR", "FRACCIONAMIENTO")
        self.assertEqual(name, "Fracc. Paseos Del Mar")
        self.assertEqual(ptype, "neighbourhood")

        name, ptype = format_clean_place_name("COLONIA TRES REYES", "COLONIA")
        self.assertEqual(name, "Tres Reyes")
        self.assertEqual(ptype, "suburb")

    def test_generate_denue_neighborhoods_geojson(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            denue_csv = os.path.join(tmpdir, "sample_denue.csv")
            output_geojson = os.path.join(tmpdir, "additional_neighborhoods.geojson")

            # Crear datos sinteticos representativos del DENUE
            data = {
                "cve_mun": [5] * 40,
                "nom_estab": [f"Tienda {i}" for i in range(40)],
                "tipo_asent": ["SUPERMANZANA"] * 20 + ["FRACCIONAMIENTO"] * 15 + ["COLONIA"] * 5,
                "nomb_asent": ["228"] * 20 + ["PASEOS DEL MAR"] * 15 + ["MICRO RUIDO"] * 5,
                "latitud": [21.1800 + (i * 0.0001) for i in range(20)] +
                           [21.1730 + (i * 0.0001) for i in range(15)] +
                           [21.1500] * 5,
                "longitud": [-86.8520 - (i * 0.0001) for i in range(20)] +
                            [-86.9090 - (i * 0.0001) for i in range(15)] +
                            [-86.8800] * 5
            }
            pd.DataFrame(data).to_csv(denue_csv, index=False)

            bbox = [-87.0, 21.0, -86.7, 21.3]

            # Ejecutar generacion con min_count=10 (debe excluir 'MICRO RUIDO' que tiene 5)
            res = generate_denue_neighborhoods_geojson(
                denue_path=denue_csv,
                bbox=bbox,
                output_geojson=output_geojson,
                min_count=10,
                exclude_existing_names={"supermanzana 228"}
            )

            self.assertIsNotNone(res)
            self.assertTrue(os.path.exists(output_geojson))

            with open(output_geojson, "r", encoding="utf-8") as f:
                gdata = json.load(f)

            features = gdata.get("features", [])
            # Debe excluir 'Supermanzana 228' (por exclude_existing_names) y 'MICRO RUIDO' (por min_count < 10)
            names = [f["properties"]["name"] for f in features]
            self.assertIn("Fracc. Paseos Del Mar", names)
            self.assertNotIn("Supermanzana 228", names)
            self.assertNotIn("Micro Ruido", names)

            # Verificar que todas las geometrias son Point
            for f in features:
                self.assertEqual(f["geometry"]["type"], "Point")
                self.assertEqual(len(f["geometry"]["coordinates"]), 2)

    def test_patch_polygon_neighborhoods_logic(self):
        sample_code = """            # Build the osmium filter string
            # e.g., "n/place=city n/place=borough"
            filter_cmd.extend([f"n/place{self.places_suffix}={t}" for t in tags])
            filter_cmd.extend(["-o", str(osm_pbf), "--overwrite"])
            self._run_command(filter_cmd)
            self._run_command(["osmium", "export", str(osm_pbf), "-o", 
                               str(geojson), "--overwrite"])
            self._rewrite_label_geojson_names(geojson)"""

        patched, mod = patch_polygon_neighborhoods(sample_code)
        self.assertTrue(mod)
        self.assertIn("nwr/place", patched)
        self.assertIn("Toponymy Centroids", patched)

        # Verificar idempotencia
        patched_again, mod_again = patch_polygon_neighborhoods(patched)
        self.assertFalse(mod_again)


if __name__ == "__main__":
    unittest.main()
