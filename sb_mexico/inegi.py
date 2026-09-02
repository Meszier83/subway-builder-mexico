"""
sb_mexico.inegi
===============
Módulo de ingesta, normalización, georreferenciación y calibración
de fuentes estadísticas oficiales del INEGI (CPV 2020, DENUE, CE 2024, ENOE).
"""

import os
import glob
import math
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Union

# Estratos de personal ocupado en el DENUE y sus medias geométricas
DENUE_ESTRATOS = {
    "0 a 5 personas": 2.24,
    "6 a 10 personas": 7.75,
    "11 a 30 personas": 18.17,
    "31 a 50 personas": 39.37,
    "51 a 100 personas": 71.41,
    "101 a 250 personas": 158.90,
    "251 y más personas": 450.00
}


def format_cve_mun(cve_mun_raw, cve_ent_raw) -> str:
    """Homologa claves municipales y estatales al estándar INEGI de 5 dígitos (EEMMM)."""
    s_mun = str(cve_mun_raw).strip()
    s_ent = str(cve_ent_raw).strip().zfill(2)
    if not s_mun or s_mun.lower() == 'nan' or s_mun == '-1':
        return "-1"
    if not s_ent or s_ent == '00' or s_ent.lower() == 'nan':
        return "-1"
    try:
        num_mun = int(float(s_mun))
        num_ent = int(float(s_ent))
        if num_mun <= 0 or num_ent < 1 or num_ent > 32:
            return "-1"
        return f"{num_ent:02d}{num_mun:03d}"
    except (ValueError, TypeError):
        return "-1"


def parse_enoe_indicators(enoe_path: str) -> Dict[str, float]:
    """
    Parsea el archivo CSV de Indicadores Estratégicos de la ENOE para una entidad.
    Extrae la Tasa de Participación Laboral (Tasa PEA) y la Tasa de Informalidad Laboral 1 (TIL1).
    """
    if not os.path.exists(enoe_path):
        raise FileNotFoundError(f"Archivo ENOE no encontrado: {enoe_path}")

    tasa_pea = None
    til_1 = None

    with open(enoe_path, mode='r', encoding='utf-8-sig', errors='ignore') as f:
        for line in f:
            parts = [p.strip().replace('"', '') for p in line.split(',')]
            # Buscar Tasa de participación
            if any("Tasa de participación" in p for p in parts):
                for val in parts:
                    try:
                        v = float(val)
                        if 30.0 <= v <= 90.0:  # Rango lógico de participación %
                            tasa_pea = v / 100.0
                            break
                    except ValueError:
                        continue
            # Buscar TIL1
            if any("Tasa de informalidad laboral 1" in p for p in parts) or any("TIL1" in p for p in parts):
                for val in parts:
                    try:
                        v = float(val)
                        if 10.0 <= v <= 90.0:  # Rango lógico de informalidad %
                            til_1 = v / 100.0
                            break
                    except ValueError:
                        continue

    return {
        "tasa_pea": tasa_pea if tasa_pea is not None else 0.62,
        "til_1": til_1 if til_1 is not None else 0.45
    }


