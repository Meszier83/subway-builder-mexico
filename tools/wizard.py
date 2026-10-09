#!/usr/bin/env python3
"""
Subway Builder México v7.1 - Wizard Server
============================================
Servidor local interactivo con API REST, soporte para carga manual de datos,
streaming en vivo de compilación (SSE) y calibración geoespacial integral.

Inspirado en la estética del Metro de la CDMX (Lance Wyman).

Uso:
    python tools/wizard.py
    python tools/wizard.py --city cities/cancun_riviera_maya.yaml
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
import hashlib
import mimetypes
import socket
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional

# Directorios base
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CITIES_DIR = os.path.join(ROOT_DIR, "cities")
DATA_DIR = os.path.join(ROOT_DIR, "data")
DIST_DIR = os.path.join(ROOT_DIR, "dist")
TEMPLATE_HTML_PATH = os.path.join(os.path.dirname(__file__), "templates", "wizard.html")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static", "wizard")
INSTANCE_STARTED_AT = time.time()
INSTANCE_ID = uuid.uuid4().hex
source_download_manager = None
source_download_manager_lock = threading.Lock()
candidate_preview_lock = threading.Lock()
candidate_preview_jobs = {}


def candidate_preview_job(resolved, stage, compute, job_id=None):
    """Keep long candidate calculations outside an idle HTTP connection."""
    from sb_mexico.build_delivery import config_hash
    fingerprint = config_hash(resolved)
    key = (resolved, stage, fingerprint)
    with candidate_preview_lock:
        if job_id:
            job = candidate_preview_jobs.get(job_id)
            if not job or job['key'] != key:
                raise ValueError('La configuración cambió; vuelve a evaluar el candidato.')
        else:
            # A repeated click joins the same active evaluation.
            job = next((j for j in candidate_preview_jobs.values()
                        if j['key'] == key and j['status'] == 'running'), None)
            if job is None:
                for old_id, old in list(candidate_preview_jobs.items()):
                    if old['status'] != 'running' and time.monotonic()-old['finished'] > 1800:
                        del candidate_preview_jobs[old_id]
                if sum(j['status']=='running' for j in candidate_preview_jobs.values()) >= 2:
                    raise ValueError('Hay otras evaluaciones en curso; espera a que terminen.')
                job = dict(id=uuid.uuid4().hex, key=key, status='running')
                candidate_preview_jobs[job['id']] = job
                def worker():
                    try:
                        data = compute()
                        if config_hash(resolved) != fingerprint:
                            raise ValueError('La configuración cambió; vuelve a evaluar el candidato.')
                        update = dict(status='complete', data=data)
                    except Exception as error:
                        update = dict(status='error', error=str(error))
                    with candidate_preview_lock:
                        job.update(update, finished=time.monotonic())
                threading.Thread(target=worker, daemon=True).start()
        if job['status'] == 'complete':
            return dict(job['data'], status='complete', job_id=job['id'])
        return dict(status=job['status'], job_id=job['id'], stage=stage, error=job.get('error'))


def get_source_download_manager():
    global source_download_manager
    from sb_mexico.source_downloads import DownloadManager
    with source_download_manager_lock:
        if source_download_manager is None:
            source_download_manager = DownloadManager(ROOT_DIR, DATA_DIR, load_city_data, _resolve_city_path)
        return source_download_manager

with open(__file__, 'rb') as _wizard_source:
    INSTANCE_CODE_SHA256 = hashlib.sha256(_wizard_source.read()).hexdigest()
system_health_lock = threading.Lock()
system_health_cache = None
system_health_running = False


def probe_system_health():
    """Bounded read-only preflight; opening a page must not restart WSL."""
    result = {'status': 'ok', 'platform': sys.platform, 'wsl_ready': False,
              'distro': '', 'tools': {}, 'checked_at': time.time()}
    if sys.platform != 'win32':
        result.update(wsl_ready=True, distro='native',
                      tools={'tippecanoe': bool(shutil.which('tippecanoe')), 'depot': True})
        return result
    script = ('import json, shutil\n'
              'try:\n import depot\n has_depot = True\n'
              'except Exception:\n has_depot = False\n'
              'print(json.dumps({"tippecanoe": bool(shutil.which("tippecanoe")), "depot": has_depot}))')
    try:
        checked = subprocess.run(['wsl.exe', '-d', 'Ubuntu', '-e', 'python3', '-c', script],
                                 capture_output=True, text=True, timeout=10)
        if checked.returncode != 0:
            raise RuntimeError('WSL Ubuntu no disponible para la comprobación')
        tools = json.loads(checked.stdout.strip().splitlines()[-1])
        tools['wsl'] = True
        result.update(distro='Ubuntu', tools=tools,
                      wsl_ready=bool(tools.get('tippecanoe') and tools.get('depot')))
    except (OSError, subprocess.SubprocessError, ValueError, IndexError, RuntimeError) as error:
        result['check_error'] = str(error)
    return result


def get_system_health():
    global system_health_running
    def update():
        global system_health_cache, system_health_running
        try:
            result = probe_system_health()
        except Exception as error:
            result = {'status': 'ok', 'platform': sys.platform, 'wsl_ready': False,
                      'distro': '', 'tools': {}, 'checked_at': time.time(), 'check_error': str(error)}
        with system_health_lock:
            system_health_cache = result
            system_health_running = False
    with system_health_lock:
        if system_health_cache and time.time() - system_health_cache['checked_at'] < 300:
            return dict(system_health_cache)
        if not system_health_running:
            system_health_running = True
            threading.Thread(target=update, daemon=True).start()
        return {'status': 'checking', 'platform': sys.platform, 'wsl_ready': None,
                'distro': '', 'tools': {}}


class WizardHTTPServer(ThreadingHTTPServer):
    """One exclusive listener; abandoned persistent clients cannot hold threads forever."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)

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
queue_lock = threading.Lock()
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
    if "cancun.yaml" in rel_or_abs_path:
        candidates.append(os.path.abspath(os.path.join(ROOT_DIR, "cities", "cancun_riviera_maya.yaml")))
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
    macro = data["macroeconomics"]
    if "modal_experiment" not in macro or not isinstance(macro["modal_experiment"], dict):
        macro["modal_experiment"] = {
            "enabled": False,
            "preset": "canonical",
            "traffic_speed_kmh": 40.0,
            "motorization_rate": 1.0
        }
    if not isinstance(data.get("pois"), list):
        data["pois"] = []
    if not isinstance(data.get("places"), list):
        data["places"] = []
    if "data_dir" not in data:
        data["data_dir"] = ""
    if not isinstance(data.get("data_exclusions"), list):
        data["data_exclusions"] = []
    if not isinstance(data.get("isolated_zones"), list):
        if isinstance(data.get("city", {}).get("isolated_zones"), list):
            data["isolated_zones"] = data["city"]["isolated_zones"]
        else:
            data["isolated_zones"] = []
    if not isinstance(data.get("affluence_zones"), list):
        data["affluence_zones"] = []
    if not isinstance(data.get("exclusion_zones"), list):
        data["exclusion_zones"] = []

    from sb_mexico.config_defaults import apply_demand_defaults
    apply_demand_defaults(data)
    # Valores por defecto para ciudad y macroeconomía si faltan
    city = data["city"]
    if "min_residents" not in city:
        city["min_residents"] = 10
    if "min_jobs" not in city:
        city["min_jobs"] = 3
    if "building_filter_size" not in city:
        city["building_filter_size"] = 15.0
    if "building_simplification" not in city:
        city["building_simplification"] = 0.2
    if "restrict_demand_to_urban_core" not in city:
        city["restrict_demand_to_urban_core"] = True
    if "lod_peripheral_roads" not in city:
        city["lod_peripheral_roads"] = "standard"
    if "include_pedestrian_paths" not in city:
        city["include_pedestrian_paths"] = False
    if "lod_peripheral_labels" not in city:
        city["lod_peripheral_labels"] = "none"
    if "lod_peripheral_buildings" not in city:
        city["lod_peripheral_buildings"] = "none"
    if "bbox_locked" not in city:
        city["bbox_locked"] = False
    else:
        city["bbox_locked"] = bool(city["bbox_locked"])


    if "min_pop_size" not in macro:
        macro["min_pop_size"] = 25
    if "target_pop_size" not in macro:
        macro["target_pop_size"] = 150
    if "max_pop_size" not in macro:
        macro["max_pop_size"] = 200
    if "cohort_mode" not in macro:
        macro["cohort_mode"] = "rigid" if macro.get("min_pop_size") == macro.get("max_pop_size") else "adaptive"
    if "cohort_preset" not in macro:
        macro["cohort_preset"] = "canonical_200" if (macro.get("min_pop_size") == 200 and macro.get("max_pop_size") == 200) else "custom"
    if "sample_threshold" not in macro:
        macro["sample_threshold"] = 500
    if "default_growth_factor" not in macro:
        macro["default_growth_factor"] = 1.05
    if "furness_iterations" not in macro:
        macro["furness_iterations"] = 15
    if "furness_tol" not in macro:
        macro["furness_tol"] = 0.02

    return data


def _yaml_quote(val: Any) -> str:
    """Escapa y formatea de forma segura un valor como escalar entrecomillado en YAML."""
    if val is None:
        return '""'
    return json.dumps(str(val), ensure_ascii=False)


def bind_available_engine_sources(data):
    """Resolve pending EIC from this project's or national official files only."""
    from pathlib import Path
    import pandas as pd
    from sb_mexico.demand_sources import select_sources
    from sb_mexico.inegi import _detect_cpv_format
    from sb_mexico.source_downloads import EIC_INDICATOR_NAME, validate_csv
    macro = data.get('macroeconomics', {})
    reference = macro.get('demographic_reference') or {}
    if data.get('demand', {}).get('engine') != 'v2' or not reference.get('pending_download'):
        return
    project = Path(ROOT_DIR) / data.get('data_dir', 'data')
    try:
        cpv = data.get('demand', {}).get('sources', {}).get('cpv')
        if cpv is None:
            cpv = select_sources(project, DATA_DIR, 'cpv', data.get('data_exclusions', []))
        states = set()
        for name in cpv:
            path = Path(ROOT_DIR) / name
            encoding, separator = _detect_cpv_format(str(path))
            frame = pd.read_csv(path, encoding=encoding, sep=separator, dtype=str,
                                usecols=lambda column: column.strip().upper().lstrip('\ufeff') == 'ENTIDAD')
            if frame.empty or len(frame.columns) != 1:
                return
            states.update(str(value).strip().zfill(2) for value in frame.iloc[:, 0].dropna().unique())
        if not states or any(not re.fullmatch(r'\d{2}', state) for state in states):
            return
        indicators = reference.get('indicators')
        if not indicators:
            indicators = next((str(folder / EIC_INDICATOR_NAME) for folder in (project, Path(DATA_DIR))
                               if (folder / EIC_INDICATOR_NAME).is_file()), None)
        if not indicators:
            return
        persons = list(reference.get('persons', []))
        present = {re.fullmatch(r'personas(\d{2})\.csv', Path(name).name).group(1)
                   for name in persons if re.fullmatch(r'personas(\d{2})\.csv', Path(name).name)}
        for state in sorted(states - present):
            path = next((folder / f'personas{state}.csv' for folder in (project, Path(DATA_DIR))
                         if (folder / f'personas{state}.csv').is_file()), None)
            if path is None:
                return
            persons.append(str(path))
        validate_csv(Path(ROOT_DIR) / indicators, 'eic_indicators')
        for name in persons:
            validate_csv(Path(ROOT_DIR) / name, 'eic_persons')
        def display(name):
            return os.path.relpath((Path(ROOT_DIR) / name).resolve(), ROOT_DIR).replace('\\', '/')
        reference.update(indicators=display(indicators), persons=[display(name) for name in persons])
        reference.pop('pending_download', None)
    except (ValueError, OSError, KeyError, TypeError):
        # Keep the pending reference visible; download/build performs full reconciliation.
        return


