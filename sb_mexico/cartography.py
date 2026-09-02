"""
sb_mexico.cartography
=====================
Módulo de compilación cartográfica y generación de capas vectoriales
mediante la integración nativa con la librería depot.maps.MapGen.
"""

import os
import shutil
import glob
try:
    import psutil
except ImportError:
    psutil = None
from typing import Dict, List, Optional, Tuple

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
    Si se proporcionan 'places', inyecta un parche de toponimia previa compilación.
    """
    from depot.maps import MapGen

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
        "ocean_depth_index.json.gz"
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
            if filename == "ocean_depth_index.json.gz" and not include_ocean:
                continue
            if filename == "runways_taxiways.geojson":
                continue
            print(f"  [WARN] Advertencia: no se encontró {filename} en {native_build_dir}")

    return generated_files
