"""
sb_mexico.special_demand
========================
Módulo de generación y validación de metadatos de demanda especial conforme
al estándar canónico de Subway Builder Modded / Foundry (@subway-builder-modded/special-demand-schemas).
"""

import os
import re
import json
import datetime
from typing import Dict, List, Any, Tuple, Optional

SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "schemas")
TYPES_PATH = os.path.join(SCHEMAS_DIR, "special_demand_types.json")
POINTS_SCHEMA_PATH = os.path.join(SCHEMAS_DIR, "special_demand_points.schema.json")

# Mapeo taxonómico de prefijos clásicos a tipos canónicos de Subway Builder Modded
PREFIX_TAXONOMY_MAP = {
    "AIR_": ("airport", "international_terminal"),
    "UNI_": ("university", None),
    "SPO_": ("sports_facility", None),
    "STA_": ("sports_facility", "stadium"),
    "MED_": ("hospital", None),
    "HOS_": ("hospital", None),
    "MAL_": ("shopping_center", None),
    "SHP_": ("shopping_center", None),
    "COM_": ("shopping_center", None),
    "TOU_": ("resort", None),
    "RST_": ("resort", None),
    "CUL_": ("cultural_center", None),
    "MUS_": ("museum", None),
    "AMU_": ("amusement_park", None),
    "CNV_": ("convention_center", None),
    "PRK_": ("park", None),
    "REL_": ("religious_institution", None),
    "GOV_": ("government_facility", None),
    "PORT_": ("port", None),
    "TRA_": ("transit_station", None),
    "EXT_": ("outside_connection", None),
}


