#!/usr/bin/env python3
"""
Subway Builder México v6.3 - Wizard Server
============================================
Servidor local interactivo con API REST, soporte para carga manual de datos,
streaming en vivo de compilación (SSE) y calibración geoespacial integral.

Inspirado en la estética del Metro de la CDMX (Lance Wyman).

Uso:
    python tools/wizard.py
    python tools/wizard.py --city cities/cancun.yaml
    python tools/wizard.py --port 8080 --no-browser
"""

import os
import sys
import re
import glob
import json
import yaml
import time
import queue
import shutil
import logging
import argparse
import threading
import subprocess
import webbrowser
import unicodedata
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional

# Directorios base
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CITIES_DIR = os.path.join(ROOT_DIR, "cities")
DATA_DIR = os.path.join(ROOT_DIR, "data")
DIST_DIR = os.path.join(ROOT_DIR, "dist")
TEMPLATE_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "wizard.html")

# Asegurar UTF-8 en consolas Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Asegurar que sb_mexico esté en sys.path y CWD sea ROOT_DIR
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Estado global de compilación
build_lock = threading.Lock()
active_build = {
    "running": False,
    "progress": 0,
    "step_name": "Inactivo",
    "status": "idle",
    "error": None,
    "city_code": "",
    "logs": [],
    "log_queues": []  # List[queue.Queue] para SSE
}


def _resolve_city_path(rel_or_abs_path: str) -> str:
    """Resuelve la ruta a un archivo de ciudad de forma segura contra Path Traversal."""
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

    norm_root = os.path.normcase(os.path.realpath(ROOT_DIR))
    norm_target = os.path.normcase(os.path.realpath(resolved))
    if not (norm_target.startswith(norm_root) and (resolved.endswith(".yaml") or resolved.endswith(".yml"))):
        raise PermissionError(f"Acceso denegado: ruta fuera del workspace o extensión inválida ({rel_or_abs_path})")

    return resolved


def get_available_cities() -> List[Dict[str, Any]]:
    """Escanea la carpeta cities/ y extrae metadatos de las ciudades disponibles."""
    city_files = glob.glob(os.path.join(CITIES_DIR, "*.yaml"))
    cities_list = []

    for fpath in sorted(city_files):
        fname = os.path.basename(fpath)
        if fname.startswith("_"):
            continue

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            city = data.get("city", {})
            pois = data.get("pois", [])
            macro = data.get("macroeconomics", {})
            rel_path = os.path.relpath(fpath, ROOT_DIR).replace("\\", "/")
            cities_list.append({
                "path": rel_path,
                "filename": fname,
                "code": city.get("code", "???"),
                "name": city.get("name", fname.replace(".yaml", "").capitalize()),
                "description": city.get("description", ""),
                "bbox": city.get("bbox", []),
                "poi_count": len(pois) if isinstance(pois, list) else 0,
                "tasa_pea": macro.get("tasa_pea", 0.62),
                "growth_factors": macro.get("growth_factors", {})
            })
        except Exception as e:
            print(f"[WARN] Error al leer {fname}: {e}")

    return cities_list


def load_city_data(rel_or_abs_path: str) -> Dict[str, Any]:
    """Carga y parsea un archivo de configuración de ciudad."""
    fpath = _resolve_city_path(rel_or_abs_path)

    if not os.path.exists(fpath):
        raise FileNotFoundError(f"No existe el archivo de ciudad: {fpath}")

    with open(fpath, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data.get("city"), dict):
        data["city"] = {}
    if not isinstance(data.get("macroeconomics"), dict):
        data["macroeconomics"] = {}
    if not isinstance(data.get("pois"), list):
        data["pois"] = []
    if not isinstance(data.get("places"), list):
        data["places"] = []
    if "data_dir" not in data:
        data["data_dir"] = ""
    if not isinstance(data.get("data_exclusions"), list):
        data["data_exclusions"] = []

    return data


