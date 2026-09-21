"""
sb_mexico.gravity
=================
Motor de interacción espacial y simulación de demanda de transporte.
Implementa:
1. Malla espacial de agregación (Clustering).
2. Fusión y absorción de POIs Especiales (Aeropuertos, Universidades, etc.).
3. Snapping vial vectorial con indexación espacial STRtree.
4. Modelo Gravitatorio con Distribución Multinomial y Conservación Estricta de Masa a Priori.
"""

import math
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


def sanitize_max_distance_km(val: Any, default: float = 55.0) -> float:
    """
    Sanea de forma determinista el parámetro max_distance_km.
    Filtra None, cadenas corruptas, nan, inf, -inf y valores <= 0.0,
    retornando el valor por defecto si la entrada es inválida.
    """
    if val is None:
        return default
    try:
        f = float(val)
        if math.isfinite(f) and f > 0.0:
            return f
        return default
    except (ValueError, TypeError):
        return default


def is_point_in_zone(lon: float, lat: float, zone: Dict[str, Any]) -> bool:
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
            return bool(poly.contains(Point(lon, lat)))
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
        if zone and zone.get("enabled", True) and is_point_in_zone(lon, lat, zone):
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
    restrict_demand_to_urban_core: bool = True
) -> Tuple[List[Dict], List[Dict]]:
    """
    Agrega datos de población y empleo en celdas espaciales,
    resuelve la absorción de POIs Especiales, modula Zonas de Alta Afluencia
    y realiza el snapping a la red vial.
    """
    rng = np.random.default_rng(seed)
    core_poly_prep = prepare_polygon_geom(urban_core_polygon) if (urban_core_polygon and restrict_demand_to_urban_core) else None
    denue_outside_core = 0
    cpv_outside_core = 0

    
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
        if exclusion_zones and is_point_in_exclusion_zone(r_lon, r_lat, exclusion_zones):
            continue
        if core_poly_prep and not is_point_in_prepared_polygon(r_lon, r_lat, core_poly_prep):
            denue_outside_core += 1
            continue
        r_jobs = float(rec['calibrated_jobs'])
        
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
            continue  # Se absorbe por el POI más cercano para evitar doble conteo

        # Modulación de Zonas de Alta Afluencia para empleo regular DENUE
        # (Los POIs especiales conservan estrictamente su valor manual declarado)
        if affluence_zones:
            mult, _, _ = get_zone_multipliers_for_point(r_lon, r_lat, affluence_zones)
            if mult > 1.0:
                r_jobs *= mult

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
                lon, lat = proj_candidate.x, proj_candidate.y

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
            from shapely.geometry import Point, Polygon
            poly = z["polygon"]
            if isinstance(poly, (list, tuple)):
                poly = Polygon(poly)
            for i in range(n):
                if poly.contains(Point(lons[i], lats[i])):
                    zones[i] = idx
    return zones