def parse_ce2024_municipal(ce_path: str) -> Dict[str, Dict]:
    """
    Parsea el tabulado de los Censos Económicos 2024 (SAIC).
    Extrae el personal ocupado total (H001A) para el año más reciente (2023) por municipio.
    """
    if not os.path.exists(ce_path):
        return {}

    benchmarks = {}
    df = None
    for enc in ['utf-8-sig', 'utf-8', 'latin1', 'cp1252']:
        for skip in range(0, 8):
            try:
                temp_df = pd.read_csv(ce_path, encoding=enc, skiprows=skip, dtype=str)
                cols = [str(c).strip().replace('"', '') for c in temp_df.columns]
                has_h001a = any('H001A' in c or 'Personal' in c for c in cols)
                has_mun = any('Municipio' in c or 'municipio' in c for c in cols)
                if has_h001a and has_mun:
                    df = temp_df
                    df.columns = cols
                    break
            except Exception:
                continue
        if df is not None:
            break

    if df is None:
        return {}

    col_mun = [c for c in df.columns if 'Municipio' in c or 'municipio' in c][0]
    col_h001a = [c for c in df.columns if 'H001A' in c or 'Personal' in c][0]
    col_ent = ([c for c in df.columns if 'Entidad' in c or 'entidad' in c] + [None])[0]
    col_estrato = ([c for c in df.columns if 'Estrato' in c or 'estrato' in c] + [None])[0]
    col_anio = ([c for c in df.columns if any(k in c.lower() for k in ['año', 'a\ufffdo', 'anio', 'censal', 'year'])] + [None])[0]

    df_filtered = df
    if col_anio:
        years = [int(str(y).strip()) for y in df[col_anio].dropna().unique() if str(y).strip().isdigit()]
        if years:
            latest_year = str(max(years))
            df_filtered = df_filtered[df_filtered[col_anio].str.strip() == latest_year]

    if col_estrato:
        df_filtered = df_filtered[df_filtered[col_estrato].astype(str).str.contains('Suma|Total', case=False, na=False)]

    for _, row in df_filtered.iterrows():
        mun_str = str(row[col_mun]).strip()
        if not mun_str or mun_str.lower() == 'nan' or "total" in mun_str.lower():
            continue
        
        # Extraer cve_mun de la cadena (ej. "001 Cozumel" -> "001")
        tokens = mun_str.split(' ', 1)
        cve_mun_raw = tokens[0]
        nom_mun = tokens[1] if len(tokens) > 1 else mun_str

        # Extraer cve_ent si está disponible
        ent_str = str(row[col_ent]).strip() if col_ent else "00"
        cve_ent_raw = ent_str.split(' ', 1)[0]

        cve_5 = format_cve_mun(cve_mun_raw, cve_ent_raw)
        try:
            h001a_val = float(str(row[col_h001a]).replace(',', '').strip())
            if cve_5 != "-1" and h001a_val > 0:
                benchmarks[cve_5] = {
                    "nombre": nom_mun,
                    "empleos_ce": h001a_val
                }
        except ValueError:
            continue

    return benchmarks