def save_full_city_data(rel_or_abs_path: str, data: Dict[str, Any]) -> str:
    """Guarda la configuración completa de la ciudad respetando el esquema oficial."""
    from sb_mexico.config_defaults import apply_demand_defaults
    apply_demand_defaults(data)
    bind_available_engine_sources(data)
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
    isolated_zones_cfg = data.get("isolated_zones") or city_cfg.get("isolated_zones") or []
    affluence_zones_cfg = data.get("affluence_zones") or []
    exclusion_zones_cfg = data.get("exclusion_zones") or []
    data_dir_cfg = str(data.get("data_dir", "")).strip()
    data_exclusions_cfg = data.get("data_exclusions", [])

    lines = [
        "# ==============================================================================",
        f"# CONFIGURACIÓN: {city_cfg.get('name', 'CIUDAD')} ({city_cfg.get('code', 'XXX')}) - SUBWAY BUILDER MÉXICO v7.1",
        "# ==============================================================================",
        "",
        "city:",
    ]

    seed_val = city_cfg.get("seed")
    if seed_val is None and macro_cfg.get("seed") is not None:
        seed_val = macro_cfg.get("seed")

    if seed_val is not None and str(seed_val).strip() != "":
        try:
            lines.append(f'  seed: {int(seed_val)}')
        except (ValueError, TypeError):
            pass

    lines.extend([
        f'  code: {_yaml_quote(city_cfg.get("code", "XXX"))}',
        f'  name: {_yaml_quote(city_cfg.get("name", "Nueva Ciudad"))}',
        f'  description: {_yaml_quote(city_cfg.get("description", ""))}',
        f'  bbox: {city_cfg.get("bbox", [-87.0, 21.0, -86.7, 21.3])}',
        f'  creator: {_yaml_quote(city_cfg.get("creator", "Creador"))}',
        f'  grid_size: {float(city_cfg.get("grid_size", 0.0025))}',
        f'  min_residents: {int(city_cfg.get("min_residents", 10))}',
        f'  min_jobs: {int(city_cfg.get("min_jobs", 3))}',
        f'  initial_zoom: {float(city_cfg.get("initial_zoom", 11.5))}',
        f'  building_filter_size: {float(city_cfg.get("building_filter_size", 15.0))}',
        f'  building_simplification: {float(city_cfg.get("building_simplification", 0.2))}',
        f'  include_ocean: {"true" if city_cfg.get("include_ocean") else "false"}',
        f'  urban_parks_only: {"true" if city_cfg.get("urban_parks_only") else "false"}',
        f'  bbox_locked: {"true" if city_cfg.get("bbox_locked") else "false"}',
    ])

    if city_cfg.get("initial_center") and isinstance(city_cfg.get("initial_center"), (list, tuple)) and len(city_cfg["initial_center"]) == 2:
        try:
            ic = [round(float(city_cfg["initial_center"][0]), 5), round(float(city_cfg["initial_center"][1]), 5)]
            lines.append(f'  initial_center: {ic}')
        except (ValueError, TypeError):
            pass
    if city_cfg.get("urban_core_polygon"):
        lines.append(f'  urban_core_polygon: {json.dumps(city_cfg.get("urban_core_polygon"))}')
    if city_cfg.get("restrict_demand_to_urban_core") is not None:
        lines.append(f'  restrict_demand_to_urban_core: {"true" if city_cfg.get("restrict_demand_to_urban_core") else "false"}')
    if "residential_placement" in city_cfg:
        from sb_mexico.residential import validate_placement_mode
        placement_mode = validate_placement_mode(city_cfg["residential_placement"])
        lines.append(f'  residential_placement: {_yaml_quote(placement_mode)}')
    if city_cfg.get("lod_peripheral_roads"):
        lines.append(f'  lod_peripheral_roads: {_yaml_quote(city_cfg.get("lod_peripheral_roads", "standard"))}')
    if city_cfg.get("include_pedestrian_paths") is not None:
        lines.append(f'  include_pedestrian_paths: {"true" if city_cfg.get("include_pedestrian_paths") else "false"}')
    if city_cfg.get("lod_peripheral_labels"):
        lines.append(f'  lod_peripheral_labels: {_yaml_quote(city_cfg.get("lod_peripheral_labels", "none"))}')
    if city_cfg.get("lod_peripheral_buildings"):
        lines.append(f'  lod_peripheral_buildings: {_yaml_quote(city_cfg.get("lod_peripheral_buildings", "none"))}')
    lines.append("")


    if data_dir_cfg:
        lines.append(f'data_dir: {_yaml_quote(data_dir_cfg)}')
        lines.append("")

    if data_exclusions_cfg and isinstance(data_exclusions_cfg, list):
        lines.append("data_exclusions:")
        for ex in data_exclusions_cfg:
            lines.append(f'  - {_yaml_quote(ex)}')
        lines.append("")

    lines.extend([
        "macroeconomics:",
        f'  tasa_pea: {float(macro_cfg.get("tasa_pea", 0.62))}',
        f'  til_1_state: {float(macro_cfg.get("til_1_state", 0.45))}',
        f'  sample_threshold: {int(macro_cfg.get("sample_threshold", 500))}',
        f'  default_growth_factor: {float(macro_cfg.get("default_growth_factor", 1.05))}',
        f'  gravity_beta: {float(macro_cfg.get("gravity_beta", 0.12))}',
        f'  max_distance_km: {float(macro_cfg.get("max_distance_km", 50.0))}',
        f'  min_pop_size: {int(macro_cfg.get("min_pop_size", 25))}',
        f'  target_pop_size: {int(macro_cfg.get("target_pop_size", 150))}',
        f'  max_pop_size: {int(macro_cfg.get("max_pop_size", 200))}',
        f'  furness_iterations: {int(macro_cfg.get("furness_iterations", 15))}',
        f'  furness_tol: {float(macro_cfg.get("furness_tol", 0.02))}',
    ])

    if 'demand' in data:
        from sb_mexico.demand_v2.request import validate_config
        validate_config(data)
        # The candidate contract is preserved in full, including explicit source roles.
        lines.insert(lines.index('macroeconomics:'), 'demand: ' + json.dumps(data['demand'], ensure_ascii=False))

    if 'routing' in data:
        if not isinstance(data['routing'], dict):
            raise ValueError('routing must be a mapping')
        lines.insert(lines.index('macroeconomics:'), 'routing: ' + json.dumps(data['routing'], ensure_ascii=False, allow_nan=False))

    if 'residential_employment' in macro_cfg:
        from sb_mexico.residential_employment import validate_employment_mode
        employment_mode = validate_employment_mode(macro_cfg['residential_employment'])
        lines.append(f'  residential_employment: {_yaml_quote(employment_mode)}')
    if macro_cfg.get('demographic_reference') is not None:
        from sb_mexico.demographic_reference import validate_reference
        validate_reference(macro_cfg, allow_pending=True)
        lines.append('  demographic_reference: ' + json.dumps(macro_cfg['demographic_reference'], ensure_ascii=False))
    if 'workplace_employment' in macro_cfg:
        from sb_mexico.workplace_employment import validate_workplace_mode
        lines.append(f'  workplace_employment: {_yaml_quote(validate_workplace_mode(macro_cfg["workplace_employment"]))}')
    if 'od_allocation' in macro_cfg:
        from sb_mexico.config_defaults import validate_od_allocation_mode
        lines.append(f'  od_allocation: {_yaml_quote(validate_od_allocation_mode(macro_cfg["od_allocation"]))}')
    if 'workplace_control_contract' in macro_cfg:
        from sb_mexico.historical_benchmark import validate_historical_contract
        validate_historical_contract(macro_cfg['workplace_control_contract'])
        # JSON is valid inline YAML and preserves source hashes/group identities.
        lines.append('  workplace_control_contract: ' + json.dumps(macro_cfg['workplace_control_contract'], ensure_ascii=False))

    if 'historical_workplace_benchmark' in macro_cfg:
        from sb_mexico.historical_benchmark import validate_historical_contract
        if validate_historical_contract(macro_cfg['historical_workplace_benchmark']) is None:
            raise ValueError('Expected historical_benchmark role')
        lines.append('  historical_workplace_benchmark: ' + json.dumps(macro_cfg['historical_workplace_benchmark'], ensure_ascii=False))

    if 'historical_workplace_transfer' in macro_cfg or macro_cfg.get('workplace_employment') == 'historical_transfer':
        from sb_mexico.historical_transfer import validate_transfer_contract
        validate_transfer_contract(macro_cfg.get('historical_workplace_transfer'))
        lines.append('  historical_workplace_transfer: ' + json.dumps(macro_cfg['historical_workplace_transfer'], ensure_ascii=False))

    proj_yr = macro_cfg.get("projection_year") or macro_cfg.get("target_year")
    if proj_yr is not None:
        try:
            lines.append(f'  projection_year: {int(proj_yr)}')
        except (ValueError, TypeError):
            pass
    if macro_cfg.get('conapo_source_year') is not None:
        from sb_mexico.inegi import resolve_projection_year
        confirmed_year = resolve_projection_year({'projection_year':macro_cfg['conapo_source_year']})
        lines.append(f'  conapo_source_year: {confirmed_year}')

    if "cohort_mode" in macro_cfg:
        lines.append(f'  cohort_mode: {_yaml_quote(macro_cfg.get("cohort_mode"))}')
    if "cohort_preset" in macro_cfg:
        lines.append(f'  cohort_preset: {_yaml_quote(macro_cfg.get("cohort_preset"))}')
    lines.append("")

    modal_exp = macro_cfg.get("modal_experiment")
    if isinstance(modal_exp, dict):
        is_en = "true" if modal_exp.get("enabled") else "false"
        lines.append("  # Laboratorio Experimental: Competitividad Modal (Auto vs. Metro)")
        lines.append("  modal_experiment:")
        lines.append(f'    enabled: {is_en}')
        lines.append(f'    preset: {_yaml_quote(modal_exp.get("preset", "canonical"))}')
        lines.append(f'    traffic_speed_kmh: {float(modal_exp.get("traffic_speed_kmh", 22.0))}')
        lines.append(f'    motorization_rate: {float(modal_exp.get("motorization_rate", 0.40))}')
        lines.append("")

    growth_factors = macro_cfg.get("growth_factors", {})
    if isinstance(growth_factors, dict) and growth_factors:
        lines.append("  growth_factors:")
        for k, v in growth_factors.items():
            lines.append(f'    {_yaml_quote(k)}: {float(v)}')
        lines.append("")

    factor_sources = macro_cfg.get('growth_factor_sources', {})
    if isinstance(factor_sources, dict) and factor_sources:
        lines.append('  growth_factor_sources: ' + json.dumps(factor_sources, ensure_ascii=False, allow_nan=False))
        lines.append('')

    # Bloque de Zonas Aisladas
    if isolated_zones_cfg and isinstance(isolated_zones_cfg, list):
        lines.append("isolated_zones:")
        for z in isolated_zones_cfg:
            z_id = str(z.get("id", "zona")).strip()
            z_name = str(z.get("name", z_id)).strip()
            raw_b = z.get("bbox", [])
            if isinstance(raw_b, (list, tuple)) and len(raw_b) == 4:
                try:
                    norm_b = [
                        round(min(float(raw_b[0]), float(raw_b[2])), 4),
                        round(min(float(raw_b[1]), float(raw_b[3])), 4),
                        round(max(float(raw_b[0]), float(raw_b[2])), 4),
                        round(max(float(raw_b[1]), float(raw_b[3])), 4)
                    ]
                except (ValueError, TypeError):
                    norm_b = raw_b
            else:
                norm_b = raw_b
            lines.append(f'  - id: {_yaml_quote(z_id)}')
            lines.append(f'    name: {_yaml_quote(z_name)}')
            lines.append(f'    bbox: {norm_b}')
        lines.append("")

    # Bloque de Zonas de Alta Afluencia (High Attraction Zones)
    if affluence_zones_cfg and isinstance(affluence_zones_cfg, list):
        lines.append("# Zonas de Alta Afluencia (High Attraction Zones)")
        lines.append("affluence_zones:")
        for az in affluence_zones_cfg:
            z_id = str(az.get("id", "zone_1")).strip()
            z_name = str(az.get("name", z_id)).strip()
            z_type = str(az.get("type", "polygon")).strip()
            z_archetype = str(az.get("archetype", "custom")).strip()
            z_mult = float(az.get("multiplier", 2.0))
            z_reach = float(az.get("reach_bonus", 0.3))
            z_enabled = bool(az.get("enabled", True))
            z_color = str(az.get("color", "#F59E0B")).strip()
            z_target_mode = str(az.get("target_mode", "MULTIPLIER")).strip()

            lines.append(f'  - id: {_yaml_quote(z_id)}')
            lines.append(f'    name: {_yaml_quote(z_name)}')
            lines.append(f'    type: {_yaml_quote(z_type)}')
            lines.append(f'    archetype: {_yaml_quote(z_archetype)}')
            lines.append(f'    multiplier: {z_mult:.2f}')
            lines.append(f'    reach_bonus: {z_reach:.2f}')
            lines.append(f'    target_mode: {_yaml_quote(z_target_mode)}')
            if az.get("target_jobs") is not None and int(az.get("target_jobs", 0)) > 0:
                lines.append(f'    target_jobs: {int(az["target_jobs"])}')
            lines.append(f'    color: {_yaml_quote(z_color)}')
            lines.append(f'    enabled: {"true" if z_enabled else "false"}')

            coords = az.get("coordinates")
            if isinstance(coords, list) and len(coords) >= 3:
                lines.append("    coordinates:")
                for c in coords:
                    if isinstance(c, (list, tuple)) and len(c) >= 2:
                        lines.append(f'      - [{float(c[0]):.5f}, {float(c[1]):.5f}]')

            raw_b = az.get("bbox")
            if isinstance(raw_b, (list, tuple)) and len(raw_b) == 4:
                try:
                    norm_b = [
                        round(min(float(raw_b[0]), float(raw_b[2])), 4),
                        round(min(float(raw_b[1]), float(raw_b[3])), 4),
                        round(max(float(raw_b[0]), float(raw_b[2])), 4),
                        round(max(float(raw_b[1]), float(raw_b[3])), 4)
                    ]
                    lines.append(f'    bbox: {norm_b}')
                except Exception:
                    pass
            lines.append("")

    # Bloque de Zonas de Exclusión (Exclusion Zones - Sin Demanda ni Simulación)
    if exclusion_zones_cfg and isinstance(exclusion_zones_cfg, list):
        lines.append("# Zonas de Exclusión (Exclusion Zones - Sin Demanda ni Simulación)")
        lines.append("exclusion_zones:")
        for ez in exclusion_zones_cfg:
            z_id = str(ez.get("id", "excl_1")).strip()
            z_name = str(ez.get("name", z_id)).strip()
            z_type = str(ez.get("type", "polygon")).strip()
            z_reason = str(ez.get("reason", "inhabitable")).strip()
            z_enabled = bool(ez.get("enabled", True))
            z_color = str(ez.get("color", "#EF4444")).strip()

            lines.append(f'  - id: {_yaml_quote(z_id)}')
            lines.append(f'    name: {_yaml_quote(z_name)}')
            lines.append(f'    type: {_yaml_quote(z_type)}')
            lines.append(f'    reason: {_yaml_quote(z_reason)}')
            lines.append(f'    color: {_yaml_quote(z_color)}')
            lines.append(f'    enabled: {"true" if z_enabled else "false"}')

            coords = ez.get("coordinates")
            if isinstance(coords, list) and len(coords) >= 3:
                lines.append("    coordinates:")
                for c in coords:
                    if isinstance(c, (list, tuple)) and len(c) >= 2:
                        lines.append(f'      - [{float(c[0]):.5f}, {float(c[1]):.5f}]')

            raw_b = ez.get("bbox")
            if isinstance(raw_b, (list, tuple)) and len(raw_b) == 4:
                try:
                    norm_b = [
                        round(min(float(raw_b[0]), float(raw_b[2])), 4),
                        round(min(float(raw_b[1]), float(raw_b[3])), 4),
                        round(max(float(raw_b[0]), float(raw_b[2])), 4),
                        round(max(float(raw_b[1]), float(raw_b[3])), 4)
                    ]
                    lines.append(f'    bbox: {norm_b}')
                except Exception:
                    pass
            lines.append("")

    # Bloque de POIs
    lines.append("pois:")
    for poi in pois_cfg:
        p_id = poi.get("id", "POI_Nuevo")
        loc = poi.get("loc", [0.0, 0.0])
        jobs = int(poi["jobs"]) if poi.get("jobs") is not None else 0
        rad = int(poi["radius_m"]) if poi.get("radius_m") is not None else 100
        mode = poi.get("mode", "MAX").upper()

        lines.append(f'  - id: {_yaml_quote(p_id)}')
        name_val = poi.get("name")
        if isinstance(name_val, dict):
            lines.append("    name:")
            if "es" in name_val:
                lines.append(f'      es: {_yaml_quote(name_val["es"])}')
            if "en" in name_val:
                lines.append(f'      en: {_yaml_quote(name_val["en"])}')
        elif isinstance(name_val, str) and name_val:
            lines.append(f'    name: {_yaml_quote(name_val)}')

        if poi.get("type"):
            lines.append(f'    type: {_yaml_quote(poi["type"])}')
        if poi.get("sub_type"):
            lines.append(f'    sub_type: {_yaml_quote(poi["sub_type"])}')

        lines.append(f'    loc: [{loc[0]:.5f}, {loc[1]:.5f}]')
        lines.append(f'    jobs: {jobs}')
        lines.append(f'    radius_m: {rad}')
        lines.append(f'    mode: {_yaml_quote(mode)}')

        if isinstance(poi.get("metadata"), dict) and poi["metadata"]:
            lines.append("    metadata:")
            for mk, mv in poi["metadata"].items():
                lines.append(f'      {mk}: {_yaml_quote(mv)}')
        lines.append("")

    # Bloque de Places / Toponimia
    if places_cfg:
        lines.append("# Toponimia y Colonias Curadas")
        lines.append("places:")
        from sb_mexico.place_identity import serialize_places
        for place in serialize_places(places_cfg):
            lines.extend('  ' + line for line in yaml.safe_dump(
                [place], allow_unicode=True, sort_keys=False).rstrip().splitlines())
        lines.append("")

    if data.get("toponymy_mode") in ("merge", "replace"):
        lines.append("toponymy_mode: " + data["toponymy_mode"])

    if data.get("deleted_places"):
        lines.append(yaml.safe_dump({"deleted_places": data["deleted_places"]},
                                    allow_unicode=True, sort_keys=False).rstrip())

    content = "\n".join(lines).rstrip() + "\n"
    # Publish a complete configuration, including projection year and provenance,
    # in one operation. A failed write must preserve the previous configuration.
    import tempfile
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=os.path.dirname(fpath), prefix='.wizard-',
                                         suffix='.tmp', delete=False) as stream:
            temporary_path = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, fpath)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)

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
            "seed": 42,
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
            "include_ocean": False,
            "urban_parks_only": False,
            "restrict_demand_to_urban_core": True,
            "lod_peripheral_roads": "standard",
            "include_pedestrian_paths": False,
            "lod_peripheral_labels": "none",
            "lod_peripheral_buildings": "none",
            "bbox_locked": False
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
            "min_pop_size": 25,
            "target_pop_size": 150,
            "max_pop_size": 200,
            "furness_iterations": 15,
            "furness_tol": 0.02,
            "growth_factors": {}
        },
        "isolated_zones": [],
        "affluence_zones": [],
        "exclusion_zones": [],
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
    """Actualiza la carpeta de datos personalizada del proyecto con verificación estricta de seguridad."""
    if not city_file:
        raise ValueError("Parámetro 'file' requerido")

    if not new_dir or not new_dir.strip():
        raise ValueError("Parámetro 'data_dir' requerido")

    cleaned = new_dir.strip()
    target_abs = os.path.abspath(cleaned if os.path.isabs(cleaned) else os.path.join(ROOT_DIR, cleaned))
    norm_data = os.path.normcase(os.path.realpath(DATA_DIR))
    norm_target = os.path.normcase(os.path.realpath(target_abs))
    if not (norm_target == norm_data or norm_target.startswith(norm_data + os.sep)):
        raise PermissionError(f"Acceso denegado: carpeta de datos fuera de data/ ({new_dir})")

    cdata = load_city_data(city_file)
    cdata["data_dir"] = cleaned
    save_full_city_data(city_file, cdata)
    return {"status": "ok", "data_dir": cdata["data_dir"]}


