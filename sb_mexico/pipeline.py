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
import hashlib
import shutil
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
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
    ArterialRoadIndex,
    merge_identical_commutes,
    sync_demand_points_and_pops,
    apply_modal_competitiveness_experiment,
    calculate_commute_distance_distribution,
    recommend_gravity_beta,
    is_point_in_exclusion_zone,
    prepare_polygon_geom,
    is_point_in_prepared_polygon,
)
from sb_mexico.osrm import (
    is_docker_available,
    prepare_osrm_network_wsl,
    start_osrm_daemon_wsl,
    stop_osrm_daemon_wsl,
    enrich_pops_with_osrm,
    calculate_canonical_driving_fallback
)
from sb_mexico.cartography import build_city_map
from sb_mexico.special_demand import (
    generate_special_demand_points_doc,
    validate_special_demand_points,
    save_special_demand_points
)
from sb_mexico.sources import resolve_source_manifest, manifest_paths

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
console = Console()


@dataclass(frozen=True)
class BuildResult:
    """Unambiguous outcome of one identified pipeline run."""
    status: str
    build_id: str
    city_code: str
    config_identity: str
    package_path: Optional[str] = None
    demand_path: Optional[str] = None
    staging_dir: Optional[str] = None
    package_sha256: Optional[str] = None

    @property
    def package_created(self) -> bool:
        return self.status == "package_created" and bool(self.package_path)


MAP_ARTIFACTS = (
    "roads.geojson", "buildings_index.bin.gz", "runways_taxiways.geojson",
    "ocean_depth_index.json.gz",
)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _config_identity(config_path: str) -> str:
    return _sha256_file(os.path.abspath(config_path))


def _verify_source_manifest_unchanged(manifest_path: str) -> None:
    """Reject promotion if an input changed after it was resolved for this run."""
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    changed = []
    for entries in manifest.get("sources", {}).values():
        for entry in entries:
            path = entry.get("path")
            try:
                stat = os.stat(path)
            except (OSError, TypeError):
                changed.append(str(path))
                continue
            if stat.st_size != entry.get("size_bytes") or stat.st_mtime_ns != entry.get("modified_ns"):
                changed.append(str(path))
    if changed:
        raise ValueError("Build inputs changed during execution; refusing package promotion: " + ", ".join(changed))