def furness_ipfp_balance(
    orig_pea: np.ndarray,
    dest_jobs: np.ndarray,
    dist_km_mat: np.ndarray,
    reach_bonuses: Optional[np.ndarray] = None,
    beta: float = 0.12,
    max_distance_km: float = 55.0,
    max_iter: int = 15,
    tol: float = 0.02
) -> np.ndarray:
    """
    Ejecuta el Algoritmo de Furness / IPFP (Iterative Proportional Fitting Procedure)
    para un Modelo Gravitatorio Doblemente Acotado (Doubly-Constrained).

    Garantiza que la matriz de flujos converja simultáneamente hacia:
    1. Totales por fila: Sum_j T_ij = O_i (PEA residencial por origen).
    2. Totales por columna: Sum_i T_ij proporcional a D_j (Capacidad de puestos de trabajo).
    3. Fricción espacial modulada: f(d_ij) = exp(-beta_j * d_ij) con bono de alcance y piso de retención local.

    Retorna la matriz de probabilidades condicionales P_ij = T_ij / O_i de dimensión (N, M),
    donde cada fila suma exactamente 1.0.
    """
    n_orig = len(orig_pea)
    n_dest = len(dest_jobs)
    if n_orig == 0 or n_dest == 0:
        return np.zeros((n_orig, n_dest), dtype=np.float64)

    total_o = float(orig_pea.sum())
    total_d = float(dest_jobs.sum())

    if total_o <= 0 or total_d <= 0:
        return np.full((n_orig, n_dest), 1.0 / n_dest, dtype=np.float64)

    # 1. Normalizar capacidad de destinos a la masa total de PEA (Sum_j D*_j == Sum_i O_i)
    d_target = ((dest_jobs.astype(np.float64) / total_d) * total_o).astype(np.float32)
    o_target = orig_pea.astype(np.float32)

    # 2. Matriz de Fricción Espacial con modulación de alcance por destino
    if reach_bonuses is not None and np.any(reach_bonuses > 0):
        clamped_reach = np.clip(reach_bonuses, 0.0, 0.60).astype(np.float32)
        eff_beta = (beta * (1.0 - clamped_reach)).astype(np.float32)
        friction = np.exp(-eff_beta[np.newaxis, :] * dist_km_mat, dtype=np.float32)
        # Salvaguarda de Piso de Retención Local para viajes de proximidad (<= 3 km)
        local_mask = dist_km_mat <= 3.0
        friction_base = np.exp(-beta * dist_km_mat, dtype=np.float32)
        friction = np.where(local_mask & (friction < friction_base), friction_base, friction)
    else:
        friction = np.exp(-beta * dist_km_mat, dtype=np.float32)

    friction[dist_km_mat > max_distance_km] = 0.0

    # 3. Inicializar Matriz de Flujos T_ij^(0)
    t_mat = (o_target[:, np.newaxis] * d_target[np.newaxis, :]) * friction

    # Conectividad de respaldo: asegurar que ninguna fila o columna quede en 0
    row_sums = t_mat.sum(axis=1)
    zero_rows = np.where(row_sums == 0)[0]
    for i in zero_rows:
        closest = np.argsort(dist_km_mat[i])[:min(5, n_dest)]
        t_mat[i, closest] = o_target[i] * d_target[closest] * np.exp(-beta * dist_km_mat[i, closest])
        if t_mat[i].sum() == 0:
            t_mat[i, closest] = 1.0

    col_sums = t_mat.sum(axis=0)
    zero_cols = np.where(col_sums == 0)[0]
    for j in zero_cols:
        closest = np.argsort(dist_km_mat[:, j])[:min(5, n_orig)]
        t_mat[closest, j] = o_target[closest] * d_target[j] * np.exp(-beta * dist_km_mat[closest, j])
        if t_mat[:, j].sum() == 0:
            t_mat[closest, j] = 1.0

    # 4. Iteraciones de Furness / IPFP
    eps = 1e-12
    for _ in range(max_iter):
        # Paso A: Ajuste a Filas (Orígenes / PEA)
        r_sum = t_mat.sum(axis=1)
        r_scale = np.where(r_sum > eps, o_target / (r_sum + eps), 1.0)
        t_mat *= r_scale[:, np.newaxis]

        # Paso B: Ajuste a Columnas (Destinos / Empleo)
        c_sum = t_mat.sum(axis=0)
        c_scale = np.where(c_sum > eps, d_target / (c_sum + eps), 1.0)
        t_mat *= c_scale[np.newaxis, :]

        # Verificación de convergencia marginal en destinos
        c_current = t_mat.sum(axis=0)
        active_cols = d_target > eps
        if np.any(active_cols):
            max_rel_err = np.max(np.abs(c_current[active_cols] - d_target[active_cols]) / d_target[active_cols])
            if max_rel_err < tol:
                break

    # 5. Ajuste final a las filas de orígenes para preservar exactamente O_i
    r_sum = t_mat.sum(axis=1)
    r_scale = np.where(r_sum > eps, o_target / (r_sum + eps), 1.0)
    t_mat *= r_scale[:, np.newaxis]

    # 6. Matriz Estocástica de Probabilidades (P_ij = T_ij / O_i)
    row_t_sum = t_mat.sum(axis=1, keepdims=True)
    row_t_sum[row_t_sum == 0] = 1.0
    p_mat = t_mat / row_t_sum

    # Normalización estricta por fila para compensar precisión flotante
    p_sums = p_mat.sum(axis=1, keepdims=True)
    p_sums[p_sums == 0] = 1.0
    p_mat /= p_sums

    return p_mat


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
    furness_iterations: int = 15,
    furness_tol: float = 0.02,
    road_index: Optional["ArterialRoadIndex"] = None
) -> List[Dict]:
    """
    Ejecuta el Modelo de Demanda en Dos Capas:
    - Capa 1: Asignación de Cuotas Exactas a POIs Especiales (Aeropuertos, Universidades).
    - Capa 2: Modelo Gravitatorio Doblemente Acotado (Furness / IPFP) para Empleo Regular (DENUE).
    Garantiza la conservación matemática estricta de la PEA, agrupa viajeros en cohortes
    (target_pop_size), balancea la atracción hacia los puestos de trabajo reales de destino
    y respeta las barreras topológicas insulares (isolated_zones).
    """
    rng = np.random.default_rng(seed)
    max_distance_km = sanitize_max_distance_km(max_distance_km, default=55.0)

    # 1. Separar orígenes residenciales y destinos
    origins = [p for p in demand_points if p.get("pea_15ymas", 0) > 0]
    if not origins:
        raise ValueError("No se encontraron orígenes residenciales con PEA > 0.")

    # Vectorizar coordenadas de orígenes y zonas de aislamiento
    orig_coords_deg = np.array([o["location"] for o in origins], dtype=np.float64)
    orig_coords = np.radians(orig_coords_deg)
    orig_pea = np.array([o["pea_15ymas"] for o in origins], dtype=np.int64)
    orig_zones = assign_zones(orig_coords_deg, isolated_zones)

    # Identificar Destinos Especiales (con cuota fija) vs Regulares
    special_dests = [p for p in demand_points if p.get("is_special", False) and p.get("jobs", 0) > 0]
    regular_dests = [p for p in demand_points if not p.get("is_special", False) and p.get("jobs", 0) > 0]

    pops = []
    pop_id = 1

    # =========================================================================
    # CAPA 1: ASIGNACIÓN DE DEMANDA ESPECIAL (CUOTAS EXACTAS EN COHORTES)
    # =========================================================================
    effective_target = max(1, target_pop_size) if target_pop_size > 0 else max_pop_size

    for sp_dest in special_dests:
        target_quota = int(sp_dest["jobs"])
        if target_quota <= 0:
            continue

        sp_id = sp_dest["id"]
        # Determinar tamaño de cohorte por tipo de infraestructura
        if sp_id.startswith("UNI_"):
            cohort_limit = min(75, max_pop_size) if min_pop_size < max_pop_size else max_pop_size   # Flujo escalonado universitario
        elif sp_id.startswith("AIR_"):
            cohort_limit = min(120, max_pop_size) if min_pop_size < max_pop_size else max_pop_size  # Flujo continuo 24/7 de aeropuerto
        else:
            cohort_limit = max_pop_size

        sp_target = min(effective_target, cohort_limit)

        sp_loc_deg = np.array([sp_dest["location"]], dtype=np.float64)
        sp_zone = assign_zones(sp_loc_deg, isolated_zones)[0]
        sp_coord = np.radians(sp_dest["location"])

        # Distancia Haversine desde todos los orígenes
        dlat = sp_coord[1] - orig_coords[:, 1]
        dlon = sp_coord[0] - orig_coords[:, 0]
        a = np.sin(dlat / 2.0)**2 + np.cos(orig_coords[:, 1]) * np.cos(sp_coord[1]) * np.sin(dlon / 2.0)**2
        dist_km = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

        # Determinar cohortes discretas para la cuota especial
        k_sp = max(1, int(round(target_quota / sp_target)))
        b_sp = target_quota // k_sp
        r_sp = target_quota % k_sp
        sp_cohort_sizes = [b_sp + 1 if j < r_sp else b_sp for j in range(k_sp)]

        # Asignación de cohortes acotada por capacidad remanente (Bounded Cohort Allocation)
        sp_assigned = np.zeros(len(origins), dtype=np.int64)

        # Identificar si el destino especial es también un origen (evitar auto-viajes)
        self_orig_idx = None
        for idx, o in enumerate(origins):
            if o["id"] == sp_id:
                self_orig_idx = idx
                break

        remaining_cohorts = k_sp
        cohort_cursor = 0

        while remaining_cohorts > 0 and np.any(orig_pea > 0):
            # Solo orígenes con PEA disponible, en la misma zona topológica y dentro del límite de viaje
            active_mask = (orig_pea > 0) & (orig_zones == sp_zone) & (dist_km <= max_distance_km)
            if self_orig_idx is not None:
                active_mask[self_orig_idx] = False
            if not np.any(active_mask):
                break
            sp_weights = np.zeros(len(origins), dtype=np.float64)
            sp_weights[active_mask] = orig_pea[active_mask].astype(np.float64) * np.exp(-0.04 * dist_km[active_mask])
            total_w = sp_weights.sum()
            if total_w <= 0:
                break
            sp_probs = sp_weights / total_w
            draw = rng.multinomial(remaining_cohorts, sp_probs)
            allocated_this_round = 0

            for i in np.where(draw > 0)[0]:
                num_c = int(draw[i])
                pax_wanted = sum(sp_cohort_sizes[cohort_cursor : cohort_cursor + num_c])
                actual_pax = min(pax_wanted, int(orig_pea[i]))

                sp_assigned[i] += actual_pax
                orig_pea[i] -= actual_pax
                if actual_pax == pax_wanted:
                    cohort_cursor += num_c
                    allocated_this_round += num_c
                else:
                    sp_cohort_sizes[cohort_cursor] = actual_pax
                    cohort_cursor += 1
                    allocated_this_round += 1
                    sp_cohort_sizes.append(pax_wanted - actual_pax)
                    remaining_cohorts += 1

            remaining_cohorts -= allocated_this_round
            if allocated_this_round == 0:
                break

        # Generar cohortes a partir de los viajeros efectivamente asignados
        for i, count in enumerate(sp_assigned):
            if count <= 0:
                continue

            orig = origins[i]
            d_km = float(dist_km[i])
            if road_index is not None:
                dist_m, driving_seconds = road_index.get_driving_impedance(
                    orig["location"], sp_dest["location"], d_km
                )
            else:
                dist_m, driving_seconds = calculate_commute_impedance(d_km)

            pax_left = int(count)
            while pax_left > 0:
                chunk = min(pax_left, cohort_limit)
                pid = f"pop_{pop_id:06d}"
                pops.append({
                    "id": pid,
                    "size": chunk,
                    "residenceId": orig["id"],
                    "jobId": sp_id,
                    "drivingSeconds": driving_seconds,
                    "drivingDistance": dist_m
                })
                orig.setdefault("popIds", []).append(pid)
                if sp_dest["id"] != orig["id"]:
                    sp_dest.setdefault("popIds", []).append(pid)
                pop_id += 1
                pax_left -= chunk

    # =========================================================================
    # CAPA 2: ASIGNACIÓN GRAVITATORIA DE EMPLEO REGULAR (FURNESS / IPFP)
    # =========================================================================
    if regular_dests and np.any(orig_pea > 0):
        dest_coords_deg = np.array([d["location"] for d in regular_dests], dtype=np.float64)
        dest_coords = np.radians(dest_coords_deg)
        dest_jobs = np.array([d["jobs"] for d in regular_dests], dtype=np.float64)
        dest_id_to_idx = {d["id"]: idx for idx, d in enumerate(regular_dests)}
        dest_zones = assign_zones(dest_coords_deg, isolated_zones)

        # Matriz NxM de distancias Haversine (en float32 con chunking por bloques para resiliencia de RAM)
        n_origs = len(origins)
        n_dests = len(regular_dests)
        dist_km_mat = np.empty((n_origs, n_dests), dtype=np.float32)
        chunk_size = 2000
        for start_idx in range(0, n_origs, chunk_size):
            end_idx = min(start_idx + chunk_size, n_origs)
            sub_orig = orig_coords[start_idx:end_idx]
            dlat = dest_coords[:, 1][np.newaxis, :] - sub_orig[:, 1][:, np.newaxis]
            dlon = dest_coords[:, 0][np.newaxis, :] - sub_orig[:, 0][:, np.newaxis]
            a = np.sin(dlat / 2.0)**2 + np.cos(sub_orig[:, 1][:, np.newaxis]) * np.cos(dest_coords[:, 1][np.newaxis, :]) * np.sin(dlon / 2.0)**2
            dist_km_mat[start_idx:end_idx] = (6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))).astype(np.float32)

        # Vectorizar bonos de alcance por destino según Zonas de Alta Afluencia
        reach_bonuses = np.zeros(len(regular_dests), dtype=np.float64)
        if affluence_zones:
            for d_idx, d in enumerate(regular_dests):
                _, r_bonus, _ = get_zone_multipliers_for_point(
                    d["location"][0], d["location"][1], affluence_zones
                )
                reach_bonuses[d_idx] = r_bonus

        prob_matrix = np.zeros((len(origins), len(regular_dests)), dtype=np.float32)

        # Balanceo de Furness / IPFP ejecutado de forma estanca por cada zona topológica
        unique_orig_zones = np.unique(orig_zones)

        for z in unique_orig_zones:
            orig_indices = np.where((orig_zones == z) & (orig_pea > 0))[0]
            if len(orig_indices) == 0:
                continue

            dest_indices = np.where((dest_zones == z) & (dest_jobs > 0))[0]

            if len(dest_indices) > 0:
                # Sub-matriz de la zona z (reutiliza dist_km_mat sin duplicar si cubre toda la ciudad)
                if len(orig_indices) == len(origins) and len(dest_indices) == len(regular_dests):
                    sub_dist = dist_km_mat
                else:
                    sub_dist = dist_km_mat[np.ix_(orig_indices, dest_indices)].copy()
                sub_pea = orig_pea[orig_indices].copy()
                sub_jobs = dest_jobs[dest_indices].copy()
                sub_reach = reach_bonuses[dest_indices].copy()

                # Anular auto-viajes si hay múltiples destinos disponibles en la zona
                if len(dest_indices) > 1:
                    for si, o_idx in enumerate(orig_indices):
                        orig_id = origins[o_idx]["id"]
                        if orig_id in dest_id_to_idx:
                            g_didx = dest_id_to_idx[orig_id]
                            if sub_dist is dist_km_mat:
                                sub_dist[o_idx, g_didx] = 1e6
                            else:
                                match = np.where(dest_indices == g_didx)[0]
                                if len(match) > 0:
                                    sub_dist[si, match[0]] = 1e6

                sub_prob = furness_ipfp_balance(
                    orig_pea=sub_pea,
                    dest_jobs=sub_jobs,
                    dist_km_mat=sub_dist,
                    reach_bonuses=sub_reach,
                    beta=beta,
                    max_distance_km=max_distance_km,
                    max_iter=furness_iterations,
                    tol=furness_tol
                )
                prob_matrix[np.ix_(orig_indices, dest_indices)] = sub_prob
            else:
                # Zona huérfana (residentes en zona sin empleos locales)
                for o_idx in orig_indices:
                    closest = np.argsort(dist_km_mat[o_idx])[:min(5, len(regular_dests))]
                    prob_matrix[o_idx, closest] = 1.0 / len(closest)

        # Normalizar probabilidades y asegurar consistencia
        for i in range(len(origins)):
            if orig_pea[i] > 0:
                row_sum = prob_matrix[i].sum()
                if row_sum > 0:
                    prob_matrix[i] /= row_sum
                else:
                    closest = np.argsort(dist_km_mat[i])[:min(5, len(regular_dests))]
                    prob_matrix[i, closest] = 1.0 / len(closest)

        effective_target = max(1, target_pop_size) if target_pop_size > 0 else max_pop_size

        # Asignar la PEA restante de cada origen en cohortes discretas
        for i, orig in enumerate(origins):
            rem_pea = int(orig_pea[i])
            if rem_pea <= 0:
                continue

            # Determinar cantidad de cohortes y sus tamaños exactos (conservación estricta de PEA)
            k = max(1, int(round(rem_pea / effective_target)))
            b = rem_pea // k
            r = rem_pea % k
            cohort_sizes = [b + 1 if j < r else b for j in range(k)]

            # Sorteo multinomial de las k cohortes
            pvals = (prob_matrix[i] / prob_matrix[i].sum()).astype(np.float64)
            pvals /= pvals.sum()
            assignments = rng.multinomial(k, pvals)
            active_dest_indices = np.where(assignments > 0)[0]

            c_idx = 0
            for d_idx in active_dest_indices:
                c_count = int(assignments[d_idx])
                pax_count = sum(cohort_sizes[c_idx : c_idx + c_count])
                c_idx += c_count

                dest = regular_dests[d_idx]
                d_km = float(dist_km_mat[i, d_idx])
                if road_index is not None:
                    dist_m, driving_seconds = road_index.get_driving_impedance(
                        orig["location"], dest["location"], d_km
                    )
                else:
                    dist_m, driving_seconds = calculate_commute_impedance(d_km)

                if pax_count > 0:
                    if pax_count <= max_pop_size:
                        chunks = [pax_count]
                    elif min_pop_size >= max_pop_size:
                        full_chunks = pax_count // max_pop_size
                        rem = pax_count % max_pop_size
                        chunks = [max_pop_size] * full_chunks
                        if rem > 0:
                            chunks.append(rem)
                    else:
                        num_chunks = int(math.ceil(pax_count / max_pop_size))
                        base = pax_count // num_chunks
                        rem = pax_count % num_chunks
                        chunks = [base + 1 if j < rem else base for j in range(num_chunks)]

                    for chunk in chunks:
                        pid = f"pop_{pop_id:06d}"
                        pops.append({
                            "id": pid,
                            "size": chunk,
                            "residenceId": orig["id"],
                            "jobId": dest["id"],
                            "drivingSeconds": driving_seconds,
                            "drivingDistance": dist_m
                        })
                        orig.setdefault("popIds", []).append(pid)
                        dest.setdefault("popIds", []).append(pid)
                        pop_id += 1

    return pops


