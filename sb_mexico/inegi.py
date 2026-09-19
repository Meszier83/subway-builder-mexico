"""
sb_mexico.inegi
===============
Módulo de ingesta, normalización, georreferenciación y calibración
de fuentes estadísticas oficiales del INEGI (CPV 2020, DENUE, CE 2024, ENOE).
"""

import os
import csv
import glob
import math
import unicodedata
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional, Union, Any

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


def format_cve_mun(cve_mun_raw, cve_ent_raw=None) -> str:
    """
    Homologa claves municipales y estatales al estándar INEGI de 5 dígitos (EEMMM).
    Soporta:
    - Claves municipales de 5 dígitos completas (ej. '23005' -> '23005', '23005.0' -> '23005')
    - Claves municipales de 4 dígitos (ej. '1001' -> '01001', '1001.0' -> '01001')
    - Claves separadas municipio + entidad (ej. '005', '23' -> '23005' o '5', '1' -> '01005')
    """
    if cve_mun_raw is None:
        return "-1"
    s_mun = str(cve_mun_raw).strip()
    if not s_mun or s_mun.lower() in ('nan', 'none', '-1'):
        return "-1"

    # Normalizar si viene como representación flotante (ej. '23005.0' o '1001.0')
    if s_mun.endswith('.0'):
        s_mun = s_mun[:-2].strip()

    # Caso 1: Si s_mun ya contiene la clave completa de 4 o 5 dígitos numéricos
    if s_mun.isdigit() and len(s_mun) in (4, 5):
        try:
            num = int(s_mun)
            ent = num // 1000
            mun = num % 1000
            if 1 <= ent <= 32 and mun >= 1:
                return f"{num:05d}"
        except (ValueError, TypeError):
            pass

    # Caso 2: Combinar municipio con clave de entidad
    s_ent = str(cve_ent_raw).strip() if cve_ent_raw is not None else "00"
    if s_ent.endswith('.0'):
        s_ent = s_ent[:-2].strip()
    s_ent = s_ent.zfill(2)

    if not s_ent or s_ent == '00' or s_ent.lower() in ('nan', 'none'):
        return "-1"
    try:
        num_mun = int(float(s_mun))
        num_ent = int(float(s_ent))
        if num_mun <= 0 or num_ent < 1 or num_ent > 32:
            return "-1"
        # Si num_mun ya contiene la clave completa de 5 dígitos coincidente con la entidad
        if num_mun >= 1000 and (num_mun // 1000) == num_ent:
            return f"{num_mun:05d}"
        return f"{num_ent:02d}{num_mun:03d}"
    except (ValueError, TypeError):
        return "-1"


STATE_MACRO_BENCHMARKS: Dict[str, Dict[str, Any]] = {
    "01": {"nombre": "Aguascalientes", "tasa_pea": 0.628, "til_1": 0.395},
    "02": {"nombre": "Baja California", "tasa_pea": 0.645, "til_1": 0.375},
    "03": {"nombre": "Baja California Sur", "tasa_pea": 0.682, "til_1": 0.380},
    "04": {"nombre": "Campeche", "tasa_pea": 0.625, "til_1": 0.620},
    "05": {"nombre": "Coahuila", "tasa_pea": 0.620, "til_1": 0.355},
    "06": {"nombre": "Colima", "tasa_pea": 0.665, "til_1": 0.505},
    "07": {"nombre": "Chiapas", "tasa_pea": 0.565, "til_1": 0.765},
    "08": {"nombre": "Chihuahua", "tasa_pea": 0.635, "til_1": 0.345},
    "09": {"nombre": "Ciudad de México", "tasa_pea": 0.615, "til_1": 0.465},
    "10": {"nombre": "Durango", "tasa_pea": 0.605, "til_1": 0.505},
    "11": {"nombre": "Guanajuato", "tasa_pea": 0.615, "til_1": 0.535},
    "12": {"nombre": "Guerrero", "tasa_pea": 0.605, "til_1": 0.775},
    "13": {"nombre": "Hidalgo", "tasa_pea": 0.635, "til_1": 0.715},
    "14": {"nombre": "Jalisco", "tasa_pea": 0.635, "til_1": 0.470},
    "15": {"nombre": "México", "tasa_pea": 0.605, "til_1": 0.560},
    "16": {"nombre": "Michoacán", "tasa_pea": 0.615, "til_1": 0.675},
    "17": {"nombre": "Morelos", "tasa_pea": 0.615, "til_1": 0.655},
    "18": {"nombre": "Nayarit", "tasa_pea": 0.655, "til_1": 0.615},
    "19": {"nombre": "Nuevo León", "tasa_pea": 0.625, "til_1": 0.360},
    "20": {"nombre": "Oaxaca", "tasa_pea": 0.625, "til_1": 0.805},
    "21": {"nombre": "Puebla", "tasa_pea": 0.635, "til_1": 0.695},
    "22": {"nombre": "Querétaro", "tasa_pea": 0.625, "til_1": 0.425},
    "23": {"nombre": "Quintana Roo", "tasa_pea": 0.665, "til_1": 0.455},
    "24": {"nombre": "San Luis Potosí", "tasa_pea": 0.615, "til_1": 0.555},
    "25": {"nombre": "Sinaloa", "tasa_pea": 0.615, "til_1": 0.485},
    "26": {"nombre": "Sonora", "tasa_pea": 0.625, "til_1": 0.415},
    "27": {"nombre": "Tabasco", "tasa_pea": 0.605, "til_1": 0.615},
    "28": {"nombre": "Tamaulipas", "tasa_pea": 0.615, "til_1": 0.425},
    "29": {"nombre": "Tlaxcala", "tasa_pea": 0.645, "til_1": 0.705},
    "30": {"nombre": "Veracruz", "tasa_pea": 0.585, "til_1": 0.675},
    "31": {"nombre": "Yucatán", "tasa_pea": 0.645, "til_1": 0.585},
    "32": {"nombre": "Zacatecas", "tasa_pea": 0.595, "til_1": 0.595},
}


def parse_enoe_indicators(enoe_path: str, intended_period: Optional[str] = None) -> Dict[str, Any]:
    """
    Parsea el archivo (CSV o XLS/XLSX) de Indicadores Estratégicos de la ENOE para una entidad.
    Extrae la Tasa de Participación Laboral (Tasa PEA) y la Tasa de Informalidad Laboral 1 (TIL1).
    Maneja separadores de coma o punto decimal y múltiples codificaciones.
    """
    if not os.path.exists(enoe_path):
        raise FileNotFoundError(f"Archivo ENOE no encontrado: {enoe_path}")

    tasa_pea = None
    til_1 = None
    selected_period = None

    def normalize_period(value: Any) -> Optional[str]:
        text = str(value).strip().upper().replace("_", "-").replace(" ", "-")
        import re
        match = re.search(r"(20\d{2})-?(?:T|Q|TRIMESTRE)?-?([1-4])$", text)
        if match:
            return f"{match.group(1)}-T{match.group(2)}"
        return str(int(float(text))) if text.replace('.', '', 1).isdigit() and float(text).is_integer() and 2000 <= float(text) <= 2100 else None

    normalized_intended = normalize_period(intended_period) if intended_period is not None else None

    # Structured tables must select a unique, explicit period. This prevents a
    # left-to-right numeric scan from silently choosing an obsolete quarter.
    try:
        if enoe_path.lower().endswith(('.xls', '.xlsx')):
            table = pd.read_excel(enoe_path, dtype=str)
        else:
            table = None
            for enc in ['utf-8-sig', 'utf-8', 'latin1', 'cp1252']:
                try:
                    table = pd.read_csv(enoe_path, encoding=enc, dtype=str)
                    break
                except Exception:
                    continue
        if table is not None and not table.empty:
            period_columns = [(column, normalize_period(column)) for column in table.columns]
            period_columns = [(column, period) for column, period in period_columns if period]
            if period_columns:
                if normalized_intended:
                    matches = [(column, period) for column, period in period_columns if period == normalized_intended]
                    if len(matches) != 1:
                        available = ", ".join(period for _, period in period_columns)
                        raise ValueError(f"ENOE period {normalized_intended} not uniquely available; found: {available}")
                    value_column, selected_period = matches[0]
                elif len(period_columns) == 1:
                    value_column, selected_period = period_columns[0]
                else:
                    available = ", ".join(period for _, period in period_columns)
                    raise ValueError(f"Ambiguous ENOE periods ({available}); configure temporal.enoe_period")
                label_column = table.columns[0]
                for _, row in table.iterrows():
                    label = str(row[label_column]).lower()
                    raw = str(row[value_column]).strip().replace(',', '.')
                    try:
                        value = float(raw) / 100.0
                    except ValueError:
                        continue
                    if "tasa de participaci" in label or "participacion" in label:
                        tasa_pea = round(value, 4)
                    elif "informalidad laboral 1" in label or "til1" in label:
                        til_1 = round(value, 4)
                if tasa_pea is None or til_1 is None:
                    raise ValueError(f"ENOE period {selected_period} lacks required participation/TIL1 indicators")
                return {"tasa_pea": tasa_pea, "til_1": til_1, "period": selected_period, "used_fallback": False}
            if normalized_intended:
                raise ValueError(
                    f"ENOE file does not expose a column for configured period {normalized_intended}"
                )
    except ValueError:
        raise
    except Exception:
        pass

    # Caso 1: Archivo Excel .xls o .xlsx
    lower_path = enoe_path.lower()
    if lower_path.endswith(('.xls', '.xlsx')):
        try:
            import xlrd
            wb = xlrd.open_workbook(enoe_path)
            sheet = wb.sheet_by_index(0)
            for r in range(sheet.nrows):
                row_vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
                row_str = " ".join(str(v) for v in row_vals).lower()
                if ("tasa de participaci" in row_str or "participacion" in row_str) and tasa_pea is None:
                    for v in row_vals:
                        try:
                            num = float(v)
                            if 30.0 <= num <= 90.0:
                                tasa_pea = round(num / 100.0, 4)
                                break
                        except (ValueError, TypeError):
                            continue
                if ("informalidad laboral 1" in row_str or "til1" in row_str) and til_1 is None:
                    for v in row_vals:
                        try:
                            num = float(v)
                            if 10.0 <= num <= 90.0:
                                til_1 = round(num / 100.0, 4)
                                break
                        except (ValueError, TypeError):
                            continue
        except Exception:
            pass

    # Caso 2: Archivo CSV (o respaldo si XLS no arrojó ambos valores)
    if tasa_pea is None or til_1 is None:
        encodings = ['utf-8-sig', 'utf-8', 'latin1', 'cp1252']
        for enc in encodings:
            try:
                with open(enoe_path, mode='r', encoding=enc, errors='ignore') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row:
                            continue
                        row_str = " ".join(row).lower()
                        # Buscar Tasa de participación (tolerante a acentos y codificación)
                        if ("tasa de participaci" in row_str or "participacion" in row_str) and tasa_pea is None:
                            for val in row:
                                clean_val = str(val).strip().replace(',', '.')
                                try:
                                    v = float(clean_val)
                                    if 30.0 <= v <= 90.0:
                                        tasa_pea = round(v / 100.0, 4)
                                        break
                                except ValueError:
                                    continue
                        # Buscar TIL1
                        if ("informalidad laboral 1" in row_str or "til1" in row_str) and til_1 is None:
                            for val in row:
                                clean_val = str(val).strip().replace(',', '.')
                                try:
                                    v = float(clean_val)
                                    if 10.0 <= v <= 90.0:
                                        til_1 = round(v / 100.0, 4)
                                        break
                                except ValueError:
                                    continue
                if tasa_pea is not None and til_1 is not None:
                    break
            except Exception:
                continue

    return {
        "tasa_pea": tasa_pea if tasa_pea is not None else 0.62,
        "til_1": til_1 if til_1 is not None else 0.45,
        "period": selected_period,
        "used_fallback": tasa_pea is None or til_1 is None,
    }


def calculate_cpv_pea_rate(cpv_path: str, target_cve_muns: Optional[List[str]] = None) -> Optional[float]:
    """
    Calcula la Tasa PEA real (PEA / P_15YMAS) a partir del archivo RESAGEBURB del Censo CPV 2020.
    Si se especifican target_cve_muns, filtra los registros municipales correspondientes al BBOX.
    Si target_cve_muns es None, calcula la tasa estatal ponderada.
    """
    if not os.path.exists(cpv_path):
        return None

    total_pea = 0.0
    total_p15 = 0.0

    mun_set = set()
    if target_cve_muns:
        for m in target_cve_muns:
            s = str(m).strip()
            mun_set.add(s)
            if len(s) == 5:
                mun_set.add(s[2:])  # Código de 3 dígitos del municipio

    for enc in ['utf-8-sig', 'utf-8', 'latin1', 'cp1252']:
        try:
            with open(cpv_path, mode='r', encoding=enc, errors='ignore') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    continue
                clean_header = [str(c).strip().replace('"', '') for c in header]
                if 'MUN' not in clean_header or 'PEA' not in clean_header or 'P_15YMAS' not in clean_header:
                    continue

                mun_idx = clean_header.index('MUN')
                loc_idx = clean_header.index('LOC') if 'LOC' in clean_header else -1
                pea_idx = clean_header.index('PEA')
                p15_idx = clean_header.index('P_15YMAS')
                ent_idx = clean_header.index('ENTIDAD') if 'ENTIDAD' in clean_header else 0

                for row in reader:
                    if not row or len(row) <= max(pea_idx, p15_idx):
                        continue
                    mun = row[mun_idx].strip()
                    loc = row[loc_idx].strip() if loc_idx >= 0 else ""
                    ent = row[ent_idx].strip()

                    # El registro municipal oficial tiene LOC = '0000'
                    if loc == '0000':
                        # Si no hay filtro de municipios, excluir '000' para no duplicar el total estatal con los municipios
                        if not mun_set and (mun == '000' or mun == '0'):
                            continue
                        cve_5 = f"{int(ent):02d}{int(mun):03d}" if ent.isdigit() and mun.isdigit() else mun
                        if not mun_set or mun in mun_set or cve_5 in mun_set:
                            try:
                                pea_val = float(str(row[pea_idx]).replace(',', '').strip())
                                p15_val = float(str(row[p15_idx]).replace(',', '').strip())
                                if p15_val > 0:
                                    total_pea += pea_val
                                    total_p15 += p15_val
                            except (ValueError, TypeError):
                                pass

                if total_p15 > 0:
                    return round(total_pea / total_p15, 4)
        except Exception:
            continue

    return None



def parse_ce2024_municipal(ce_path: str) -> Dict[str, Dict]:
    """
    Parsea el tabulado de los Censos Económicos 2024 (SAIC o Microdatos tr_ce).
    Extrae el personal ocupado total (H001A) por municipio.
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
                has_mun = any('Municipio' in c or 'municipio' in c or c == 'E04' for c in cols)
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

    # Formato A: Tabulado de Microdatos Oficial INEGI (E03 = Entidad, E04 = Municipio, H001A = Empleos)
    if 'E04' in df.columns and any('H001A' in c for c in df.columns):
        if 'E03' not in df.columns:
            raise ValueError("CE municipal format requires E03 state code; refusing to assume a state")
        col_h001a = [c for c in df.columns if 'H001A' in c][0]
        df_valid = df[df['E04'].notna()].copy()
        
        # Filtrar a totales de sector
        if 'CODIGO' in df_valid.columns:
            df_valid = df_valid[df_valid['CODIGO'].astype(str).str.contains('TOTAL', case=False, na=False)]
        
        # Filtrar a nivel agregado municipal (ID_ESTRATO nulo o total)
        if 'ID_ESTRATO' in df_valid.columns:
            df_valid = df_valid[df_valid['ID_ESTRATO'].isna() | df_valid['ID_ESTRATO'].astype(str).str.strip().isin(['', 'nan', '99'])]

        for _, row in df_valid.iterrows():
            e04_str = str(row['E04']).strip()
            if not e04_str.isdigit():
                continue
            e03_str = str(row['E03']).strip()
            cve_5 = f"{int(e03_str):02d}{int(e04_str):03d}"
            try:
                h001a_val = float(str(row[col_h001a]).replace(',', '').strip())
            except (ValueError, TypeError):
                continue
            if h001a_val > 0:
                if cve_5 in benchmarks:
                    raise ValueError(f"Duplicate CE municipal benchmark for {cve_5}")
                benchmarks[cve_5] = {
                    "nombre": f"Municipio {cve_5}",
                    "empleos_ce": h001a_val
                }
        return benchmarks

    # Formato B: Consulta Exportada de SAIC
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


def parse_conapo_projections(
    conapo_path: str,
    target_year: int,
    as_growth_factors: bool = False,
    base_year: int = 2020,
    return_metadata: bool = False,
    population_column: Optional[str] = None,
    source_year: Optional[int] = None,
) -> Union[Dict[str, float], Dict[str, Any]]:
    """
    Parsea las proyecciones oficiales de población municipal de CONAPO:
    - Archivos anuales simples (data-*.csv o *conapo*.csv)
    - Archivos quinquenales completos multianuales (pobproy_quinq1.csv, *pobproy*.csv)
    Si as_growth_factors=True y el archivo contiene el año 2020, calcula directamente
    el ratio homogéneo: POB_CONAPO(target_year) / POB_CONAPO(2020).
    Retorna un diccionario {cve_mun: poblacion_proyectada_o_ratio}.
    """
    if not os.path.exists(conapo_path):
        return {}

    for enc in ['utf-8-sig', 'latin1', 'utf-8', 'cp1252']:
        try:
            df = pd.read_csv(conapo_path, encoding=enc, low_memory=False)
            cols = [str(c).strip().upper() for c in df.columns]
            df.columns = cols

            if 'CLAVE' in cols and any('POB' in c for c in cols):
                if population_column:
                    col_pob = str(population_column).strip().upper()
                    if col_pob not in cols:
                        raise ValueError(f"Configured CONAPO population column {col_pob} is unavailable")
                else:
                    preferred = [c for c in ['POB_MIT_MUN', 'POB_TOTAL', 'POBTOT'] if c in cols]
                    pob_candidates = preferred or [c for c in cols if c.startswith('POB')]
                    if len(pob_candidates) != 1:
                        raise ValueError(
                            f"Ambiguous CONAPO population columns {pob_candidates}; "
                            "configure temporal.conapo_population_column"
                        )
                    col_pob = pob_candidates[0]

                df['cve_clean'] = pd.to_numeric(df['CLAVE'], errors='coerce').fillna(0).astype(int)
                df = df[df['cve_clean'] > 0]
                df['pob_clean'] = pd.to_numeric(df[col_pob].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

                # Filtrar año si el archivo contiene desglose temporal multianual
                if 'ANO' in cols:
                    df['ANO_num'] = pd.to_numeric(df['ANO'], errors='coerce')
                    available_years = df['ANO_num'].dropna().unique()
                    
                    available_years = sorted(int(year) for year in available_years)
                    if target_year not in available_years:
                        raise ValueError(f"CONAPO target year {target_year} unavailable; found {available_years}")
                    if as_growth_factors:
                        if base_year not in available_years:
                            raise ValueError(f"CONAPO base year {base_year} unavailable; found {available_years}")
                        chosen_year = target_year
                        df_target = df[df['ANO_num'] == chosen_year].groupby('cve_clean')['pob_clean'].sum()
                        df_2020 = df[df['ANO_num'] == base_year].groupby('cve_clean')['pob_clean'].sum()
                        ratios = {}
                        for cve, val_target in df_target.items():
                            val_base = df_2020.get(cve, 0)
                            if val_base > 0 and val_target > 0:
                                ratios[f"{cve:05d}"] = round(float(val_target / val_base), 4)
                        if ratios:
                            return ({
                                "values": ratios,
                                "base_year": base_year,
                                "target_year": target_year,
                                "population_column": col_pob,
                                "year_provenance": "ANO column",
                            } if return_metadata else ratios)

                    if len(available_years) > 0:
                        chosen_year = target_year
                        df = df[df['ANO_num'] == chosen_year]
                    year_provenance = "ANO column"
                else:
                    if source_year is None:
                        raise ValueError(
                            "Single-year CONAPO source without ANO requires explicit trusted source_year provenance"
                        )
                    if int(source_year) != int(target_year):
                        raise ValueError(
                            f"CONAPO source-year provenance {source_year} does not match target year {target_year}"
                        )
                    if as_growth_factors:
                        raise ValueError(
                            "Single-year CONAPO source cannot establish both base and target years for growth factors"
                        )
                    year_provenance = "explicit source_year configuration"

                grouped = df.groupby('cve_clean')['pob_clean'].sum()
                projections = {f"{cve:05d}": float(val) for cve, val in grouped.items() if val > 0}
                if projections:
                    return ({
                        "values": projections,
                        "base_year": None,
                        "target_year": target_year,
                        "population_column": col_pob,
                        "year_provenance": year_provenance,
                    } if return_metadata else projections)
        except ValueError:
            raise
        except Exception:
            continue
    return {}


def parse_conapo_growth_factors(conapo_path: str, target_year: int, base_year: int = 2020) -> Dict[str, float]:
    """Helper de conveniencia para derivar factores de crecimiento intercensal CONAPO (base 2020)."""
    return parse_conapo_projections(conapo_path, target_year=target_year, base_year=base_year, as_growth_factors=True)


def load_denue(denue_paths: Union[str, List[str]], bbox: Dict[str, float]) -> pd.DataFrame:
    """
    Carga e inicializa los registros del DENUE dentro del BBOX (soporta múltiples archivos para zonas multi-estado).
    Calcula empleos formales base por estrato y normaliza las claves espaciales.
    Almacena en df_denue.attrs['mun_totals_global'] la suma global de empleos por municipio
    antes del recorte BBOX para permitir calibración proporcional exacta.
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
                df_temp['_source_path'] = os.path.abspath(path)
                dfs.append(df_temp)
                break
            except Exception:
                continue

    if not dfs:
        raise ValueError("No se pudo leer ningún archivo DENUE válido.")

    df_denue = pd.concat(dfs, ignore_index=True)
    input_rows = len(df_denue)

    def normalize_text(value: Any) -> str:
        if value is None or pd.isna(value):
            return ''
        text = unicodedata.normalize('NFKC', str(value)).strip().casefold()
        return ' '.join(text.split()) if text not in {'nan', 'none', 'null'} else ''

    id_columns = [c for c in ['clee', 'id', 'id_ue', 'id_establecimiento'] if c in df_denue.columns]
    identity_name = id_columns[0] if id_columns else 'composite'
    if id_columns:
        primary_identity = df_denue[id_columns[0]].map(normalize_text).str.replace(r'[^a-z0-9]', '', regex=True)
    else:
        primary_identity = pd.Series('', index=df_denue.index, dtype=str)
    valid_identity = primary_identity.ne('')

    composite_columns = [
        c for c in [
            'cve_ent', 'cve_mun', 'ageb', 'manzana', 'longitud', 'latitud',
            'nom_estab', 'codigo_act', 'nom_vial', 'numero_ext', 'num_ext'
        ] if c in df_denue.columns
    ]
    required_fallback = {'cve_mun', 'longitud', 'latitud', 'nom_estab'}
    if not required_fallback.issubset(composite_columns):
        missing_blank = (~valid_identity).sum()
        if missing_blank:
            raise ValueError(
                f"DENUE has {int(missing_blank)} blank identifiers and lacks fallback identity fields "
                f"{sorted(required_fallback - set(composite_columns))}"
            )

    composite_parts = []
    for column in composite_columns:
        if column in {'longitud', 'latitud'}:
            values = pd.to_numeric(df_denue[column], errors='coerce').map(
                lambda value: f"{value:.6f}" if pd.notna(value) else ''
            )
        else:
            values = df_denue[column].map(normalize_text)
        composite_parts.append(values)
    composite_identity = composite_parts[0] if composite_parts else pd.Series('', index=df_denue.index, dtype=str)
    for part in composite_parts[1:]:
        composite_identity = composite_identity + '|' + part

    blank_identity = ~valid_identity
    insufficient_blank = blank_identity & (
        df_denue['cve_mun'].map(normalize_text).eq('') |
        pd.to_numeric(df_denue['longitud'], errors='coerce').isna() |
        pd.to_numeric(df_denue['latitud'], errors='coerce').isna() |
        df_denue['nom_estab'].map(normalize_text).eq('')
    ) if blank_identity.any() else pd.Series(False, index=df_denue.index)
    if insufficient_blank.any():
        raise ValueError(f"DENUE has {int(insufficient_blank.sum())} blank identifiers without a usable composite identity")

    duplicate_primary = valid_identity & primary_identity.duplicated(keep=False)
    duplicate_blank = blank_identity & composite_identity.duplicated(keep=False)
    duplicate_blank_vs_primary = blank_identity & composite_identity.isin(set(composite_identity[valid_identity]))
    duplicate_mask = duplicate_primary | duplicate_blank | duplicate_blank_vs_primary
    if duplicate_mask.any():
        sample = df_denue.loc[duplicate_mask, '_source_path'].head(5).tolist()
        raise ValueError(
            f"Duplicate DENUE logical records using {identity_name}/normalized composite identity: "
            f"{int(duplicate_mask.sum())} rows; sources={sample}"
        )
    df_denue['_logical_identity'] = np.where(
        valid_identity,
        'id:' + primary_identity,
        'composite:' + composite_identity,
    )

    df_denue['lat'] = pd.to_numeric(df_denue['latitud'], errors='coerce')
    df_denue['lon'] = pd.to_numeric(df_denue['longitud'], errors='coerce')

    # Normalización de claves antes del recorte BBOX
    df_denue['cve_mun_clean'] = [
        format_cve_mun(m, e) for m, e in zip(df_denue['cve_mun'], df_denue['cve_ent'])
    ]

    col_per = 'per_ocu' if 'per_ocu' in df_denue.columns else 'personal_ocupado'
    df_denue[col_per] = df_denue[col_per].astype(str).str.strip()
    unknown_strata = sorted(set(df_denue.loc[~df_denue[col_per].isin(DENUE_ESTRATOS), col_per].tolist()))
    if unknown_strata:
        raise ValueError(f"Unknown DENUE employment strata: {unknown_strata[:10]}")
    df_denue['jobs_formal'] = df_denue[col_per].map(DENUE_ESTRATOS)
    df_denue['is_micro_small'] = df_denue[col_per].isin(["0 a 5 personas", "6 a 10 personas", "11 a 30 personas", "31 a 50 personas"])

    # Totales globales de empleo formal por municipio (antes del recorte BBOX)
    mun_totals_global = df_denue[df_denue['cve_mun_clean'] != "-1"].groupby('cve_mun_clean')['jobs_formal'].sum().to_dict()

    # Filtro espacial estricto dentro del BBOX
    df_denue = df_denue[
        (df_denue['lon'] >= bbox["min_lon"]) & (df_denue['lon'] <= bbox["max_lon"]) &
        (df_denue['lat'] >= bbox["min_lat"]) & (df_denue['lat'] <= bbox["max_lat"])
    ].dropna(subset=['lat', 'lon']).copy()

    df_denue.attrs['mun_totals_global'] = mun_totals_global
    df_denue.attrs['source_paths'] = sorted(os.path.abspath(path) for path in paths if os.path.exists(path))
    df_denue.attrs['bbox'] = dict(bbox)
    df_denue.attrs['ingestion_diagnostics'] = {
        'input_rows': input_rows,
        'unique_logical_records': input_rows,
        'duplicates_rejected': 0,
        'logical_key': identity_name,
        'bbox_rows': len(df_denue),
    }

    # Normalizar AGEB y Manzana
    df_denue['ageb_clean'] = df_denue['ageb'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
    df_denue['mza_clean'] = pd.to_numeric(df_denue['manzana'], errors='coerce').fillna(-1).astype(int).astype(str)

    return df_denue


def calibrate_denue_employment(
    df_denue: pd.DataFrame,
    ce_benchmarks: Dict[str, Dict],
    til_1: float,
    min_sample_threshold: int = 500,
    mun_totals_global: Optional[Dict[str, float]] = None,
    coverage_complete: bool = False,
    denominator_contract: Optional[Dict[str, Dict[str, Any]]] = None,
    denue_vintage: Optional[int] = None,
) -> Tuple[pd.DataFrame, Dict[str, Dict]]:
    """
    Calibración asimétrica de empleo con proporción territorial BBOX:
    Ajusta el empleo de micro/pequeños negocios para igualar el control municipal CE 2024 H001A,
    escalando proporcionalmente por la cuota del municipio que cae dentro del BBOX (share_bbox).
    Evita sobreinflar áreas urbanas con el empleo total de municipios con amplia extensión rural.
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
                "share_bbox": 1.0,
                "factor": default_factor,
                "status": "FALLBACK_ESTATAL",
                "notes": f"Muestra baja (< {min_sample_threshold}) o sin benchmark CE2024"
            }
            continue

        h001a_raw = ce_benchmarks[cve_mun]["empleos_ce"]

        contract = (denominator_contract or {}).get(cve_mun)
        if not contract:
            raise ValueError(
                f"Missing validated DENUE municipal denominator contract for {cve_mun}"
            )

        contract_municipality = str(contract.get('municipality', cve_mun)).strip()
        if contract_municipality != cve_mun:
            raise ValueError(
                f"DENUE denominator municipality mismatch: calibration={cve_mun}, contract={contract_municipality}"
            )
        denominator_source = str(contract.get('source', '')).strip()
        coverage_basis = str(contract.get('coverage_basis', '')).strip()
        contract_vintage = contract.get('vintage')
        if not denominator_source or not coverage_basis or contract_vintage is None:
            raise ValueError(
                f"DENUE denominator contract for {cve_mun} requires source, vintage, and coverage_basis"
            )
        if coverage_basis != 'complete_municipality':
            raise ValueError(
                f"DENUE denominator coverage_basis for {cve_mun} must be 'complete_municipality', "
                f"got {coverage_basis!r}"
            )
        if denue_vintage is not None and int(contract_vintage) != int(denue_vintage):
            raise ValueError(
                f"DENUE denominator vintage mismatch for {cve_mun}: {contract_vintage} != {denue_vintage}"
            )
        try:
            mun_total_global = float(contract['employment'])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"DENUE denominator contract for {cve_mun} requires numeric employment")
        if not np.isfinite(mun_total_global) or mun_total_global <= 0:
            raise ValueError(f"DENUE denominator for {cve_mun} must be positive and finite")
        if jobs_formal_total > mun_total_global + 1e-9:
            raise ValueError(
                f"DENUE BBOX numerator exceeds municipal denominator for {cve_mun}: "
                f"{jobs_formal_total} > {mun_total_global}"
            )

        # Calcular proporción de empleo formal municipal que cae dentro del BBOX
        share_bbox = float(jobs_formal_total / mun_total_global)

        # Escalar el objetivo H001A al recorte del BBOX
        h001a = h001a_raw * share_bbox

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
        elif jobs_large >= h001a and h001a > 0:
            # Si el empleo en grandes empresas ya iguala o excede el total censal proporcional, no inflar microempresas
            factor_clamped = 1.0
            status = "EXCESO_FORMAL_BASE"
        else:
            factor_clamped = 1.0 + til_1
            status = "AJUSTE_GLOBAL"

        df_denue.loc[mask_micro, 'calibrated_jobs'] = df_denue.loc[mask_micro, 'jobs_formal'] * factor_clamped
        df_denue.loc[mask_large, 'calibrated_jobs'] = df_denue.loc[mask_large, 'jobs_formal']  # Grandes no se inflan

        audit_report[cve_mun] = {
            "nombre": ce_benchmarks[cve_mun]["nombre"],
            "jobs_formal": jobs_formal_total,
            "h001a_mun_total": h001a_raw,
            "denominator_denue_municipal": mun_total_global,
            "numerator_denue_bbox": jobs_formal_total,
            "numerator_source": list(df_denue.attrs.get('source_paths', [])) or ["provided DENUE dataframe"],
            "numerator_scope": {"type": "BBOX subset", "bbox": df_denue.attrs.get('bbox')},
            "denominator_source": denominator_source,
            "denominator_vintage": int(contract_vintage),
            "coverage_basis": coverage_basis,
            "municipality": cve_mun,
            "share_bbox": share_bbox,
            "h001a": h001a,
            "factor": factor_clamped,
            "status": status,
            "notes": f"Calibrado territorial ({share_bbox:.1%} en BBOX, Micro factor: {factor_clamped:.3f})"
        }

    return df_denue, audit_report


def load_marco_geoestadistico_coords(
    marco_paths: Union[str, List[str]]
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    Carga capas vectoriales del Marco Geoestadístico de INEGI (Shapefile, GeoJSON, GeoPackage)
    y extrae centroides oficiales exactos para Manzanas y AGEBs.
    """
    if isinstance(marco_paths, str):
        paths = [marco_paths]
    else:
        paths = marco_paths

    mza_df = None
    ageb_df = None

    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            import geopandas as gpd
            gdf = gpd.read_file(p)
            if gdf.empty:
                continue

            if gdf.crs is None:
                raise ValueError(f"Marco/MGM layer has no CRS: {p}")
            elif gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)

            cols_upper = {
                c: str(c).upper().strip()
                for c in gdf.columns
                if c != gdf.geometry.name
            }
            gdf = gdf.rename(columns=cols_upper)

            projected_crs = gdf.estimate_utm_crs()
            if projected_crs is None:
                raise ValueError(f"Cannot determine projected CRS for Marco/MGM layer: {p}")
            projected_centroids = gdf.to_crs(projected_crs).geometry.centroid
            centroids = gpd.GeoSeries(projected_centroids, crs=projected_crs).to_crs(epsg=4326)
            gdf['lon_geo'] = centroids.x.to_numpy()
            gdf['lat_geo'] = centroids.y.to_numpy()
            if not gdf['lon_geo'].between(-180, 180).all() or not gdf['lat_geo'].between(-90, 90).all():
                raise ValueError(f"Marco/MGM layer produced invalid longitude/latitude coordinates: {p}")

            has_ent = any(c in gdf.columns for c in ['CVE_ENT', 'ENTIDAD'])
            has_mun = any(c in gdf.columns for c in ['CVE_MUN', 'MUN'])
            has_ageb = any(c in gdf.columns for c in ['CVE_AGEB', 'AGEB'])
            has_mza = any(c in gdf.columns for c in ['CVE_MZA', 'MZA', 'MANZANA'])

            if has_ent and has_mun and has_ageb:
                col_ent = [c for c in ['CVE_ENT', 'ENTIDAD'] if c in gdf.columns][0]
                col_mun = [c for c in ['CVE_MUN', 'MUN'] if c in gdf.columns][0]
                col_ageb = [c for c in ['CVE_AGEB', 'AGEB'] if c in gdf.columns][0]

                gdf['cve_mun_clean'] = [format_cve_mun(m, e) for m, e in zip(gdf[col_mun], gdf[col_ent])]
                gdf['ageb_clean'] = gdf[col_ageb].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)

                if has_mza:
                    col_mza = [c for c in ['CVE_MZA', 'MZA', 'MANZANA'] if c in gdf.columns][0]
                    gdf['mza_clean'] = pd.to_numeric(gdf[col_mza], errors='coerce').fillna(0).astype(int).astype(str)
                    mza_sub = gdf[['cve_mun_clean', 'ageb_clean', 'mza_clean', 'lon_geo', 'lat_geo']].drop_duplicates(
                        subset=['cve_mun_clean', 'ageb_clean', 'mza_clean']
                    )
                    mza_df = mza_sub if mza_df is None else pd.concat([mza_df, mza_sub], ignore_index=True)
                else:
                    ageb_sub = gdf[['cve_mun_clean', 'ageb_clean', 'lon_geo', 'lat_geo']].drop_duplicates(
                        subset=['cve_mun_clean', 'ageb_clean']
                    )
                    ageb_df = ageb_sub if ageb_df is None else pd.concat([ageb_df, ageb_sub], ignore_index=True)
        except ValueError:
            raise
        except Exception:
            continue

    def validate_combined(frame: Optional[pd.DataFrame], keys: List[str], label: str) -> Optional[pd.DataFrame]:
        if frame is None or frame.empty:
            return frame
        duplicate_mask = frame.duplicated(subset=keys, keep=False)
        if duplicate_mask.any():
            conflicts = frame.loc[duplicate_mask].groupby(keys, dropna=False)[['lon_geo', 'lat_geo']].nunique()
            if ((conflicts['lon_geo'] > 1) | (conflicts['lat_geo'] > 1)).any():
                raise ValueError(f"Conflicting overlapping Marco/MGM {label} keys would multiply CPV joins")
            frame = frame.drop_duplicates(subset=keys, keep='first').copy()
        if frame.duplicated(subset=keys).any():
            raise ValueError(f"Marco/MGM {label} keys are not unique")
        return frame

    if paths and (mza_df is None or mza_df.empty) and (ageb_df is None or ageb_df.empty):
        raise ValueError("No valid Marco/MGM manzana or AGEB records were loaded")
    mza_df = validate_combined(mza_df, ['cve_mun_clean', 'ageb_clean', 'mza_clean'], 'manzana')
    ageb_df = validate_combined(ageb_df, ['cve_mun_clean', 'ageb_clean'], 'AGEB')

    return mza_df, ageb_df


