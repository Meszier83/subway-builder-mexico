"""Shared staged entrypoint used by previews, candidate builds and comparisons."""
import copy
import hashlib
import json
import time
from pathlib import Path
from collections import Counter
from .request import STAGES, identity, validate_config
from .sources import ingest
from .population import allocate_population
from .points import build_points, consolidate_points
from .allocation import allocate


class DisconnectedCommutes(ValueError):
    def __init__(self,pairs):
        self.pairs=set(pairs)
        super().__init__('OSRM proves allocated pairs are disconnected: '+repr(sorted(self.pairs)))


def build_demand(request, stage='export'):
    if stage not in STAGES:
        raise ValueError('Unknown demand stage: '+str(stage))
    started = time.perf_counter()
    fingerprint, files = identity(request)
    evidence = ingest(request, files)
    result = dict(stage=stage, identity=fingerprint, report=evidence['report'])
    if stage == 'sources':
        result['evidence'] = evidence
        return result
    population, controls = allocate_population(evidence['census'], evidence['bases'], evidence['controls'], evidence['people'])
    result['report']['population_controls'] = controls
    result['population'] = population
    if stage == 'population':
        return result
    points, retained, point_report = build_points(request, evidence, population)
    points, consolidation = consolidate_points(request, points)
    from .cohorts import settings
    cohort_options = settings(request.config)
    result['statistical_points'] = copy.deepcopy(points)
    if cohort_options['fixed_size']:
        from .fixed_groups import resolve
        points,point_report['fixed_cohort_resolution'] = resolve(points,cohort_options['fixed_size'])
    point_report['final_consolidation'] = consolidation
    point_report['point_mapping'] = {old:consolidation[new] for old,new in point_report['point_mapping'].items()}
    result['points'] = points
    result['report']['territory'] = point_report
    result['report']['mobility'] = evidence['mobility']
    result['retained'] = retained
    if stage == 'points':
        return result
    cfg = request.config
    parameters = dict(validate_config(cfg))
    macro = cfg.get('macroeconomics', {})
    if 'max_distance_km' not in parameters and macro.get('max_distance_km') is not None:
        parameters['max_distance_km'] = macro['max_distance_km']
    validate_config({'demand':parameters})
    if cohort_options['fixed_size']:
        # Pooling cannot hide an unsupported original residence.
        from .allocation import support
        support(result['statistical_points'],parameters)
    parameters['cohort_settings'] = cohort_options
    cells, allocation = allocate(points, parameters, evidence['mobility'])
    result['cells'] = cells
    result['report']['allocation'] = allocation
    if stage == 'allocation':
        return result
    ceiling = cohort_options['max_pop_size']
    if type(ceiling) is not int or ceiling <= 0:
        raise ValueError('max_pop_size must be a positive integer')
    pops = []
    for cell in cells:
        for ordinal,count in enumerate(cell['cohort_sizes']):
            raw = repr((cell['origin'], cell['destination'], ordinal)).encode()
            pops.append(dict(id='pop_v2_'+hashlib.sha256(raw).hexdigest()[:20],
                             residenceId=cell['origin'], jobId=cell['destination'], size=count))
    forbidden=set()
    for attempt in range(8):
        game_points = copy.deepcopy(points)
        try:
            routing = route_commutes(request,pops,game_points)
            break
        except DisconnectedCommutes as error:
            if attempt==7 or error.pairs<=forbidden:
                raise ValueError('OSRM support refinement did not close; no export produced') from error
            forbidden.update(error.pairs)
            parameters['_forbidden_pairs']=forbidden
            cells,allocation=allocate(points,parameters,evidence['mobility'])
            result['cells']=cells; result['report']['allocation']=allocation
            pops=[]
            for cell in cells:
                for ordinal,count in enumerate(cell['cohort_sizes']):
                    raw=repr((cell['origin'],cell['destination'],ordinal)).encode()
                    pops.append(dict(id='pop_v2_'+hashlib.sha256(raw).hexdigest()[:20],
                                     residenceId=cell['origin'],jobId=cell['destination'],size=count))
    routing.update(excluded_no_route_pairs=[list(p) for p in sorted(forbidden)],support_refinements=attempt)
    from sb_mexico.gravity import sync_demand_points_and_pops, sanitize_demand_points
    game_points, pops = sync_demand_points_and_pops(game_points, pops, remove_orphans=True,
        include_driving_path=bool(cfg.get('routing', {}).get('include_driving_path', False)))
    game_points = sanitize_demand_points(game_points)
    validate_export(points, cells, game_points, pops, ceiling)
    from .export_sites import collapse
    game_points, pops, mapping, sites = collapse(game_points, pops, points)
    validate_export(points, cells, game_points, pops, ceiling, mapping)
    result['export_point_mapping'] = mapping
    if len(pops) != allocation['cohorts']['actual_count']:
        raise ValueError('Export changed the planned cohort count')
    if cohort_options['fixed_size']:
        minimum=point_report['fixed_cohort_resolution']['expected_cohorts']
        if len(pops)<minimum:
            raise ValueError('Export reduced the spatially required cohort resolution')
        allocation['od_additional_cohorts']=len(pops)-minimum
    # Detect mid-run source/config drift before exposing any export.
    if identity(request)[0] != fingerprint:
        raise ValueError('Demand inputs changed during build')
    result['game'] = dict(points=game_points, pops=pops)
    result['report']['routing'] = routing
    result['report']['elapsed_seconds'] = time.perf_counter()-started
    result['report']['export'] = dict(points=len(game_points), cohorts=len(pops), commuters=sum(p['size'] for p in pops),
        sites=sites,
        arrivals=dict(Counter({p['id']:p['jobs'] for p in game_points})),
        bytes=len(json.dumps(result['game'], separators=(',', ':')).encode()))
    result['report'].update(engine='v2', points=len(game_points), cohorts=len(pops),
                            commuters=result['report']['export']['commuters'])
    return result


