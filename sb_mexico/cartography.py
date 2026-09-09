"""
sb_mexico.cartography
=====================
Módulo de compilación cartográfica y generación de capas vectoriales
mediante la integración nativa con la librería depot.maps.MapGen.
"""

import os
import shutil
import glob
import subprocess
try:
    import psutil
except ImportError:
    psutil = None
from typing import Dict, List, Optional, Tuple, Any
from shapely.geometry import shape, mapping, MultiPolygon, Polygon
from shapely.ops import unary_union

ETIQUETAS_CITIES = ['city', 'borough', 'town']
ETIQUETAS_SUBURBS = ['city', 'borough', 'town', 'suburb', 'quarter']
ETIQUETAS_NEIGHBORHOODS = [
    'city', 'borough', 'town', 'suburb', 'quarter',
    'neighbourhood', 'neighborhood', 'subdivision',
    'village', 'hamlet', 'locality',
    'allotment', 'residential', 'city_block', 'isolated_dwelling'
]


def get_optimal_hardware_resources() -> Tuple[int, int]:
    """Calcula cores y memoria RAM recomendada (75% de la RAM disponible)."""
    cores = max(1, (os.cpu_count() or 4) - 1)
    if psutil is not None:
        try:
            ram_disponible_mb = int(psutil.virtual_memory().available / (1024 * 1024))
            ram_mb = max(2048, int(ram_disponible_mb * 0.75))
        except Exception:
            ram_mb = 6000
    else:
        ram_mb = 6000
    return cores, ram_mb


def to_wsl_path(win_path: str) -> str:
    """Convierte una ruta de Windows (ej. C:\\foo\\bar) al formato de montaje de WSL (/mnt/c/foo/bar)."""
    if not win_path:
        return ""
    abs_p = os.path.abspath(win_path)
    drive, rest = os.path.splitdrive(abs_p)
    if drive:
        letter = drive.replace(":", "").lower()
        clean_rest = rest.replace("\\", "/").lstrip("/")
        return f"/mnt/{letter}/{clean_rest}"
    return abs_p.replace("\\", "/")


def process_commercial_college_features(
    features: List[Dict],
    tile_bounds: Optional[Any] = None,
    water_mask: Optional[Any] = None,
    aerodrome_mask: Optional[Any] = None
) -> List[Dict]:
    """
    Procesa las características de la capa 'commercial' aplicando el canon oficial
    de Subway Builder:
    1. Etiquetado dual canónico:
       - 'type': 'college' para campus universitarios y escuelas.
       - 'type': 'commercial' para distritos comerciales y retail.
    2. Regla canónica 'Campus Wins' (disyuntividad espacial estricta):
       - Los campus universitarios tienen prioridad visual y espacial sobre los comercios.
       - La máscara de campus universitarios se sustrae de los polígonos comerciales
         superpuestos, evitando la mezcla anómala de colores semitransparentes en MapLibre.
    """
    if not features:
        return []

    # 1. Normalizar etiquetas y tipología
    normalized_features = []
    for feat in features:
        props = dict(feat.get("properties", {}))
        geom = feat.get("geometry")
        if not geom:
            continue

        raw_type = str(props.get("type", "")).lower()
        raw_kind = str(props.get("kind", "")).lower()

        is_college = (
            raw_type in ["college", "university"]
            or raw_kind in ["college", "university", "school"]
        )

        if is_college:
            props["type"] = "college"
            props["kind"] = "college"
        else:
            props["type"] = "commercial"
            if not props.get("kind"):
                props["kind"] = "commercial"

        normalized_features.append({
            "geometry": geom,
            "properties": props,
            "id": feat.get("id"),
            "type": feat.get("type", "Feature")
        })

    # 2. Construir máscara de campus universitarios (para Campus Wins)
    college_geoms = []
    for feat in normalized_features:
        if feat["properties"].get("type") == "college":
            try:
                g = shape(feat["geometry"])
                if tile_bounds is not None:
                    g = g.intersection(tile_bounds)
                if not g.is_empty:
                    if not g.is_valid:
                        g = g.buffer(0)
                    college_geoms.append(g)
            except Exception:
                continue

    college_mask = unary_union(college_geoms) if college_geoms else None

    # 3. Procesar y recortar geometrías
    kept = []
    for feat in normalized_features:
        try:
            geom = shape(feat["geometry"])
        except Exception:
            continue

        if tile_bounds is not None:
            geom = geom.intersection(tile_bounds)
        if geom.is_empty:
            continue
        if not geom.is_valid:
            geom = geom.buffer(0)

        # Sustraer cuerpos de agua
        if water_mask is not None and not water_mask.is_empty:
            if geom.intersects(water_mask):
                geom = geom.difference(water_mask)
                if not geom.is_valid:
                    geom = geom.buffer(0)

        is_college = (feat["properties"].get("type") == "college")

        if is_college:
            # Los campus universitarios permanecen intactos (Campus Wins)
            if geom.is_empty or geom.area < 1.0:
                continue
            if geom.geom_type not in ("Polygon", "MultiPolygon"):
                parts = [g for g in getattr(geom, 'geoms', []) if g.geom_type in ("Polygon", "MultiPolygon")]
                if not parts:
                    continue
                geom = unary_union(parts) if len(parts) > 1 else parts[0]
            feat["geometry"] = mapping(geom)
            kept.append(feat)
            continue

        # Distritos comerciales: sustraer aeródromos y campus universitarios
        if aerodrome_mask is not None and not aerodrome_mask.is_empty:
            if geom.intersects(aerodrome_mask):
                geom = geom.difference(aerodrome_mask)
                if not geom.is_valid:
                    geom = geom.buffer(0)

        # Regla Campus Wins: sustraer campus universitarios
        if college_mask is not None and not college_mask.is_empty:
            if geom.intersects(college_mask):
                geom = geom.difference(college_mask)
                if not geom.is_valid:
                    geom = geom.buffer(0)

        if geom.is_empty or geom.area < 1.0:
            continue

        if geom.geom_type not in ("Polygon", "MultiPolygon"):
            parts = [g for g in getattr(geom, 'geoms', []) if g.geom_type in ("Polygon", "MultiPolygon")]
            if not parts:
                continue
            geom = unary_union(parts) if len(parts) > 1 else parts[0]

        feat["geometry"] = mapping(geom)
        kept.append(feat)

    return kept


