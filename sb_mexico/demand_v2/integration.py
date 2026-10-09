"""Host adapters. Cartography and POI behavior remain owned by existing modules."""
from pathlib import Path
import copy
import json


def load_roads(path):
    import geopandas as gpd
    path = Path(path)
    return gpd.read_file(path).to_crs('EPSG:4326') if path.is_file() else None


def demand_roads(config, root, default):
    """Optional complete demand network; rendered map assets stay unchanged."""
    selected = config.get('routing', {}).get('demand_roads_path')
    if selected:
        path = Path(selected)
        if not path.is_absolute():
            path = Path(root) / path
        if not path.is_file():
            raise ValueError('Missing explicit demand road network: '+str(path))
        return load_roads(path)
    return load_roads(default) if default is not None else None


def osrm_provider(routing):
    if not routing.get('osrm_url'):
        return None
    if not routing.get('network_identity'):
        raise ValueError('Explicit OSRM provider requires routing.network_identity (network and car profile)')
    def provider(pops, points, include):
        from sb_mexico.osrm import enrich_pops_with_osrm
        diagnostics={}
        enrich_pops_with_osrm(pops,points,osrm_url=routing['osrm_url'],
            include_driving_path=include,diagnostics=diagnostics)
        return {(r['origin_id'],r['destination_id']):(r['source'],r['reason']) for r in diagnostics.get('pairs',[])}
    return provider


def execute_candidate(config, root, project_dir, output_dir, include_driving_path=False):
    from .request import prepare_request
    from .engine import build_demand, write_result
    from sb_mexico.osrm import enrich_pops_with_osrm
    cfg = copy.deepcopy(config)
    cfg.setdefault('routing', {})['include_driving_path'] = bool(include_driving_path)
    routing = cfg['routing']
    provider = None
    started_daemon = False
    network_identity = routing.get('network_identity')
    if routing.get('osrm_url') and not network_identity:
        raise ValueError('Explicit OSRM provider requires routing.network_identity (network and car profile)')
    if not routing.get('osrm_url') and routing.get('use_osrm', True):
        from sb_mexico.pipeline import is_docker_available, prepare_osrm_network_wsl, start_osrm_daemon_wsl
        from sb_mexico.osrm import compute_osrm_fingerprint
        pbf = sorted(Path(project_dir).glob('*.osm.pbf')) if project_dir else []
        if not pbf:
            pbf = sorted((Path(root)/'data').glob('*.osm.pbf'))
        if pbf and is_docker_available()[0]:
            ready, _ = prepare_osrm_network_wsl(config['city']['code'], str(pbf[0]), config['city']['bbox'], force_rebuild=False)
            if ready and start_osrm_daemon_wsl(city_code=config['city']['code'], port=5000):
                routing['osrm_url'] = 'http://127.0.0.1:5000'
                network_identity = compute_osrm_fingerprint(config['city']['code'], config['city']['bbox'], str(pbf[0]))
                started_daemon = True
    if routing.get('osrm_url'):
        routing['network_identity']=network_identity
        provider=osrm_provider(routing)
    request = prepare_request(cfg, root, project_dir,
        roads=demand_roads(cfg, root, Path(output_dir)/'roads.geojson'), route_provider=provider,
        route_identity=(network_identity or 'canonical')+':car:v2',
        route_cache=Path(output_dir)/'.demand-v2-routes.json')
    try:
        result = build_demand(request)
    finally:
        if started_daemon:
            from sb_mexico.osrm import stop_osrm_daemon_wsl
            stop_osrm_daemon_wsl(config['city']['code'])
    demand_path = write_result(result, request, output_dir)
    # The package contract is shared with the existing pipeline.
    from sb_mexico.pipeline import package_demand_outputs
    return package_demand_outputs(output_dir, config['city']['code'], demand_path,
                                  result['game']['points'], result['game']['pops'], result['report']['export']['commuters'])


def preview_candidate(config, root, project_dir=None, roads_path=None, stage='points'):
    from .request import prepare_request
    from .engine import build_demand
    request = prepare_request(config, root, project_dir,
                              roads=demand_roads(config, root, roads_path),
                              route_provider=osrm_provider(config.get('routing',{})),
                              route_identity=(config.get('routing',{}).get('network_identity') or 'canonical')+':car:v2')
    result = build_demand(request, stage)
    return dict(identity=result['identity'], stage=stage, engine='v2',
                points=result.get('points', []), report=result['report'])