def inspect_data_files(city_name: str = "", city_code: str = "", city_file: str = "", data_dir_override: str = "") -> Dict[str, Any]:
    """
    Describe the same source candidates selected by compilation and previews.
    Includes shared national sources, never another city's subdirectory.
    Presence is not content validation; ZIP archives are not usable sources.
    """
    target_dir = None
    exclusions = set()
    cdata = {}

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

    from pathlib import Path
    from sb_mexico.demand_sources import select_sources

    def describe(paths, first_only=False):
        records = []
        for index, source in enumerate(paths):
            path = Path(source).resolve()
            try:
                display_path = os.path.relpath(path, ROOT_DIR)
            except ValueError:
                display_path = str(path)
            records.append(dict(path=display_path.replace('\\', '/'),
                                abs_path=str(path), filename=path.name,
                                size_mb=round(path.stat().st_size / (1024 * 1024), 2),
                                selected=(index == 0 if first_only else True),
                                shared=path.parent == Path(DATA_DIR).resolve()))
        return records

    source_paths = {kind: select_sources(target_dir, DATA_DIR, kind, exclusions)
                    for kind in ('denue', 'cpv', 'ce', 'conapo', 'enoe')}
    explicit = cdata.get('demand', {}).get('sources', {}) if cdata.get('demand', {}).get('engine') == 'v2' else {}
    for kind in ('denue', 'cpv', 'ce'):
        if kind in explicit:
            source_paths[kind] = [str(Path(ROOT_DIR) / name) for name in explicit[kind]]
    sources = {}
    archive_patterns = {
        'denue': ['*denue*.zip', '*DENUE*.zip'],
        'cpv': ['*RESAGEBURB*.zip', '*resageburb*.zip', '*cpv*.zip', '*censo*.zip'],
        'ce': ['*SAIC*.zip', '*saic*.zip', '*tr_ce*.zip'],
        'conapo': ['*pobproy*.zip', '*conapo*.zip', '*proyeccion*.zip'],
        'enoe': ['*enoe*.zip', '*ENOE*.zip', '*trim*.zip'],
        'marco': ['*marco*.zip', '*Marco*.zip', '*mg*.zip', '*MG*.zip', '[0-9][0-9]_*.zip'],
    }
    for kind, paths in source_paths.items():
        archives = find_files(archive_patterns[kind])
        missing_paths = [str(path) for path in paths if not Path(path).is_file()]
        files = describe([path for path in paths if Path(path).is_file()], first_only=kind in ('conapo', 'enoe'))
        sources['ce2024' if kind == 'ce' else kind] = dict(
            status='missing' if missing_paths else 'ok' if files else ('archive' if archives else 'missing'),
            files=files, archives=archives, missing_paths=missing_paths, selection='first' if kind in ('conapo', 'enoe') else 'all')

    macro = cdata.get('macroeconomics', {})
    reference = macro.get('demographic_reference')
    eic_active = isinstance(reference, dict) and reference.get('mode') == 'eic2025'
    eic = dict(status='inactive', files=[], archives=find_files(['*eic2025*.zip']),
               selection='explicit', required=eic_active, active=eic_active, missing_paths=[])
    if reference is not None:
        try:
            from sb_mexico.demographic_reference import validate_reference
            from sb_mexico.source_downloads import validate_csv
            validate_reference(macro)
            for name, kind in [(reference['indicators'], 'eic_indicators')] + [(p, 'eic_persons') for p in reference['persons']]:
                path = (Path(ROOT_DIR) / name).resolve()
                if not path.is_file():
                    eic['missing_paths'].append(name)
                    continue
                files = describe([path])
                files[0]['shared'] = not path.is_relative_to(Path(target_dir).resolve())
                eic['files'].extend(files)
                validate_csv(path, kind)
            eic['status'] = 'missing' if eic['missing_paths'] else 'ok'
            eic['validation'] = 'column_structure; municipal reconciliation checked during download/build'
        except (ValueError, OSError, KeyError, TypeError) as error:
            pending = isinstance(reference, dict) and reference.get('pending_download')
            eic.update(status='missing' if pending else 'invalid',
                       message='EIC pendiente: prepara las descargas y guarda las rutas antes de compilar.' if pending else str(error))
    sources['eic'] = eic
    sources['conapo']['active'] = not eic_active
    if eic_active:
        for record in sources['conapo']['files']:
            record['selected'] = False

    placement = cdata.get('city', {}).get('residential_placement', 'official_blocks')
    if 'marco' in explicit:
        marco_paths = [str(Path(ROOT_DIR) / name) for name in explicit['marco']]
    elif placement == 'official_blocks':
        from sb_mexico.residential import discover_marco_layers
        marco_paths = discover_marco_layers(target_dir)
    else:
        marco_paths = select_sources(target_dir, DATA_DIR, 'marco', exclusions)
    marco_archives = find_files(archive_patterns['marco'])
    missing_marco = [str(path) for path in marco_paths if not Path(path).is_file()]
    sources['marco'] = dict(status='missing' if missing_marco else 'ok' if marco_paths else ('archive' if marco_archives else 'missing'),
                            files=describe([path for path in marco_paths if Path(path).is_file()]), missing_paths=missing_marco, archives=marco_archives, selection='all',
                            placement=placement)

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
        **sources,
        "osm": {"status": "ok" if osm else "missing", "files": osm},
        "all_ready": bool(sources['denue']['status'] == 'ok' and sources['cpv']['status'] == 'ok' and
                          (reference is None or eic['status'] == 'ok')),
        "presence_only": True
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


