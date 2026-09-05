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
from typing import List, Dict, Tuple, Optional, Union
from shapely.geometry import Point
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


def build_demand_grid(
    df_denue: pd.DataFrame,
    df_cpv: pd.DataFrame,
    special_pois: List[Dict],
    roads_gdf: gpd.GeoDataFrame,
    grid_size: float = 0.0025,
    min_residents: int = 10,
    min_jobs: int = 3,
    seed: int = 42
) -> Tuple[List[Dict], List[Dict]]:
    """
    Agrega datos de población y empleo en celdas espaciales,
    resuelve la absorción de POIs Especiales y realiza el snapping a la red vial.
    """
    rng = np.random.default_rng(seed)
    
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

    # 3. Sumar Población del Censo a la Malla
    cpv_records = (
        df_cpv[['lon', 'lat', 'pobtot_adj', 'pea_real']].to_dict('records')
        if not df_cpv.empty and all(c in df_cpv.columns for c in ['lon', 'lat', 'pobtot_adj', 'pea_real'])
        else []
    )
    for rec in cpv_records:
        r_lon = float(rec['lon'])
        r_lat = float(rec['lat'])
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


def calculate_commute_impedance(d_km: float) -> Tuple[int, int]:
    """
    Calcula distancia y tiempo de manejo con modelo de congestión urbana continuo no lineal.
    - Tortuosidad continua: tau(d) = 1.20 + 0.20 * exp(-d / 6.0) (de 1.40 urbano a 1.20 autopista).
    - Distancia real estimada: max(150, int(d * 1000.0 * tau(d))).
    - Curva de velocidad suave: V(d) = 15.0 + 55.0 * (1 - exp(-d / 6.0)) km/h.
    - Tiempo de manejo: max(45, int(45 + dist_m / (V(d) / 3.6))).
    Elimina pisos rígidos artificiales (antes 800m y 180s) preservando viajes peatonales/cortos.
    """
    d = max(0.0, float(d_km))
    tau = 1.20 + 0.20 * np.exp(-d / 6.0)
    dist_m = max(150, int(d * 1000.0 * tau))

    speed_kmh = 15.0 + 55.0 * (1.0 - np.exp(-d / 6.0))
    speed_ms = speed_kmh / 3.6
    driving_seconds = max(45, int(45 + dist_m / speed_ms))

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

        # Velocidad de congestión urbana con factor de desvío
        d_km = road_m / 1000.0
        detour_ratio = road_m / max(1.0, euclid_m)
        base_speed = 15.0 + 55.0 * (1.0 - math.exp(-d_km / 6.0))
        speed_factor = 1.0 / math.sqrt(max(1.0, detour_ratio))
        effective_speed_kmh = max(15.0, base_speed * speed_factor)
        speed_ms = effective_speed_kmh / 3.6

        driving_seconds = max(45, int(45 + road_m / speed_ms))
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


def furness_ipfp_balance(
    orig_pea: np.ndarray,
    dest_jobs: np.ndarray,
    dist_km_mat: np.ndarray,
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
    3. Fricción espacial: f(d_ij) = exp(-beta * d_ij) para d_ij <= max_distance_km.

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
    d_target = (dest_jobs.astype(np.float64) / total_d) * total_o
    o_target = orig_pea.astype(np.float64)

    # 2. Matriz de Fricción Espacial Base
    friction = np.exp(-beta * dist_km_mat)
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
    max_pop_size: int = 150,
    target_pop_size: int = 35,
    seed: int = 42,
    isolated_zones: Optional[List[Dict]] = None,
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
            cohort_limit = min(75, max_pop_size)   # Flujo escalonado universitario
        elif sp_id.startswith("AIR_"):
            cohort_limit = min(120, max_pop_size)  # Flujo continuo 24/7 de aeropuerto
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
                orig["popIds"].append(pid)
                if sp_dest["id"] != orig["id"]:
                    sp_dest["popIds"].append(pid)
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

        # Matriz NxM de distancias Haversine
        dlat = dest_coords[:, 1][np.newaxis, :] - orig_coords[:, 1][:, np.newaxis]
        dlon = dest_coords[:, 0][np.newaxis, :] - orig_coords[:, 0][:, np.newaxis]
        a = np.sin(dlat / 2.0)**2 + np.cos(orig_coords[:, 1][:, np.newaxis]) * np.cos(dest_coords[:, 1][np.newaxis, :]) * np.sin(dlon / 2.0)**2
        dist_km_mat = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

        prob_matrix = np.zeros((len(origins), len(regular_dests)), dtype=np.float64)

        # Balanceo de Furness / IPFP ejecutado de forma estanca por cada zona topológica
        unique_orig_zones = np.unique(orig_zones)

        for z in unique_orig_zones:
            orig_indices = np.where((orig_zones == z) & (orig_pea > 0))[0]
            if len(orig_indices) == 0:
                continue

            dest_indices = np.where((dest_zones == z) & (dest_jobs > 0))[0]

            if len(dest_indices) > 0:
                # Sub-matriz de la zona z
                sub_dist = dist_km_mat[np.ix_(orig_indices, dest_indices)].copy()
                sub_pea = orig_pea[orig_indices].copy()
                sub_jobs = dest_jobs[dest_indices].copy()

                # Anular auto-viajes si hay múltiples destinos disponibles en la zona
                if len(dest_indices) > 1:
                    for si, o_idx in enumerate(orig_indices):
                        orig_id = origins[o_idx]["id"]
                        if orig_id in dest_id_to_idx:
                            g_didx = dest_id_to_idx[orig_id]
                            match = np.where(dest_indices == g_didx)[0]
                            if len(match) > 0:
                                sub_dist[si, match[0]] = 1e6

                sub_prob = furness_ipfp_balance(
                    orig_pea=sub_pea,
                    dest_jobs=sub_jobs,
                    dist_km_mat=sub_dist,
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
            pvals = prob_matrix[i] / prob_matrix[i].sum()
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

                while pax_count > 0:
                    chunk = min(pax_count, max_pop_size)
                    pid = f"pop_{pop_id:06d}"
                    pops.append({
                        "id": pid,
                        "size": chunk,
                        "residenceId": orig["id"],
                        "jobId": dest["id"],
                        "drivingSeconds": driving_seconds,
                        "drivingDistance": dist_m
                    })
                    orig["popIds"].append(pid)
                    dest["popIds"].append(pid)
                    pop_id += 1
                    pax_count -= chunk

    return pops


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
