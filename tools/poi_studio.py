#!/usr/bin/env python3
"""
Subway Builder México v7.2 - POI Studio Server
===============================================
Servidor local interactivo para visualizar, crear, calibrar radios de absorción
y validar POIs sobre mapas Leaflet y capas satelitales en tiempo real.

Uso:
    python tools/poi_studio.py
    python tools/poi_studio.py --city cities/cancun_riviera_maya.yaml
    python tools/poi_studio.py --port 8085 --no-browser
"""

import os
import sys
import re
import glob
import json
import yaml
import argparse
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional, Tuple

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
    if "cancun.yaml" in rel_or_abs_path:
        candidates.append(os.path.abspath(os.path.join(ROOT_DIR, "cities", "cancun_riviera_maya.yaml")))
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
    if not isinstance(data.get("affluence_zones"), list):
        data["affluence_zones"] = []

    from sb_mexico.config_defaults import apply_demand_defaults
    return apply_demand_defaults(data)


def _format_affluence_zones_yaml(zones: List[Dict[str, Any]]) -> str:
    """Serializa la lista de zonas de afluencia a formato YAML canónico."""
    if not zones:
        return ""
    lines = [
        "# ==============================================================================",
        "# ZONAS DE ALTA AFLUENCIA (HIGH ATTRACTION ZONES)",
        "# ==============================================================================",
        "affluence_zones:"
    ]
    for az in zones:
        z_id = str(az.get("id", "zone_1")).strip()
        z_name = str(az.get("name", z_id)).strip()
        z_type = str(az.get("type", "polygon")).strip()
        z_archetype = str(az.get("archetype", "custom")).strip()
        z_mult = float(az.get("multiplier", 2.0))
        z_reach = float(az.get("reach_bonus", 0.3))
        z_enabled = bool(az.get("enabled", True))
        z_color = str(az.get("color", "#F59E0B")).strip()
        z_target_mode = str(az.get("target_mode", "MULTIPLIER")).strip()

        lines.append(f'  - id: "{z_id}"')
        lines.append(f'    name: "{z_name}"')
        lines.append(f'    type: "{z_type}"')
        lines.append(f'    archetype: "{z_archetype}"')
        lines.append(f'    multiplier: {z_mult:.2f}')
        lines.append(f'    reach_bonus: {z_reach:.2f}')
        lines.append(f'    target_mode: "{z_target_mode}"')
        if az.get("target_jobs") is not None and int(az.get("target_jobs", 0)) > 0:
            lines.append(f'    target_jobs: {int(az["target_jobs"])}')
        lines.append(f'    color: "{z_color}"')
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
    return "\n".join(lines).rstrip() + "\n"


def _split_yaml_top_level_blocks(content: str) -> Tuple[str, List[Tuple[str, str]]]:
    """
    Divide un YAML en (encabezado_inicial, [(clave, bloque_texto), ...])
    asociando los comentarios precedentes a cada clave correspondiente.
    Previene la pérdida destructiva de secciones ubicadas después de pois: o places:.
    """
    pattern = re.compile(r'^[a-zA-Z0-9_-]+:', re.MULTILINE)
    matches = list(pattern.finditer(content))
    if not matches:
        return content, []

    lines_offsets = []
    curr = 0
    for line in content.splitlines(keepends=True):
        lines_offsets.append((curr, line))
        curr += len(line)

    block_cut_points = []
    for m in matches:
        m_pos = m.start()
        line_idx = 0
        for idx, (offset, _) in enumerate(lines_offsets):
            if offset == m_pos:
                line_idx = idx
                break

        start_idx = line_idx
        while start_idx > 0:
            prev_line = lines_offsets[start_idx - 1][1].strip()
            if prev_line.startswith("#") or (not prev_line and start_idx > 1 and lines_offsets[start_idx - 2][1].strip().startswith("#")):
                start_idx -= 1
            else:
                break
        block_cut_points.append((lines_offsets[start_idx][0], m.group(0)[:-1]))

    header = content[:block_cut_points[0][0]]
    blocks = []
    for i in range(len(block_cut_points)):
        start_pos, k = block_cut_points[i]
        end_pos = block_cut_points[i + 1][0] if i + 1 < len(block_cut_points) else len(content)
        blocks.append((k, content[start_pos:end_pos]))

    return header, blocks


