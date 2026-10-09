"""Freeze final geography before allocation. Existing POI rules are an opaque adapter."""
import hashlib
import numpy as np
import pandas as pd
from .population import integer_budgets, validate_population


def build_points(request, evidence, census):
    import geopandas as gpd
    from sb_mexico.residential import place_census, mass_summary
    from sb_mexico.gravity import build_demand_grid, assign_zones, is_point_in_exclusion_zone, prepare_polygon_geom, is_point_in_prepared_polygon
    cfg, city = request.config, request.config['city']
    box = city['bbox']
    bbox = dict(min_lon=box[0], min_lat=box[1], max_lon=box[2], max_lat=box[3])
    frame = place_census(census, evidence['denue'], evidence['blocks_geo'],
                         evidence['areas_geo'], bbox, evidence['layers'])
    placement = frame.attrs['residential_placement']
    validate_population(frame)
    exclusions = cfg.get('exclusion_zones', [])
    core = prepare_polygon_geom(city.get('urban_core_polygon')) if city.get('restrict_demand_to_urban_core', True) else None
    allowed = [not is_point_in_exclusion_zone(r.lon, r.lat, exclusions) and
               (core is None or is_point_in_prepared_polygon(r.lon, r.lat, core)) for r in frame.itertuples()]
    excluded = frame.loc[~np.asarray(allowed, bool)]
    frame = frame.loc[allowed].copy()
    ids = list(frame['_full_identity'])
    groups = list(frame.cve_mun_clean)
    frame['population_integer'] = integer_budgets(frame.pobtot_adj, groups, ids)
    frame['occupied_integer'] = integer_budgets(frame.occupied_residents, groups, ids, frame.population_integer)
    frame['commuters_integer'] = integer_budgets(frame.pea_real, groups, ids, frame.occupied_integer)
    # The unchanged grid sees integer inputs, so its per-cell rounding cannot lose mass.
    grid_frame = frame.copy()
    grid_frame['pobtot_adj'] = frame.population_integer
    grid_frame['pea_real'] = frame.commuters_integer
    denue = evidence['denue']
    denue = denue.loc[denue.lon.between(box[0], box[2]) & denue.lat.between(box[1], box[3])].copy()
    roads = request.roads if request.roads is not None else gpd.GeoDataFrame(geometry=[], crs='EPSG:4326')
    zones = cfg.get('isolated_zones', city.get('isolated_zones', []))
    merge = {}
    points, poi_audit = build_demand_grid(denue, grid_frame, cfg.get('pois') or [], roads,
        grid_size=city.get('grid_size', .002), min_residents=city.get('min_residents', 10),
        min_jobs=city.get('min_jobs', 3), seed=city.get('seed', 42),
        affluence_zones=cfg.get('affluence_zones', []), exclusion_zones=exclusions,
        urban_core_polygon=city.get('urban_core_polygon'),
        restrict_demand_to_urban_core=city.get('restrict_demand_to_urban_core', True),
        isolated_zones=zones, merge_diagnostics=merge)
    zone_ids = assign_zones(np.asarray([p['location'] for p in points]).reshape(-1, 2), zones)
    coordinate_ordinals = {}
    for p, zone in zip(points, zone_ids):
        if not (box[0] <= p['location'][0] <= box[2] and box[1] <= p['location'][1] <= box[3]):
            raise ValueError('Final point outside game BBOX (configuration is unchanged): '+p['id'])
        p['zone'] = int(zone)
        p['attraction'] = p['jobs']
        p['population'] = p['residents']
        p['commuters'] = p['pea_15ymas']
        p['legacy_id'] = p['id']
        if not p.get('is_special'):
            # IDs reflect the final location and territory, not iteration order.
            signature = repr((p['location'], p['zone'])).encode()
            ordinal = coordinate_ordinals.get(signature, 0)
            coordinate_ordinals[signature] = ordinal+1
            if ordinal:
                signature += (':'+str(ordinal)).encode()
            p['id'] = 'dv2_'+hashlib.sha256(signature).hexdigest()[:16]
    if len({p['id'] for p in points}) != len(points):
        raise ValueError('Final point ID collision')
    if sum(p['commuters'] for p in points) != int(frame.commuters_integer.sum()):
        raise ValueError('Grid adapter changed retained commuter budget')
    if any(p['commuters'] > p['population'] or p['commuters'] < 0 for p in points):
        raise ValueError('Final points violate population capacity')
    # Geography labels are estimated from nearest represented residential/establishment evidence.
    from scipy.spatial import cKDTree
    reference = pd.concat([frame[['lon','lat','cve_mun_clean']], denue[['lon','lat','cve_mun_clean']]], ignore_index=True).dropna()
    if points and reference.empty:
        raise ValueError('No geographic references for final points')
    if points:
        lat = float(np.mean(reference.lat))
        scale = np.array([np.cos(np.deg2rad(lat)), 1.])
        tree = cKDTree(reference[['lon','lat']].to_numpy(float)*scale)
        _, nearest = tree.query(np.asarray([p['location'] for p in points])*scale)
        for p, idx in zip(points, nearest):
            p['municipality'] = str(reference.iloc[int(idx)].cve_mun_clean)
    topology = attach_connectivity(points, request.roads)
    from .connectivity import reconcile
    topology['osrm_reconciliation'] = reconcile(request, points)
    report = dict(placement=placement, excluded=mass_summary(excluded), retained=mass_summary(frame),
                  integer_population=int(frame.population_integer.sum()), integer_occupied=int(frame.occupied_integer.sum()),
                  integer_commuters=int(frame.commuters_integer.sum()), grid=merge, poi_audit=poi_audit,
                  municipality_label_basis='nearest source reference; estimated for merged points',
                  connectivity=topology,
                  point_mapping={p['legacy_id']:p['id'] for p in points})
    return points, frame, report


