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
    city_code: str,
    lod_peripheral_roads: str = "standard",
    include_pedestrian_paths: bool = False,
    lod_peripheral_labels: str = "none"
) -> str:
    """
    Subway Builder México: Urban Core AOI LOD Filter.
    Aplica zonificación concéntrica:
    - Conserva todas las calles residenciales, andadores y detalles urbanos DENTRO de urban_core_geojson.
    - Conserva autopistas y vías seleccionadas según lod_peripheral_roads en el BBOX exterior.
    - Opcionalmente incluye o descarta andadores peatonales según include_pedestrian_paths.
    - Elimina o modula calles menores y etiquetas fuera del núcleo para acelerar Planetiler y WebGL.
    """
    if not os.path.exists(input_pbf) or not os.path.exists(urban_core_geojson):
        return input_pbf

    osmium_bin = shutil.which("osmium")
    if not osmium_bin:
        print("-> [LOD] osmium no disponible; usando PBF original.")
        return input_pbf

    try:
        t0 = time.time()
        print(f"-> [LOD] Aplicando filtro de Zonificación Concéntrica (Urban Core AOI - Vías: {lod_peripheral_roads})...")
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

        # 2. Filtrar vias urbanas y etiquetas de lugares estrictamente dentro del nucleo
        core_filtered_pbf = os.path.join(build_dir, f"{city_code.lower()}_core_filtered.osm.pbf")
        core_highways = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link,unclassified,residential,living_street,service"
        if include_pedestrian_paths:
            core_highways += ",pedestrian,footway,cycleway,path"

        res_core = subprocess.run([
            osmium_bin, "tags-filter",
            core_pbf,
            core_highways,
            "n/place=city,town,suburb,neighbourhood,quarter,village,hamlet",
            "--overwrite",
            "-o", core_filtered_pbf
        ], capture_output=True, text=True)

        # 3. Filtrar red troncal y terreno para TODO el BBOX exterior según lod_peripheral_roads
        bg_pbf = os.path.join(build_dir, f"{city_code.lower()}_bg_arterials.osm.pbf")
        if lod_peripheral_roads == "ultralight":
            bg_highways = "w/highway=motorway,motorway_link,trunk,trunk_link"
        elif lod_peripheral_roads == "detailed":
            bg_highways = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link,tertiary,tertiary_link"
        else: # standard
            bg_highways = "w/highway=motorway,motorway_link,trunk,trunk_link,primary,primary_link,secondary,secondary_link"

        tags_filter_cmd = [
            osmium_bin, "tags-filter",
            input_pbf,
            bg_highways,
            "wr/natural=*", "wr/waterway=*", "wr/landuse=*", "wr/landcover=*",
            "wr/leisure=*", "wr/amenity=*", "wr/aeroway=*", "wr/boundary=*",
            "r/type=multipolygon", "r/type=boundary"
        ]
        if lod_peripheral_labels == "cities_only":
            tags_filter_cmd.append("n/place=city")
        elif lod_peripheral_labels == "all":
            tags_filter_cmd.append("n/place=*")

        tags_filter_cmd.extend(["--overwrite", "-o", bg_pbf])
        res_bg = subprocess.run(tags_filter_cmd, capture_output=True, text=True)


        if res_core.returncode != 0 or res_bg.returncode != 0 or not os.path.exists(core_filtered_pbf) or not os.path.exists(bg_pbf):
            print("   [WARN] osmium tags-filter falló. Usando PBF base.")
            return input_pbf

        # 4. Fusionar troncales exteriores + red urbana completa del núcleo
        optimized_pbf = os.path.join(build_dir, f"{city_code.lower()}_lod_optimized.osm.pbf")
        res_merge = subprocess.run([
            osmium_bin, "merge",
            bg_pbf, core_filtered_pbf,
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
    urban_core_geojson: Optional[str] = None,
    lod_peripheral_roads: str = "standard",
    include_pedestrian_paths: bool = False,
    lod_peripheral_labels: str = "none",
    lod_peripheral_buildings: str = "none",
    denue_csv: Optional[str] = None
) -> int:
    if urban_parks_only:
        os.environ["SB_URBAN_PARKS_ONLY"] = "1"
        print("-> [OPCIÓN] Filtrado de parques urbanos activo: omitiendo macro-selvas y bosques rurales.")
    else:
        os.environ["SB_URBAN_PARKS_ONLY"] = "0"

    os.environ["SB_LOD_PERIPHERAL_LABELS"] = str(lod_peripheral_labels).lower()
    os.environ["SB_LOD_PERIPHERAL_BUILDINGS"] = str(lod_peripheral_buildings).lower()

    if urban_core_geojson and os.path.exists(urban_core_geojson):
        os.environ["SB_URBAN_CORE_GEOJSON"] = os.path.abspath(urban_core_geojson)
        print(f"-> [OPCIÓN] Polígono de detalle urbano (LOD) activo: {urban_core_geojson}")
        print(f"   • Vías periferia: {lod_peripheral_roads} | Andadores núcleo: {include_pedestrian_paths} | Etiquetas: {lod_peripheral_labels} | Edificios: {lod_peripheral_buildings}")
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
        # Limpiar teselas temporales, pmtiles parciales y archivos intermedios residuales recursivamente
        for pat in ["*.mbtiles", "*.pmtiles", "*.pbf.tmp", "roads.pbf", "runways_taxiways.pbf", "places.osm.pbf", "*-nobuildings.osm.pbf", "*-merged-source.osm.pbf"]:
            for stale in glob.glob(os.path.join(native_build_dir, "**", pat), recursive=True):
                if stale.endswith(("_clipped.osm.pbf", "_lod_optimized.osm.pbf", f"{city_code.lower()}.osm.pbf")):
                    continue
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
        effective_pbf = apply_urban_lod_filtering(
            effective_pbf, bbox, urban_core_geojson, native_build_dir, city_code,
            lod_peripheral_roads=lod_peripheral_roads,
            include_pedestrian_paths=include_pedestrian_paths,
            lod_peripheral_labels=lod_peripheral_labels
        )

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
        cache_valid = False
        if os.path.exists(ocean_cache_main) and os.path.exists(ocean_cache_contours):
            try:
                import gzip
                with gzip.open(ocean_cache_main, "rt", encoding="utf-8") as f:
                    c_data = json.load(f)
                c_bbox = c_data.get("bbox", [])
                if len(c_bbox) == 4 and all(abs(c_bbox[i] - bbox[i]) < 1e-3 for i in range(4)):
                    cache_valid = True
                else:
                    print(f"-> [Caché] BBOX cartográfico cambió (guardado: {c_bbox}, actual: {bbox}).")
                    print("   -> Invalidando caché de batimetría para recalcular el océano completo.")
            except Exception as ce:
                print(f"-> [Caché] Error validando caché de batimetría ({ce}). Se recalculará.")

        if cache_valid:
            print("-> [Caché] Restaurando batimetría previamente calculada para reutilización inmediata (0s)...")
            shutil.copyfile(ocean_cache_main, os.path.join(build_output_dir, "ocean_depth_index.json.gz"))
            shutil.copyfile(ocean_cache_contours, os.path.join(build_output_dir, "ocean_depth_index_contours.json.gz"))
        else:
            for stale_f in ["ocean_depth_index.json.gz", "ocean_depth_index_contours.json.gz"]:
                for stale_p in glob.glob(os.path.join(native_build_dir, "**", stale_f), recursive=True):
                    try:
                        os.remove(stale_p)
                    except OSError:
                        pass

    # Sincronización y validación preventiva de caché de edificios 3D (Overture Maps)
    buildings_meta_file = os.path.join(native_build_dir, ".buildings_bbox.json")
    buildings_cache_valid = False
    existing_pkls = glob.glob(os.path.join(native_build_dir, "**", "buildings.pkl"), recursive=True)

    if existing_pkls:
        if os.path.exists(buildings_meta_file):
            try:
                with open(buildings_meta_file, "r", encoding="utf-8") as bf:
                    b_meta = json.load(bf)
                b_bbox = b_meta.get("bbox", [])
                if len(b_bbox) == 4 and all(abs(b_bbox[i] - bbox[i]) < 1e-3 for i in range(4)):
                    buildings_cache_valid = True
                else:
                    print(f"-> [Caché Edificios 3D] BBOX cartográfico cambió (guardado: {b_bbox}, actual: {bbox}).")
                    print("   -> Invalidando caché de edificios 3D para consultar Overture Maps en el BBOX completo.")
            except Exception as be:
                print(f"-> [Caché Edificios 3D] Error validando metadatos ({be}). Se recalculará.")
        else:
            print("-> [Caché Edificios 3D] Sin registro previo de BBOX para edificios 3D. Verificando extensión espacial...")
            try:
                import pandas as pd
                import geopandas as gpd
                df_chk = pd.read_pickle(existing_pkls[0])
                if not df_chk.empty:
                    tb = gpd.GeoSeries(df_chk['geometry']).total_bounds
                    if (bbox[0] < tb[0] - 0.03) or (bbox[1] < tb[1] - 0.03) or (bbox[2] > tb[2] + 0.03) or (bbox[3] > tb[3] + 0.03):
                        print(f"   -> Extensión actual {bbox} excede la huella de edificios guardada {tb.tolist()}. Invalidando.")
                        buildings_cache_valid = False
                    else:
                        buildings_cache_valid = True
            except Exception:
                buildings_cache_valid = False

        if not buildings_cache_valid:
            stale_building_patterns = [
                "buildings.pkl",
                "buildings.geojson",
                "buildings_cleaned.json",
                "buildings_zoom.geojson",
                "buildings.mbtiles",
                "buildings_foundations.*",
                "buildings_index.*"
            ]
            for b_pat in stale_building_patterns:
                for stale_b in glob.glob(os.path.join(native_build_dir, "**", b_pat), recursive=True):
                    try:
                        os.remove(stale_b)
                    except OSError:
                        pass
        else:
            print("-> [Caché Edificios 3D] Reutilizando edificios 3D previamente descargados (BBOX verificado).")

    cores, ram_mb = get_optimal_hardware_resources()
    # MapGen expects RAM in Gigabytes (its setter converts GB to MB: self._RAM = int(RAM * 1000))
    ram_gb = max(2.0, round(ram_mb / 1000.0, 1))

    # Enriquecimiento automático de toponimia urbana desde DENUE si está disponible
    additional_neighborhoods_geojson = None
    if not denue_csv:
        # Intentar auto-descubrimiento en work_dir o data/<city>
        candidates = glob.glob(os.path.join(work_dir, "denue_*.csv"))
        if not candidates:
            candidates = glob.glob(os.path.join(REPO_ROOT, "data", "**", "denue_*.csv"), recursive=True)
            city_cands = [c for c in candidates if city_code.lower() in c.lower()]
            if city_cands:
                candidates = city_cands
        if candidates:
            denue_csv = candidates[0]

    if denue_csv and os.path.exists(denue_csv):
        try:
            from sb_mexico.toponymy import generate_denue_neighborhoods_geojson
            denue_out_geojson = os.path.join(native_build_dir, f"{city_code.lower()}_denue_neighborhoods.geojson")
            res_geo = generate_denue_neighborhoods_geojson(
                denue_path=denue_csv,
                bbox=bbox,
                output_geojson=denue_out_geojson,
                urban_core_geojson=urban_core_geojson,
                min_count=15
            )
            if res_geo and os.path.exists(res_geo):
                additional_neighborhoods_geojson = res_geo
                print(f"-> [Toponimia DENUE] Inyectando colonias y fraccionamientos adicionales desde {denue_csv}")
        except Exception as te:
            print(f"  [WARN] No se pudo generar toponimia complementaria de DENUE: {te}")

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
            neighborhoods_additional=additional_neighborhoods_geojson,
            label_name_language="prefer:es",
            road_name_preferred_language="es",
            building_index_filter_size=building_filter_size,
            building_index_simplification=building_simplification,
            building_tile_simplification=building_simplification,
            create_building_foundations=False,
            create_ocean_foundations=include_ocean,
            redownload_buildings=not buildings_cache_valid
        )

        print("-> Ejecutando extracción de geometrías, vialidades, toponimia y edificios 3D...")
        m.run_all()

        # Guardar metadatos de BBOX de edificios 3D tras compilación exitosa
        try:
            with open(buildings_meta_file, "w", encoding="utf-8") as bf:
                json.dump({"bbox": bbox}, bf, indent=2)
        except Exception:
            pass
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
    parser.add_argument("--lod-peripheral-roads", default="standard", choices=["ultralight", "standard", "detailed"], help="Jerarquía de vías en periferia")
    parser.add_argument("--include-pedestrian-paths", action="store_true", default=False, help="Incluir andadores y senderos dentro del núcleo")
    parser.add_argument("--lod-peripheral-labels", default="none", choices=["none", "cities_only", "all"], help="Etiquetas toponímicas en periferia")
    parser.add_argument("--lod-peripheral-buildings", default="none", choices=["none", "large_only", "all"], help="Edificios 3D en periferia")
    parser.add_argument("--denue-csv", default=None, help="Ruta al archivo DENUE CSV para toponimia complementaria")

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
        urban_core_geojson=args.urban_core_geojson,
        lod_peripheral_roads=args.lod_peripheral_roads,
        include_pedestrian_paths=args.include_pedestrian_paths,
        lod_peripheral_labels=args.lod_peripheral_labels,
        lod_peripheral_buildings=args.lod_peripheral_buildings,
        denue_csv=args.denue_csv
    )
    sys.exit(ret)



if __name__ == "__main__":
    main()