def merge_identical_commutes(
    pops: List[Dict],
    min_pop_size: int = 25,
    max_pop_size: int = 200,
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
        if tot_w > 0:
            avg_sec = int(round(sum(p.get("drivingSeconds", 0) * w for p, w in zip(pop_list, weights)) / tot_w))
            avg_dist = int(round(sum(p.get("drivingDistance", 0) * w for p, w in zip(pop_list, weights)) / tot_w))
        else:
            avg_sec = pop_list[0].get("drivingSeconds", 0)
            avg_dist = pop_list[0].get("drivingDistance", 0)

        path = next((p["drivingPath"] for p in pop_list if "drivingPath" in p and p["drivingPath"]), None) if include_driving_path is not False else None

        if total_size <= max_pop_size:
            chunks = [total_size]
        elif min_pop_size >= max_pop_size:
            full_chunks = total_size // max_pop_size
            rem = total_size % max_pop_size
            chunks = [max_pop_size] * full_chunks
            if rem > 0:
                chunks.append(rem)
        else:
            num_chunks = int(math.ceil(total_size / max_pop_size))
            base = total_size // num_chunks
            rem = total_size % num_chunks
            chunks = [base + 1 if j < rem else base for j in range(num_chunks)]

        for sz in chunks:
            item = {
                "id": f"pop_{pop_counter:06d}",
                "size": sz,
                "residenceId": res_id,
                "jobId": job_id,
                "drivingSeconds": avg_sec,
                "drivingDistance": avg_dist
            }
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
    Consolida micro-flujos residuales por debajo de umbrales en cohortes más grandes
    dentro de un radio espacial de vecindad, portando el algoritmo canónico de Subway Builder (depot).
    
    Paso 1: Para un mismo empleo (jobId), fusiona orígenes residenciales cercanos (< distance_m)
            que tengan pocos viajeros (< max_size).
    Paso 2: Para una misma residencia (residenceId), fusiona destinos de empleo cercanos
            que tengan pocos viajeros.
    Protege los POIs especiales para que no se desplacen ni pierdan cuota.
    """
    if not pops or not demand_points:
        return demand_points, pops

    if consolidate_max_sizes is None:
        thresholds = {max(2, min_pop_size), max(2, int(min_pop_size * 0.6)), max(2, int(min_pop_size * 0.3)), 2}
        consolidate_max_sizes = sorted(list(thresholds), reverse=True)
    if consolidate_distances is None:
        consolidate_distances = [2000.0, 4000.0, 8000.0, 16000.0]

    from collections import defaultdict
    coords_by_id = {p["id"]: p["location"] for p in demand_points}
    special_ids = {p["id"] for p in demand_points if p.get("is_special", False)}

    current_pops = [dict(p) for p in pops]

    for max_sz, max_dist in zip(consolidate_max_sizes, consolidate_distances):
        # Paso A: Consolidar orígenes para un mismo trabajo (jobId)
        job_groups = defaultdict(list)
        for idx, p in enumerate(current_pops):
            if p["size"] > 0 and p["jobId"] not in special_ids and p["residenceId"] not in special_ids:
                job_groups[p["jobId"]].append(idx)

        for job_id, p_indices in job_groups.items():
            if len(p_indices) < 2:
                continue

            p_indices.sort(key=lambda idx: current_pops[idx]["size"])

            for i in range(len(p_indices)):
                pi = p_indices[i]
                pop_i = current_pops[pi]
                if pop_i["size"] <= 0 or pop_i["size"] >= max_sz:
                    continue

                loc1 = coords_by_id.get(pop_i["residenceId"])
                if not loc1:
                    continue

                for j in range(i + 1, len(p_indices)):
                    pj = p_indices[j]
                    pop_j = current_pops[pj]
                    if pop_j["size"] <= 0 or pop_j["size"] >= max_pop_size:
                        continue

                    loc2 = coords_by_id.get(pop_j["residenceId"])
                    if not loc2:
                        continue

                    cos_lat = math.cos(math.radians((loc1[1] + loc2[1]) / 2.0))
                    dist_m = math.hypot((loc1[0] - loc2[0]) * 111320.0 * cos_lat, (loc1[1] - loc2[1]) * 110574.0)

                    if dist_m <= max_dist:
                        transfer = min(pop_i["size"], max_pop_size - pop_j["size"])
                        if transfer > 0:
                            w_tot = pop_j["size"] + transfer
                            pop_j["drivingSeconds"] = int(round((pop_j.get("drivingSeconds", 0) * pop_j["size"] + pop_i.get("drivingSeconds", 0) * transfer) / w_tot))
                            pop_j["drivingDistance"] = int(round((pop_j.get("drivingDistance", 0) * pop_j["size"] + pop_i.get("drivingDistance", 0) * transfer) / w_tot))
                            pop_j["size"] += transfer
                            pop_i["size"] -= transfer

                        if pop_i["size"] <= 0:
                            break

        # Paso B: Consolidar destinos para una misma residencia (residenceId)
        res_groups = defaultdict(list)
        for idx, p in enumerate(current_pops):
            if p["size"] > 0 and p["residenceId"] not in special_ids and p["jobId"] not in special_ids:
                res_groups[p["residenceId"]].append(idx)

        for res_id, p_indices in res_groups.items():
            if len(p_indices) < 2:
                continue

            p_indices.sort(key=lambda idx: current_pops[idx]["size"])

            for i in range(len(p_indices)):
                pi = p_indices[i]
                pop_i = current_pops[pi]
                if pop_i["size"] <= 0 or pop_i["size"] >= max_sz:
                    continue

                loc1 = coords_by_id.get(pop_i["jobId"])
                if not loc1:
                    continue

                for j in range(i + 1, len(p_indices)):
                    pj = p_indices[j]
                    pop_j = current_pops[pj]
                    if pop_j["size"] <= 0 or pop_j["size"] >= max_pop_size:
                        continue

                    loc2 = coords_by_id.get(pop_j["jobId"])
                    if not loc2:
                        continue

                    cos_lat = math.cos(math.radians((loc1[1] + loc2[1]) / 2.0))
                    dist_m = math.hypot((loc1[0] - loc2[0]) * 111320.0 * cos_lat, (loc1[1] - loc2[1]) * 110574.0)

                    if dist_m <= max_dist:
                        transfer = min(pop_i["size"], max_pop_size - pop_j["size"])
                        if transfer > 0:
                            w_tot = pop_j["size"] + transfer
                            pop_j["drivingSeconds"] = int(round((pop_j.get("drivingSeconds", 0) * pop_j["size"] + pop_i.get("drivingSeconds", 0) * transfer) / w_tot))
                            pop_j["drivingDistance"] = int(round((pop_j.get("drivingDistance", 0) * pop_j["size"] + pop_i.get("drivingDistance", 0) * transfer) / w_tot))
                            pop_j["size"] += transfer
                            pop_i["size"] -= transfer

                        if pop_i["size"] <= 0:
                            break

    active_pops = [p for p in current_pops if p["size"] > 0]
    return demand_points, active_pops


def cluster_demand_points(
    demand_points: List[Dict],
    pops: List[Dict],
    max_pop_threshold: Optional[List[float]] = None,
    buffer_meters: Optional[List[float]] = None
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
                unique_centers[merged_to][0] = (unique_centers[merged_to][0] * cur_sz + pt_loc[0] * pt_sz) / new_sz
                unique_centers[merged_to][1] = (unique_centers[merged_to][1] * cur_sz + pt_loc[1] * pt_sz) / new_sz
            unique_sizes[merged_to] = new_sz

    updated_pops = []
    for p in pops:
        new_p = dict(p)
        orig_r = new_p["residenceId"]
        orig_j = new_p["jobId"]

        if orig_r in point_mapping:
            new_p["residenceId"] = point_mapping[orig_r]
        if orig_j in point_mapping:
            new_p["jobId"] = point_mapping[orig_j]

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
    Sincroniza estrictamente los valores de display (residents, jobs) y referencias (popIds)
    de cada demand_point con la suma real de los pops que viajan a través de él.
    Garantiza que la simulación de Subway Builder y la visualización de burbujas coincidan 1:1.
    Elimina nodos huérfanos que no tienen flujos activos (a menos que sean POIs especiales).
    """
    from collections import defaultdict
    res_by_id = defaultdict(int)
    jobs_by_id = defaultdict(int)
    pop_ids_by_point = defaultdict(list)

    clean_pops = []
    for idx, p in enumerate(pops, start=1):
        if p["size"] <= 0:
            continue
        pid = f"pop_{idx:06d}"
        pop_dict = {
            "id": pid,
            "size": int(p["size"]),
            "residenceId": str(p["residenceId"]),
            "jobId": str(p["jobId"]),
            "drivingSeconds": int(p.get("drivingSeconds", 180)),
            "drivingDistance": int(p.get("drivingDistance", 5000))
        }
        if include_driving_path is not False and "drivingPath" in p and p["drivingPath"]:
            pop_dict["drivingPath"] = p["drivingPath"]
        clean_pops.append(pop_dict)
        res_by_id[pop_dict["residenceId"]] += pop_dict["size"]
        jobs_by_id[pop_dict["jobId"]] += pop_dict["size"]
        pop_ids_by_point[pop_dict["residenceId"]].append(pid)
        pop_ids_by_point[pop_dict["jobId"]].append(pid)

    synced_points = []
    for pt in demand_points:
        pid = pt["id"]
        r_total = res_by_id[pid]
        j_total = jobs_by_id[pid]
        is_sp = bool(pt.get("is_special", False))

        if remove_orphans and r_total == 0 and j_total == 0 and not is_sp:
            continue

        p_copy = dict(pt)
        p_copy["residents"] = r_total
        if is_sp and j_total == 0 and pt.get("jobs", 0) > 0:
            p_copy["jobs"] = int(pt["jobs"])
        else:
            p_copy["jobs"] = j_total
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


def _calibrate_beta_from_demand_points(
    demand_points: List[Dict],
    bbox: Optional[List[float]] = None,
    isolated_zones: Optional[List[Dict]] = None,
    max_distance_km: Any = 55.0
) -> Optional[Dict[str, Any]]:
    """
    Calibra analítica y empíricamente el coeficiente de fricción espacial (beta)
    analizando la dispersión morfológica de centroides de demanda (PEA residencial
    y puestos de trabajo ordinarios), alineado estrictamente con las restricciones
    de admisibilidad de Furness / IPFP (zonas aisladas, distancia máxima y auto-viajes).
    """
    if not demand_points or not isinstance(demand_points, list):
        return None

    def _safe_pos_float(val: Any) -> float:
        if val is None:
            return 0.0
        try:
            f = float(val)
            return f if (math.isfinite(f) and f > 0.0) else 0.0
        except (ValueError, TypeError):
            return 0.0

    origins_raw = []
    destinations_raw = []
    for p in demand_points:
        if not isinstance(p, dict):
            continue
        loc = p.get("location")
        if not loc or len(loc) < 2:
            continue
        try:
            lon, lat = float(loc[0]), float(loc[1])
            if not (math.isfinite(lon) and math.isfinite(lat)):
                continue
        except (ValueError, TypeError):
            continue

        pea = _safe_pos_float(p.get("pea_15ymas", 0))
        jobs = _safe_pos_float(p.get("jobs", 0))
        is_special = bool(p.get("is_special", False))
        pid = str(p.get("id") or "")

        if pea > 0.0:
            origins_raw.append({"lon": lon, "lat": lat, "pea": pea, "id": pid})
        if jobs > 0.0 and not is_special:
            destinations_raw.append({"lon": lon, "lat": lat, "jobs": jobs, "id": pid})

    if len(origins_raw) < 2 or len(destinations_raw) < 2:
        return None

    # Agrupación espacial por coordenadas para garantizar invarianza ante fragmentación representacional (ESS)
    # Conservando simultáneamente el desglose de masa por ID para auto-viajes idénticos a Furness
    orig_spatial = {}
    for o in origins_raw:
        k = (round(o["lon"], 5), round(o["lat"], 5))
        if k not in orig_spatial:
            orig_spatial[k] = {"lon": o["lon"], "lat": o["lat"], "pea": 0.0, "pea_by_id": {}}
        orig_spatial[k]["pea"] += o["pea"]
        if o["id"]:
            orig_spatial[k]["pea_by_id"][o["id"]] = orig_spatial[k]["pea_by_id"].get(o["id"], 0.0) + o["pea"]

    dest_spatial = {}
    for d in destinations_raw:
        k = (round(d["lon"], 5), round(d["lat"], 5))
        if k not in dest_spatial:
            dest_spatial[k] = {"lon": d["lon"], "lat": d["lat"], "jobs": 0.0, "jobs_by_id": {}}
        dest_spatial[k]["jobs"] += d["jobs"]
        if d["id"]:
            dest_spatial[k]["jobs_by_id"][d["id"]] = dest_spatial[k]["jobs_by_id"].get(d["id"], 0.0) + d["jobs"]

    origins = list(orig_spatial.values())
    destinations = list(dest_spatial.values())

    coord_to_dest_idx = {(round(d["lon"], 5), round(d["lat"], 5)): v_idx for v_idx, d in enumerate(destinations)}
    raw_dest_lons = np.array([d["lon"] for d in destinations_raw], dtype=np.float64)
    raw_dest_lats = np.array([d["lat"] for d in destinations_raw], dtype=np.float64)
    rlat_raw_d = np.radians(raw_dest_lats)
    rlon_raw_d = np.radians(raw_dest_lons)
    raw_to_spatial_dest = np.array([
        coord_to_dest_idx[(round(d["lon"], 5), round(d["lat"], 5))]
        for d in destinations_raw
    ], dtype=np.int32)

    if len(origins) < 2 or len(destinations) < 2:
        return None

    total_pea = sum(o["pea"] for o in origins)
    total_jobs = sum(d["jobs"] for d in destinations)
    if total_pea <= 0.0 or total_jobs <= 0.0:
        return None

    orig_lons = np.array([o["lon"] for o in origins], dtype=np.float64)
    orig_lats = np.array([o["lat"] for o in origins], dtype=np.float64)
    orig_pea = np.array([o["pea"] for o in origins], dtype=np.float64)

    dest_lons = np.array([d["lon"] for d in destinations], dtype=np.float64)
    dest_lats = np.array([d["lat"] for d in destinations], dtype=np.float64)
    dest_jobs = np.array([d["jobs"] for d in destinations], dtype=np.float64)

    orig_w = orig_pea / total_pea
    dest_w = dest_jobs / total_jobs

    # ESS invariante a nivel de nodo espacial único
    ess_o = float(1.0 / max(1e-12, np.sum(orig_w**2)))
    ess_d = float(1.0 / max(1e-12, np.sum(dest_w**2)))
    conf_factor = float(min(1.0, ess_o / 20.0, ess_d / 20.0))

    # Asignación de zonas aisladas idéntica a Furness
    orig_coords_deg = np.column_stack([orig_lons, orig_lats])
    dest_coords_deg = np.column_stack([dest_lons, dest_lats])
    orig_zones = assign_zones(orig_coords_deg, isolated_zones) if isolated_zones else np.zeros(len(origins), dtype=int)
    dest_zones = assign_zones(dest_coords_deg, isolated_zones) if isolated_zones else np.zeros(len(destinations), dtype=int)

    # Conteo de destinos disponibles por zona (Furness anula auto-viajes sólo si len(dest_indices) > 1)
    raw_dest_count_per_zone = {}
    for j, d in enumerate(destinations):
        z_val = int(dest_zones[j])
        cnt = max(1, len(d["jobs_by_id"]))
        raw_dest_count_per_zone[z_val] = raw_dest_count_per_zone.get(z_val, 0) + cnt

    # Mapeo invertido de puestos de trabajo por ID hacia celdas destino
    dest_id_to_nodes = {}
    for v_idx, d in enumerate(destinations):
        for did, j_val in d["jobs_by_id"].items():
            dest_id_to_nodes.setdefault(did, []).append((v_idx, j_val))

    # Precomputar deducción de masa cruzada para pares de celdas que comparten ID (auto-viajes reales)
    shared_deductions_map = {}
    for u_idx, o in enumerate(origins):
        u_deductions = {}
        for oid, p_val in o["pea_by_id"].items():
            if oid in dest_id_to_nodes:
                for v_idx, j_val in dest_id_to_nodes[oid]:
                    u_deductions[v_idx] = u_deductions.get(v_idx, 0.0) + (p_val * j_val)
        if u_deductions:
            shared_deductions_map[u_idx] = u_deductions

    # -------------------------------------------------------------------------
    # Soporte Espacial y Grafo Bipartito de Interacción Admisible
    # Alineado estrictamente con Furness / IPFP (zonas aisladas, distancia máxima,
    # auto-viajes y conectividad de respaldo para huérfanos).
    # Streaming adaptativo para mantener consumo de RAM incremental acotado (< 30 MB) sin materializar pares densos.
    # -------------------------------------------------------------------------
    effective_max_dist = sanitize_max_distance_km(max_distance_km, default=55.0)
    effective_max_dist = max(5.0, effective_max_dist)

    n_orig = len(origins)
    n_dest = len(destinations)

    parent = np.arange(n_orig + n_dest, dtype=np.int32)
    dest_comp = np.arange(n_dest, dtype=np.int32) + n_orig

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    orig_has_dest = np.zeros(n_orig, dtype=bool)
    dest_has_orig = np.zeros(n_dest, dtype=bool)

    BIN_WIDTH = 0.25
    num_bins = max(10, int(math.ceil(effective_max_dist / BIN_WIDTH)))
    H = np.zeros(num_bins, dtype=np.float64)
    bin_centers = (np.arange(num_bins, dtype=np.float64) + 0.5) * BIN_WIDTH

    rlat_o = np.radians(orig_lats)
    rlon_o = np.radians(orig_lons)
    rlat_d = np.radians(dest_lats)
    rlon_d = np.radians(dest_lons)

    target_elements_per_chunk = 100_000
    chunk_size = max(1, min(250, target_elements_per_chunk // max(1, len(dest_lons))))
    admissible_pairs_count = 0
    total_denom = total_pea * total_jobs

    for start_idx in range(0, len(orig_lons), chunk_size):
        end_idx = min(start_idx + chunk_size, len(orig_lons))
        chunk_len = end_idx - start_idx

        chunk_lat_o = rlat_o[start_idx:end_idx, np.newaxis]
        chunk_lon_o = rlon_o[start_idx:end_idx, np.newaxis]
        chunk_w_o = orig_w[start_idx:end_idx, np.newaxis]
        chunk_z_o = orig_zones[start_idx:end_idx, np.newaxis]

        dlat = rlat_d[np.newaxis, :] - chunk_lat_o
        dlon = rlon_d[np.newaxis, :] - chunk_lon_o
        sin_half_dlat = np.sin(0.5 * dlat)
        sin_half_dlon = np.sin(0.5 * dlon)
        a = sin_half_dlat**2 + np.cos(chunk_lat_o) * np.cos(rlat_d[np.newaxis, :]) * sin_half_dlon**2
        d_km = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

        # Admisibilidad idéntica a Furness:
        # 1. Zonas aisladas: orígenes en zona z solo viajan a destinos en la misma zona z
        zone_mask = (chunk_z_o == dest_zones[np.newaxis, :])
        # 2. Truncamiento por distancia máxima
        dist_mask = (d_km <= effective_max_dist)
        admissible = zone_mask & dist_mask

        pair_w = np.where(admissible, chunk_w_o * dest_w[np.newaxis, :], 0.0)

        # 3. Deducción estricta de auto-viajes a nivel de ID individual
        for local_u, u_idx in enumerate(range(start_idx, end_idx)):
            z_u = int(orig_zones[u_idx])
            if raw_dest_count_per_zone.get(z_u, 0) > 1 and u_idx in shared_deductions_map:
                for v_idx, ded_mass in shared_deductions_map[u_idx].items():
                    if admissible[local_u, v_idx]:
                        ded_w = ded_mass / total_denom
                        pair_w[local_u, v_idx] = max(0.0, pair_w[local_u, v_idx] - ded_w)

        valid_mask = pair_w > 0.0
        if np.any(valid_mask):
            admissible_pairs_count += int(np.sum(valid_mask))
            valid_d = d_km[valid_mask]
            valid_w = pair_w[valid_mask]
            bin_idx = np.clip((valid_d / BIN_WIDTH).astype(np.int64), 0, num_bins - 1)
            np.add.at(H, bin_idx, valid_w)

            orig_has_dest[start_idx:end_idx] |= np.any(valid_mask, axis=1)
            dest_has_orig |= np.any(valid_mask, axis=0)

            # Grafo bipartito: conectar u_node (origen) con v_idx (destinos admisibles con flujo positivo)
            for local_u in range(chunk_len):
                u_node = start_idx + local_u
                v_idx = np.where(valid_mask[local_u])[0]
                if len(v_idx) == 0:
                    continue
                v0 = v_idx[0]
                r0 = find(dest_comp[v0])
                parent[u_node] = r0
                comps = dest_comp[v_idx]
                diff = (comps != r0)
                if np.any(diff):
                    for c in np.unique(comps[diff]):
                        rc = find(c)
                        if rc != r0:
                            parent[rc] = r0
                    dest_comp[v_idx] = r0

    # Soporte de Respaldo para Filas/Columnas Huérfanas (Equivalencia exacta con simulate_gravity_demand)
    # Furness conecta cada fila o columna sin pares admisibles a sus <= 5 destinos/orígenes
    # más cercanos en la misma zona. Si una zona carece totalmente de empleo local (zona residencial huérfana),
    # simulate_gravity_demand() conecta los residentes uniformemente a los <= 5 destinos globales más cercanos.
    orphan_pairs = {}
    if np.any(~orig_has_dest):
        orphan_origins = np.where(~orig_has_dest)[0]
        for u_idx in orphan_origins:
            z_u = int(orig_zones[u_idx])
            cand_dests = np.where(dest_zones == z_u)[0]
            if len(cand_dests) > 0:
                # 1. Respaldo local de Furness dentro de la misma zona
                dlat = rlat_d[cand_dests] - rlat_o[u_idx]
                dlon = rlon_d[cand_dests] - rlon_o[u_idx]
                sin_half_dlat = np.sin(0.5 * dlat)
                sin_half_dlon = np.sin(0.5 * dlon)
                a = sin_half_dlat**2 + math.cos(rlat_o[u_idx]) * np.cos(rlat_d[cand_dests]) * sin_half_dlon**2
                d_cand = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

                d_eval = d_cand.copy()
                if raw_dest_count_per_zone.get(z_u, 0) > 1 and u_idx in shared_deductions_map:
                    for k, v_idx in enumerate(cand_dests):
                        if v_idx in shared_deductions_map[u_idx]:
                            ded_w = shared_deductions_map[u_idx][v_idx] / total_denom
                            raw_w = orig_w[u_idx] * dest_w[v_idx]
                            if raw_w - ded_w <= 1e-12:
                                d_eval[k] += 1e6

                k_closest = min(5, len(cand_dests))
                closest_idx = np.argsort(d_eval)[:k_closest]
                for c_idx in closest_idx:
                    v_idx = cand_dests[c_idx]
                    d_val = float(d_cand[c_idx])
                    pw = orig_w[u_idx] * dest_w[v_idx]
                    if raw_dest_count_per_zone.get(z_u, 0) > 1 and u_idx in shared_deductions_map:
                        if v_idx in shared_deductions_map[u_idx]:
                            ded_w = shared_deductions_map[u_idx][v_idx] / total_denom
                            pw = max(0.0, pw - ded_w)
                    if pw > 0.0:
                        pair_key = (u_idx, v_idx)
                        orphan_pairs[pair_key] = (d_val, pw)
                        orig_has_dest[u_idx] = True
                        dest_has_orig[v_idx] = True

            # 2. Respaldo global idéntico a simulate_gravity_demand() (líneas 1175-1178 y 1186-1188)
            # Para zonas exclusivamente residenciales sin empleo local o filas residuales en cero
            # Selecciona sobre las filas originales de regular_dests conservando multiplicidad de centroides colocados
            if not orig_has_dest[u_idx]:
                dlat = rlat_raw_d - rlat_o[u_idx]
                dlon = rlon_raw_d - rlon_o[u_idx]
                sin_half_dlat = np.sin(0.5 * dlat)
                sin_half_dlon = np.sin(0.5 * dlon)
                a = sin_half_dlat**2 + math.cos(rlat_o[u_idx]) * np.cos(rlat_raw_d) * sin_half_dlon**2
                d_raw_all = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

                k_closest = min(5, len(destinations_raw))
                closest_raw_idx = np.argsort(d_raw_all)[:k_closest]
                unit_w = orig_w[u_idx] / float(k_closest)
                for r_idx in closest_raw_idx:
                    v_idx = int(raw_to_spatial_dest[r_idx])
                    d_val = float(d_raw_all[r_idx])
                    pair_key = (u_idx, v_idx)
                    if pair_key in orphan_pairs:
                        old_d, old_w = orphan_pairs[pair_key]
                        orphan_pairs[pair_key] = (old_d, old_w + unit_w)
                    else:
                        orphan_pairs[pair_key] = (d_val, unit_w)
                    orig_has_dest[u_idx] = True
                    dest_has_orig[v_idx] = True

    if np.any(~dest_has_orig):
        orphan_dests = np.where(~dest_has_orig)[0]
        for v_idx in orphan_dests:
            z_v = int(dest_zones[v_idx])
            cand_origs = np.where(orig_zones == z_v)[0]
            if len(cand_origs) == 0:
                continue

            dlat = rlat_d[v_idx] - rlat_o[cand_origs]
            dlon = rlon_d[v_idx] - rlon_o[cand_origs]
            sin_half_dlat = np.sin(0.5 * dlat)
            sin_half_dlon = np.sin(0.5 * dlon)
            a = sin_half_dlat**2 + np.cos(rlat_o[cand_origs]) * math.cos(rlat_d[v_idx]) * sin_half_dlon**2
            d_cand = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

            d_eval = d_cand.copy()
            if raw_dest_count_per_zone.get(z_v, 0) > 1:
                for k, u_idx in enumerate(cand_origs):
                    if u_idx in shared_deductions_map and v_idx in shared_deductions_map[u_idx]:
                        ded_w = shared_deductions_map[u_idx][v_idx] / total_denom
                        raw_w = orig_w[u_idx] * dest_w[v_idx]
                        if raw_w - ded_w <= 1e-12:
                            d_eval[k] += 1e6

            k_closest = min(5, len(cand_origs))
            closest_idx = np.argsort(d_eval)[:k_closest]
            for c_idx in closest_idx:
                u_idx = cand_origs[c_idx]
                d_val = float(d_cand[c_idx])
                pw = orig_w[u_idx] * dest_w[v_idx]
                if raw_dest_count_per_zone.get(z_v, 0) > 1 and u_idx in shared_deductions_map:
                    if v_idx in shared_deductions_map[u_idx]:
                        ded_w = shared_deductions_map[u_idx][v_idx] / total_denom
                        pw = max(0.0, pw - ded_w)
                if pw > 0.0:
                    pair_key = (u_idx, v_idx)
                    orphan_pairs[pair_key] = (d_val, pw)
                    orig_has_dest[u_idx] = True
                    dest_has_orig[v_idx] = True

    if orphan_pairs:
        max_orphan_d = max(d for d, _ in orphan_pairs.values())
        if max_orphan_d >= effective_max_dist:
            new_num_bins = int(math.ceil(max_orphan_d / BIN_WIDTH)) + 1
            if new_num_bins > num_bins:
                extra_bins = new_num_bins - num_bins
                H = np.pad(H, (0, extra_bins), mode='constant')
                bin_centers = (np.arange(new_num_bins, dtype=np.float64) + 0.5) * BIN_WIDTH
                num_bins = new_num_bins

        for (u_idx, v_idx), (d_val, pw) in orphan_pairs.items():
            admissible_pairs_count += 1
            bin_idx = np.clip(int(d_val / BIN_WIDTH), 0, num_bins - 1)
            H[bin_idx] += pw

            ru = find(u_idx)
            rv = find(dest_comp[v_idx])
            if ru != rv:
                parent[ru] = rv
                dest_comp[v_idx] = rv

    h_sum = H.sum()
    if h_sum <= 0 or admissible_pairs_count < 2:
        return None
    H /= h_sum

    opportunity_mean_km = float(np.sum(H * bin_centers))
    cum_h = np.cumsum(H)

    def _interp_quantile(pct_val: float) -> float:
        idx = int(np.searchsorted(cum_h, pct_val))
        if idx == 0:
            denom = max(1e-12, cum_h[0])
            alpha = min(1.0, max(0.0, pct_val / denom))
            return float(alpha * BIN_WIDTH)
        idx = min(idx, num_bins - 1)
        prev_c = cum_h[idx - 1]
        curr_c = cum_h[idx]
        denom = max(1e-12, curr_c - prev_c)
        alpha = min(1.0, max(0.0, (pct_val - prev_c) / denom))
        return float(idx * BIN_WIDTH + alpha * BIN_WIDTH)

    opportunity_median_km = _interp_quantile(0.50)
    opportunity_p75_km = _interp_quantile(0.75)

    # -------------------------------------------------------------------------
    # Morfología por Componentes Conexos del Grafo Bipartito Admisible
    # Calculada exclusivamente sobre componentes con interacción bidireccional
    # (PEA > 0 y Empleo > 0), evitando puentes artificiales O-O o D-D.
    # -------------------------------------------------------------------------
    orig_comp = np.array([find(i) for i in range(len(origins))], dtype=int)
    dest_comp_roots = np.array([find(dest_comp[j]) for j in range(len(destinations))], dtype=int)
    unique_comps = np.unique(np.concatenate([orig_comp, dest_comp_roots]))

    comp_metrics_list = []
    comp_masses = []

    for c in unique_comps:
        o_mask = (orig_comp == c)
        d_mask = (dest_comp_roots == c)

        c_pea = float(np.sum(orig_pea[o_mask]))
        c_jobs = float(np.sum(dest_jobs[d_mask]))

        # Solo componentes con capacidad de interacción interna admisible (PEA y Empleo)
        if c_pea <= 0.0 or c_jobs <= 0.0:
            continue

        c_orig_lons = orig_lons[o_mask]
        c_orig_lats = orig_lats[o_mask]
        c_dest_lons = dest_lons[d_mask]
        c_dest_lats = dest_lats[d_mask]

        c_orig_w = orig_pea[o_mask] / c_pea
        c_dest_w = dest_jobs[d_mask] / c_jobs

        c_comb_lons = np.concatenate([c_orig_lons, c_dest_lons])
        c_comb_lats = np.concatenate([c_orig_lats, c_dest_lats])
        c_comb_w = np.concatenate([0.5 * c_orig_w, 0.5 * c_dest_w])

        c_center_lon = float(np.sum(c_comb_w * c_comb_lons))
        c_center_lat = float(np.sum(c_comb_w * c_comb_lats))

        c_cos_lat = math.cos(math.radians(c_center_lat))
        c_kx = 111.320 * c_cos_lat
        c_ky = 110.574

        c_orig_x = (c_orig_lons - c_center_lon) * c_kx
        c_orig_y = (c_orig_lats - c_center_lat) * c_ky
        c_dest_x = (c_dest_lons - c_center_lon) * c_kx
        c_dest_y = (c_dest_lats - c_center_lat) * c_ky

        mu_o_x, mu_o_y = float(np.sum(c_orig_w * c_orig_x)), float(np.sum(c_orig_w * c_orig_y))
        mu_d_x, mu_d_y = float(np.sum(c_dest_w * c_dest_x)), float(np.sum(c_dest_w * c_dest_y))
        c_centroid_sep = float(math.hypot(mu_o_x - mu_d_x, mu_o_y - mu_d_y))

        c_orig_dx = c_orig_x - mu_o_x
        c_orig_dy = c_orig_y - mu_o_y
        c_origin_rg = float(math.sqrt(max(0.0, np.sum(c_orig_w * (c_orig_dx**2 + c_orig_dy**2)))))

        c_dest_dx = c_dest_x - mu_d_x
        c_dest_dy = c_dest_y - mu_d_y
        c_dest_rg = float(math.sqrt(max(0.0, np.sum(c_dest_w * (c_dest_dx**2 + c_dest_dy**2)))))

        c_comb_x = np.concatenate([c_orig_x, c_dest_x])
        c_comb_y = np.concatenate([c_orig_y, c_dest_y])

        c_var_xx = float(np.sum(c_comb_w * c_comb_x**2))
        c_var_yy = float(np.sum(c_comb_w * c_comb_y**2))
        c_cov_xy = float(np.sum(c_comb_w * c_comb_x * c_comb_y))
        c_combined_rg = float(math.sqrt(max(0.0, c_var_xx + c_var_yy)))

        trace = c_var_xx + c_var_yy
        diff = c_var_xx - c_var_yy
        delta = math.sqrt(diff**2 + 4.0 * c_cov_xy**2)
        l1 = max(0.0, (trace + delta) / 2.0)
        l2 = max(0.0, (trace - delta) / 2.0)

        c_major_axis = float(math.sqrt(l1))
        c_minor_axis = float(math.sqrt(l2))
        c_elongation = float(c_major_axis / max(c_minor_axis, 0.05))

        c_orientation_rad = 0.5 * math.atan2(2.0 * c_cov_xy, diff)
        c_orientation_deg = float(math.degrees(c_orientation_rad) % 180.0)

        mass_frac = float(0.5 * (c_pea / total_pea) + 0.5 * (c_jobs / total_jobs))
        comp_masses.append(mass_frac)
        comp_metrics_list.append({
            "component": c,
            "center_lon": c_center_lon,
            "center_lat": c_center_lat,
            "origin_rg_km": c_origin_rg,
            "dest_rg_km": c_dest_rg,
            "combined_rg_km": c_combined_rg,
            "major_axis_km": c_major_axis,
            "minor_axis_km": c_minor_axis,
            "elongation_ratio": c_elongation,
            "centroid_sep_km": c_centroid_sep,
            "orientation_deg": c_orientation_deg,
            "mass_fraction": mass_frac
        })

    if not comp_masses or sum(comp_masses) <= 0.0:
        return None

    mass_weights = np.array(comp_masses, dtype=np.float64) / sum(comp_masses)

    origin_rg_km = float(np.sum(mass_weights * [m["origin_rg_km"] for m in comp_metrics_list]))
    dest_rg_km = float(np.sum(mass_weights * [m["dest_rg_km"] for m in comp_metrics_list]))
    combined_rg_km = float(np.sum(mass_weights * [m["combined_rg_km"] for m in comp_metrics_list]))
    major_axis_km = float(np.sum(mass_weights * [m["major_axis_km"] for m in comp_metrics_list]))
    minor_axis_km = float(np.sum(mass_weights * [m["minor_axis_km"] for m in comp_metrics_list]))
    elongation_ratio = float(major_axis_km / max(minor_axis_km, 0.05))
    centroid_sep_km = float(np.sum(mass_weights * [m["centroid_sep_km"] for m in comp_metrics_list]))

    dominant_idx = int(np.argmax(mass_weights))
    center_lon = comp_metrics_list[dominant_idx]["center_lon"]
    center_lat = comp_metrics_list[dominant_idx]["center_lat"]
    orientation_deg = comp_metrics_list[dominant_idx]["orientation_deg"]
    opportunity_rms_km = float(math.sqrt(max(0.0, origin_rg_km**2 + dest_rg_km**2 + centroid_sep_km**2)))

    # Diagonal de BBOX (referencial)
    diag_km = 40.0
    if bbox and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = [float(x) for x in bbox]
        rlat1, rlon1 = math.radians(min_lat), math.radians(min_lon)
        rlat2, rlon2 = math.radians(max_lat), math.radians(max_lon)
        a_b = math.sin((rlat2 - rlat1) / 2.0)**2 + math.cos(rlat1) * math.cos(rlat2) * math.sin((rlon2 - rlon1) / 2.0)**2
        diag_km = float(6371.0 * 2.0 * math.asin(math.sqrt(max(0.0, min(1.0, a_b)))))

    # Clasificación Morfológica en Arquetipos
    is_linear = (elongation_ratio >= 2.25 and major_axis_km >= 8.0 and opportunity_p75_km >= 14.0)

    if is_linear:
        archetype = "corredor_lineal"
        label = "Conurbación Lineal / Corredor Turístico-Industrial"
        target_median_km = 12.0
        beta_prior = 0.095
        expected_median_km = "14 – 18 km"
        rationale = f"Corredor lineal detectado (elongación={elongation_ratio:.2f}x, eje mayor={major_axis_km:.1f} km, P75 OD={opportunity_p75_km:.1f} km)."
    elif not is_linear and (
        (combined_rg_km >= 20.0 and opportunity_mean_km >= 15.0)
        or opportunity_mean_km >= 25.0
        or opportunity_p75_km >= 32.0
    ):
        archetype = "megaciudad"
        label = "Megaciudad / Conurbación Metropolitana Masiva"
        target_median_km = 16.0
        beta_prior = 0.085
        expected_median_km = "18 – 25 km"
        rationale = f"Conurbación metropolitana masiva (Rg={combined_rg_km:.1f} km, media OD={opportunity_mean_km:.1f} km)."
    elif (combined_rg_km <= 8.0 or opportunity_mean_km <= 6.0) and opportunity_mean_km <= 12.0 and centroid_sep_km <= 6.0:
        archetype = "compacta"
        label = "Ciudad Monocéntrica Compacta"
        target_median_km = 5.5
        beta_prior = 0.150
        expected_median_km = "6 – 9 km"
        rationale = f"Morfología concentrada monocéntrica (Rg={combined_rg_km:.1f} km, media OD={opportunity_mean_km:.1f} km)."
    else:
        archetype = "intermedia"
        label = "Metrópoli Policéntrica Intermedia"
        target_median_km = 8.5
        beta_prior = 0.120
        expected_median_km = "10 – 15 km"
        rationale = f"Escala metropolitana intermedia (Rg={combined_rg_km:.1f} km, media OD={opportunity_mean_km:.1f} km)."

    # La mediana objetivo no puede superar la fracción física de oportunidad admisible
    target_median_eff = min(target_median_km, 0.70 * opportunity_median_km)

    # Calibración Numérica por Bisección sobre H_beta(d) = H(d) * exp(-beta * d)
    def _eval_median_for_beta(b_val: float) -> float:
        w_b = H * np.exp(-b_val * bin_centers)
        s_b = w_b.sum()
        if s_b <= 0:
            return 0.0
        c_b = np.cumsum(w_b) / s_b
        idx = int(np.searchsorted(c_b, 0.50))
        if idx == 0:
            denom = max(1e-12, c_b[0])
            alpha = min(1.0, max(0.0, 0.50 / denom))
            return float(alpha * BIN_WIDTH)
        idx = min(idx, num_bins - 1)
        prev_c = c_b[idx - 1]
        curr_c = c_b[idx]
        denom = max(1e-12, curr_c - prev_c)
        alpha = min(1.0, max(0.0, (0.50 - prev_c) / denom))
        return float(idx * BIN_WIDTH + alpha * BIN_WIDTH)

    BETA_MIN = 0.065
    BETA_MAX = 0.180
    med_at_min = _eval_median_for_beta(BETA_MIN)
    med_at_max = _eval_median_for_beta(BETA_MAX)

    if target_median_eff >= med_at_min:
        beta_emp = BETA_MIN
        calib_status = "bounded_min"
    elif target_median_eff <= med_at_max:
        beta_emp = BETA_MAX
        calib_status = "bounded_max"
    else:
        low_b, high_b = BETA_MIN, BETA_MAX
        for _ in range(18):
            mid_b = (low_b + high_b) / 2.0
            mid_med = _eval_median_for_beta(mid_b)
            if mid_med > target_median_eff:
                low_b = mid_b
            else:
                high_b = mid_b
        beta_emp = (low_b + high_b) / 2.0
        calib_status = "solved"

    # Regularización con prior según ESS
    beta_final = float(np.clip((1.0 - conf_factor) * beta_prior + conf_factor * beta_emp, BETA_MIN, BETA_MAX))
    rec_beta = round(beta_final, 3)

    # Evaluación de mediana con el beta_final efectivamente retornado
    calibrated_median_km = float(_eval_median_for_beta(rec_beta))

    return {
        "recommended_beta": rec_beta,
        "archetype": archetype,
        "label": label,
        "metrics": {
            "method": "demand_points",
            "origin_count": len(origins),
            "destination_count": len(destinations),
            "raw_origin_records": len(origins_raw),
            "raw_destination_records": len(destinations_raw),
            "total_pea": float(total_pea),
            "total_jobs": float(total_jobs),
            "effective_origins": round(ess_o, 1),
            "effective_destinations": round(ess_d, 1),
            "center_lon": round(center_lon, 5),
            "center_lat": round(center_lat, 5),
            "origin_rg_km": round(origin_rg_km, 2),
            "destination_rg_km": round(dest_rg_km, 2),
            "combined_rg_km": round(combined_rg_km, 2),
            "major_axis_km": round(major_axis_km, 2),
            "minor_axis_km": round(minor_axis_km, 2),
            "elongation_ratio": round(elongation_ratio, 2),
            "orientation_deg": round(orientation_deg, 1),
            "centroid_separation_km": round(centroid_sep_km, 2),
            "opportunity_mean_km": round(opportunity_mean_km, 2),
            "opportunity_median_km": round(opportunity_median_km, 2),
            "opportunity_p75_km": round(opportunity_p75_km, 2),
            "opportunity_rms_km": round(opportunity_rms_km, 2),
            "target_median_km": round(target_median_eff, 2),
            "calibrated_median_km": round(calibrated_median_km, 2),
            "calibration_confidence": round(conf_factor, 2),
            "calibration_status": calib_status,
            "pair_mode": "exact_chunked",
            "max_distance_km": round(effective_max_dist, 1),
            "admissible_pairs": admissible_pairs_count,
            "beta_bounds": [BETA_MIN, BETA_MAX],
            "bbox_diagonal_km": round(diag_km, 1)
        },
        "expected_median_km": expected_median_km,
        "rationale": rationale
    }


def recommend_gravity_beta(
    bbox: Optional[List[float]] = None,
    city_archetype: Optional[str] = None,
    demand_points: Optional[List[Dict]] = None,
    isolated_zones: Optional[List[Dict]] = None,
    max_distance_km: Any = 55.0
) -> Dict[str, Any]:
    """
    Recomienda el coeficiente de fricción espacial óptimo (beta) según:
    1. Override manual de arquetipo urbano (city_archetype).
    2. Calibración analítica y empírica a partir de la malla espacial de centroides
       de demanda (demand_points) alineada a restricciones Furness (isolated_zones, max_distance_km).
    3. Fallback heurístico por diagonal del BBOX si no se dispone de centroides.
    Emula y expande la calibración empírica oficial de Colin Miller en Subway Builder.
    """
    arch = str(city_archetype or "").strip().lower()

    if arch in ["compacta", "compact", "pequeña"]:
        return {
            "archetype": "compacta",
            "label": "Ciudad Compacta",
            "recommended_beta": 0.150,
            "expected_median_km": "6 – 9 km",
            "rationale": "Metrópoli concentrada o costera. Fricción alta para evitar dispersión ficticia hacia la periferia rural.",
            "metrics": {"method": "explicit_archetype"}
        }
    elif arch in ["megaciudad", "metropolis", "megacity", "extendida"]:
        return {
            "archetype": "megaciudad",
            "label": "Megaciudad Extendida",
            "recommended_beta": 0.085,
            "expected_median_km": "18 – 25 km",
            "rationale": "Gran valle conurbado con múltiples municipios. Fricción reducida para permitir flujos metropolitanos de largo alcance.",
            "metrics": {"method": "explicit_archetype"}
        }
    elif arch in ["corredor", "corredor_lineal", "lineal", "linear"]:
        return {
            "archetype": "corredor_lineal",
            "label": "Conurbación Lineal / Corredor Turístico-Industrial",
            "recommended_beta": 0.095,
            "expected_median_km": "14 – 18 km",
            "rationale": "Corredor intermunicipal lineal. Fricción modulada para permitir interacción entre polos funcionales contiguos.",
            "metrics": {"method": "explicit_archetype"}
        }
    elif arch in ["intermedia", "intermediate", "media"]:
        return {
            "archetype": "intermedia",
            "label": "Metrópoli Intermedia",
            "recommended_beta": 0.120,
            "expected_median_km": "10 – 15 km",
            "rationale": "Escala metropolitana estándar con balance entre centralidad y expansión suburbana.",
            "metrics": {"method": "explicit_archetype"}
        }

    # Intentar calibración analítica de alta fidelidad con demand_points si están presentes
    if demand_points:
        sanitized_max_dist = sanitize_max_distance_km(max_distance_km, default=55.0)
        calib = _calibrate_beta_from_demand_points(
            demand_points=demand_points,
            bbox=bbox,
            isolated_zones=isolated_zones,
            max_distance_km=sanitized_max_dist
        )
        if calib is not None:
            return calib

    # Fallback BBOX heurístico
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
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (< 28 km). Perfil compacto detectado.",
            "metrics": {"method": "bbox_fallback", "bbox_diagonal_km": round(diag_km, 1)}
        }
    elif diag_km > 65.0:
        return {
            "archetype": "megaciudad",
            "label": "Megaciudad Extendida",
            "diagonal_km": round(diag_km, 1),
            "recommended_beta": 0.085,
            "expected_median_km": "18 – 25 km",
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (> 65 km). Conurbación masiva detectada.",
            "metrics": {"method": "bbox_fallback", "bbox_diagonal_km": round(diag_km, 1)}
        }
    else:
        return {
            "archetype": "intermedia",
            "label": "Metrópoli Intermedia",
            "diagonal_km": round(diag_km, 1),
            "recommended_beta": 0.120,
            "expected_median_km": "10 – 15 km",
            "rationale": f"BBOX diagonal de {diag_km:.1f} km (28–65 km). Escala metropolitana típica.",
            "metrics": {"method": "bbox_fallback", "bbox_diagonal_km": round(diag_km, 1)}
        }

