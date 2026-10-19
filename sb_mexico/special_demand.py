"""
sb_mexico.special_demand
========================
Módulo de generación y validación de metadatos de demanda especial conforme
al estándar canónico de Subway Builder Modded / Foundry (@subway-builder-modded/special-demand-schemas).
"""

import os
import re
import json
import math
import statistics
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


TYPE_ALIAS_MAP = {
    "air": "airport",
    "uni": "university",
    "spo": "stadium",
    "tou": "resort",
    "med": "hospital",
    "tra": "transit_station",
    "cen": "convention_center",
    "cul": "cultural_center",
    "mus": "museum",
    "amu": "amusement_park",
    "prk": "park",
    "rel": "religious_institution",
    "gov": "government_facility",
    "port": "port",
    "rst": "resort"
}


def infer_poi_type_and_subtype(poi_id: str, poi_cfg: Optional[Dict] = None) -> Tuple[str, Optional[str]]:
    """
    Infiere el tipo y subtipo canónico a partir del prefijo taxonómico o de la configuración YAML.
    """
    poi_cfg = poi_cfg or {}
    explicit_type = poi_cfg.get("type")
    explicit_subtype = poi_cfg.get("sub_type")

    if explicit_type:
        clean_type = explicit_type.strip().lower()
        normalized_type = TYPE_ALIAS_MAP.get(clean_type, clean_type)
        return normalized_type, explicit_subtype.strip().lower() if explicit_subtype else None

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


def write_railyard_metadata_dir(out_dir: str, special_demand_points_doc: Dict[str, Any]) -> str:
    """
    Escribe el subdirectorio .railyard_map/ conforme al estándar canónico de depot y Railyard:
    - .railyard_map/special_demand_points.json
    - .railyard_map/special_demand_types.json
    """
    railyard_dir = os.path.join(out_dir, ".railyard_map")
    os.makedirs(railyard_dir, exist_ok=True)

    sp_target = os.path.join(railyard_dir, "special_demand_points.json")
    with open(sp_target, "w", encoding="utf-8") as f:
        json.dump(special_demand_points_doc, f, indent=2, ensure_ascii=False)

    types_target = os.path.join(railyard_dir, "special_demand_types.json")
    types_doc = load_special_demand_types()
    with open(types_target, "w", encoding="utf-8") as f:
        json.dump(types_doc, f, indent=2, ensure_ascii=False)

    return railyard_dir