def route_commutes(request, pops, points):
    from sb_mexico.osrm import calculate_canonical_driving_fallback
    import math
    locations = {p['id']:p['location'] for p in points}
    include = bool(request.config.get('routing', {}).get('include_driving_path', False))
    records = {}
    cache = {}
    if request.route_cache and Path(request.route_cache).exists():
        cache = json.loads(Path(request.route_cache).read_text(encoding='utf-8'))
    missing = []
    disconnected = set()
    keys = {}
    for pop in pops:
        a, b = locations[pop['residenceId']], locations[pop['jobId']]
        key = hashlib.sha256(json.dumps([a, b, request.route_identity, include]).encode()).hexdigest()
        keys[pop['id']] = key
        if key in cache:
            pop.update(cache[key])
            records[key] = 'cached_osrm'
        else:
            dx = (b[0]-a[0])*111320*math.cos(math.radians((a[1]+b[1])/2))
            dy = (b[1]-a[1])*110574
            distance, seconds = calculate_canonical_driving_fallback(math.hypot(dx, dy))
            pop.update(drivingDistance=distance, drivingSeconds=seconds)
            records[key] = 'canonical:provider_not_configured'
            missing.append(pop)
    if missing and request.route_provider:
        provenance = request.route_provider(missing, points, include) or {}
        for pop in missing:
            pair = (pop['residenceId'], pop['jobId'])
            kind, reason = provenance.get(pair, ('canonical', 'provider_unclassified'))
            key = keys[pop['id']]
            records[key] = kind+':'+str(reason or '')
            if str(reason or '').lower().replace('_','') == 'noroute':
                disconnected.add(pair)
            if kind == 'osrm':
                cache[key] = {k:pop[k] for k in ('drivingDistance', 'drivingSeconds', 'drivingPath') if k in pop}
    if request.route_cache:
        path = Path(request.route_cache)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(cache), encoding='utf-8')
        temporary.replace(path)
    if disconnected:
        raise DisconnectedCommutes(disconnected)
    return dict(identity=request.route_identity, unique_pairs=len(records), provenance=dict(Counter(records.values())),
                fallback_cached=False)