def automatic_workplace_status(city_file):
    from pathlib import Path
    from sb_mexico.demand_sources import select_sources
    from sb_mexico.automatic_workplace import resolve_automatic_workplace
    config = load_city_data(city_file)
    if (config.get('demand', {}).get('engine') == 'v2'
            and config['macroeconomics']['workplace_employment'] == 'auto'):
        from sb_mexico.demand_v2.request import prepare_request
        from sb_mexico.fine_workplace import load_fine_workplaces
        request = prepare_request(config, ROOT_DIR)
        bbox = dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), config['city']['bbox']))
        _, _, report = load_fine_workplaces(request.sources['denue'], bbox,
            config['macroeconomics'], request.sources['ce'], ROOT_DIR)
        return dict(report, requested_mode='auto', engine='v2')
    project = os.path.join(ROOT_DIR, config.get('data_dir') or 'data/' + Path(city_file).stem)
    exclusions = config.get('data_exclusions', [])
    macro, notice = resolve_automatic_workplace(
        select_sources(project, DATA_DIR, 'denue', exclusions),
        select_sources(project, DATA_DIR, 'ce', exclusions), config['macroeconomics'], ROOT_DIR)
    if macro['workplace_employment'] == 'historical_transfer':
        from sb_mexico.historical_transfer import checked_sources
        checked_sources(select_sources(project, DATA_DIR, 'denue', exclusions), macro, ROOT_DIR)
    return dict(mode=macro['workplace_employment'], requested_mode=config['macroeconomics']['workplace_employment'], automatic_selection=notice,
                reference_year=(macro.get('historical_workplace_transfer') or {}).get('reference_year'))


def inspect_workplace_benchmark(city_file, contract):
    """Read explicitly selected CE files; never register sources or save a city."""
    from pathlib import Path
    from sb_mexico.demand_sources import select_sources
    from sb_mexico.historical_benchmark import inspect_historical_benchmark
    root = Path(ROOT_DIR).resolve()
    cdata = load_city_data(city_file)
    project = root / cdata.get('data_dir', 'data/' + Path(city_file).stem)
    denue = select_sources(project, root / 'data', 'denue', cdata.get('data_exclusions', []))
    sources = []
    for value in contract.get('ce_sources', []):
        path = (root / value).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != '.csv' or not path.is_file():
            raise ValueError('La fuente CE debe ser un CSV dentro del proyecto')
        sources.append(str(path))
    report = inspect_historical_benchmark(denue, sources, contract)
    bound = dict(contract)
    # First inspection may bind a draft. A stale binding is never refreshed silently.
    if bound.get('source_sha256') is None:
        bound['source_sha256'] = report['source_sha256']
    if report['source_binding'] != 'mismatch':
        from sb_mexico.historical_transfer import SUPPORTED, FORMULA_VERSION, load_historical_workplaces
        groups = [dict(municipality=g['municipality'], scian_prefixes=g['scian_prefixes'],
                       reporting_unit='establishment', reporting_unit_evidence='Conditional preflight only; analyst must supply reporting-unit evidence before activation')
                  for g in report['groups'] if g['activity_code'] in SUPPORTED]
        if groups:
            draft = {**bound, 'role': 'historical_transfer', 'enabled': True,
                     'formula_version': FORMULA_VERSION, 'strength': 1, 'groups': groups}
            bbox = dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), cdata['city']['bbox']))
            _, _, preflight = load_historical_workplaces(denue, bbox,
                {**cdata.get('macroeconomics', {}), 'historical_workplace_transfer': draft}, root)
            report['transfer_preflight'] = preflight
    return dict(status='ok', report=report, contract=bound)


def inspect_conapo_years(city_file: str, source_year: Optional[str] = None) -> Dict[str, Any]:
    """Inspect the selected source without loading census or business records."""
    try:
        import pandas as pd
        from sb_mexico.demand_sources import select_sources
        from sb_mexico.inegi import resolve_projection_year
        cdata = load_city_data(city_file)
        project_dir = cdata.get('data_dir') or os.path.join(DATA_DIR, os.path.splitext(os.path.basename(_resolve_city_path(city_file)))[0].lower())
        if not os.path.isabs(project_dir):
            project_dir = os.path.join(ROOT_DIR, project_dir)
        files = select_sources(project_dir, DATA_DIR, 'conapo', cdata.get('data_exclusions', []))
        if not files:
            return dict(status='missing_conapo', message='No se detectó archivo CONAPO.', available_years=[])
        source = files[0]
        for encoding in ('utf-8-sig', 'latin1'):
            try:
                header = pd.read_csv(source, encoding=encoding, nrows=0)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('No se pudo leer el archivo CONAPO.')
        normalized = {str(c).strip().upper(): c for c in header.columns}
        if 'CLAVE' not in normalized or not any(c.startswith('POB') for c in normalized):
            raise ValueError('El archivo CONAPO no contiene CLAVE y población municipal.')
        year_column = next((original for name, original in normalized.items()
                            if name.replace('Ñ', 'N').replace('Á', 'A').replace('Ó', 'O') in ('ANO', 'ANIO', 'YEAR', 'AO')), None)
        macro = cdata.get('macroeconomics', {})
        result = dict(conapo_file=os.path.basename(source), requested_year=resolve_projection_year(macro))
        if year_column is None:
            confirmed = macro.get('conapo_source_year') if source_year is None else (source_year or None)
            if confirmed is None:
                return dict(result, status='needs_source_year', available_years=[],
                            message='Completa el campo Año confirmado del archivo CONAPO; la fuente no incluye columna de año.')
            year = resolve_projection_year({'projection_year': confirmed})
            return dict(result, status='ok', available_years=[year], year_basis='confirmed_source_year')
        try:
            frame = pd.read_csv(source, encoding=encoding, usecols=[year_column])
        except UnicodeDecodeError:
            frame = pd.read_csv(source, encoding='latin1', usecols=[year_column])
        values = pd.to_numeric(frame[year_column], errors='coerce')
        if values.isna().any() or (values % 1 != 0).any() or (~values.between(1900, 2100)).any() or frame.empty:
            raise ValueError('La columna de año CONAPO contiene valores vacíos o inválidos.')
        return dict(result, status='ok', available_years=sorted(int(y) for y in values.unique()), year_basis='source_column')
    except Exception as error:
        return dict(status='error', message=str(error), available_years=[])


def calculate_conapo_factors(city_file: str, target_year: Optional[int] = None, automatic: bool = False) -> Dict[str, Any]:
    """Return the same population factors and denominator policy used by builds."""
    try:
        from sb_mexico.population_projection import resolve_population_factors
        from sb_mexico.inegi import load_denue, resolve_projection_year
        cdata = load_city_data(city_file)
        city = cdata.get('city', {})
        macro = dict(cdata.get('macroeconomics', {}))
        if automatic:
            macro['growth_factors'] = {}
        if target_year is not None:
            macro['projection_year'] = resolve_projection_year({'projection_year': target_year})
        from sb_mexico.demand_sources import select_sources
        project_dir = cdata.get('data_dir') or os.path.join(DATA_DIR, os.path.splitext(os.path.basename(_resolve_city_path(city_file)))[0].lower())
        if not os.path.isabs(project_dir):
            project_dir = os.path.join(ROOT_DIR, project_dir)
        files = [{'path': source} for source in select_sources(project_dir, DATA_DIR, 'conapo', cdata.get('data_exclusions', []))]
        if not files:
            return dict(status='missing_conapo', message='No se detectó archivo CONAPO.', factors=[])
        cpv = select_sources(project_dir, DATA_DIR, 'cpv', cdata.get('data_exclusions', []))
        inspection = inspect_conapo_years(city_file)
        if inspection['status'] != 'ok':
            return dict(inspection, factors=[])
        report = {}
        factors = resolve_population_factors(os.path.join(ROOT_DIR, files[0]['path']), cpv, macro, report)
        if report.get('year_basis') == 'unverified':
            return dict(status='needs_source_year', message='Confirma el año del archivo CONAPO sin columna de año.',
                        factors=[], diagnostics={'year_basis': 'unverified'})
        if not factors:
            return dict(status='error', message='No se pudieron leer proyecciones CONAPO.', factors=[])
        municipalities = set(factors)
        denue = select_sources(project_dir, DATA_DIR, 'denue', cdata.get('data_exclusions', []))
        bbox = city.get('bbox')
        if denue and bbox:
            frame = load_denue(denue, dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), bbox)))
            municipalities = set(frame['cve_mun_clean']) & set(factors)
        rows = []
        for key in sorted(municipalities):
            detail = report['municipalities'][key]
            rows.append(dict(cve_mun=key, name=report.get('municipality_names', {}).get(key, f'Municipio {key}'), ano=report.get('effective_year'),
                             pob_2020=detail['census_population_2020'],
                             pob_conapo=detail['projected_population'], factor=factors[key],
                             denominator=detail['denominator'], in_bbox=True))
        compact_report = {key: report[key] for key in ('requested_year', 'effective_year', 'year_basis', 'factor_policy') if key in report}
        return dict(status='ok', conapo_file=os.path.basename(files[0]['path']),
                    requested_year=report['requested_year'], projection_year=report.get('effective_year'),
                    available_years=report['available_years'] or inspection['available_years'], factors=rows, diagnostics=compact_report)
    except Exception as error:
        return dict(status='error', message=str(error), factors=[])


