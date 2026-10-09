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
    from .toponymy_sources import read_denue_sources, denue_places
    from .toponymy_sources import read_denue_sources, denue_places
    if not os.path.exists(denue_path) or not bbox or len(bbox) != 4:
        return []
    return denue_places(read_denue_sources([denue_path], bbox), min_count=min_count)


def extract_city_localities_from_denue(
    denue_path: str,
    bbox: List[float],
    min_count: int = 100
) -> List[Dict[str, Any]]:
    """
    Extrae localidades urbanas principales (ciudades, cabeceras, pueblos metropolitanos)
    a partir de la columna 'localidad' del DENUE del INEGI dentro del BBOX.
    Calcula la mediana de coordenadas de sus establecimientos para situar el nodo urbano.
    """
    from .toponymy_sources import read_denue_sources, denue_places
    from .toponymy_sources import read_denue_sources, denue_places
    if not os.path.exists(denue_path) or not bbox or len(bbox) != 4:
        return []
    return denue_places(read_denue_sources([denue_path], bbox), min_count=min_count, cities=True)


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


def run_osmium_places_filter(pbf_path: str, output_geojson: str, timeout: int = 180,
                            bbox=None, diagnostics=None) -> bool:
    """Extract a bounded area and publish only a complete GeoJSON."""
    import shutil, subprocess, uuid
    from pathlib import Path
    token = uuid.uuid4().hex
    output = Path(output_geojson).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    native = shutil.which("osmium")
    wsl = shutil.which("wsl") if not native else None
    if not native and not wsl:
        if diagnostics is not None:
            diagnostics.update(status="error", message="No se encontró osmium nativo ni WSL.")
        return False
    files = []
    temporary = output.with_name(output.name + "." + token + ".tmp.geojson")
    try:
        if wsl:
            from sb_mexico.cartography import to_wsl_path
            source = to_wsl_path(str(Path(pbf_path).resolve()))
            target = to_wsl_path(str(temporary))
            area = "/tmp/sb-toponymy-" + token + "-area.osm.pbf"
            filtered = "/tmp/sb-toponymy-" + token + "-places.osm.pbf"
            def run(args):
                # GNU timeout kills the Linux child too; killing only wsl.exe does not.
                command = [wsl, "--exec", "timeout", str(timeout) + "s", "osmium"] + args
                result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 5)
                if result.returncode:
                    detail = result.stderr.strip()[-800:]
                    if result.returncode == 124:
                        detail = "Tiempo agotado (" + str(timeout) + " s) durante " + args[0]
                    raise RuntimeError(detail or "osmium terminó con código " + str(result.returncode))
        else:
            source = str(Path(pbf_path).resolve())
            target = str(temporary)
            area = str(output.with_name(token + "-area.osm.pbf"))
            filtered = str(output.with_name(token + "-places.osm.pbf"))
            def run(args):
                result = subprocess.run([native] + args, capture_output=True, text=True, timeout=timeout)
                if result.returncode:
                    raise RuntimeError(result.stderr.strip()[-800:] or "Error en osmium")
        if bbox:
            files.append(area)
            run(["extract", source, "-b", ",".join(map(str, bbox)), "-s", "complete_ways",
                 "-o", area, "--overwrite"])
            source = area
        files.append(filtered)
        run(["tags-filter", source, "nwr/place=city,town,suburb,neighbourhood,quarter,village,hamlet",
             "-o", filtered, "--overwrite"])
        run(["export", filtered, "-o", target, "--overwrite"])
        with temporary.open(encoding="utf-8") as stream:
            document = json.load(stream)
        if document.get("type") != "FeatureCollection":
            raise ValueError("La extracción OSM no produjo un GeoJSON válido")
        os.replace(temporary, output)
        if diagnostics is not None:
            diagnostics.update(status="ok", features=len(document.get("features", [])))
        return True
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        if diagnostics is not None:
            diagnostics.update(status="error", message=str(error))
        return False
    finally:
        if wsl and files:
            try:
                subprocess.run([wsl, "--exec", "rm", "-f", "--"] + files,
                               capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        elif native:
            for path in files:
                Path(path).unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def _toponymy_context(city_file):
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    path = Path(city_file)
    with path.open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    bbox = config.get("city", {}).get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4 or not all(math.isfinite(float(x)) for x in bbox):
        raise ValueError("Define un BBOX válido antes de escanear nombres.")
    if bbox[0] >= bbox[2] or bbox[1] >= bbox[3]:
        raise ValueError("El BBOX tiene límites invertidos.")
    project = Path(config.get("data_dir") or "data/" + path.stem)
    if not project.is_absolute():
        project = root / project
    return root, project, config, bbox


def _load_osm_places(city_file, context, sources):
    from pathlib import Path
    import hashlib
    from shapely.geometry import shape
    from .place_identity import merge_places, place_id
    from .toponymy_homogenizer import ToponymyAnalyzer
    root, project, config, bbox = context
    cfg = config.get("city", {})
    base, code = Path(city_file).stem.lower(), str(cfg.get("code", "")).lower()
    exclusions = {str(n).lower() for n in config.get("data_exclusions", [])}
    pbfs = [p for folder in dict.fromkeys((project, root / "data"))
            for p in sorted(folder.glob("*.osm.pbf")) if p.name.lower() not in exclusions]
    prepared = [root / "dist" / base / name for name in
                (base + "_places.geojson", "osm_places.geojson", code + "_places.geojson")]
    prepared.append(root / "dist" / code / "osm_places.geojson")
    item = dict(kind="OSM", status="missing", path="")
    document = None
    if pbfs:
        pbf = pbfs[0]
        signature = dict(path=str(pbf.resolve()), size=pbf.stat().st_size,
                         mtime=pbf.stat().st_mtime_ns, bbox=bbox, version=2,
                         code=[hashlib.sha256((root / p).read_bytes()).hexdigest()
                               for p in ("sb_mexico/toponymy.py", "sb_mexico/place_identity.py")
                               if (root / p).is_file()])
        key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        cached = project / ".toponymy" / (key + ".geojson")
        item.update(path=str(pbf), cached=False)
        try:
            if cached.exists():
                document = json.loads(cached.read_text(encoding="utf-8"))
                if document.get("type") != "FeatureCollection":
                    raise ValueError("Caché OSM inválida")
                item.update(status="ok", cached=True)
            elif run_osmium_places_filter(str(pbf), str(cached), bbox=bbox, diagnostics=item):
                document = json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            item.update(status="error", message=str(error))
    else:
        for candidate in prepared:
            if candidate.exists():
                try:
                    document = json.loads(candidate.read_text(encoding="utf-8"))
                    item.update(status="unverified", path=str(candidate),
                                message="GeoJSON preparado sin huella del PBF original; cobertura sin verificar.")
                    break
                except (OSError, ValueError) as error:
                    item.update(status="error", path=str(candidate), message=str(error))
        if document is None and item["status"] == "missing":
            item["message"] = "No hay PBF OSM autorizado disponible."
    # Legacy manifests are a visible, spatially filtered fallback, never a full-source success.
    if document is None:
        for candidate in (root / "dist" / base / "toponymy_manifest.json",
                          root / "dist" / code / "toponymy_manifest.json"):
            if candidate.exists():
                try:
                    manifest = json.loads(candidate.read_text(encoding="utf-8"))
                    document = dict(features=[
                        dict(properties=dict(name=n.get("name", ""), place=n.get("place", "suburb")),
                             geometry=dict(type="Point", coordinates=[n.get("lon"), n.get("lat")]))
                        for n in manifest.get("osm_nodes", []) + manifest.get("osm_polygons", [])])
                    item["fallback"] = str(candidate)
                    break
                except (OSError, ValueError) as error:
                    item.update(status="error", message=str(error))
    sources.append(item)
    places = []
    for feature in (document or {}).get("features", []):
        props, geom = feature.get("properties", {}), feature.get("geometry") or {}
        name = str(props.get("name") or "").strip()
        typ = str(props.get("place") or "").lower()
        if not name or not typ or name.upper() in {"NAN", "NULL", "DESCONOCIDO", "NINGUNO", "OTRO", "PREDIO SELECCIONADO", "CENTRO"}:
            continue
        if props.get("highway") or props.get("traffic_signals") or props.get("boundary") == "administrative" or props.get("admin_level"):
            continue
        try:
            if geom.get("type") == "Point":
                lon, lat = map(float, geom["coordinates"])
                source = "OSM_NODE"
            elif geom.get("type") in ("Polygon", "MultiPolygon", "LineString"):
                if typ in ("city", "town"):
                    continue
                polygon = shape(geom)
                if polygon.is_empty:
                    continue
                point = polygon.representative_point()
                lon, lat, source = point.x, point.y, "OSM_POLYGON"
            else:
                continue
            if not all(math.isfinite(v) for v in (lon, lat)) or not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
                continue
        except (ValueError, TypeError, KeyError):
            continue
        ptype = "city" if typ in ("city", "town", "borough") else (
            "suburb" if typ in ("suburb", "quarter") else (
            "neighbourhood" if typ in ("neighbourhood", "neighborhood", "subdivision") else "village"))
        cls = ToponymyAnalyzer.classify_name(name)
        place = dict(name=name, loc=[round(lon, 5), round(lat, 5)], type=ptype,
                     source=source, source_files=[item["path"] or item.get("fallback", "")],
                     category="CIUDAD" if ptype == "city" else cls["category"],
                     is_micro=cls["category"] == "PRIVADA_CERRADA", establishments=0)
        place["id"] = place_id(place)
        places.append(place)
    return merge_places([], places)


def scan_city_settlements_catalog(city_file: str, min_count: int = 8) -> Dict[str, Any]:
    from .demand_sources import select_sources
    from .toponymy_sources import read_denue_sources, denue_places
    from .place_identity import merge_places
    from .toponymy_homogenizer import ToponymyAnalyzer
    if not 1 <= min_count <= 1000:
        raise ValueError("El mínimo de establecimientos debe estar entre 1 y 1000.")
    context = _toponymy_context(city_file)
    root, project, config, bbox = context
    sources = []
    osm = _load_osm_places(city_file, context, sources)
    paths = select_sources(project, root / "data", "denue", config.get("data_exclusions", []))
    frame = read_denue_sources(paths, bbox, sources)
    if not paths:
        sources.append(dict(kind="DENUE", status="missing", path="", message="No hay fuentes DENUE autorizadas."))
    localities = denue_places(frame, min_count=80, cities=True)
    settlements = denue_places(frame, min_count=min_count)
    candidates = merge_places([], osm + localities + settlements, config.get("deleted_places", []))
    discovered = merge_places(config.get("places", []), candidates, config.get("deleted_places", []))
    coverage = []
    if not frame.empty:
        for (ent, mun, municipality), group in frame.groupby(["cve_ent", "cve_mun", "municipio"]):
            coverage.append(dict(cve_ent=ent, cve_mun=str(ent).zfill(2) + str(mun).zfill(3),
                                 municipality=municipality, establishments=len(group)))
    warnings = [s.get("message", "Fuente no disponible") for s in sources if s["status"] != "ok"]
    return dict(city=config.get("city", {}).get("name", ""), city_file=city_file, bbox=bbox,
                total=len(discovered), places=discovered, diagnosis=ToponymyAnalyzer.analyze_collection(discovered),
                sources=sources, coverage=coverage, warnings=warnings, partial=bool(warnings),
                new_candidates=len(candidates), preserved=len(config.get("places") or []),
                schema_version=2)


def city_settlement_suggestions(city_file, min_count=10):
    """Legacy suggestion endpoints use the same authorized DENUE set."""
    from .demand_sources import select_sources
    from .toponymy_sources import read_denue_sources, denue_places
    root, project, config, bbox = _toponymy_context(city_file)
    sources = []
    paths = select_sources(project, root / 'data', 'denue', config.get('data_exclusions', []))
    points = denue_places(read_denue_sources(paths, bbox, sources), min_count=min_count)
    return dict(suggestions=points, sources=sources, partial=any(p['status'] != 'ok' for p in sources))


def extract_native_osm_places_preview(city_file: str) -> List[Dict[str, Any]]:
    """Use the same source identity, extraction cache and bbox as the scanner."""
    from .toponymy_homogenizer import normalize_place_scale
    sources = []
    return [dict(place, scale=normalize_place_scale(place["type"]))
            for place in _load_osm_places(city_file, _toponymy_context(city_file), sources)]