def save_full_city_data(rel_or_abs_path: str, data: Dict[str, Any]) -> str:
    """Guarda la configuración completa de la ciudad respetando el esquema oficial."""
    fpath = _resolve_city_path(rel_or_abs_path)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)

    city_cfg = data.get("city") or {}
    raw_bbox = city_cfg.get("bbox")
    if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4:
        try:
            b0 = float(raw_bbox[0])
            b1 = float(raw_bbox[1])
            b2 = float(raw_bbox[2])
            b3 = float(raw_bbox[3])
            city_cfg["bbox"] = [
                round(min(b0, b2), 4),
                round(min(b1, b3), 4),
                round(max(b0, b2), 4),
                round(max(b1, b3), 4)
            ]
        except (ValueError, TypeError):
            pass

    macro_cfg = data.get("macroeconomics") or {}
    pois_cfg = data.get("pois") or []
    places_cfg = data.get("places") or []
    data_dir_cfg = str(data.get("data_dir", "")).strip()
    data_exclusions_cfg = data.get("data_exclusions", [])

    lines = [
        "# ==============================================================================",
        f"# CONFIGURACIÓN: {city_cfg.get('name', 'CIUDAD')} ({city_cfg.get('code', 'XXX')}) - SUBWAY BUILDER MÉXICO v6.3",
        "# ==============================================================================",
        "",
        "city:",
        f'  code: "{city_cfg.get("code", "XXX")}"',
        f'  name: "{city_cfg.get("name", "Nueva Ciudad")}"',
        f'  description: "{city_cfg.get("description", "")}"',
        f'  bbox: {city_cfg.get("bbox", [-87.0, 21.0, -86.7, 21.3])}',
        f'  creator: "{city_cfg.get("creator", "Creador")}"',
        f'  grid_size: {float(city_cfg.get("grid_size", 0.0025))}',
        f'  min_residents: {int(city_cfg.get("min_residents", 10))}',
        f'  min_jobs: {int(city_cfg.get("min_jobs", 3))}',
        f'  initial_zoom: {float(city_cfg.get("initial_zoom", 11.5))}',
        f'  building_filter_size: {float(city_cfg.get("building_filter_size", 15.0))}',
        f'  building_simplification: {float(city_cfg.get("building_simplification", 0.2))}',
        f'  include_ocean: {"true" if city_cfg.get("include_ocean") else "false"}',
        ""
    ]

    if data_dir_cfg:
        lines.append(f'data_dir: "{data_dir_cfg}"')
        lines.append("")

    if data_exclusions_cfg and isinstance(data_exclusions_cfg, list):
        lines.append("data_exclusions:")
        for ex in data_exclusions_cfg:
            lines.append(f'  - "{ex}"')
        lines.append("")

    lines.extend([
        "macroeconomics:",
        f'  tasa_pea: {float(macro_cfg.get("tasa_pea", 0.62))}',
        f'  til_1_state: {float(macro_cfg.get("til_1_state", 0.45))}',
        f'  sample_threshold: {int(macro_cfg.get("sample_threshold", 500))}',
        f'  default_growth_factor: {float(macro_cfg.get("default_growth_factor", 1.05))}',
        f'  gravity_beta: {float(macro_cfg.get("gravity_beta", 0.12))}',
        f'  max_distance_km: {float(macro_cfg.get("max_distance_km", 50.0))}',
        f'  max_pop_size: {int(macro_cfg.get("max_pop_size", 150))}',
        ""
    ])

    growth_factors = macro_cfg.get("growth_factors", {})
    if isinstance(growth_factors, dict) and growth_factors:
        lines.append("  growth_factors:")
        for k, v in growth_factors.items():
            lines.append(f'    "{k}": {float(v)}')
        lines.append("")

    # Bloque de POIs
    lines.append("pois:")
    for poi in pois_cfg:
        p_id = poi.get("id", "POI_Nuevo")
        loc = poi.get("loc", [0.0, 0.0])
        jobs = int(poi.get("jobs", 5000))
        rad = int(poi.get("radius_m", 750))
        mode = poi.get("mode", "MAX").upper()

        lines.append(f'  - id: "{p_id}"')
        name_val = poi.get("name")
        if isinstance(name_val, dict):
            lines.append("    name:")
            if "es" in name_val:
                lines.append(f'      es: "{name_val["es"]}"')
            if "en" in name_val:
                lines.append(f'      en: "{name_val["en"]}"')
        elif isinstance(name_val, str) and name_val:
            lines.append(f'    name: "{name_val}"')

        if poi.get("type"):
            lines.append(f'    type: "{poi["type"]}"')
        if poi.get("sub_type"):
            lines.append(f'    sub_type: "{poi["sub_type"]}"')

        lines.append(f'    loc: [{loc[0]:.5f}, {loc[1]:.5f}]')
        lines.append(f'    jobs: {jobs}')
        lines.append(f'    radius_m: {rad}')
        lines.append(f'    mode: "{mode}"')

        if isinstance(poi.get("metadata"), dict) and poi["metadata"]:
            lines.append("    metadata:")
            for mk, mv in poi["metadata"].items():
                lines.append(f'      {mk}: "{mv}"')
        lines.append("")

    # Bloque de Places / Toponimia
    if places_cfg:
        lines.append("# Toponimia y Colonias Curadas")
        lines.append("places:")
        for pl in places_cfg:
            pl_name = pl.get("name", "Colonia")
            pl_loc = pl.get("loc", [0.0, 0.0])
            pl_type = pl.get("type", "suburb")
            lines.append(f'  - name: "{pl_name}"')
            lines.append(f'    loc: [{pl_loc[0]:.5f}, {pl_loc[1]:.5f}]')
            lines.append(f'    type: "{pl_type}"')
        lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(content)

    return fpath


def create_new_project(name: str, code: str, creator: str = "Creador", data_dir: str = "") -> Dict[str, Any]:
    clean_code = code.strip().upper()
    s_norm = unicodedata.normalize('NFKD', name.strip().lower()).encode('ascii', 'ignore').decode('utf-8')
    slug = re.sub(r'[^a-zA-Z0-9_-]', '', s_norm.replace(" ", "_"))
    slug = re.sub(r'[-_]+', '_', slug).strip('-_') or clean_code.lower()
    yaml_name = f"{slug}.yaml"
    yaml_path = os.path.join(CITIES_DIR, yaml_name)

    resolved_data_dir = data_dir.strip() if data_dir else os.path.join("data", slug).replace("\\", "/")
    if not os.path.isabs(resolved_data_dir):
        os.makedirs(os.path.join(ROOT_DIR, resolved_data_dir), exist_ok=True)
    else:
        os.makedirs(resolved_data_dir, exist_ok=True)

    initial_data = {
        "city": {
            "code": clean_code,
            "name": name.strip(),
            "description": f"Zona Metropolitana de {name.strip()}",
            "bbox": [-99.3, 19.2, -98.9, 19.6],
            "creator": creator or "Creador",
            "grid_size": 0.0025,
            "min_residents": 10,
            "min_jobs": 3,
            "initial_zoom": 11.5,
            "building_filter_size": 15.0,
            "building_simplification": 0.2,
            "include_ocean": False
        },
        "data_dir": resolved_data_dir,
        "data_exclusions": [],
        "macroeconomics": {
            "tasa_pea": 0.62,
            "til_1_state": 0.45,
            "sample_threshold": 500,
            "default_growth_factor": 1.05,
            "gravity_beta": 0.12,
            "max_distance_km": 50.0,
            "max_pop_size": 150,
            "growth_factors": {}
        },
        "pois": [],
        "places": []
    }

    save_full_city_data(yaml_path, initial_data)
    rel_path = os.path.relpath(yaml_path, ROOT_DIR).replace("\\", "/")
    return {
        "status": "ok",
        "path": rel_path,
        "file": rel_path,
        "filename": yaml_name,
        "data_dir": resolved_data_dir,
        "city": initial_data["city"]
    }


