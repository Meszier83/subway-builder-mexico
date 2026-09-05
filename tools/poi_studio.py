#!/usr/bin/env python3
"""
Subway Builder México v6.3 - POI Studio Server
===============================================
Servidor local interactivo para visualizar, crear, calibrar radios de absorción
y validar POIs sobre mapas Leaflet y capas satelitales en tiempo real.

Uso:
    python tools/poi_studio.py
    python tools/poi_studio.py --city cities/cancun.yaml
    python tools/poi_studio.py --port 8085 --no-browser
"""

import os
import sys
import glob
import json
import yaml
import argparse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CITIES_DIR = os.path.join(ROOT_DIR, "cities")
TEMPLATE_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "poi_studio.html")


def get_available_cities() -> List[Dict[str, Any]]:
    """Escanea la carpeta cities/ y extrae metadatos de las ciudades disponibles."""
    city_files = glob.glob(os.path.join(CITIES_DIR, "*.yaml"))
    cities_list = []

    for fpath in sorted(city_files):
        fname = os.path.basename(fpath)
        if fname.startswith("_"):
            continue  # Omitir templates

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            city = data.get("city", {})
            pois = data.get("pois", [])
            rel_path = os.path.relpath(fpath, ROOT_DIR).replace("\\", "/")
            cities_list.append({
                "path": rel_path,
                "filename": fname,
                "code": city.get("code", "???"),
                "name": city.get("name", fname.replace(".yaml", "").capitalize()),
                "poi_count": len(pois) if isinstance(pois, list) else 0
            })
        except Exception as e:
            print(f"[WARN] Error al leer {fname}: {e}")

    return cities_list


def _resolve_city_path(rel_or_abs_path: str) -> str:
    """Resuelve la ruta a un archivo de ciudad de forma flexible y segura contra Path Traversal."""
    candidates = [
        os.path.abspath(os.path.join(ROOT_DIR, rel_or_abs_path)),
        os.path.abspath(rel_or_abs_path),
        os.path.abspath(os.path.join(ROOT_DIR, "cities", os.path.basename(rel_or_abs_path)))
    ]
    resolved = None
    for p in candidates:
        if os.path.exists(p):
            resolved = p
            break

    if resolved is None:
        resolved = os.path.abspath(os.path.join(ROOT_DIR, rel_or_abs_path))

    # Seguridad: Restringir a archivos dentro del workspace y con extensión yaml
    norm_root = os.path.normcase(os.path.realpath(ROOT_DIR))
    norm_target = os.path.normcase(os.path.realpath(resolved))
    if not (norm_target.startswith(norm_root) and (resolved.endswith(".yaml") or resolved.endswith(".yml"))):
        raise PermissionError(f"Acceso denegado: ruta fuera del workspace o extensión inválida ({rel_or_abs_path})")

    return resolved


def load_city_data(rel_or_abs_path: str) -> Dict[str, Any]:
    """Carga y parsea un archivo de configuración de ciudad."""
    fpath = _resolve_city_path(rel_or_abs_path)

    if not os.path.exists(fpath):
        raise FileNotFoundError(f"No existe el archivo de ciudad: {fpath}")

    with open(fpath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data.get("pois"), list):
        data["pois"] = []
    if not isinstance(data.get("places"), list):
        data["places"] = []

    return data


