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
import json
import xml.sax.saxutils as saxutils
import pandas as pd
from typing import Dict, List, Tuple, Optional, Set, Any


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

        # Clave canónica de deduplicación: si es Supermanzana/Región/número puro, aislar el número.
        # Si es fraccionamiento o colonia general, usar el nombre completo alfanumérico normalizado.
        clean_upper = clean_name.upper()
        if any(w in clean_upper for w in ["SUPERMANZANA", "SM", "REGION", "REG"]) or clean_name.isdigit():
            num_match = re.findall(r'\d+', clean_name)
            if num_match:
                norm_key = f"sm_{num_match[0]}"
            else:
                norm_key = re.sub(r'[^a-zA-Z0-9]', '', clean_name.lower())
        else:
            norm_key = re.sub(r'[^a-zA-Z0-9]', '', clean_name.lower())

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


def generate_denue_neighborhoods_geojson(
    denue_path: str,
    bbox: List[float],
    output_geojson: str,
    urban_core_geojson: Optional[str] = None,
    min_count: int = 15,
    exclude_existing_names: Optional[Set[str]] = None
) -> Optional[str]:
    """
    Extrae asentamientos humanos de alta densidad desde el DENUE del INEGI y los exporta
    como un archivo GeoJSON de puntos ('neighborhood_labels') listo para Tippecanoe / MapGen.

    Aplica:
    1. Filtro espacial por BBOX y opcionalmente por urban_core_geojson.
    2. Mediana de coordenadas GPS para evitar distorsiones por outliers censales.
    3. Normalizacion de nombres al estandar mexicano (ej. 'Supermanzana 228', 'Fracc. Paseos del Mar').
    4. Deduplicacion con clave canonica y exclusion de toponimia ya presente en OSM.
    """
    suggestions = extract_settlement_suggestions(
        denue_path=denue_path,
        bbox=bbox,
        min_count=min_count
    )
    if not suggestions:
        return None

    core_geom = None
    if urban_core_geojson and os.path.exists(urban_core_geojson):
        try:
            from shapely.geometry import shape, Point
            import shapely
            with open(urban_core_geojson, "r", encoding="utf-8") as cf:
                cdata = json.load(cf)
            if cdata.get("type") == "FeatureCollection" and cdata.get("features"):
                core_geoms = [shape(f["geometry"]) for f in cdata["features"] if f.get("geometry")]
                core_geom = shapely.unary_union(core_geoms) if core_geoms else None
            elif cdata.get("type") == "Feature" and cdata.get("geometry"):
                core_geom = shape(cdata["geometry"])
            elif cdata.get("type") in ("Polygon", "MultiPolygon"):
                core_geom = shape(cdata)
            if core_geom and not core_geom.is_valid:
                core_geom = core_geom.buffer(0)
        except Exception:
            core_geom = None

    exclude_set = {n.strip().lower() for n in exclude_existing_names} if exclude_existing_names else set()

    features = []
    seen_names = set()

    for item in suggestions:
        name = item.get("name", "").strip()
        if not name:
            continue

        norm_name = name.lower()
        if norm_name in exclude_set or norm_name in seen_names:
            continue

        loc = item.get("loc")
        if not loc or len(loc) != 2:
            continue

        lon, lat = float(loc[0]), float(loc[1])

        # Si hay nucleo urbano definido, descartar asentamientos rurales remotos
        if core_geom is not None:
            from shapely.geometry import Point
            pt = Point(lon, lat)
            if not core_geom.intersects(pt):
                continue

        seen_names.add(norm_name)
        features.append({
            "type": "Feature",
            "properties": {
                "name": name,
                "place": item.get("type", "neighbourhood"),
                "establishments": item.get("establishments", 0),
                "source": "INEGI_DENUE"
            },
            "geometry": {
                "type": "Point",
                "coordinates": [round(lon, 6), round(lat, 6)]
            }
        })

    if not features:
        return None

    os.makedirs(os.path.dirname(os.path.abspath(output_geojson)), exist_ok=True)
    geojson_doc = {
        "type": "FeatureCollection",
        "features": features
    }

    with open(output_geojson, "w", encoding="utf-8") as gf:
        json.dump(geojson_doc, gf, ensure_ascii=False, indent=2)

    return output_geojson