def delete_project(rel_or_abs_path: str, delete_data_folder: bool = False) -> Dict[str, Any]:
    """Elimina el archivo .yaml de la ciudad de forma segura, y opcionalmente su carpeta data local."""
    fpath = _resolve_city_path(rel_or_abs_path)
    if not os.path.exists(fpath):
        raise FileNotFoundError(f"Proyecto no encontrado: {rel_or_abs_path}")

    data_dir_to_clean = None
    if delete_data_folder:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                cdata = yaml.safe_load(f) or {}
            dd = cdata.get("data_dir")
            if dd and not os.path.isabs(dd):
                data_dir_to_clean = os.path.abspath(os.path.join(ROOT_DIR, dd))
        except Exception:
            pass

    os.remove(fpath)

    if data_dir_to_clean and os.path.exists(data_dir_to_clean):
        norm_data = os.path.normcase(os.path.realpath(DATA_DIR))
        norm_target = os.path.normcase(os.path.realpath(data_dir_to_clean))
        if norm_target.startswith(norm_data + os.sep) and norm_target != norm_data:
            import shutil
            shutil.rmtree(data_dir_to_clean, ignore_errors=True)

    return {"status": "ok", "deleted": rel_or_abs_path}


def open_file_location(target_path: str) -> Dict[str, Any]:
    """Abre la ubicación física del archivo o carpeta en el explorador del sistema operativo."""
    if not target_path:
        raise ValueError("Ruta de archivo no proporcionada")

    if os.path.isabs(target_path):
        full_p = os.path.abspath(target_path)
    else:
        full_p = os.path.abspath(os.path.join(ROOT_DIR, target_path))

    # Seguridad: no permitir salir de ROOT_DIR
    norm_root = os.path.normcase(os.path.realpath(ROOT_DIR))
    norm_target = os.path.normcase(os.path.realpath(full_p))
    if not norm_target.startswith(norm_root):
        raise PermissionError(f"Acceso denegado: ruta fuera del proyecto ({target_path})")

    if not os.path.exists(full_p):
        _, ext = os.path.splitext(full_p)
        if ext:
            parent_dir = os.path.dirname(full_p)
            os.makedirs(parent_dir, exist_ok=True)
            full_p = parent_dir
        else:
            os.makedirs(full_p, exist_ok=True)

    import subprocess
    if sys.platform == "win32":
        if os.path.isfile(full_p):
            subprocess.Popen(["explorer", f"/select,{os.path.normpath(full_p)}"])
        else:
            subprocess.Popen(["explorer", os.path.normpath(full_p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if os.path.isfile(full_p) else "", full_p])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(full_p) if os.path.isfile(full_p) else full_p])

    return {"status": "ok", "opened": full_p}


def exclude_data_file(city_file: str, filename: str) -> Dict[str, Any]:
    """Desvincula un archivo de la sesión/configuración del proyecto sin eliminarlo del disco."""
    if not city_file or not filename:
        raise ValueError("Parámetros 'file' y 'filename' requeridos")

    cdata = load_city_data(city_file)
    exclusions = cdata.get("data_exclusions", [])
    clean_fn = os.path.basename(filename)
    if clean_fn not in exclusions:
        exclusions.append(clean_fn)
    cdata["data_exclusions"] = exclusions
    save_full_city_data(city_file, cdata)
    return {"status": "ok", "excluded": clean_fn, "exclusions": exclusions}


def relink_data_file(city_file: str, filename: str) -> Dict[str, Any]:
    """Reactiva un archivo previamente desvinculado."""
    if not city_file or not filename:
        raise ValueError("Parámetros 'file' y 'filename' requeridos")

    cdata = load_city_data(city_file)
    exclusions = cdata.get("data_exclusions", [])
    clean_fn = os.path.basename(filename)
    if clean_fn in exclusions:
        exclusions.remove(clean_fn)
    cdata["data_exclusions"] = exclusions
    save_full_city_data(city_file, cdata)
    return {"status": "ok", "relinked": clean_fn, "exclusions": exclusions}


def set_project_data_dir(city_file: str, new_dir: str) -> Dict[str, Any]:
    """Actualiza la carpeta de datos personalizada del proyecto."""
    if not city_file:
        raise ValueError("Parámetro 'file' requerido")

    cdata = load_city_data(city_file)
    cdata["data_dir"] = new_dir.strip()
    save_full_city_data(city_file, cdata)
    return {"status": "ok", "data_dir": cdata["data_dir"]}