def save_city_data(
    rel_or_abs_path: str,
    new_pois: List[Dict[str, Any]],
    new_places: Optional[List[Dict[str, Any]]] = None,
    new_affluence_zones: Optional[List[Dict[str, Any]]] = None
) -> None:
    """
    Guarda los POIs, las Colonias/Toponimia (places) y Zonas de Alta Afluencia en el archivo YAML
    preservando la estructura, comentarios y todas las secciones adicionales (isolated_zones,
    exclusion_zones, macroeconomics, etc.) sin truncamiento destructivo.
    """
    fpath = _resolve_city_path(rel_or_abs_path)

    if not os.path.exists(fpath):
        raise FileNotFoundError(f"No existe el archivo de ciudad: {fpath}")

    with open(fpath, "r", encoding="utf-8") as f:
        content = f.read()

    try:
        current_data = yaml.safe_load(content) or {}
    except Exception:
        current_data = {}

    target_az = new_affluence_zones if new_affluence_zones is not None else current_data.get("affluence_zones", [])

    def _safe_q(val: Any) -> str:
        if val is None:
            return '""'
        return json.dumps(str(val), ensure_ascii=False)

    # 1. Formatear el bloque de POIs en YAML limpio
    pois_yaml_lines = ["pois:"]
    for poi in new_pois:
        p_id = poi.get("id", "POI_Sin_Nombre")
        loc = poi.get("loc", [0.0, 0.0])
        jobs = int(poi.get("jobs", 5000))
        rad = int(poi.get("radius_m", 750))
        mode = poi.get("mode", "MAX").upper()

        pois_yaml_lines.append(f'  - id: {_safe_q(p_id)}')
        name_val = poi.get("name")
        if isinstance(name_val, dict):
            pois_yaml_lines.append('    name:')
            if "es" in name_val:
                pois_yaml_lines.append(f'      es: {_safe_q(name_val["es"])}')
            if "en" in name_val:
                pois_yaml_lines.append(f'      en: {_safe_q(name_val["en"])}')
        elif isinstance(name_val, str) and name_val:
            pois_yaml_lines.append(f'    name: {_safe_q(name_val)}')

        if poi.get("type"):
            pois_yaml_lines.append(f'    type: {_safe_q(poi["type"])}')
        if poi.get("sub_type"):
            pois_yaml_lines.append(f'    sub_type: {_safe_q(poi["sub_type"])}')

        pois_yaml_lines.append(f'    loc: [{loc[0]:.5f}, {loc[1]:.5f}]')
        pois_yaml_lines.append(f'    jobs: {jobs}')
        pois_yaml_lines.append(f'    radius_m: {rad}')
        pois_yaml_lines.append(f'    mode: {_safe_q(mode)}')

        if isinstance(poi.get("metadata"), dict) and poi["metadata"]:
            pois_yaml_lines.append('    metadata:')
            for mk, mv in poi["metadata"].items():
                pois_yaml_lines.append(f'      {mk}: {_safe_q(mv)}')
        pois_yaml_lines.append('')

    new_pois_block = "\n".join(pois_yaml_lines).rstrip() + "\n"

    # 2. Formatear el bloque opcional de Places/Toponimia en YAML
    new_places_block = ""
    if new_places is not None and len(new_places) > 0:
        places_yaml_lines = ["# Toponimia y Colonias Curadas (Inyección de etiquetas en .pmtiles)", "places:"]
        from sb_mexico.place_identity import serialize_places
        for place in serialize_places(new_places):
            places_yaml_lines.extend('  ' + line for line in yaml.safe_dump(
                [place], allow_unicode=True, sort_keys=False).rstrip().splitlines())
        new_places_block = "\n".join(places_yaml_lines) + "\n"

    # 3. Preservación modular de bloques evitando cualquier truncamiento destructivo
    header, blocks = _split_yaml_top_level_blocks(content)

    if not blocks:
        updated_content = content + "\n\n" + new_pois_block
        if new_places_block:
            updated_content += "\n" + new_places_block
    else:
        block_keys = {k for k, _ in blocks}
        new_blocks = []
        for k, text in blocks:
            if k == "pois":
                new_blocks.append(new_pois_block)
            elif k == "places":
                if new_places_block:
                    new_blocks.append(new_places_block)
                elif new_places is None:
                    new_blocks.append(text)
            elif k == "affluence_zones":
                if new_affluence_zones is not None:
                    if target_az:
                        new_blocks.append(_format_affluence_zones_yaml(target_az) + "\n")
                else:
                    new_blocks.append(text)
            else:
                new_blocks.append(text)

        if "pois" not in block_keys:
            new_blocks.append(new_pois_block)

        if new_places is not None and len(new_places) > 0 and "places" not in block_keys:
            new_blocks.append(new_places_block)

        if target_az and "affluence_zones" not in block_keys:
            new_blocks.append(_format_affluence_zones_yaml(target_az) + "\n")

        prefix = header.rstrip() + ("\n\n" if header.strip() else "")
        body = "\n\n".join([b.strip() for b in new_blocks if b.strip()]) + "\n"
        updated_content = prefix + body

    with open(fpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(updated_content)


def save_city_pois(rel_or_abs_path: str, new_pois: List[Dict[str, Any]]) -> None:
    """Wrapper de compatibilidad retroactiva para guardar POIs."""
    save_city_data(rel_or_abs_path, new_pois=new_pois)


def load_demand_sample(bbox: List[float] = None, city_file: str = "", ignore_urban_core: bool = False,
                       diagnostics: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Carga puntos de demanda de referencia espacial EXCLUSIVAMENTE derivados de las fuentes oficiales
    de datos (DENUE y Censo CPV RESAGEBURB) en la carpeta del proyecto (target_data_dir).

    Reglas de Integridad:
    1. CERO contaminación de POIs manuales: Ningún POI personalizado (ej. aeropuertos 'AIR_',
       estadios, universidades creadas en el YAML o 'is_special: True') puede aparecer en la
       capa de referencia de empleo o población.
    2. CERO fallbacks cruzados a otras ciudades: Se limita estrictamente a los datos del proyecto activo.
    3. Caché con identidad de configuración, fuentes, geometría y etapa de vista.
    4. Si no hay archivos fuente en la carpeta de datos pero existe demand_data.json compilado
       (ej. entornos de test o proyectos heredados), se purgan estrictamente todos los POIs
       y puntos especiales antes de retornarlo.
    """
    city_base = ""
    if diagnostics is None:
        diagnostics = {}
    target_data_dir = None
    poi_ids = set()
    poi_prefixes = ("AIR_", "UNI_", "TOU_", "MED_", "SPO_", "TRA_")
    cdata = None

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

    if not cdata and city_base:
        cand_yaml = os.path.join(ROOT_DIR, "cities", f"{city_base}.yaml")
        if os.path.exists(cand_yaml):
            try:
                cdata = load_city_data(cand_yaml)
            except Exception:
                pass
    exclusion_zones = cdata.get("exclusion_zones", []) if cdata else []
    city_dict = cdata.get("city", {}) if cdata else {}
    urban_core_polygon = city_dict.get("urban_core_polygon") or (cdata.get("urban_core_polygon") if cdata else None)
    restrict_demand_core = city_dict.get("restrict_demand_to_urban_core", True)
    prep_core = None
    if not ignore_urban_core and urban_core_polygon and restrict_demand_core:
        try:
            from sb_mexico.gravity import prepare_polygon_geom
            prep_core = prepare_polygon_geom(urban_core_polygon)
        except Exception:
            pass

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
            if exclusion_zones:
                try:
                    from sb_mexico.gravity import is_point_in_exclusion_zone
                    if is_point_in_exclusion_zone(loc[0], loc[1], exclusion_zones):
                        continue
                except Exception:
                    pass
            if prep_core:
                try:
                    from sb_mexico.gravity import is_point_in_prepared_polygon
                    if not is_point_in_prepared_polygon(loc[0], loc[1], prep_core):
                        continue
                except Exception:
                    pass

            raw_j = p.get("raw_jobs")
            clean_pts.append({
                "id": p_id,
                "location": [float(loc[0]), float(loc[1])],
                "jobs": int(round(p.get("jobs", 0))),
                "raw_jobs": int(round(raw_j)) if raw_j is not None else int(round(p.get("jobs", 0))),
                "residents": float(p.get("residents", 0)),
                **({'employed_residents': float(p['employed_residents'])}
                   if 'employed_residents' in p else {}),
                **({'labor_commuters': float(p['labor_commuters'])}
                   if 'labor_commuters' in p else {})
            })
        return clean_pts

    if (cdata or {}).get('demand', {}).get('engine') == 'v2':
        import copy
        from sb_mexico.demand_v2.integration import preview_candidate
        candidate_config = copy.deepcopy(cdata)
        if ignore_urban_core:
            candidate_config['city']['restrict_demand_to_urban_core'] = False
        if bbox:
            candidate_config['city']['bbox'] = bbox
        preview = preview_candidate(candidate_config, ROOT_DIR, target_data_dir,
            os.path.join(ROOT_DIR, 'dist', city_base, 'roads.geojson'))
        diagnostics.update(engine='v2', identity=preview['identity'], candidate_report=preview['report'])
        # Existing reference layers intentionally omit POIs; the adapter still applies their rules.
        return _clean_and_filter(preview['points'], bbox)

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

    from sb_mexico.demand_sources import select_sources
    if target_data_dir:
        national_dir = os.path.join(ROOT_DIR, 'data')
        exclusions = (cdata or {}).get('data_exclusions', [])
        denue_files = select_sources(target_data_dir, national_dir, 'denue', exclusions)
        cpv_files = select_sources(target_data_dir, national_dir, 'cpv', exclusions)
    if diagnostics is not None:
        diagnostics.update(stage='census_before_road_snapping',
                           placement_mode=city_dict.get('residential_placement', 'legacy'))
    from sb_mexico.residential_employment import validate_employment_mode, attach_source_context
    employment_mode = validate_employment_mode((cdata or {}).get('macroeconomics', {}).get('residential_employment', 'legacy'))
    diagnostics['employment_mode'] = employment_mode
    from sb_mexico.workplace_employment import validate_workplace_mode, load_workplaces
    workplace_mode = validate_workplace_mode((cdata or {}).get('macroeconomics', {}).get('workplace_employment', 'legacy'))
    diagnostics['workplace_mode'] = workplace_mode
    if workplace_mode != 'legacy' and not denue_files:
        raise ValueError(workplace_mode + ' requires DENUE sources for the demand preview')
    if employment_mode == 'census_employed' and not cpv_files:
        raise ValueError('census_employed requires CPV sources for the demand preview')
    # Si hay fuentes oficiales disponibles en target_data_dir
    if (denue_files or cpv_files) and bbox and len(bbox) == 4:
        import hashlib
        from pathlib import Path
        signature_files = sorted(set(path for path in list(Path(target_data_dir).rglob('*')) + list(Path(national_dir).glob('*'))
                                 if path.is_file() and path.suffix.lower() in
                                 {'.csv', '.xlsx', '.shp', '.dbf', '.shx', '.prj', '.cpg', '.geojson', '.gpkg'}))
        signature = dict(config=cdata, bbox=bbox, ignore_urban_core=ignore_urban_core,
                         files=[(str(path), path.stat().st_size, path.stat().st_mtime_ns)
                                for path in signature_files], version=3,
                         code=[hashlib.sha256((Path(ROOT_DIR) / source).read_bytes()).hexdigest()
                               for source in ('sb_mexico/inegi.py', 'sb_mexico/residential.py',
                                              'sb_mexico/residential_employment.py', 'sb_mexico/demographic_reference.py', 'sb_mexico/source_identity.py', 'sb_mexico/config_defaults.py',
                                              'sb_mexico/workplace_employment.py',
                                              'sb_mexico/ce_controls.py', 'sb_mexico/historical_benchmark.py', 'sb_mexico/historical_transfer.py', 'sb_mexico/automatic_workplace.py',
                                              'sb_mexico/population_projection.py', 'tools/poi_studio.py')
                               if (Path(ROOT_DIR) / source).exists()])
        if (cdata or {}).get('macroeconomics', {}).get('demographic_reference') is not None:
            from sb_mexico.demographic_reference import reference_identity
            signature['demographic_reference_sources'] = reference_identity(cdata['macroeconomics'], ROOT_DIR)
        if workplace_mode != 'legacy':
            from sb_mexico.residential_employment import file_sha256
            workplace_sources = sorted(set(denue_files + select_sources(target_data_dir, national_dir, 'ce', exclusions)))
            if workplace_mode == 'historical_transfer':
                from sb_mexico.historical_transfer import selected_ce_sources
                workplace_sources = sorted(set(denue_files + selected_ce_sources(cdata['macroeconomics'], ROOT_DIR)))
            signature['workplace_source_sha256'] = [(str(path), file_sha256(path)) for path in workplace_sources]
        cache_key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        cache_path = Path(target_data_dir) / '.density_cache.json'
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            if cached.get('key') == cache_key:
                if diagnostics is not None:
                    diagnostics.update(cached.get('diagnostics', {}))
                return _clean_and_filter(cached['points'], bbox)
        except (OSError, ValueError, KeyError):
            pass
        # 2. Generar muestras de densidad a partir de archivos oficiales en tiempo real
        try:
            import numpy as np
            import pandas as pd
            from sb_mexico.inegi import (
                DENUE_ESTRATOS,
                format_cve_mun,
                load_denue,
                calibrate_denue_employment,
                parse_ce2024_municipal,
                parse_enoe_indicators,
            )

            grid_size = float(cdata.get("city", {}).get("grid_size", 0.0025)) if cdata else 0.0025
            macro = cdata.get("macroeconomics", {}) if cdata else {}
            til_1 = macro.get("til_1_state")
            if til_1 is None:
                enoe_files = select_sources(target_data_dir, national_dir, 'enoe', exclusions)
                if enoe_files:
                    try:
                        enoe_res = parse_enoe_indicators(enoe_files[0])
                        til_1 = enoe_res.get("til_1", 0.45)
                    except Exception:
                        til_1 = 0.45
                else:
                    til_1 = 0.45

            ce_benchmarks = macro.get("ce_2024_benchmarks", {})
            ce_files = select_sources(target_data_dir, national_dir, 'ce', exclusions)
            if not ce_benchmarks:
                ce_files = select_sources(target_data_dir, national_dir, 'ce', exclusions)
                for cf in ce_files:
                    try:
                        parsed = parse_ce2024_municipal(cf)
                        if parsed:
                            ce_benchmarks = parsed
                            break
                    except Exception:
                        pass

            df_denue = None
            mza_coords = None
            ageb_coords = None

            if denue_files:
                bbox_dict = {
                    "min_lon": bbox[0],
                    "min_lat": bbox[1],
                    "max_lon": bbox[2],
                    "max_lat": bbox[3]
                }
                df_denue, audit_calib, workplace_report = load_workplaces(
                    denue_files, bbox_dict, {**macro, 'til_1_state':float(til_1)}, ce_benchmarks, ce_files, source_root=ROOT_DIR)
                diagnostics['workplace_employment'] = workplace_report

            # Production census placement, identities, and population factors.
            df_cpv = None
            if cpv_files:
                from sb_mexico.inegi import load_cpv_demography, resolve_projection_year
                from sb_mexico.residential import discover_marco_layers
                from sb_mexico.population_projection import resolve_population_factors
                projections = None
                projection_report = {}
                patterns = ('*pobproy*.csv', '*quinq*.csv', '*pob_proy*.csv',
                            '*conapo*.csv', 'data-*.csv', '*proyeccion*.csv')
                projection_files = select_sources(target_data_dir, national_dir, 'conapo', exclusions)
                if projection_files and macro.get('demographic_reference') is None:
                    projections = resolve_population_factors(projection_files[0], cpv_files, macro, projection_report)
                df_cpv = load_cpv_demography(
                    cpv_files, df_denue if df_denue is not None else pd.DataFrame(),
                    dict(zip(('min_lon', 'min_lat', 'max_lon', 'max_lat'), bbox)),
                    tasa_pea=macro.get('tasa_pea', .62),
                    growth_factors={**(projections or {}), **macro.get('growth_factors', {})} if macro.get('demographic_reference') is None else {},
                    conapo_projections=None,
                    default_growth=macro.get('default_growth_factor', 1.0) if macro.get('demographic_reference') is None else 1.0,
                    marco_paths=(discover_marco_layers(target_data_dir)
                                 if city_dict.get('residential_placement', 'legacy') == 'official_blocks'
                                 else select_sources(target_data_dir, national_dir, 'marco', exclusions)),
                    placement_mode=city_dict.get('residential_placement', 'legacy'),
                    employment_mode=employment_mode, projection_year=resolve_projection_year(macro))
                if macro.get('demographic_reference') is not None:
                    from sb_mexico.demographic_reference import apply_reference
                    df_cpv = apply_reference(df_cpv, cpv_files, macro, ROOT_DIR)
                    projection_report.update(effective_year=2025, year_basis='observed_reference',
                        demographic_reference=df_cpv.attrs['residential_employment']['demographic_reference'])
                if diagnostics is not None:
                    diagnostics['residential_placement'] = df_cpv.attrs.get('residential_placement')
                    diagnostics['population_projection'] = projection_report
                    diagnostics['residential_employment'] = df_cpv.attrs.get('residential_employment')
                    attach_source_context(diagnostics['residential_employment'], city_file,
                                          projection_report, projection_files[:1] if macro.get('demographic_reference') is None else [])
                if projection_report.get('year_basis') == 'unverified':
                    automatic = set(df_cpv.cve_mun_clean) & set(projections or {}) - set(macro.get('growth_factors', {}))
                    if automatic:
                        raise ValueError('Confirma el año del archivo CONAPO antes de previsualizar población.')

            # Agregación espacial en cuadrícula
            grp_d = None
            if df_denue is not None and len(df_denue) > 0:
                from sb_mexico.gravity import assign_zones
                df_denue['zone'] = assign_zones(df_denue[['lon', 'lat']].to_numpy(),
                                               (cdata or {}).get('isolated_zones'))
                df_denue['gx'] = np.floor(df_denue['lon'] / grid_size).astype(int)
                df_denue['gy'] = np.floor(df_denue['lat'] / grid_size).astype(int)
                grp_d = df_denue.groupby(['gx', 'gy', 'zone']).agg(
                    jobs=('calibrated_jobs', 'sum'),
                    raw_jobs=('jobs_formal', 'sum'),
                    lon=('lon', 'mean'),
                    lat=('lat', 'mean')
                ).reset_index()

            # Display residential source placements separately from job grids.
            # Joining on a job centroid would relocate the residential preview.
            raw_points = []
            if grp_d is not None:
                for row in grp_d.itertuples():
                    raw_points.append(dict(id=f'ref_jobs_{row.Index}', location=[float(row.lon), float(row.lat)],
                                           jobs=int(round(row.jobs)), raw_jobs=int(round(row.raw_jobs)), residents=0))
            if df_cpv is not None:
                columns = ['lon', 'lat', 'pobtot_adj'] + (['pea_real'] if employment_mode == 'census_employed' else [])
                if 'occupied_residents' in df_cpv:
                    columns.append('occupied_residents')
                for i, row in enumerate(df_cpv[columns].itertuples(index=False)):
                    raw_points.append(dict(id=f'ref_residential_{i}', location=[float(row.lon), float(row.lat)],
                                           jobs=0, raw_jobs=0, residents=float(row.pobtot_adj),
                                           **({'employed_residents': float(getattr(row,'occupied_residents',row.pea_real))}
                                              if employment_mode == 'census_employed' else {})))
                    if 'occupied_residents' in df_cpv:
                        raw_points[-1]['labor_commuters'] = float(row.pea_real)
            try:
                import uuid
                temporary = cache_path.with_name(cache_path.name + '.' + uuid.uuid4().hex + '.tmp')
                temporary.write_text(json.dumps(dict(version=3, key=cache_key, points=raw_points,
                                                     diagnostics=diagnostics or {})), encoding='utf-8')
                os.replace(temporary, cache_path)
            except OSError:
                pass
            return _clean_and_filter(raw_points, bbox)

        except Exception as e:
            raise ValueError(f"No se pudo generar la referencia de demanda para {city_base}: {e}") from e

    # Fallback estricto: Si NO hay archivos fuente brutos en data/, pero existe demand_data.json
    # compilado (ej. para tests o proyectos heredados), cargar pero PURGANDO estrictamente cualquier POI manual o especial
    if city_base:
        city_demand_path = os.path.join(ROOT_DIR, "dist", city_base, "demand_data.json")
        manifest = os.path.join(ROOT_DIR, 'dist', city_base, 'wizard-build.json')
        if os.path.exists(manifest):
            from sb_mexico.build_delivery import config_hash
            with open(manifest, encoding='utf-8') as stream:
                result = json.load(stream)
            if result['status'] not in ('success', 'demand_only') or result['config_hash'] != config_hash(_resolve_city_path(city_file)):
                return []
            city_demand_path = result.get('demand_path', '')
        if diagnostics is not None:
            diagnostics['stage'] = 'compiled_reference'
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
                from sb_mexico.toponymy import city_settlement_suggestions
                city_file = query.get("file", [""])[0]
                resolved = _resolve_city_path(city_file)
                self.serve_json(city_settlement_suggestions(resolved))
            except Exception as error:
                self.serve_json({"suggestions": [], "error": str(error)})

        elif path == "/api/density":
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
                new_affluence_zones = req_data.get("affluence_zones")

                if not city_file:
                    self.serve_error("Falta el parámetro 'file'", 400)
                    return

                save_city_data(city_file, new_pois=new_pois, new_places=new_places, new_affluence_zones=new_affluence_zones)
                resp_payload = {
                    "status": "ok",
                    "saved_pois": len(new_pois),
                    "saved_places": len(new_places)
                }
                if new_affluence_zones is not None:
                    resp_payload["saved_affluence_zones"] = len(new_affluence_zones)
                self.serve_json(resp_payload)
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
    print(" SUBWAY BUILDER MEXICO v7.2 - POI STUDIO ")
    print("=" * 60)
    print(f" Servidor iniciado en: {url}")
    print(f" Raiz del proyecto:    {ROOT_DIR}")
    print(" [NOTA] POI Studio se encuentra integrado en el Wizard unificado:")
    print("        http://127.0.0.1:8080/#step-4")
    print(" Presiona Ctrl+C para detener el servidor.")
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
        description="POI Studio v7.2 - Visualizador y Editor Interactivo de POIs para Subway Builder México"
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