def generate_railyard_description_md(
    city_info: Dict[str, Any],
    demand_payload: Dict[str, Any],
    special_demand_points_doc: Optional[Dict[str, Any]] = None,
    out_dir: Optional[str] = None
) -> str:
    """
    Genera el archivo description.md conforme a la especificación de DemandData.create_description() de depot
    para la publicación oficial de mapas en Railyard.
    """
    name = city_info.get("name", "Map")
    code = str(city_info.get("code", "MAP")).upper()
    version = str(city_info.get("version", "7.2.0"))
    description_text = city_info.get("description", f"Mapa oficial de {name} para Subway Builder.")
    creator = city_info.get("creator", "Subway Builder México Pipeline")
    bbox = [float(x) for x in city_info.get("bbox", [-99.3, 19.2, -98.9, 19.6])[:4]]

    # Cálculo esferoidal del área del BBOX (equivalente a depot / DemandData.create_description)
    minlon, minlat, maxlon, maxlat = [math.radians(v) for v in bbox]
    area_sq_km = int(math.floor((6371.0 ** 2) * abs(math.sin(minlat) - math.sin(maxlat)) * abs(minlon - maxlon)))

    bbox_str = f"{bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]}"
    points = demand_payload.get("points", [])
    pops = demand_payload.get("pops", [])

    total_demand = sum(int(p.get("size", 0)) for p in pops)
    if total_demand == 0 and points:
        total_demand = sum(int(p.get("residents", 0)) for p in points)

    sp_points = (special_demand_points_doc.get("points", []) if special_demand_points_doc else [])
    types_doc = load_special_demand_types()
    type_labels = {t["id"]: t["label"].get("__default__", t["id"]) for t in types_doc.get("types", [])}
    type_by_code = {t["code"]: t["label"].get("__default__", t["code"]) for t in types_doc.get("types", [])}

    categories: Dict[str, Dict[str, Any]] = {}
    pops_by_id = {p.get("id"): p for p in pops if "id" in p}

    for sp in sp_points:
        p_id = sp.get("point_id", "")
        prefix = p_id.split("_")[0] if "_" in p_id else sp.get("type", "POI")
        cat_label = type_by_code.get(prefix) or type_labels.get(sp.get("type", "")) or prefix
        p_name = sp.get("name", {}).get("__default__", p_id)

        if prefix not in categories:
            categories[prefix] = {"label": cat_label, "total": 0, "entries": {}}
        if p_name not in categories[prefix]["entries"]:
            categories[prefix]["entries"][p_name] = {"type": sp.get("type"), "size": 0}

        for pid in sp.get("pop_ids", []):
            if pid in pops_by_id:
                pop_sz = int(pops_by_id[pid].get("size", 0))
                categories[prefix]["entries"][p_name]["size"] += pop_sz
                categories[prefix]["total"] += pop_sz

    nspec = sum(c["total"] for c in categories.values())
    normal_demand = max(0, total_demand - nspec)

    def fmt(v: Any) -> str:
        if isinstance(v, (int, float)):
            if isinstance(v, float):
                return f"{v:,.1f}" if v >= 10000 else str(v)
            return f"{v:,}" if v >= 10000 else str(v)
        return str(v)

    md: List[str] = [
        f"<h1>{name}</h1>",
        f"<h3>{code} · {version}</h3>",
        "",
        f"<p>{description_text}</p>",
        "",
        "<h2>Coverage</h2>",
        '<table style="width: auto">',
        f"<tr><td><strong>Bounding box</strong></td><td>{bbox_str}</td></tr>",
        f"<tr><td><strong>Bounding Box Area</strong></td><td>{area_sq_km:,} km²</td></tr>",
        "</table>",
        "",
        "<h2>Population Summary</h2>",
        '<table style="width: auto">',
        f'<tr><td><strong>Total Modeled Demand</strong></td><td align="right">{fmt(total_demand)}</td></tr>',
        f'<tr><td><strong>Modeled Normal Demand</strong></td><td align="right">{fmt(normal_demand)}</td></tr>',
        f'<tr><td><strong>Modeled Special Demand</strong></td><td align="right">{fmt(nspec)}</td></tr>',
        "</table>",
        ""
    ]

    # Estadísticas del mapa
    if points:
        pt_sizes = [p.get("jobs", 0) + p.get("residents", 0) for p in points]
        med_point = int(statistics.median(pt_sizes)) if pt_sizes else 0
        avg_point = round(float(statistics.mean(pt_sizes)), 1) if pt_sizes else 0.0
    else:
        med_point, avg_point = 0, 0.0

    if pops:
        pop_sizes = [p.get("size", 0) for p in pops]
        med_pop = int(statistics.median(pop_sizes)) if pop_sizes else 0
        avg_pop = round(float(statistics.mean(pop_sizes)), 1) if pop_sizes else 0.0

        distances = [float(p.get("drivingDistance", 0)) / 1000.0 for p in pops]
        med_dist = round(float(statistics.median(distances)), 2) if distances else 0.0
        avg_dist = round(float(statistics.mean(distances)), 2) if distances else 0.0

        times = [float(p.get("drivingSeconds", 0)) / 60.0 for p in pops]
        med_time = round(float(statistics.median(times)), 1) if times else 0.0
        avg_time = round(float(statistics.mean(times)), 1) if times else 0.0
    else:
        med_pop, avg_pop, med_dist, avg_dist, med_time, avg_time = 0, 0.0, 0.0, 0.0, 0.0, 0.0

    stats = [
        ("Demand Points", len(points)),
        ("Populations", len(pops)),
        ("Median Point Size", med_point),
        ("Mean Point Size", avg_point),
        ("Median Population Size", med_pop),
        ("Mean Population Size", avg_pop),
        ("Median Commute Distance (km)", med_dist),
        ("Mean Commute Distance (km)", avg_dist),
        ("Median Commute Time (min)", med_time),
        ("Mean Commute Time (min)", avg_time),
    ]

    md.append("<h2>Map Statistics</h2>")
    md.append('<table style="width: auto">')
    for label, val in stats:
        md.append(f'<tr><td><strong>{label}</strong></td><td align="right">{fmt(val)}</td></tr>')
    md.append("</table>\n")

    if categories:
        md.append("<h2>Special Demand</h2>\n")
        for cat in sorted(categories.keys()):
            c_data = categories[cat]
            if c_data["total"] == 0 and not c_data["entries"]:
                continue
            md.append("<details>")
            md.append(f"<summary>{c_data['label']} — {fmt(c_data['total'])}</summary>\n")
            md.append('<table style="width: auto">')
            md.append('<tr><th align="left">Name</th><th align="right">Modeled Demand</th></tr>')
            for entry, ent_data in sorted(c_data["entries"].items(), key=lambda x: x[1]["size"], reverse=True):
                md.append(f'<tr><td align="left">&nbsp;&nbsp;{entry}&nbsp;&nbsp;</td><td align="right">&nbsp;&nbsp;{fmt(ent_data["size"])}&nbsp;&nbsp;</td></tr>')
            md.append("</table>\n</details>\n")
        md.append("<br>\n")

    use_bld = city_info.get("use_buildings", True)
    bld_foundations = city_info.get("create_building_foundations", True)
    ocean = city_info.get("include_ocean", False)

    md.extend([
        "<h2>Additional Features</h2>",
        "<ul>",
        f"<li><strong>Building Collision</strong> — {'Included (Overture Maps 3D)' if use_bld else 'Disabled'}. "
        f"{'Buildings have underground foundations ranging from -10 m to -80 m calculated from height and footprint.' if bld_foundations else ''}</li>",
        f"<li><strong>Water Depths</strong> — {'Included (GEBCO bathymetry / flat -5 m ocean depths).' if ocean else 'Disabled / standard.'}</li>",
        "<li><strong>Place Labels</strong> — Included city, suburb, and neighborhood labels extracted from OpenStreetMap and curated toponymy.</li>",
        "</ul>",
        "<h2>Methodology</h2>",
        "<ul>",
        "<p>This map was generated using the Subway Builder México pipeline and Depot mapmaking utilities. "
        "Origin-destination commuter trips are allocated using a bounded multinomial gravity model calibrated on municipal and inter-AGEB commuting margins.</p>",
        "</ul>",
        "<h2>Data Sources</h2>",
        "<ul>",
        "<p><strong>INEGI</strong> — Censo de Población y Vivienda 2020 (RESAGEBURB / CPV universal por manzana).</p>",
        "<p><strong>INEGI DENUE / SAIC</strong> — Directorio Estadístico Nacional de Unidades Económicas y Censos Económicos.</p>",
        "<p><strong>CONAPO</strong> — Proyecciones de la Población de los Municipios de México 2020–2026.</p>",
        "<p><strong>OpenStreetMap & Overture Maps</strong> — Vías peatonales/carreteras, toponimia y volumetría de edificios 3D.</p>",
        "</ul>",
        "<h2>License</h2>",
        '<p>This map data is released under the <a href="https://www.gnu.org/licenses/gpl-3.0.html">GNU General Public License v3.0</a>.</p>',
        "<h2>Credits</h2>",
        f"<p>Map authored by {creator}. Powered by Subway Builder México & Subway-Builder-Modded/depot.</p>"
    ])

    full_md = "\n".join(md) + "\n"
    if out_dir:
        with open(os.path.join(out_dir, "description.md"), "w", encoding="utf-8") as f:
            f.write(full_md)
    return full_md