def consolidate_points(request, points):
    """Reuse existing clustering geometry before OD, preserving statistical sums.

    The legacy helper reads sizes from flow-shaped records. These adapter records
    carry geometric weights only, reference no actual destination, and never enter OD.
    """
    from sb_mexico.gravity import cluster_demand_points
    cfg, city = request.config, request.config['city']
    groups = {}
    final = [p for p in points if p.get('is_special')]
    mapping = {p['id']:p['id'] for p in final}
    for p in points:
        if not p.get('is_special'):
            key = (p['zone'], p['municipality'], p.get('road_component'))
            groups.setdefault(key, []).append(p)
    for key, group in sorted(groups.items(), key=lambda x:repr(x[0])):
        weights = [dict(residenceId=p['id'], jobId='__geometry_weight_only__', size=max(1,p['commuters']+p['attraction'])) for p in group]
        local_mapping = {}
        geometry, _ = cluster_demand_points(group, weights,
            isolated_zones=cfg.get('isolated_zones', city.get('isolated_zones', [])),
            exclusion_zones=cfg.get('exclusion_zones', []),
            urban_core_polygon=city.get('urban_core_polygon') if city.get('restrict_demand_to_urban_core', True) else None,
            mapping_diagnostics=local_mapping)
        for item in geometry:
            members = [p for p in group if local_mapping[p['id']] == item['id']]
            combined = dict(members[0], location=item['location'])
            for field in ('population','commuters','attraction','residents','jobs','pea_15ymas'):
                combined[field] = sum(p[field] for p in members)
            signature = repr((sorted(p['id'] for p in members), key)).encode()
            combined['id'] = 'dv2_'+hashlib.sha256(signature).hexdigest()[:16]
            for p in members:
                mapping[p['id']] = combined['id']
            final.append(combined)
    return sorted(final, key=lambda p:p['id']), mapping


def attach_connectivity(points, roads):
    """Road-vertex components constrain support without modifying map geometry."""
    if roads is None or roads.empty:
        return dict(status='unverified', reason='No road geometry supplied')
    from shapely.geometry import Point
    from shapely.strtree import STRtree
    parents, vertices, lines = [], {}, []
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    for geometry in roads.geometry:
        if geometry is None or geometry.is_empty:
            continue
        for line in list(geometry.geoms) if geometry.geom_type == 'MultiLineString' else [geometry]:
            if line.geom_type != 'LineString':
                continue
            i = len(lines)
            lines.append(line)
            parents.append(i)
            for coordinate in line.coords:
                # Shared stored road vertices only; crossing grade-separated lines are not joined.
                key = tuple(coordinate[:2])
                if key in vertices:
                    a, b = find(i), find(vertices[key])
                    parents[max(a,b)] = min(a,b)
                vertices[key] = i
    if not lines:
        return dict(status='unverified', reason='No line geometries')
    tree = STRtree(lines)
    for p in points:
        nearest = int(tree.nearest(Point(*p['location'])))
        p['road_component'] = find(nearest)
    return dict(status='road_vertices', components=len({find(i) for i in range(len(lines))}),
                limitation='Nearest road association; no turn restrictions or ferry inference')
