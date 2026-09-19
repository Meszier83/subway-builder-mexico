"""
sb_mexico.gravity
=================
Motor de interacción espacial y simulación de demanda de transporte.
Implementa:
1. Malla espacial de agregación (Clustering).
2. Fusión y absorción de POIs Especiales (Aeropuertos, Universidades, etc.).
3. Snapping vial vectorial con indexación espacial STRtree.
4. Modelo Gravitatorio con factibilidad, IPFP e integerización OD determinista.
"""

import math
import time
import numpy as np
import pandas as pd
import geopandas as gpd
from typing import List, Dict, Tuple, Optional, Union, Any
from shapely.geometry import Point, Polygon
from shapely.prepared import prep
from shapely.strtree import STRtree
from shapely.ops import nearest_points
from scipy.spatial import cKDTree
from scipy.sparse.csgraph import shortest_path
import networkx as nx


DEFAULT_RADIUS_METERS = {
    "AIR_": 2500,
    "UNI_": 800,
    "DEFAULT": 750
}

ROUTE_DEPENDENT_FIELDS = (
    "drivingDistance", "drivingSeconds", "drivingPath", "routeSource", "routeFingerprint"
)

# One numerical contract is used by balancing and integerization. ``atol``
# absorbs floating-point noise near zero; ``rtol`` scales with each marginal.
OD_ABSOLUTE_TOLERANCE = 1e-8
OD_RELATIVE_TOLERANCE = 1e-10