def inspect_data_files(city_name: str = "", city_code: str = "", city_file: str = "", data_dir_override: str = "") -> Dict[str, Any]:
    """
    Escanea ÚNICAMENTE la carpeta de datos asignada al proyecto.
    Cero escaneo en carpetas de otras ciudades o en la raíz para evitar duplicados.
    """
    target_dir = None
    exclusions = set()

    if city_file:
        try:
            cdata = load_city_data(city_file)
            cfg_dir = cdata.get("data_dir")
            if cfg_dir:
                target_dir = cfg_dir if os.path.isabs(cfg_dir) else os.path.join(ROOT_DIR, cfg_dir)
            for ex in cdata.get("data_exclusions", []):
                exclusions.add(str(ex).strip().lower())
        except Exception:
            pass

    if data_dir_override:
        target_dir = data_dir_override if os.path.isabs(data_dir_override) else os.path.join(ROOT_DIR, data_dir_override)

    if not target_dir:
        slug = ""
        if city_file:
            slug = os.path.splitext(os.path.basename(city_file))[0].lower()
        elif city_code:
            slug = city_code.lower()
        elif city_name:
            slug = city_name.lower().split()[0]

        target_dir = os.path.join(DATA_DIR, slug) if slug else DATA_DIR

    try:
        rel_active_dir = os.path.relpath(target_dir, ROOT_DIR).replace("\\", "/")
    except ValueError:
        rel_active_dir = target_dir.replace("\\", "/")

    search_dirs = [target_dir] if os.path.exists(target_dir) else []

    def find_files(patterns: List[str]) -> List[Dict[str, Any]]:
        found = []
        seen = set()
        for sdir in search_dirs:
            for pat in patterns:
                for fpath in glob.glob(os.path.join(sdir, pat)):
                    abs_p = os.path.abspath(fpath)
                    fname = os.path.basename(abs_p)
                    if fname.lower() in exclusions:
                        continue
                    if abs_p not in seen and os.path.isfile(abs_p):
                        seen.add(abs_p)
                        size_mb = os.path.getsize(abs_p) / (1024 * 1024)
                        try:
                            rel_p = os.path.relpath(abs_p, ROOT_DIR).replace("\\", "/")
                        except ValueError:
                            rel_p = abs_p.replace("\\", "/")
                        found.append({
                            "path": rel_p,
                        "abs_path": abs_p,
                            "filename": fname,
                            "size_mb": round(size_mb, 2),
                            "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(abs_p)))
                        })
        return found

    denue = find_files(["*denue*.csv", "*DENUE*.csv", "*denue*.zip"])
    cpv = find_files(["*RESAGEBURB*.csv", "*resageburb*.csv", "*censo*.csv", "*cpv*.csv"])
    ce2024 = find_files(["*SAIC*.csv", "*saic*.csv", "*exporta*.csv", "*tr_ce*.csv", "*ce2024*.csv", "*ce_2024*.csv", "*ce*.csv"])
    conapo = find_files(["*pobproy*.csv", "*quinq*.csv", "*pob_proy*.csv", "*conapo*.csv", "data-*.csv", "*proyeccion*.csv"])

    # Para OSM: buscar en la carpeta del proyecto, y solo si falta, verificar extracto nacional en data/
    osm = find_files(["*.osm.pbf", "*.osm", "roads.geojson"])
    if not osm and os.path.exists(DATA_DIR):
        for fpath in glob.glob(os.path.join(DATA_DIR, "*.osm.pbf")):
            abs_p = os.path.abspath(fpath)
            fname = os.path.basename(abs_p)
            if fname.lower() not in exclusions and os.path.isfile(abs_p):
                size_mb = os.path.getsize(abs_p) / (1024 * 1024)
                osm.append({
                    "path": os.path.relpath(abs_p, ROOT_DIR).replace("\\", "/"),
                    "abs_path": abs_p,
                    "filename": fname,
                    "size_mb": round(size_mb, 2),
                    "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(abs_p)))
                })

    return {
        "active_dir": rel_active_dir,
        "abs_active_dir": os.path.abspath(target_dir),
        "dir_exists": os.path.exists(target_dir),
        "exclusions": list(exclusions),
        "denue": {"status": "ok" if denue else "missing", "files": denue},
        "cpv": {"status": "ok" if cpv else "missing", "files": cpv},
        "ce2024": {"status": "ok" if ce2024 else "missing", "files": ce2024},
        "conapo": {"status": "ok" if conapo else "missing", "files": conapo},
        "osm": {"status": "ok" if osm else "missing", "files": osm},
        "all_ready": bool(denue and cpv)
    }


def exclude_data_file(city_file: str, filename: str) -> Dict[str, Any]:
    """
    Desvincula un archivo de datos añadiéndolo a 'data_exclusions' en el YAML de la ciudad.
    NO BORRA el archivo físico del disco.
    """
    fpath = _resolve_city_path(city_file)
    if not os.path.exists(fpath):
        raise FileNotFoundError(f"Proyecto no encontrado: {city_file}")

    with open(fpath, "r", encoding="utf-8") as f:
        cdata = yaml.safe_load(f) or {}

    exclusions = cdata.get("data_exclusions", [])
    clean_fn = os.path.basename(filename).strip()
    if clean_fn and clean_fn not in exclusions:
        exclusions.append(clean_fn)
        cdata["data_exclusions"] = exclusions
        save_full_city_data(fpath, cdata)

    return {"status": "ok", "excluded": clean_fn, "exclusions": exclusions}


def relink_data_file(city_file: str, filename: str) -> Dict[str, Any]:
    """
    Vuelve a vincular un archivo previamente excluido eliminándolo de 'data_exclusions'.
    """
    fpath = _resolve_city_path(city_file)
    if not os.path.exists(fpath):
        raise FileNotFoundError(f"Proyecto no encontrado: {city_file}")

    with open(fpath, "r", encoding="utf-8") as f:
        cdata = yaml.safe_load(f) or {}

    exclusions = cdata.get("data_exclusions", [])
    clean_fn = os.path.basename(filename).strip()
    if clean_fn in exclusions:
        exclusions.remove(clean_fn)
        cdata["data_exclusions"] = exclusions
        save_full_city_data(fpath, cdata)

    return {"status": "ok", "relinked": clean_fn, "exclusions": exclusions}


def delete_data_file(rel_or_abs_path: str) -> str:
    """
    Función de compatibilidad: desvincula sin eliminar físicamente.
    """
    return rel_or_abs_path


