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
import glob
import json
import yaml
import time
import queue
import shutil
import logging
import argparse
import threading
import webbrowser
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

# Asegurar que sb_mexico esté en sys.path
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

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

    return data


def save_full_city_data(rel_or_abs_path: str, data: Dict[str, Any]) -> str:
    """Guarda la configuración completa de la ciudad respetando el esquema oficial."""
    fpath = _resolve_city_path(rel_or_abs_path)
    os.makedirs(os.path.dirname(fpath), exist_ok=True)

    city_cfg = data.get("city", {})
    macro_cfg = data.get("macroeconomics", {})
    pois_cfg = data.get("pois", [])
    places_cfg = data.get("places", [])

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
        "",
        "macroeconomics:",
        f'  tasa_pea: {float(macro_cfg.get("tasa_pea", 0.62))}',
        f'  til_1_state: {float(macro_cfg.get("til_1_state", 0.45))}',
        f'  sample_threshold: {int(macro_cfg.get("sample_threshold", 500))}',
        f'  default_growth_factor: {float(macro_cfg.get("default_growth_factor", 1.05))}',
        f'  gravity_beta: {float(macro_cfg.get("gravity_beta", 0.12))}',
        f'  max_distance_km: {float(macro_cfg.get("max_distance_km", 50.0))}',
        f'  max_pop_size: {int(macro_cfg.get("max_pop_size", 150))}',
        ""
    ]

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