def scan_city_settlements_catalog(
    city_file: str,
    min_count: int = 8
) -> Dict[str, Any]:
    """
    Escanea y cataloga todos los asentamientos disponibles para una ciudad desde:
    1. DENUE del INEGI (agrupados por asentamiento con mediana espacial).
    2. Manifiesto toponímico dist/<city>/toponymy_manifest.json (si existe).
    3. Archivo de configuración YAML de la ciudad (places existentes).

    Devuelve un catálogo completo con diagnóstico taxonómico de prefijos.
    """
    from sb_mexico.toponymy_homogenizer import ToponymyAnalyzer

    # Cargar archivo de configuración YAML de la ciudad
    cdata = {}
    if os.path.exists(city_file):
        try:
            import yaml
            with open(city_file, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
        except Exception:
            cdata = {}

    city_cfg = cdata.get("city", {})
    city_name = city_cfg.get("name", os.path.splitext(os.path.basename(city_file))[0])
    city_code = str(city_cfg.get("code", "")).lower()
    city_base = os.path.splitext(os.path.basename(city_file))[0].lower()
    bbox = city_cfg.get("bbox")

    discovered: List[Dict[str, Any]] = []
    seen_keys: Set[str] = set()

    # 1. Incorporar places ya curados en el YAML
    existing_places = cdata.get("places", [])
    if isinstance(existing_places, list):
        for ep in existing_places:
            name = ep.get("name", "").strip()
            loc = ep.get("loc", [0.0, 0.0])
            if name and len(loc) == 2:
                norm_k = re.sub(r'[^a-zA-Z0-9]', '', name.lower())
                seen_keys.add(norm_k)
                discovered.append({
                    "name": name,
                    "loc": [float(loc[0]), float(loc[1])],
                    "type": ep.get("type", "suburb"),
                    "source": "YAML_CURATED",
                    "establishments": 0
                })

    # 2. Revisar manifiesto toponímico en dist/<city>/toponymy_manifest.json
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    manifest_candidates = [
        os.path.join(root_dir, "dist", city_base, "toponymy_manifest.json"),
        os.path.join(root_dir, "dist", city_code, "toponymy_manifest.json")
    ]
    for mf_path in manifest_candidates:
        if os.path.exists(mf_path):
            try:
                with open(mf_path, "r", encoding="utf-8") as mff:
                    mf = json.load(mff)
                # Nodos OSM
                for n in mf.get("osm_nodes", []):
                    nm = n.get("name", "").strip()
                    lon = float(n.get("lon", 0.0))
                    lat = float(n.get("lat", 0.0))
                    norm_k = re.sub(r'[^a-zA-Z0-9]', '', nm.lower())
                    if nm and norm_k not in seen_keys:
                        seen_keys.add(norm_k)
                        discovered.append({
                            "name": nm,
                            "loc": [round(lon, 5), round(lat, 5)],
                            "type": n.get("place", "suburb"),
                            "source": "OSM_NODE",
                            "establishments": 0
                        })
                # Polígonos OSM (centroides)
                for p in mf.get("osm_polygons", []):
                    nm = p.get("name", "").strip()
                    lon = float(p.get("lon", 0.0))
                    lat = float(p.get("lat", 0.0))
                    norm_k = re.sub(r'[^a-zA-Z0-9]', '', nm.lower())
                    if nm and norm_k not in seen_keys:
                        seen_keys.add(norm_k)
                        discovered.append({
                            "name": nm,
                            "loc": [round(lon, 5), round(lat, 5)],
                            "type": p.get("place", "suburb"),
                            "source": "OSM_POLYGON",
                            "establishments": 0
                        })
            except Exception:
                pass
            break

    # 3. Extraer desde DENUE si existe archivo CSV
    denue_candidates = [
        os.path.join(root_dir, "data", city_base),
        os.path.join(root_dir, "data", city_code),
        os.path.join(root_dir, "data")
    ]
    denue_file_found = None
    for d_dir in denue_candidates:
        if os.path.isdir(d_dir):
            for fname in os.listdir(d_dir):
                if fname.lower().startswith("denue") and fname.lower().endswith(".csv"):
                    denue_file_found = os.path.join(d_dir, fname)
                    break
        if denue_file_found:
            break

    if denue_file_found and bbox and len(bbox) == 4:
        suggs = extract_settlement_suggestions(denue_file_found, bbox, min_count=min_count)
        for s in suggs:
            nm = s.get("name", "").strip()
            loc = s.get("loc", [0.0, 0.0])
            norm_k = re.sub(r'[^a-zA-Z0-9]', '', nm.lower())
            if nm and norm_k not in seen_keys:
                seen_keys.add(norm_k)
                discovered.append({
                    "name": nm,
                    "loc": [float(loc[0]), float(loc[1])],
                    "type": s.get("type", "neighbourhood"),
                    "source": "INEGI_DENUE",
                    "establishments": int(s.get("establishments", 0))
                })

    # Diagnóstico taxonómico
    diagnosis = ToponymyAnalyzer.analyze_collection(discovered)

    return {
        "city": city_name,
        "city_file": city_file,
        "bbox": bbox,
        "total": len(discovered),
        "places": discovered,
        "diagnosis": diagnosis
    }