def detect_macro_parameters(city_file: str) -> Dict[str, Any]:
    """
    Detecta o restablece los parámetros macroeconómicos oficiales (ENOE / Modelo Gravitatorio):
    1. Nivel 1: Si existe archivo ENOE (CSV o XLS/XLSX) en la carpeta del proyecto, extrae Tasa PEA y TIL1 reales.
    2. Nivel 2: Si no hay ENOE pero existe Censo CPV (RESAGEBURB), calcula la Tasa PEA real de los municipios
       del BBOX y consulta la TIL1 en el catálogo oficial estatal de INEGI (2024).
    3. Nivel 3: Si se identifica la entidad, usa las referencias oficiales estatales de la ENOE.
    4. Nivel 4: Valores estándar de respaldo (0.62 PEA, 0.45 TIL1).
    """
    target_dir = None
    cdata = {}
    if city_file:
        try:
            cdata = load_city_data(city_file)
            cfg_dir = cdata.get("data_dir")
            if cfg_dir:
                target_dir = cfg_dir if os.path.isabs(cfg_dir) else os.path.join(ROOT_DIR, cfg_dir)
            else:
                city_base = os.path.splitext(os.path.basename(city_file))[0].lower()
                cand = os.path.join(ROOT_DIR, "data", city_base)
                if os.path.exists(cand):
                    target_dir = cand
        except Exception:
            pass

    enoe_files = []
    cpv_files = []
    if target_dir and os.path.exists(target_dir):
        from sb_mexico.demand_sources import select_sources
        enoe_files = select_sources(target_dir, DATA_DIR, 'enoe', cdata.get('data_exclusions', []))

        cpv_files = select_sources(target_dir, DATA_DIR, 'cpv', cdata.get('data_exclusions', []))

    # Identificar claves municipales del proyecto si existen
    target_muns = []
    cve_ent = None
    growth_factors = cdata.get("macroeconomics", {}).get("growth_factors", {})
    if not growth_factors:
        growth_factors = cdata.get("growth_factors", {})
    if growth_factors:
        target_muns = [str(k).strip() for k in growth_factors.keys()]
        for m in target_muns:
            if len(m) == 5 and m[:2].isdigit():
                cve_ent = m[:2]
                break

    # Si cve_ent aún no se detecta, inferir por nombres de archivo en target_dir
    if not cve_ent and target_dir and os.path.exists(target_dir):
        for f in os.listdir(target_dir):
            m = re.search(r'(?:RESAGEBURB_|denue_inegi_)(\d{2})', f)
            if m:
                cve_ent = m.group(1)
                break

    from sb_mexico.inegi import parse_enoe_indicators, calculate_cpv_pea_rate, STATE_MACRO_BENCHMARKS

    tasa_pea = None
    til_1 = None
    source_msg = ""
    method = "default"

    # Nivel 1: Archivo ENOE en la carpeta
    if enoe_files:
        try:
            enoe_res = parse_enoe_indicators(enoe_files[0])
            tasa_pea = enoe_res.get("tasa_pea")
            til_1 = enoe_res.get("til_1")
            fn = os.path.basename(enoe_files[0])
            source_msg = f"Detectado automáticamente desde archivo ENOE ({fn})"
            method = "enoe_file"
        except Exception as e:
            source_msg = f"Error al parsear ENOE ({e})"

    # Nivel 2: Inferencia híbrida Censo CPV (PEA) + Catálogo Estatal (TIL1)
    if tasa_pea is None and cpv_files:
        try:
            pea_cpv = calculate_cpv_pea_rate(cpv_files[0], target_cve_muns=target_muns)
            if pea_cpv is not None:
                tasa_pea = pea_cpv
                state_info = STATE_MACRO_BENCHMARKS.get(cve_ent or "")
                if state_info and til_1 is None:
                    til_1 = state_info["til_1"]
                    source_msg = f"Calculado desde Censo CPV 2020 (PEA: {tasa_pea:.2%}) y catálogo oficial de {state_info['nombre']} (TIL1: {til_1:.2%})"
                    method = "census_hybrid"
                else:
                    source_msg = f"Calculado desde Censo CPV 2020 (PEA: {tasa_pea:.2%})"
                    method = "census_pea"
        except Exception:
            pass

    # Nivel 3: Catálogo Estatal de referencia si falta TIL1 o PEA
    state_info = STATE_MACRO_BENCHMARKS.get(cve_ent or "")
    if state_info:
        if tasa_pea is None:
            tasa_pea = state_info["tasa_pea"]
        if til_1 is None:
            til_1 = state_info["til_1"]
        if not source_msg:
            source_msg = f"Referencia oficial ENOE estatal para {state_info['nombre']} (cve {cve_ent})"
            method = "state_benchmark"
    elif til_1 is None:
        til_1 = 0.45

    if tasa_pea is None:
        tasa_pea = 0.62
    if til_1 is None:
        til_1 = 0.45
    if not source_msg:
        source_msg = "Valores estándar de referencia base (sin archivos locales)"
        method = "default"

    from sb_mexico.gravity import recommend_gravity_beta
    bbox_cand = cdata.get("city", {}).get("bbox")
    beta_rec = recommend_gravity_beta(bbox=bbox_cand)
    rec_beta = beta_rec.get("recommended_beta", 0.120)

    return {
        "status": "ok",
        "source": source_msg,
        "method": method,
        "cve_ent": cve_ent,
        "has_enoe_file": bool(enoe_files),
        "beta_recommendation": beta_rec,
        "parameters": {
            "tasa_pea": round(float(tasa_pea), 4),
            "til_1_state": round(float(til_1), 4),
            "gravity_beta": 0.120,
            "max_distance_km": 50.0,
            "min_pop_size": 25,
            "target_pop_size": 150,
            "max_pop_size": 200,
            "seed": 42
        }
    }