def calculate_conapo_factors(city_file: str) -> Dict[str, Any]:
    """
    Calcula automáticamente los factores de sincronización intercensal CONAPO
    cruzando las proyecciones (data-*.csv, *conapo*.csv, *pobproy*.csv) con el Censo CPV 2020.
    Retorna nombres legibles, poblaciones 2020, poblaciones proyectadas,
    año de proyección y si el municipio intersecta el BBOX.
    """
    import pandas as pd
    import numpy as np

    try:
        cdata = load_city_data(city_file)
    except Exception as e:
        return {"status": "error", "message": f"No se pudo cargar la ciudad: {e}", "factors": []}

    city_cfg = cdata.get("city", {})
    city_code = city_cfg.get("code", "")
    city_name = city_cfg.get("name", "")
    bbox = city_cfg.get("bbox", [])

    status = inspect_data_files(city_name=city_name, city_code=city_code, city_file=city_file)
    conapo_files = status.get("conapo", {}).get("files", [])
    cpv_files = status.get("cpv", {}).get("files", [])
    denue_files = status.get("denue", {}).get("files", [])

    if not conapo_files:
        return {
            "status": "missing_conapo",
            "message": "No se detectó ningún archivo de proyecciones CONAPO en data/.",
            "factors": []
        }

    conapo_path = os.path.join(ROOT_DIR, conapo_files[0]["path"])

    # 1. Parsear CONAPO de forma vectorizada de alto rendimiento
    conapo_dict = {}
    proj_year = 2024
    for enc in ['utf-8-sig', 'latin1', 'utf-8', 'cp1252']:
        try:
            df_con = pd.read_csv(conapo_path, encoding=enc, low_memory=False)
            cols = [str(c).strip().upper() for c in df_con.columns]
            df_con.columns = cols

            if 'CLAVE' in cols and any('POB' in c for c in cols):
                pob_candidates = [c for c in cols if 'POB_TOTAL' in c or 'POB_MIT_MUN' in c or 'POBTOT' in c or c.startswith('POB')]
                col_pob = pob_candidates[0]
                has_nom = 'NOM_MUN' in cols
                has_ano = 'ANO' in cols

                if has_ano:
                    df_con['ANO_num'] = pd.to_numeric(df_con['ANO'], errors='coerce')
                    available_years = df_con['ANO_num'].dropna().unique()
                    if len(available_years) > 0:
                        chosen_year = 2024 if 2024 in available_years else (
                            available_years[np.argmin(np.abs(available_years - 2024))]
                        )
                        proj_year = int(chosen_year)
                        df_con = df_con[df_con['ANO_num'] == chosen_year]

                df_con['cve_clean'] = pd.to_numeric(df_con['CLAVE'], errors='coerce').fillna(0).astype(int)
                df_con = df_con[df_con['cve_clean'] > 0]
                df_con['pob_clean'] = pd.to_numeric(df_con[col_pob].astype(str).str.replace(',', ''), errors='coerce').fillna(0)

                if has_nom:
                    grouped = df_con.groupby(['cve_clean', 'NOM_MUN'])['pob_clean'].sum().reset_index()
                    for _, r in grouped.iterrows():
                        cve_5 = f"{int(r['cve_clean']):05d}"
                        pob_val = float(r['pob_clean'])
                        nom_mun = str(r['NOM_MUN']).strip()
                        if pob_val > 0:
                            conapo_dict[cve_5] = {
                                "pob_conapo": pob_val,
                                "name": nom_mun
                            }
                else:
                    grouped = df_con.groupby('cve_clean')['pob_clean'].sum()
                    for cve_num, pob_val in grouped.items():
                        cve_5 = f"{int(cve_num):05d}"
                        if pob_val > 0:
                            conapo_dict[cve_5] = {
                                "pob_conapo": float(pob_val),
                                "name": f"Municipio {cve_5}"
                            }

                if conapo_dict:
                    break
        except Exception:
            continue

    if not conapo_dict:
        return {
            "status": "error",
            "message": f"No se pudieron leer proyecciones válidas en {os.path.basename(conapo_path)}",
            "factors": []
        }

    # 2. Parsear Censo CPV 2020 por municipio
    cpv_totals = {}
    if cpv_files:
        for finfo in cpv_files:
            cpv_path = os.path.join(ROOT_DIR, finfo["path"])
            for enc in ['utf-8-sig', 'latin1', 'utf-8']:
                try:
                    df_cen = pd.read_csv(cpv_path, encoding=enc, low_memory=False, dtype=str)
                    df_cen.columns = [c.strip().upper() for c in df_cen.columns]
                    if 'ENTIDAD' in df_cen.columns and 'MUN' in df_cen.columns and 'POBTOT' in df_cen.columns:
                        # Prioridad 1: Registros de totales municipales oficiales de INEGI (LOC == 0000, AGEB == 0000)
                        tot_mun = df_cen[(df_cen.get('LOC', '') == '0000') & (df_cen['MUN'] != '000') & (df_cen.get('AGEB', '') == '0000')]
                        if not tot_mun.empty:
                            for _, r in tot_mun.iterrows():
                                try:
                                    cve_mun = f"{int(str(r['ENTIDAD']).strip()):02d}{int(str(r['MUN']).strip()):03d}"
                                    p_val = float(str(r['POBTOT']).replace(',', '').strip())
                                    if p_val > 0:
                                        cpv_totals[cve_mun] = p_val
                                except Exception:
                                    continue
                        else:
                            # Prioridad 2: Suma de manzanas urbanas habitadas
                            mza_col = pd.to_numeric(df_cen.get('MZA', '1'), errors='coerce').fillna(0)
                            pob_col = pd.to_numeric(df_cen['POBTOT'].replace('*', '1.5'), errors='coerce').fillna(0)
                            df_sub = df_cen[(mza_col > 0) & (pob_col > 0)]
                            for ent, mun, p in zip(df_sub['ENTIDAD'], df_sub['MUN'], pob_col[df_sub.index]):
                                try:
                                    cve_mun = f"{int(str(ent).strip()):02d}{int(str(mun).strip()):03d}"
                                    cpv_totals[cve_mun] = cpv_totals.get(cve_mun, 0.0) + float(p)
                                except Exception:
                                    continue
                        if cpv_totals:
                            break
                except Exception:
                    continue

    # 3. Detectar municipios que intersectan estrictamente el BBOX vía DENUE
    bbox_muns = set()
    if denue_files and bbox and len(bbox) == 4:
        min_lon = min(float(bbox[0]), float(bbox[2]))
        max_lon = max(float(bbox[0]), float(bbox[2]))
        min_lat = min(float(bbox[1]), float(bbox[3]))
        max_lat = max(float(bbox[1]), float(bbox[3]))
        for finfo in denue_files:
            denue_path = os.path.join(ROOT_DIR, finfo["path"])
            for enc in ['utf-8-sig', 'latin1', 'utf-8']:
                try:
                    df_den = pd.read_csv(denue_path, encoding=enc, low_memory=False, dtype=str)
                    df_den.columns = [c.strip().lower() for c in df_den.columns]
                    if 'longitud' in df_den.columns and 'latitud' in df_den.columns:
                        lons = pd.to_numeric(df_den['longitud'], errors='coerce')
                        lats = pd.to_numeric(df_den['latitud'], errors='coerce')
                        mask = (lons >= min_lon) & (lons <= max_lon) & (lats >= min_lat) & (lats <= max_lat)
                        df_in_bbox = df_den[mask]
                        if 'cve_ent' in df_den.columns and 'cve_mun' in df_den.columns:
                            for ent, mun in zip(df_in_bbox['cve_ent'].dropna(), df_in_bbox['cve_mun'].dropna()):
                                try:
                                    bbox_muns.add(f"{int(str(ent).strip()):02d}{int(str(mun).strip()):03d}")
                                except Exception:
                                    pass
                        elif 'cve_mun' in df_den.columns:
                            for m in df_in_bbox['cve_mun'].dropna().unique():
                                try:
                                    m_str = str(m).strip()
                                    if len(m_str) >= 5:
                                        bbox_muns.add(f"{int(m_str):05d}")
                                    elif cpv_totals:
                                        ent_cand = list(cpv_totals.keys())[0][:2]
                                        bbox_muns.add(f"{ent_cand}{int(m_str):03d}")
                                except Exception:
                                    pass
                        if bbox_muns:
                            break
                except Exception:
                    continue

    # 4. Formar lista de resultados (filtrando estrictamente por municipios dentro del BBOX)
    factors_list = []
    target_muns = sorted(bbox_muns) if bbox_muns else sorted(cpv_totals.keys() if cpv_totals else conapo_dict.keys())

    for cve_5 in target_muns:
        c_info = conapo_dict.get(cve_5)
        pob_2020 = cpv_totals.get(cve_5, 0.0)
        pob_proj = c_info["pob_conapo"] if c_info else pob_2020
        nom_mun = c_info["name"] if c_info else f"Municipio {cve_5}"

        if pob_2020 > 0 and pob_proj > 0:
            ratio = float(np.clip(pob_proj / pob_2020, 0.90, 1.60))
            calc_factor = round(ratio, 2)
        else:
            calc_factor = 1.05

        factors_list.append({
            "cve_mun": cve_5,
            "name": nom_mun,
            "pob_2020": int(pob_2020) if pob_2020 > 0 else None,
            "pob_conapo": int(pob_proj) if pob_proj > 0 else None,
            "factor": calc_factor,
            "in_bbox": True
        })

    return {
        "status": "ok",
        "conapo_file": os.path.basename(conapo_path),
        "projection_year": proj_year,
        "factors": factors_list
    }


