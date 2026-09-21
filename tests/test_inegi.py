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
    STATE_MACRO_BENCHMARKS,
    load_denue,
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

    def test_load_denue_resilient_formats(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}

        # Archivo 1: CSV delimitado por punto y coma, UTF-8, columna personal_ocupado
        # Incluye una fila dentro de BBOX y una fuera
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f1:
            content1 = (
                "id;nom_estab;latitud;longitud;cve_ent;cve_mun;personal_ocupado;ageb;manzana\n"
                "1;Abarrotes El Sol;21.15;-86.85;23;005;0 a 5 personas;0001;1\n"
                "2;Hotel Selva;20.50;-87.50;23;009;51 a 100 personas;0002;2\n"
            )
            f1.write(content1.encode("utf-8"))
            tmp1 = f1.name

        # Archivo 2: CSV delimitado por comas, Latin-1, columna per_ocu con acento
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f2:
            content2 = (
                "id,nom_estab,latitud,longitud,cve_ent,cve_mun,per_ocu,ageb,manzana\n"
                "3,Fábrica Grande,21.16,-86.84,23,005,251 y más personas,0001,3\n"
            )
            f2.write(content2.encode("latin1"))
            tmp2 = f2.name

        try:
            df = load_denue([tmp1, tmp2], bbox)
            # Solo deben incluirse id 1 e id 3 (ambos en BBOX)
            self.assertEqual(len(df), 2)
            self.assertIn("jobs_formal", df.columns)
            self.assertIn("is_micro_small", df.columns)

            # id 1: 0 a 5 personas -> 2.24, micro=True
            row1 = df[df["id"] == "1"].iloc[0]
            self.assertAlmostEqual(row1["jobs_formal"], 2.24)
            self.assertTrue(row1["is_micro_small"])

            # id 3: 251 y más personas -> 450.0, micro=False
            row3 = df[df["id"] == "3"].iloc[0]
            self.assertAlmostEqual(row3["jobs_formal"], 450.0)
            self.assertFalse(row3["is_micro_small"])

            # mun_totals_global debe incluir id 2 (fuera de BBOX) acumulado antes del corte
            # id 1 (23005): 2.24 + id 3 (23005): 450.0 = 452.24
            # id 2 (23009): 71.41
            totals = df.attrs.get("mun_totals_global", {})
            self.assertIn("23005", totals)
            self.assertIn("23009", totals)
            self.assertAlmostEqual(totals["23005"], 452.24, places=2)
            self.assertAlmostEqual(totals["23009"], 71.41, places=2)
        finally:
            os.remove(tmp1)
            os.remove(tmp2)

    def test_load_denue_duplicate_paths(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write("latitud,longitud,cve_ent,cve_mun,per_ocu\n21.15,-86.85,23,005,0 a 5 personas\n")
            tmp = f.name
        try:
            # Pasar la misma ruta dos veces
            df = load_denue([tmp, tmp], bbox)
            self.assertEqual(len(df), 1)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_semicolon_and_cp1252(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
        ])
        content = (
            "ENTIDAD;MUN;AGEB;MZA;POBTOT;P_15YMAS\n"
            "23;005;0001;1;120;90\n"
        )
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
            f.write(content.encode('cp1252'))
            tmp = f.name
        try:
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.65)
            self.assertEqual(len(df_geo), 1)
            self.assertAlmostEqual(df_geo.iloc[0]['lon'], -86.85, places=3)
            self.assertAlmostEqual(df_geo.iloc[0]['pobtot_adj'], 120.0)
            self.assertAlmostEqual(df_geo.iloc[0]['pea_real'], 90.0 * 0.65)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_mza_asterisk_and_clamping(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "2", "lon": -86.86, "lat": 21.16, "calibrated_jobs": 30.0},
        ])
        # Fila 1: Manzana 1 legítima (POBTOT 50)
        # Fila 2: MZA '*' (confidencial/resumen) -> NO debe asociarse a Manzana 1, debe descartarse
        # Fila 3: Manzana 2 legítima donde P_15YMAS (60) > POBTOT (40) -> debe clampearse a 40
        # Fila 4: Manzana 3 legítima con '*' en POBTOT y P_15YMAS -> 1.5 y 1.0
        # Fila 5: MZA '000' (resumen de AGEB) -> debe descartarse
        df_censo = pd.DataFrame([
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "1", "POBTOT": "50", "P_15YMAS": "30"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "*", "POBTOT": "999", "P_15YMAS": "500"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "2", "POBTOT": "40", "P_15YMAS": "60"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "3", "POBTOT": "*", "P_15YMAS": "*"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "000", "POBTOT": "5000", "P_15YMAS": "3000"},
        ])
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            df_censo.to_csv(f.name, index=False)
            tmp = f.name
        try:
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.5)
            # Solo deben pasar Manzana 1, 2 y 3 (todas dentro del AGEB 0001 en BBOX)
            self.assertEqual(len(df_geo), 3)
            # Manzana 1 no absorbió la fila con MZA '*'
            row1 = df_geo[df_geo['mza_clean'] == '1'].iloc[0]
            self.assertAlmostEqual(row1['pobtot_adj'], 50.0)

            # Manzana 2: P_15YMAS debe estar clampeada a POBTOT (40)
            row2 = df_geo[df_geo['mza_clean'] == '2'].iloc[0]
            self.assertAlmostEqual(row2['pobtot_adj'], 40.0)
            self.assertAlmostEqual(row2['pob15_adj'], 40.0)
            self.assertAlmostEqual(row2['pea_real'], 20.0)

            # Manzana 3: POBTOT 1.5, P_15YMAS 1.0
            row3 = df_geo[df_geo['mza_clean'] == '3'].iloc[0]
            self.assertAlmostEqual(row3['pobtot_adj'], 1.5)
            self.assertAlmostEqual(row3['pob15_adj'], 1.0)
            self.assertAlmostEqual(row3['pea_real'], 0.5)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_duplicate_paths(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
        ])
        content = "ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n23,005,0001,1,100,70\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp = f.name
        try:
            df_geo = load_cpv_demography([tmp, tmp], df_denue, bbox, tasa_pea=0.65)
            self.assertEqual(len(df_geo), 1)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_early_municipal_filter(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        # Solo municipio 23005 en DENUE
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
        ])
        content = (
            "ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n"
            "23,005,0001,1,100,70\n"
            "23,001,0002,1,500,350\n"  # Cozumel fuera de DENUE target
            "23,008,0003,1,800,600\n"  # Solidaridad fuera de DENUE target
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp = f.name
        try:
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.65)
            # Solo el municipio 23005 debe ingresar a df_geo
            self.assertEqual(len(df_geo), 1)
            self.assertEqual(df_geo.iloc[0]['cve_mun_clean'], "23005")
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_atomic_coords(self):
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        # DENUE con coordenadas para AGEB 0001
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "-1", "lon": -86.80, "lat": 21.10, "calibrated_jobs": 10.0},
        ])
        content = (
            "ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n"
            "23,005,0001,1,100,70\n"
        )
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp = f.name
        try:
            # Imputación por AGEB DENUE (nivel 3): tanto lon como lat provienen atómicamente de DENUE AGEB
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.65)
            self.assertEqual(len(df_geo), 1)
            self.assertAlmostEqual(df_geo.iloc[0]['lon'], -86.80)
            self.assertAlmostEqual(df_geo.iloc[0]['lat'], 21.10)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_denue_atomic_coords_adversarial(self):
        """Falsificación de centroide híbrido DENUE: no sintetizar punto a partir de filas con coordenadas parciales."""
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        # Fila 1: solo lon válida, lat NaN
        # Fila 2: lon NaN, solo lat válida
        # Ambas en la misma manzana 1.
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.80, "lat": float('nan'), "calibrated_jobs": 10.0},
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": float('nan'), "lat": 21.10, "calibrated_jobs": 10.0},
        ])
        content = "ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n23,005,0001,1,100,70\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f:
            f.write(content)
            tmp = f.name
        try:
            # Al no existir ninguna fila con par (lon, lat) atómico completo, no se debe sintetizar (-86.80, 21.10)
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.65)
            # La manzana no puede ser georreferenciada con coordenadas parciales y se descarta
            self.assertEqual(len(df_geo), 0)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_heterogeneous_files_tolerance(self):
        """Un archivo censal con columnas faltantes no debe abortar la carga de archivos válidos posteriores."""
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
        ])
        # Archivo 1: Invalido (falta P_15YMAS)
        content_inv = "ENTIDAD,MUN,AGEB,MZA,POBTOT\n23,005,0001,1,100\n"
        # Archivo 2: Valido
        content_val = "ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n23,005,0001,1,100,70\n"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f1:
            f1.write(content_inv)
            tmp1 = f1.name
        with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, encoding='utf-8') as f2:
            f2.write(content_val)
            tmp2 = f2.name
        try:
            # Debe procesar tmp2 ignorando tmp1
            df_geo = load_cpv_demography([tmp1, tmp2], df_denue, bbox, tasa_pea=0.65)
            self.assertEqual(len(df_geo), 1)
            self.assertAlmostEqual(df_geo.iloc[0]['lon'], -86.85)
        finally:
            os.remove(tmp1)
            os.remove(tmp2)

    def test_calculate_cpv_pea_rate_semicolon_and_cp1252(self):
        """calculate_cpv_pea_rate con delimitador punto y coma y acentos en CP1252."""
        content = (
            "ENTIDAD;NOM_ENT;MUN;NOM_MUN;LOC;NOM_LOC;AGEB;MZA;POBTOT;P_15YMAS;PEA\n"
            "23;Quintana Roo;005;Benito Juárez;0000;Total del municipio;0000;000;100000;80000;56000\n"
        )
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
            f.write(content.encode('cp1252'))
            tmp = f.name
        try:
            rate = calculate_cpv_pea_rate(tmp, ['23005'])
            self.assertIsNotNone(rate)
            self.assertAlmostEqual(rate, 0.70, places=3)
        finally:
            os.remove(tmp)

    def test_load_cpv_demography_late_cp1252_byte(self):
        """Archivo CP1252 cuyo byte no-ASCII aparece después de los primeros 100 KB de ASCII puro."""
        bbox = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}
        df_denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.85, "lat": 21.15, "calibrated_jobs": 50.0},
        ])
        with tempfile.NamedTemporaryFile(mode='wb', suffix='.csv', delete=False) as f:
            # Cabecera estándar
            f.write(b"ENTIDAD,MUN,AGEB,MZA,POBTOT,P_15YMAS\n")
            # 100 KB de líneas dummy de padding ASCII
            dummy_row = b"23,005,0001,1,0,0\n"
            f.write(dummy_row * 5000)
            # Fila censal con población
            f.write(b"23,005,0001,1,100,70\n")
            # Byte no-ASCII en CP1252 (0xf3 = 'o' con acento)
            f.write(b"# Comentario: Canc\xf3n\n")
            tmp = f.name
        try:
            df_geo = load_cpv_demography(tmp, df_denue, bbox, tasa_pea=0.65)
            self.assertEqual(len(df_geo), 1)
            self.assertAlmostEqual(df_geo.iloc[0]['lon'], -86.85)
        finally:
            os.remove(tmp)

if __name__ == "__main__":
    unittest.main()