def is_wsl_available() -> Tuple[bool, str, Dict[str, bool]]:
    """
    Comprueba si WSL 2 está disponible con Ubuntu y verifica las herramientas requeridas.
    Si el servicio WSL presenta un error transitorio de Windows (E_UNEXPECTED),
    ejecuta un reinicio ligero automático con wsl --shutdown para auto-recuperarse.
    """
    if os.name != "nt":
        return True, "native", {"tippecanoe": bool(shutil.which("tippecanoe")), "depot": True}

    def _probe_tool(cmd: List[str]):
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    try:
        check_cmd = ["wsl.exe", "-d", "Ubuntu", "-e", "which", "tippecanoe"]
        res = _probe_tool(check_cmd)

        if res.returncode != 0 and "E_UNEXPECTED" in (res.stderr or ""):
            try:
                subprocess.run(["wsl.exe", "--shutdown"], capture_output=True, timeout=5)
                import time; time.sleep(1.2)
                res = _probe_tool(check_cmd)
            except Exception:
                pass

        has_tippecanoe = (res.returncode == 0)

        res_depot = _probe_tool(["wsl.exe", "-d", "Ubuntu", "-e", "python3", "-c", "import depot; print('DEPOT_OK')"])
        has_depot = "DEPOT_OK" in (res_depot.stdout or "")

        tools_status = {
            "tippecanoe": has_tippecanoe,
            "depot": has_depot,
            "wsl": True
        }
        is_ready = has_tippecanoe and has_depot
        return is_ready, "Ubuntu", tools_status
    except Exception as e:
        return False, str(e), {}