def validate_export(model_points, cells, points, pops, ceiling, point_mapping=None):
    mapping = point_mapping or {p['id']:p['id'] for p in model_points}
    authority = {p['id']:p for p in model_points}
    if set(mapping) != set(authority):
        raise ValueError('Incomplete export point mapping')
    expected, expected_pairs = Counter(), Counter()
    for p in model_points:
        representative = authority.get(mapping[p['id']])
        if representative is None or any(p.get(k)!=representative.get(k) for k in
                                          ('location','municipality','zone','road_component')):
            raise ValueError('Export mapping moved a statistical point or changed territory')
        if (p.get('is_special') or representative.get('is_special')) and p['id']!=mapping[p['id']]:
            raise ValueError('Export mapping changed a special identifier')
        if p['commuters']:
            expected[mapping[p['id']]] += int(p['commuters'])
    for cell in cells:
        expected_pairs[mapping[cell['origin']],mapping[cell['destination']]] += cell['size']
    origins, pairs = Counter(), Counter()
    ids = {p['id'] for p in points}
    locations = {p['id']:p['location'] for p in points}
    territories = {p['id']:p for p in model_points}
    if len(ids) != len(points) or len({p['id'] for p in pops}) != len(pops):
        raise ValueError('Duplicate export identifiers')
    for p in pops:
        if type(p['size']) is not int or not 0 < p['size'] <= ceiling or p['residenceId'] not in ids or p['jobId'] not in ids:
            raise ValueError('Invalid exported cohort')
        a,b=p['residenceId'],p['jobId']
        if a==b or locations[a]==locations[b]:
            raise ValueError('Export contains coincident commute endpoints')
        origin,destination=territories[a],territories[b]
        if origin['zone']!=destination['zone'] or (origin.get('road_component') is not None and
                                                  origin['road_component']!=destination.get('road_component')):
            raise ValueError('Export crosses a hard territory')
        if not all(__import__('math').isfinite(p[k]) and p[k] >= 0 for k in ('drivingDistance', 'drivingSeconds')):
            raise ValueError('Invalid driving statistics')
        origins[p['residenceId']] += p['size']
        pairs[p['residenceId'], p['jobId']] += p['size']
    if origins != expected or pairs != expected_pairs:
        raise ValueError('Export changed exact origins or OD cells')


def write_result(result, request, output_dir):
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    if 'game' not in result:
        raise ValueError('Only export stage can be written as game demand')
    files = {'demand_data.json':result['game'], 'demand_pipeline_report.json':result['report'],
             'demand_model.json':dict(points=result['points'], cells=result['cells'],
                 statistical_points=result['statistical_points'],
                 export_point_mapping=result['export_point_mapping'],
                 residents=result['retained'][['_full_identity','cve_mun_clean','pobtot_adj','occupied_residents',
                                               'pea_real','population_integer','occupied_integer','commuters_integer',
                                               'employment_source','placement_source']].to_dict('records'))}
    city = request.config['city']
    center = city.get('initial_center', [(city['bbox'][0]+city['bbox'][2])/2, (city['bbox'][1]+city['bbox'][3])/2])
    files['config.json'] = dict(name=city['name'], code=city['code'], description=city.get('description', ''),
        population=sum(p['size'] for p in result['game']['pops']), creator=city.get('creator', 'Subway Builder México'),
        version='7.1.0', initialViewState=dict(longitude=center[0], latitude=center[1], zoom=city.get('initial_zoom', 12), bearing=0, pitch=0))
    for name, value in files.items():
        (path/name).write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    from sb_mexico.special_demand import generate_special_demand_points_doc, save_special_demand_points
    if request.config.get('pois'):
        save_special_demand_points(generate_special_demand_points_doc(city['code'], request.config['pois'], result['points']),
                                   str(path/'special_demand_points.json'))
    manifest = dict(schema_version=1, engine='v2', status='candidate', identity=result['identity'],
                    target_year=request.config.get('demand', {}).get('target_year', 2025),
                    files={name:hashlib.sha256((path/name).read_bytes()).hexdigest() for name in files},
                    game_validation='pending')
    (path/'demand_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return str(path/'demand_data.json')