def load_denue(denue_paths: Union[str, List[str]], bbox: Dict[str, float]) -> pd.DataFrame:
    """
    Carga e inicializa los registros del DENUE dentro del BBOX (soporta múltiples archivos para zonas multi-estado).
    Calcula empleos formales base por estrato y normaliza las claves espaciales.
    """
    if isinstance(denue_paths, str):
        paths = [denue_paths]
    else:
        paths = denue_paths

    dfs = []
    for path in paths:
        if not os.path.exists(path):
            continue
        df_temp = None
        for enc in ['utf-8-sig', 'latin1', 'utf-8', 'ISO-8859-1']:
            try:
                df_temp = pd.read_csv(path, encoding=enc, low_memory=False, dtype=str)
                df_temp.columns = [c.strip().lower() for c in df_temp.columns]
                dfs.append(df_temp)
                break
            except Exception:
                continue

    if not dfs:
        raise ValueError("No se pudo leer ningún archivo DENUE válido.")

    df_denue = pd.concat(dfs, ignore_index=True)

    df_denue['lat'] = pd.to_numeric(df_denue['latitud'], errors='coerce')
    df_denue['lon'] = pd.to_numeric(df_denue['longitud'], errors='coerce')
    
    # Filtro espacial estricto
    df_denue = df_denue[
        (df_denue['lon'] >= bbox["min_lon"]) & (df_denue['lon'] <= bbox["max_lon"]) &
        (df_denue['lat'] >= bbox["min_lat"]) & (df_denue['lat'] <= bbox["max_lat"])
    ].dropna(subset=['lat', 'lon']).copy()

    # Normalización de claves
    df_denue['cve_mun_clean'] = [
        format_cve_mun(m, e) for m, e in zip(df_denue['cve_mun'], df_denue['cve_ent'])
    ]

    col_per = 'per_ocu' if 'per_ocu' in df_denue.columns else 'personal_ocupado'
    df_denue['jobs_formal'] = df_denue[col_per].map(DENUE_ESTRATOS).fillna(2.24)
    
    # Clasificación de tamaño de empresa (Micro/Pequeño vs Grande)
    df_denue['is_micro_small'] = df_denue[col_per].isin(["0 a 5 personas", "6 a 10 personas", "11 a 30 personas", "31 a 50 personas"])

    # Normalizar AGEB y Manzana
    df_denue['ageb_clean'] = df_denue['ageb'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
    df_denue['mza_clean'] = pd.to_numeric(df_denue['manzana'], errors='coerce').fillna(-1).astype(int).astype(str)

    return df_denue


def calibrate_denue_employment(
    df_denue: pd.DataFrame,
    ce_benchmarks: Dict[str, Dict],
    til_1: float,
    min_sample_threshold: int = 500
) -> Tuple[pd.DataFrame, Dict[str, Dict]]:
    """
    Calibración asimétrica de empleo:
    Ajusta el empleo de micro/pequeños negocios para igualar el control municipal CE 2024 H001A,
    respetando el empleo de grandes empresas y acotando el factor según la tasa de informalidad ENOE.
    """
    audit_report = {}
    df_denue = df_denue.copy()
    df_denue['calibrated_jobs'] = df_denue['jobs_formal']

    municipalities = [m for m in df_denue['cve_mun_clean'].unique() if m != "-1"]

    for cve_mun in municipalities:
        mask_mun = df_denue['cve_mun_clean'] == cve_mun
        jobs_formal_total = df_denue.loc[mask_mun, 'jobs_formal'].sum()
        
        if cve_mun not in ce_benchmarks or jobs_formal_total < min_sample_threshold:
            # Fallback a tasa de informalidad estatal 1 + TIL1
            default_factor = 1.0 + til_1
            df_denue.loc[mask_mun, 'calibrated_jobs'] = df_denue.loc[mask_mun, 'jobs_formal'] * default_factor
            audit_report[cve_mun] = {
                "nombre": ce_benchmarks.get(cve_mun, {}).get("nombre", "Desconocido"),
                "jobs_formal": jobs_formal_total,
                "h001a": ce_benchmarks.get(cve_mun, {}).get("empleos_ce", 0),
                "factor": default_factor,
                "status": "FALLBACK_ESTATAL",
                "notes": f"Muestra baja (< {min_sample_threshold}) o sin benchmark CE2024"
            }
            continue

        h001a = ce_benchmarks[cve_mun]["empleos_ce"]
        
        # Separar empleo grande y micro
        mask_micro = mask_mun & (df_denue['is_micro_small'])
        mask_large = mask_mun & (~df_denue['is_micro_small'])

        jobs_micro = df_denue.loc[mask_micro, 'jobs_formal'].sum()
        jobs_large = df_denue.loc[mask_large, 'jobs_formal'].sum()

        techo_teorico = 1.0 / max(0.01, (1.0 - til_1))
        
        if jobs_micro > 0 and h001a > jobs_large:
            factor_micro = (h001a - jobs_large) / jobs_micro
            factor_clamped = float(np.clip(factor_micro, 1.0, techo_teorico))
            status = "CALIBRADO" if 1.0 <= factor_micro <= techo_teorico else ("CLAMPED_TECHO" if factor_micro > techo_teorico else "CLAMPED_PISO")
        else:
            factor_clamped = 1.0 + til_1
            status = "AJUSTE_GLOBAL"

        df_denue.loc[mask_micro, 'calibrated_jobs'] = df_denue.loc[mask_micro, 'jobs_formal'] * factor_clamped
        df_denue.loc[mask_large, 'calibrated_jobs'] = df_denue.loc[mask_large, 'jobs_formal']  # Grandes no se inflan

        audit_report[cve_mun] = {
            "nombre": ce_benchmarks[cve_mun]["nombre"],
            "jobs_formal": jobs_formal_total,
            "h001a": h001a,
            "factor": factor_clamped,
            "status": status,
            "notes": f"Calibrado asimétrico (Micro factor: {factor_clamped:.3f})"
        }

    return df_denue, audit_report


def load_cpv_demography(
    cpv_paths: Union[str, List[str]],
    df_denue: pd.DataFrame,
    bbox: Dict[str, float],
    tasa_pea: float,
    growth_factors: Optional[Dict[str, float]] = None,
    default_growth: float = 1.0
) -> pd.DataFrame:
    """
    Carga e imputa georreferenciación de población (CPV 2020) por manzana.
    Aplica tasa PEA y resuelve coordenadas mediante cruce jerárquico con el DENUE.
    Soporta múltiples archivos para zonas metropolitanas multi-estado.
    """
    if isinstance(cpv_paths, str):
        paths = [cpv_paths]
    else:
        paths = cpv_paths

    dfs = []
    for path in paths:
        if not os.path.exists(path):
            continue
        df_temp = None
        for enc in ['utf-8-sig', 'latin1', 'utf-8']:
            try:
                if path.endswith('.xlsx') or path.endswith('.xls'):
                    df_temp = pd.read_excel(path, dtype=str)
                else:
                    df_temp = pd.read_csv(path, encoding=enc, low_memory=False, dtype=str)
                df_temp.columns = [c.strip().upper() for c in df_temp.columns]
                dfs.append(df_temp)
                break
            except Exception:
                continue

    if not dfs:
        raise ValueError("No se pudo cargar ningún archivo censal válido.")

    df_censo = pd.concat(dfs, ignore_index=True)

    # Normalizar nombres de columnas requeridas
    req = ['ENTIDAD', 'MUN', 'POBTOT', 'P_15YMAS', 'AGEB', 'MZA']
    for col in req:
        if col not in df_censo.columns:
            raise KeyError(f"Columna censal faltante: {col}")

    # Filtrar solo manzanas habitadas reales (MZA > 0 y POBTOT > 0)
    df_censo['mza_num'] = pd.to_numeric(df_censo['MZA'].replace('*', '1'), errors='coerce').fillna(0)
    df_censo['pobtot_num'] = pd.to_numeric(df_censo['POBTOT'].replace('*', '1.5'), errors='coerce').fillna(0)
    df_censo['pob15_num'] = pd.to_numeric(df_censo['P_15YMAS'].replace('*', '1.0'), errors='coerce').fillna(0)

    df_censo = df_censo[(df_censo['mza_num'] > 0) & (df_censo['pobtot_num'] > 0)].copy()

    # Normalizar códigos geográficos
    df_censo['cve_mun_clean'] = [
        format_cve_mun(m, e) for m, e in zip(df_censo['MUN'], df_censo['ENTIDAD'])
    ]
    df_censo['ageb_clean'] = df_censo['AGEB'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
    df_censo['mza_clean'] = df_censo['mza_num'].astype(int).astype(str)

    # Factores de proyección poblacional
    growth_dict = growth_factors or {}
    df_censo['growth'] = df_censo['cve_mun_clean'].map(growth_dict).fillna(default_growth)
    df_censo['pobtot_adj'] = df_censo['pobtot_num'] * df_censo['growth']
    df_censo['pob15_adj'] = df_censo['pob15_num'] * df_censo['growth']
    df_censo['pea_real'] = df_censo['pob15_adj'] * tasa_pea

    # ==========================================
    # Georreferenciación Jerárquica vía DENUE
    # ==========================================
    # Nivel 1: Centroide de Manzana
    mza_coords = df_denue.groupby(['cve_mun_clean', 'ageb_clean', 'mza_clean'])[['lon', 'lat']].mean().reset_index()
    # Nivel 2: Centroide de AGEB
    ageb_coords = df_denue.groupby(['cve_mun_clean', 'ageb_clean'])[['lon', 'lat']].mean().reset_index().rename(
        columns={'lon': 'lon_ageb', 'lat': 'lat_ageb'}
    )

    df_geo = pd.merge(df_censo, mza_coords, on=['cve_mun_clean', 'ageb_clean', 'mza_clean'], how='left')
    df_geo = pd.merge(df_geo, ageb_coords, on=['cve_mun_clean', 'ageb_clean'], how='left')
    
    # Imputar nivel 1 -> nivel 2
    df_geo['lon'] = df_geo['lon'].fillna(df_geo['lon_ageb'])
    df_geo['lat'] = df_geo['lat'].fillna(df_geo['lat_ageb'])

    # Nivel 3: Si aún hay nulos, fallback a centro de BBOX o descarte seguro
    df_geo = df_geo[
        (df_geo['lon'] >= bbox["min_lon"]) & (df_geo['lon'] <= bbox["max_lon"]) &
        (df_geo['lat'] >= bbox["min_lat"]) & (df_geo['lat'] <= bbox["max_lat"])
    ].dropna(subset=['lon', 'lat']).copy()

    return df_geo
