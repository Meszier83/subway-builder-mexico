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
    Carga puntos de demanda reales de forma estrictamente aislada:
    1. dist/<ciudad>/demand_data.json (si este proyecto específico ya fue compilado).
    2. DENUE en tiempo real EXCLUSIVAMENTE desde la carpeta asignada a este proyecto (data_dir).
    Si el proyecto no ha sido compilado y no tiene DENUE propio, retorna [] (cero contaminación cruzada).
    """
    city_base = ""
    target_data_dir = None
    if city_file:
        city_base = os.path.splitext(os.path.basename(city_file))[0].lower()
        try:
            cdata = load_city_data(city_file)
            cfg_dir = cdata.get("data_dir")
            if cfg_dir:
                target_data_dir = cfg_dir if os.path.isabs(cfg_dir) else os.path.join(ROOT_DIR, cfg_dir)
            if not bbox:
                bbox = cdata.get("city", {}).get("bbox")
        except Exception:
            pass

    if not target_data_dir and city_base:
        cand_dir = os.path.join(ROOT_DIR, "data", city_base)
        if os.path.exists(cand_dir):
            target_data_dir = cand_dir

    # 1. Buscar demand_data.json compilado EXCLUSIVAMENTE para esta ciudad
    if city_base:
        city_demand_path = os.path.join(ROOT_DIR, "dist", city_base, "demand_data.json")
        if os.path.exists(city_demand_path):
            try:
                with open(city_demand_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                points = data.get("points", [])
                if bbox and len(bbox) == 4:
                    points = [
                        p for p in points
                        if bbox[0] <= p["location"][0] <= bbox[2] and bbox[1] <= p["location"][1] <= bbox[3]
                    ]
                if points:
                    return points
            except Exception as e:
                print(f"[WARN] Error al leer {city_demand_path}: {e}")

    # Estrategia 2: Si no hay demand_data compilado para esta ciudad,
    # procesar DENUE en tiempo real ÚNICAMENTE desde su propia carpeta de datos
    if target_data_dir and os.path.exists(target_data_dir) and bbox and len(bbox) == 4:
        denue_files = glob.glob(os.path.join(target_data_dir, "*denue*.csv")) + glob.glob(os.path.join(target_data_dir, "*DENUE*.csv"))
        if denue_files:
            try:
                import pandas as pd
                import math
                df = pd.read_csv(denue_files[0], encoding='latin1', low_memory=False, dtype=str)
                lat_cols = [c for c in df.columns if 'latitud' in c.lower()]
                lon_cols = [c for c in df.columns if 'longitud' in c.lower()]
                if lat_cols and lon_cols:
                    lat_col = lat_cols[0]
                    lon_col = lon_cols[0]

                    df['lat'] = pd.to_numeric(df[lat_col], errors='coerce')
                    df['lon'] = pd.to_numeric(df[lon_col], errors='coerce')

                    df = df[
                        (df['lon'] >= bbox[0]) & (df['lon'] <= bbox[2]) &
                        (df['lat'] >= bbox[1]) & (df['lat'] <= bbox[3])
                    ].dropna(subset=['lat', 'lon'])

                    # Bins de agregación espacial rápida (~250m)
                    grid_size = 0.0025
                    grid = {}
                    for _, r in df.iterrows():
                        r_lon = r['lon']
                        r_lat = r['lat']
                        k = f"{int(math.floor(r_lon / grid_size))}_{int(math.floor(r_lat / grid_size))}"
                        if k not in grid:
                            grid[k] = {'sum_lon': 0.0, 'sum_lat': 0.0, 'count': 0}
                        grid[k]['sum_lon'] += r_lon
                        grid[k]['sum_lat'] += r_lat
                        grid[k]['count'] += 1

                    pts = []
                    for idx, (k, cell) in enumerate(grid.items()):
                        c_lon = cell['sum_lon'] / cell['count']
                        c_lat = cell['sum_lat'] / cell['count']
                        pts.append({
                            'id': f"denue_{idx+1:04d}",
                            'location': [round(c_lon, 5), round(c_lat, 5)],
                            'jobs': int(cell['count'] * 4.5),
                            'residents': 0
                        })
                    return pts
            except Exception as e:
                print(f"[WARN] Error al procesar DENUE en tiempo real para {city_base}: {e}")

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