def _cartography_identity(cfg: Dict[str, Any], source_manifest: Dict[str, Any]) -> Dict[str, Any]:
    city = cfg["city"]
    osm = []
    for entry in source_manifest.get("sources", {}).get("osm", []):
        path = entry["path"]
        osm.append({"path": os.path.abspath(path), "sha256": _sha256_file(path)})
    affecting_keys = (
        "building_filter_size", "building_simplification", "include_ocean",
        "urban_parks_only", "urban_core_polygon", "lod_peripheral_roads",
        "include_pedestrian_paths", "lod_peripheral_labels", "lod_peripheral_buildings",
    )
    payload = {
        "format_version": 1,
        "city_code": city["code"],
        "bbox": city["bbox"],
        "osm_sources": osm,
        "cartography_config": {key: city.get(key) for key in affecting_keys},
        "pipeline_version": "7.1.0",
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return payload


def validate_package_integrity(directory: str, city_code: str) -> None:
    """Validate package structure and cross-file references before ZIP creation."""
    required = ("config.json", "demand_data.json", f"{city_code}.pmtiles", "roads.geojson")
    missing = [name for name in required if not os.path.isfile(os.path.join(directory, name))]
    if missing:
        raise ValueError(f"Package integrity: missing required artifacts: {', '.join(missing)}")
    with open(os.path.join(directory, "config.json"), encoding="utf-8") as stream:
        config = json.load(stream)
    if config.get("code") != city_code:
        raise ValueError(f"Package integrity: config code {config.get('code')!r} != {city_code!r}")
    with open(os.path.join(directory, "demand_data.json"), encoding="utf-8") as stream:
        demand = json.load(stream)
    points, pops = demand.get("points"), demand.get("pops")
    if not isinstance(points, list) or not isinstance(pops, list):
        raise ValueError("Package integrity: demand_data must contain points and pops arrays")
    point_ids = [point.get("id") for point in points]
    pop_ids = [pop.get("id") for pop in pops]
    if any(not value for value in point_ids) or len(point_ids) != len(set(point_ids)):
        raise ValueError("Package integrity: demand points contain missing or duplicate entity IDs")
    if any(not value for value in pop_ids) or len(pop_ids) != len(set(pop_ids)):
        raise ValueError("Package integrity: pops contain missing or duplicate entity IDs")
    point_set, pop_set = set(point_ids), set(pop_ids)
    for pop in pops:
        if pop.get("residenceId") not in point_set or pop.get("jobId") not in point_set:
            raise ValueError(f"Package integrity: pop {pop.get('id')!r} has dangling origin/destination")
    for point in points:
        dangling = set(point.get("popIds", [])) - pop_set
        if dangling:
            raise ValueError(f"Package integrity: point {point.get('id')!r} has dangling popIds {sorted(dangling)}")
    special_path = os.path.join(directory, "special_demand_points.json")
    if os.path.isfile(special_path):
        with open(special_path, encoding="utf-8") as stream:
            special = json.load(stream)
        valid, errors = validate_special_demand_points(special, demand_data=demand, expected_map_code=city_code)
        if not valid:
            raise ValueError("Package integrity: invalid special demand: " + "; ".join(errors))
    map_manifest_path = os.path.join(directory, "cartography_manifest.json")
    if not os.path.isfile(map_manifest_path):
        raise ValueError("Package integrity: missing cartography_manifest.json")
    with open(map_manifest_path, encoding="utf-8") as stream:
        map_manifest = json.load(stream)
    if map_manifest.get("city_code") != city_code:
        raise ValueError("Package integrity: cartography city code does not match config")


def _prepare_depot_demand(points: List[Dict[str, Any]], pops: List[Dict[str, Any]]):
    """Use Depot when available; only import absence permits the local exporter."""
    try:
        from depot.demand import DemandData
    except ImportError:
        return None
    demand = DemandData({"points": points, "pops": pops})
    demand.sanitize()  # Deliberately not caught: rejected payloads are fatal.
    return demand


def _validate_created_zip(zip_path: str, city_code: str) -> None:
    required = {"config.json", "demand_data.json", f"{city_code}.pmtiles", "roads.geojson"}
    with zipfile.ZipFile(zip_path, "r") as archive:
        corrupt = archive.testzip()
        if corrupt:
            raise ValueError(f"Created package ZIP is corrupt at member {corrupt}")
        missing = required - set(archive.namelist())
        if missing:
            raise ValueError(f"Created package ZIP is incomplete: {sorted(missing)}")


def build_gravity_input_contract(
    df_cpv,
    df_denue,
    demand_points: List[Dict[str, Any]],
    exclusion_zones: List[Dict[str, Any]],
    urban_core_polygon: Any = None,
    restrict_demand_to_urban_core: bool = True,
    employment_ledger: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reconcile authoritative marginals immediately before gravity execution."""
    core = prepare_polygon_geom(urban_core_polygon) if urban_core_polygon and restrict_demand_to_urban_core else None
    removed_exclusion_pop = 0.0
    removed_exclusion_pea = 0.0
    removed_core_pop = 0.0
    removed_core_pea = 0.0
    for row in df_cpv[['lon', 'lat', 'pobtot_adj', 'pea_real']].to_dict('records'):
        lon, lat = float(row['lon']), float(row['lat'])
        if exclusion_zones and is_point_in_exclusion_zone(lon, lat, exclusion_zones):
            removed_exclusion_pop += float(row['pobtot_adj'])
            removed_exclusion_pea += float(row['pea_real'])
        elif core and not is_point_in_prepared_polygon(lon, lat, core):
            removed_core_pop += float(row['pobtot_adj'])
            removed_core_pea += float(row['pea_real'])

    entering_population = float(df_cpv['pobtot_adj'].sum())
    entering_pea = float(df_cpv['pea_real'].sum())
    expected_active_population = entering_population - removed_exclusion_pop - removed_core_pop
    expected_active_pea = entering_pea - removed_exclusion_pea - removed_core_pea
    final_population = int(sum(int(point.get('residents', 0)) for point in demand_points))
    final_pea = int(sum(int(point.get('pea_15ymas', 0)) for point in demand_points))
    final_jobs = int(sum(int(point.get('jobs', 0)) for point in demand_points))
    final_special_jobs = int(sum(int(point.get('jobs', 0)) for point in demand_points if point.get('is_special')))
    final_regular_jobs = final_jobs - final_special_jobs
    calibrated_denue_input = float(df_denue['calibrated_jobs'].sum()) if 'calibrated_jobs' in df_denue else 0.0
    rounding_tolerance = 0.500001 * max(1, len(demand_points)) + 1.0

    for name, value in {
        'population': final_population,
        'workers': final_pea,
        'employment': final_jobs,
    }.items():
        if not np.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid final gravity marginal {name}={value}")
    if abs(final_population - expected_active_population) > rounding_tolerance:
        raise ValueError(
            f"Population grid reconciliation failed: expected {expected_active_population:.3f}, got {final_population}"
        )
    if abs(final_pea - expected_active_pea) > rounding_tolerance:
        raise ValueError(f"PEA grid reconciliation failed: expected {expected_active_pea:.3f}, got {final_pea}")
    validated_employment = validate_employment_ledger(employment_ledger, demand_points)
    if abs(calibrated_denue_input - validated_employment['authoritative_calibrated_employment']) > 1e-6:
        raise ValueError(
            "Employment ledger authoritative input does not match calibrated DENUE employment"
        )

    population_ledger = dict(getattr(df_cpv, 'attrs', {}).get('population_ledger', {}))
    population_ledger.update({
        'population_removed_by_exclusion_zones': removed_exclusion_pop,
        'pea_removed_by_exclusion_zones': removed_exclusion_pea,
        'population_removed_by_urban_core': removed_core_pop,
        'pea_removed_by_urban_core': removed_core_pea,
        'population_expected_in_grid_before_rounding': expected_active_population,
        'pea_expected_in_grid_before_rounding': expected_active_pea,
        'final_grid_population': final_population,
        'final_grid_pea': final_pea,
        'grid_rounding_population_delta': final_population - expected_active_population,
        'grid_rounding_pea_delta': final_pea - expected_active_pea,
    })
    return {
        'population_ledger': population_ledger,
        'denue_ingestion': dict(getattr(df_denue, 'attrs', {}).get('ingestion_diagnostics', {})),
        'authoritative_marginals': {
            'population': final_population,
            'workers_origins_pea': final_pea,
            'employment_destination_capacity': final_jobs,
        },
        'employment_ledger': validated_employment,
        'checks': {
            'nonnegative_finite_nonzero': True,
            'population_reconciled_within_cell_rounding': True,
            'pea_reconciled_within_cell_rounding': True,
            'rounding_tolerance': rounding_tolerance,
        },
    }


def validate_employment_ledger(
    ledger: Optional[Dict[str, Any]],
    demand_points: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Validate destination mass against independently accumulated grid stages."""
    if not ledger:
        raise ValueError("Missing independent employment conservation ledger")
    required = {
        'authoritative_calibrated_employment', 'geographic_bbox_selected_employment',
        'removed_by_exclusion_zones', 'removed_by_urban_core', 'after_explicit_removals',
        'absorbed_by_special_pois', 'regular_before_affluence', 'affluence_multiplier_delta',
        'target_capacity_delta', 'regular_grid_before_rounding', 'grid_rounding_delta',
        'regular_grid_employment_expected', 'poi_capacity_delta',
        'special_poi_employment_expected', 'final_employment_expected',
    }
    missing = sorted(required - set(ledger))
    if missing:
        raise ValueError(f"Employment ledger missing stages: {missing}")
    values = {key: float(ledger[key]) for key in required}
    if any(not np.isfinite(value) for value in values.values()):
        raise ValueError("Employment ledger contains non-finite values")
    tolerance = float(ledger.get('tolerance', 1e-6))

    def assert_close(label: str, actual: float, expected: float) -> None:
        if abs(actual - expected) > tolerance:
            raise ValueError(
                f"Employment conservation failed at {label}: actual={actual}, expected={expected}"
            )

    assert_close(
        'geographic/BBOX selection',
        values['geographic_bbox_selected_employment'],
        values['authoritative_calibrated_employment'],
    )
    assert_close(
        'explicit removals',
        values['after_explicit_removals'],
        values['geographic_bbox_selected_employment']
        - values['removed_by_exclusion_zones']
        - values['removed_by_urban_core'],
    )
    assert_close(
        'POI absorption split',
        values['after_explicit_removals'],
        values['absorbed_by_special_pois'] + values['regular_before_affluence'],
    )
    assert_close(
        'affluence/target transformations',
        values['regular_grid_before_rounding'],
        values['regular_before_affluence']
        + values['affluence_multiplier_delta']
        + values['target_capacity_delta'],
    )
    assert_close(
        'regular grid rounding',
        values['regular_grid_employment_expected'],
        values['regular_grid_before_rounding'] + values['grid_rounding_delta'],
    )
    assert_close(
        'special POI capacity',
        values['special_poi_employment_expected'],
        values['absorbed_by_special_pois'] + values['poi_capacity_delta'],
    )
    assert_close(
        'final expected destination mass',
        values['final_employment_expected'],
        values['regular_grid_employment_expected'] + values['special_poi_employment_expected'],
    )

    actual_regular = float(sum(int(point.get('jobs', 0)) for point in demand_points if not point.get('is_special')))
    actual_special = float(sum(int(point.get('jobs', 0)) for point in demand_points if point.get('is_special')))
    actual_final = actual_regular + actual_special
    assert_close('regular demand points', actual_regular, values['regular_grid_employment_expected'])
    assert_close('special demand points', actual_special, values['special_poi_employment_expected'])
    assert_close('final demand points', actual_final, values['final_employment_expected'])
    validated = dict(ledger)
    validated.update({
        'actual_regular_grid_employment': int(actual_regular),
        'actual_special_poi_employment': int(actual_special),
        'actual_final_employment': int(actual_final),
        'validated': True,
    })
    return validated


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


def validate_cohort_spatial_integrity(
    pops: List[Dict[str, Any]],
    demand_points: List[Dict[str, Any]]
) -> None:
    """
    Auditoría estricta pre-exportación para garantizar que ninguna cohorte (pop)
    contenga distancias físicamente imposibles, micro-atajos anómalos o tiempos no válidos.
    Lanza ValueError y aborta el pipeline si se detecta cualquier anomalía.
    """
    import math
    dp_locs = {p["id"]: p["location"] for p in demand_points if "id" in p and "location" in p}
    anomalies = []
    min_speed_kmh = 1.0
    max_speed_kmh = 180.0

    for p in pops:
        pid = p.get("id", "unknown")
        r_id = p.get("residenceId")
        j_id = p.get("jobId")
        d_road = p.get("drivingDistance")
        d_sec = p.get("drivingSeconds")

        if r_id not in dp_locs:
            anomalies.append(f"{pid}: unknown residenceId={r_id}")
            continue
        if j_id not in dp_locs:
            anomalies.append(f"{pid}: unknown jobId={j_id}")
            continue
        try:
            d_road = float(d_road)
            d_sec = float(d_sec)
        except (TypeError, ValueError):
            anomalies.append(f"{pid}: non-numeric route fields distance={d_road}, seconds={d_sec}")
            continue
        if not math.isfinite(d_road) or not math.isfinite(d_sec):
            anomalies.append(f"{pid}: non-finite route fields distance={d_road}, seconds={d_sec}")
            continue
        if d_sec <= 0:
            anomalies.append(f"{pid}: drivingSeconds={d_sec} <= 0")
            continue
        if d_road < 0:
            anomalies.append(f"{pid}: drivingDistance={d_road} < 0")
            continue

        # Speeds outside 1-180 km/h are treated as corrupt for non-trivial
        # trips. Very short trips are exempt because duration floors dominate.
        if d_road >= 500.0:
            implied_speed_kmh = d_road / d_sec * 3.6
            if not (min_speed_kmh <= implied_speed_kmh <= max_speed_kmh):
                anomalies.append(
                    f"{pid}: implied speed={implied_speed_kmh:.2f} km/h outside "
                    f"{min_speed_kmh:.0f}-{max_speed_kmh:.0f} km/h"
                )
                continue

        o_loc = dp_locs[r_id]
        d_loc = dp_locs[j_id]

        if r_id == j_id:
            continue

        cos_lat = math.cos(math.radians((o_loc[1] + d_loc[1]) / 2.0))
        dx_m = (d_loc[0] - o_loc[0]) * 111_320.0 * cos_lat
        dy_m = (d_loc[1] - o_loc[1]) * 110_574.0
        euclid_m = math.hypot(dx_m, dy_m)

        # Regla 1: Distancia euclidiana > 500m pero drivingDistance < 70% euclidiana
        if euclid_m > 500.0 and d_road < (0.70 * euclid_m):
            anomalies.append(
                f"{pid}: {r_id}->{j_id} Euclid={euclid_m:.0f}m but drivingDistance={d_road}m (< 0.70x)"
            )

        # Regla 2: Puntos distintos (> 250m euclidiano) pero drivingDistance < 150m
        elif euclid_m > 250.0 and d_road < 150:
            anomalies.append(
                f"{pid}: {r_id}->{j_id} Euclid={euclid_m:.0f}m but drivingDistance={d_road}m (< 150m)"
            )

    if anomalies:
        err_msg = (
            f"Fallo de integridad espacial: Se detectaron {len(anomalies)} cohortes con distancias "
            f"físicamente imposibles antes de exportar demand_data.json:\n"
            + "\n".join(f"  - {a}" for a in anomalies[:10])
        )
        if len(anomalies) > 10:
            err_msg += f"\n  ... y {len(anomalies) - 10} más."
        raise ValueError(err_msg)


def _execute_pipeline_run(
    config_path: str,
    skip_map: bool = False,
    output_dir: str = ".",
    data_dir: Optional[str] = None,
    include_driving_path: Optional[bool] = None,
    build_id: Optional[str] = None,
    config_identity: Optional[str] = None,
) -> BuildResult:
    """
    Ejecuta el pipeline completo de principio a fin de manera determinista y autovalidada.
    """
    console.print(Panel.fit("[bold green]SUBWAY BUILDER MÉXICO v7.1[/bold green]\n[cyan]Pipeline Integral y Autovalidado[/cyan]"))

    cfg = load_city_config(config_path)
    city_info = cfg["city"]
    macro = cfg["macroeconomics"]
    routing_cfg = cfg.get("routing", {})

    if include_driving_path is None:
        if "include_driving_path" in routing_cfg:
            include_driving_path = bool(routing_cfg["include_driving_path"])
        elif "include_driving_path" in macro:
            include_driving_path = bool(macro["include_driving_path"])
        else:
            include_driving_path = bool(cfg.get("include_driving_path", False))

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
    build_id = build_id or uuid.uuid4().hex
    config_identity = config_identity or _config_identity(config_path)
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

    temporal_cfg = cfg.get("temporal") or {}
    if "model_year" not in temporal_cfg:
        raise ValueError("Configuration must declare temporal.model_year explicitly")
    model_year = int(temporal_cfg["model_year"])
    cpv_base_year = int(temporal_cfg.get("cpv_base_year", 2020))
    if cpv_base_year != 2020:
        raise ValueError("CPV ingestion currently supports only the documented 2020 base year")
    source_manifest = resolve_source_manifest(cfg, config_path, ROOT_DIR, data_dir=data_dir)
    source_manifest["temporal_contract"] = {
        "model_year": model_year,
        "cpv_base_year": cpv_base_year,
        "enoe_period": temporal_cfg.get("enoe_period"),
        "declared_vintages": temporal_cfg.get("source_vintages", {}),
    }
    source_use = {
        "cpv": True,
        "denue": True,
        "marco": True,
        "ce": not bool(macro.get("ce_2024_benchmarks")),
        "enoe": macro.get("tasa_pea") is None or macro.get("til_1_state") is None,
        "conapo": True,
        "osm": not skip_map,
        "roads": True,
    }
    for dataset, entries in source_manifest["sources"].items():
        for entry in entries:
            entry["consumed"] = bool(source_use.get(dataset, False))
    for entry in source_manifest["sources"].get("cpv", []):
        if entry["year"] is None:
            entry["year"] = cpv_base_year
            entry["year_source"] = "cpv_base_year_contract"
        elif entry["year"] != cpv_base_year:
            raise ValueError(f"CPV source vintage {entry['year']} does not match base year {cpv_base_year}")
    for dataset in ("denue", "ce"):
        entries = source_manifest["sources"].get(dataset, [])
        if source_use[dataset] and entries and any(entry["year"] is None for entry in entries):
            raise ValueError(
                f"Unable to establish {dataset} vintage; declare temporal.source_vintages.{dataset}"
            )
    with open(os.path.join(out_dir, "source_manifest.json"), "w", encoding="utf-8") as manifest_file:
        json.dump(source_manifest, manifest_file, indent=2, ensure_ascii=False)
    src_dir = source_manifest["project_dir"]

    # =========================================================================
    # 1. COMPILACIÓN CARTOGRÁFICA (SI NO SE OMITE)
    # =========================================================================
    if not skip_map:
        console.print(f"\n[bold yellow]1. Compilación Cartográfica ({city_code})[/bold yellow]")
        pbf_candidates = manifest_paths(source_manifest, "osm")
        osm_pbf = pbf_candidates[0] if pbf_candidates else None
        build_city_map(
            city_code=city_code,
            bbox=bbox_list,
            osm_pbf_path=osm_pbf,
            building_filter_size=city_info.get("building_filter_size", 15.0),
            building_simplification=city_info.get("building_simplification", 0.2),
            include_ocean=city_info.get("include_ocean", False),
            urban_parks_only=city_info.get("urban_parks_only", False),
            urban_core_polygon=city_info.get("urban_core_polygon"),
            lod_peripheral_roads=city_info.get("lod_peripheral_roads", "standard"),
            include_pedestrian_paths=city_info.get("include_pedestrian_paths", False),
            lod_peripheral_labels=city_info.get("lod_peripheral_labels", "none"),
            lod_peripheral_buildings=city_info.get("lod_peripheral_buildings", "none"),
            places=cfg.get("places", []),
            output_dir=out_dir
        )
        with open(os.path.join(out_dir, "cartography_manifest.json"), "w", encoding="utf-8") as map_file:
            json.dump(_cartography_identity(cfg, source_manifest), map_file, indent=2, ensure_ascii=False)
    else:
        console.print(f"\n[dim]1. Compilación Cartográfica omitida por parámetro.[/dim]")

    # =========================================================================
    # 2. INGESTA ESTADÍSTICA DE LA CUATRIFECTA INEGI
    # =========================================================================
    console.print(f"\n[bold yellow]2. Ingesta y Calibración INEGI[/bold yellow]")

    denue_files = manifest_paths(source_manifest, "denue")
    cpv_files = manifest_paths(source_manifest, "cpv")
    ce_files = manifest_paths(source_manifest, "ce")
    enoe_files = manifest_paths(source_manifest, "enoe")
    conapo_files = manifest_paths(source_manifest, "conapo")
    marco_files = manifest_paths(source_manifest, "marco")
    if marco_files:
        console.print(f"-> Capas de Marco Geoestadístico detectadas: [green]{len(marco_files)}[/green] archivos.")

    # A. Macroeconomía (ENOE)
    tasa_pea = macro.get("tasa_pea")
    til_1 = macro.get("til_1_state")
    if (tasa_pea is None or til_1 is None) and enoe_files:
        intended_enoe_period = temporal_cfg.get("enoe_period")
        if not intended_enoe_period:
            raise ValueError("temporal.enoe_period is required when deriving macro rates from ENOE")
        enoe_data = parse_enoe_indicators(enoe_files[0], intended_period=intended_enoe_period)
        source_manifest["sources"]["enoe"][0]["period"] = enoe_data["period"]
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
        min_sample_threshold=macro.get("sample_threshold", 500),
        denominator_contract=(cfg.get("data_integrity") or {}).get("denue_municipal_denominators"),
        denue_vintage=next(
            (entry.get("year") for entry in source_manifest["sources"]["denue"] if entry.get("year") is not None),
            None,
        ),
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
        growth_denominator = temporal_cfg.get("conapo_growth_denominator")
        if growth_denominator not in {"conapo_2020", "cpv_2020"}:
            raise ValueError(
                "temporal.conapo_growth_denominator must explicitly be 'conapo_2020' or 'cpv_2020'"
            )
        conapo_meta = parse_conapo_projections(
            conapo_files[0], target_year=model_year, base_year=cpv_base_year,
            as_growth_factors=growth_denominator == "conapo_2020", return_metadata=True,
            population_column=temporal_cfg.get("conapo_population_column"),
            source_year=(temporal_cfg.get("source_vintages") or {}).get("conapo"),
        )
        conapo_projs = conapo_meta["values"]
        source_manifest["sources"]["conapo"][0].update({
            "base_year": conapo_meta["base_year"],
            "target_year": conapo_meta["target_year"],
            "population_column": conapo_meta["population_column"],
            "growth_denominator": growth_denominator,
            "year_provenance": conapo_meta["year_provenance"],
        })
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
        marco_paths=marco_files if marco_files else None,
        max_unmatched_population_fraction=float(
            (cfg.get("data_integrity") or {}).get("max_unmatched_population_fraction", 0.01)
        ),
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

    if os.path.exists(roads_path):
        roads_gdf = gpd.read_file(roads_path)
        console.print(f"-> Snapping vial activado: [cyan]{len(roads_gdf):,}[/cyan] segmentos de vía ({os.path.basename(roads_path)}).")
    else:
        roads_gdf = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        console.print("[yellow]-> roads.geojson no encontrado. La malla de demanda se posicionará en los centroides urbanos sin snapping vial.[/yellow]")

    grid_size = city_info.get("grid_size", 0.0025)
    raw_seed = city_info.get("seed", macro.get("seed", 42))
    try:
        seed = int(raw_seed)
    except (ValueError, TypeError):
        seed = 42

    affluence_zones = cfg.get("affluence_zones", [])
    if affluence_zones:
        enabled_zones = [z for z in affluence_zones if z.get("enabled", True)]
        console.print(f"-> Zonas de Alta Afluencia detectadas: [yellow]{len(enabled_zones)}[/yellow] activas ({len(affluence_zones)} totales).")
        tabla_az = Table(title=f"Zonas de Alta Afluencia ({city_code})")
        tabla_az.add_column("ID / Nombre", style="cyan")
        tabla_az.add_column("Arquetipo", style="magenta")
        tabla_az.add_column("Multiplicador", justify="right", style="bold yellow")
        tabla_az.add_column("Bono Alcance", justify="right", style="blue")
        tabla_az.add_column("Modo", style="white")
        for az in affluence_zones:
            status = "[green]Activa[/green]" if az.get("enabled", True) else "[dim]Desactivada[/dim]"
            mult_str = f"{float(az.get('multiplier', 1.0)):.1f}x"
            reach_str = f"+{int(float(az.get('reach_bonus', 0.0)) * 100)}%"
            tabla_az.add_row(
                f"{az.get('name', az.get('id', 'Zona'))} ({status})",
                az.get("archetype", "custom"),
                mult_str,
                reach_str,
                az.get("target_mode", "MULTIPLIER")
            )
        console.print(tabla_az)

    exclusion_zones = cfg.get("exclusion_zones", [])
    if exclusion_zones:
        enabled_excl = [z for z in exclusion_zones if z.get("enabled", True)]
        console.print(f"-> Zonas de Exclusión detectadas: [bold red]{len(enabled_excl)}[/bold red] activas ({len(exclusion_zones)} totales) - Sin simulación de demanda en sus perímetros.")

    demand_points, poi_audit, employment_ledger = build_demand_grid(
        df_denue=df_denue,
        df_cpv=df_cpv,
        special_pois=pois_cfg,
        roads_gdf=roads_gdf,
        grid_size=grid_size,
        min_residents=city_info.get("min_residents", 10),
        min_jobs=city_info.get("min_jobs", 3),
        seed=seed,
        affluence_zones=affluence_zones,
        exclusion_zones=exclusion_zones,
        urban_core_polygon=city_info.get("urban_core_polygon"),
        restrict_demand_to_urban_core=city_info.get("restrict_demand_to_urban_core", True),
        return_employment_ledger=True,
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
    console.print(f"\n[bold yellow]4. Modelo Gravitatorio, OD Entera y Cohortes Deterministas[/bold yellow]")

    gravity_input_contract = build_gravity_input_contract(
        df_cpv=df_cpv,
        df_denue=df_denue,
        demand_points=demand_points,
        exclusion_zones=exclusion_zones,
        urban_core_polygon=city_info.get("urban_core_polygon"),
        restrict_demand_to_urban_core=city_info.get("restrict_demand_to_urban_core", True),
        employment_ledger=employment_ledger,
    )
    with open(os.path.join(out_dir, "source_manifest.json"), "w", encoding="utf-8") as manifest_file:
        json.dump(source_manifest, manifest_file, indent=2, ensure_ascii=False)
    integrity_audit = {
        "source_manifest": source_manifest,
        "temporal_contract": source_manifest["temporal_contract"],
        "denue_calibration": audit_calib,
        "gravity_input_contract": gravity_input_contract,
    }
    with open(os.path.join(out_dir, "data_integrity_audit.json"), "w", encoding="utf-8") as audit_file:
        json.dump(
            integrity_audit,
            audit_file,
            indent=2,
            ensure_ascii=False,
            default=lambda value: value.item() if hasattr(value, "item") else str(value),
        )

    isolated_zones = cfg.get("isolated_zones", cfg.get("city", {}).get("isolated_zones", []))
    if isolated_zones:
        console.print(f"-> Zonas topológicas aisladas detectadas: [cyan]{len(isolated_zones)}[/cyan] zonas.")
    console.print("-> Motor Gravitatorio Doblemente Acotado (Furness / IPFP): [green]Habilitado[/green]")

    total_pea = gravity_input_contract["authoritative_marginals"]["workers_origins_pea"]

    target_pop_size = macro.get("target_pop_size", 180)
    max_pop_size = macro.get("max_pop_size", 200)
    min_pop_size = macro.get("min_pop_size", 25)

    console.print(f"-> Escala canónica de cohortes: [cyan]min_pop_size = {min_pop_size} | target_pop_size = {target_pop_size} | max_pop_size = {max_pop_size} | seed = {seed}[/cyan] (PEA Total: {total_pea:,})")

    raw_pops, od_diagnostics = simulate_gravity_demand(
        demand_points=demand_points,
        beta=macro.get("gravity_beta", 0.12),
        max_distance_km=macro.get("max_distance_km", 55.0),
        min_pop_size=min_pop_size,
        max_pop_size=max_pop_size,
        target_pop_size=target_pop_size,
        seed=seed,
        isolated_zones=isolated_zones,
        affluence_zones=affluence_zones,
        furness_iterations=macro.get("furness_iterations", 1000),
        furness_tol=macro.get("furness_tol", 1e-10),
        road_index=None,
        return_diagnostics=True,
    )
    feasibility_diagnostics = od_diagnostics["feasibility"]
    integerization_diagnostics = od_diagnostics["integerization"]
    integrity_audit["od_allocation"] = {
        "origin_count": feasibility_diagnostics["origin_count"],
        "destination_count": feasibility_diagnostics["destination_count"],
        "support_vertex_count": feasibility_diagnostics["support_vertex_count"],
        "support_edge_count": feasibility_diagnostics["support_edge_count"],
        "components": feasibility_diagnostics["components"],
        "max_flow_shortfall": feasibility_diagnostics["max_flow_shortfall"],
        "validation_seconds": feasibility_diagnostics["validation_seconds"],
        "max_flow_seconds": feasibility_diagnostics["max_flow_seconds"],
        "ipfp": {
            key: value for key, value in od_diagnostics["ipfp"].items()
            if key != "feasibility"
        },
        "integerization": integerization_diagnostics,
        "undersized_cohorts": od_diagnostics["undersized_cohorts"],
    }
    with open(os.path.join(out_dir, "data_integrity_audit.json"), "w", encoding="utf-8") as audit_file:
        json.dump(
            integrity_audit,
            audit_file,
            indent=2,
            ensure_ascii=False,
            default=lambda value: value.item() if hasattr(value, "item") else str(value),
        )
    console.print(
        "-> OD factible e integerizada: "
        f"[cyan]{feasibility_diagnostics['support_edge_count']:,}[/cyan] aristas, "
        f"max-flow {feasibility_diagnostics['max_flow_seconds']:.3f}s, "
        f"IPFP {od_diagnostics['ipfp']['ipfp_seconds']:.3f}s, "
        f"integerización {integerization_diagnostics['integerization_seconds']:.3f}s."
    )

    console.print(f"-> Pipeline Canónico de Consolidación y Escala Subway Builder:")
    raw_pop_count = len(raw_pops)

    # Tras fijar la matriz OD no se permite clustering ni relocalización entre pares.
    # La única transformación válida es volver a empacar cohortes del mismo OD exacto.
    pops = merge_identical_commutes(
        raw_pops,
        min_pop_size=min_pop_size,
        max_pop_size=max_pop_size,
        target_pop_size=target_pop_size,
        include_driving_path=include_driving_path,
    )

    # La sincronización valida los marginales autoritativos; no reescribe empleo.
    demand_points, pops = sync_demand_points_and_pops(demand_points, pops, remove_orphans=True, include_driving_path=include_driving_path)

    total_viajeros = sum(p["size"] for p in pops)

    console.print(f"   • Cohortes generadas: de [yellow]{raw_pop_count:,}[/yellow] a [green]{len(pops):,}[/green] pops consolidados.")
    console.print(f"   • Nodos de demanda activos: [green]{len(demand_points):,}[/green] puntos (display 1:1 sincronizado).")
    console.print(f"   • Total de Pasajeros Activos: [bold green]{total_viajeros:,}[/bold green] (PEA Total: {total_pea:,})")

    # Validación de conservación estricta de masa
    if total_viajeros != total_pea:
        raise ValueError(f"Inconsistencia de masa: {total_viajeros} viajeros vs {total_pea} PEA")

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
    # 5. ENRIQUECIMIENTO CANÓNICO DE RUTAS VIALES (OSRM)
    # =========================================================================
    console.print(f"\n[bold yellow]5. Enriquecimiento Canónico de Rutas Viales (OSRM)[/bold yellow]")
    pbf_candidates = manifest_paths(source_manifest, "osm")
    osm_pbf = pbf_candidates[0] if pbf_candidates else None
    docker_avail, docker_env = is_docker_available()

    osrm_applied = False
    if osm_pbf and docker_avail:
        console.print(f"-> Docker en WSL 2 detectado ({docker_env}). Preparando red vial canónica OSRM...")
        prep_ok, osrm_dir = prepare_osrm_network_wsl(
            city_code=city_code,
            osm_pbf_path=osm_pbf,
            bbox=bbox_list,
            force_rebuild=False
        )
        if prep_ok:
            console.print(f"-> Iniciando microservicio OSRM daemon en WSL...")
            if start_osrm_daemon_wsl(city_code=city_code, port=5000):
                try:
                    console.print(f"-> Consultando rutas reales para {len(pops):,} cohortes de viajeros...")
                    osrm_ok, osrm_fb = enrich_pops_with_osrm(
                        pops=pops,
                        demand_points=demand_points,
                        osrm_url="http://127.0.0.1:5000",
                        include_driving_path=include_driving_path
                    )
                    console.print(
                        f"   • Rutas OSRM exactas (geometría y tiempos reales): [green]{osrm_ok:,}[/green]\n"
                        f"   • Rutas con fallback canónico (1.3× @ 40 km/h): [yellow]{osrm_fb:,}[/yellow]"
                    )
                    osrm_applied = True
                finally:
                    stop_osrm_daemon_wsl(city_code=city_code)
            else:
                console.print("[yellow][WARN] No fue posible levantar el daemon OSRM. Se aplicará fallback canónico.[/yellow]")
        else:
            console.print("[yellow][WARN] No fue posible preparar la red OSRM en WSL. Se aplicará fallback canónico.[/yellow]")
    else:
        if not osm_pbf:
            console.print("[dim]-> Archivo OSM PBF no encontrado. Aplicando fallback canónico directo.[/dim]")
        elif not docker_avail:
            console.print("[dim]-> Docker en WSL 2 no disponible. Aplicando fallback canónico directo.[/dim]")

    if not osrm_applied:
        console.print("-> Verificando métricas canónicas Colin de respaldo (1.3× circuidad @ 40 km/h flujo libre)...")
        dp_locs = {p["id"]: p["location"] for p in demand_points}
        import math
        for p in pops:
            o_loc = dp_locs.get(p.get("residenceId"))
            d_loc = dp_locs.get(p.get("jobId"))
            if o_loc and d_loc:
                cos_lat = math.cos(math.radians((o_loc[1] + d_loc[1]) / 2.0))
                dx_m = (d_loc[0] - o_loc[0]) * 111_320.0 * cos_lat
                dy_m = (d_loc[1] - o_loc[1]) * 110_574.0
                euclid_m = math.hypot(dx_m, dy_m)
                fb_dist, fb_sec = calculate_canonical_driving_fallback(euclid_m)
                p["drivingDistance"] = fb_dist
                p["drivingSeconds"] = fb_sec
                p.pop("drivingPath", None)

    # =========================================================================
    # 5.1. LABORATORIO EXPERIMENTAL: COMPETITIVIDAD MODAL (AUTO VS. METRO)
    # =========================================================================
    modal_exp_cfg = macro.get("modal_experiment") or cfg.get("modal_experiment")
    if modal_exp_cfg and modal_exp_cfg.get("enabled"):
        preset_name = modal_exp_cfg.get("preset", "custom")
        apply_modal_competitiveness_experiment(pops, modal_exp_cfg)
        v_speed = modal_exp_cfg.get("traffic_speed_kmh", 22.0)
        p_motor = modal_exp_cfg.get("motorization_rate", 0.40)
        console.print(f"\n[bold magenta]• Laboratorio Experimental de Competitividad Modal Activo:[/bold magenta]")
        console.print(
            f"   [magenta]-> Preset: [bold]{preset_name}[/bold] | "
            f"Velocidad Tráfico: [bold]{v_speed} km/h[/bold] | "
            f"Tasa Motorización: [bold]{int(round(float(p_motor)*100))}%[/bold][/magenta]"
        )

    # =========================================================================
    # 5.2. AUDITORÍA DE DISTRIBUCIÓN DE DISTANCIAS DE VIAJE (COLIN MILLER STANDARD)
    # =========================================================================
    distrib = calculate_commute_distance_distribution(pops, demand_points)
    tabla_tld = Table(
        title="Auditoría de Curva de Distancias de Viaje (Trip Length Distribution - Colin Miller Standard)",
        header_style="bold cyan"
    )
    tabla_tld.add_column("Estrato de Movilidad", style="white")
    tabla_tld.add_column("Rango", style="cyan")
    tabla_tld.add_column("Viajeros", justify="right", style="green")
    tabla_tld.add_column("Participación", justify="right", style="bold yellow")
    tabla_tld.add_column("Cohortes", justify="right", style="dim white")
    tabla_tld.add_column("Barra de Distribución", style="magenta")

    for b in distrib.get("brackets", []):
        pct = b["percentage"]
        bar_len = int(round(pct / 4.0))
        bar_str = "█" * bar_len + "░" * max(0, 25 - bar_len)
        tabla_tld.add_row(
            b["category"],
            b["label"],
            f"{b['commuters']:,}",
            f"{pct:.1f}%",
            f"{b['pops_count']:,}",
            bar_str
        )
    console.print(tabla_tld)
    console.print(
        f"   • Mediana Ponderada (P50): [bold green]{distrib['median_km']:.1f} km[/bold green] "
        f"([dim]P25: {distrib['p25_km']:.1f} km | P75: {distrib['p75_km']:.1f} km | P95: {distrib['p95_km']:.1f} km[/dim])\n"
        f"   • Promedio Ponderado: [cyan]{distrib['mean_km']:.1f} km[/cyan] | Tiempo Medio Manejo: [cyan]{distrib['mean_minutes']:.1f} min[/cyan]\n"
        f"   • Perfil Metropolitano: [bold]{distrib['profile_label']}[/bold]"
    )

    # =========================================================================
    # 6. SANITIZACIÓN NATIVA CON DEPOT Y EXPORTACIÓN
    # =========================================================================
    console.print(f"\n[bold yellow]6. Sanitización y Generación de Archivos[/bold yellow]")

    # Auditoría estricta de integridad física y espacial antes de tocar disco
    validate_cohort_spatial_integrity(pops, demand_points)

    # Centrado de Cámara (initialViewState):
    # Si el usuario configuró manualmente initial_center en el Wizard, se respeta con máxima prioridad.
    manual_center = city_info.get("initial_center")
    if manual_center and isinstance(manual_center, (list, tuple)) and len(manual_center) == 2:
        try:
            center_lon = float(manual_center[0])
            center_lat = float(manual_center[1])
            console.print(f"[cyan]-> Cámara inicial configurada manualmente:[/] lon={center_lon:.5f}, lat={center_lat:.5f}")
        except (ValueError, TypeError):
            manual_center = None

    if not manual_center:
        # Cálculo del Baricentro Urbano Ponderado por Actividad Humana (Cámara)
        total_mass = sum(p["residents"] + 1.5 * p["jobs"] for p in demand_points)
        if total_mass > 0:
            center_lon = sum(p["location"][0] * (p["residents"] + 1.5 * p["jobs"]) for p in demand_points) / total_mass
            center_lat = sum(p["location"][1] * (p["residents"] + 1.5 * p["jobs"]) for p in demand_points) / total_mass
        else:
            center_lon = (bbox_dict["min_lon"] + bbox_dict["max_lon"]) / 2.0
            center_lat = (bbox_dict["min_lat"] + bbox_dict["max_lat"]) / 2.0

    # Sanitización de drivingPath y validación de límites de memoria en V8 (Subway Builder)
    if not include_driving_path:
        for p in pops:
            p.pop("drivingPath", None)
    elif len(pops) > 20000:
        console.print(
            f"[bold red][ADVERTENCIA][/bold red] 'include_driving_path' está habilitado con {len(pops):,} cohortes.\n"
            f"   El archivo demand_data.json resultante probablemente excederá el límite de 512 MB de V8 (Chromium)\n"
            f"   y provocará que Subway Builder descarte la demanda al iniciar el juego."
        )

    clean_demand_points = sanitize_demand_points(demand_points)
    cfg_out_path = os.path.join(out_dir, "config.json")
    demand_out_path = os.path.join(out_dir, "demand_data.json")

    dd = _prepare_depot_demand(clean_demand_points, pops)
    if dd is not None:
        # Generar config.json con viewport calculado por depot
        dd.generate_config(
            name=city_info["name"],
            code=city_code,
            description=city_info["description"][:80],
            creator=city_info.get("creator", "Subway Builder México v7.1"),
            version="7.1.0",
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
    else:
        console.print("[yellow]depot.demand no está instalado; usando exportador local validado.[/yellow]")
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
            "creator": city_info.get("creator", "Subway Builder México v7.1"),
            "version": "7.1.0"
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
        is_valid, validation_errors = validate_special_demand_points(
            sp_doc,
            demand_data={"points": clean_demand_points, "pops": pops},
            expected_map_code=city_code,
        )
        if not is_valid:
            raise ValueError("Special demand validation failed: " + "; ".join(validation_errors))
        else:
            console.print(f"[green][OK][/green] Validación Special Demand Schema: [bold green]OK ({len(sp_doc['points'])} POIs conformes con @subway-builder-modded/special-demand-schemas)[/bold green]")

        sp_out_path = os.path.join(out_dir, "special_demand_points.json")
        save_special_demand_points(sp_doc, sp_out_path)

    # =========================================================================
    # 7. EMPAQUETADO EN ARCHIVO ZIP FINAL
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
        return BuildResult(
            status="demand_only", build_id=build_id, city_code=city_code,
            config_identity=config_identity, demand_path=demand_out_path, staging_dir=out_dir,
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

    validate_package_integrity(out_dir, city_code)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for fname in files_to_pack:
            fpath = os.path.join(out_dir, fname)
            if os.path.exists(fpath):
                zipf.write(fpath, arcname=fname)
                console.print(f"  + Empaquetado: [dim]{fname}[/dim]")
    _validate_created_zip(zip_path, city_code)

    console.print(Panel.fit(
        f"[bold green]¡PAQUETE {zip_name} GENERADO CON ÉXITO![/bold green]\n"
        f"Listo para importar en Kronifer's Map Manager / Railyard.",
        style="green"
    ))

    return BuildResult(
        status="package_created", build_id=build_id, city_code=city_code,
        config_identity=config_identity, package_path=zip_path, demand_path=demand_out_path,
        staging_dir=out_dir, package_sha256=_sha256_file(zip_path),
    )


def execute_pipeline(
    config_path: str,
    skip_map: bool = False,
    output_dir: str = ".",
    data_dir: Optional[str] = None,
    include_driving_path: Optional[bool] = None,
) -> BuildResult:
    """Run in unique staging and promote only a validated package from this run."""
    config_path = os.path.abspath(config_path)
    cfg = load_city_config(config_path)
    city_code = cfg["city"]["code"]
    city_base = os.path.splitext(os.path.basename(config_path))[0].lower()
    final_dir = (
        os.path.join(ROOT_DIR, "dist", city_base)
        if output_dir in (".", ROOT_DIR, "") else os.path.abspath(output_dir)
    )
    build_id = uuid.uuid4().hex
    identity = _config_identity(config_path)
    stage_dir = os.path.join(os.path.dirname(final_dir), f".{os.path.basename(final_dir)}.staging-{build_id}")
    os.makedirs(stage_dir, exist_ok=False)
    audit_path = os.path.join(stage_dir, "build_status.json")

    def write_status(status: str, **extra: Any) -> None:
        record = {
            "status": status, "build_id": build_id, "city_code": city_code,
            "config_identity": identity, "updated_at": datetime.now(timezone.utc).isoformat(),
            "package_validated": status in {"package_validated", "package_created"},
            "package_created": status == "package_created",
            **extra,
        }
        with open(audit_path, "w", encoding="utf-8") as stream:
            json.dump(record, stream, indent=2, ensure_ascii=False)

    write_status("started")
    try:
        if skip_map:
            manifest = resolve_source_manifest(cfg, config_path, ROOT_DIR, data_dir=data_dir)
            expected = _cartography_identity(cfg, manifest)
            existing_manifest_path = os.path.join(final_dir, "cartography_manifest.json")
            if not os.path.isfile(existing_manifest_path):
                raise ValueError("--skip-map cannot reuse unverifiable maps: cartography_manifest.json is missing")
            with open(existing_manifest_path, encoding="utf-8") as stream:
                existing = json.load(stream)
            if existing.get("fingerprint") != expected.get("fingerprint"):
                raise ValueError("--skip-map rejected stale/incompatible cartography artifacts for current city, BBOX, OSM source, or map configuration")
            reusable = (f"{city_code}.pmtiles", *MAP_ARTIFACTS, "cartography_manifest.json")
            for name in reusable:
                source = os.path.join(final_dir, name)
                if os.path.isfile(source):
                    shutil.copy2(source, os.path.join(stage_dir, name))
            for required in (f"{city_code}.pmtiles", "roads.geojson"):
                if not os.path.isfile(os.path.join(stage_dir, required)):
                    raise ValueError(f"--skip-map compatible artifact set is incomplete: missing {required}")

        result = _execute_pipeline_run(
            config_path=config_path, skip_map=skip_map, output_dir=stage_dir,
            data_dir=data_dir, include_driving_path=include_driving_path,
            build_id=build_id, config_identity=identity,
        )
        if not result.package_created:
            write_status("incomplete", result_status=result.status, demand_path=result.demand_path)
            return result

        if _config_identity(config_path) != identity:
            raise ValueError("Configuration changed during execution; refusing package promotion")
        _verify_source_manifest_unchanged(os.path.join(stage_dir, "source_manifest.json"))

        write_status("package_validated", package_path=result.package_path, package_sha256=result.package_sha256)
        backup_dir = f"{final_dir}.previous-{build_id}"
        moved_previous = False
        try:
            if os.path.isdir(final_dir):
                os.replace(final_dir, backup_dir)
                moved_previous = True
            os.replace(stage_dir, final_dir)
        except Exception:
            if moved_previous and not os.path.exists(final_dir) and os.path.isdir(backup_dir):
                os.replace(backup_dir, final_dir)
            raise
        if moved_previous:
            shutil.rmtree(backup_dir)
        promoted_package = os.path.join(final_dir, os.path.basename(result.package_path))
        promoted_demand = os.path.join(final_dir, os.path.basename(result.demand_path))
        audit_path = os.path.join(final_dir, "build_status.json")
        write_status("package_created", package_path=promoted_package, package_sha256=result.package_sha256)
        return replace(result, package_path=promoted_package, demand_path=promoted_demand, staging_dir=None)
    except Exception as exc:
        if os.path.isdir(stage_dir):
            write_status("failed", error=str(exc))
        raise


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Subway Builder México Pipeline")
    parser.add_argument("config", help="Ruta al archivo YAML de la ciudad")
    parser.add_argument("--skip-map", action="store_true", help="Omitir compilación cartográfica")
    parser.add_argument("--output-dir", default=".", help="Directorio de salida")
    parser.add_argument("--data-dir", default=None, help="Directorio de datos del proyecto")
    parser.add_argument(
        "--include-driving-path",
        action="store_true",
        default=None,
        help="Incluir geometrías de rutas en cohortes (drivingPath). Desactivado por defecto para evitar exceder 512 MB."
    )
    args = parser.parse_args()

    execute_pipeline(
        config_path=args.config,
        skip_map=args.skip_map,
        output_dir=args.output_dir,
        data_dir=args.data_dir,
        include_driving_path=args.include_driving_path
    )
