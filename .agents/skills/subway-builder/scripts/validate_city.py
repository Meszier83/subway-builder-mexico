#!/usr/bin/env python3
"""Comprobaciones estaticas parciales de configuracion; no certifica un build."""

import math
import os
from pathlib import Path
import re
import sys
from typing import List, Tuple

import yaml

VALID_POI_PREFIXES = (
    "AIR_", "UNI_", "SPO_", "STA_", "MED_", "HOS_", "MAL_", "SHP_", "COM_",
    "TOU_", "RST_", "CUL_", "MUS_", "AMU_", "CNV_", "PRK_", "REL_", "GOV_",
    "PORT_", "TRA_", "EXT_",
)
VALID_POI_MODES = ("MAX", "BOOST", "REPLACE")
VALID_AFFLUENCE_ARCHETYPES = ("cbd", "tourism", "industrial", "commercial", "custom")


def validate_city_yaml(yaml_path: str) -> Tuple[bool, List[str], List[str]]:
    """Retorna (sin errores estaticos detectados, errores, advertencias)."""
    errors: List[str] = []
    warnings: List[str] = []
    try:
        with open(yaml_path, encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return False, [f"No se pudo leer YAML '{yaml_path}': {exc}"], []
    if not isinstance(data, dict):
        return False, ["El YAML debe tener un diccionario raiz."], []

    def mapping(value, label):
        if not isinstance(value, dict):
            errors.append(f"{label} debe ser un diccionario.")
            return {}
        return value

    def number(value, label, minimum=None, maximum=None, integer=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or (isinstance(value, float) and not math.isfinite(value))):
            errors.append(f"{label} debe ser un numero finito, no un booleano ni texto.")
            return None
        if integer and not isinstance(value, int):
            errors.append(f"{label} debe ser un entero.")
            return None
        if minimum is not None and value < minimum:
            errors.append(f"{label} debe ser >= {minimum}.")
        if maximum is not None and value > maximum:
            errors.append(f"{label} debe ser <= {maximum}.")
        return value

    def boolean_fields(obj, label, fields):
        for field in fields:
            if field in obj and not isinstance(obj[field], bool):
                errors.append(f"{label}.{field} debe ser true o false.")

    def text(value, label):
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label} debe ser texto no vacio.")
            return None
        return value

    def location(value, label):
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            errors.append(f"{label} debe ser [lon, lat].")
            return None
        lon = number(value[0], f"{label}.lon", -180, 180)
        lat = number(value[1], f"{label}.lat", -90, 90)
        return None if lon is None or lat is None else (lon, lat)

    def bbox(value, label):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            errors.append(f"{label} debe ser [min_lon, min_lat, max_lon, max_lat].")
            return None
        start = location(value[:2], f"{label}.min")
        end = location(value[2:], f"{label}.max")
        if start is None or end is None:
            return None
        if start[0] >= end[0] or start[1] >= end[1]:
            errors.append(f"{label} debe tener min_lon < max_lon y min_lat < max_lat.")
        return (*start, *end)

    def polygon(value, label):
        if not isinstance(value, (list, tuple)) or len(value) < 3:
            errors.append(f"{label} requiere al menos tres vertices [lon, lat].")
            return
        for i, vertex in enumerate(value):
            location(vertex, f"{label}[{i}]")

    for section in ("city", "macroeconomics"):
        if section not in data:
            errors.append(f"Falta la seccion obligatoria '{section}'.")
    city = mapping(data.get("city", {}), "city")
    macro = mapping(data.get("macroeconomics", {}), "macroeconomics")
    routing = mapping(data.get("routing", {}), "routing")
    for field in ("code", "name", "description"):
        text(city.get(field), f"city.{field}")
    code = city.get("code")
    if isinstance(code, str) and not re.fullmatch(r"[A-Z0-9]{2,5}", code):
        errors.append("city.code debe tener 2-5 caracteres ASCII mayusculos o digitos.")
    bounds = bbox(city.get("bbox"), "city.bbox")
    if city.get("initial_center") is not None:
        center = location(city["initial_center"], "city.initial_center")
        if center and bounds and not (
                bounds[0] <= center[0] <= bounds[2]
                and bounds[1] <= center[1] <= bounds[3]):
            warnings.append("city.initial_center esta fuera del BBOX.")
    core = city.get("urban_core_polygon")
    if isinstance(core, dict):
        warnings.append("urban_core_polygon GeoJSON requiere inspeccion geometrica; no se valida aqui.")
    elif core is not None:
        polygon(core, "city.urban_core_polygon")
    boolean_fields(city, "city", (
        "bbox_locked", "include_ocean", "urban_parks_only",
        "restrict_demand_to_urban_core", "include_pedestrian_paths",
    ))
    for field in ("grid_size", "building_filter_size", "building_simplification"):
        if field in city:
            val = number(city[field], f"city.{field}", 0)
            if field == "grid_size" and val == 0:
                errors.append("city.grid_size debe ser > 0.")
    for field in ("min_residents", "min_jobs", "seed"):
        if field in city:
            number(city[field], f"city.{field}", 0, integer=True)
    if "initial_zoom" in city:
        number(city["initial_zoom"], "city.initial_zoom", 0)

    for field in ("tasa_pea", "til_1_state", "furness_tol"):
        if field in macro:
            number(macro[field], f"macroeconomics.{field}", 0, 1)
    for field in ("default_growth_factor", "max_distance_km"):
        if field in macro:
            val = number(macro[field], f"macroeconomics.{field}", 0)
            if val == 0:
                errors.append(f"macroeconomics.{field} debe ser > 0.")
    beta = macro.get("gravity_beta")
    if "gravity_beta" in macro and beta is not None and not (
            isinstance(beta, str) and beta.strip().lower() in ("auto", "none", "")):
        number(beta, "macroeconomics.gravity_beta", 0)
    for field in ("sample_threshold", "furness_iterations", "projection_year", "seed"):
        if field in macro:
            number(macro[field], f"macroeconomics.{field}", 0, integer=True)
    sizes = {}
    for field, default in (("min_pop_size", 25), ("target_pop_size", 180), ("max_pop_size", 200)):
        sizes[field] = number(macro.get(field, default), f"macroeconomics.{field}", 1, integer=True)
    if sizes["min_pop_size"] is not None and sizes["max_pop_size"] is not None:
        if sizes["min_pop_size"] > sizes["max_pop_size"]:
            errors.append("min_pop_size debe ser <= max_pop_size.")
        if sizes["max_pop_size"] > 200:
            warnings.append("max_pop_size supera la convencion del proyecto (200); verificar version objetivo.")
    if "growth_factors" in macro:
        growth = mapping(macro["growth_factors"], "macroeconomics.growth_factors")
        for key, value in growth.items():
            if not isinstance(key, str) or not re.fullmatch(r"[0-9]{5}", key):
                errors.append("growth_factors requiere claves municipales de texto EEMMM.")
            val = number(value, f"growth_factors[{key}]", 0)
            if val == 0:
                errors.append(f"growth_factors[{key}] debe ser > 0.")
    if "modal_experiment" in macro:
        modal = mapping(macro["modal_experiment"], "macroeconomics.modal_experiment")
        boolean_fields(modal, "macroeconomics.modal_experiment", ("enabled",))

    # El pipeline admite tres ubicaciones; validar todas y respetar su precedencia.
    for obj, label in ((data, "config"), (macro, "macroeconomics"), (routing, "routing")):
        boolean_fields(obj, label, ("include_driving_path",))
    inc_path = routing.get("include_driving_path", macro.get(
        "include_driving_path", data.get("include_driving_path", False)))
    if inc_path is True:
        warnings.append("include_driving_path esta activado; medir bytes y memoria de carga del runtime objetivo.")

    for section in ("pois", "affluence_zones", "exclusion_zones", "isolated_zones", "places"):
        items = data.get(section, [])
        if not isinstance(items, list):
            errors.append(f"{section} debe ser una lista.")
            items = []
        seen = set()
        for i, item in enumerate(items):
            label = f"{section}[{i}]"
            if not isinstance(item, dict):
                errors.append(f"{label} debe ser un diccionario.")
                continue
            boolean_fields(item, label, ("enabled",))
            if section == "places":
                text(item.get("name"), f"{label}.name")
                location(item.get("loc"), f"{label}.loc")
                continue
            identifier = text(item.get("id"), f"{label}.id")
            if identifier is not None:
                if identifier in seen:
                    errors.append(f"{label}.id duplicado: {identifier}")
                seen.add(identifier)
            if section == "pois":
                location(item.get("loc"), f"{label}.loc")
                for field in ("jobs", "radius_m"):
                    if field in item:
                        number(item[field], f"{label}.{field}", 0, integer=field == "jobs")
                if "mode" in item and item["mode"] not in VALID_POI_MODES:
                    errors.append(f"{label}.mode debe ser MAX, BOOST o REPLACE.")
                if identifier:
                    if not identifier.startswith(VALID_POI_PREFIXES):
                        warnings.append(f"{identifier}: prefijo fuera de la convencion del proyecto.")
                    match = re.match(r"^[A-Z]+_(.*)$", identifier)
                    if match and "_" in match.group(1):
                        warnings.append(f"{identifier}: se recomiendan espacios en el cuerpo del nombre.")
                continue
            if "bbox" in item:
                bbox(item["bbox"], f"{label}.bbox")
            polygon_key = "polygon" if section == "isolated_zones" else "coordinates"
            if polygon_key in item:
                polygon(item[polygon_key], f"{label}.{polygon_key}")
            if "bbox" not in item and polygon_key not in item:
                errors.append(f"{label} requiere bbox o {polygon_key}.")
            if section == "affluence_zones":
                if item.get("archetype", "custom") not in VALID_AFFLUENCE_ARCHETYPES:
                    warnings.append(f"{label}: archetype fuera de los presets del proyecto.")
                for field in ("reach_bonus", "multiplier", "target_jobs"):
                    if field in item:
                        number(item[field], f"{label}.{field}", 0)
                reach = item.get("reach_bonus")
                if isinstance(reach, (int, float)) and not isinstance(reach, bool) and reach > 0.60:
                    warnings.append(f"{label}.reach_bonus supera 0.60; el motor puede acotarlo.")

    repo_root = Path(__file__).resolve().parents[4]
    data_dir = data.get("data_dir")
    if data_dir is not None:
        if text(data_dir, "data_dir") is not None:
            directory = Path(data_dir)
            if not directory.is_absolute():
                directory = repo_root / directory
            if not directory.is_dir():
                warnings.append(f"No existe el data_dir configurado: {directory}")
    else:
        slug = Path(yaml_path).stem.lower()
        candidates = [repo_root / "data" / slug]
        if isinstance(code, str) and re.fullmatch(r"[A-Z0-9]{2,5}", code):
            candidates.append(repo_root / "data" / code.lower())
        if not any(path.is_dir() for path in candidates):
            warnings.append(f"No se detecto un directorio de microdatos para {slug}.")
    return not errors, errors, warnings


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        print("Uso: python validate_city.py <ciudad.yaml>")
        return 1
    valid, errors, warnings = validate_city_yaml(sys.argv[1])
    print(f"VALIDACION ESTATICA PARCIAL: {os.path.basename(sys.argv[1])}")
    for error in errors:
        print(f"[ERROR] {error}")
    for warning in warnings:
        print(f"[WARN] {warning}")
    print("[OK] Sin errores en las comprobaciones estaticas implementadas." if valid
          else "[INVALIDO] Corregir los errores estaticos detectados.")
    print("No verifica fuentes, convergencia OD, cartografia, ZIP ni carga en el juego.")
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
