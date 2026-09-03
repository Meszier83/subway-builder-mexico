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


def run_cartography(
    city_code: str,
    bbox: List[float],
    osm_pbf: str,
    output_dir: str,
    building_filter_size: float = 15.0,
    building_simplification: float = 0.2,
    include_ocean: bool = False
) -> int:
    """Ejecuta la compilación con depot.maps.MapGen en el entorno Linux/WSL."""
    try:
        from depot.maps import MapGen
    except ImportError as e:
        print(f"ERROR: 'depot.maps' no está disponible en este entorno Python ({e}).", file=sys.stderr)
        return 1

    work_dir = os.path.abspath(output_dir)
    native_build_dir = os.path.abspath(os.path.expanduser(f"~/build_{city_code.lower()}"))

    if os.path.exists(native_build_dir):
        shutil.rmtree(native_build_dir)
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
    pbf_name = os.path.basename(effective_pbf)
    target_pbf = os.path.join(native_build_dir, pbf_name)

    if effective_pbf != target_pbf:
        print(f"-> Copiando {pbf_name} a partición rápida ext4 ({native_build_dir})...")
        shutil.copyfile(effective_pbf, target_pbf)

    build_output_dir = os.path.join(native_build_dir, city_code)
    os.makedirs(build_output_dir, exist_ok=True)

    cores, ram_mb = get_optimal_hardware_resources()
    print(f"-> Inicializando MapGen (Cores: {cores}, RAM asignada: {ram_mb} MB)...")

    prev_cwd = os.getcwd()
    try:
        os.chdir(native_build_dir)
        m = MapGen(
            city=city_code,
            bbox=bbox,
            osmpbf=pbf_name,
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

    print("-> Compilación completada en ext4. Transfiriendo archivos a destino...")
    expected_files = [
        f"{city_code}.pmtiles",
        "buildings_index.bin.gz",
        "roads.geojson",
        "runways_taxiways.geojson",
        "ocean_depth_index.json.gz"
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

    args = parser.parse_args()
    ret = run_cartography(
        city_code=args.city_code,
        bbox=args.bbox,
        osm_pbf=args.osm_pbf,
        output_dir=args.output_dir,
        building_filter_size=args.building_filter_size,
        building_simplification=args.building_simplification,
        include_ocean=args.include_ocean
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