def inspect_data_files(city_name: str = "", city_code: str = "", city_file: str = "") -> Dict[str, Any]:
    """
    Escanea la carpeta data/ y sus subcarpetas para detectar archivos INEGI y OSM.
    Busca por nombre de archivo YAML, clave de ciudad y escaneo recursivo en data/.
    """
    search_dirs = []

    # 1. Candidatos derivados de la ciudad
    candidates = []
    if city_file:
        base = os.path.splitext(os.path.basename(city_file))[0].lower()
        candidates.append(base)
    if city_code:
        candidates.append(city_code.lower())
    if city_name:
        candidates.append(city_name.lower().split()[0])
        candidates.append(city_name.lower().replace(" ", "_"))
        candidates.append(city_name.lower().replace(" ", ""))

    for c in candidates:
        cand_dir = os.path.join(DATA_DIR, c)
        if os.path.exists(cand_dir) and cand_dir not in search_dirs:
            search_dirs.append(cand_dir)

    # 2. Agregar todos los subdirectorios existentes dentro de DATA_DIR
    if os.path.exists(DATA_DIR):
        for entry in os.scandir(DATA_DIR):
            if entry.is_dir() and entry.path not in search_dirs:
                search_dirs.append(entry.path)

    # 3. Directorio base data/ y ROOT_DIR
    if DATA_DIR not in search_dirs:
        search_dirs.append(DATA_DIR)
    if ROOT_DIR not in search_dirs:
        search_dirs.append(ROOT_DIR)

    def find_files(patterns: List[str]) -> List[Dict[str, Any]]:
        found = []
        seen = set()
        for sdir in search_dirs:
            if not os.path.exists(sdir):
                continue
            for pat in patterns:
                for fpath in glob.glob(os.path.join(sdir, pat)):
                    abs_p = os.path.abspath(fpath)
                    if abs_p not in seen and os.path.isfile(abs_p):
                        seen.add(abs_p)
                        size_mb = os.path.getsize(abs_p) / (1024 * 1024)
                        rel_p = os.path.relpath(abs_p, ROOT_DIR).replace("\\", "/")
                        found.append({
                            "path": rel_p,
                            "filename": os.path.basename(abs_p),
                            "size_mb": round(size_mb, 2),
                            "modified": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(abs_p)))
                        })
        return found

    denue = find_files(["*denue*.csv", "*DENUE*.csv", "*denue*.zip"])
    cpv = find_files(["*RESAGEBURB*.csv", "*resageburb*.csv", "*censo*.csv", "*cpv*.csv"])
    ce2024 = find_files(["*tr_ce*.csv", "*ce2024*.csv", "*ce_2024*.csv", "*ce*.csv"])
    conapo = find_files(["*conapo*.csv", "data-*.csv", "*proyeccion*.csv"])
    osm = find_files(["*.osm.pbf", "*.osm", "roads.geojson"])

    return {
        "denue": {"status": "ok" if denue else "missing", "files": denue},
        "cpv": {"status": "ok" if cpv else "missing", "files": cpv},
        "ce2024": {"status": "ok" if ce2024 else "missing", "files": ce2024},
        "conapo": {"status": "ok" if conapo else "missing", "files": conapo},
        "osm": {"status": "ok" if osm else "missing", "files": osm},
        "all_ready": bool(denue and cpv)
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

        # Resolver data_dir inteligente si existe carpeta de la ciudad en data/
        city_base = os.path.splitext(os.path.basename(config_file))[0].lower()
        candidate_data_dirs = [
            os.path.join(DATA_DIR, city_base),
            DATA_DIR,
            ROOT_DIR
        ]
        effective_data_dir = None
        for cd in candidate_data_dirs:
            if os.path.exists(cd) and os.path.isdir(cd):
                if glob.glob(os.path.join(cd, "*denue*.csv")) or glob.glob(os.path.join(cd, "*RESAGEBURB*.csv")):
                    effective_data_dir = cd
                    break

        old_stdout = sys.stdout
        sys.stdout = LogCaptureStream(old_stdout)

        try:
            broadcast_log("📊 Procesando fuentes de datos INEGI y Modelo Gravitatorio...", progress=30, step_name="Ingesta INEGI")
            execute_pipeline(
                config_path=config_file,
                skip_map=skip_map,
                output_dir=ROOT_DIR,
                data_dir=effective_data_dir
            )
            broadcast_log("✨ ¡Compilación completada exitosamente!", progress=100, step_name="Finalizado")
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
        elif path == "/api/data-status":
            city_code = query.get("city", [""])[0]
            city_name = query.get("name", [""])[0]
            city_file = query.get("file", [""])[0]
            status = inspect_data_files(city_name=city_name, city_code=city_code, city_file=city_file)
            self.serve_json(status)
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
                city_base = os.path.splitext(os.path.basename(city_file))[0] if city_file else ""

                denue_candidates = []
                if city_base:
                    denue_candidates.extend(glob.glob(os.path.join(DATA_DIR, city_base, "*denue*.csv")))
                denue_candidates.extend(glob.glob(os.path.join(DATA_DIR, "*denue*.csv")))
                denue_candidates.extend(glob.glob(os.path.join(ROOT_DIR, "*denue*.csv")))

                valid = [c for c in denue_candidates if os.path.isfile(c)]
                if valid and bbox:
                    suggs = extract_settlement_suggestions(valid[0], bbox, min_count=10)
                else:
                    suggs = []
                self.serve_json({"suggestions": suggs})
            except Exception as e:
                self.serve_json({"suggestions": [], "error": str(e)})
        elif path == "/api/demand-preview":
            city_file = query.get("file", [""])[0]
            city_base = os.path.splitext(os.path.basename(city_file))[0] if city_file else ""
            candidates = [
                os.path.join(DIST_DIR, city_base, "demand_data.json") if city_base else "",
                os.path.join(ROOT_DIR, "demand_data.json"),
                os.path.join(DIST_DIR, "demand_data.json")
            ]
            found_path = None
            for c in candidates:
                if c and os.path.exists(c):
                    found_path = c
                    break

            if found_path:
                try:
                    with open(found_path, "r", encoding="utf-8") as f:
                        demand_json = json.load(f)
                    self.serve_json(demand_json)
                except Exception as e:
                    self.serve_error(f"Error al leer demand_data.json: {e}", 500)
            else:
                self.serve_json({"points": [], "metadata": {"status": "not_compiled"}})
        elif path == "/api/build/stream":
            self.serve_sse_stream()
        elif path == "/api/build/status":
            self.serve_json(active_build)
        elif path == "/api/download":
            city_file = query.get("file", [""])[0]
            city_base = os.path.splitext(os.path.basename(city_file))[0] if city_file else ""
            zip_candidates = glob.glob(os.path.join(DIST_DIR, f"*{city_base}*.zip")) or glob.glob(os.path.join(DIST_DIR, "*.zip"))
            if zip_candidates:
                target_zip = zip_candidates[0]
                with open(target_zip, "rb") as zf:
                    data = zf.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(target_zip)}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.serve_error("No se encontró ningún paquete .zip compilado", 404)
        else:
            self.serve_error("Ruta no encontrada", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

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
                self.serve_json({"status": "ok", "path": saved_path})
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/city/create":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                code = req_data.get("code", "NEW").upper()
                name = req_data.get("name", "Nueva Ciudad")
                filename = f"{code.lower()}.yaml"
                fpath = os.path.join(CITIES_DIR, filename)

                if os.path.exists(fpath):
                    self.serve_error(f"Ya existe una ciudad con el código '{code}' ({filename})", 400)
                    return

                default_data = {
                    "city": {
                        "code": code,
                        "name": name,
                        "description": req_data.get("description", f"Zona Metropolitana de {name}"),
                        "bbox": req_data.get("bbox", [-99.25, 19.30, -99.05, 19.50]),
                        "creator": req_data.get("creator", "Creador"),
                        "grid_size": 0.0025,
                        "min_residents": 10,
                        "min_jobs": 3,
                        "initial_zoom": 11.5,
                        "building_filter_size": 15.0,
                        "building_simplification": 0.2,
                        "include_ocean": False
                    },
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
                save_full_city_data(fpath, default_data)
                self.serve_json({"status": "ok", "filename": filename, "file": f"cities/{filename}"})
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
                            target_dir = DATA_DIR
                            os.makedirs(target_dir, exist_ok=True)
                            out_path = os.path.join(target_dir, clean_fn)
                            with open(out_path, "wb") as out_f:
                                out_f.write(file_bytes)
                            
                            uploaded_files.append({
                                "filename": clean_fn,
                                "size_mb": round(len(file_bytes) / (1024 * 1024), 2),
                                "path": os.path.relpath(out_path, ROOT_DIR).replace("\\", "/")
                            })

                self.serve_json({
                    "status": "ok",
                    "uploaded_count": len(uploaded_files),
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
        default="cities/cancun.yaml",
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
