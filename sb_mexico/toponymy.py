"""
sb_mexico.toponymy
==================
Módulo de gestión y curación de toponimia urbana y colonias ('places') para
Subway Builder México. Permite generar parches OSM XML con nodos sintéticos
y extraer sugerencias deduplicadas desde microdatos del DENUE.
"""

import os
import re
import math
import xml.sax.saxutils as saxutils
import pandas as pd
from typing import Dict, List, Tuple, Optional


def format_clean_place_name(nomb_raw: str, tipo_raw: str = "") -> Tuple[str, str]:
    """
    Normaliza y formatea nombres de colonias, supermanzanas y fraccionamientos.
    Retorna (display_name, place_type).
    """
    nomb = str(nomb_raw).strip()
    tipo = str(tipo_raw).strip().upper()

    # Caso 1: Número puro (ej. '94', '100', '228', '510')
    if nomb.isdigit():
        num = int(nomb)
        if "REGION" in tipo or "REG" in tipo:
            clean_name = f"Región {num}"
        else:
            clean_name = f"Supermanzana {num}"
        return clean_name, "suburb"

    # Caso 2: Limpieza de prefijos redundantes
    clean = re.sub(
        r'^(FRACCIONAMIENTO|COLONIA|SUPERMANZANA|REGION|RESIDENCIAL|EJIDO|PUEBLO|SM)\s+',
        '', nomb, flags=re.IGNORECASE
    ).strip()
    clean_title = clean.title()

    # Normalizar abreviaturas frecuentes
    clean_title = clean_title.replace("Sm ", "Supermanzana ").replace("Fracc ", "Fracc. ")

    # Detectar si el nombre es puramente numérico (ej. '94', '100', '102') o alfanumérico corto (ej. '92A')
    is_numeric_code = bool(re.match(r'^\d+[A-Za-z]?$', clean_title))

    if "FRACCIONAMIENTO" in tipo or "FRACC" in tipo or "RESIDENCIAL" in tipo:
        if not clean_title.lower().startswith("fracc"):
            clean_name = f"Fracc. {clean_title}"
        else:
            clean_name = clean_title
        place_type = "neighbourhood"
    elif "SUPERMANZANA" in tipo:
        if is_numeric_code:
            clean_name = f"Supermanzana {clean_title}"
        else:
            clean_name = f"Supermanzana {clean_title}" if not clean_title.lower().startswith("supermanzana") else clean_title
        place_type = "suburb"
    elif "REGION" in tipo:
        if is_numeric_code:
            clean_name = f"Región {clean_title}"
        else:
            clean_name = clean_title
        place_type = "suburb"
    else:
        clean_name = clean_title
        place_type = "suburb"

    return clean_name, place_type