def broadcast_log(line: str, progress: Optional[int] = None, step_name: Optional[str] = None):
    """Envía un mensaje de log y estado a todos los clientes conectados por SSE."""
    msg = {
        "timestamp": time.strftime("%H:%M:%S"),
        "line": line
    }
    if progress is not None:
        active_build["progress"] = progress
        msg["progress"] = progress
    if step_name is not None:
        active_build["step_name"] = step_name
        msg["step_name"] = step_name

    active_build["logs"].append(msg)
    if len(active_build["logs"]) > 2000:
        active_build["logs"].pop(0)

    dead_queues = []
    for q in active_build["log_queues"]:
        try:
            q.put_nowait(msg)
        except Exception:
            dead_queues.append(q)
    for dq in dead_queues:
        if dq in active_build["log_queues"]:
            active_build["log_queues"].remove(dq)


class LogCaptureStream:
    """Stream para interceptar stdout/stderr y transmitirlo a la consola web."""
    def __init__(self, original_stream):
        self.original_stream = original_stream

    def write(self, text):
        self.original_stream.write(text)
        if text.strip():
            for line in text.splitlines():
                if line.strip():
                    broadcast_log(line)

    def flush(self):
        self.original_stream.flush()


def run_pipeline_task(config_file: str, skip_map: bool = False):
    """Ejecuta el pipeline de compilación de Subway Builder México en un hilo en segundo plano."""
    global active_build
    try:
        from sb_mexico.pipeline import execute_pipeline

        with build_lock:
            active_build["running"] = True
            active_build["status"] = "running"
            active_build["progress"] = 5
            active_build["step_name"] = "Iniciando Pipeline"
            active_build["logs"].clear()
            active_build["error"] = None

        broadcast_log(f"🚀 Iniciando compilación para '{config_file}'...", progress=10, step_name="Cargando Configuración")

        # Resolver data_dir inteligente priorizando el configurado en la ciudad
        effective_data_dir = None
        try:
            cdata = load_city_data(config_file)
            cfg_dir = cdata.get("data_dir")
            if cfg_dir:
                effective_data_dir = cfg_dir if os.path.isabs(cfg_dir) else os.path.join(ROOT_DIR, cfg_dir)
        except Exception:
            pass

        if not effective_data_dir or not os.path.exists(effective_data_dir):
            city_base = os.path.splitext(os.path.basename(config_file))[0].lower()
            cand = os.path.join(DATA_DIR, city_base)
            effective_data_dir = cand if os.path.exists(cand) else DATA_DIR

        old_stdout = sys.stdout
        sys.stdout = LogCaptureStream(old_stdout)

        try:
            if not skip_map:
                broadcast_log("🗺️ Ejecutando compilación cartográfica 3D (MapGen vía WSL 2)...", progress=15, step_name="Cartografía 3D")
            else:
                broadcast_log("📊 Procesando fuentes de datos INEGI y Modelo Gravitatorio...", progress=30, step_name="Ingesta INEGI")
            city_base = os.path.splitext(os.path.basename(config_file))[0].lower()
            city_out_dir = os.path.join(DIST_DIR, city_base)
            os.makedirs(city_out_dir, exist_ok=True)
            resolved_config = _resolve_city_path(config_file)
            execute_pipeline(
                config_path=resolved_config,
                skip_map=skip_map,
                output_dir=city_out_dir,
                data_dir=effective_data_dir
            )
            broadcast_log("✨ ¡Compilación y empaquetado final completados con éxito!", progress=100, step_name="Finalizado")
            with build_lock:
                active_build["running"] = False
                active_build["status"] = "success"
                active_build["progress"] = 100
                active_build["step_name"] = "Completado"
        finally:
            sys.stdout = old_stdout

    except Exception as e:
        import traceback
        err_trace = traceback.format_exc()
        broadcast_log(f"❌ ERROR CRÍTICO EN PIPELINE: {e}", progress=100, step_name="Error en Compilación")
        for line in err_trace.strip().splitlines():
            if line.strip():
                broadcast_log(f"   {line}")
        print(err_trace, file=sys.stderr)
        with build_lock:
            active_build["running"] = False
            active_build["status"] = "error"
            active_build["error"] = str(e)
            active_build["step_name"] = "Error"


