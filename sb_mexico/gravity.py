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
from typing import List, Dict, Tuple, Optional
from shapely.geometry import Point
from shapely.strtree import STRtree
from shapely.ops import nearest_points


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
    
    # 1. Resolver radios de absorción de POIs Especiales
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
        pois_resolved.append({
            **poi,
            "radius_m": radius_m,
            "cos_lat": cos_lat,
            "mode": poi.get("mode", "MAX").upper(),
            "denue_jobs_absorbidos": 0.0,
            "jobs_declarados": int(poi.get("jobs", 0))
        })

    # 2. Asignar DENUE a POIs Especiales o a la Malla Regular
    grid: Dict[str, Dict] = {}
    def get_grid_key(lon: float, lat: float) -> str:
        return f"{int(math.floor(lon / grid_size))}_{int(math.floor(lat / grid_size))}"

    denue_records = df_denue[['lon', 'lat', 'calibrated_jobs']].to_dict('records')
    for rec in denue_records:
        r_lon = float(rec['lon'])
        r_lat = float(rec['lat'])
        r_jobs = float(rec['calibrated_jobs'])
        
        # Verificar si cae dentro de algún POI especial (distancia métrica precisa)
        matched_poi = None
        best_dist = float('inf')
        for poi in pois_resolved:
            d_lon_m = (r_lon - poi["loc"][0]) * 111_320.0 * poi["cos_lat"]
            d_lat_m = (r_lat - poi["loc"][1]) * 110_574.0
            dist_m = math.hypot(d_lon_m, d_lat_m)
            if dist_m <= poi["radius_m"] and dist_m < best_dist:
                matched_poi = poi
                best_dist = dist_m

        if matched_poi is not None:
            matched_poi["denue_jobs_absorbidos"] += r_jobs
            continue  # Se absorbe por el POI más cercano para evitar doble conteo

        # Sumar a la malla regular con acumulación O(1) de memoria
        k = get_grid_key(r_lon, r_lat)
        if k not in grid:
            grid[k] = {"sum_lon": 0.0, "sum_lat": 0.0, "count": 0, "jobs": 0.0, "residents": 0.0, "pea": 0.0}
        cell = grid[k]
        cell["sum_lon"] += r_lon
        cell["sum_lat"] += r_lat
        cell["count"] += 1
        cell["jobs"] += r_jobs

    # 3. Sumar Población del Censo a la Malla
    cpv_records = df_cpv[['lon', 'lat', 'pobtot_adj', 'pea_real']].to_dict('records')
    for rec in cpv_records:
        r_lon = float(rec['lon'])
        r_lat = float(rec['lat'])
        k = get_grid_key(r_lon, r_lat)
        if k not in grid:
            grid[k] = {"sum_lon": 0.0, "sum_lat": 0.0, "count": 0, "jobs": 0.0, "residents": 0.0, "pea": 0.0}
        cell = grid[k]
        cell["sum_lon"] += r_lon
        cell["sum_lat"] += r_lat
        cell["count"] += 1
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

    # 5. Construir lista de demand_points regulares
    demand_points = []
    valid_cells = [
        (k, cell) for k, cell in grid.items()
        if not (cell["jobs"] < min_jobs and cell["residents"] < min_residents)
    ]

    points_to_snap = [
        Point(
            float(cell["sum_lon"] / max(1, cell["count"])),
            float(cell["sum_lat"] / max(1, cell["count"]))
        )
        for _, cell in valid_cells
    ]

    if tree is not None and len(points_to_snap) > 0:
        nearest_indices = tree.query_nearest(points_to_snap, all_matches=False)[1]
    else:
        nearest_indices = [None] * len(points_to_snap)

    for idx, ((k, cell), pt, near_idx) in enumerate(zip(valid_cells, points_to_snap, nearest_indices)):
        lon, lat = pt.x, pt.y
        if near_idx is not None and tree is not None:
            nearest_road = road_geoms[int(near_idx)]
            if pt.distance(nearest_road) > 0.0004:  # > ~44m
                proj_pt = nearest_points(pt, nearest_road)[1]
                lon, lat = proj_pt.x, proj_pt.y

        demand_points.append({
            "id": f"dp_{idx+1:04d}",
            "location": [round(lon, 5), round(lat, 5)],
            "jobs": int(round(cell["jobs"])),
            "residents": int(round(cell["residents"])),
            "pea_15ymas": int(round(cell["pea"])),
            "popIds": []
        })

    # 6. Resolver y agregar POIs Especiales
    poi_audit = []
    if len(pois_resolved) > 0:
        poi_pts = [Point(p["loc"][0], p["loc"][1]) for p in pois_resolved]
        if tree is not None and len(poi_pts) > 0:
            poi_near_indices = tree.query_nearest(poi_pts, all_matches=False)[1]
        else:
            poi_near_indices = [None] * len(poi_pts)

        for poi, pt, near_idx in zip(pois_resolved, poi_pts, poi_near_indices):
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

            lon, lat = poi["loc"][0], poi["loc"][1]
            if near_idx is not None and tree is not None:
                nearest_road = road_geoms[int(near_idx)]
                if pt.distance(nearest_road) > 0.0004:
                    proj_pt = nearest_points(pt, nearest_road)[1]
                    lon, lat = proj_pt.x, proj_pt.y

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
    Calcula distancia y tiempo de manejo con modelo de congestión urbana no lineal.
    - Curva asintótica de velocidad: 18 km/h (centro denso) a 65 km/h (vías rápidas).
    - Tortuosidad de red vial: 1.40 (cuadrícula urbana corta) a 1.25 (autopistas largas).
    """
    # Tortuosidad según longitud del viaje
    tau = 1.25 + 0.15 * np.exp(-d_km / 10.0)
    dist_m = max(800, int(d_km * 1000.0 * tau))

    # Curva de velocidad suave
    speed_kmh = 18.0 + (65.0 - 18.0) * (1.0 - np.exp(-d_km / 8.0))
    speed_ms = speed_kmh / 3.6
    driving_seconds = max(180, int(dist_m / speed_ms))

    return dist_m, driving_seconds


def simulate_gravity_demand(
    demand_points: List[Dict],
    beta: float = 0.12,
    max_distance_km: float = 55.0,
    max_pop_size: int = 150,
    seed: int = 42
) -> List[Dict]:
    """
    Ejecuta el Modelo de Demanda en Dos Capas:
    - Capa 1: Asignación de Cuotas Exactas a POIs Especiales (Aeropuertos, Universidades).
    - Capa 2: Modelo Gravitatorio Multinomial para Empleo Regular (DENUE).
    Garantiza la conservación matemática estricta de la PEA y la cuota exacta de los POIs.
    """
    rng = np.random.default_rng(seed)

    # 1. Separar orígenes residenciales y destinos
    origins = [p for p in demand_points if p.get("pea_15ymas", 0) > 0]
    if not origins:
        raise ValueError("No se encontraron orígenes residenciales con PEA > 0.")

    # Vectorizar coordenadas de orígenes
    orig_coords = np.radians([o["location"] for o in origins])
    orig_pea = np.array([o["pea_15ymas"] for o in origins], dtype=np.int64)

    # Identificar Destinos Especiales (con cuota fija) vs Regulares
    special_dests = [p for p in demand_points if p.get("is_special", False) and p.get("jobs", 0) > 0]
    regular_dests = [p for p in demand_points if not p.get("is_special", False) and p.get("jobs", 0) > 0]

    pops = []
    pop_id = 1

    # =========================================================================
    # CAPA 1: ASIGNACIÓN DE DEMANDA ESPECIAL (CUOTAS EXACTAS)
    # =========================================================================
    for sp_dest in special_dests:
        target_quota = int(sp_dest["jobs"])
        if target_quota <= 0:
            continue

        sp_id = sp_dest["id"]
        # Determinar tamaño de cohorte por tipo de infraestructura
        if sp_id.startswith("UNI_"):
            cohort_limit = 75   # Flujo escalonado universitario
        elif sp_id.startswith("AIR_"):
            cohort_limit = 120  # Flujo continuo 24/7 de aeropuerto
        else:
            cohort_limit = max_pop_size

        sp_coord = np.radians(sp_dest["location"])
        # Distancia Haversine desde todos los orígenes
        dlat = sp_coord[1] - orig_coords[:, 1]
        dlon = sp_coord[0] - orig_coords[:, 0]
        a = np.sin(dlat / 2.0)**2 + np.cos(orig_coords[:, 1]) * np.cos(sp_coord[1]) * np.sin(dlon / 2.0)**2
        dist_km = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

        # Asignación multinomial acotada por capacidad remanente (Bounded Multinomial Allocation)
        remaining_quota = target_quota
        sp_assigned = np.zeros(len(origins), dtype=np.int64)

        # Identificar si el destino especial es también un origen (evitar auto-viajes)
        self_orig_idx = None
        for idx, o in enumerate(origins):
            if o["id"] == sp_id:
                self_orig_idx = idx
                break

        while remaining_quota > 0 and np.any(orig_pea > 0):
            active_mask = orig_pea > 0
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
            draw = rng.multinomial(remaining_quota, sp_probs)
            actual_alloc = np.minimum(draw, orig_pea)
            sp_assigned += actual_alloc
            orig_pea -= actual_alloc
            remaining_quota = target_quota - int(sp_assigned.sum())
            if np.all(draw == actual_alloc) or remaining_quota <= 0:
                break

        # Generar cohortes a partir de los viajeros efectivamente asignados
        for i, count in enumerate(sp_assigned):
            if count <= 0:
                continue

            orig = origins[i]
            d_km = float(dist_km[i])
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
    # CAPA 2: ASIGNACIÓN GRAVITATORIA DE EMPLEO REGULAR (DENUE)
    # =========================================================================
    if regular_dests and np.any(orig_pea > 0):
        dest_coords = np.radians([d["location"] for d in regular_dests])
        dest_jobs = np.array([d["jobs"] for d in regular_dests], dtype=np.float64)
        dest_id_to_idx = {d["id"]: idx for idx, d in enumerate(regular_dests)}

        # Matriz NxM de distancias
        dlat = dest_coords[:, 1][np.newaxis, :] - orig_coords[:, 1][:, np.newaxis]
        dlon = dest_coords[:, 0][np.newaxis, :] - orig_coords[:, 0][:, np.newaxis]
        a = np.sin(dlat / 2.0)**2 + np.cos(orig_coords[:, 1][:, np.newaxis]) * np.cos(dest_coords[:, 1][np.newaxis, :]) * np.sin(dlon / 2.0)**2
        dist_km_mat = 6371.0 * 2.0 * np.arcsin(np.clip(np.sqrt(a), 0.0, 1.0))

        # Atracción y Fricción
        attraction = dest_jobs[np.newaxis, :] ** 0.85
        friction = np.exp(-beta * dist_km_mat)
        friction[dist_km_mat > max_distance_km] = 0.0
        weights = attraction * friction

        # Anular auto-viajes (búsqueda O(1))
        for i, orig in enumerate(origins):
            if orig["id"] in dest_id_to_idx:
                weights[i, dest_id_to_idx[orig["id"]]] = 0.0

        # Normalizar probabilidades
        row_sums = weights.sum(axis=1, keepdims=True)
        zero_rows = (row_sums.reshape(-1) == 0)
        if np.any(zero_rows):
            for i in np.where(zero_rows)[0]:
                nearest_dests = np.argsort(dist_km_mat[i])[:5]
                weights[i, nearest_dests] = 1.0
            row_sums = weights.sum(axis=1, keepdims=True)

        prob_matrix = weights / row_sums

        # Asignar la PEA restante de cada origen
        for i, orig in enumerate(origins):
            rem_pea = int(orig_pea[i])
            if rem_pea <= 0:
                continue

            assignments = rng.multinomial(rem_pea, prob_matrix[i])
            active_dest_indices = np.where(assignments > 0)[0]

            for d_idx in active_dest_indices:
                pax_count = int(assignments[d_idx])
                dest = regular_dests[d_idx]
                d_km = float(dist_km_mat[i, d_idx])
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
