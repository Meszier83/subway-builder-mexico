"""
sb_mexico.pipeline
==================
Orquestador principal del proceso de generación de mapas y demanda.
Integra las fuentes del INEGI, el modelo gravitatorio y la exportación/empaquetado
con validaciones automáticas de integridad.
"""

import os
import glob
import json
import zipfile
import yaml
import numpy as np
import geopandas as gpd
from typing import Dict, Any, Optional, List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from sb_mexico.inegi import (
    load_denue,
    load_cpv_demography,
    calibrate_denue_employment,
    parse_enoe_indicators,
    parse_ce2024_municipal,
    parse_conapo_projections
)
from sb_mexico.gravity import (
    build_demand_grid,
    simulate_gravity_demand,
    sanitize_demand_points,
    assign_zones,
    build_arterial_road_network,
    ArterialRoadIndex
)
from sb_mexico.cartography import build_city_map
from sb_mexico.special_demand import (
    generate_special_demand_points_doc,
    validate_special_demand_points,
    save_special_demand_points
)

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
console = Console()


def _dedup_glob(patterns: List[str]) -> List[str]:
    """Expande y desduplica rutas de archivos existentes."""
    seen = set()
    result = []
    for pat in patterns:
        for f in glob.glob(pat):
            abs_f = os.path.abspath(f)
            if abs_f not in seen and os.path.isfile(abs_f):
                seen.add(abs_f)
                result.append(abs_f)
    return result