# =============================================================================
# MANEJADOR HTTP PRINCIPAL
# =============================================================================

class WizardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ["/", "/index.html"]:
            self.serve_html()
        elif path in ["/api/cities", "/api/projects"]:
            self.serve_json({"cities": get_available_cities()})
        elif path == "/api/system-check":
            from sb_mexico.cartography import is_wsl_available
            wsl_ok, distro, tools = is_wsl_available()
            self.serve_json({
                "status": "ok",
                "platform": sys.platform,
                "wsl_ready": wsl_ok,
                "distro": distro,
                "tools": tools
            })
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
        elif path == "/api/data-status":
            city_code = query.get("city", [""])[0]
            city_name = query.get("name", [""])[0]
            city_file = query.get("file", [""])[0]
            data_dir = query.get("data_dir", [""])[0]
            status = inspect_data_files(city_name=city_name, city_code=city_code, city_file=city_file, data_dir_override=data_dir)
            self.serve_json(status)
        elif path == "/api/conapo/calculate":
            city_file = query.get("file", [""])[0]
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            factors_res = calculate_conapo_factors(city_file)
            self.serve_json(factors_res)
        elif path == "/api/density":
            from tools.poi_studio import load_demand_sample
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
        elif path == "/api/settlement_suggestions":
            try:
                city_file = query.get("file", [""])[0]
                from tools.poi_studio import load_city_data as l_city
                from sb_mexico.toponymy import extract_settlement_suggestions
                
                cdata = l_city(city_file) if city_file else {}
                bbox = cdata.get("city", {}).get("bbox")
                st = inspect_data_files(city_file=city_file)
                denue_files = st.get("denue", {}).get("files", [])

                if denue_files and bbox:
                    first_f = denue_files[0]
                    denue_path = first_f.get("abs_path") or os.path.join(ROOT_DIR, first_f["path"])
                    suggs = extract_settlement_suggestions(denue_path, bbox, min_count=10)
                else:
                    suggs = []
                self.serve_json({"suggestions": suggs})
            except Exception as e:
                self.serve_json({"suggestions": [], "error": str(e)})
        elif path == "/api/demand-preview":
            city_file = query.get("file", [""])[0]
            city_base = os.path.splitext(os.path.basename(city_file))[0].lower() if city_file else ""

            # Buscar demand_data.json EXCLUSIVAMENTE dentro de dist/<city_base>/
            target_path = os.path.join(DIST_DIR, city_base, "demand_data.json") if city_base else ""
            if target_path and os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        demand_json = json.load(f)
                    self.serve_json(demand_json)
                except Exception as e:
                    self.serve_error(f"Error al leer demand_data.json de {city_base}: {e}", 500)
            else:
                self.serve_json({"points": [], "metadata": {"status": "not_compiled", "city": city_base}})
        elif path == "/api/build/stream":
            self.serve_sse_stream()
        elif path == "/api/build/status":
            self.serve_json(active_build)
        elif path == "/api/download":
            city_file = query.get("file", [""])[0]
            city_base = os.path.splitext(os.path.basename(city_file))[0].lower() if city_file else ""
            city_code = ""
            if city_file:
                try:
                    cdata = load_city_data(city_file)
                    city_code = cdata.get("city", {}).get("code", "").upper()
                except Exception:
                    pass

            zip_candidates = []
            if city_base:
                zip_candidates.extend(glob.glob(os.path.join(DIST_DIR, city_base, "*.zip")))
                zip_candidates.extend(glob.glob(os.path.join(DIST_DIR, f"*{city_base}*.zip")))
            if city_code:
                zip_candidates.extend(glob.glob(os.path.join(DIST_DIR, f"{city_code}.zip")))
                zip_candidates.extend(glob.glob(os.path.join(DIST_DIR, city_base, f"{city_code}.zip")))

            # Deduplicar preservando orden
            valid_zips = [z for z in dict.fromkeys(zip_candidates) if os.path.isfile(z)]

            if valid_zips:
                target_zip = valid_zips[0]
                with open(target_zip, "rb") as zf:
                    data = zf.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(target_zip)}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.serve_error(f"No se encontró ningún paquete .zip compilado para '{city_base or 'este proyecto'}'. Debes compilarlo primero.", 404)
        else:
            self.serve_error("Ruta no encontrada", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/city/save":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                saved_path = save_full_city_data(city_file, req_data)
                self.serve_json({"status": "ok", "saved_path": saved_path})
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path in ["/api/project/new", "/api/city/create"]:
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                name = req_data.get("name", "").strip()
                code = req_data.get("code", "").strip()
                creator = req_data.get("creator", "Creador").strip()
                data_dir = req_data.get("data_dir", "").strip()

                if not name or not code:
                    self.serve_error("Nombre y código son obligatorios", 400)
                    return

                proj_info = create_new_project(name, code, creator, data_dir)
                self.serve_json(proj_info)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/project/delete":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                delete_data = bool(req_data.get("delete_data_folder", False))
                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                del_res = delete_project(city_file, delete_data)
                self.serve_json(del_res)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/data/open-location":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                target_path = req_data.get("path")
                if not target_path:
                    self.serve_error("Falta el parámetro 'path'", 400)
                    return

                res = open_file_location(target_path)
                self.serve_json(res)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path in ["/api/data/unlink", "/api/data/exclude", "/api/data/delete"]:
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                filename = req_data.get("filename") or os.path.basename(req_data.get("path", ""))

                if not city_file or not filename:
                    self.serve_error("Faltan parámetros 'file' o 'filename'", 400)
                    return

                # Desvincular de la configuración SIN BORRAR DEL DISCO
                res = exclude_data_file(city_file, filename)
                self.serve_json(res)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/data/relink":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                filename = req_data.get("filename")

                if not city_file or not filename:
                    self.serve_error("Faltan parámetros 'file' o 'filename'", 400)
                    return

                res = relink_data_file(city_file, filename)
                self.serve_json(res)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/data/set-directory":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                new_dir = req_data.get("data_dir", "").strip()

                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                res = set_project_data_dir(city_file, new_dir)
                self.serve_json(res)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/upload":
            try:
                content_type = self.headers.get('Content-Type', '')
                content_len = int(self.headers.get('Content-Length', 0))

                if "multipart/form-data" not in content_type:
                    self.serve_error("Se esperaba multipart/form-data", 400)
                    return

                boundary = content_type.split("boundary=")[-1].strip().encode('utf-8')
                body = self.rfile.read(content_len)

                # Resolver destino de subcarpeta priorizando data_dir del proyecto
                city_file_param = query.get("file", [""])[0]
                city_param = query.get("city", [""])[0] or query.get("folder", [""])[0]

                target_dir = None
                if city_file_param:
                    try:
                        cdata = load_city_data(city_file_param)
                        cfg_d = cdata.get("data_dir")
                        if cfg_d:
                            target_dir = cfg_d if os.path.isabs(cfg_d) else os.path.join(ROOT_DIR, cfg_d)
                    except Exception:
                        pass

                if not target_dir:
                    target_sub = ""
                    if city_param:
                        target_sub = os.path.basename(city_param).lower()
                    elif city_file_param:
                        target_sub = os.path.splitext(os.path.basename(city_file_param))[0].lower()
                    target_dir = os.path.join(DATA_DIR, target_sub) if target_sub else DATA_DIR

                os.makedirs(target_dir, exist_ok=True)

                parts = body.split(b"--" + boundary)
                uploaded_files = []

                for part in parts:
                    if b'filename="' in part:
                        header_part, file_bytes = part.split(b"\r\n\r\n", 1)
                        file_bytes = file_bytes.rstrip(b"\r\n")

                        headers_str = header_part.decode('utf-8', errors='ignore')
                        fn_match = [line for line in headers_str.split("\r\n") if 'filename="' in line]
                        if not fn_match:
                            continue
                        
                        raw_fn = fn_match[0].split('filename="')[-1].split('"')[0]
                        clean_fn = os.path.basename(raw_fn)

                        if clean_fn:
                            out_path = os.path.join(target_dir, clean_fn)
                            with open(out_path, "wb") as out_f:
                                out_f.write(file_bytes)
                            
                            try:
                                rel_out = os.path.relpath(out_path, ROOT_DIR).replace("\\", "/")
                            except ValueError:
                                rel_out = out_path.replace("\\", "/")

                            uploaded_files.append({
                                "filename": clean_fn,
                                "size_mb": round(len(file_bytes) / (1024 * 1024), 2),
                                "path": rel_out,
                                "abs_path": out_path
                            })

                try:
                    display_target = os.path.relpath(target_dir, ROOT_DIR).replace("\\", "/")
                except ValueError:
                    display_target = target_dir.replace("\\", "/")

                self.serve_json({
                    "status": "ok",
                    "uploaded_count": len(uploaded_files),
                    "target_dir": display_target,
                    "files": uploaded_files
                })
            except Exception as e:
                self.serve_error(f"Error al subir archivo: {e}", 500)

        elif path == "/api/build/start":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                skip_map = bool(req_data.get("skip_map", False))

                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                if active_build["running"]:
                    self.serve_error("Ya hay una compilación en progreso", 409)
                    return

                thread = threading.Thread(
                    target=run_pipeline_task,
                    args=(city_file, skip_map),
                    daemon=True
                )
                thread.start()

                self.serve_json({"status": "started", "file": city_file})
            except Exception as e:
                self.serve_error(str(e), 500)

        else:
            self.serve_error("Método no permitido", 405)

    def serve_sse_stream(self):
        """Streaming de eventos Server-Sent Events (SSE) para logs en tiempo real."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        log_q = queue.Queue()
        active_build["log_queues"].append(log_q)

        for past_log in active_build["logs"][-50:]:
            data_str = json.dumps(past_log, ensure_ascii=False)
            self.wfile.write(f"data: {data_str}\n\n".encode('utf-8'))
        self.wfile.flush()

        try:
            while True:
                try:
                    msg = log_q.get(timeout=15.0)
                    data_str = json.dumps(msg, ensure_ascii=False)
                    self.wfile.write(f"data: {data_str}\n\n".encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if log_q in active_build["log_queues"]:
                active_build["log_queues"].remove(log_q)

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

    for attempt in range(5):
        try:
            httpd = ThreadingHTTPServer(server_address, WizardRequestHandler)
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

    print("=" * 65)
    print(" 🚇 SUBWAY BUILDER MÉXICO v6.3 - WIZARD STUDIO")
    print(" 🎨 Identidad Gráfica: Metro CDMX / Lance Wyman Standard")
    print("=" * 65)
    print(f" Servidor iniciado en: {url}")
    print(f" Raíz del proyecto:    {ROOT_DIR}")
    print(f" Presiona Ctrl+C para detener el servidor.")
    print("=" * 65)

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Servidor detenido por el usuario.")
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(
        description="Subway Builder México Wizard v6.3 - Suite Integral de Modelación"
    )
    parser.add_argument(
        "--city",
        default=None,
        help="Archivo YAML de ciudad inicial a cargar (ej. cities/cancun.yaml)"
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