def build_city_map_wsl(
    city_code: str,
    bbox: List[float],
    osm_pbf_path: str,
    output_dir: str,
    building_filter_size: float = 15.0,
    building_simplification: float = 0.2,
    include_ocean: bool = False
) -> Dict[str, str]:
    """
    Ejecuta la compilación cartográfica dentro de WSL Ubuntu vía subprocess con streaming en vivo.
    """
    wsl_pbf = to_wsl_path(osm_pbf_path)
    wsl_out = to_wsl_path(output_dir)

    wsl_cmd = [
        "wsl.exe", "-d", "Ubuntu", "-e",
        "python3", "-u", "-m", "sb_mexico.cartography_runner",
        "--city-code", city_code,
        "--bbox", str(bbox[0]), str(bbox[1]), str(bbox[2]), str(bbox[3]),
        "--osm-pbf", wsl_pbf,
        "--output-dir", wsl_out,
        "--building-filter-size", str(building_filter_size),
        "--building-simplification", str(building_simplification)
    ]
    if include_ocean:
        wsl_cmd.append("--include-ocean")

    print(f"-> Conectando con motor cartográfico en WSL 2 (Ubuntu)...")
    print(f"-> Comando WSL: python3 -m sb_mexico.cartography_runner --city-code {city_code} ...")

    proc = subprocess.Popen(
        wsl_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1
    )

    if proc.stdout:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
        proc.stdout.close()

    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"La compilación cartográfica en WSL falló con código de salida {ret}.")

    work_dir = os.path.abspath(output_dir)
    generated_files = {}
    for filename in [f"{city_code}.pmtiles", "roads.geojson", "buildings_index.bin.gz", "runways_taxiways.geojson", "ocean_depth_index.json.gz", "ocean_depth_index_contours.json.gz"]:
        cand = os.path.join(work_dir, filename)
        if os.path.exists(cand):
            generated_files[filename] = cand

    return generated_files