def load_city_config(config_path: str) -> Dict[str, Any]:
    """Carga y valida el archivo YAML de configuración de la ciudad."""
    resolved = config_path
    if not os.path.isabs(resolved):
        candidates = [
            os.path.abspath(resolved),
            os.path.abspath(os.path.join(ROOT_DIR, resolved)),
            os.path.abspath(os.path.join(ROOT_DIR, "cities", os.path.basename(resolved)))
        ]
        for c in candidates:
            if os.path.exists(c):
                resolved = c
                break

    if not os.path.exists(resolved):
        raise FileNotFoundError(f"No se encontró el archivo de configuración en '{config_path}' (buscado en: '{resolved}')")

    with open(resolved, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Validaciones mínimas requeridas
    required_keys = ["city", "macroeconomics"]
    for k in required_keys:
        if k not in config:
            raise KeyError(f"El archivo de configuración carece de la sección obligatoria '{k}'.")

    # Normalizar BBOX para prevenir coordenadas invertidas
    if "city" in config and "bbox" in config["city"]:
        raw_b = config["city"]["bbox"]
        if isinstance(raw_b, (list, tuple)) and len(raw_b) == 4:
            try:
                config["city"]["bbox"] = [
                    round(min(float(raw_b[0]), float(raw_b[2])), 4),
                    round(min(float(raw_b[1]), float(raw_b[3])), 4),
                    round(max(float(raw_b[0]), float(raw_b[2])), 4),
                    round(max(float(raw_b[1]), float(raw_b[3])), 4)
                ]
            except (ValueError, TypeError):
                pass

    return config


def execute_pipeline(
    config_path: str,
    skip_map: bool = False,
    output_dir: str = ".",
    data_dir: Optional[str] = None
) -> str:
    """
    Ejecuta el pipeline completo de principio a fin de manera determinista y autovalidada.
    """
    console.print(Panel.fit("[bold green]SUBWAY BUILDER MÉXICO v6.3[/bold green]\n[cyan]Pipeline Integral y Autovalidado[/cyan]"))

    cfg = load_city_config(config_path)
    city_info = cfg["city"]
    macro = cfg["macroeconomics"]
    pois_cfg = cfg.get("pois") or []
    poi_ids = [p.get("id") for p in pois_cfg if isinstance(p, dict) and "id" in p]
    from collections import Counter
    poi_counts = Counter(poi_ids)
    dup_ids = [pid for pid, count in poi_counts.items() if count > 1]
    if dup_ids:
        raise ValueError(
            f"Error de integridad en configuración de POIs: Se detectaron IDs duplicados: {dup_ids}. "
            "Cada POI debe tener un ID único para evitar colisiones y pérdida de nodos en Subway Builder."
        )

    city_code = city_info["code"]
    city_base = os.path.splitext(os.path.basename(config_path))[0].lower()
    bbox_list = city_info["bbox"]  # [min_lon, min_lat, max_lon, max_lat]
    bbox_dict = {
        "min_lon": bbox_list[0],
        "min_lat": bbox_list[1],
        "max_lon": bbox_list[2],
        "max_lat": bbox_list[3]
    }

    # Garantizar salida aislada en dist/<ciudad> si no se especifica una ruta dedicada
    if output_dir in (".", ROOT_DIR, ""):
        out_dir = os.path.join(ROOT_DIR, "dist", city_base)
    else:
        out_dir = os.path.abspath(output_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Resolución estrictamente aislada de fuentes de datos por proyecto
    search_dirs = []

    # 1. Carpeta específica del proyecto
    project_dir = None
    if data_dir:
        project_dir = os.path.abspath(data_dir)
    elif cfg.get("data_dir"):
        c_dir = cfg.get("data_dir")
        project_dir = c_dir if os.path.isabs(c_dir) else os.path.join(ROOT_DIR, c_dir)
    else:
        cand_base = os.path.join(ROOT_DIR, "data", city_base)
        cand_code = os.path.join(ROOT_DIR, "data", city_code.lower())
        if os.path.exists(cand_base):
            project_dir = cand_base
        elif os.path.exists(cand_code):
            project_dir = cand_code

    if project_dir and os.path.exists(project_dir):
        search_dirs.append(project_dir)

    # 2. Raíz de data/ reservada exclusivamente para datasets nacionales (OSM PBF y CONAPO)
    national_data_dir = os.path.join(ROOT_DIR, "data")
    if os.path.exists(national_data_dir) and national_data_dir not in search_dirs:
        search_dirs.append(national_data_dir)

    def find_sources(patterns: List[str]) -> List[str]:
        candidates = []
        for sdir in search_dirs:
            for pat in patterns:
                candidates.append(os.path.join(sdir, pat))
        return _dedup_glob(candidates)

    src_dir = search_dirs[0] if search_dirs else ROOT_DIR

    # =========================================================================
    # 1. COMPILACIÓN CARTOGRÁFICA (SI NO SE OMITE)
    # =========================================================================
    if not skip_map:
        console.print(f"\n[bold yellow]1. Compilación Cartográfica ({city_code})[/bold yellow]")
        pbf_candidates = find_sources(["*.osm.pbf"])
        osm_pbf = pbf_candidates[0] if pbf_candidates else None
        build_city_map(
            city_code=city_code,
            bbox=bbox_list,
            osm_pbf_path=osm_pbf,
            building_filter_size=city_info.get("building_filter_size", 15.0),
            building_simplification=city_info.get("building_simplification", 0.2),
            include_ocean=city_info.get("include_ocean", False),
            places=cfg.get("places", []),
            output_dir=out_dir
        )
    else:
        console.print(f"\n[dim]1. Compilación Cartográfica omitida por parámetro.[/dim]")

    # =========================================================================
    # 2. INGESTA ESTADÍSTICA DE LA CUATRIFECTA INEGI
    # =========================================================================
    console.print(f"\n[bold yellow]2. Ingesta y Calibración INEGI[/bold yellow]")

    # Detección universal automática en todas las ubicaciones candidatas
    denue_files = find_sources(["*denue*.csv", "*DENUE*.csv"])
    cpv_files = find_sources([
        "*RESAGEBURB*.csv",
        "*resageburb*.csv",
        "*censo*.csv",
        "*censo*.xlsx",
        "*cpv*.csv"
    ])
    ce_files = find_sources([
        "*SAIC*.csv",
        "*saic*.csv",
        "*exporta*.csv",
        "*cenu24*.csv",
        "*tr_ce*.csv",
        "*ce_*.csv",
        "*ce2024*.csv"
    ])
    enoe_files = find_sources([
        "*2026_trim*.csv",
        "*2024_trim*.csv",
        "*2025_trim*.csv",
        "*trim*.csv",
        "*enoe*.csv"
    ])
    conapo_files = find_sources([
        "*pobproy*.csv",
        "*quinq*.csv",
        "*pob_proy*.csv",
        "*conapo*.csv",
        "data-*.csv",
        "*proyeccion*.csv"
    ])
    marco_files = find_sources([
        "*mza*.shp", "*mza*.geojson", "*mza*.gpkg",
        "*ageb*.shp", "*ageb*.geojson", "*ageb*.gpkg",
        "*manzana*.shp", "*manzana*.geojson"
    ])
    if marco_files:
        console.print(f"-> Capas de Marco Geoestadístico detectadas: [green]{len(marco_files)}[/green] archivos.")

    # A. Macroeconomía (ENOE)
    tasa_pea = macro.get("tasa_pea")
    til_1 = macro.get("til_1_state")
    if (tasa_pea is None or til_1 is None) and enoe_files:
        enoe_data = parse_enoe_indicators(enoe_files[0])
        tasa_pea = tasa_pea or enoe_data["tasa_pea"]
        til_1 = til_1 or enoe_data["til_1"]

    tasa_pea = tasa_pea or 0.62
    til_1 = til_1 or 0.45
    console.print(f"-> Parámetros Macro: Tasa PEA = [green]{tasa_pea:.2%}[/green] | TIL1 (Informalidad) = [green]{til_1:.2%}[/green]")

    # B. Censo Económico 2024
    ce_benchmarks = macro.get("ce_2024_benchmarks", {})
    if not ce_benchmarks and ce_files:
        for cf in ce_files:
            parsed = parse_ce2024_municipal(cf)
            if parsed:
                ce_benchmarks = parsed
                break
        console.print(f"-> Benchmarks CE 2024 cargados automáticamente para [green]{len(ce_benchmarks)}[/green] municipios.")

    # C. Carga y Calibración DENUE
    if not denue_files:
        raise FileNotFoundError("No se encontró archivo de DENUE (*denue*.csv).")
    df_denue_raw = load_denue(denue_files, bbox_dict)
    console.print(f"-> DENUE cargado: [cyan]{len(df_denue_raw):,}[/cyan] establecimientos en BBOX.")

    df_denue, audit_calib = calibrate_denue_employment(
        df_denue=df_denue_raw,
        ce_benchmarks=ce_benchmarks,
        til_1=til_1,
        min_sample_threshold=macro.get("sample_threshold", 500)
    )

    # Imprimir tabla de calibración con desglose territorial BBOX
    tabla_calib = Table(title=f"Calibración de Empleo Municipal ({city_code})")
    tabla_calib.add_column("Cve", style="cyan")
    tabla_calib.add_column("Municipio", style="white")
    tabla_calib.add_column("DENUE Base", justify="right", style="yellow")
    tabla_calib.add_column("BBOX %", justify="right", style="blue")
    tabla_calib.add_column("H001A (BBOX)", justify="right", style="green")
    tabla_calib.add_column("Factor Micro", justify="right", style="bold")
    tabla_calib.add_column("Estado", style="magenta")

    for cve, data in audit_calib.items():
        share_str = f"{data.get('share_bbox', 1.0):.1%}"
        h001a_val = data.get('h001a')
        tabla_calib.add_row(
            cve,
            data.get("nombre", "-"),
            f"{int(data['jobs_formal']):,}",
            share_str,
            f"{int(h001a_val):,}" if h001a_val else "-",
            f"{data['factor']:.3f}",
            data["status"]
        )
    console.print(tabla_calib)

    # D. Carga y Georreferenciación CPV 2020 con Proyecciones CONAPO
    if not cpv_files:
        raise FileNotFoundError("No se encontró archivo de Censo CPV 2020 (*RESAGEBURB*.csv o *censo*).")

    growth_factors = macro.get("growth_factors", {}).copy()
    conapo_projs = None
    if conapo_files:
        conapo_projs = parse_conapo_projections(conapo_files[0], as_growth_factors=True)
        if conapo_projs:
            console.print(f"-> Proyecciones CONAPO cargadas automáticamente: [green]{len(conapo_projs)}[/green] municipios ({os.path.basename(conapo_files[0])}).")

    df_cpv = load_cpv_demography(
        cpv_paths=cpv_files,
        df_denue=df_denue,
        bbox=bbox_dict,
        tasa_pea=tasa_pea,
        growth_factors=growth_factors,
        conapo_projections=conapo_projs,
        default_growth=macro.get("default_growth_factor", 1.0),
        marco_paths=marco_files if marco_files else None
    )
    total_cpv_pop = float(df_cpv['pobtot_adj'].sum()) if len(df_cpv) > 0 else 0.0
    total_cpv_pea = float(df_cpv['pea_real'].sum()) if len(df_cpv) > 0 else 0.0
    console.print(f"-> Censo CPV cargado y georreferenciado: [cyan]{len(df_cpv):,}[/cyan] manzanas habitadas | [bold green]{int(total_cpv_pop):,}[/bold green] hab. proyectados (PEA base: [bold green]{int(total_cpv_pea):,}[/bold green]).")

    # =========================================================================
    # 3. MALLA ESPACIAL, SNAPPING VIAL Y FUSIÓN DE POIS
    # =========================================================================
    console.print(f"\n[bold yellow]3. Malla Espacial y Snapping Vial[/bold yellow]")

    roads_path = os.path.join(out_dir, "roads.geojson")
    if not os.path.exists(roads_path) and os.path.exists(os.path.join(src_dir, "roads.geojson")):
        roads_path = os.path.join(src_dir, "roads.geojson")

    road_index = None
    if os.path.exists(roads_path):
        roads_gdf = gpd.read_file(roads_path)
        console.print(f"-> Snapping vial activado: [cyan]{len(roads_gdf):,}[/cyan] segmentos de vía ({os.path.basename(roads_path)}).")
        try:
            max_detour_ratio = float(macro.get("max_detour_ratio", 3.5))
            road_index = build_arterial_road_network(roads_gdf, max_detour_ratio=max_detour_ratio)
            if road_index is not None:
                console.print(f"-> Grafo arterial topológico construido: [green]{len(road_index.all_nodes_coords):,}[/green] nodos viales activos (Techo desvío: {max_detour_ratio}x).")
        except Exception as e:
            console.print(f"[yellow]-> Advertencia construyendo grafo arterial ({e}). Se aplicará modelo continuo base.[/yellow]")
            road_index = None
    else:
        roads_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        console.print("[yellow]-> roads.geojson no encontrado. La malla de demanda se posicionará en los centroides urbanos sin snapping vial.[/yellow]")

    grid_size = city_info.get("grid_size", 0.0025)
    demand_points, poi_audit = build_demand_grid(
        df_denue=df_denue,
        df_cpv=df_cpv,
        special_pois=pois_cfg,
        roads_gdf=roads_gdf,
        grid_size=grid_size,
        min_residents=city_info.get("min_residents", 10),
        min_jobs=city_info.get("min_jobs", 3),
        seed=city_info.get("seed", 42)
    )

    console.print(f"-> Nodos de demanda consolidados: [green]{len(demand_points):,}[/green]")

    if poi_audit:
        tabla_poi = Table(title=f"Auditoría de POIs Especiales ({city_code})")
        tabla_poi.add_column("ID", style="cyan")
        tabla_poi.add_column("Modo", style="magenta")
        tabla_poi.add_column("Manual", justify="right", style="yellow")
        tabla_poi.add_column("DENUE Absorbido", justify="right", style="blue")
        tabla_poi.add_column("Final Asignado", justify="right", style="bold green")
        tabla_poi.add_column("Diagnóstico", style="white")
        for p in poi_audit:
            tabla_poi.add_row(p["id"], p["mode"], f"{p['manual']:,}", f"{p['absorbed']:,}", f"{p['final_jobs']:,}", p["status"])
        console.print(tabla_poi)

    # =========================================================================
    # 4. MODELO GRAVITATORIO Y GENERACIÓN DE COHORTES (MULTINOMIAL)
    # =========================================================================
    console.print(f"\n[bold yellow]4. Modelo Gravitatorio y Generación de Cohortes (Multinomial)[/bold yellow]")

    isolated_zones = cfg.get("isolated_zones", cfg.get("city", {}).get("isolated_zones", []))
    if isolated_zones:
        console.print(f"-> Zonas topológicas aisladas detectadas: [cyan]{len(isolated_zones)}[/cyan] zonas.")
    console.print("-> Motor Gravitatorio Doblemente Acotado (Furness / IPFP): [green]Habilitado[/green]")

    total_pea = sum(p.get("pea_15ymas", 0) for p in demand_points)

    target_pop_size = macro.get("target_pop_size")
    if target_pop_size is None:
        target_pop_size = max(35, int(round(total_pea / 18000.0)))
        console.print(f"-> Calibración adaptativa de cohortes: [cyan]target_pop_size = {target_pop_size}[/cyan] (PEA: {total_pea:,})")

    pops = simulate_gravity_demand(
        demand_points=demand_points,
        beta=macro.get("gravity_beta", 0.12),
        max_distance_km=macro.get("max_distance_km", 55.0),
        max_pop_size=macro.get("max_pop_size", 150),
        target_pop_size=target_pop_size,
        seed=city_info.get("seed", 42),
        isolated_zones=isolated_zones,
        furness_iterations=macro.get("furness_iterations", 15),
        furness_tol=macro.get("furness_tol", 0.02),
        road_index=road_index
    )

    total_viajeros = sum(p["size"] for p in pops)

    console.print(f"-> Cohortes de viaje generadas: [green]{len(pops):,}[/green] pops.")
    console.print(f"-> Total de Pasajeros Activos: [bold green]{total_viajeros:,}[/bold green] (PEA Total: {total_pea:,})")

    # Aserción de conservación estricta de masa
    assert total_viajeros == total_pea, f"Inconsistencia de masa: {total_viajeros} viajeros vs {total_pea} PEA"

    # Desglose Tabular por Masa Territorial y Zonas Aisladas
    if isolated_zones:
        coords_arr = np.array([p["location"] for p in demand_points], dtype=np.float64)
        dp_zones = assign_zones(coords_arr, isolated_zones)
        zone_names = {0: "Continente / Base"}
        for idx, z in enumerate(isolated_zones, start=1):
            zone_names[idx] = z.get("name", z.get("id", f"Zona {idx}"))

        tabla_zonas = Table(title="Auditoría de Demanda por Masa Territorial / Aislamiento", header_style="bold magenta")
        tabla_zonas.add_column("Masa / Zona", style="cyan")
        tabla_zonas.add_column("Puntos", justify="right", style="white")
        tabla_zonas.add_column("Residentes", justify="right", style="blue")
        tabla_zonas.add_column("PEA Activa", justify="right", style="yellow")
        tabla_zonas.add_column("Empleos", justify="right", style="green")
        tabla_zonas.add_column("Viajeros", justify="right", style="bold green")
        tabla_zonas.add_column("Cohortes", justify="right", style="dim white")
        tabla_zonas.add_column("Balance Masa", justify="center", style="bold")

        dp_id_to_zone = {p["id"]: dp_zones[idx] for idx, p in enumerate(demand_points)}

        for z_idx in sorted(zone_names.keys()):
            pts_in_zone = [p for idx, p in enumerate(demand_points) if dp_zones[idx] == z_idx]
            if not pts_in_zone:
                continue
            z_res = sum(p.get("residents", 0) for p in pts_in_zone)
            z_pea = sum(p.get("pea_15ymas", 0) for p in pts_in_zone)
            z_jobs = sum(p.get("jobs", 0) for p in pts_in_zone)
            z_viajeros = sum(p["size"] for p in pops if dp_id_to_zone.get(p["residenceId"]) == z_idx)
            z_cohortes = sum(1 for p in pops if dp_id_to_zone.get(p["residenceId"]) == z_idx)
            bal_str = "[green]Δ = 0[/green]" if z_viajeros == z_pea else f"[red]Δ = {z_viajeros - z_pea}[/red]"

            tabla_zonas.add_row(
                zone_names[z_idx],
                f"{len(pts_in_zone):,}",
                f"{z_res:,}",
                f"{z_pea:,}",
                f"{z_jobs:,}",
                f"{z_viajeros:,}",
                f"{z_cohortes:,}",
                bal_str
            )
        console.print(tabla_zonas)

    # =========================================================================
    # 5. SANITIZACIÓN NATIVA CON DEPOT Y EXPORTACIÓN
    # =========================================================================
    console.print(f"\n[bold yellow]5. Sanitización y Generación de Archivos[/bold yellow]")

    # Cálculo del Baricentro Urbano Ponderado por Actividad Humana (Cámara)
    total_mass = sum(p["residents"] + 1.5 * p["jobs"] for p in demand_points)
    if total_mass > 0:
        center_lon = sum(p["location"][0] * (p["residents"] + 1.5 * p["jobs"]) for p in demand_points) / total_mass
        center_lat = sum(p["location"][1] * (p["residents"] + 1.5 * p["jobs"]) for p in demand_points) / total_mass
    else:
        center_lon = (bbox_dict["min_lon"] + bbox_dict["max_lon"]) / 2.0
        center_lat = (bbox_dict["min_lat"] + bbox_dict["max_lat"]) / 2.0

    clean_demand_points = sanitize_demand_points(demand_points)
    cfg_out_path = os.path.join(out_dir, "config.json")
    demand_out_path = os.path.join(out_dir, "demand_data.json")

    try:
        from depot.demand import DemandData
        dd = DemandData({"points": clean_demand_points, "pops": pops})
        dd.sanitize()
        # Generar config.json con viewport calculado por depot
        dd.generate_config(
            name=city_info["name"],
            code=city_code,
            description=city_info["description"][:80],
            creator=city_info.get("creator", "Subway Builder México v6.3"),
            version="6.3.0",
            filename=cfg_out_path
        )
        # Asegurar centrado baricéntrico inteligente
        if os.path.exists(cfg_out_path):
            with open(cfg_out_path, "r", encoding="utf-8") as f:
                cfg_json = json.load(f)
            if "initialViewState" not in cfg_json or not isinstance(cfg_json["initialViewState"], dict):
                cfg_json["initialViewState"] = {}
            cfg_json["initialViewState"]["latitude"] = round(center_lat, 5)
            cfg_json["initialViewState"]["longitude"] = round(center_lon, 5)
            cfg_json["initialViewState"]["zoom"] = city_info.get("initial_zoom", 12.0)
            with open(cfg_out_path, "w", encoding="utf-8") as f:
                json.dump(cfg_json, f, indent=2, ensure_ascii=False)

        dd.save(demand_out_path)
        console.print("[green][OK][/green] Sanitización y exportación mediante [bold]depot.demand.DemandData[/bold] exitosa.")
    except Exception as e:
        console.print(f"[yellow]Nota: depot.demand fallback nativo ({e}). Exportando directamente...[/yellow]")
        # Exportación manual de respaldo
        with open(demand_out_path, "w", encoding="utf-8") as f:
            json.dump({"points": clean_demand_points, "pops": pops}, f, separators=(',', ':'))

        config_data = {
            "name": city_info["name"],
            "code": city_code,
            "description": city_info["description"][:80],
            "population": total_viajeros,
            "initialViewState": {
                "zoom": city_info.get("initial_zoom", 12.0),
                "latitude": round(center_lat, 5),
                "longitude": round(center_lon, 5),
                "pitch": 0,
                "bearing": 0
            },
            "creator": city_info.get("creator", "Subway Builder México v6.3"),
            "version": "6.3.0"
        }
        with open(cfg_out_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    # B. Generación y Validación de Metadatos de Demanda Especial (Subway Builder Modded Standard)
    if pois_cfg:
        sp_doc = generate_special_demand_points_doc(
            map_code=city_code,
            special_pois_cfg=pois_cfg,
            demand_points=demand_points
        )
        is_valid, validation_errors = validate_special_demand_points(sp_doc)
        if not is_valid:
            console.print(f"[bold red][WARN] Advertencia de validación en Special Demand Points ({len(validation_errors)} errores):[/bold red]")
            for err in validation_errors:
                console.print(f"  - [red]{err}[/red]")
        else:
            console.print(f"[green][OK][/green] Validación Special Demand Schema: [bold green]OK ({len(sp_doc['points'])} POIs conformes con @subway-builder-modded/special-demand-schemas)[/bold green]")

        sp_out_path = os.path.join(out_dir, "special_demand_points.json")
        save_special_demand_points(sp_doc, sp_out_path)

    # =========================================================================
    # 6. EMPAQUETADO EN ARCHIVO ZIP FINAL
    # =========================================================================
    zip_name = f"{city_code}.zip"
    zip_path = os.path.join(out_dir, zip_name)

    # Validación de artefactos generados
    mandatory_data_files = ["config.json", "demand_data.json"]
    missing_data = [f for f in mandatory_data_files if not os.path.exists(os.path.join(out_dir, f))]
    if missing_data:
        raise RuntimeError(f"Faltan artefactos esenciales de demanda en '{out_dir}': {', '.join(missing_data)}")

    mandatory_map_files = [f"{city_code}.pmtiles", "roads.geojson"]
    missing_map = [f for f in mandatory_map_files if not os.path.exists(os.path.join(out_dir, f))]

    if missing_map:
        console.print(Panel.fit(
            f"[bold green]¡DEMANDA Y CONFIGURACIÓN GENERADAS EXITOSAMENTE AL 100%![/bold green]\n"
            f"Archivos exportados a: [cyan]{out_dir}[/cyan]\n"
            f"• demand_data.json: {len(clean_demand_points):,} nodos | {len(pops):,} cohortes | {total_viajeros:,} viajeros\n"
            f"• config.json: Viewport baricéntrico y metadatos listos para el Visor (Paso 6)\n\n"
            f"[yellow]Nota para el paquete final del juego ({zip_name}):[/yellow]\n"
            f"Para generar el .zip importable a Subway Builder se requieren: {', '.join(missing_map)}.\n"
            f"Puedes compilar la cartografía en WSL/Linux o colocar los archivos generados en la carpeta del proyecto.",
            style="yellow"
        ))
        return demand_out_path

    files_to_pack = [
        "config.json",
        "demand_data.json",
        "special_demand_points.json",
        f"{city_code}.pmtiles",
        "buildings_index.bin.gz",
        "roads.geojson",
        "runways_taxiways.geojson",
        "ocean_depth_index.json.gz"
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fname in files_to_pack:
            fpath = os.path.join(out_dir, fname)
            if os.path.exists(fpath):
                zipf.write(fpath, arcname=fname)
                console.print(f"  + Empaquetado: [dim]{fname}[/dim]")

    console.print(Panel.fit(
        f"[bold green]¡PAQUETE {zip_name} GENERADO CON ÉXITO![/bold green]\n"
        f"Listo para importar en Kronifer's Map Manager / Railyard.",
        style="green"
    ))

    return zip_path
