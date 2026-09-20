import unittest
import os
import tempfile
import pandas as pd
from sb_mexico.inegi import (
    format_cve_mun,
    parse_enoe_indicators,
    calibrate_denue_employment,
    load_cpv_demography,
    calculate_cpv_pea_rate,
    STATE_MACRO_BENCHMARKS
)

class TestInegi(unittest.TestCase):
    def test_format_cve_mun(self):
        self.assertEqual(format_cve_mun("23005", "23"), "23005")
        self.assertEqual(format_cve_mun("23005", None), "23005")
        self.assertEqual(format_cve_mun("1001", "01"), "01001")
        self.assertEqual(format_cve_mun("005", "23"), "23005")
        self.assertEqual(format_cve_mun("5", "23"), "23005")
        self.assertEqual(format_cve_mun("5", "1"), "01005")
        self.assertEqual(format_cve_mun("invalid", "23"), "-1")
        self.assertEqual(format_cve_mun("-1", "23"), "-1")
        self.assertEqual(format_cve_mun(None, "23"), "-1")
        # Casos con flotantes de pandas
        self.assertEqual(format_cve_mun("23005.0", None), "23005")
        self.assertEqual(format_cve_mun(23005.0, None), "23005")
        self.assertEqual(format_cve_mun("23005.0", "23"), "23005")
        self.assertEqual(format_cve_mun("1001.0", None), "01001")
        self.assertEqual(format_cve_mun("1001.0", "01"), "01001")
        self.assertEqual(format_cve_mun("5.0", "23"), "23005")
        self.assertEqual(format_cve_mun(" 23005 ", None), "23005")

    def test_parse_enoe_indicators(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write('"Indicador","2024-T1","2024-T2"\n')
            f.write('"Tasa de participación","66,44","65,12"\n')
            f.write('"Tasa de informalidad laboral 1 (TIL1)","44,97","45,20"\n')
            tmp_name = f.name
        try:
            data = parse_enoe_indicators(tmp_name)
            self.assertAlmostEqual(data["tasa_pea"], 0.6644, places=4)
            self.assertAlmostEqual(data["til_1"], 0.4497, places=4)
        finally:
            os.remove(tmp_name)

    def test_calibrate_denue_employment_normal(self):
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0},
            {"cve_mun_clean": "23005", "is_micro_small": False, "jobs_formal": 200.0},
        ])
        ce_benchmarks = {
            "23005": {"nombre": "Benito Juárez", "empleos_ce": 350.0}
        }
        df_calib, report = calibrate_denue_employment(df_denue, ce_benchmarks, til_1=0.45, min_sample_threshold=50)
        self.assertEqual(report["23005"]["status"], "CALIBRADO")
        self.assertAlmostEqual(report["23005"]["factor"], 1.5, places=3)

    def test_calibrate_denue_employment_excess(self):
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 50.0},
            {"cve_mun_clean": "23005", "is_micro_small": False, "jobs_formal": 500.0},
        ])
        ce_benchmarks = {
            "23005": {"nombre": "Benito Juárez", "empleos_ce": 400.0}
        }
        df_calib, report = calibrate_denue_employment(df_denue, ce_benchmarks, til_1=0.45, min_sample_threshold=50)
        self.assertEqual(report["23005"]["status"], "EXCESO_FORMAL_BASE")
        self.assertEqual(report["23005"]["factor"], 1.0)

    def test_load_cpv_demography_resilience(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "2", "lon": -86.86, "lat": 21.16, "calibrated_jobs": 30.0},
        ])
        df_censo_raw = pd.DataFrame([
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "1", "POBTOT": "100", "P_15YMAS": "70"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "99", "POBTOT": "200", "P_15YMAS": "140"},
            {"ENTIDAD": "23", "MUN": "004", "AGEB": "9999", "MZA": "1", "POBTOT": "150", "P_15YMAS": "100"},  # Otro municipio fuera de BBOX
        ])
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            df_censo_raw.to_csv(f.name, index=False)
            tmp_name = f.name
        try:
            df_geo = load_cpv_demography(tmp_name, df_denue, bbox, tasa_pea=0.65)
            # Solo deben incluirse las 2 manzanas pertenecientes al AGEB 0001 dentro del BBOX
            self.assertEqual(len(df_geo), 2)
            self.assertFalse(df_geo['lon'].isna().any())
            self.assertFalse(df_geo['lat'].isna().any())
            # Caso 1: coordenadas de manzana
            self.assertAlmostEqual(df_geo.iloc[0]['lon'], -86.85, places=3)
            # Caso 2: centroide de AGEB (-86.855)
            self.assertAlmostEqual(df_geo.iloc[1]['lon'], -86.855, places=3)
        finally:
            os.remove(tmp_name)

    def test_calibrate_denue_employment_bbox_share(self):
        # 300 empleos formales en BBOX (100 micro, 200 grandes)
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0},
            {"cve_mun_clean": "23005", "is_micro_small": False, "jobs_formal": 200.0},
        ])
        # Total municipal estatal = 600 empleos -> Cuota en BBOX = 50%
        mun_totals_global = {"23005": 600.0}
        # H001A municipal total = 500 -> H001A en BBOX = 500 * 50% = 250
        ce_benchmarks = {"23005": {"nombre": "Benito Juárez", "empleos_ce": 500.0}}

        df_calib, report = calibrate_denue_employment(
            df_denue, ce_benchmarks, til_1=0.45, min_sample_threshold=50, mun_totals_global=mun_totals_global
        )
        self.assertEqual(report["23005"]["share_bbox"], 0.5)
        self.assertEqual(report["23005"]["h001a"], 250.0)
        # Factor micro: (250 - 200) / 100 = 0.50 -> clamped a piso 1.0
        self.assertEqual(report["23005"]["factor"], 1.0)

    def test_parse_conapo_projections_growth_factors(self):
        from sb_mexico.inegi import parse_conapo_projections
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write('CLAVE,ANO,POB_TOTAL\n')
            f.write('23005,2020,100000\n')
            f.write('23005,2024,115000\n')
            f.write('23001,2020,50000\n')
            f.write('23001,2024,55000\n')
            tmp_name = f.name
        try:
            factors = parse_conapo_projections(tmp_name, target_year=2024, as_growth_factors=True)
            self.assertIn("23005", factors)
            self.assertAlmostEqual(factors["23005"], 1.15, places=3)
            self.assertIn("23001", factors)
            self.assertAlmostEqual(factors["23001"], 1.10, places=3)
        finally:
            os.remove(tmp_name)

    def test_parse_enoe_indicators_latin1(self):
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
            # Archivo codificado en latin-1 con acento en participación
            content = '"Indicador","2024-T1"\n"Tasa de participaci\xf3n","64,14"\n"Tasa de informalidad laboral 1 (TIL1)","59,14"\n'
            f.write(content.encode('latin1'))
            tmp_name = f.name
        try:
            data = parse_enoe_indicators(tmp_name)
            self.assertAlmostEqual(data["tasa_pea"], 0.6414, places=4)
            self.assertAlmostEqual(data["til_1"], 0.5914, places=4)
        finally:
            os.remove(tmp_name)

    def test_calculate_cpv_pea_rate(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write('ENTIDAD,NOM_ENT,MUN,NOM_MUN,LOC,NOM_LOC,AGEB,MZA,POBTOT,P_15YMAS,PEA\n')
            f.write('31,Yucatán,000,Total de la entidad,0000,Total,0000,000,200000,100000,65000\n')
            f.write('31,Yucatán,050,Mérida,0000,Total del municipio,0000,000,100000,80000,56000\n')
            f.write('31,Yucatán,041,Kanasín,0000,Total del municipio,0000,000,50000,20000,14000\n')
            tmp_name = f.name
        try:
            # Mérida solo (56000 / 80000 = 0.70)
            rate_mid = calculate_cpv_pea_rate(tmp_name, ['31050'])
            self.assertAlmostEqual(rate_mid, 0.70, places=4)

            # Zona metropolitana Mérida + Kanasín: (56000 + 14000) / (80000 + 20000) = 70000 / 100000 = 0.70
            rate_metro = calculate_cpv_pea_rate(tmp_name, ['31050', '31041'])
            self.assertAlmostEqual(rate_metro, 0.70, places=4)

            # Todo el estado
            rate_all = calculate_cpv_pea_rate(tmp_name)
            self.assertAlmostEqual(rate_all, 0.70, places=4)
        finally:
            os.remove(tmp_name)

    def test_state_macro_benchmarks_coverage(self):
        self.assertEqual(len(STATE_MACRO_BENCHMARKS), 32)
        for i in range(1, 33):
            cve = f"{i:02d}"
            self.assertIn(cve, STATE_MACRO_BENCHMARKS)
            bm = STATE_MACRO_BENCHMARKS[cve]
            self.assertIn("nombre", bm)
            self.assertGreater(bm["tasa_pea"], 0.40)
            self.assertLess(bm["tasa_pea"], 0.85)
            self.assertGreater(bm["til_1"], 0.20)
            self.assertLess(bm["til_1"], 0.90)

if __name__ == "__main__":
    unittest.main()