def validate_city_configuration(city_file: str) -> Dict[str, Any]:
    """
    Audita exhaustivamente la configuración de una ciudad contra los Estándares
    de Modelación de Subway Builder México y las restricciones del motor geoespacial.
    Retorna un informe con estado ('ok', 'warning', 'error'), lista de errores y advertencias.
    """
    errors = []
    warnings = []
    summary = {}

    try:
        fpath = _resolve_city_path(city_file)
    except Exception as e:
        return {
            "valid": False,
            "status": "error",
            "errors": [f"Ruta de archivo inválida o fuera del proyecto: {e}"],
            "warnings": [],
            "summary": {}
        }

    if not os.path.exists(fpath):
        return {
            "valid": False,
            "status": "error",
            "errors": [f"El archivo no existe: {city_file}"],
            "warnings": [],
            "summary": {}
        }

    try:
        with open(fpath, "r", encoding="utf-8") as f:
            cdata = yaml.safe_load(f) or {}
    except Exception as e:
        return {
            "valid": False,
            "status": "error",
            "errors": [f"Error de sintaxis YAML: {e}"],
            "warnings": [],
            "summary": {}
        }

    # 1. Validación de Bloque City
    city_cfg = cdata.get("city", {})
    code = str(city_cfg.get("code", "")).strip()
    name = str(city_cfg.get("name", "")).strip()
    bbox = city_cfg.get("bbox", [])

    if not code:
        errors.append("El código de ciudad ('city.code') es obligatorio.")
    elif len(code) > 6 or not code.isalnum():
        warnings.append(f"El código de ciudad '{code}' debería ser un identificador alfanumérico corto (ej. CUN, GDL, MTY).")

    if not name:
        errors.append("El nombre de la ciudad ('city.name') es obligatorio.")

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        errors.append("El BBOX debe ser una lista de 4 coordenadas [min_lon, min_lat, max_lon, max_lat].")
    else:
        try:
            min_lon, min_lat, max_lon, max_lat = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            if min_lon >= max_lon:
                errors.append(f"BBOX inválido: min_lon ({min_lon}) debe ser menor que max_lon ({max_lon}).")
            if min_lat >= max_lat:
                errors.append(f"BBOX inválido: min_lat ({min_lat}) debe ser menor que max_lat ({max_lat}).")
            summary["bbox_dims"] = {
                "lon_span": round(max_lon - min_lon, 4),
                "lat_span": round(max_lat - min_lat, 4)
            }
        except (ValueError, TypeError):
            errors.append("Las 4 coordenadas del BBOX deben ser números flotantes válidos.")

    # 2. Validación de Macroeconomía
    macro_cfg = cdata.get("macroeconomics", {})
    tasa_pea = macro_cfg.get("tasa_pea", 0.62)
    try:
        t_val = float(tasa_pea)
        if not (0.2 <= t_val <= 0.95):
            warnings.append(f"Tasa PEA inusual: {t_val}. El valor típico en México oscila entre 0.55 y 0.70.")
    except Exception:
        errors.append("Tasa PEA debe ser un valor numérico.")

    try:
        beta = float(macro_cfg.get("gravity_beta", 0.12))
        if beta <= 0.01 or beta > 0.5:
            warnings.append(f"Parámetro gravity_beta inusual: {beta}. Valores recomendados: 0.08 a 0.20.")
    except Exception:
        errors.append("gravity_beta debe ser un valor numérico.")

    try:
        max_dist = float(macro_cfg.get("max_distance_km", 50.0))
        if max_dist < 10.0 or max_dist > 150.0:
            warnings.append(f"max_distance_km inusual: {max_dist} km. El estándar metropolitano es 40 a 70 km.")
    except Exception:
        errors.append("max_distance_km debe ser un valor numérico.")

    seed_cand = city_cfg.get("seed", macro_cfg.get("seed"))
    if seed_cand is not None and str(seed_cand).strip() != "":
        try:
            s_val = int(seed_cand)
            if s_val < 0:
                warnings.append(f"La semilla ('seed: {s_val}') es negativa. Se recomienda usar enteros no negativos.")
        except Exception:
            errors.append("La semilla aleatoria ('seed') debe ser un número entero.")

    # Validación de Cohortes Demográficas (min_pop_size, target_pop_size, max_pop_size)
    min_pop = macro_cfg.get("min_pop_size", 25)
    target_pop = macro_cfg.get("target_pop_size", 150)
    max_pop = macro_cfg.get("max_pop_size", 200)
    try:
        min_p = int(min_pop)
        target_p = int(target_pop)
        max_p = int(max_pop)
        is_rigid = (min_p == max_p)
        if min_p < 1:
            errors.append(f"min_pop_size ({min_p}) debe ser un entero >= 1.")
        if max_p < min_p:
            errors.append(f"max_pop_size ({max_p}) no puede ser menor que min_pop_size ({min_p}).")
        if not is_rigid and (target_p < min_p or target_p > max_p):
            warnings.append(f"target_pop_size ({target_p}) debería estar entre min_pop_size ({min_p}) y max_pop_size ({max_p}).")
        if min_p < 10:
            warnings.append(f"min_pop_size bajo ({min_p}): cohortes muy pequeñas pueden generar miles de pops y degradar el rendimiento.")
        if max_p > 500:
            warnings.append(f"max_pop_size alto ({max_p}): cohortes masivas pueden provocar picos repentinos en estaciones individuales.")
        summary["cohort_bounds"] = {
            "min": min_p,
            "target": target_p,
            "max": max_p,
            "mode": "rigid" if is_rigid else "adaptive",
            "is_canonical_200": (min_p == 200 and max_p == 200)
        }
    except Exception:
        errors.append("min_pop_size, target_pop_size y max_pop_size deben ser enteros válidos.")

    # 3. Validación de Zonas Aisladas (isolated_zones)
    isolated_zones = cdata.get("isolated_zones") or city_cfg.get("isolated_zones") or []
    summary["isolated_zones_count"] = len(isolated_zones)
    for idx, z in enumerate(isolated_zones):
        z_id = z.get("id", "")
        z_bbox = z.get("bbox", [])
        if not z_id:
            errors.append(f"Zona aislada #{idx+1} carece de identificador 'id'.")
        if not isinstance(z_bbox, (list, tuple)) or len(z_bbox) != 4:
            errors.append(f"Zona aislada '{z_id or idx+1}' debe tener un 'bbox' de 4 valores [min_lon, min_lat, max_lon, max_lat].")
        else:
            try:
                z_b = [float(x) for x in z_bbox]
                if z_b[0] >= z_b[2] or z_b[1] >= z_b[3]:
                    errors.append(f"BBOX de zona aislada '{z_id}' inválido: min >= max.")
            except Exception:
                errors.append(f"Coordenadas de BBOX de zona aislada '{z_id}' no numéricas.")

    # 4. Validación de Zonas de Exclusión (exclusion_zones)
    exclusion_zones = cdata.get("exclusion_zones") or city_cfg.get("exclusion_zones") or []
    summary["exclusion_zones_count"] = len(exclusion_zones)
    for idx, ez in enumerate(exclusion_zones):
        ez_id = ez.get("id", f"excl_{idx+1}")
        ez_bbox = ez.get("bbox")
        ez_poly = ez.get("polygon")
        if not ez_bbox and not ez_poly:
            errors.append(f"Zona de exclusión '{ez_id}' debe definir un 'bbox' o un 'polygon'.")
        if ez_bbox:
            if not isinstance(ez_bbox, (list, tuple)) or len(ez_bbox) != 4:
                errors.append(f"Zona de exclusión '{ez_id}': 'bbox' debe tener 4 valores [min_lon, min_lat, max_lon, max_lat].")
            else:
                try:
                    b = [float(x) for x in ez_bbox]
                    if b[0] >= b[2] or b[1] >= b[3]:
                        errors.append(f"Zona de exclusión '{ez_id}': BBOX inválido (min >= max).")
                except Exception:
                    errors.append(f"Zona de exclusión '{ez_id}': Coordenadas de BBOX no numéricas.")
        if ez_poly:
            if not isinstance(ez_poly, (list, tuple)) or len(ez_poly) < 3:
                errors.append(f"Zona de exclusión '{ez_id}': 'polygon' debe tener al menos 3 vértices [[lon, lat], ...].")
            else:
                try:
                    for pt in ez_poly:
                        _ = float(pt[0]), float(pt[1])
                except Exception:
                    errors.append(f"Zona de exclusión '{ez_id}': Vértices de polígono deben contener coordenadas [lon, lat] numéricas.")

    # 5. Auditoría de POIs (Estándares de Nomenclatura)
    pois = cdata.get("pois") or []
    summary["pois_count"] = len(pois)
    for idx, poi in enumerate(pois):
        p_id = str(poi.get("id", "")).strip()
        p_type = str(poi.get("type", "")).lower()
        p_mode = str(poi.get("mode", "MAX")).upper()
        p_jobs = poi.get("jobs")

        if not p_id:
            errors.append(f"POI #{idx+1} carece de 'id'.")

        # Regla 1: Aeropuertos
        if p_id.startswith("AIR_"):
            clean_name = p_id[4:]
            if "_" in clean_name:
                warnings.append(
                    f"POI '{p_id}': El motor de Subway Builder anexa automáticamente ' Terminal'. "
                    f"Evita guiones bajos como '{clean_name}'. Usa un formato legible (ej. 'AIR_{clean_name.replace('_', ' ')}')."
                )

        # Regla 1: Universidades
        if p_type == "uni" and not p_id.startswith("UNI_"):
            warnings.append(
                f"POI '{p_id}': Es de tipo 'uni' pero no tiene prefijo 'UNI_'. "
                f"El prefijo 'UNI_' es necesario para activar el algoritmo de flujo estudiantil escalonado."
            )

        if p_mode not in ["MAX", "BOOST", "ADDITIVE", "REPLACE", "SUM"]:
            warnings.append(f"POI '{p_id}': Modo '{p_mode}' no estándar. Usar 'MAX', 'BOOST', 'ADDITIVE' o 'REPLACE'.")

        if p_jobs is None or int(p_jobs) <= 0:
            warnings.append(f"POI '{p_id}': Número de empleos ('jobs') nulo o menor a 1.")

    # 6. Estado de Archivos de Datos
    status = inspect_data_files(city_name=name, city_code=code, city_file=city_file)
    summary["data_files"] = {
        "denue_count": len(status.get("denue", {}).get("files", [])),
        "cpv_count": len(status.get("cpv", {}).get("files", [])),
        "ce2024_count": len(status.get("ce2024", {}).get("files", [])),
        "conapo_count": len(status.get("conapo", {}).get("files", [])),
        "osm_count": len(status.get("osm", {}).get("files", []))
    }
    if not status.get("denue", {}).get("files"):
        warnings.append("No se detectó ningún archivo DENUE en la carpeta del proyecto ni en data/.")
    if not status.get("cpv", {}).get("files"):
        warnings.append("No se detectó ningún archivo Censo CPV (RESAGEBURB) en la carpeta del proyecto ni en data/.")

    is_valid = len(errors) == 0
    status_str = "ok" if is_valid and len(warnings) == 0 else ("warning" if is_valid else "error")

    return {
        "valid": is_valid,
        "status": status_str,
        "errors": errors,
        "warnings": warnings,
        "summary": summary
    }