def _marginal_residuals_within_tolerance(
    actual: np.ndarray,
    target: np.ndarray,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> Tuple[bool, float, float]:
    """Return shared abs+rel marginal acceptance and residual diagnostics."""
    actual_values = np.asarray(actual, dtype=np.float64)
    target_values = np.asarray(target, dtype=np.float64)
    errors = np.abs(actual_values - target_values)
    limits = absolute_tolerance + relative_tolerance * np.abs(target_values)
    accepted = bool(np.all(errors <= limits))
    max_absolute = float(np.max(errors)) if errors.size else 0.0
    nonzero = np.abs(target_values) > 0.0
    max_relative = float(np.max(errors[nonzero] / np.abs(target_values[nonzero]))) \
        if np.any(nonzero) else 0.0
    return accepted, max_absolute, max_relative


def invalidate_route_metrics(pop: Dict[str, Any]) -> None:
    """Remove metrics that are only valid for the pop's previous OD pair."""
    for field in ROUTE_DEPENDENT_FIELDS:
        pop.pop(field, None)


class IPFPConvergenceError(ValueError):
    """Raised when IPFP cannot satisfy both marginals on the permitted support."""

    def __init__(self, message: str, diagnostics: Dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


class ODFeasibilityError(ValueError):
    """Raised when hard OD marginals cannot be carried by the allowed support."""

    def __init__(self, message: str, diagnostics: Dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


class ODIntegerizationError(ValueError):
    """Raised when a continuous OD solution cannot be rounded on its support."""

    def __init__(self, message: str, diagnostics: Dict[str, Any]):
        super().__init__(message)
        self.diagnostics = diagnostics


def is_point_in_zone(
    lon: float,
    lat: float,
    zone: Dict[str, Any],
    include_boundary: bool = False,
) -> bool:
    """
    Verifica si una coordenada [lon, lat] cae dentro de una zona (polígono o bbox).
    Maneja geometrías cerradas, auto-intersecciones y optimización con prepared geometries.
    """
    if not zone or not zone.get("enabled", True):
        return False

    # Chequeo preliminar ultrarrápido por bounding box
    bbox = zone.get("bbox")
    if bbox and len(bbox) == 4:
        min_lon = min(float(bbox[0]), float(bbox[2]))
        min_lat = min(float(bbox[1]), float(bbox[3]))
        max_lon = max(float(bbox[0]), float(bbox[2]))
        max_lat = max(float(bbox[1]), float(bbox[3]))
        if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
            return False
        if zone.get("type") == "bbox" or not zone.get("coordinates"):
            return True

    # Chequeo poligonal detallado
    coords = zone.get("coordinates")
    if coords and len(coords) >= 3:
        try:
            poly = zone.get("_prepared_poly")
            if poly is None:
                pts = [list(c) for c in coords]
                if pts[0] != pts[-1]:
                    pts.append(pts[0])
                p_obj = Polygon(pts)
                if not p_obj.is_valid:
                    p_obj = p_obj.buffer(0)
                poly = prep(p_obj)
                zone["_prepared_poly"] = poly
            point = Point(lon, lat)
            return bool(poly.intersects(point) if include_boundary else poly.contains(point))
        except Exception:
            return False

    return False


def is_point_in_exclusion_zone(lon: float, lat: float, exclusion_zones: Optional[List[Dict[str, Any]]]) -> bool:
    """
    Verifica si una coordenada [lon, lat] cae dentro de alguna de las zonas de exclusión activas.
    Las zonas de exclusión descartan la simulación de demanda (población y empleo) en el área.
    """
    if not exclusion_zones:
        return False
    for zone in exclusion_zones:
        if zone and zone.get("enabled", True) and is_point_in_zone(
            lon, lat, zone, include_boundary=True
        ):
            return True
    return False


def prepare_polygon_geom(poly_data: Any) -> Optional[Any]:
    """
    Convierte una estructura de coordenadas (lista de [lon, lat], GeoJSON dict, etc.)
    en un objeto shapely preparado (prepared.prep) para chequeos espaciales vectorizados de alta velocidad.
    """
    if not poly_data:
        return None
    try:
        from shapely.geometry import Polygon, MultiPolygon, shape
        from shapely.prepared import prep
        import shapely

        p_obj = None
        if isinstance(poly_data, dict):
            if poly_data.get("type") in ("Polygon", "MultiPolygon", "Feature", "FeatureCollection"):
                if poly_data.get("type") == "FeatureCollection" and poly_data.get("features"):
                    geoms = [shape(f["geometry"]) for f in poly_data["features"] if f.get("geometry")]
                    p_obj = shapely.unary_union(geoms) if geoms else None
                elif poly_data.get("type") == "Feature" and poly_data.get("geometry"):
                    p_obj = shape(poly_data["geometry"])
                else:
                    p_obj = shape(poly_data)
            elif "coordinates" in poly_data:
                coords = poly_data["coordinates"]
                if coords and len(coords) >= 3:
                    pts = [list(c) for c in coords]
                    if pts[0] != pts[-1]:
                        pts.append(pts[0])
                    p_obj = Polygon(pts)
        elif isinstance(poly_data, list) and len(poly_data) >= 3:
            pts = [list(c) for c in poly_data]
            if pts[0] != pts[-1]:
                pts.append(pts[0])
            p_obj = Polygon(pts)

        if p_obj is not None and not p_obj.is_empty:
            if not p_obj.is_valid:
                p_obj = p_obj.buffer(0)
            return prep(p_obj)
    except Exception:
        return None
    return None


def is_point_in_prepared_polygon(lon: float, lat: float, prepared_poly: Any) -> bool:
    """Verifica si una coordenada [lon, lat] interseca un polígono preparado."""
    if prepared_poly is None:
        return True
    try:
        from shapely.geometry import Point
        pt = Point(lon, lat)
        return bool(prepared_poly.contains(pt) or prepared_poly.intersects(pt))
    except Exception:
        return False



def get_zone_multipliers_for_point(
    lon: float,
    lat: float,
    affluence_zones: Optional[List[Dict[str, Any]]]
) -> Tuple[float, float, Optional[str]]:
    """
    Determina el multiplicador de empleo y bono de alcance para un punto espacial,
    resolviendo solapamientos mediante la regla canónica MAX Priority:
    alpha = max(alpha_1, ..., alpha_k), reach_bonus = max(reach_1, ..., reach_k).
    Retorna: (multiplier, reach_bonus, matched_zone_id)
    """
    if not affluence_zones:
        return 1.0, 0.0, None

    active_mults = []
    active_reaches = []
    matched_ids = []

    for zone in affluence_zones:
        if is_point_in_zone(lon, lat, zone):
            mult = float(zone.get("multiplier", 1.0))
            reach = float(zone.get("reach_bonus", 0.0))
            active_mults.append(mult)
            active_reaches.append(reach)
            matched_ids.append(zone.get("id"))

    if not active_mults:
        return 1.0, 0.0, None

    final_mult = max(active_mults)
    final_reach = max(active_reaches)
    chosen_id = matched_ids[active_mults.index(final_mult)]
    return final_mult, final_reach, chosen_id


def build_demand_grid(
    df_denue: pd.DataFrame,
    df_cpv: pd.DataFrame,
    special_pois: List[Dict],
    roads_gdf: gpd.GeoDataFrame,
    grid_size: float = 0.0025,
    min_residents: int = 10,
    min_jobs: int = 3,
    seed: int = 42,
    affluence_zones: Optional[List[Dict]] = None,
    exclusion_zones: Optional[List[Dict]] = None,
    urban_core_polygon: Optional[Any] = None,
    restrict_demand_to_urban_core: bool = True,
    return_employment_ledger: bool = False,
) -> Union[Tuple[List[Dict], List[Dict]], Tuple[List[Dict], List[Dict], Dict[str, Any]]]:
    """
    Agrega datos de población y empleo en celdas espaciales,
    resuelve la absorción de POIs Especiales, modula Zonas de Alta Afluencia
    y realiza el snapping a la red vial.
    """
    rng = np.random.default_rng(seed)
    core_poly_prep = prepare_polygon_geom(urban_core_polygon) if (urban_core_polygon and restrict_demand_to_urban_core) else None
    denue_outside_core = 0
    cpv_outside_core = 0
    authoritative_employment = float(df_denue['calibrated_jobs'].sum()) if 'calibrated_jobs' in df_denue else 0.0
    employment_removed_exclusion = 0.0
    employment_removed_urban_core = 0.0
    employment_absorbed_by_pois = 0.0
    regular_before_affluence = 0.0
    affluence_multiplier_delta = 0.0
    target_capacity_delta = 0.0

    
    # 1. Validar unicidad estricta y resolver radios de absorción de POIs Especiales
    poi_ids = [p["id"] for p in special_pois if isinstance(p, dict) and "id" in p]
    from collections import Counter
    poi_id_counts = Counter(poi_ids)
    dup_poi_ids = [pid for pid, count in poi_id_counts.items() if count > 1]
    if dup_poi_ids:
        raise ValueError(
            f"Error de integridad en POIs: Se detectaron IDs duplicados: {dup_poi_ids}. "
            "Cada POI especial debe tener un ID único para evitar colisiones y pérdida de nodos en Subway Builder."
        )

    pois_resolved = []
    for poi in special_pois:
        if exclusion_zones and is_point_in_exclusion_zone(poi["loc"][0], poi["loc"][1], exclusion_zones):
            continue
        p_id = poi["id"]
        radius_m = poi.get("radius_m")
        if radius_m is None:
            if p_id.startswith("AIR_"):
                radius_m = DEFAULT_RADIUS_METERS["AIR_"]
            elif p_id.startswith("UNI_"):
                radius_m = DEFAULT_RADIUS_METERS["UNI_"]
            else:
                radius_m = DEFAULT_RADIUS_METERS["DEFAULT"]

        cos_lat = math.cos(math.radians(poi["loc"][1]))
        r_m = float(radius_m)
        pois_resolved.append({
            **poi,
            "radius_m": r_m,
            "cos_lat": cos_lat,
            "d_lat_max": r_m / 110574.0,
            "d_lon_max": r_m / (111320.0 * max(0.1, cos_lat)),
            "mode": poi.get("mode", "MAX").upper(),
            "denue_jobs_absorbidos": 0.0,
            "jobs_declarados": int(poi.get("jobs", 0))
        })

    # 2. Asignar DENUE a POIs Especiales o a la Malla Regular
    grid: Dict[str, Dict] = {}
    def get_grid_key(lon: float, lat: float) -> str:
        return f"{int(math.floor(lon / grid_size))}_{int(math.floor(lat / grid_size))}"

    denue_records = (
        df_denue[['lon', 'lat', 'calibrated_jobs']].to_dict('records')
        if not df_denue.empty and all(c in df_denue.columns for c in ['lon', 'lat', 'calibrated_jobs'])
        else []
    )
    for rec in denue_records:
        r_lon = float(rec['lon'])
        r_lat = float(rec['lat'])
        source_jobs = float(rec['calibrated_jobs'])
        if exclusion_zones and is_point_in_exclusion_zone(r_lon, r_lat, exclusion_zones):
            employment_removed_exclusion += source_jobs
            continue
        if core_poly_prep and not is_point_in_prepared_polygon(r_lon, r_lat, core_poly_prep):
            denue_outside_core += 1
            employment_removed_urban_core += source_jobs
            continue
        r_jobs = source_jobs
        
        # Verificar si cae dentro de algún POI especial (con prefiltro rápido de bounding box)
        matched_poi = None
        best_dist = float('inf')
        for poi in pois_resolved:
            if abs(r_lat - poi["loc"][1]) > poi["d_lat_max"]:
                continue
            if abs(r_lon - poi["loc"][0]) > poi["d_lon_max"]:
                continue
            d_lon_m = (r_lon - poi["loc"][0]) * 111_320.0 * poi["cos_lat"]
            d_lat_m = (r_lat - poi["loc"][1]) * 110_574.0
            dist_m = math.hypot(d_lon_m, d_lat_m)
            if dist_m <= poi["radius_m"] and dist_m < best_dist:
                matched_poi = poi
                best_dist = dist_m

        if matched_poi is not None:
            matched_poi["denue_jobs_absorbidos"] += r_jobs
            employment_absorbed_by_pois += r_jobs
            continue  # Se absorbe por el POI más cercano para evitar doble conteo

        # Modulación de Zonas de Alta Afluencia para empleo regular DENUE
        # (Los POIs especiales conservan estrictamente su valor manual declarado)
        regular_before_affluence += r_jobs
        if affluence_zones:
            mult, _, _ = get_zone_multipliers_for_point(r_lon, r_lat, affluence_zones)
            if mult > 1.0:
                before_multiplier = r_jobs
                r_jobs *= mult
                affluence_multiplier_delta += r_jobs - before_multiplier

        # Sumar a la malla regular con acumulación de masa humana activa
        k = get_grid_key(r_lon, r_lat)
        if k not in grid:
            grid[k] = {
                "sum_lon_w": 0.0, "sum_lat_w": 0.0, "weight": 0.0,
                "jobs": 0.0, "residents": 0.0, "pea": 0.0
            }
        cell = grid[k]
        w = max(0.1, r_jobs)
        cell["sum_lon_w"] += r_lon * w
        cell["sum_lat_w"] += r_lat * w
        cell["weight"] += w
        cell["jobs"] += r_jobs

    # 2b. Reescalado para Zonas con Cupo Objetivo Fijo (TARGET_CAPACITY)
    if affluence_zones:
        for zone in affluence_zones:
            if not zone.get("enabled", True):
                continue
            if zone.get("target_mode") == "TARGET_CAPACITY":
                target_j = float(zone.get("target_jobs", 0))
                if target_j > 0:
                    matching_keys = []
                    current_j = 0.0
                    for k, cell in grid.items():
                        c_lon = cell["sum_lon_w"] / cell["weight"] if cell["weight"] > 0 else (float(k.split("_")[0]) + 0.5) * grid_size
                        c_lat = cell["sum_lat_w"] / cell["weight"] if cell["weight"] > 0 else (float(k.split("_")[1]) + 0.5) * grid_size
                        if is_point_in_zone(c_lon, c_lat, zone) and cell["jobs"] > 0:
                            matching_keys.append(k)
                            current_j += cell["jobs"]
                    if matching_keys and current_j > 0:
                        scale = target_j / current_j
                        target_capacity_delta += target_j - current_j
                        for k in matching_keys:
                            grid[k]["jobs"] *= scale

    # 3. Sumar Población del Censo a la Malla
    cpv_records = (
        df_cpv[['lon', 'lat', 'pobtot_adj', 'pea_real']].to_dict('records')
        if not df_cpv.empty and all(c in df_cpv.columns for c in ['lon', 'lat', 'pobtot_adj', 'pea_real'])
        else []
    )
    for rec in cpv_records:
        r_lon = float(rec['lon'])
        r_lat = float(rec['lat'])
        if exclusion_zones and is_point_in_exclusion_zone(r_lon, r_lat, exclusion_zones):
            continue
        if core_poly_prep and not is_point_in_prepared_polygon(r_lon, r_lat, core_poly_prep):
            cpv_outside_core += 1
            continue
        k = get_grid_key(r_lon, r_lat)
        if k not in grid:
            grid[k] = {
                "sum_lon_w": 0.0, "sum_lat_w": 0.0, "weight": 0.0,
                "jobs": 0.0, "residents": 0.0, "pea": 0.0
            }
        cell = grid[k]
        w = max(0.1, float(rec['pobtot_adj']))
        cell["sum_lon_w"] += r_lon * w
        cell["sum_lat_w"] += r_lat * w
        cell["weight"] += w
        cell["residents"] += float(rec['pobtot_adj'])
        cell["pea"] += float(rec['pea_real'])

    if core_poly_prep:
        print(f"-> [Núcleo Urbano] Máscara de demanda activa: {cpv_outside_core:,} manzanas censales y {denue_outside_core:,} comercios fuera del núcleo excluidos.")


    # 4. Preparar Snapping Vial con STRtree (Filtrando vías peatonales / urbanas accesibles)
    if roads_gdf.crs is None:
        roads_gdf = roads_gdf.set_crs(epsg=4326)
    elif roads_gdf.crs.to_epsg() != 4326:
        roads_gdf = roads_gdf.to_crs(epsg=4326)

    if "highway" in roads_gdf.columns:
        accessible_highways = {
            "residential", "primary", "secondary", "tertiary",
            "unclassified", "service", "living_street", "pedestrian",
            "footway", "path", "track", "trunk", "trunk_link",
            "primary_link", "secondary_link", "tertiary_link"
        }
        roads_subset = roads_gdf[roads_gdf["highway"].isin(accessible_highways)]
        if len(roads_subset) > 0:
            road_geoms = [g for g in roads_subset.geometry if g is not None and not g.is_empty]
        else:
            road_geoms = [g for g in roads_gdf.geometry if g is not None and not g.is_empty]
    else:
        road_geoms = [g for g in roads_gdf.geometry if g is not None and not g.is_empty]

    tree = STRtree(road_geoms) if len(road_geoms) > 0 else None

    # 5. Consolidación de Celdas Sub-Umbral (Conservación Estricta de Masa y PEA)
    valid_cells = []
    subthreshold_cells = []
    for k, cell in grid.items():
        if cell["jobs"] >= min_jobs or cell["residents"] >= min_residents:
            valid_cells.append((k, cell))
        elif cell["jobs"] > 0 or cell["residents"] > 0 or cell["pea"] > 0:
            subthreshold_cells.append((k, cell))

    if valid_cells and subthreshold_cells:
        valid_pts_for_consolidation = [
            Point(
                c["sum_lon_w"] / c["weight"] if c["weight"] > 0 else (float(k.split("_")[0]) + 0.5) * grid_size,
                c["sum_lat_w"] / c["weight"] if c["weight"] > 0 else (float(k.split("_")[1]) + 0.5) * grid_size
            )
            for k, c in valid_cells
        ]
        cell_tree = STRtree(valid_pts_for_consolidation)
        sub_pts = [
            Point(
                c["sum_lon_w"] / c["weight"] if c["weight"] > 0 else (float(k.split("_")[0]) + 0.5) * grid_size,
                c["sum_lat_w"] / c["weight"] if c["weight"] > 0 else (float(k.split("_")[1]) + 0.5) * grid_size
            )
            for k, c in subthreshold_cells
        ]
        nearest_valid_indices = cell_tree.query_nearest(sub_pts, all_matches=False)[1]
        for sub_idx, v_idx in enumerate(nearest_valid_indices):
            target_cell = valid_cells[int(v_idx)][1]
            sub_cell = subthreshold_cells[sub_idx][1]
            target_cell["jobs"] += sub_cell["jobs"]
            target_cell["residents"] += sub_cell["residents"]
            target_cell["pea"] += sub_cell["pea"]
            target_cell["sum_lon_w"] += sub_cell["sum_lon_w"]
            target_cell["sum_lat_w"] += sub_cell["sum_lat_w"]
            target_cell["weight"] += sub_cell["weight"]
    elif not valid_cells and subthreshold_cells:
        valid_cells = subthreshold_cells

    # 6. Construir lista de demand_points regulares con Centroides Ponderados por Masa
    regular_grid_before_rounding = float(sum(cell['jobs'] for _, cell in valid_cells))
    regular_grid_expected = int(sum(int(round(cell['jobs'])) for _, cell in valid_cells))
    demand_points = []
    points_to_snap = [
        Point(
            float(cell["sum_lon_w"] / cell["weight"]) if cell["weight"] > 0 else (float(k.split("_")[0]) + 0.5) * grid_size,
            float(cell["sum_lat_w"] / cell["weight"]) if cell["weight"] > 0 else (float(k.split("_")[1]) + 0.5) * grid_size
        )
        for k, cell in valid_cells
    ]

    if tree is not None and len(points_to_snap) > 0:
        nearest_indices = tree.query_nearest(points_to_snap, all_matches=False)[1]
    else:
        nearest_indices = [None] * len(points_to_snap)

    MIN_SNAP_METERS = 5.0
    MAX_SNAP_METERS = 300.0

    for idx, ((k, cell), pt, near_idx) in enumerate(zip(valid_cells, points_to_snap, nearest_indices)):
        lon, lat = pt.x, pt.y
        if near_idx is not None and tree is not None:
            nearest_road = road_geoms[int(near_idx)]
            proj_candidate = nearest_points(pt, nearest_road)[1]
            cos_lat = math.cos(math.radians(lat))
            dx_m = (proj_candidate.x - pt.x) * 111_320.0 * cos_lat
            dy_m = (proj_candidate.y - pt.y) * 110_574.0
            dist_m = math.hypot(dx_m, dy_m)

            # Snapping vial acotado en rango 5m a 300m
            if MIN_SNAP_METERS <= dist_m <= MAX_SNAP_METERS:
                candidate_lon, candidate_lat = proj_candidate.x, proj_candidate.y
                if not is_point_in_exclusion_zone(candidate_lon, candidate_lat, exclusion_zones):
                    lon, lat = candidate_lon, candidate_lat

        if is_point_in_exclusion_zone(lon, lat, exclusion_zones):
            raise ValueError(
                f"Final demand point dp_{idx+1:04d} falls inside or on an exclusion boundary"
            )

        demand_points.append({
            "id": f"dp_{idx+1:04d}",
            "location": [round(lon, 5), round(lat, 5)],
            "jobs": int(round(cell["jobs"])),
            "residents": int(round(cell["residents"])),
            "pea_15ymas": int(round(cell["pea"])),
            "popIds": []
        })

    # 7. Resolver y agregar POIs Especiales (preservando coordenadas exactas de usuario)
    poi_audit = []
    special_poi_expected = 0
    poi_capacity_delta = 0.0
    if len(pois_resolved) > 0:
        for poi in pois_resolved:
            manual = poi["jobs_declarados"]
            absorbed = int(round(poi["denue_jobs_absorbidos"]))
            mode = poi["mode"]

            if mode == "MAX":
                final_jobs = max(manual, absorbed)
                status = "Piso DENUE" if absorbed > manual else "Valor Manual"
            elif mode in ["BOOST", "ADDITIVE"]:
                final_jobs = manual + absorbed
                status = "Suma (Exógena + DENUE)"
            elif mode == "REPLACE":
                final_jobs = manual
                status = "Sobrescritura Forzada"
            else:
                final_jobs = max(manual, absorbed)
                status = "Fallback MAX"

            special_poi_expected += int(final_jobs)
            poi_capacity_delta += float(final_jobs) - float(poi["denue_jobs_absorbidos"])

            # Los POIs manuales preservan fielmente las coordenadas elegidas por el usuario
            lon, lat = poi["loc"][0], poi["loc"][1]

            demand_points.append({
                "id": poi["id"],
                "location": [round(lon, 5), round(lat, 5)],
                "jobs": int(final_jobs),
                "residents": 0,
                "pea_15ymas": 0,
                "popIds": [],
                "is_special": True
            })

            poi_audit.append({
                "id": poi["id"],
                "mode": mode,
                "manual": manual,
                "absorbed": absorbed,
                "final_jobs": final_jobs,
                "status": status
            })

    after_explicit_removals = (
        authoritative_employment - employment_removed_exclusion - employment_removed_urban_core
    )
    employment_ledger = {
        "authoritative_calibrated_employment": authoritative_employment,
        "geographic_bbox_selected_employment": authoritative_employment,
        "removed_by_exclusion_zones": employment_removed_exclusion,
        "removed_by_urban_core": employment_removed_urban_core,
        "after_explicit_removals": after_explicit_removals,
        "absorbed_by_special_pois": employment_absorbed_by_pois,
        "regular_before_affluence": regular_before_affluence,
        "affluence_multiplier_delta": affluence_multiplier_delta,
        "target_capacity_delta": target_capacity_delta,
        "regular_grid_before_rounding": regular_grid_before_rounding,
        "grid_rounding_delta": float(regular_grid_expected) - regular_grid_before_rounding,
        "regular_grid_employment_expected": regular_grid_expected,
        "poi_capacity_delta": poi_capacity_delta,
        "special_poi_employment_expected": special_poi_expected,
        "final_employment_expected": regular_grid_expected + special_poi_expected,
        "tolerance": 1e-6,
    }
    if return_employment_ledger:
        return demand_points, poi_audit, employment_ledger
    return demand_points, poi_audit


CANONICAL_SPEED_KMH = 40.0
CANONICAL_CIRCUITY = 1.3


def calculate_commute_impedance(d_km: float) -> Tuple[int, int]:
    """
    Calcula distancia y tiempo de manejo usando el estándar canónico oficial de Subway Builder:
    - Circuidad vial canónica: distancia euclidiana x 1.3
    - Velocidad canónica a flujo libre: 40 km/h (~11.11 m/s)
    - Tiempo de manejo: max(45, int(round(dist_m / (40.0 / 3.6))))
    Documentado en Subway Builder Custom Cities / Demand Modification API.
    """
    d = max(0.0, float(d_km))
    dist_m = max(150, int(round(d * 1000.0 * CANONICAL_CIRCUITY)))
    speed_ms = CANONICAL_SPEED_KMH / 3.6
    driving_seconds = max(45, int(round(dist_m / speed_ms)))
    return dist_m, driving_seconds


class ArterialRoadIndex:
    """
    Índice de distancias y tiempos de viaje sobre la red vial arterial real (OSM).
    Permite calcular impedancias de viaje considerando obstáculos geográficos (lagunas,
    ríos, bahías, penínsulas) de forma 100% automática a partir de roads.geojson.
    """
    def __init__(
        self,
        dist_matrix: np.ndarray,
        all_nodes_coords: np.ndarray,
        all_node_to_junction: Dict[int, Tuple[int, int, int, float, float]],
        cos_lat_ref: float = 1.0,
        max_detour_ratio: float = 3.5
    ):
        self.dist_matrix = dist_matrix
        self.all_nodes_coords = all_nodes_coords
        self.all_node_to_junction = all_node_to_junction
        self.cos_lat_ref = cos_lat_ref
        self.max_detour_ratio = max_detour_ratio

        if len(all_nodes_coords) > 0:
            kdtree_coords = all_nodes_coords.copy()
            kdtree_coords[:, 0] *= cos_lat_ref
            self.kdtree = cKDTree(kdtree_coords)
        else:
            self.kdtree = None

    def get_driving_impedance(
        self,
        orig_loc: Union[List[float], Tuple[float, float], np.ndarray],
        dest_loc: Union[List[float], Tuple[float, float], np.ndarray],
        euclid_d_km: float,
        max_detour_ratio: Optional[float] = None
    ) -> Tuple[int, int]:
        """
        Calcula distancia (m) y drivingSeconds aplicando las 3 salvaguardas:
        1. Piso Físico: dist_road >= dist_euclid
        2. Techo de Desvío Acotado: dist_road <= max_detour_ratio * dist_euclid
        3. Fallback Seguro: Si no hay conexión en red vial, usa calculate_commute_impedance.
        """
        detour_ceiling = max_detour_ratio if max_detour_ratio is not None else self.max_detour_ratio
        euclid_m = max(150.0, float(euclid_d_km) * 1000.0)
        if self.kdtree is None or self.dist_matrix.size == 0:
            return calculate_commute_impedance(euclid_d_km)

        # 1. Proyectar origen y destino al nodo vial métricamente más cercano
        orig_arr = np.array([orig_loc[0] * self.cos_lat_ref, orig_loc[1]], dtype=np.float64)
        dest_arr = np.array([dest_loc[0] * self.cos_lat_ref, dest_loc[1]], dtype=np.float64)

        _, orig_node_idx = self.kdtree.query(orig_arr)
        _, dest_node_idx = self.kdtree.query(dest_arr)

        o_coord = self.all_nodes_coords[orig_node_idx]
        d_coord = self.all_nodes_coords[dest_node_idx]

        cos_lat = math.cos(math.radians((orig_loc[1] + dest_loc[1]) / 2.0))
        d_orig_road = math.hypot(
            (orig_loc[0] - o_coord[0]) * 111_320.0 * cos_lat,
            (orig_loc[1] - o_coord[1]) * 110_574.0
        )
        d_dest_road = math.hypot(
            (dest_loc[0] - d_coord[0]) * 111_320.0 * cos_lat,
            (dest_loc[1] - d_coord[1]) * 110_574.0
        )

        c1, ja1, jb1, s1, l1 = self.all_node_to_junction[orig_node_idx]
        c2, ja2, jb2, s2, l2 = self.all_node_to_junction[dest_node_idx]

        # 2. Distancia a lo largo de la red vial
        cand = []

        # Caso A: Si están sobre la misma cadena vial directa
        if c1 == c2 and c1 >= 0:
            if ja1 == jb1 and l1 > 0:
                # Bucle cerrado (self-loop)
                cand.append(min(abs(s1 - s2), l1 - abs(s1 - s2)))
            else:
                cand.append(abs(s1 - s2))

        # Caso B: Rutas a través de las intersecciones de la red
        conns1 = [(ja1, s1)]
        if (jb1 != ja1 or l1 > 0) and jb1 is not None:
            conns1.append((jb1, max(0.0, l1 - s1)))

        conns2 = [(ja2, s2)]
        if (jb2 != ja2 or l2 > 0) and jb2 is not None:
            conns2.append((jb2, max(0.0, l2 - s2)))

        for u, du in conns1:
            for v, dv in conns2:
                if u is not None and v is not None:
                    nw = float(self.dist_matrix[u, v])
                    if not math.isinf(nw) and not math.isnan(nw):
                        cand.append(du + nw + dv)

        # Salvaguarda 3: Fallback si no hay ruta conectada
        if not cand:
            return calculate_commute_impedance(euclid_d_km)

        network_m = min(cand)
        if math.isinf(network_m) or math.isnan(network_m):
            return calculate_commute_impedance(euclid_d_km)

        road_m = network_m + d_orig_road + d_dest_road

        # Salvaguarda 1: Piso Físico (dist_road >= dist_euclid)
        road_m = max(road_m, euclid_m)

        # Salvaguarda 2: Techo de Desvío Acotado (dist_road <= detour_ceiling * dist_euclid)
        road_m = min(road_m, detour_ceiling * euclid_m)

        # Velocidad canónica oficial de Subway Builder (40 km/h promedio a flujo libre)
        speed_ms = CANONICAL_SPEED_KMH / 3.6
        driving_seconds = max(45, int(round(road_m / speed_ms)))
        return int(round(road_m)), driving_seconds


def build_arterial_road_network(
    roads_gdf: gpd.GeoDataFrame,
    max_detour_ratio: float = 3.5
) -> Optional[ArterialRoadIndex]:
    """
    Construye un índice topológico de la red arterial vial a partir de un GeoDataFrame de carreteras.
    Contrae cadenas de grado 2 a intersecciones principales para resolver caminos mínimos en milisegundos.
    Retorna None si roads_gdf está vacío o carece de geometrías transitables.
    """
    if roads_gdf is None or len(roads_gdf) == 0:
        return None

    # Filtrar arterias principales
    if "roadClass" in roads_gdf.columns:
        major_gdf = roads_gdf[roads_gdf["roadClass"].isin(["major", "highway"])]
        if len(major_gdf) == 0:
            major_gdf = roads_gdf
    elif "highway" in roads_gdf.columns:
        accessible_highways = {
            "motorway", "trunk", "primary", "secondary", "tertiary",
            "motorway_link", "trunk_link", "primary_link", "secondary_link"
        }
        major_gdf = roads_gdf[roads_gdf["highway"].isin(accessible_highways)]
        if len(major_gdf) == 0:
            major_gdf = roads_gdf
    else:
        major_gdf = roads_gdf

    # Extraer segmentos de líneas
    lines = []
    for geom in major_gdf.geometry:
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "MultiLineString":
            lines.extend(geom.geoms)
        elif geom.geom_type == "LineString":
            lines.append(geom)

    if not lines:
        return None

    # Construir grafo topológico base
    G = nx.Graph()
    for geom in lines:
        coords = list(geom.coords)
        for u, v in zip(coords[:-1], coords[1:]):
            dx = (v[0] - u[0]) * 111_320.0 * math.cos(math.radians((u[1] + v[1]) / 2.0))
            dy = (v[1] - u[1]) * 110_574.0
            d = math.hypot(dx, dy)
            u_r = (round(u[0], 5), round(u[1], 5))
            v_r = (round(v[0], 5), round(v[1], 5))
            if u_r != v_r:
                prev_w = G.get_edge_data(u_r, v_r, {}).get("weight", float("inf"))
                G.add_edge(u_r, v_r, weight=min(d, prev_w))

    if G.number_of_nodes() < 2 or G.number_of_edges() == 0:
        return None

    # Identificar nodos de unión/intersección (grado != 2)
    junctions = set(n for n, d in G.degree() if d != 2)
    # Garantizar que todo componente conexo (ej. rotondas o bucles aislados de grado 2) tenga al menos una unión
    for comp in nx.connected_components(G):
        if not any(n in junctions for n in comp):
            junctions.add(next(iter(comp)))

    j_list = list(junctions)
    j_to_idx = {j: i for i, j in enumerate(j_list)}
    contracted_G = nx.Graph()
    for j in junctions:
        contracted_G.add_node(j)

    node_info = {}
    for j in junctions:
        j_idx = j_to_idx[j]
        node_info[j] = (-1, j_idx, j_idx, 0.0, 0.0)

    visited_edges = set()
    chain_id = 0
    for j in junctions:
        for neighbor in G.neighbors(j):
            edge_key = tuple(sorted([j, neighbor]))
            if edge_key in visited_edges:
                continue
            visited_edges.add(edge_key)

            curr = neighbor
            prev = j
            chain = [j, curr]
            cum_dist = [0.0, G[prev][curr]["weight"]]

            while curr not in junctions and G.degree(curr) == 2:
                next_nodes = [n for n in G.neighbors(curr) if n != prev]
                if not next_nodes:
                    break
                nxt = next_nodes[0]
                visited_edges.add(tuple(sorted([curr, nxt])))
                w = G[curr][nxt]["weight"]
                prev = curr
                curr = nxt
                chain.append(curr)
                cum_dist.append(cum_dist[-1] + w)

            total_len = cum_dist[-1]
            target_j = curr
            if target_j in junctions:
                prev_w = contracted_G.get_edge_data(j, target_j, {}).get("weight", float("inf"))
                contracted_G.add_edge(j, target_j, weight=min(total_len, prev_w))

                for idx_c, node in enumerate(chain):
                    if node not in junctions:
                        node_info[node] = (
                            chain_id,
                            j_to_idx[j],
                            j_to_idx[target_j],
                            cum_dist[idx_c],
                            total_len
                        )
                chain_id += 1

    adj = nx.to_scipy_sparse_array(contracted_G, nodelist=j_list, weight="weight", format="csr")
    dist_matrix = shortest_path(csgraph=adj, directed=False)

    all_nodes = list(G.nodes())
    all_nodes_coords = np.array(all_nodes, dtype=np.float64)
    cos_lat_ref = math.cos(math.radians(np.mean(all_nodes_coords[:, 1]))) if len(all_nodes_coords) > 0 else 1.0

    all_node_to_junction = {}
    for idx_n, node in enumerate(all_nodes):
        all_node_to_junction[idx_n] = node_info.get(node, (-1, 0, 0, 0.0, 0.0))

    return ArterialRoadIndex(
        dist_matrix=dist_matrix,
        all_nodes_coords=all_nodes_coords,
        all_node_to_junction=all_node_to_junction,
        cos_lat_ref=cos_lat_ref,
        max_detour_ratio=max_detour_ratio
    )


def assign_zones(coords: np.ndarray, isolated_zones: Optional[List[Dict]] = None) -> np.ndarray:
    """
    Asigna un ID de zona topológica a cada coordenada [lon, lat] (en grados).
    - 0: Territorio base / continental.
    - 1..K: Zonas aisladas (islas o cuencas sin conexión vial directa).
    """
    n = len(coords)
    zones = np.zeros(n, dtype=np.int32)
    if not isolated_zones:
        return zones

    lons = coords[:, 0]
    lats = coords[:, 1]
    for idx, z in enumerate(isolated_zones, start=1):
        if "bbox" in z:
            b = z["bbox"]  # [min_lon, min_lat, max_lon, max_lat]
            mask = (lons >= b[0]) & (lons <= b[2]) & (lats >= b[1]) & (lats <= b[3])
            zones[mask] = idx
        elif "polygon" in z:
            from shapely.geometry import Point
            poly = z["polygon"]
            for i in range(n):
                if poly.contains(Point(lons[i], lats[i])):
                    zones[i] = idx
    return zones


def build_od_support(
    origin_ids: List[str],
    destination_ids: List[str],
    distances_km: np.ndarray,
    origin_zones: np.ndarray,
    destination_zones: np.ndarray,
    max_distance_km: float,
    prohibited_pairs: Optional[List[Tuple[str, str]]] = None,
) -> np.ndarray:
    """Build the single authoritative support used by feasibility, IPFP and rounding."""
    distances = np.asarray(distances_km, dtype=np.float64)
    expected = (len(origin_ids), len(destination_ids))
    if distances.shape != expected:
        raise ValueError(f"distance matrix shape {distances.shape} does not match {expected}")
    support = (
        np.isfinite(distances)
        & (distances <= float(max_distance_km))
        & (np.asarray(origin_zones)[:, None] == np.asarray(destination_zones)[None, :])
    )
    prohibited = set(prohibited_pairs or [])
    origin_index = {point_id: i for i, point_id in enumerate(origin_ids)}
    destination_index = {point_id: j for j, point_id in enumerate(destination_ids)}
    for origin_id, destination_id in prohibited:
        i = origin_index.get(origin_id)
        j = destination_index.get(destination_id)
        if i is not None and j is not None:
            support[i, j] = False
    for i, origin_id in enumerate(origin_ids):
        self_j = destination_index.get(origin_id)
        if self_j is not None:
            support[i, self_j] = False
    return support


def authoritative_destination_marginals(
    employment: np.ndarray,
    total_workers: float,
) -> np.ndarray:
    """Convert employment weights once into the global hard destination marginal."""
    weights = np.asarray(employment, dtype=np.float64)
    if weights.ndim != 1 or not np.all(np.isfinite(weights)) or np.any(weights < 0):
        raise ValueError("destination employment must be a finite nonnegative vector")
    total_employment = float(weights.sum())
    if total_workers < 0 or not np.isfinite(total_workers):
        raise ValueError("total worker mass must be finite and nonnegative")
    if total_workers > 0 and total_employment <= 0:
        raise ValueError("positive worker mass requires positive destination employment")
    if total_workers == 0:
        return np.zeros_like(weights)
    return weights * (float(total_workers) / total_employment)


def validate_od_feasibility(
    origin_marginals: np.ndarray,
    destination_marginals: np.ndarray,
    support: np.ndarray,
    origin_ids: Optional[List[str]] = None,
    destination_ids: Optional[List[str]] = None,
    tolerance: float = 1e-8,
) -> Dict[str, Any]:
    """Validate hard marginals and certify transportation feasibility with max flow."""
    started_at = time.perf_counter()
    origins = np.asarray(origin_marginals, dtype=np.float64)
    destinations = np.asarray(destination_marginals, dtype=np.float64)
    allowed = np.asarray(support, dtype=bool)
    origin_count = int(origins.shape[0]) if origins.ndim == 1 else int(origins.size)
    destination_count = int(destinations.shape[0]) if destinations.ndim == 1 else int(destinations.size)
    origin_ids = list(origin_ids if origin_ids is not None else [str(i) for i in range(origin_count)])
    destination_ids = list(
        destination_ids if destination_ids is not None else [str(j) for j in range(destination_count)]
    )
    diagnostics: Dict[str, Any] = {
        "origin_count": origin_count,
        "destination_count": destination_count,
        "support_vertex_count": origin_count + destination_count,
        "support_edge_count": int(np.count_nonzero(allowed)) if allowed.ndim == 2 else 0,
    }

    if origins.ndim != 1 or destinations.ndim != 1 or allowed.shape != (origin_count, destination_count):
        diagnostics["support_shape"] = tuple(allowed.shape)
        raise ODFeasibilityError("OD shape validation failed", diagnostics)
    if len(origin_ids) != origin_count or len(destination_ids) != destination_count:
        raise ODFeasibilityError("OD identifier shape validation failed", diagnostics)
    if not np.all(np.isfinite(origins)) or not np.all(np.isfinite(destinations)):
        raise ODFeasibilityError("OD marginals must be finite", diagnostics)
    if np.any(origins < -tolerance) or np.any(destinations < -tolerance):
        raise ODFeasibilityError("OD marginals must be nonnegative", diagnostics)
    origins = np.where(np.abs(origins) <= tolerance, 0.0, origins)
    destinations = np.where(np.abs(destinations) <= tolerance, 0.0, destinations)
    origin_total = float(origins.sum())
    destination_total = float(destinations.sum())
    diagnostics.update({"origin_total": origin_total, "destination_total": destination_total})
    if abs(origin_total - destination_total) > tolerance * max(1.0, origin_total, destination_total):
        diagnostics["total_mass_deficit"] = origin_total - destination_total
        raise ODFeasibilityError("OD origin and destination totals differ", diagnostics)
    if origin_total == 0.0:
        diagnostics.update({
            "unsupported_origin_ids": [],
            "unsupported_destination_ids": [],
            "components": [],
            "max_flow": 0.0,
            "max_flow_shortfall": 0.0,
            "max_flow_seconds": 0.0,
            "feasible": True,
            "validation_seconds": time.perf_counter() - started_at,
        })
        return diagnostics

    active_origins = origins > tolerance
    active_destinations = destinations > tolerance
    unsupported_origins = [
        origin_ids[i] for i in np.where(active_origins & ~np.any(allowed[:, active_destinations], axis=1))[0]
    ]
    unsupported_destinations = [
        destination_ids[j] for j in np.where(active_destinations & ~np.any(allowed[active_origins, :], axis=0))[0]
    ]
    diagnostics.update({
        "unsupported_origin_ids": unsupported_origins,
        "unsupported_destination_ids": unsupported_destinations,
    })
    if unsupported_origins or unsupported_destinations:
        raise ODFeasibilityError(
            "Positive-mass OD vertices have zero allowed degree "
            f"(origins={unsupported_origins}, destinations={unsupported_destinations})",
            diagnostics,
        )

    graph = nx.Graph()
    graph.add_nodes_from(("o", i) for i in np.where(active_origins)[0])
    graph.add_nodes_from(("d", j) for j in np.where(active_destinations)[0])
    for i, j in np.argwhere(allowed & active_origins[:, None] & active_destinations[None, :]):
        graph.add_edge(("o", int(i)), ("d", int(j)))
    components = []
    unequal_components = []
    for component_index, nodes in enumerate(nx.connected_components(graph)):
        o_idx = sorted(node[1] for node in nodes if node[0] == "o")
        d_idx = sorted(node[1] for node in nodes if node[0] == "d")
        o_mass = float(origins[o_idx].sum())
        d_mass = float(destinations[d_idx].sum())
        item = {
            "component": component_index,
            "origin_ids": [origin_ids[i] for i in o_idx],
            "destination_ids": [destination_ids[j] for j in d_idx],
            "origin_mass": o_mass,
            "destination_mass": d_mass,
            "deficit": o_mass - d_mass,
        }
        components.append(item)
        if abs(o_mass - d_mass) > tolerance * max(1.0, o_mass, d_mass):
            unequal_components.append(item)
    diagnostics["components"] = components
    if unequal_components:
        diagnostics["unequal_components"] = unequal_components
        raise ODFeasibilityError("Disconnected OD component masses differ", diagnostics)

    source, sink = ("source", -1), ("sink", -1)
    flow_graph = nx.DiGraph()
    for i in np.where(active_origins)[0]:
        flow_graph.add_edge(source, ("o", int(i)), capacity=float(origins[i]))
    for i, j in np.argwhere(allowed & active_origins[:, None] & active_destinations[None, :]):
        flow_graph.add_edge(("o", int(i)), ("d", int(j)), capacity=origin_total)
    for j in np.where(active_destinations)[0]:
        flow_graph.add_edge(("d", int(j)), sink, capacity=float(destinations[j]))
    flow_started_at = time.perf_counter()
    flow_value, _ = nx.maximum_flow(flow_graph, source, sink)
    diagnostics["max_flow_seconds"] = time.perf_counter() - flow_started_at
    diagnostics["max_flow"] = float(flow_value)
    diagnostics["max_flow_shortfall"] = float(origin_total - flow_value)
    if origin_total - flow_value > tolerance * max(1.0, origin_total):
        _, partition = nx.minimum_cut(flow_graph, source, sink)
        reachable, non_reachable = partition
        witness_origins = sorted(origin_ids[n[1]] for n in reachable if isinstance(n, tuple) and n[0] == "o")
        witness_destinations = sorted(destination_ids[n[1]] for n in reachable if isinstance(n, tuple) and n[0] == "d")
        affected_destinations = sorted(destination_ids[n[1]] for n in non_reachable if isinstance(n, tuple) and n[0] == "d")
        diagnostics["minimum_cut_witness"] = {
            "reachable_origin_ids": witness_origins,
            "reachable_destination_ids": witness_destinations,
            "affected_destination_ids": affected_destinations,
        }
        diagnostics["affected_ids"] = sorted(set(witness_origins + affected_destinations))
        diagnostics["validation_seconds"] = time.perf_counter() - started_at
        raise ODFeasibilityError("OD support fails transportation max-flow feasibility", diagnostics)
    diagnostics["feasible"] = True
    diagnostics["validation_seconds"] = time.perf_counter() - started_at
    return diagnostics


def integerize_od_matrix(
    continuous: np.ndarray,
    origin_marginals: np.ndarray,
    destination_marginals: np.ndarray,
    support: np.ndarray,
    tolerance: float = OD_ABSOLUTE_TOLERANCE,
    relative_tolerance: float = OD_RELATIVE_TOLERANCE,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Deterministically round a feasible OD table by min-cost residual flow."""
    started_at = time.perf_counter()
    table = np.asarray(continuous, dtype=np.float64)
    origins_f = np.asarray(origin_marginals, dtype=np.float64)
    destinations = np.asarray(destination_marginals, dtype=np.float64)
    allowed = np.asarray(support, dtype=bool)
    if tolerance < 0 or relative_tolerance < 0:
        raise ValueError("OD tolerances must be nonnegative")
    if origins_f.ndim != 1 or destinations.ndim != 1:
        raise ODIntegerizationError("Integerization marginals must be vectors", {})
    if table.shape != allowed.shape or table.shape != (origins_f.size, destinations.size):
        raise ODIntegerizationError("Integerization shape mismatch", {"shape": table.shape})
    if not np.all(np.isfinite(table)):
        raise ODIntegerizationError("Continuous OD matrix must contain only finite values", {})
    if not np.all(np.isfinite(origins_f)) or not np.all(np.isfinite(destinations)):
        raise ODIntegerizationError("Integerization marginals must be finite", {})
    if np.any(origins_f < -tolerance) or np.any(destinations < -tolerance):
        raise ODIntegerizationError("Integerization marginals must be nonnegative", {})
    if np.any(table < -tolerance):
        raise ODIntegerizationError(
            "Continuous OD matrix contains materially negative values",
            {"minimum_continuous_value": float(np.min(table))},
        )
    origins_f = np.where(origins_f < 0.0, 0.0, origins_f)
    destinations = np.where(destinations < 0.0, 0.0, destinations)
    table = np.where(table < 0.0, 0.0, table)
    if np.any(np.abs(origins_f - np.rint(origins_f)) > tolerance):
        raise ODIntegerizationError("Origin marginals must be integral", {})
    origins = np.rint(origins_f).astype(np.int64)
    if np.any(table[~allowed] > tolerance):
        raise ODIntegerizationError("Continuous flow uses forbidden support", {})
    table = np.where(allowed, table, 0.0)
    rows_accepted, row_error, row_relative_error = _marginal_residuals_within_tolerance(
        table.sum(axis=1), origins_f, tolerance, relative_tolerance
    )
    columns_accepted, col_error, column_relative_error = _marginal_residuals_within_tolerance(
        table.sum(axis=0), destinations, tolerance, relative_tolerance
    )
    if not rows_accepted or not columns_accepted:
        raise ODIntegerizationError(
            "Continuous OD marginals are not tight enough for integerization",
            {
                "max_row_absolute_error": row_error,
                "max_column_absolute_error": col_error,
                "max_row_relative_error": row_relative_error,
                "max_column_relative_error": column_relative_error,
                "absolute_tolerance": float(tolerance),
                "relative_tolerance": float(relative_tolerance),
            },
        )

    snapped = np.where(np.abs(table - np.rint(table)) <= tolerance, np.rint(table), table)
    lower = np.floor(snapped).astype(np.int64)
    fractions = snapped - lower
    row_residual = origins - lower.sum(axis=1)
    destination_lower = np.floor(destinations + tolerance).astype(np.int64)
    destination_upper = np.ceil(destinations - tolerance).astype(np.int64)
    col_lower_residual = destination_lower - lower.sum(axis=0)
    col_upper_residual = destination_upper - lower.sum(axis=0)
    if np.any(row_residual < 0) or np.any(col_lower_residual < 0) or np.any(col_upper_residual < col_lower_residual):
        raise ODIntegerizationError("Invalid floor residuals", {})

    residual_total = int(row_residual.sum())
    if not (int(col_lower_residual.sum()) <= residual_total <= int(col_upper_residual.sum())):
        raise ODIntegerizationError("Destination apportionment bounds cannot match origin total", {})
    graph = nx.DiGraph()
    source, sink = "source", "sink"
    graph.add_node(source, demand=-residual_total)
    graph.add_node(sink, demand=residual_total - int(col_lower_residual.sum()))
    for i, supply in enumerate(row_residual):
        graph.add_node(("o", i), demand=0)
        graph.add_edge(source, ("o", i), capacity=int(supply), weight=0)
    edge_positions = [(int(i), int(j)) for i, j in np.argwhere(allowed & (fractions > tolerance))]
    tie_span = max(1, len(edge_positions) * max(1, residual_total) + 1)
    for rank, (i, j) in enumerate(edge_positions):
        delta = 1.0 - 2.0 * float(fractions[i, j])
        primary = int(round(delta * 1_000_000_000))
        graph.add_edge(("o", i), ("d", j), capacity=1, weight=primary * tie_span + rank)
    for j in range(len(destinations)):
        graph.add_node(("d", j), demand=int(col_lower_residual[j]))
        graph.add_edge(
            ("d", j), sink,
            capacity=int(col_upper_residual[j] - col_lower_residual[j]),
            weight=0,
        )
    flow_started_at = time.perf_counter()
    try:
        flow = nx.min_cost_flow(graph)
    except (nx.NetworkXUnfeasible, nx.NetworkXError) as exc:
        raise ODIntegerizationError(
            "Residual OD rounding flow is infeasible",
            {"residual_total": residual_total, "fractional_edge_count": len(edge_positions)},
        ) from exc
    result = lower.copy()
    for i, j in edge_positions:
        result[i, j] += int(flow[("o", i)].get(("d", j), 0))
    if np.any(result < 0):
        raise ODIntegerizationError("Integerized OD matrix contains negative values", {})
    if not np.array_equal(result.sum(axis=1), origins):
        raise ODIntegerizationError("Integerized row marginals are not exact", {})
    if np.any(result[~allowed] != 0) or np.any((result != lower) & (result != lower + 1)):
        raise ODIntegerizationError("Integerized cells violate support or floor/ceil bounds", {})
    integer_destinations = result.sum(axis=0)
    if np.any(integer_destinations < destination_lower) or np.any(integer_destinations > destination_upper):
        raise ODIntegerizationError("Integerized destination apportionment violates floor/ceil", {})
    diagnostics = {
        "residual_units": residual_total,
        "fractional_edge_count": len(edge_positions),
        "integer_destination_marginals": integer_destinations.tolist(),
        "absolute_rounding_error": float(np.abs(result - table).sum()),
        "absolute_tolerance": float(tolerance),
        "relative_tolerance": float(relative_tolerance),
        "min_cost_flow_seconds": time.perf_counter() - flow_started_at,
        "integerization_seconds": time.perf_counter() - started_at,
    }
    return result, diagnostics


def pack_od_cohorts(
    flow: int,
    min_pop_size: int,
    target_pop_size: int,
    max_pop_size: int,
) -> Tuple[List[int], Dict[str, Any]]:
    """Pack one exact OD cell without changing its origin or destination."""
    n = int(flow)
    if n < 0 or max_pop_size <= 0 or min_pop_size <= 0:
        raise ValueError("invalid cohort packing bounds")
    if n == 0:
        return [], {"undersized": False, "undersized_sizes": []}
    target = min(max(1, int(target_pop_size)), int(max_pop_size))
    minimum_k = int(math.ceil(n / max_pop_size))
    maximum_k = n // min_pop_size
    feasible_minimum = minimum_k <= maximum_k
    if feasible_minimum:
        ideal = n / target
        candidates = range(minimum_k, maximum_k + 1)
        k = min(candidates, key=lambda value: (abs(value - ideal), value))
        base, remainder = divmod(n, k)
        sizes = [base + (1 if index < remainder else 0) for index in range(k)]
    else:
        full_chunks, remainder = divmod(n, max_pop_size)
        sizes = [max_pop_size] * full_chunks
        if remainder:
            sizes.append(remainder)
    undersized = [size for size in sizes if size < min_pop_size]
    return sizes, {
        "undersized": bool(undersized),
        "undersized_sizes": undersized,
        "minimum_was_mathematically_feasible": feasible_minimum,
    }


def _reduce_support_to_feasible_face(
    origin_marginals: np.ndarray,
    destination_marginals: np.ndarray,
    support: np.ndarray,
    absolute_tolerance: float,
    relative_tolerance: float,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Remove allowed edges that are zero in every feasible transportation table.

    Starting from one max-flow witness, a zero edge can carry positive flow in
    another feasible table exactly when it belongs to an alternating residual
    cycle. Those edges are identified by strongly connected components in the
    bipartite residual graph. This leaves the relative interior of the actual
    feasible face, where positive-initialized IPFP is well-defined.
    """
    origins = np.asarray(origin_marginals, dtype=np.float64)
    destinations = np.asarray(destination_marginals, dtype=np.float64)
    allowed = np.asarray(support, dtype=bool)
    active_rows = origins > 0.0
    active_columns = destinations > 0.0
    total = float(origins.sum())
    face_support = np.zeros_like(allowed)
    if total == 0.0:
        return face_support, {
            "structurally_forced_zero_edges": int(np.count_nonzero(allowed)),
            "feasible_face_edge_count": 0,
        }

    source, sink = ("source", -1), ("sink", -1)
    flow_graph = nx.DiGraph()
    for i in np.where(active_rows)[0]:
        flow_graph.add_edge(source, ("o", int(i)), capacity=float(origins[i]))
    active_allowed = allowed & active_rows[:, None] & active_columns[None, :]
    for i, j in np.argwhere(active_allowed):
        flow_graph.add_edge(("o", int(i)), ("d", int(j)), capacity=total)
    for j in np.where(active_columns)[0]:
        flow_graph.add_edge(("d", int(j)), sink, capacity=float(destinations[j]))
    flow_value, flow = nx.maximum_flow(flow_graph, source, sink)
    accepted, absolute_shortfall, relative_shortfall = _marginal_residuals_within_tolerance(
        np.asarray([flow_value]),
        np.asarray([total]),
        absolute_tolerance,
        relative_tolerance,
    )
    if not accepted:
        raise ODFeasibilityError(
            "Could not construct a feasible transportation-face witness",
            {
                "max_flow": float(flow_value),
                "max_flow_shortfall": absolute_shortfall,
                "relative_shortfall": relative_shortfall,
            },
        )

    witness = np.zeros_like(allowed, dtype=np.float64)
    for i, j in np.argwhere(active_allowed):
        witness[i, j] = float(flow.get(("o", int(i)), {}).get(("d", int(j)), 0.0))

    residual = nx.DiGraph()
    residual.add_nodes_from(("o", int(i)) for i in np.where(active_rows)[0])
    residual.add_nodes_from(("d", int(j)) for j in np.where(active_columns)[0])
    for i, j in np.argwhere(active_allowed):
        origin_node = ("o", int(i))
        destination_node = ("d", int(j))
        residual.add_edge(origin_node, destination_node)
        if witness[i, j] > 0.0:
            residual.add_edge(destination_node, origin_node)
    component_by_node: Dict[Tuple[str, int], int] = {}
    for component_index, component in enumerate(nx.strongly_connected_components(residual)):
        for node in component:
            component_by_node[node] = component_index

    for i, j in np.argwhere(active_allowed):
        positive_in_witness = witness[i, j] > 0.0
        on_alternating_cycle = (
            component_by_node[("o", int(i))] == component_by_node[("d", int(j))]
        )
        face_support[i, j] = positive_in_witness or on_alternating_cycle

    forced_zero = active_allowed & ~face_support
    return face_support, {
        "structurally_forced_zero_edges": int(np.count_nonzero(forced_zero)),
        "feasible_face_edge_count": int(np.count_nonzero(face_support)),
    }


def furness_ipfp_balance(
    orig_pea: np.ndarray,
    dest_jobs: np.ndarray,
    dist_km_mat: np.ndarray,
    reach_bonuses: Optional[np.ndarray] = None,
    beta: float = 0.12,
    max_distance_km: float = 55.0,
    max_iter: int = 1000,
    tol: float = OD_RELATIVE_TOLERANCE,
    absolute_tolerance: float = OD_ABSOLUTE_TOLERANCE,
    allowed_support: Optional[np.ndarray] = None,
    return_diagnostics: bool = False,
) -> Union[np.ndarray, Tuple[np.ndarray, Dict[str, Any]]]:
    """
    Balance hard origin and destination marginals on an already-defined support.

    Returns the continuous OD flow matrix T. It never rescales destination
    marginals and never creates an edge outside ``allowed_support``.
    """
    started_at = time.perf_counter()
    o_target = np.asarray(orig_pea, dtype=np.float64)
    d_target = np.asarray(dest_jobs, dtype=np.float64)
    dist = np.asarray(dist_km_mat, dtype=np.float64)
    n_orig = int(o_target.shape[0]) if o_target.ndim == 1 else int(o_target.size)
    n_dest = int(d_target.shape[0]) if d_target.ndim == 1 else int(d_target.size)
    support = (
        np.asarray(dist <= max_distance_km, dtype=bool)
        if allowed_support is None else np.asarray(allowed_support, dtype=bool)
    )
    if dist.shape != support.shape:
        raise ODFeasibilityError(
            "OD distance and support shapes differ",
            {"distance_shape": tuple(dist.shape), "support_shape": tuple(support.shape)},
        )
    if tol < 0 or absolute_tolerance < 0:
        raise ValueError("OD tolerances must be nonnegative")
    if reach_bonuses is not None and np.asarray(reach_bonuses).shape != (n_dest,):
        raise ODFeasibilityError(
            "Destination reach bonus shape mismatch",
            {"reach_bonus_shape": tuple(np.asarray(reach_bonuses).shape)},
        )
    feasibility = validate_od_feasibility(o_target, d_target, support)
    total_o = float(o_target.sum())
    total_d = float(d_target.sum())
    if total_o <= 0:
        result = np.zeros((n_orig, n_dest), dtype=np.float64)
        diagnostics = {"converged": True, "iterations": 0,
                       "max_row_residual": 0.0, "max_column_residual": 0.0,
                       "feasibility": feasibility,
                       "ipfp_seconds": time.perf_counter() - started_at}
        return (result, diagnostics) if return_diagnostics else result
    if reach_bonuses is not None and np.any(reach_bonuses > 0):
        clamped_reach = np.clip(reach_bonuses, 0.0, 0.60).astype(np.float64)
        eff_beta = beta * (1.0 - clamped_reach)
        friction = np.exp(-eff_beta[np.newaxis, :] * dist)
        local_mask = dist <= 3.0
        friction_base = np.exp(-beta * dist)
        friction = np.where(local_mask & (friction < friction_base), friction_base, friction)
    else:
        friction = np.exp(-beta * dist)

    face_support, face_diagnostics = _reduce_support_to_feasible_face(
        o_target, d_target, support, absolute_tolerance, tol
    )
    friction[~face_support] = 0.0

    active_rows = o_target > 0.0
    active_cols = d_target > 0.0
    t_mat = (o_target[:, np.newaxis] * d_target[np.newaxis, :]) * friction
    converged = False
    iterations = 0
    max_row_residual = float("inf")
    max_column_residual = float("inf")
    max_row_absolute_residual = float("inf")
    max_column_absolute_residual = float("inf")
    for iteration in range(1, max_iter + 1):
        r_sum = t_mat.sum(axis=1)
        r_scale = np.ones_like(o_target)
        r_scale[active_rows] = o_target[active_rows] / r_sum[active_rows]
        t_mat *= r_scale[:, np.newaxis]

        c_sum = t_mat.sum(axis=0)
        c_scale = np.ones_like(d_target)
        c_scale[active_cols] = d_target[active_cols] / c_sum[active_cols]
        t_mat *= c_scale[np.newaxis, :]

        r_current = t_mat.sum(axis=1)
        c_current = t_mat.sum(axis=0)
        rows_accepted, max_row_absolute_residual, max_row_residual = \
            _marginal_residuals_within_tolerance(
                r_current, o_target, absolute_tolerance, tol
            )
        columns_accepted, max_column_absolute_residual, max_column_residual = \
            _marginal_residuals_within_tolerance(
                c_current, d_target, absolute_tolerance, tol
            )
        iterations = iteration
        if rows_accepted and columns_accepted:
            converged = True
            break

    diagnostics = {
        "converged": converged,
        "iterations": iterations,
        "max_row_residual": max_row_residual,
        "max_column_residual": max_column_residual,
        "max_row_absolute_residual": max_row_absolute_residual,
        "max_column_absolute_residual": max_column_absolute_residual,
        "absolute_tolerance": float(absolute_tolerance),
        "relative_tolerance": float(tol),
        **face_diagnostics,
        "feasibility": feasibility,
        "ipfp_seconds": time.perf_counter() - started_at,
    }
    if not converged:
        raise IPFPConvergenceError(
            "IPFP did not converge after "
            f"{iterations} iterations (max row residual={max_row_residual:.6g}, "
            f"max column residual={max_column_residual:.6g})",
            diagnostics,
        )

    t_mat[~face_support] = 0.0
    final_rows = t_mat.sum(axis=1)
    final_cols = t_mat.sum(axis=0)
    rows_accepted, diagnostics["max_row_absolute_residual"], diagnostics["max_row_residual"] = \
        _marginal_residuals_within_tolerance(
            final_rows, o_target, absolute_tolerance, tol
        )
    columns_accepted, diagnostics["max_column_absolute_residual"], diagnostics["max_column_residual"] = \
        _marginal_residuals_within_tolerance(
            final_cols, d_target, absolute_tolerance, tol
        )
    if not rows_accepted or not columns_accepted:
        diagnostics["converged"] = False
        raise IPFPConvergenceError(
            "IPFP final normalization invalidated a marginal "
            f"(max row residual={diagnostics['max_row_residual']:.6g}, "
            f"max column residual={diagnostics['max_column_residual']:.6g})",
            diagnostics,
        )

    return (t_mat, diagnostics) if return_diagnostics else t_mat


def simulate_gravity_demand(
    demand_points: List[Dict],
    beta: float = 0.12,
    max_distance_km: float = 55.0,
    min_pop_size: int = 25,
    max_pop_size: int = 200,
    target_pop_size: int = 180,
    seed: int = 42,
    isolated_zones: Optional[List[Dict]] = None,
    affluence_zones: Optional[List[Dict]] = None,
    furness_iterations: int = 1000,
    furness_tol: float = 1e-10,
    road_index: Optional["ArterialRoadIndex"] = None,
    prohibited_pairs: Optional[List[Tuple[str, str]]] = None,
    integerization_tol: float = 1e-8,
    return_diagnostics: bool = False,
) -> Union[List[Dict], Tuple[List[Dict], Dict[str, Any]]]:
    """Allocate one hard-marginal OD table, integerize it, then pack exact cells."""
    del seed  # The approved allocation is deliberately deterministic.
    origins = [point for point in demand_points if point.get("pea_15ymas", 0) > 0]
    destinations = [point for point in demand_points if point.get("jobs", 0) > 0]
    if not origins:
        raise ValueError("No se encontraron orígenes residenciales con PEA > 0.")
    if not destinations:
        raise ODFeasibilityError("Positive origins have no destinations", {})

    origin_ids = [str(point["id"]) for point in origins]
    destination_ids = [str(point["id"]) for point in destinations]
    origin_marginals = np.asarray([point["pea_15ymas"] for point in origins], dtype=np.float64)
    employment = np.asarray([point["jobs"] for point in destinations], dtype=np.float64)
    destination_marginals = authoritative_destination_marginals(
        employment, float(origin_marginals.sum())
    )
    origin_coordinates_degrees = np.asarray([point["location"] for point in origins], dtype=np.float64)
    destination_coordinates_degrees = np.asarray(
        [point["location"] for point in destinations], dtype=np.float64
    )
    origin_coordinates = np.radians(origin_coordinates_degrees)
    destination_coordinates = np.radians(destination_coordinates_degrees)
    dlat = destination_coordinates[:, 1][None, :] - origin_coordinates[:, 1][:, None]
    dlon = destination_coordinates[:, 0][None, :] - origin_coordinates[:, 0][:, None]
    haversine = (
        np.sin(dlat / 2.0) ** 2
        + np.cos(origin_coordinates[:, 1])[:, None]
        * np.cos(destination_coordinates[:, 1])[None, :]
        * np.sin(dlon / 2.0) ** 2
    )
    distances = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(haversine), 0.0, 1.0))
    origin_zones = assign_zones(origin_coordinates_degrees, isolated_zones)
    destination_zones = assign_zones(destination_coordinates_degrees, isolated_zones)
    support = build_od_support(
        origin_ids,
        destination_ids,
        distances,
        origin_zones,
        destination_zones,
        max_distance_km,
        prohibited_pairs=prohibited_pairs,
    )
    feasibility = validate_od_feasibility(
        origin_marginals,
        destination_marginals,
        support,
        origin_ids=origin_ids,
        destination_ids=destination_ids,
    )

    reach_bonuses = np.zeros(len(destinations), dtype=np.float64)
    if affluence_zones:
        for destination_index, destination in enumerate(destinations):
            _, reach_bonus, _ = get_zone_multipliers_for_point(
                destination["location"][0], destination["location"][1], affluence_zones
            )
            reach_bonuses[destination_index] = reach_bonus
    continuous, ipfp = furness_ipfp_balance(
        origin_marginals,
        destination_marginals,
        distances,
        reach_bonuses=reach_bonuses,
        beta=beta,
        max_distance_km=max_distance_km,
        max_iter=furness_iterations,
        tol=min(float(furness_tol), float(integerization_tol)),
        absolute_tolerance=float(integerization_tol),
        allowed_support=support,
        return_diagnostics=True,
    )
    integer_od, integerization = integerize_od_matrix(
        continuous,
        origin_marginals,
        destination_marginals,
        support,
        tolerance=integerization_tol,
        relative_tolerance=min(float(furness_tol), float(integerization_tol)),
    )

    integer_origin_marginals = np.rint(origin_marginals).astype(np.int64)
    integer_destination_marginals = integer_od.sum(axis=0)
    origin_totals_by_id = dict(zip(origin_ids, integer_origin_marginals.tolist()))
    destination_totals_by_id = dict(zip(destination_ids, integer_destination_marginals.tolist()))
    for point in demand_points:
        point_id = str(point["id"])
        point["residents"] = int(origin_totals_by_id.get(point_id, 0))
        point["jobs"] = int(destination_totals_by_id.get(point_id, 0))
        point["popIds"] = []
        point["_authoritative_od_residents"] = point["residents"]
        point["_authoritative_od_jobs"] = point["jobs"]

    pops: List[Dict[str, Any]] = []
    undersized = []
    pop_id = 1
    for origin_index, destination_index in np.argwhere(integer_od > 0):
        cell_flow = int(integer_od[origin_index, destination_index])
        cohort_sizes, packing = pack_od_cohorts(
            cell_flow, min_pop_size, target_pop_size, max_pop_size
        )
        origin = origins[int(origin_index)]
        destination = destinations[int(destination_index)]
        distance_km = float(distances[origin_index, destination_index])
        if road_index is not None:
            distance_m, driving_seconds = road_index.get_driving_impedance(
                origin["location"], destination["location"], distance_km
            )
        else:
            distance_m, driving_seconds = calculate_commute_impedance(distance_km)
        for cohort_size in cohort_sizes:
            identifier = f"pop_{pop_id:06d}"
            pop = {
                "id": identifier,
                "size": int(cohort_size),
                "residenceId": origin_ids[int(origin_index)],
                "jobId": destination_ids[int(destination_index)],
                "drivingSeconds": driving_seconds,
                "drivingDistance": distance_m,
            }
            pops.append(pop)
            origin["popIds"].append(identifier)
            if destination is not origin:
                destination["popIds"].append(identifier)
            pop_id += 1
        if packing["undersized"]:
            undersized.append({
                "origin_id": origin_ids[int(origin_index)],
                "destination_id": destination_ids[int(destination_index)],
                "cell_flow": cell_flow,
                "cohort_sizes": cohort_sizes,
                "undersized_sizes": packing["undersized_sizes"],
            })

    cohort_od = np.zeros_like(integer_od)
    origin_index_by_id = {point_id: index for index, point_id in enumerate(origin_ids)}
    destination_index_by_id = {point_id: index for index, point_id in enumerate(destination_ids)}
    for pop in pops:
        cohort_od[origin_index_by_id[pop["residenceId"]], destination_index_by_id[pop["jobId"]]] += int(pop["size"])
    if not np.array_equal(cohort_od, integer_od):
        raise AssertionError("Per-OD cohort totals differ from the authoritative integer OD table")
    if any(int(pop["size"]) <= 0 or int(pop["size"]) > max_pop_size for pop in pops):
        raise AssertionError("Cohort packing violated the hard maximum")

    diagnostics = {
        "origin_ids": origin_ids,
        "destination_ids": destination_ids,
        "support": support,
        "continuous_od": continuous,
        "integer_od": integer_od,
        "destination_marginals_continuous": destination_marginals,
        "destination_marginals_integer": integer_destination_marginals,
        "feasibility": feasibility,
        "ipfp": ipfp,
        "integerization": integerization,
        "undersized_cohorts": undersized,
    }
    return (pops, diagnostics) if return_diagnostics else pops


def merge_identical_commutes(
    pops: List[Dict],
    min_pop_size: int = 25,
    max_pop_size: int = 200,
    target_pop_size: Optional[int] = None,
    include_driving_path: Optional[bool] = None
) -> List[Dict]:
    """
    Agrupa y fusiona cohortes con los mismos nodos de origen y destino exactos (residenceId, jobId).
    Si la suma de personas supera max_pop_size, genera trozos balanceados de hasta max_pop_size,
    asegurando que ninguna cohorte dividida sea menor a min_pop_size.
    Calcula distancias y tiempos de manejo ponderados por tamaño de cohorte.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for p in pops:
        key = (p["residenceId"], p["jobId"])
        groups[key].append(p)

    merged_pops = []
    pop_counter = 1
    for (res_id, job_id), pop_list in groups.items():
        total_size = sum(p["size"] for p in pop_list)
        if total_size <= 0:
            continue

        weights = [p["size"] for p in pop_list]
        tot_w = sum(weights)
        has_complete_metrics = all(
            "drivingSeconds" in p and "drivingDistance" in p for p in pop_list
        )
        if tot_w > 0 and has_complete_metrics:
            avg_sec = int(round(sum(p.get("drivingSeconds", 0) * w for p, w in zip(pop_list, weights)) / tot_w))
            avg_dist = int(round(sum(p.get("drivingDistance", 0) * w for p, w in zip(pop_list, weights)) / tot_w))
        elif has_complete_metrics:
            avg_sec = pop_list[0].get("drivingSeconds", 0)
            avg_dist = pop_list[0].get("drivingDistance", 0)

        path = next((p["drivingPath"] for p in pop_list if "drivingPath" in p and p["drivingPath"]), None) if include_driving_path is not False and has_complete_metrics else None

        chunks, _ = pack_od_cohorts(
            total_size,
            min_pop_size=min_pop_size,
            target_pop_size=target_pop_size if target_pop_size is not None else max_pop_size,
            max_pop_size=max_pop_size,
        )

        for sz in chunks:
            item = {
                "id": f"pop_{pop_counter:06d}",
                "size": sz,
                "residenceId": res_id,
                "jobId": job_id,
            }
            if has_complete_metrics:
                item["drivingSeconds"] = avg_sec
                item["drivingDistance"] = avg_dist
            if path:
                item["drivingPath"] = path
            merged_pops.append(item)
            pop_counter += 1

    return merged_pops


def consolidate_small_pops(
    demand_points: List[Dict],
    pops: List[Dict],
    min_pop_size: int = 25,
    max_pop_size: int = 200,
    consolidate_max_sizes: Optional[List[int]] = None,
    consolidate_distances: Optional[List[float]] = None
) -> Tuple[List[Dict], List[Dict]]:
    """
    Compatibility wrapper that only merges cohorts sharing the exact same OD.

    Spatial relocation is intentionally forbidden after integerization. The
    legacy tuning arguments are retained so existing callers do not break.
    """
    del consolidate_max_sizes, consolidate_distances
    if not pops or not demand_points:
        return demand_points, pops

    # OD assignments are authoritative at this stage. The former spatial
    # consolidation moved passengers between origins or destinations; only
    # exact-OD merging is permitted now.
    return demand_points, merge_identical_commutes(
        pops, min_pop_size=min_pop_size, max_pop_size=max_pop_size
    )

def cluster_demand_points(
    demand_points: List[Dict],
    pops: List[Dict],
    max_pop_threshold: Optional[List[float]] = None,
    buffer_meters: Optional[List[float]] = None,
    exclusion_zones: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Agrupa y fusiona espacialmente puntos de demanda contiguos según el método de clustering
    oficial de Subway Builder (Colin's method), evitando nodos redundantes a menos de 100-250m.
    Protege 100% de los POIs especiales (aeropuertos, universidades, estadios).
    """
    if not demand_points or not pops:
        return demand_points, pops

    if max_pop_threshold is None:
        max_pop_threshold = [200, 500, 5000, 15000, float('inf')]
    if buffer_meters is None:
        buffer_meters = [250, 200, 150, 125, 100]

    special_pts = [p for p in demand_points if p.get("is_special", False)]
    regular_pts = [p for p in demand_points if not p.get("is_special", False)]

    if not regular_pts:
        return demand_points, pops

    invalid_inputs = [
        p["id"] for p in regular_pts
        if is_point_in_exclusion_zone(p["location"][0], p["location"][1], exclusion_zones)
    ]
    if invalid_inputs:
        raise ValueError(f"Demand points inside exclusion zones before clustering: {invalid_inputs[:10]}")

    res_by_id = {p["id"]: sum(pop["size"] for pop in pops if pop["residenceId"] == p["id"]) for p in regular_pts}
    jobs_by_id = {p["id"]: sum(pop["size"] for pop in pops if pop["jobId"] == p["id"]) for p in regular_pts}
    sizes = np.array([res_by_id[p["id"]] + jobs_by_id[p["id"]] for p in regular_pts], dtype=np.float64)

    isort = np.argsort(sizes)[::-1]
    sorted_pts = [regular_pts[i] for i in isort]
    sorted_sizes = sizes[isort]

    unique_centers = []
    unique_sizes = []
    unique_ids = []
    point_mapping = {}

    for i, pt in enumerate(sorted_pts):
        pt_sz = sorted_sizes[i]
        pt_loc = pt["location"]

        buf = buffer_meters[-1]
        for th, b in zip(max_pop_threshold, buffer_meters):
            if pt_sz <= th:
                buf = b
                break

        cos_lat = math.cos(math.radians(pt_loc[1]))

        merged_to = None
        for c_idx, c_loc in enumerate(unique_centers):
            dist_m = math.hypot(
                (pt_loc[0] - c_loc[0]) * 111320.0 * cos_lat,
                (pt_loc[1] - c_loc[1]) * 110574.0
            )
            if dist_m <= buf:
                merged_to = c_idx
                break

        if merged_to is None:
            new_idx = len(unique_centers)
            unique_centers.append(list(pt_loc))
            unique_sizes.append(pt_sz)
            unique_ids.append(pt["id"])
            point_mapping[pt["id"]] = pt["id"]
        else:
            target_id = unique_ids[merged_to]
            point_mapping[pt["id"]] = target_id
            cur_sz = unique_sizes[merged_to]
            new_sz = cur_sz + pt_sz
            if new_sz > 0:
                candidate_lon = (unique_centers[merged_to][0] * cur_sz + pt_loc[0] * pt_sz) / new_sz
                candidate_lat = (unique_centers[merged_to][1] * cur_sz + pt_loc[1] * pt_sz) / new_sz
                if is_point_in_exclusion_zone(candidate_lon, candidate_lat, exclusion_zones):
                    unique_centers.append(list(pt_loc))
                    unique_sizes.append(pt_sz)
                    unique_ids.append(pt["id"])
                    point_mapping[pt["id"]] = pt["id"]
                    continue
                unique_centers[merged_to][0] = candidate_lon
                unique_centers[merged_to][1] = candidate_lat
            unique_sizes[merged_to] = new_sz

    original_locations = {p["id"]: list(p["location"]) for p in regular_pts}
    final_regular_locations = {
        point_id: location for point_id, location in zip(unique_ids, unique_centers)
    }
    updated_pops = []
    for p in pops:
        new_p = dict(p)
        orig_r = new_p["residenceId"]
        orig_j = new_p["jobId"]

        if orig_r in point_mapping:
            new_p["residenceId"] = point_mapping[orig_r]
        if orig_j in point_mapping:
            new_p["jobId"] = point_mapping[orig_j]
        origin_moved = (
            orig_r in original_locations
            and final_regular_locations.get(new_p["residenceId"]) != original_locations[orig_r]
        )
        destination_moved = (
            orig_j in original_locations
            and final_regular_locations.get(new_p["jobId"]) != original_locations[orig_j]
        )
        if (new_p["residenceId"] != orig_r or new_p["jobId"] != orig_j
                or origin_moved or destination_moved):
            invalidate_route_metrics(new_p)

        updated_pops.append(new_p)

    merged_pts = []
    for c_id, c_loc in zip(unique_ids, unique_centers):
        merged_pts.append({
            "id": c_id,
            "location": [round(c_loc[0], 5), round(c_loc[1], 5)],
            "jobs": 0,
            "residents": 0,
            "popIds": []
        })

    merged_pts.extend(special_pts)
    return merged_pts, updated_pops


def sync_demand_points_and_pops(
    demand_points: List[Dict],
    pops: List[Dict],
    remove_orphans: bool = True,
    include_driving_path: Optional[bool] = None
) -> Tuple[List[Dict], List[Dict]]:
    """
    Rebuild pop IDs and validate authoritative OD marginals during serialization.

    Points produced by the OD allocator carry private authoritative row/column
    totals. Those totals are checked, never rewritten to hide cohort drift.
    Legacy points without the private audit fields retain display synchronization.
    """
    from collections import defaultdict
    res_by_id = defaultdict(int)
    jobs_by_id = defaultdict(int)
    pop_ids_by_point = defaultdict(list)
    point_ids = [str(point["id"]) for point in demand_points]
    if len(point_ids) != len(set(point_ids)):
        raise ValueError("Demand point IDs must be unique before OD synchronization")
    known_point_ids = set(point_ids)

    clean_pops = []
    for idx, p in enumerate(pops, start=1):
        if p["size"] <= 0:
            continue
        residence_id = str(p["residenceId"])
        job_id = str(p["jobId"])
        unknown_endpoints = [
            endpoint for endpoint in (residence_id, job_id) if endpoint not in known_point_ids
        ]
        if unknown_endpoints:
            raise ValueError(
                f"Pop {p.get('id', idx)} references unknown OD endpoint(s): "
                f"{sorted(set(unknown_endpoints))}"
            )
        pid = f"pop_{idx:06d}"
        pop_dict = {
            "id": pid,
            "size": int(p["size"]),
            "residenceId": residence_id,
            "jobId": job_id,
        }
        if "drivingSeconds" in p:
            pop_dict["drivingSeconds"] = int(p["drivingSeconds"])
        if "drivingDistance" in p:
            pop_dict["drivingDistance"] = int(p["drivingDistance"])
        if include_driving_path is not False and "drivingPath" in p and p["drivingPath"]:
            pop_dict["drivingPath"] = p["drivingPath"]
        clean_pops.append(pop_dict)
        res_by_id[pop_dict["residenceId"]] += pop_dict["size"]
        jobs_by_id[pop_dict["jobId"]] += pop_dict["size"]
        pop_ids_by_point[pop_dict["residenceId"]].append(pid)
        pop_ids_by_point[pop_dict["jobId"]].append(pid)

    # Audit every authoritative endpoint before orphan removal. In particular,
    # a positive expected marginal with no remaining cohorts must fail rather
    # than disappear from the serialized point list.
    for pt, pid in zip(demand_points, point_ids):
        expected_residents = pt.get("_authoritative_od_residents")
        expected_jobs = pt.get("_authoritative_od_jobs")
        if expected_residents is not None and int(expected_residents) != res_by_id[pid]:
            raise ValueError(
                f"Post-OD origin marginal changed for {pid}: expected "
                f"{int(expected_residents)}, observed {res_by_id[pid]}"
            )
        if expected_jobs is not None and int(expected_jobs) != jobs_by_id[pid]:
            raise ValueError(
                f"Post-OD destination marginal changed for {pid}: expected "
                f"{int(expected_jobs)}, observed {jobs_by_id[pid]}"
            )

    synced_points = []
    for pt, pid in zip(demand_points, point_ids):
        r_total = res_by_id[pid]
        j_total = jobs_by_id[pid]
        is_sp = bool(pt.get("is_special", False))

        if remove_orphans and r_total == 0 and j_total == 0 and not is_sp:
            continue

        p_copy = dict(pt)
        expected_residents = pt.get("_authoritative_od_residents")
        expected_jobs = pt.get("_authoritative_od_jobs")
        p_copy["residents"] = int(expected_residents) if expected_residents is not None else r_total
        p_copy["jobs"] = int(expected_jobs) if expected_jobs is not None else j_total
        p_copy.pop("_authoritative_od_residents", None)
        p_copy.pop("_authoritative_od_jobs", None)
        p_copy["popIds"] = list(dict.fromkeys(pop_ids_by_point[pid]))
        synced_points.append(p_copy)

    return synced_points, clean_pops


def sanitize_demand_points(demand_points: List[Dict]) -> List[Dict]:
    """
    Devuelve una copia limpia de los puntos de demanda conforme al esquema canónico
    de Subway Builder (id, location, jobs, residents, popIds).
    No muta los diccionarios originales en memoria.
    """
    clean_points = []
    for p in demand_points:
        clean_points.append({
            "id": str(p["id"]),
            "location": [float(p["location"][0]), float(p["location"][1])],
            "jobs": int(p["jobs"]),
            "residents": int(p["residents"]),
            "popIds": list(p.get("popIds", []))
        })
    return clean_points


MODAL_PRESETS: Dict[str, Dict[str, Any]] = {
    "canonical": {
        "name": "Flujo Libre (Oficial Colin)",
        "traffic_speed_kmh": 40.0,
        "motorization_rate": 1.0
    },
    "moderate_traffic": {
        "name": "Tráfico Moderado",
        "traffic_speed_kmh": 28.0,
        "motorization_rate": 0.55
    },
    "cdmx_peak": {
        "name": "Megaciudad Saturada",
        "traffic_speed_kmh": 18.0,
        "motorization_rate": 0.35
    },
    "captive_transit": {
        "name": "Transporte Cautivo",
        "traffic_speed_kmh": 24.0,
        "motorization_rate": 0.20
    },
    "custom": {
        "name": "Personalizado"
    }
}


def apply_modal_competitiveness_experiment(
    pops: List[Dict[str, Any]],
    modal_config: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """
    Aplica el Laboratorio Experimental de Competitividad Modal (Auto vs. Metro).
    Transforma drivingSeconds mediante Impedancia Alternativa Ponderada:
      T_auto = T_base * (40.0 / V_trafico)
      T_colectivo = T_auto * 2.0 + 300 s
      drivingSeconds = round(P_auto * T_auto + (1 - P_auto) * T_colectivo)

    Si modal_config es None o enabled es False, retorna pops sin alteración alguna.
    """
    if not modal_config or not modal_config.get("enabled", False):
        return pops

    preset = str(modal_config.get("preset", "custom")).strip().lower()
    preset_data = MODAL_PRESETS.get(preset, {})

    raw_speed = modal_config.get("traffic_speed_kmh")
    if raw_speed is None and "traffic_speed_kmh" in preset_data:
        raw_speed = preset_data["traffic_speed_kmh"]
    try:
        traffic_speed_kmh = float(raw_speed if raw_speed is not None else 22.0)
    except (ValueError, TypeError):
        traffic_speed_kmh = 22.0

    raw_motor = modal_config.get("motorization_rate")
    if raw_motor is None and "motorization_rate" in preset_data:
        raw_motor = preset_data["motorization_rate"]
    try:
        motorization_rate = float(raw_motor if raw_motor is not None else 0.40)
    except (ValueError, TypeError):
        motorization_rate = 0.40

    # Salvaguardas de rango físico
    traffic_speed_kmh = max(10.0, min(60.0, traffic_speed_kmh))
    motorization_rate = max(0.05, min(1.0, motorization_rate))

    # Factor de escala de velocidad automotriz respecto a línea base
    speed_factor = CANONICAL_SPEED_KMH / traffic_speed_kmh
    speed_ms = traffic_speed_kmh / 3.6

    for p in pops:
        base_sec = float(p.get("drivingSeconds", 0))
        dist_m = float(p.get("drivingDistance", 0))

        if base_sec > 0:
            t_auto = max(45.0, base_sec * speed_factor)
        elif dist_m > 0:
            t_auto = max(45.0, dist_m / speed_ms)
        else:
            t_auto = 45.0

        # Fricción de transporte público de superficie (paradas frecuentes + espera)
        t_colectivo = t_auto * 2.0 + 300.0

        # Impedancia alternativa ponderada
        t_effective = motorization_rate * t_auto + (1.0 - motorization_rate) * t_colectivo
        p["drivingSeconds"] = max(45, int(round(t_effective)))

    return pops


def calculate_commute_distance_distribution(
    pops: List[Dict[str, Any]],
    demand_points: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Calcula la Distribución de Longitud de Viajes (Trip Length Distribution - TLD)
    de las cohortes de viajeros, ponderada por volumen de pasajeros (size).
    Sigue el estándar oficial de calibración de Subway Builder (Colin Miller).
    """
    if not pops:
        return {
            "total_pops": 0,
            "total_commuters": 0,
            "mean_km": 0.0,
            "median_km": 0.0,
            "p10_km": 0.0,
            "p25_km": 0.0,
            "p75_km": 0.0,
            "p90_km": 0.0,
            "p95_km": 0.0,
            "min_km": 0.0,
            "max_km": 0.0,
            "mean_minutes": 0.0,
            "median_minutes": 0.0,
            "profile": "sin_datos",
            "profile_label": "Sin datos de demanda",
            "brackets": []
        }

    distances_km = []
    weights = []
    durations_min = []

    pt_coords = {}
    if demand_points:
        for pt in demand_points:
            if "id" in pt and "location" in pt:
                pt_coords[pt["id"]] = pt["location"]

    for p in pops:
        w = max(1, int(p.get("size", 1)))
        d_m = float(p.get("drivingDistance", 0))
        sec = float(p.get("drivingSeconds", 0))

        if d_m <= 0 and pt_coords:
            r_id = p.get("residenceId")
            j_id = p.get("jobId")
            if r_id in pt_coords and j_id in pt_coords:
                r_loc = pt_coords[r_id]
                j_loc = pt_coords[j_id]
                rlon, rlat = np.radians(r_loc[0]), np.radians(r_loc[1])
                jlon, jlat = np.radians(j_loc[0]), np.radians(j_loc[1])
                dlat = jlat - rlat
                dlon = jlon - rlon
                a = np.sin(dlat / 2.0)**2 + np.cos(rlat) * np.cos(jlat) * np.sin(dlon / 2.0)**2
                euclid_km = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))
                d_m = euclid_km * 1000.0 * 1.3

        d_km = max(0.0, d_m / 1000.0)
        t_min = max(0.0, sec / 60.0)

        distances_km.append(d_km)
        weights.append(w)
        durations_min.append(t_min)

    d_arr = np.array(distances_km, dtype=np.float64)
    w_arr = np.array(weights, dtype=np.float64)
    t_arr = np.array(durations_min, dtype=np.float64)

    total_commuters = int(w_arr.sum())
    total_pops = len(pops)

    if total_commuters <= 0:
        return {
            "total_pops": total_pops,
            "total_commuters": 0,
            "mean_km": 0.0,
            "median_km": 0.0,
            "p10_km": 0.0,
            "p25_km": 0.0,
            "p75_km": 0.0,
            "p90_km": 0.0,
            "p95_km": 0.0,
            "min_km": 0.0,
            "max_km": 0.0,
            "mean_minutes": 0.0,
            "median_minutes": 0.0,
            "profile": "sin_datos",
            "profile_label": "Sin masa activa",
            "brackets": []
        }

    mean_km = float((d_arr * w_arr).sum() / total_commuters)
    mean_min = float((t_arr * w_arr).sum() / total_commuters)

    sorter = np.argsort(d_arr)
    d_sorted = d_arr[sorter]
    w_sorted = w_arr[sorter]
    cum_w = np.cumsum(w_sorted)

    def _weighted_pct(pct: float) -> float:
        cutoff = (pct / 100.0) * total_commuters
        idx = np.searchsorted(cum_w, cutoff)
        idx = min(idx, len(d_sorted) - 1)
        return float(d_sorted[idx])

    p10_km = _weighted_pct(10.0)
    p25_km = _weighted_pct(25.0)
    median_km = _weighted_pct(50.0)
    p75_km = _weighted_pct(75.0)
    p90_km = _weighted_pct(90.0)
    p95_km = _weighted_pct(95.0)
    min_km = float(d_arr.min())
    max_km = float(d_arr.max())

    t_sorter = np.argsort(t_arr)
    t_sorted = t_arr[t_sorter]
    tw_sorted = w_arr[t_sorter]
    t_cum_w = np.cumsum(tw_sorted)
    t_cutoff = 0.5 * total_commuters
    t_idx = min(np.searchsorted(t_cum_w, t_cutoff), len(t_sorted) - 1)
    median_min = float(t_sorted[t_idx])

    bracket_defs = [
        {"id": "micro", "label": "< 5 km", "min": 0.0, "max": 5.0, "category": "Barrial / Micromovilidad"},
        {"id": "short", "label": "5 – 10 km", "min": 5.0, "max": 10.0, "category": "Urbano Corto"},
        {"id": "medium", "label": "10 – 15 km", "min": 10.0, "max": 15.0, "category": "Metropolitano Medio"},
        {"id": "suburban", "label": "15 – 25 km", "min": 15.0, "max": 25.0, "category": "Conurbación Suburbana"},
        {"id": "regional", "label": "> 25 km", "min": 25.0, "max": 1e9, "category": "Metropolitano Largo / Regional"},
    ]

    brackets = []
    for b in bracket_defs:
        mask = (d_arr >= b["min"]) & (d_arr < b["max"])
        c_count = int(w_arr[mask].sum())
        p_count = int(mask.sum())
        pct = (c_count / total_commuters) * 100.0 if total_commuters > 0 else 0.0
        brackets.append({
            "id": b["id"],
            "label": b["label"],
            "category": b["category"],
            "min_km": b["min"],
            "max_km": b["max"] if b["max"] < 1e6 else None,
            "commuters": c_count,
            "pops_count": p_count,
            "percentage": round(pct, 2)
        })

    if median_km < 8.0:
        profile = "compacta"
        profile_label = "Ciudad Compacta (Alta densidad de viajes cortos < 8 km)"
    elif median_km <= 16.0:
        profile = "intermedia"
        profile_label = "Metrópoli Intermedia (Equilibrio de trayectos medios 8–16 km)"
    else:
        profile = "megaciudad"
        profile_label = "Megaciudad Extendida (Predominio de trayectos largos > 16 km)"

    return {
        "total_pops": total_pops,
        "total_commuters": total_commuters,
        "mean_km": round(mean_km, 2),
        "median_km": round(median_km, 2),
        "p10_km": round(p10_km, 2),
        "p25_km": round(p25_km, 2),
        "p75_km": round(p75_km, 2),
        "p90_km": round(p90_km, 2),
        "p95_km": round(p95_km, 2),
        "min_km": round(min_km, 2),
        "max_km": round(max_km, 2),
        "mean_minutes": round(mean_min, 1),
        "median_minutes": round(median_min, 1),
        "profile": profile,
        "profile_label": profile_label,
        "brackets": brackets
    }


def recommend_gravity_beta(
    bbox: Optional[List[float]] = None,
    city_archetype: Optional[str] = None
) -> Dict[str, Any]:
    """
    Recomienda el coeficiente de fricción espacial óptimo (beta) según la extensión
    geográfica (diagonal del BBOX) o el arquetipo urbano de la metrópoli.
    Emula la calibración empírica oficial de Colin Miller en Subway Builder.
    """
    arch = str(city_archetype or "").strip().lower()

    if arch in ["compacta", "compact", "pequeña"]:
        return {
            "archetype": "compacta",
            "label": "Ciudad Compacta",
            "recommended_beta": 0.150,
            "expected_median_km": "6 – 9 km",
            "rationale": "Metrópoli concentrada o costera. Fricción alta para evitar dispersión ficticia hacia la periferia rural."
        }
    elif arch in ["megaciudad", "metropolis", "megacity", "extendida"]:
        return {
            "archetype": "megaciudad",
            "label": "Megaciudad Extendida",
            "recommended_beta": 0.085,
            "expected_median_km": "18 – 25 km",
            "rationale": "Gran valle conurbado con múltiples municipios. Fricción reducida para permitir flujos metropolitanos de largo alcance."
        }
    elif arch in ["intermedia", "intermediate", "media"]:
        return {
            "archetype": "intermedia",
            "label": "Metrópoli Intermedia",
            "recommended_beta": 0.120,
            "expected_median_km": "10 – 15 km",
            "rationale": "Escala metropolitana estándar con balance entre centralidad y expansión suburbana."
        }

    diag_km = 40.0
    if bbox and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = [float(x) for x in bbox]
        rlat1, rlon1 = np.radians(min_lat), np.radians(min_lon)
        rlat2, rlon2 = np.radians(max_lat), np.radians(max_lon)
        dlat = rlat2 - rlat1
        dlon = rlon2 - rlon1
        a = np.sin(dlat / 2.0)**2 + np.cos(rlat1) * np.cos(rlat2) * np.sin(dlon / 2.0)**2
        diag_km = float(6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0)))

    if diag_km < 28.0:
        return {
            "archetype": "compacta",
            "label": "Ciudad Compacta",
            "diagonal_km": round(diag_km, 1),
            "recommended_beta": 0.150,
            "expected_median_km": "6 – 9 km",
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (< 28 km). Perfil compacto detectado."
        }
    elif diag_km > 65.0:
        return {
            "archetype": "megaciudad",
            "label": "Megaciudad Extendida",
            "diagonal_km": round(diag_km, 1),
            "recommended_beta": 0.085,
            "expected_median_km": "18 – 25 km",
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (> 65 km). Conurbación masiva detectada."
        }
    else:
        return {
            "archetype": "intermedia",
            "label": "Metrópoli Intermedia",
            "diagonal_km": round(diag_km, 1),
            "recommended_beta": 0.120,
            "expected_median_km": "10 – 15 km",
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (28–65 km). Escala metropolitana típica."
        }