def load_cpv_demography(
    cpv_paths: Union[str, List[str]],
    df_denue: pd.DataFrame,
    bbox: Dict[str, float],
    tasa_pea: float,
    growth_factors: Optional[Dict[str, float]] = None,
    conapo_projections: Optional[Dict[str, float]] = None,
    default_growth: float = 1.0,
    marco_paths: Optional[Union[str, List[str]]] = None,
    max_unmatched_population_fraction: float = 1.0,
) -> pd.DataFrame:
    """
    Carga e imputa georreferenciación de población (CPV 2020) por manzana.
    Aplica tasa PEA y resuelve coordenadas mediante jerarquía resiliente:
    1. Marco Geoestadístico de INEGI (centroides vectoriales oficiales de manzana o AGEB).
    2. Cruce con comercios DENUE a nivel Manzana.
    3. Cruce con comercios DENUE a nivel AGEB dentro del BBOX.
    Deriva factores de proyección poblacional (CONAPO) soportando tanto ratios directos
    como proyecciones poblacionales absolutas.
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
                df_temp['_SOURCE_PATH'] = os.path.abspath(path)
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

    raw_parsed_population = float(df_censo['pobtot_num'].sum())

    df_censo = df_censo[(df_censo['mza_num'] > 0) & (df_censo['pobtot_num'] > 0)].copy()

    # Normalizar códigos geográficos
    df_censo['cve_mun_clean'] = [
        format_cve_mun(m, e) for m, e in zip(df_censo['MUN'], df_censo['ENTIDAD'])
    ]
    df_censo['ageb_clean'] = df_censo['AGEB'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
    df_censo['mza_clean'] = df_censo['mza_num'].astype(int).astype(str)
    df_censo['loc_clean'] = df_censo['LOC'].astype(str).str.strip().str.zfill(4) if 'LOC' in df_censo.columns else ''
    cpv_key = ['cve_mun_clean', 'loc_clean', 'ageb_clean', 'mza_clean']
    duplicate_mask = df_censo.duplicated(subset=cpv_key, keep=False)
    if duplicate_mask.any():
        examples = df_censo.loc[duplicate_mask, cpv_key + ['_SOURCE_PATH']].head(5).to_dict('records')
        raise ValueError(f"Duplicate CPV logical records for {cpv_key}: {examples}")
    deduplicated_population = float(df_censo['pobtot_num'].sum())

    # Factores de proyección poblacional (integración de CONAPO)
    growth_dict = dict(growth_factors or {})
    if conapo_projections:
        cpv_mun_totals = df_censo.groupby('cve_mun_clean')['pobtot_num'].sum().to_dict()
        for cve_mun, proj_val in conapo_projections.items():
            if cve_mun not in growth_dict:
                # Si proj_val ya es un ratio de crecimiento directo (ej. de parse_conapo_growth_factors)
                if 0.70 <= proj_val <= 2.50:
                    growth_dict[cve_mun] = round(float(np.clip(proj_val, 0.90, 1.60)), 4)
                elif cve_mun in cpv_mun_totals and cpv_mun_totals[cve_mun] > 0:
                    ratio = proj_val / cpv_mun_totals[cve_mun]
                    growth_dict[cve_mun] = round(float(np.clip(ratio, 0.90, 1.60)), 4)

    df_censo['growth'] = df_censo['cve_mun_clean'].map(growth_dict).fillna(default_growth)
    df_censo['pobtot_adj'] = df_censo['pobtot_num'] * df_censo['growth']
    df_censo['pob15_adj'] = df_censo['pob15_num'] * df_censo['growth']
    df_censo['pea_real'] = df_censo['pob15_adj'] * tasa_pea
    projected_population = float(df_censo['pobtot_adj'].sum())

    # =========================================================================
    # Georreferenciación Jerárquica Vía Marco Geoestadístico / DENUE
    # =========================================================================
    df_geo = df_censo.copy()

    # Nivel 0 (Oficial): Marco Geoestadístico
    if marco_paths:
        mza_geo, ageb_geo = load_marco_geoestadistico_coords(marco_paths)
        if mza_geo is not None and not mza_geo.empty:
            mza_geo = mza_geo.rename(columns={'lon_geo': 'lon_mza_geo', 'lat_geo': 'lat_mza_geo'})
            df_geo = pd.merge(df_geo, mza_geo, on=['cve_mun_clean', 'ageb_clean', 'mza_clean'], how='left', validate='many_to_one')
        if ageb_geo is not None and not ageb_geo.empty:
            ageb_geo = ageb_geo.rename(columns={'lon_geo': 'lon_ageb_geo', 'lat_geo': 'lat_ageb_geo'})
            df_geo = pd.merge(df_geo, ageb_geo, on=['cve_mun_clean', 'ageb_clean'], how='left', validate='many_to_one')

    # Nivel 1: Centroide de Manzana (comercios DENUE)
    mza_coords = df_denue.groupby(['cve_mun_clean', 'ageb_clean', 'mza_clean'])[['lon', 'lat']].mean().reset_index().rename(
        columns={'lon': 'lon_mza_denue', 'lat': 'lat_mza_denue'}
    )
    # Nivel 2: Centroide de AGEB (comercios DENUE dentro del BBOX)
    ageb_coords = df_denue.groupby(['cve_mun_clean', 'ageb_clean'])[['lon', 'lat']].mean().reset_index().rename(
        columns={'lon': 'lon_ageb_denue', 'lat': 'lat_ageb_denue'}
    )

    rows_before_join = len(df_geo)
    df_geo = pd.merge(df_geo, mza_coords, on=['cve_mun_clean', 'ageb_clean', 'mza_clean'], how='left', validate='many_to_one')
    df_geo = pd.merge(df_geo, ageb_coords, on=['cve_mun_clean', 'ageb_clean'], how='left', validate='many_to_one')
    if len(df_geo) != rows_before_join:
        raise ValueError(f"CPV georeferencing join changed cardinality: {rows_before_join} -> {len(df_geo)}")

    # Imputación jerárquica:
    # 1. Marco MZA -> 2. DENUE MZA -> 3. Marco AGEB -> 4. DENUE AGEB
    lon_s = df_geo['lon_mza_geo'] if 'lon_mza_geo' in df_geo.columns else pd.Series(np.nan, index=df_geo.index)
    lat_s = df_geo['lat_mza_geo'] if 'lat_mza_geo' in df_geo.columns else pd.Series(np.nan, index=df_geo.index)

    lon_s = lon_s.fillna(df_geo['lon_mza_denue'])
    lat_s = lat_s.fillna(df_geo['lat_mza_denue'])

    if 'lon_ageb_geo' in df_geo.columns:
        lon_s = lon_s.fillna(df_geo['lon_ageb_geo'])
        lat_s = lat_s.fillna(df_geo['lat_ageb_geo'])

    lon_s = lon_s.fillna(df_geo['lon_ageb_denue'])
    lat_s = lat_s.fillna(df_geo['lat_ageb_denue'])

    df_geo['lon'] = lon_s
    df_geo['lat'] = lat_s

    df_geo['georef_level'] = 'unmatched'
    if 'lon_mza_geo' in df_geo.columns:
        df_geo.loc[df_geo['lon_mza_geo'].notna() & df_geo['lat_mza_geo'].notna(), 'georef_level'] = 'marco_mza'
    mask = (df_geo['georef_level'] == 'unmatched') & df_geo['lon_mza_denue'].notna() & df_geo['lat_mza_denue'].notna()
    df_geo.loc[mask, 'georef_level'] = 'denue_mza'
    if 'lon_ageb_geo' in df_geo.columns:
        mask = (df_geo['georef_level'] == 'unmatched') & df_geo['lon_ageb_geo'].notna() & df_geo['lat_ageb_geo'].notna()
        df_geo.loc[mask, 'georef_level'] = 'marco_ageb'
    mask = (df_geo['georef_level'] == 'unmatched') & df_geo['lon_ageb_denue'].notna() & df_geo['lat_ageb_denue'].notna()
    df_geo.loc[mask, 'georef_level'] = 'denue_ageb'

    georef_population = {
        str(level): float(group['pobtot_adj'].sum())
        for level, group in df_geo.groupby('georef_level', dropna=False)
    }
    unmatched_mask = df_geo['georef_level'] == 'unmatched'
    unmatched_population = float(df_geo.loc[unmatched_mask, 'pobtot_adj'].sum())
    unmatched_fraction = unmatched_population / projected_population if projected_population > 0 else 0.0
    if unmatched_fraction > float(max_unmatched_population_fraction):
        raise ValueError(
            f"Unmatched CPV population {unmatched_population:.3f} ({unmatched_fraction:.2%}) exceeds "
            f"configured threshold {float(max_unmatched_population_fraction):.2%}"
        )

    # Filtro espacial estricto dentro de BBOX y descarte de registros fuera del área
    located = df_geo[~unmatched_mask].dropna(subset=['lon', 'lat']).copy()
    in_bbox_mask = (
        (located['lon'] >= bbox["min_lon"]) & (located['lon'] <= bbox["max_lon"]) &
        (located['lat'] >= bbox["min_lat"]) & (located['lat'] <= bbox["max_lat"])
    )
    outside_bbox_population = float(located.loc[~in_bbox_mask, 'pobtot_adj'].sum())
    df_geo = located.loc[in_bbox_mask].copy()
    ledger = {
        'input_rows': int(sum(len(frame) for frame in dfs)),
        'unique_logical_records': int(len(df_censo)),
        'duplicates_rejected': 0,
        'logical_key': cpv_key,
        'raw_parsed_population': raw_parsed_population,
        'deduplicated_population': deduplicated_population,
        'population_removed_as_non_manzana_or_uninhabited': raw_parsed_population - deduplicated_population,
        'projected_population': projected_population,
        'georeferenced_population': float(located['pobtot_adj'].sum()),
        'georef_population_by_level': georef_population,
        'unmatched_records': int(unmatched_mask.sum()),
        'unmatched_population': unmatched_population,
        'unmatched_fraction': unmatched_fraction,
        'outside_bbox_population': outside_bbox_population,
        'population_entering_grid': float(df_geo['pobtot_adj'].sum()),
        'rows_before_join': rows_before_join,
        'rows_after_join': len(located) + int(unmatched_mask.sum()),
    }
    df_geo.attrs['population_ledger'] = ledger

    return df_geo