def generate_osm_patch(places: List[Dict], output_osm_path: str) -> str:
    """
    Genera un archivo .osm XML válido que contiene nodos sintéticos con IDs negativos
    y etiquetas place=* y name=* para su fusión en el compilador cartográfico.
    """
    os.makedirs(os.path.dirname(os.path.abspath(output_osm_path)), exist_ok=True)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="SubwayBuilderMexico-Toponymy">',
    ]

    for idx, place in enumerate(places, start=1):
        node_id = -abs(idx)
        name = place.get("name", f"Place {idx}")
        loc = place.get("loc", [0.0, 0.0])
        lon, lat = loc[0], loc[1]
        place_type = place.get("type", "suburb")

        name_escaped = saxutils.escape(name)
        place_escaped = saxutils.escape(place_type)

        lines.append(f'  <node id="{node_id}" lat="{lat:.6f}" lon="{lon:.6f}" version="1">')
        lines.append(f'    <tag k="place" v="{place_escaped}"/>')
        lines.append(f'    <tag k="name" v="{name_escaped}"/>')
        lines.append('  </node>')

    lines.append('</osm>')

    content = "\n".join(lines) + "\n"
    with open(output_osm_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_osm_path


def extract_settlement_suggestions(
    denue_path: str,
    bbox: List[float],
    min_count: int = 15
) -> List[Dict]:
    """
    Extrae sugerencias deduplicadas de colonias y supermanzanas desde el DENUE
    para asistir al usuario en POI Studio.
    """
    if not os.path.exists(denue_path) or not bbox or len(bbox) != 4:
        return []

    df = None
    for enc in ['latin1', 'utf-8-sig', 'utf-8', 'cp1252']:
        try:
            df = pd.read_csv(denue_path, encoding=enc, low_memory=False, dtype=str)
            break
        except Exception:
            continue

    if df is None:
        return []

    lat_cols = [c for c in df.columns if 'latitud' in c.lower()]
    lon_cols = [c for c in df.columns if 'longitud' in c.lower()]
    if not lat_cols or not lon_cols:
        return []

    df['lat'] = pd.to_numeric(df[lat_cols[0]], errors='coerce')
    df['lon'] = pd.to_numeric(df[lon_cols[0]], errors='coerce')

    df_box = df[
        (df['lon'] >= bbox[0]) & (df['lon'] <= bbox[2]) &
        (df['lat'] >= bbox[1]) & (df['lat'] <= bbox[3])
    ].dropna(subset=['lat', 'lon']).copy()

    col_nomb = 'nomb_asent' if 'nomb_asent' in df_box.columns else 'asentamiento'
    col_tipo = 'tipo_asent' if 'tipo_asent' in df_box.columns else 'tipo_asentamiento'

    if col_nomb not in df_box.columns:
        return []

    df_box['nomb_clean'] = df_box[col_nomb].fillna('').astype(str).str.strip().str.upper()
    df_box['tipo_clean'] = df_box[col_tipo].fillna('').astype(str).str.strip().str.upper() if col_tipo in df_box.columns else 'COLONIA'

    invalid_names = {'', 'NAN', 'NINGUNO', 'OTRO', 'SIN NOMBRE', 'DESCONOCIDO', 'NULL', 'NO APLICA', 'CENTRO'}
    df_valid = df_box[~df_box['nomb_clean'].isin(invalid_names)].copy()

    suggestions = []
    # Normalización de claves para agrupar variantes (ej. '97' y 'SUPERMANZANA 97')
    grouped_data = {}

    for raw_name, group in df_valid.groupby('nomb_clean'):
        count = int(len(group))
        if count < min_count:
            continue

        lon_med = float(group['lon'].median())
        lat_med = float(group['lat'].median())

        tipo_mode = str(group['tipo_clean'].mode().iloc[0]) if not group['tipo_clean'].empty else 'COLONIA'
        clean_name, place_type = format_clean_place_name(str(raw_name), tipo_mode)

        # Clave canónica de deduplicación (ej. solo el número para supermanzanas o nombre sin signos)
        norm_key = re.sub(r'[^a-zA-Z0-9]', '', clean_name.lower())
        num_match = re.findall(r'\d+', clean_name)
        if num_match:
            norm_key = f"num_{num_match[0]}"

        if norm_key in grouped_data:
            # Si ya existe, nos quedamos con el que tenga mayor cantidad de comercios
            if count > grouped_data[norm_key]['establishments']:
                grouped_data[norm_key] = {
                    'name': str(clean_name),
                    'type': str(place_type),
                    'loc': [float(round(lon_med, 5)), float(round(lat_med, 5))],
                    'establishments': int(count),
                    'tipo_asent': str(tipo_mode)
                }
            else:
                grouped_data[norm_key]['establishments'] = int(grouped_data[norm_key]['establishments'] + count)
        else:
            grouped_data[norm_key] = {
                'name': str(clean_name),
                'type': str(place_type),
                'loc': [float(round(lon_med, 5)), float(round(lat_med, 5))],
                'establishments': int(count),
                'tipo_asent': str(tipo_mode)
            }

    suggestions = list(grouped_data.values())
    suggestions.sort(key=lambda x: int(x['establishments']), reverse=True)
    return suggestions