def load_special_demand_types() -> Dict[str, Any]:
    """Carga el catálogo oficial de tipos de demanda especial."""
    if not os.path.exists(TYPES_PATH):
        raise FileNotFoundError(f"Archivo de tipos oficiales no encontrado en '{TYPES_PATH}'.")
    with open(TYPES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def infer_poi_type_and_subtype(poi_id: str, poi_cfg: Optional[Dict] = None) -> Tuple[str, Optional[str]]:
    """
    Infiere el tipo y subtipo canónico a partir del prefijo taxonómico o de la configuración YAML.
    """
    poi_cfg = poi_cfg or {}
    explicit_type = poi_cfg.get("type")
    explicit_subtype = poi_cfg.get("sub_type")

    if explicit_type:
        return explicit_type.strip().lower(), explicit_subtype.strip().lower() if explicit_subtype else None

    for prefix, (t_id, sub_id) in PREFIX_TAXONOMY_MAP.items():
        if poi_id.startswith(prefix):
            return t_id, sub_id

    # Inferencia heurística por palabras clave comunes en nombres mexicanos
    id_lower = poi_id.lower()
    if "aeropuerto" in id_lower or "airport" in id_lower:
        return "airport", "international_terminal"
    if "universidad" in id_lower or "tecnológico" in id_lower or "facultad" in id_lower:
        return "university", None
    if "plaza" in id_lower or "mall" in id_lower or "centro comercial" in id_lower:
        return "shopping_center", None
    if "hotel" in id_lower or "resort" in id_lower or "punta" in id_lower:
        return "resort", None
    if "estadio" in id_lower or "stadium" in id_lower or "deportivo" in id_lower:
        return "sports_facility", "stadium"
    if "hospital" in id_lower or "clínica" in id_lower or "médico" in id_lower:
        return "hospital", None
    if "parque" in id_lower or "park" in id_lower:
        return "park", None

    return "custom", None


def clean_display_name(poi_id: str) -> str:
    """Genera un nombre legible eliminando prefijos de IDs crudos."""
    name = poi_id
    for prefix in PREFIX_TAXONOMY_MAP.keys():
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("_", " ").strip()


def resolve_localized_name(poi_id: str, poi_cfg: Optional[Dict] = None) -> Dict[str, str]:
    """
    Construye el diccionario LocalizedString (__default__, es, en)
    según el estándar @subway-builder-modded/special-demand-schemas.
    """
    poi_cfg = poi_cfg or {}
    raw_name = poi_cfg.get("name")

    if isinstance(raw_name, dict):
        loc = {str(k): str(v) for k, v in raw_name.items()}
        if "__default__" not in loc:
            loc["__default__"] = loc.get("es") or loc.get("en") or next(iter(loc.values()))
        return loc

    if isinstance(raw_name, str) and raw_name.strip():
        val = raw_name.strip()
        return {"__default__": val, "es": val}

    clean_name = clean_display_name(poi_id)
    if poi_id.startswith("AIR_"):
        es_name = f"Aeropuerto Internacional de {clean_name}"
        en_name = f"{clean_name} International Airport"
        return {"__default__": es_name, "es": es_name, "en": en_name}

    return {"__default__": clean_name, "es": clean_name}


def generate_special_demand_points_doc(
    map_code: str,
    special_pois_cfg: List[Dict],
    demand_points: List[Dict],
    timestamp: Optional[str] = None
) -> Dict[str, Any]:
    """
    Genera el documento completo special_demand_points.json conforme
    al schema oficial de Subway Builder Modded.
    """
    point_popids_map = {
        p["id"]: list(p.get("popIds", []))
        for p in demand_points
        if p.get("is_special") or any(p["id"] == poi["id"] for poi in special_pois_cfg)
    }

    iso_time = timestamp or datetime.datetime.now(datetime.timezone.utc).isoformat()
    points_list = []

    for poi in special_pois_cfg:
        poi_id = poi["id"]
        t_id, sub_id = infer_poi_type_and_subtype(poi_id, poi)
        loc_name = resolve_localized_name(poi_id, poi)
        pop_ids = point_popids_map.get(poi_id, [])

        item: Dict[str, Any] = {
            "point_id": poi_id,
            "type": t_id,
            "name": loc_name,
            "pop_ids": pop_ids
        }

        if sub_id:
            item["sub_type"] = sub_id

        meta = {}
        if "source" in poi:
            meta["source"] = str(poi["source"])
        if "metadata" in poi and isinstance(poi["metadata"], dict):
            meta.update(poi["metadata"])
        if meta:
            item["metadata"] = meta

        if "sibling_point_ids" in poi and isinstance(poi["sibling_point_ids"], list):
            item["sibling_point_ids"] = poi["sibling_point_ids"]

        points_list.append(item)

    return {
        "$schema": "special_demand_points.schema.json",
        "version": 1,
        "map_code": map_code,
        "generated_at": iso_time,
        "points": points_list
    }


def validate_special_demand_points(
    content: Dict[str, Any],
    types_doc: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str]]:
    """
    Valida rigurosamente el documento special_demand_points.json contra:
    1. Esquema oficial JSON Schema (utilizando jsonschema si está instalado o validador nativo).
    2. Integridad taxonómica contra special_demand_types.json.
    """
    errors = []
    types_data = types_doc or load_special_demand_types()
    valid_types = {t["id"]: [s["id"] for s in t.get("sub_types", [])] for t in types_data.get("types", [])}

    if content.get("version") != 1:
        errors.append(f"El campo 'version' debe ser 1 (obtenido: {content.get('version')})")
    if not content.get("map_code") or not isinstance(content["map_code"], str):
        errors.append("El campo 'map_code' es obligatorio y debe ser texto (ej. 'CUN').")
    if not isinstance(content.get("points"), list):
        errors.append("El campo 'points' debe ser una lista.")
        return False, errors

    for idx, pt in enumerate(content["points"]):
        p_id = pt.get("point_id")
        if not p_id or not isinstance(p_id, str):
            errors.append(f"Punto #{idx}: 'point_id' obligatorio no válido.")
            continue

        p_type = pt.get("type")
        if not p_type or p_type not in valid_types:
            errors.append(f"Punto '{p_id}': tipo '{p_type}' no existe en el catálogo oficial de special_demand_types.json.")

        sub_type = pt.get("sub_type")
        if sub_type:
            allowed_subs = valid_types.get(p_type, [])
            if allowed_subs and sub_type not in allowed_subs:
                errors.append(f"Punto '{p_id}': sub_tipo '{sub_type}' no está registrado para el tipo '{p_type}'. Subtipos permitidos: {allowed_subs}")

        name = pt.get("name")
        if not isinstance(name, dict) or "__default__" not in name:
            errors.append(f"Punto '{p_id}': 'name' debe ser un objeto LocalizedString con clave obligatoria '__default__'.")

        pop_ids = pt.get("pop_ids")
        if not isinstance(pop_ids, list):
            errors.append(f"Punto '{p_id}': 'pop_ids' debe ser una lista de cadenas.")

    try:
        import jsonschema
        if os.path.exists(POINTS_SCHEMA_PATH):
            with open(POINTS_SCHEMA_PATH, "r", encoding="utf-8") as f:
                schema_json = json.load(f)
            jsonschema.validate(instance=content, schema=schema_json)
    except ImportError:
        pass
    except Exception as e:
        errors.append(f"Error de validación JSON Schema: {e}")

    return len(errors) == 0, errors


def save_special_demand_points(content: Dict[str, Any], output_path: str) -> None:
    """Exporta el archivo JSON con formato limpio y validado."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)
