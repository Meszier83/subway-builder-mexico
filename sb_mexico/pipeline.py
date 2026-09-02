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
from sb_mexico.gravity import build_demand_grid, simulate_gravity_demand, sanitize_demand_points
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
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"No se encontró el archivo de configuración en '{config_path}'")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Validaciones mínimas requeridas
    required_keys = ["city", "macroeconomics"]
    for k in required_keys:
        if k not in config:
            raise KeyError(f"El archivo de configuración carece de la sección obligatoria '{k}'.")

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

    city_code = city_info["code"]
    city_base = os.path.splitext(os.path.basename(config_path))[0].lower()
    bbox_list = city_info["bbox"]  # [min_lon, min_lat, max_lon, max_lat]
    bbox_dict = {
        "min_lon": bbox_list[0],
        "min_lat": bbox_list[1],
        "max_lon": bbox_list[2],
        "max_lat": bbox_list[3]
    }

    out_dir = os.path.abspath(output_dir)
    os.makedirs(out_dir, exist_ok=True)

    # Resolución universal de directorios de búsqueda de datos
    search_dirs = []
    if data_dir:
        search_dirs.append(os.path.abspath(data_dir))

    candidate_subdirs = [
        os.path.join(ROOT_DIR, "data", city_base),
        os.path.join(ROOT_DIR, "data", city_code.lower()),
        os.path.join(ROOT_DIR, "data"),
        out_dir,
        ROOT_DIR
    ]
    for d in candidate_subdirs:
        if os.path.exists(d) and d not in search_dirs:
            search_dirs.append(d)

    data_parent = os.path.join(ROOT_DIR, "data")
    if os.path.exists(data_parent):
        for entry in os.scandir(data_parent):
            if entry.is_dir() and entry.path not in search_dirs:
                search_dirs.append(entry.path)

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
        "*conapo*.csv",
        "data-*.csv",
        "*proyeccion*.csv"
    ])

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

    # Imprimir tabla de calibración
    tabla_calib = Table(title=f"Calibración de Empleo Municipal ({city_code})")
    tabla_calib.add_column("Cve", style="cyan")
    tabla_calib.add_column("Municipio", style="white")
    tabla_calib.add_column("DENUE Base", justify="right", style="yellow")
    tabla_calib.add_column("H001A (CE24)", justify="right", style="green")
    tabla_calib.add_column("Factor Micro", justify="right", style="bold")
    tabla_calib.add_column("Estado", style="magenta")

    for cve, data in audit_calib.items():
        tabla_calib.add_row(
            cve,
            data.get("nombre", "-"),
            f"{int(data['jobs_formal']):,}",
            f"{int(data['h001a']):,}" if data.get('h001a') else "-",
            f"{data['factor']:.3f}",
            data["status"]
        )
    console.print(tabla_calib)

    # D. Carga y Georreferenciación CPV 2020 con Proyecciones CONAPO
    if not cpv_files:
        raise FileNotFoundError("No se encontró archivo de Censo CPV 2020 (*RESAGEBURB*.csv o *censo*).")

    growth_factors = macro.get("growth_factors", {}).copy()
    if conapo_files:
        conapo_projs = parse_conapo_projections(conapo_files[0])
        if conapo_projs:
            console.print(f"-> Proyecciones CONAPO cargadas automáticamente: [green]{len(conapo_projs)}[/green] municipios ({os.path.basename(conapo_files[0])}).")

    df_cpv = load_cpv_demography(
        cpv_paths=cpv_files,
        df_denue=df_denue,
        bbox=bbox_dict,
        tasa_pea=tasa_pea,
        growth_factors=growth_factors,
        default_growth=macro.get("default_growth_factor", 1.0)
    )
    console.print(f"-> Censo CPV cargado y georreferenciado: [cyan]{len(df_cpv):,}[/cyan] manzanas habitadas.")

    # =========================================================================
    # 3. MALLA ESPACIAL, SNAPPING VIAL Y FUSIÓN DE POIS
    # =========================================================================
    console.print(f"\n[bold yellow]3. Malla Espacial y Snapping Vial[/bold yellow]")

    roads_path = os.path.join(out_dir, "roads.geojson")
    if not os.path.exists(roads_path) and os.path.exists(os.path.join(src_dir, "roads.geojson")):
        roads_path = os.path.join(src_dir, "roads.geojson")
    if not os.path.exists(roads_path):
        raise FileNotFoundError(f"roads.geojson no encontrado en '{out_dir}' ni en '{src_dir}'. Compila el mapa primero.")

    roads_gdf = gpd.read_file(roads_path)

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

    total_pea = sum(p.get("pea_15ymas", 0) for p in demand_points)

    pops = simulate_gravity_demand(
        demand_points=demand_points,
        beta=macro.get("gravity_beta", 0.12),
        max_distance_km=macro.get("max_distance_km", 55.0),
        max_pop_size=macro.get("max_pop_size", 150),
        seed=city_info.get("seed", 42)
    )

    total_viajeros = sum(p["size"] for p in pops)

    console.print(f"-> Cohortes de viaje generadas: [green]{len(pops):,}[/green] pops.")
    console.print(f"-> Total de Pasajeros Activos: [bold green]{total_viajeros:,}[/bold green] (PEA Total: {total_pea:,})")

    # Aserción de conservación estricta de masa
    assert total_viajeros == total_pea, f"Inconsistencia de masa: {total_viajeros} viajeros vs {total_pea} PEA"

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

    # Validación estricta de artefactos obligatorios
    mandatory_files = ["config.json", "demand_data.json", f"{city_code}.pmtiles", "roads.geojson"]
    missing_mandatory = [f for f in mandatory_files if not os.path.exists(os.path.join(out_dir, f))]
    if missing_mandatory:
        raise RuntimeError(
            f"No se puede generar el archivo {zip_name}. Faltan artefactos obligatorios en '{out_dir}': "
            f"{', '.join(missing_mandatory)}"
        )

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