def save_city_data(
    rel_or_abs_path: str,
    new_pois: List[Dict[str, Any]],
    new_places: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    Guarda los POIs y las Colonias/Toponimia (places) en el archivo YAML
    preservando la estructura y comentarios base del archivo.
    """
    fpath = _resolve_city_path(rel_or_abs_path)

    if not os.path.exists(fpath):
        raise FileNotFoundError(f"No existe el archivo de ciudad: {fpath}")

    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Formatear el bloque de POIs en YAML limpio
    pois_yaml_lines = ["pois:"]
    for poi in new_pois:
        p_id = poi.get("id", "POI_Sin_Nombre")
        loc = poi.get("loc", [0.0, 0.0])
        jobs = int(poi.get("jobs", 5000))
        rad = int(poi.get("radius_m", 750))
        mode = poi.get("mode", "MAX").upper()

        pois_yaml_lines.append(f'  - id: "{p_id}"')
        name_val = poi.get("name")
        if isinstance(name_val, dict):
            pois_yaml_lines.append('    name:')
            if "es" in name_val:
                pois_yaml_lines.append(f'      es: "{name_val["es"]}"')
            if "en" in name_val:
                pois_yaml_lines.append(f'      en: "{name_val["en"]}"')
        elif isinstance(name_val, str) and name_val:
            pois_yaml_lines.append(f'    name: "{name_val}"')

        if poi.get("type"):
            pois_yaml_lines.append(f'    type: "{poi["type"]}"')
        if poi.get("sub_type"):
            pois_yaml_lines.append(f'    sub_type: "{poi["sub_type"]}"')

        pois_yaml_lines.append(f'    loc: [{loc[0]:.5f}, {loc[1]:.5f}]')
        pois_yaml_lines.append(f'    jobs: {jobs}')
        pois_yaml_lines.append(f'    radius_m: {rad}')
        pois_yaml_lines.append(f'    mode: "{mode}"')

        if isinstance(poi.get("metadata"), dict) and poi["metadata"]:
            pois_yaml_lines.append('    metadata:')
            for mk, mv in poi["metadata"].items():
                pois_yaml_lines.append(f'      {mk}: "{mv}"')
        pois_yaml_lines.append('')

    new_pois_block = "\n".join(pois_yaml_lines).rstrip() + "\n"

    # 2. Formatear el bloque opcional de Places/Toponimia en YAML
    new_places_block = ""
    if new_places is not None and len(new_places) > 0:
        places_yaml_lines = ["\n# Toponimia y Colonias Curadas (Inyección de etiquetas en .pmtiles)", "places:"]
        for pl in new_places:
            pl_name = pl.get("name", "Colonia")
            pl_loc = pl.get("loc", [0.0, 0.0])
            pl_type = pl.get("type", "suburb")
            places_yaml_lines.append(f'  - name: "{pl_name}"')
            places_yaml_lines.append(f'    loc: [{pl_loc[0]:.5f}, {pl_loc[1]:.5f}]')
            places_yaml_lines.append(f'    type: "{pl_type}"')
        new_places_block = "\n".join(places_yaml_lines) + "\n"

    # 3. Remover bloques anteriores de pois: y places: del contenido original
    # Cortar a partir de la primera aparición de pois: o places:
    cut_idx = len(content)
    for marker in ["\npois:", "pois:", "\nplaces:", "places:"]:
        pos = content.find(marker)
        if pos != -1 and pos < cut_idx:
            cut_idx = pos

    base_content = content[:cut_idx].rstrip() + "\n\n"
    updated_content = base_content + new_pois_block + new_places_block

    with open(fpath, "w", encoding="utf-8") as f:
        f.write(updated_content)


def save_city_pois(rel_or_abs_path: str, new_pois: List[Dict[str, Any]]) -> None:
    """Wrapper de compatibilidad retroactiva para guardar POIs."""
    save_city_data(rel_or_abs_path, new_pois=new_pois)


def load_demand_sample(bbox: List[float] = None, city_file: str = "") -> List[Dict[str, Any]]:
    """
    Carga puntos de demanda de referencia espacial EXCLUSIVAMENTE derivados de las fuentes oficiales
    de datos (DENUE y Censo CPV RESAGEBURB) en la carpeta del proyecto (target_data_dir).

    Reglas de Integridad:
    1. CERO contaminación de POIs manuales: Ningún POI personalizado (ej. aeropuertos 'AIR_',
       estadios, universidades creadas en el YAML o 'is_special: True') puede aparecer en la
       capa de referencia de empleo o población.
    2. CERO fallbacks cruzados a otras ciudades: Se limita estrictamente a los datos del proyecto activo.
    3. Caché de alto rendimiento: Si existe '.density_cache.json' en la carpeta de datos y sus
       mtimes coinciden con los archivos fuente, se carga de inmediato.
    4. Si no hay archivos fuente en la carpeta de datos pero existe demand_data.json compilado
       (ej. entornos de test o proyectos heredados), se purgan estrictamente todos los POIs
       y puntos especiales antes de retornarlo.
    """
    city_base = ""
    target_data_dir = None
    poi_ids = set()
    poi_prefixes = ("AIR_", "UNI_", "TOU_", "MED_", "SPO_", "TRA_")

    if city_file:
        city_base = os.path.splitext(os.path.basename(city_file))[0].lower()
        try:
            cdata = load_city_data(city_file)
            cfg_dir = cdata.get("data_dir")
            if cfg_dir:
                target_data_dir = cfg_dir if os.path.isabs(cfg_dir) else os.path.join(ROOT_DIR, cfg_dir)
            if not bbox:
                bbox = cdata.get("city", {}).get("bbox")
            for p in cdata.get("pois", []):
                if isinstance(p, dict) and p.get("id"):
                    poi_ids.add(str(p["id"]))
        except Exception:
            pass

    if not target_data_dir and city_base:
        cand_dir = os.path.join(ROOT_DIR, "data", city_base)
        if os.path.exists(cand_dir):
            target_data_dir = cand_dir

    def _clean_and_filter(pts: List[Dict[str, Any]], box: Optional[List[float]]) -> List[Dict[str, Any]]:
        clean_pts = []
        for p in pts:
            p_id = str(p.get("id", ""))
            if p.get("is_special"):
                continue
            if p_id in poi_ids:
                continue
            if any(p_id.startswith(pref) for pref in poi_prefixes):
                continue
            loc = p.get("location")
            if not loc or len(loc) != 2:
                continue
            if box and len(box) == 4:
                if not (box[0] <= loc[0] <= box[2] and box[1] <= loc[1] <= box[3]):
                    continue
            clean_pts.append({
                "id": p_id,
                "location": [round(float(loc[0]), 5), round(float(loc[1]), 5)],
                "jobs": int(round(p.get("jobs", 0))),
                "residents": int(round(p.get("residents", 0)))
            })
        return clean_pts

    # Detectar archivos DENUE y Censo en target_data_dir
    denue_files = []
    cpv_files = []
    if target_data_dir and os.path.exists(target_data_dir):
        raw_denue = glob.glob(os.path.join(target_data_dir, "*denue*.csv")) + glob.glob(os.path.join(target_data_dir, "*DENUE*.csv"))
        denue_files = sorted(list(dict.fromkeys(os.path.normpath(f) for f in raw_denue if os.path.isfile(f))))

        raw_cpv = (
            glob.glob(os.path.join(target_data_dir, "*resageburb*.csv")) +
            glob.glob(os.path.join(target_data_dir, "*RESAGEBURB*.csv")) +
            glob.glob(os.path.join(target_data_dir, "*censo*.csv")) +
            glob.glob(os.path.join(target_data_dir, "*CENSO*.csv"))
        )
        cpv_files = sorted(list(dict.fromkeys(os.path.normpath(f) for f in raw_cpv if os.path.isfile(f))))

    # Si hay fuentes oficiales disponibles en target_data_dir
    if (denue_files or cpv_files) and bbox and len(bbox) == 4:
        cache_path = os.path.join(target_data_dir, ".density_cache.json")
        src_files = denue_files + cpv_files
        src_mtimes = {os.path.basename(f): os.path.getmtime(f) for f in src_files}

        # 1. Verificar si existe caché válido
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                if (
                    cached_data.get("mtimes") == src_mtimes and
                    cached_data.get("bbox") == bbox and
                    isinstance(cached_data.get("points"), list)
                ):
                    return _clean_and_filter(cached_data["points"], bbox)
            except Exception:
                pass

        # 2. Generar muestras de densidad a partir de archivos oficiales en tiempo real
        try:
            import numpy as np
            import pandas as pd
            from sb_mexico.inegi import DENUE_ESTRATOS, format_cve_mun

            grid_size = 0.0025
            df_denue = None
            mza_coords = None
            ageb_coords = None

            if denue_files:
                dfs_d = []
                for df_path in denue_files:
                    for enc in ['latin1', 'utf-8-sig', 'utf-8', 'cp1252']:
                        try:
                            t_df = pd.read_csv(df_path, encoding=enc, low_memory=False, dtype=str)
                            t_df.columns = [c.strip().lower() for c in t_df.columns]
                            dfs_d.append(t_df)
                            break
                        except Exception:
                            continue
                if dfs_d:
                    df_denue = pd.concat(dfs_d, ignore_index=True)
                    lat_cols = [c for c in df_denue.columns if 'latitud' in c]
                    lon_cols = [c for c in df_denue.columns if 'longitud' in c]
                    if lat_cols and lon_cols:
                        df_denue['lat'] = pd.to_numeric(df_denue[lat_cols[0]], errors='coerce')
                        df_denue['lon'] = pd.to_numeric(df_denue[lon_cols[0]], errors='coerce')
                        df_denue = df_denue[
                            (df_denue['lon'] >= bbox[0]) & (df_denue['lon'] <= bbox[2]) &
                            (df_denue['lat'] >= bbox[1]) & (df_denue['lat'] <= bbox[3])
                        ].dropna(subset=['lat', 'lon']).copy()

                        col_per = 'per_ocu' if 'per_ocu' in df_denue.columns else (
                            [c for c in df_denue.columns if 'personal' in c or 'estrato' in c] + [''])[0]
                        if col_per:
                            df_denue['jobs'] = df_denue[col_per].astype(str).str.strip().map(DENUE_ESTRATOS).fillna(2.24)
                        else:
                            df_denue['jobs'] = 2.24

                        # Normalización para matching con CPV
                        if 'cve_mun' in df_denue.columns and 'cve_ent' in df_denue.columns:
                            df_denue['cve_mun_clean'] = [format_cve_mun(m, e) for m, e in zip(df_denue['cve_mun'], df_denue['cve_ent'])]
                        else:
                            df_denue['cve_mun_clean'] = "-1"

                        if 'ageb' in df_denue.columns:
                            df_denue['ageb_clean'] = df_denue['ageb'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
                        else:
                            df_denue['ageb_clean'] = ""

                        if 'manzana' in df_denue.columns:
                            df_denue['mza_clean'] = pd.to_numeric(df_denue['manzana'], errors='coerce').fillna(-1).astype(int).astype(str)
                        else:
                            df_denue['mza_clean'] = "-1"

                        valid_mza = df_denue[df_denue['mza_clean'] != '-1']
                        if len(valid_mza) > 0:
                            mza_coords = valid_mza.groupby(['cve_mun_clean', 'ageb_clean', 'mza_clean'])[['lon', 'lat']].mean().reset_index()
                        ageb_coords = df_denue.groupby(['cve_mun_clean', 'ageb_clean'])[['lon', 'lat']].mean().reset_index()

            # Procesar Censo CPV
            df_cpv = None
            if cpv_files and (mza_coords is not None or ageb_coords is not None):
                dfs_c = []
                for cpv_path in cpv_files:
                    for enc in ['utf-8-sig', 'latin1', 'utf-8', 'cp1252']:
                        try:
                            t_df = pd.read_csv(cpv_path, encoding=enc, low_memory=False, dtype=str)
                            t_df.columns = [c.strip().replace('\ufeff', '').replace('ï»¿', '').upper() for c in t_df.columns]
                            dfs_c.append(t_df)
                            break
                        except Exception:
                            continue
                if dfs_c:
                    raw_cpv_df = pd.concat(dfs_c, ignore_index=True)
                    req = ['ENTIDAD', 'MUN', 'AGEB', 'MZA', 'POBTOT']
                    if all(c in raw_cpv_df.columns for c in req):
                        raw_cpv_df['mza_num'] = pd.to_numeric(raw_cpv_df['MZA'].replace('*', '1'), errors='coerce').fillna(0)
                        raw_cpv_df['pobtot_num'] = pd.to_numeric(raw_cpv_df['POBTOT'].replace('*', '1.5'), errors='coerce').fillna(0)
                        raw_cpv_df = raw_cpv_df[(raw_cpv_df['mza_num'] > 0) & (raw_cpv_df['pobtot_num'] > 0)].copy()

                        raw_cpv_df['cve_mun_clean'] = [format_cve_mun(m, e) for m, e in zip(raw_cpv_df['MUN'], raw_cpv_df['ENTIDAD'])]
                        raw_cpv_df['ageb_clean'] = raw_cpv_df['AGEB'].astype(str).str.strip().str.upper().str.replace('-', '').str.zfill(4)
                        raw_cpv_df['mza_clean'] = raw_cpv_df['mza_num'].astype(int).astype(str)

                        matched_parts = []
                        if mza_coords is not None and len(mza_coords) > 0:
                            merged_mza = pd.merge(raw_cpv_df, mza_coords, on=['cve_mun_clean', 'ageb_clean', 'mza_clean'], how='inner')
                            matched_parts.append(merged_mza)
                            key_mza = raw_cpv_df['cve_mun_clean'] + "_" + raw_cpv_df['ageb_clean'] + "_" + raw_cpv_df['mza_clean']
                            key_matched = merged_mza['cve_mun_clean'] + "_" + merged_mza['ageb_clean'] + "_" + merged_mza['mza_clean']
                            unmatched = raw_cpv_df[~key_mza.isin(key_matched)]
                        else:
                            unmatched = raw_cpv_df

                        if ageb_coords is not None and len(ageb_coords) > 0 and len(unmatched) > 0:
                            merged_ageb = pd.merge(unmatched, ageb_coords, on=['cve_mun_clean', 'ageb_clean'], how='inner')
                            matched_parts.append(merged_ageb)

                        if matched_parts:
                            df_cpv = pd.concat(matched_parts, ignore_index=True)
                            df_cpv = df_cpv[
                                (df_cpv['lon'] >= bbox[0]) & (df_cpv['lon'] <= bbox[2]) &
                                (df_cpv['lat'] >= bbox[1]) & (df_cpv['lat'] <= bbox[3])
                            ].dropna(subset=['lat', 'lon']).copy()

            # Agregación espacial en cuadrícula
            grp_d = None
            if df_denue is not None and len(df_denue) > 0:
                df_denue['gx'] = np.floor(df_denue['lon'] / grid_size).astype(int)
                df_denue['gy'] = np.floor(df_denue['lat'] / grid_size).astype(int)
                grp_d = df_denue.groupby(['gx', 'gy']).agg(
                    jobs=('jobs', 'sum'),
                    lon=('lon', 'mean'),
                    lat=('lat', 'mean')
                ).reset_index()

            grp_c = None
            if df_cpv is not None and len(df_cpv) > 0:
                df_cpv['gx'] = np.floor(df_cpv['lon'] / grid_size).astype(int)
                df_cpv['gy'] = np.floor(df_cpv['lat'] / grid_size).astype(int)
                grp_c = df_cpv.groupby(['gx', 'gy']).agg(
                    residents=('pobtot_num', 'sum'),
                    lon=('lon', 'mean'),
                    lat=('lat', 'mean')
                ).reset_index()

            if grp_d is not None and grp_c is not None:
                merged_grid = pd.merge(grp_d, grp_c, on=['gx', 'gy'], how='outer', suffixes=('_d', '_c'))
                merged_grid['jobs'] = merged_grid['jobs'].fillna(0).round().astype(int)
                merged_grid['residents'] = merged_grid['residents'].fillna(0).round().astype(int)
                merged_grid['lon'] = merged_grid['lon_d'].fillna(merged_grid['lon_c']).round(5)
                merged_grid['lat'] = merged_grid['lat_d'].fillna(merged_grid['lat_c']).round(5)
            elif grp_d is not None:
                merged_grid = grp_d
                merged_grid['jobs'] = merged_grid['jobs'].round().astype(int)
                merged_grid['residents'] = 0
                merged_grid['lon'] = merged_grid['lon'].round(5)
                merged_grid['lat'] = merged_grid['lat'].round(5)
            elif grp_c is not None:
                merged_grid = grp_c
                merged_grid['jobs'] = 0
                merged_grid['residents'] = merged_grid['residents'].round().astype(int)
                merged_grid['lon'] = merged_grid['lon'].round(5)
                merged_grid['lat'] = merged_grid['lat'].round(5)
            else:
                merged_grid = None

            if merged_grid is not None and len(merged_grid) > 0:
                raw_points = []
                for i, r in merged_grid.iterrows():
                    raw_points.append({
                        'id': f"ref_{i+1:04d}",
                        'location': [float(r['lon']), float(r['lat'])],
                        'jobs': int(r['jobs']),
                        'residents': int(r['residents'])
                    })

                # Guardar caché atómicamente
                try:
                    cache_payload = {
                        "bbox": bbox,
                        "mtimes": src_mtimes,
                        "points": raw_points
                    }
                    with open(cache_path, "w", encoding="utf-8") as cf:
                        json.dump(cache_payload, cf)
                except Exception:
                    pass

                return _clean_and_filter(raw_points, bbox)

        except Exception as e:
            print(f"[WARN] Error al procesar datos brutos para {city_base}: {e}")

    # Fallback estricto: Si NO hay archivos fuente brutos en data/, pero existe demand_data.json
    # compilado (ej. para tests o proyectos heredados), cargar pero PURGANDO estrictamente cualquier POI manual o especial
    if city_base:
        city_demand_path = os.path.join(ROOT_DIR, "dist", city_base, "demand_data.json")
        if os.path.exists(city_demand_path):
            try:
                with open(city_demand_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                points = data.get("points", [])
                return _clean_and_filter(points, bbox)
            except Exception as e:
                print(f"[WARN] Error al leer {city_demand_path}: {e}")

    return []


class PoiStudioRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silenciar logs ruidosos de polling/tile requests
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ["/", "/index.html"]:
            self.serve_html()
        elif path == "/api/cities":
            self.serve_json({"cities": get_available_cities()})
        elif path == "/api/city":
            city_file = query.get("file", [""])[0]
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            try:
                data = load_city_data(city_file)
                self.serve_json(data)
            except Exception as e:
                self.serve_error(str(e), 404)
        elif path == "/api/settlement_suggestions":
            try:
                city_file = query.get("file", [""])[0]
                bbox = None
                city_base = ""
                if city_file:
                    try:
                        cdata = load_city_data(city_file)
                        bbox = cdata.get("city", {}).get("bbox")
                        city_base = os.path.splitext(os.path.basename(city_file))[0]
                    except Exception as e:
                        print(f"[WARN] Error cargando city_data en suggestions: {e}")

                from sb_mexico.toponymy import extract_settlement_suggestions
                denue_candidates = []
                if city_base:
                    denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "data", city_base, "*denue*.csv")))
                    denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "dist", city_base, "*denue*.csv")))
                denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "data", "*", "*denue*.csv")))
                denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "data", "*denue*.csv")))
                denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "*denue*.csv")))

                seen = set()
                valid_denue = []
                for c in denue_candidates:
                    if c not in seen and os.path.isfile(c):
                        seen.add(c)
                        valid_denue.append(c)

                if valid_denue and bbox:
                    suggs = extract_settlement_suggestions(valid_denue[0], bbox, min_count=10)
                else:
                    suggs = []
                self.serve_json({"suggestions": suggs})
            except Exception as e:
                print(f"[ERROR] En /api/settlement_suggestions: {e}")
                self.serve_json({"suggestions": [], "error": str(e)})
        elif path == "/api/density":
            city_file = query.get("file", [""])[0]
            bbox = None
            if city_file:
                try:
                    cdata = load_city_data(city_file)
                    bbox = cdata.get("city", {}).get("bbox")
                except Exception:
                    pass
            points = load_demand_sample(bbox, city_file=city_file)
            self.serve_json({"points": points})
        else:
            self.serve_error("Ruta no encontrada", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/save":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                new_pois = req_data.get("pois", [])
                new_places = req_data.get("places", [])

                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                save_city_data(city_file, new_pois=new_pois, new_places=new_places)
                self.serve_json({"status": "ok", "saved_pois": len(new_pois), "saved_places": len(new_places)})
            except Exception as e:
                self.serve_error(str(e), 500)
        else:
            self.serve_error("Método POST no permitido", 405)

    def serve_html(self):
        if not os.path.exists(TEMPLATE_HTML_PATH):
            self.serve_error("Template HTML no encontrado", 500)
            return

        with open(TEMPLATE_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def serve_json(self, data: Any, status: int = 200):
        try:
            body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": f"JSON serialization error: {e}"}).encode("utf-8")
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def serve_error(self, message: str, status: int = 400):
        self.serve_json({"error": message}, status=status)


def run_server(port: int = 8080, initial_city: str = None, open_browser: bool = True, host: str = "127.0.0.1"):
    server_address = (host, port)
    
    # Manejo automático de puertos ocupados
    for attempt in range(5):
        try:
            httpd = ThreadingHTTPServer(server_address, PoiStudioRequestHandler)
            break
        except OSError:
            port += 1
            server_address = (host, port)
    else:
        print(f"[ERROR] No se pudo vincular el servidor en los puertos 8080-8085.")
        sys.exit(1)

    url = f"http://{host}:{port}/"
    if initial_city:
        url += f"?city={initial_city}"

    print("=" * 60)
    print(" 🚇 SUBWAY BUILDER MÉXICO v6.3 - POI STUDIO ")
    print("=" * 60)
    print(f" Servidor iniciado en: {url}")
    print(f" Raíz del proyecto:    {ROOT_DIR}")
    print(f" Presiona Ctrl+C para detener el servidor.")
    print("=" * 60)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Servidor POI Studio detenido por el usuario.")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="POI Studio v6.3 - Visualizador y Editor Interactivo de POIs para Subway Builder México"
    )
    parser.add_argument(
        "--city",
        default=None,
        help="Archivo YAML de ciudad inicial a cargar (ej. cities/<ciudad>.yaml)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Puerto HTTP local (default: 8080)"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host de enlace local (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="No abrir automáticamente el navegador web al iniciar"
    )

    args = parser.parse_args()
    run_server(
        port=args.port,
        initial_city=args.city,
        open_browser=not args.no_browser,
        host=args.host
    )


if __name__ == "__main__":
    main()
