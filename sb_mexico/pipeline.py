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
from typing import Dict, Any, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from sb_mexico.inegi import (
    load_denue,
    load_cpv_demography,
    calibrate_denue_employment,
    parse_enoe_indicators,
    parse_ce2024_municipal
)
from sb_mexico.gravity import build_demand_grid, simulate_gravity_demand
from sb_mexico.cartography import build_city_map

console = Console()


def load_city_config(config_path: str) -> Dict[str, Any]:
    """Carga y valida el archivo de configuración YAML de la ciudad."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Archivo de configuración no encontrado: {config_path}")

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
    output_dir: str = "."
) -> str:
    """
    Ejecuta el pipeline completo de principio a fin de manera determinista y autovalidada.
    """
    console.print(Panel.fit("[bold green]SUBWAY BUILDER MÉXICO v6.0[/bold green]\n[cyan]Pipeline Integral y Autovalidado[/cyan]"))

    cfg = load_city_config(config_path)
    city_info = cfg["city"]
    macro = cfg["macroeconomics"]
    pois_cfg = cfg.get("pois", [])

    city_code = city_info["code"]
    bbox_list = city_info["bbox"]  # [min_lon, min_lat, max_lon, max_lat]
    bbox_dict = {
        "min_lon": bbox_list[0],
        "min_lat": bbox_list[1],
        "max_lon": bbox_list[2],
        "max_lat": bbox_list[3]
    }

    # =========================================================================
    # 1. COMPILACIÓN CARTOGRÁFICA (SI NO SE OMITE)
    # =========================================================================
    if not skip_map:
        console.print(f"\n[bold yellow]1. Compilación Cartográfica ({city_code})[/bold yellow]")
        build_city_map(
            city_code=city_code,
            bbox=bbox_list,
            building_filter_size=city_info.get("building_filter_size", 15.0),
            building_simplification=city_info.get("building_simplification", 0.2),
            include_ocean=city_info.get("include_ocean", False),
            output_dir=output_dir
        )
    else:
        console.print(f"\n[dim]1. Compilación Cartográfica omitida por parámetro.[/dim]")

    # =========================================================================
    # 2. INGESTA ESTADÍSTICA DE LA CUATRIFECTA INEGI
    # =========================================================================
    console.print(f"\n[bold yellow]2. Ingesta y Calibración INEGI[/bold yellow]")

    # Detección automática o rutas configuradas
    denue_files = glob.glob(os.path.join(output_dir, "*denue*.csv"))
    cpv_files = glob.glob(os.path.join(output_dir, "*RESAGEBURB*.csv")) + glob.glob(os.path.join(output_dir, "*censo*.csv")) + glob.glob(os.path.join(output_dir, "*censo*.xlsx"))
    ce_files = glob.glob(os.path.join(output_dir, "*SAIC*.csv")) + glob.glob(os.path.join(output_dir, "*cenu24*.csv"))
    enoe_files = glob.glob(os.path.join(output_dir, "*2026_trim*.csv")) + glob.glob(os.path.join(output_dir, "*enoe*.csv"))

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
            f"{int(data['h001a']):,}" if data['h001a'] else "-",
            f"{data['factor']:.3f}",
            data["status"]
        )
    console.print(tabla_calib)

    # D. Carga y Georreferenciación CPV 2020
    if not cpv_files:
        raise FileNotFoundError("No se encontró archivo de Censo CPV 2020 (*RESAGEBURB*.csv o *censo*).")
    df_cpv = load_cpv_demography(
        cpv_paths=cpv_files,
        df_denue=df_denue,
        bbox=bbox_dict,
        tasa_pea=tasa_pea,
        growth_factors=macro.get("growth_factors", {}),
        default_growth=macro.get("default_growth_factor", 1.0)
    )
    console.print(f"-> Censo CPV cargado y georreferenciado: [cyan]{len(df_cpv):,}[/cyan] manzanas habitadas.")

    # =========================================================================
    # 3. MALLA ESPACIAL, SNAPPING VIAL Y FUSIÓN DE POIS
    # =========================================================================
    console.print(f"\n[bold yellow]3. Malla Espacial y Snapping Vial[/bold yellow]")

    roads_path = os.path.join(output_dir, "roads.geojson")
    if not os.path.exists(roads_path):
        raise FileNotFoundError("roads.geojson no encontrado. Compila el mapa primero.")

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
    # 4. MODELO GRAVITATORIO MULTINOMIAL
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

    try:
        from depot.demand import DemandData
        dd = DemandData({"points": demand_points, "pops": pops})
        dd.sanitize()
        # Generar config.json con viewport calculado por depot
        dd.generate_config(
            name=city_info["name"],
            code=city_code,
            description=city_info["description"][:80],
            creator=city_info.get("creator", "Subway Builder México v6.0"),
            version="6.0.0",
            filename=os.path.join(output_dir, "config.json")
        )
        dd.save(os.path.join(output_dir, "demand_data.json"))
        console.print("[green]✓[/green] Sanitización y exportación mediante [bold]depot.demand.DemandData[/bold] exitosa.")
    except Exception as e:
        console.print(f"[yellow]Nota: depot.demand fallback nativo ({e}). Exportando directamente...[/yellow]")
        # Exportación manual de respaldo
        with open(os.path.join(output_dir, "demand_data.json"), "w", encoding="utf-8") as f:
            json.dump({"points": demand_points, "pops": pops}, f, separators=(',', ':'))

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
            "creator": city_info.get("creator", "Subway Builder México v6.0"),
            "version": "6.0.0"
        }
        with open(os.path.join(output_dir, "config.json"), "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)

    # =========================================================================
    # 6. EMPAQUETADO EN ARCHIVO ZIP FINAL
    # =========================================================================
    zip_name = f"{city_code}.zip"
    zip_path = os.path.join(output_dir, zip_name)

    files_to_pack = [
        "config.json",
        "demand_data.json",
        f"{city_code}.pmtiles",
        "buildings_index.bin.gz",
        "roads.geojson",
        "runways_taxiways.geojson",
        "ocean_depth_index.json.gz"
    ]

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fname in files_to_pack:
            fpath = os.path.join(output_dir, fname)
            if os.path.exists(fpath):
                zipf.write(fpath, arcname=fname)
                console.print(f"  + Empaquetado: [dim]{fname}[/dim]")

    console.print(Panel.fit(
        f"[bold green]¡PAQUETE {zip_name} GENERADO CON ÉXITO![/bold green]\n"
        f"Listo para importar en Kronifer's Map Manager / Railyard.",
        style="green"
    ))

    return zip_path
