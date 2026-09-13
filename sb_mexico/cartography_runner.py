"""
sb_mexico.cartography_runner
=============================
Módulo CLI para ejecución nativa dentro de Linux / WSL.
Permite orquestar depot.maps.MapGen de forma aislada, con optimización
previa de recorte BBOX mediante osmium-tool para acelerar la compilación.
"""

import sys
import os
import argparse
import json
import shutil
import subprocess
import glob
import time
from typing import List, Optional

# Asegurar que el repositorio esté en sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from sb_mexico.cartography import (
    get_optimal_hardware_resources,
    ETIQUETAS_CITIES,
    ETIQUETAS_SUBURBS,
    ETIQUETAS_NEIGHBORHOODS
)


def extract_pbf_bbox_if_large(
    input_pbf: str,
    bbox: List[float],
    build_dir: str,
    city_code: str
) -> str:
    """
    Si el archivo PBF es mayor a 50MB y osmium está disponible, recorta el PBF
    al BBOX de la ciudad con un margen de seguridad de 0.05 grados.
    Esto reduce el tiempo de Planetiler de ~15 minutos a solo ~1-2 minutos.
    """
    if not os.path.exists(input_pbf):
        return input_pbf

    file_size_mb = os.path.getsize(input_pbf) / (1024 * 1024)
    osmium_bin = shutil.which("osmium")

    if file_size_mb > 50 and osmium_bin:
        margin = 0.05
        min_lon = max(-180.0, bbox[0] - margin)
        min_lat = max(-90.0, bbox[1] - margin)
        max_lon = min(180.0, bbox[2] + margin)
        max_lat = min(90.0, bbox[3] + margin)

        clipped_pbf = os.path.join(build_dir, f"{city_code.lower()}_clipped.osm.pbf")
        bbox_str = f"{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}"

        print(f"-> [OPTIMIZACIÓN] PBF nacional detectado ({file_size_mb:.1f} MB).")
        print(f"-> Recortando al BBOX metropolitano con osmium extract [{bbox_str}]...")

        cmd = [
            osmium_bin, "extract",
            "-b", bbox_str,
            "--overwrite",
            "-o", clipped_pbf,
            input_pbf
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(clipped_pbf):
            clipped_size_mb = os.path.getsize(clipped_pbf) / (1024 * 1024)
            print(f"-> Recorte exitoso: {clipped_size_mb:.2f} MB generados. Usando extracto optimizado.")
            return clipped_pbf
        else:
            print(f"-> Advertencia: osmium extract falló ({res.stderr.strip()}). Usando PBF original.")
            return input_pbf

    return input_pbf


def apply_urban_lod_filtering(
    input_pbf: str,
    bbox: List[float],
    urban_core_geojson: str,
    build_dir: str,
    city_code: str
) -> str:
    """
    Subway Builder México: Urban Core AOI LOD Filter.
    Aplica zonificación concéntrica:
    - Conserva todas las calles residenciales, andadores y detalles urbanos DENTRO de urban_core_geojson.
    - Conserva autopistas troncales (motorway, trunk, primary, secondary), agua y cobertura vegetal en TODO el BBOX.
    - Elimina calles menores fuera del núcleo para acelerar Planetiler y WebGL sin romper el horizonte.
    """
    if not os.path.exists(input_pbf) or not os.path.exists(urban_core_geojson):
        return input_pbf

    osmium_bin = shutil.which("osmium")
    if not osmium_bin:
        print("-> [LOD] osmium no disponible; usando PBF original.")
        return input_pbf

    try:
        t0 = time.time()
        print("-> [LOD] Aplicando filtro de Zonificación Concéntrica (Urban Core AOI)...")
        print(f"   • Polígono núcleo: {urban_core_geojson}")

        # 1. Extraer elementos dentro del polígono núcleo urbano
        core_pbf = os.path.join(build_dir, f"{city_code.lower()}_core_raw.osm.pbf")
        res_extract = subprocess.run([
            osmium_bin, "extract",
            "-p", urban_core_geojson,
            "-s", "complete_ways",
            "--overwrite",
            "-o", core_pbf,
            input_pbf
        ], capture_output=True, text=True)

        if res_extract.returncode != 0 or not os.path.exists(core_pbf):
            print(f"   [WARN] osmium extract falló ({res_extract.stderr.strip()}). Usando PBF original.")
            return input_pbf

        # 2. Filtrar solo vías menores dentro del núcleo para evitar colisiones con troncales
        core_minor_pbf = os.path.join(build_dir, f"{city_code.lower()}_core_minor.osm.pbf")
        res_minor = subprocess.run([
            osmium_bin, "tags-filter",
            core_pbf,
            "w/highway=tertiary,tertiary_link,unclassified,residential,living_street,service,pedestrian,footway,cycleway,path",
            "n/place=suburb,neighbourhood,quarter",
            "--overwrite",
            "-o", core_minor_pbf
        ], capture_output=True, text=True)

        # 3. Filtrar red troncal y terreno para TODO el BBOX exterior
        bg_pbf = os.path.join(build_dir, f"{city_code.lower()}_bg_arterials.osm.pbf")
        res_bg = subprocess.run([
            osmium_bin, "tags-filter",
            input_pbf,
            "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link",
            "w/natural=water", "w/waterway=*", "w/landuse=*", "w/boundary=*", "w/landcover=*", "w/aeroway=*",
            "n/place=city,town",
            "--overwrite",
            "-o", bg_pbf
        ], capture_output=True, text=True)

        if res_minor.returncode != 0 or res_bg.returncode != 0 or not os.path.exists(core_minor_pbf) or not os.path.exists(bg_pbf):
            print("   [WARN] osmium tags-filter falló. Usando PBF base.")
            return input_pbf

        # 4. Fusionar troncales + vías menores del núcleo
        optimized_pbf = os.path.join(build_dir, f"{city_code.lower()}_lod_optimized.osm.pbf")
        res_merge = subprocess.run([
            osmium_bin, "merge",
            bg_pbf, core_minor_pbf,
            "--overwrite",
            "-o", optimized_pbf
        ], capture_output=True, text=True)

        if res_merge.returncode == 0 and os.path.exists(optimized_pbf):
            orig_sz = os.path.getsize(input_pbf) / (1024 * 1024)
            opt_sz = os.path.getsize(optimized_pbf) / (1024 * 1024)
            elapsed = time.time() - t0
            print(f"   ✓ PBF optimizado con éxito ({orig_sz:.1f} MB -> {opt_sz:.1f} MB) en {elapsed:.1f}s.")
            return optimized_pbf
        else:
            print(f"   [WARN] osmium merge falló ({res_merge.stderr.strip()}). Usando PBF base.")
            return input_pbf
    except Exception as e:
        print(f"   [WARN] Error durante optimización LOD: {e}. Usando PBF base.")
        return input_pbf


def run_cartography(
    city_code: str,
    bbox: List[float],
    osm_pbf: str,
    output_dir: str,
    building_filter_size: float = 15.0,
    building_simplification: float = 0.2,
    include_ocean: bool = False,
    urban_parks_only: bool = False,
    urban_core_geojson: Optional[str] = None
) -> int:
    if urban_parks_only:
        os.environ["SB_URBAN_PARKS_ONLY"] = "1"
        print("-> [OPCIÓN] Filtrado de parques urbanos activo: omitiendo macro-selvas y bosques rurales.")
    else:
        os.environ["SB_URBAN_PARKS_ONLY"] = "0"

    if urban_core_geojson and os.path.exists(urban_core_geojson):
        os.environ["SB_URBAN_CORE_GEOJSON"] = os.path.abspath(urban_core_geojson)
        print(f"-> [OPCIÓN] Polígono de detalle urbano (LOD) activo: {urban_core_geojson}")
    else:
        os.environ.pop("SB_URBAN_CORE_GEOJSON", None)

    try:
        from tools.patch_depot_wsl import patch_depot_maps
        patch_depot_maps()
    except Exception:
        pass

    try:
        from depot.maps import MapGen
    except ImportError as e:
        print(f"ERROR: 'depot.maps' no está disponible en este entorno Python ({e}).", file=sys.stderr)
        return 1

    work_dir = os.path.abspath(output_dir)
    native_build_dir = os.path.abspath(os.path.expanduser(f"~/build_{city_code.lower()}"))

    if os.path.exists(native_build_dir):
        # Limpiar teselas temporales y pmtiles parciales, preservando descargas pesadas (.pkl, .pbf)
        for stale in glob.glob(os.path.join(native_build_dir, "**", "*.mbtiles"), recursive=True):
            try:
                os.remove(stale)
            except OSError:
                pass
        for stale in glob.glob(os.path.join(native_build_dir, "**", "*.pmtiles"), recursive=True):
            try:
                os.remove(stale)
            except OSError:
                pass
    else:
        os.makedirs(native_build_dir, exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)

    print(f"===========================================================")
    print(f"  SUBWAY BUILDER MÉXICO - WSL CARTOGRAPHY ENGINE")
    print(f"  Ciudad: {city_code} | BBOX: {bbox}")
    print(f"  Directorio ext4: {native_build_dir}")
    print(f"  Directorio destino: {work_dir}")
    print(f"===========================================================")

    # Optimización de recorte BBOX
    effective_pbf = extract_pbf_bbox_if_large(osm_pbf, bbox, native_build_dir, city_code)

    # Optimización de zonificación concéntrica (Urban Core AOI LOD) si se definió polígono núcleo
    if urban_core_geojson and os.path.exists(urban_core_geojson):
        effective_pbf = apply_urban_lod_filtering(effective_pbf, bbox, urban_core_geojson, native_build_dir, city_code)

    pbf_name = os.path.basename(effective_pbf)
    target_pbf = os.path.join(native_build_dir, pbf_name)

    if effective_pbf != target_pbf:
        print(f"-> Copiando {pbf_name} a partición rápida ext4 ({native_build_dir})...")
        shutil.copyfile(effective_pbf, target_pbf)

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

    cores, ram_mb = get_optimal_hardware_resources()
    # MapGen expects RAM in Gigabytes (its setter converts GB to MB: self._RAM = int(RAM * 1000))
    ram_gb = max(2.0, round(ram_mb / 1000.0, 1))
    print(f"-> Inicializando MapGen (Cores: {cores}, RAM asignada: {ram_gb} GB [{ram_mb} MB])...")

    prev_cwd = os.getcwd()
    try:
        os.chdir(native_build_dir)
        m = MapGen(
            city=city_code,
            bbox=bbox,
            osmpbf=pbf_name,
            outputdir=build_output_dir,
            RAM=ram_gb,
            ncores=cores,
            cities=ETIQUETAS_CITIES,
            suburbs=ETIQUETAS_SUBURBS,
            neighborhoods=ETIQUETAS_NEIGHBORHOODS,
            label_name_language="prefer:es",
            road_name_preferred_language="es",
            building_index_filter_size=building_filter_size,
            building_index_simplification=building_simplification,
            building_tile_simplification=building_simplification,
            create_building_foundations=False,
            create_ocean_foundations=include_ocean
        )

        print("-> Ejecutando extracción de geometrías, vialidades, toponimia y edificios 3D...")
        m.run_all()
    finally:
        os.chdir(prev_cwd)

    print("-> Compilación completada en ext4. Transfiriendo archivos a destino...")
    expected_files = [
        f"{city_code}.pmtiles",
        "buildings_index.bin.gz",
        "roads.geojson",
        "runways_taxiways.geojson",
        "ocean_depth_index.json.gz",
        "ocean_depth_index_contours.json.gz"
    ]

    copied_count = 0
    for filename in expected_files:
        matches = glob.glob(os.path.join(native_build_dir, "**", filename), recursive=True)
        if matches:
            src = matches[0]
            dst = os.path.join(work_dir, filename)
            shutil.copyfile(src, dst)
            size_kb = os.path.getsize(dst) / 1024
            print(f"  ✓ Archivo cartográfico transferido: {filename} ({size_kb:,.1f} KB)")
            copied_count += 1
        else:
            if filename in ("ocean_depth_index.json.gz", "ocean_depth_index_contours.json.gz") and not include_ocean:
                continue
            print(f"  ⚠ Advertencia: no se encontró {filename} en {native_build_dir}")

    print(f"-> {copied_count}/{len(expected_files)} artefactos cartográficos generados exitosamente.")
    return 0 if copied_count >= 2 else 1


def main():
    parser = argparse.ArgumentParser(description="Subway Builder México - WSL Cartography Runner")
    parser.add_argument("--city-code", required=True, help="Código de ciudad")
    parser.add_argument("--bbox", nargs=4, type=float, required=True, help="min_lon min_lat max_lon max_lat")
    parser.add_argument("--osm-pbf", required=True, help="Ruta al archivo .osm.pbf")
    parser.add_argument("--output-dir", required=True, help="Directorio destino")
    parser.add_argument("--building-filter-size", type=float, default=15.0)
    parser.add_argument("--building-simplification", type=float, default=0.2)
    parser.add_argument("--include-ocean", action="store_true", default=False)
    parser.add_argument("--urban-parks-only", action="store_true", default=False, help="Excluir macro-selvas/bosques y dejar solo parques urbanos")
    parser.add_argument("--urban-core-geojson", default=None, help="Ruta al GeoJSON del polígono núcleo urbano para LOD espacial")

    args = parser.parse_args()
    ret = run_cartography(
        city_code=args.city_code,
        bbox=args.bbox,
        osm_pbf=args.osm_pbf,
        output_dir=args.output_dir,
        building_filter_size=args.building_filter_size,
        building_simplification=args.building_simplification,
        include_ocean=args.include_ocean,
        urban_parks_only=args.urban_parks_only,
        urban_core_geojson=args.urban_core_geojson
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