def build_city_map(
    city_code: str,
    bbox: List[float],
    osm_pbf_path: Optional[str] = None,
    output_dir: str = ".",
    build_dir: Optional[str] = None,
    building_filter_size: float = 15.0,
    building_simplification: float = 0.2,
    include_ocean: bool = False,
    places: Optional[List[Dict]] = None
) -> Dict[str, str]:
    """
    Ejecuta el pipeline cartográfico completo de depot.maps.MapGen.
    Genera .pmtiles, roads.geojson, buildings_index.bin.gz, etc.
    Si se ejecuta en Windows, delega transparentemente a WSL 2.
    """
    work_dir = os.path.abspath(output_dir)

    # Localizar archivo PBF si no fue proporcionado directamente
    if not osm_pbf_path or not os.path.exists(osm_pbf_path):
        candidates = glob.glob(os.path.join(work_dir, "*.osm.pbf"))
        if not candidates:
            # Buscar en data/
            candidates = glob.glob(os.path.join(work_dir, "..", "..", "data", "**", "*.osm.pbf"), recursive=True)
        if not candidates:
            raise FileNotFoundError(f"No se encontró ningún archivo .osm.pbf para la compilación cartográfica de {city_code}.")
        osm_pbf_path = candidates[0]

    # Delegación automática a WSL 2 en entornos Windows
    if os.name == "nt":
        wsl_ok, distro, tools = is_wsl_available()
        if wsl_ok:
            return build_city_map_wsl(
                city_code=city_code,
                bbox=bbox,
                osm_pbf_path=osm_pbf_path,
                output_dir=output_dir,
                building_filter_size=building_filter_size,
                building_simplification=building_simplification,
                include_ocean=include_ocean
            )
        else:
            print(f"  [WARN] WSL 2 no está disponible o carece de herramientas ({distro}).")
            print("  -> Se omite la generación cartográfica nativa.")
            generated_files = {}
            for fname in [f"{city_code}.pmtiles", "roads.geojson", "buildings_index.bin.gz"]:
                cand = os.path.join(work_dir, fname)
                if os.path.exists(cand):
                    generated_files[fname] = cand
            return generated_files

    try:
        from depot.maps import MapGen
    except (ImportError, ModuleNotFoundError) as e:
        print(f"  [WARN] 'depot.maps' no está disponible en este entorno ({e}).")
        generated_files = {}
        for fname in [f"{city_code}.pmtiles", "roads.geojson", "buildings_index.bin.gz"]:
            cand = os.path.join(work_dir, fname)
            if os.path.exists(cand):
                generated_files[fname] = cand
        return generated_files

    work_dir = os.path.abspath(output_dir)
    native_build_dir = os.path.abspath(build_dir or os.path.expanduser(f"~/build_{city_code.lower()}"))

    # Localizar archivo PBF
    if not osm_pbf_path or not os.path.exists(osm_pbf_path):
        candidates = glob.glob(os.path.join(work_dir, "*.osm.pbf"))
        if not candidates:
            raise FileNotFoundError("No se encontró ningún archivo .osm.pbf en el directorio de trabajo.")
        osm_pbf_path = candidates[0]

    osm_pbf_name = os.path.basename(osm_pbf_path)
    cores, ram_mb = get_optimal_hardware_resources()

    os.makedirs(native_build_dir, exist_ok=True)
    target_pbf_path = os.path.join(native_build_dir, osm_pbf_name)

    # Inyección opcional de toponimia curada si se definieron 'places'
    if places and len(places) > 0:
        try:
            from sb_mexico.toponymy import generate_osm_patch
            patch_path = os.path.join(native_build_dir, f"{city_code.lower()}_places_patch.osm")
            generate_osm_patch(places, patch_path)
            print(f"-> Parche de toponimia generado: {len(places)} colonias en {patch_path}")
        except Exception as e:
            print(f"  [WARN] No se pudo generar el parche de toponimia: {e}")

    # Copiar PBF al directorio de compilación solo si no existe o cambió de tamaño
    if not os.path.exists(target_pbf_path) or os.path.getsize(target_pbf_path) != os.path.getsize(osm_pbf_path):
        print(f"-> Copiando {osm_pbf_name} a {native_build_dir}...")
        shutil.copyfile(osm_pbf_path, target_pbf_path)
    else:
        print(f"-> Reutilizando {osm_pbf_name} existente en {native_build_dir}.")

    build_output_dir = os.path.join(native_build_dir, city_code)
    os.makedirs(build_output_dir, exist_ok=True)

    # Sincronización previa de caché de batimetría si existe en work_dir
    if include_ocean:
        ocean_cache_main = os.path.join(work_dir, "ocean_depth_index.json.gz")
        ocean_cache_contours = os.path.join(work_dir, "ocean_depth_index_contours.json.gz")
        if os.path.exists(ocean_cache_main) and os.path.exists(ocean_cache_contours):
            print("-> [Caché] Restaurando batimetría previamente calculada para reutilización inmediata (0s)...")
            shutil.copyfile(ocean_cache_main, os.path.join(build_output_dir, "ocean_depth_index.json.gz"))
            shutil.copyfile(ocean_cache_contours, os.path.join(build_output_dir, "ocean_depth_index_contours.json.gz"))

    print(f"-> Inicializando MapGen para {city_code} (Cores: {cores}, RAM: {ram_mb} MB)...")

    prev_cwd = os.getcwd()
    try:
        os.chdir(native_build_dir)
        m = MapGen(
            city=city_code,
            bbox=bbox,
            osmpbf=osm_pbf_name,
            outputdir=build_output_dir,
            RAM=ram_mb,
            ncores=cores,
            cities=ETIQUETAS_CITIES,
            suburbs=ETIQUETAS_SUBURBS,
            neighborhoods=ETIQUETAS_NEIGHBORHOODS,
            label_name_language="prefer:es",
            road_name_preferred_language="es",
            building_index_filter_size=building_filter_size,
            building_index_simplification=building_simplification,
            create_ocean_foundations=include_ocean
        )

        print("-> Ejecutando extracción de geometrías, vialidades, toponimia y edificios 3D...")
        m.run_all()
    finally:
        os.chdir(prev_cwd)
    generated_files = {}

    expected_files = [
        f"{city_code}.pmtiles",
        "buildings_index.bin.gz",
        "roads.geojson",
        "runways_taxiways.geojson",
        "ocean_depth_index.json.gz",
        "ocean_depth_index_contours.json.gz"
    ]

    for filename in expected_files:
        matches = glob.glob(os.path.join(native_build_dir, "**", filename), recursive=True)
        if matches:
            src = matches[0]
            dst = os.path.join(work_dir, filename)
            shutil.copyfile(src, dst)
            generated_files[filename] = dst
            print(f"  [OK] Archivo cartográfico copiado: {filename}")
        else:
            if filename in ("ocean_depth_index.json.gz", "ocean_depth_index_contours.json.gz") and not include_ocean:
                continue
            if filename == "runways_taxiways.geojson":
                continue
            print(f"  [WARN] Advertencia: no se encontró {filename} en {native_build_dir}")

    return generated_files