def broadcast_log(line: str, progress: Optional[int] = None, step_name: Optional[str] = None):
    """Envía un mensaje de log y estado a todos los clientes conectados por SSE."""
    msg = {
        "timestamp": time.strftime("%H:%M:%S"),
        "line": line
    }
    with build_lock:
        msg['file'] = active_build.get('config_file')
        if progress is not None:
            active_build["progress"] = progress
            msg["progress"] = progress
        if step_name is not None:
            active_build["step_name"] = step_name
            msg["step_name"] = step_name

        active_build["logs"].append(msg)
        if len(active_build["logs"]) > 2000:
            active_build["logs"].pop(0)

    with queue_lock:
        queues_copy = list(active_build["log_queues"])

    dead_queues = []
    for q in queues_copy:
        try:
            q.put_nowait(msg)
        except Exception:
            dead_queues.append(q)

    if dead_queues:
        with queue_lock:
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
        from sb_mexico.build_delivery import execute_wizard_build

        with build_lock:
            active_build["running"] = True
            active_build['config_file'] = config_file
            active_build["status"] = "running"
            active_build["progress"] = 5
            active_build["step_name"] = "Iniciando Pipeline"
            active_build["logs"].clear()
            active_build["error"] = None
            active_build["result"] = None

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
            city_base = os.path.splitext(os.path.basename(_resolve_city_path(config_file)))[0].lower()
            city_out_dir = os.path.join(DIST_DIR, city_base)
            os.makedirs(city_out_dir, exist_ok=True)
            resolved_config = _resolve_city_path(config_file)
            result = execute_wizard_build(resolved_config, city_out_dir,
                                          effective_data_dir, skip_map=skip_map)
            with build_lock:
                active_build["running"] = False
                active_build["status"] = result['status']
                active_build['result'] = result
                active_build["progress"] = 100
                active_build["step_name"] = "Completado"
            if result['status'] == 'success':
                broadcast_log("✨ ¡Paquete ZIP validado y listo para descargar!", progress=100, step_name="Finalizado")
            else:
                broadcast_log("Demanda generada; falta cartografía para el ZIP: " +
                              ', '.join(result['missing_map_files']), progress=100,
                              step_name="Solo demanda")
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
    # Closing a large response immediately reproducibly truncates filtered Windows
    # loopback traffic. HTTP/1.1 framing permits normal persistent browser delivery.
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        # Keep idle browser sockets beyond Chromium's own idle-pool lifetime.
        # Early server-side closes are unreliable through Windows loopback filters.
        # Requests remain bounded by frontend deadlines; idle threads are daemons.
        self.connection.settimeout(300)

    def end_headers(self):
        self.send_header('X-Wizard-Instance', INSTANCE_ID)
        super().end_headers()

    def log_message(self, format, *args):
        pass

    def _get_allowed_origin(self) -> str:
        headers = getattr(self, "headers", None)
        origin = headers.get("Origin", "") if headers and hasattr(headers, "get") else ""
        if origin:
            parsed = urlparse(origin)
            if parsed.hostname in ["localhost", "127.0.0.1", "::1"]:
                return origin
        port = getattr(self.server, "server_port", 8080) if hasattr(self, "server") and self.server else 8080
        return f"http://127.0.0.1:{port}"

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)

        if path in ["/", "/index.html"]:
            self.serve_html()
        elif path == '/api/instance':
            with open(TEMPLATE_HTML_PATH, 'rb') as source:
                template_hash = hashlib.sha256(source.read()).hexdigest()
            self.serve_json({'instance_id': INSTANCE_ID, 'pid': os.getpid(),
                             'started_at': INSTANCE_STARTED_AT, 'port': self.server.server_port,
                             'project_root': ROOT_DIR, 'code_sha256': INSTANCE_CODE_SHA256,
                             'template_sha256': template_hash})
        elif path.startswith('/static/wizard/'):
            self.serve_static(path)
        elif path in ["/api/cities", "/api/projects"]:
            self.serve_json({"cities": get_available_cities()})
        elif path == "/api/system-check":
            self.serve_json(get_system_health())
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
        elif path == "/api/sources/job":
            try:
                self.serve_json(get_source_download_manager().status(query.get('id', [''])[0]))
            except Exception as error:
                self.serve_error(str(error), 400)
        elif path == "/api/sources/plan-job":
            try:
                self.serve_json(get_source_download_manager().preparation_status(query.get('id', [''])[0]))
            except Exception as error:
                self.serve_error(str(error), 400)
        elif path == "/api/conapo/years":
            city_file = query.get('file', [''])[0]
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            self.serve_json(inspect_conapo_years(city_file, source_year=query.get('source_year', [None])[0]))
        elif path == "/api/conapo/calculate":
            city_file = query.get("file", [""])[0]
            year_param = query.get("year", [""])[0]
            target_year = None
            if year_param:
                try:
                    from sb_mexico.inegi import resolve_projection_year
                    target_year = resolve_projection_year({'projection_year': year_param})
                except ValueError:
                    self.serve_error('Año de proyección inválido', 400)
                    return
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            try:
                cursor = int(query.get('cursor', ['0'])[0])
                if cursor < 0:
                    raise ValueError()
            except ValueError:
                self.serve_error('Página CONAPO inválida', 400)
                return
            factors_res = calculate_conapo_factors(city_file, target_year=target_year,
                                                  automatic=query.get('mode', [''])[0] == 'automatic')
            if factors_res.get('status') == 'ok':
                token = hashlib.sha256(json.dumps(factors_res, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()
                expected_token = query.get('result_token', [''])[0]
                if expected_token and token != expected_token:
                    self.serve_error('La fuente o configuración cambió durante la consulta; vuelve a sincronizar.', 409)
                    return
                rows = factors_res['factors']
                if cursor > len(rows):
                    self.serve_error('Página CONAPO fuera de rango', 400)
                    return
                factors_res = dict(factors_res, factors=[], total_factor_count=len(rows),
                                   result_token=token, next_cursor=None)
                for index in range(cursor, len(rows)):
                    candidate = dict(factors_res, factors=factors_res['factors'] + [rows[index]], next_cursor=index + 1)
                    if len(json.dumps(candidate, ensure_ascii=False, default=str).encode('utf-8')) > 24000:
                        if not factors_res['factors']:
                            self.serve_error('Un registro CONAPO excede el tamaño permitido.', 422)
                            return
                        factors_res['next_cursor'] = index
                        break
                    factors_res['factors'].append(rows[index])
            self.serve_json(factors_res)
        elif path == "/api/macro/detect":
            city_file = query.get("file", [""])[0]
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            res = detect_macro_parameters(city_file)
            self.serve_json(res)
        elif path == "/api/validate":
            city_file = query.get("file", [""])[0]
            if not city_file:
                self.serve_error("Parámetro 'file' faltante", 400)
                return
            val_res = validate_city_configuration(city_file)
            self.serve_json(val_res)
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
            try:
                diagnostics = {}
                points = load_demand_sample(bbox, city_file=city_file, diagnostics=diagnostics)
                self.serve_json({"points": points, "diagnostics": diagnostics})
            except ValueError as error:
                self.serve_error(str(error), 422)
        elif path == "/api/auto-urban-polygon":
            city_file = query.get("file", [""])[0]
            reach_str = query.get("reach_km", query.get("reach", ["15"]))[0]
            try:
                reach_km = float(reach_str)
            except (ValueError, TypeError):
                reach_km = 15.0

            try:
                import math
                import shapely
                from tools.poi_studio import load_demand_sample, load_city_data as l_city
                cdata = l_city(city_file) if city_file else {}
                bbox = cdata.get("city", {}).get("bbox")
                # Ensure we load all points without clipping to any previous urban core
                pts = load_demand_sample(bbox, city_file=city_file, ignore_urban_core=True)
                populated_pts = [
                    p for p in pts
                    if (p.get("residents", 0) > 0 or p.get("jobs", 0) > 0)
                    and p.get("location") and len(p["location"]) == 2
                ]

                if not populated_pts or len(populated_pts) < 3:
                    self.serve_json({"status": "error", "message": "No se encontraron suficientes puntos de población o empleo para calcular el núcleo urbano."})
                    return

                # Calculate weighted mass barycenter of active population & jobs
                tot_weight = sum(max(1.0, p.get("residents", 0) + 1.5 * p.get("jobs", 0)) for p in populated_pts)
                center_lon = sum(p["location"][0] * max(1.0, p.get("residents", 0) + 1.5 * p.get("jobs", 0)) for p in populated_pts) / tot_weight
                center_lat = sum(p["location"][1] * max(1.0, p.get("residents", 0) + 1.5 * p.get("jobs", 0)) for p in populated_pts) / tot_weight

                # Filter by reach distance from barycenter if reach_km > 0
                if reach_km > 0:
                    cos_lat = math.cos(math.radians(center_lat))
                    selected_pts = [
                        p["location"] for p in populated_pts
                        if math.hypot(
                            (p["location"][0] - center_lon) * cos_lat * 111.0,
                            (p["location"][1] - center_lat) * 111.0
                        ) <= reach_km
                    ]
                    # If very few points fall in reach, fallback to all points
                    if len(selected_pts) < 3:
                        selected_pts = [p["location"] for p in populated_pts]

                    ratio = min(0.38, max(0.18, 0.18 + (reach_km - 5.0) * 0.006))
                    buf_deg = min(2.5, max(0.8, reach_km * 0.08)) / 111.0
                else:
                    selected_pts = [p["location"] for p in populated_pts]
                    ratio = 0.25
                    buf_deg = 0.015

                points = [shapely.Point(c[0], c[1]) for c in selected_pts]
                mp = shapely.MultiPoint(points)
                hull = shapely.concave_hull(mp, ratio=ratio).buffer(buf_deg).simplify(0.002, preserve_topology=True)

                if hull.geom_type == "Polygon":
                    coords = [[round(c[0], 5), round(c[1], 5)] for c in hull.exterior.coords]
                elif hull.geom_type == "MultiPolygon":
                    largest = max(hull.geoms, key=lambda g: g.area)
                    coords = [[round(c[0], 5), round(c[1], 5)] for c in largest.exterior.coords]
                else:
                    coords = []

                self.serve_json({
                    "status": "ok",
                    "polygon": coords,
                    "points_count": len(selected_pts),
                    "reach_km": reach_km,
                    "center": [round(center_lon, 5), round(center_lat, 5)]
                })
            except Exception as e:
                self.serve_json({"status": "error", "message": str(e)})
        elif path == "/api/settlement_suggestions":
            try:
                from sb_mexico.toponymy import city_settlement_suggestions
                city_file = query.get("file", [""])[0]
                resolved = _resolve_city_path(city_file)
                self.serve_json(city_settlement_suggestions(resolved))
            except Exception as error:
                self.serve_json({"suggestions": [], "error": str(error)})

        elif path == "/api/toponymy/scan":
            try:
                city_file = query.get("file", [""])[0]
                min_count = int(query.get("min_count", ["8"])[0])
                from sb_mexico.toponymy import scan_city_settlements_catalog
                resolved_f = _resolve_city_path(city_file) if city_file else ""
                catalog = scan_city_settlements_catalog(resolved_f, min_count=min_count)
                self.serve_json({"status": "ok", "catalog": catalog})
            except Exception as e:
                self.serve_json({"status": "error", "message": str(e), "catalog": {"total": 0, "places": []}})
        elif path == "/api/toponymy/osm-preview":
            try:
                city_file = query.get("file", [""])[0]
                resolved_f = _resolve_city_path(city_file) if city_file else ""
                from sb_mexico.toponymy import extract_native_osm_places_preview
                places_osm = extract_native_osm_places_preview(resolved_f)
                self.serve_json({"status": "ok", "places": places_osm, "total": len(places_osm)})
            except Exception as e:
                self.serve_json({"status": "error", "message": str(e), "places": [], "total": 0})
        elif path == "/api/demand-v2-preview":
            try:
                resolved = _resolve_city_path(query.get('file', [''])[0])
                from sb_mexico.demand_v2.integration import preview_candidate
                config = load_city_data(resolved)
                config.setdefault('demand', {})['engine'] = 'v2'
                stage = query.get('stage', ['points'])[0]
                if stage not in ('points', 'allocation'):
                    raise ValueError('Preview stage must be points or allocation')
                base = os.path.splitext(os.path.basename(resolved))[0].lower()
                from sb_mexico.build_delivery import resolve_preview_roads
                roads_path = resolve_preview_roads(resolved, os.path.join(DIST_DIR, base))
                if query.get('async', [''])[0] == '1':
                    root = ROOT_DIR
                    result = candidate_preview_job(resolved, stage,
                        lambda: preview_candidate(config, root, roads_path=roads_path, stage=stage),
                        query.get('job', [None])[0])
                else:
                    result = preview_candidate(config, ROOT_DIR, roads_path=roads_path, stage=stage)
                self.serve_json(result)
            except Exception as error:
                self.serve_error(str(error), 400)
        elif path == "/api/demand-preview":
            city_file = query.get("file", [""])[0]
            city_base = os.path.splitext(os.path.basename(city_file))[0].lower() if city_file else ""
            package_available = False

            # Buscar demand_data.json EXCLUSIVAMENTE dentro de dist/<city_base>/
            target_path = os.path.join(DIST_DIR, city_base, "demand_data.json") if city_base else ""
            try:
                resolved = _resolve_city_path(city_file)
                city_base = os.path.splitext(os.path.basename(resolved))[0].lower()
                from sb_mexico.build_delivery import config_hash
                manifest_path = os.path.join(DIST_DIR, city_base, 'wizard-build.json')
                if os.path.exists(manifest_path):
                    with open(manifest_path, encoding='utf-8') as stream:
                        result = json.load(stream)
                    target_path = (result.get('demand_path', '')
                                   if result['status'] in ('success', 'demand_only')
                                   and result['config_hash'] == config_hash(resolved) else '')
                    package_available = bool(target_path and result['status'] == 'success')
            except (ValueError, OSError, KeyError):
                target_path = ''

            if target_path and os.path.exists(target_path):
                try:
                    with open(target_path, "r", encoding="utf-8") as f:
                        demand_json = json.load(f)
                    from sb_mexico.gravity import calculate_commute_distance_distribution
                    pops = demand_json.get("pops", [])
                    points = demand_json.get("points", [])
                    demand_json["distance_distribution"] = calculate_commute_distance_distribution(pops, points)
                    demand_json.setdefault('metadata', {})['package_available'] = package_available
                    candidate_manifest = os.path.join(os.path.dirname(target_path), 'demand_manifest.json')
                    if os.path.isfile(candidate_manifest):
                        with open(candidate_manifest, encoding='utf-8') as stream:
                            candidate = json.load(stream)
                        demand_json['metadata']['demand_engine'] = candidate.get('engine')
                        demand_json['metadata']['candidate_identity'] = candidate.get('identity')
                        demand_json['metadata']['game_validation'] = candidate.get('game_validation')
                    allocation_path = os.path.join(os.path.dirname(target_path), 'od_allocation_report.json')
                    if os.path.isfile(allocation_path):
                        with open(allocation_path, encoding='utf-8') as stream:
                            allocation = json.load(stream)
                        if allocation.get('mode') == 'balanced_integer_v1':
                            from sb_mexico.od_allocation import validate_integer_margins
                            validation = validate_integer_margins(pops, allocation)
                            demand_json['metadata']['od_allocation'] = dict(
                                mode=allocation['mode'], semantics=allocation['semantics'],
                                validation=validation, small_cohorts=allocation['small_cohorts'])
                    self.serve_json(demand_json)
                except Exception as e:
                    self.serve_error(f"Error al leer demand_data.json de {city_base}: {e}", 500)
            else:
                self.serve_json({"points": [], "pops": [], "distance_distribution": None, "metadata": {"status": "not_compiled", "city": city_base}})
        elif path == "/api/build/stream":
            self.serve_sse_stream()
        elif path == "/api/build/status":
            with build_lock:
                status_copy = {
                    "running": active_build["running"],
                    "progress": active_build["progress"],
                    "step_name": active_build["step_name"],
                    "status": active_build["status"],
                    "error": active_build["error"],
                    "city_code": active_build["city_code"],
                    "result": active_build.get("result"),
                    "logs": list(active_build["logs"][-100:])
                }
            self.serve_json(status_copy)
        elif path == "/api/download":
            from sb_mexico.build_delivery import resolve_download
            city_file = query.get("file", [""])[0]
            try:
                resolved = _resolve_city_path(city_file)
                city_base = os.path.splitext(os.path.basename(resolved))[0].lower()
                target_zip, result = resolve_download(resolved, os.path.join(DIST_DIR, city_base))
                with build_lock:
                    if active_build['running']:
                        raise ValueError('Hay una compilación en progreso; espera a que termine.')
                data = target_zip.read_bytes()
                if hashlib.sha256(data).hexdigest() != result['package_hash']:
                    raise ValueError('El ZIP cambió durante la descarga; recompila.')
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(target_zip)}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, OSError, KeyError) as error:
                self.serve_error(f"ZIP no disponible: {error}", 409)
        else:
            self.serve_error("Ruta no encontrada", 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ('/api/sources/plan', '/api/sources/start'):
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 16384:
                    raise ValueError('Solicitud de descarga fuera de tamaño permitido')
                request = json.loads(self.rfile.read(length).decode('utf-8'))
                manager = get_source_download_manager()
                if path.endswith('/plan'):
                    result = manager.prepare_async(request['file'])
                else:
                    result = manager.start(request['plan_id'], request['kinds'], request.get('enoe_state'),
                                           request.get('enoe_year', 2026), request.get('enoe_quarter', 2),
                                           request.get('refresh') is True)
                self.serve_json(result)
            except Exception as error:
                self.serve_error(str(error), 400)
        elif path == "/api/city/save":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file")
                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                saved_path = save_full_city_data(city_file, req_data)
                self.serve_json({"status": "ok", "saved_path": saved_path,
                                 "demographic_reference": req_data.get('macroeconomics', {}).get('demographic_reference')})
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/homogenize":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                options = req_data.get("options", {})

                from sb_mexico.toponymy_homogenizer import ToponymyHomogenizer
                res = ToponymyHomogenizer.apply_pipeline(places, options)
                self.serve_json({"status": "ok", "result": res})
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/deduplicate":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                dist_m = float(req_data.get("distance_m", 500.0))

                from sb_mexico.toponymy_homogenizer import ToponymyHomogenizer
                kept, removed = ToponymyHomogenizer.spatial_deduplicate(places, distance_threshold_m=dist_m)
                self.serve_json({
                    "status": "ok",
                    "kept": kept,
                    "removed": removed,
                    "removed_count": len(removed)
                })
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/cluster-zones":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                radius_m = float(req_data.get("radius_m", 1000.0))
                heuristic = str(req_data.get("rank_heuristic") or req_data.get("heuristic", "density"))
                exclude_micro = bool(req_data.get("exclude_micro", True))

                from sb_mexico.toponymy_homogenizer import cluster_places_by_proximity
                result = cluster_places_by_proximity(places, radius_m=radius_m, rank_heuristic=heuristic, exclude_micro=exclude_micro)
                self.serve_json(result)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/apply-thinning":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                zones = req_data.get("zones", [])

                from sb_mexico.toponymy_homogenizer import apply_zone_thinning_selection
                result = apply_zone_thinning_selection(places, zones)
                self.serve_json(result)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/find-duplicates":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                dist_m = float(req_data.get("distance_m", 3500.0))

                from sb_mexico.toponymy_homogenizer import find_exact_and_fuzzy_duplicates
                result = find_exact_and_fuzzy_duplicates(places, distance_threshold_m=dist_m)
                self.serve_json(result)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/toponymy/resolve-duplicates":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                places = req_data.get("places", [])
                resolutions = req_data.get("resolutions", [])

                from sb_mexico.toponymy_homogenizer import resolve_duplicate_groups
                result = resolve_duplicate_groups(places, resolutions)
                self.serve_json(result)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/workplace/status":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                if not 0 < content_len <= 262144:
                    raise ValueError('Solicitud de inspección fuera de tamaño permitido')
                req_data = json.loads(self.rfile.read(content_len).decode('utf-8'))
                self.serve_json(automatic_workplace_status(req_data['file']))
            except Exception as e:
                self.serve_error(str(e), 400)

        elif path == "/api/workplace/inspect":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                if not 0 < content_len <= 262144:
                    raise ValueError('Solicitud de inspección fuera de tamaño permitido')
                req_data = json.loads(self.rfile.read(content_len).decode('utf-8'))
                self.serve_json(inspect_workplace_benchmark(req_data['file'], req_data['contract']))
            except (ValueError, KeyError, PermissionError) as e:
                self.serve_error(str(e), 400)
            except Exception as e:
                self.serve_error(str(e), 500)

        elif path == "/api/affluence-zones/inspect":
            try:
                content_len = int(self.headers.get('Content-Length', 0))
                post_body = self.rfile.read(content_len)
                req_data = json.loads(post_body.decode('utf-8'))

                city_file = req_data.get("file", "")
                zone_data = req_data.get("zone", {})
                if not zone_data:
                    self.serve_error("Falta el objeto 'zone'", 400)
                    return

                from sb_mexico.gravity import is_point_in_zone
                from tools.poi_studio import load_demand_sample

                cdata = load_city_data(city_file) if city_file else {}
                bbox = cdata.get("city", {}).get("bbox")
                sample_pts = load_demand_sample(bbox, city_file=city_file)

                multiplier = float(zone_data.get("multiplier", 1.0))
                est_count = 0
                base_jobs = 0

                for pt in sample_pts:
                    loc = pt.get("location", [0, 0])
                    if is_point_in_zone(loc[0], loc[1], zone_data):
                        jobs = int(pt.get("jobs", 0))
                        if jobs > 0:
                            est_count += 1
                            base_jobs += jobs

                boosted_jobs = int(round(base_jobs * multiplier))

                # POIs dentro de la zona (indicando que mantienen su cuota manual)
                pois_inside = []
                for p in cdata.get("pois", []):
                    ploc = p.get("loc", [0, 0])
                    if is_point_in_zone(ploc[0], ploc[1], zone_data):
                        pois_inside.append({
                            "id": p.get("id"),
                            "name": p.get("name"),
                            "jobs": p.get("jobs", 0)
                        })

                # Detección de solapamiento con otras zonas existentes
                existing_zones = cdata.get("affluence_zones", [])
                overlapping = []
                cur_id = zone_data.get("id")
                z_coords = zone_data.get("coordinates")
                from shapely.geometry import Polygon
                if z_coords and len(z_coords) >= 3:
                    try:
                        p1 = Polygon(z_coords)
                        if not p1.is_valid:
                            p1 = p1.buffer(0)
                        for ez in existing_zones:
                            if ez.get("id") == cur_id or not ez.get("enabled", True):
                                continue
                            ez_coords = ez.get("coordinates")
                            if ez_coords and len(ez_coords) >= 3:
                                p2 = Polygon(ez_coords)
                                if not p2.is_valid:
                                    p2 = p2.buffer(0)
                                if p1.intersects(p2):
                                    overlapping.append({
                                        "id": ez.get("id"),
                                        "name": ez.get("name", ez.get("id")),
                                        "multiplier": ez.get("multiplier", 1.0)
                                    })
                    except Exception:
                        pass

                self.serve_json({
                    "status": "ok",
                    "establishment_count": est_count,
                    "base_jobs": base_jobs,
                    "boosted_jobs": boosted_jobs,
                    "pois_inside": pois_inside,
                    "overlapping": overlapping
                })
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
                try:
                    content_len = int(self.headers.get('Content-Length', 0))
                except (ValueError, TypeError):
                    content_len = 0

                MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
                if content_len > MAX_UPLOAD_SIZE:
                    self.serve_error("El archivo excede el tamaño máximo permitido de 2 GB", 413)
                    return
                if content_len <= 0:
                    self.serve_error("El archivo está vacío o Content-Length es inválido", 400)
                    return

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

                norm_data = os.path.normcase(os.path.realpath(DATA_DIR))
                norm_target = os.path.normcase(os.path.realpath(os.path.abspath(target_dir)))
                if not (norm_target == norm_data or norm_target.startswith(norm_data + os.sep)):
                    self.serve_error("Acceso denegado: carpeta de subida fuera de data/", 403)
                    return

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

                with build_lock:
                    if active_build["running"]:
                        self.serve_error("Ya hay una compilación en progreso", 409)
                        return
                    active_build["running"] = True
                    active_build["status"] = "running"
                    active_build["progress"] = 5
                    active_build["step_name"] = "Iniciando Pipeline"
                    active_build["logs"].clear()
                    active_build["error"] = None
                    active_build["result"] = None

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
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
        self.send_header("Vary", "Origin")
        self.end_headers()

        def write_event(body):
            self.wfile.write(f'{len(body):x}\r\n'.encode('ascii') + body + b'\r\n')
            self.wfile.flush()

        log_q = queue.Queue(maxsize=500)
        with queue_lock:
            active_build["log_queues"].append(log_q)

        try:
            with build_lock:
                recent_logs = list(active_build["logs"][-50:])
            for past_log in recent_logs:
                data_str = json.dumps(past_log, ensure_ascii=False)
                write_event(f"data: {data_str}\n\n".encode('utf-8'))
            self.wfile.flush()

            while True:
                try:
                    msg = log_q.get(timeout=15.0)
                    data_str = json.dumps(msg, ensure_ascii=False)
                    write_event(f"data: {data_str}\n\n".encode('utf-8'))
                    self.wfile.flush()
                except queue.Empty:
                    write_event(b": heartbeat\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, ConnectionError, OSError):
            pass
        finally:
            with queue_lock:
                if log_q in active_build["log_queues"]:
                    active_build["log_queues"].remove(log_q)

    def serve_html(self):
        if not os.path.exists(TEMPLATE_HTML_PATH):
            self.serve_error("Template HTML no encontrado", 500)
            return

        with open(TEMPLATE_HTML_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        def version_asset(match):
            path = match.group(1)
            target = os.path.join(STATIC_DIR, path[len('/static/wizard/'):])
            if not os.path.isfile(target):
                return path
            with open(target, 'rb') as asset:
                version = hashlib.sha256(asset.read()).hexdigest()[:12]
            return path + '?v=' + version
        content = re.sub(r'(/static/wizard/[a-zA-Z0-9_./-]+\.(?:js|css))', version_asset, content)

        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header('Connection', 'close' if self.close_connection else 'keep-alive')
        self.send_header('Cache-Control', 'no-store')
        self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
        self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, path):
        from urllib.parse import unquote
        relative = unquote(path[len('/static/wizard/'):])
        root = os.path.realpath(STATIC_DIR)
        try:
            target = os.path.realpath(os.path.join(STATIC_DIR, relative))
            valid = os.path.commonpath([root, target]) == root and os.path.isfile(target)
        except ValueError:
            valid = False
        if not valid:
            self.serve_error('Archivo no encontrado', 404)
            return
        with open(target, 'rb') as source:
            body = source.read()
        content_type = mimetypes.guess_type(target)[0] or 'application/octet-stream'
        if target.endswith('.js'):
            content_type = 'application/javascript'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
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
        self.send_header('Connection', 'close' if self.close_connection else 'keep-alive')
        self.send_header("Access-Control-Allow-Origin", self._get_allowed_origin())
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def serve_error(self, message: str, status: int = 400):
        # Rejected POSTs may have an unread body. Never parse it as a new request.
        if getattr(self, 'command', None) == 'POST':
            self.close_connection = True
        self.serve_json({"error": message}, status=status)


def run_server(port: int = 8080, initial_city: str = None, open_browser: bool = True, host: str = "127.0.0.1"):
    server_address = (host, port)

    try:
        httpd = WizardHTTPServer(server_address, WizardRequestHandler)
    except OSError as error:
        print(f'[ERROR] No se pudo iniciar {host}:{port}: {error}. '
              'Comprueba la instancia existente o elige --port explícitamente.')
        sys.exit(1)

    port = httpd.server_port

    url = f"http://{host}:{port}/"
    if initial_city:
        url += f"?city={initial_city}"

    print("=" * 65)
    print(" 🚇 SUBWAY BUILDER MÉXICO v7.1 - WIZARD STUDIO")
    print(" 🎨 Identidad Gráfica: Metro CDMX / Lance Wyman Standard")
    print("=" * 65)
    print(f" Servidor iniciado en: {url}")
    print(f" Raíz del proyecto:    {ROOT_DIR}")
    print(f' Instancia:            {INSTANCE_ID} (PID {os.getpid()})')
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
        description="Subway Builder México Wizard v7.1 - Suite Integral de Modelación"
    )
    parser.add_argument(
        "--city",
        default=None,
        help="Archivo YAML de ciudad inicial a cargar (ej. cities/cancun_riviera_maya.yaml)"
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
