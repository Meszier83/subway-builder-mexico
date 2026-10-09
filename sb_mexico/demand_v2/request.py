"""Boundary adapter: resolve inputs once, outside numerical stages."""
from dataclasses import dataclass, field
from pathlib import Path
import copy
import hashlib
import json

STAGES = ('sources', 'population', 'points', 'allocation', 'export')


@dataclass
class DemandRequest:
    config: dict
    sources: dict
    root: Path
    roads: object = None
    route_provider: object = None
    route_identity: str = 'canonical:car:v1'
    route_cache: Path = None
    source_metadata: dict = field(default_factory=dict)


def validate_config(config):
    demand = config.get('demand', {})
    if not isinstance(demand, dict):
        raise ValueError('demand must be a mapping')
    if demand.get('engine', 'legacy') not in ('legacy', 'v2'):
        raise ValueError('demand.engine must be legacy or v2')
    if type(demand.get('target_year', 2025)) is not int or demand.get('target_year', 2025) not in (2020, 2025):
        raise ValueError('Candidate supports observed references 2020 and 2025 only')
    if demand.get('boundary_policy', 'closed') != 'closed':
        raise ValueError('Candidate boundary_policy must be closed')
    count = demand.get('cohort_count')
    if count is not None and (type(count) is not int or count <= 0):
        raise ValueError('demand.cohort_count must be a positive integer or null')
    fixed = demand.get('fixed_cohort_size')
    if fixed is not None and (type(fixed) is not int or fixed <= 0):
        raise ValueError('demand.fixed_cohort_size must be a positive integer')
    for key in ('beta', 'max_distance_km'):
        value = demand.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                  not __import__('math').isfinite(value) or value <= 0):
            raise ValueError('demand.' + key + ' must be finite and positive')
    return demand


def prepare_request(config, root, project_dir=None, roads=None, **kwargs):
    """Discovery is restricted to the project and national shared sources."""
    from sb_mexico.demand_sources import select_sources
    from sb_mexico.residential import discover_marco_layers
    root = Path(root).resolve()
    cfg = copy.deepcopy(config)
    demand = validate_config(cfg)
    explicit = demand.get('sources', {})
    if not isinstance(explicit, dict):
        raise ValueError('demand.sources must be a mapping of roles to paths')
    project = Path(project_dir or cfg.get('data_dir', root / 'data'))
    if not project.is_absolute():
        project = root / project
    sources = {}
    for role in ('cpv', 'denue', 'ce', 'marco'):
        values = explicit.get(role)
        if values is None:
            values = select_sources(project, root/'data', role, cfg.get('data_exclusions', []))
            if role == 'marco':
                values = sorted(set(values + discover_marco_layers(project)))
        if not isinstance(values, list):
            raise ValueError('demand.sources.' + role + ' must be a list')
        sources[role] = [str((root / p).resolve()) for p in values]
    ref = cfg.get('macroeconomics', {}).get('demographic_reference') or {}
    sources['eic_indicators'] = [str((root/p).resolve()) for p in explicit.get('eic_indicators',
                                    [ref['indicators']] if ref.get('indicators') else [])]
    sources['eic_persons'] = [str((root/p).resolve()) for p in explicit.get('eic_persons', ref.get('persons', []))]
    for role in ('eic_indicators', 'eic_persons'):
        if role in explicit and not isinstance(explicit[role], list):
            raise ValueError('demand.sources.'+role+' must be a list')
    return DemandRequest(cfg, sources, root, roads=roads,
                         source_metadata=demand.get('source_metadata', {}), **kwargs)


def identity(request):
    from sb_mexico.residential_employment import file_sha256
    files = []
    for role, paths in sorted(request.sources.items()):
        for path in sorted(set(paths)):
            p = Path(path)
            if not p.is_file():
                raise ValueError(f'Missing {role} source: {p}')
            files.append((role, str(p.resolve()), file_sha256(p)))
            if p.suffix.lower() == '.shp':
                for suffix in ('.dbf', '.shx', '.prj', '.cpg'):
                    companion = p.with_suffix(suffix)
                    if companion.exists():
                        files.append((role, str(companion), file_sha256(companion)))
    code = [(p.name, file_sha256(p)) for p in sorted(Path(__file__).parent.glob('*.py'))]
    # Shared adapters are part of the candidate's identity too.
    for name in ('gravity.py', 'residential.py', 'residential_employment.py', 'workplace_employment.py',
                 'historical_transfer.py', 'automatic_workplace.py', 'fine_workplace.py', 'ce_controls.py', 'demographic_reference.py', 'osrm.py'):
        p = Path(__file__).parents[1]/name
        code.append((name, file_sha256(p)))
    roads = None
    if request.roads is not None:
        roads = hashlib.sha256(b''.join(bytes(g.wkb) for g in request.roads.geometry
                                         if g is not None)).hexdigest()
    raw = json.dumps(dict(config=request.config, sources=files, code=code, roads=roads,
                         metadata=request.source_metadata, routing=request.route_identity),
                     sort_keys=True, ensure_ascii=False, default=str).encode()
    return hashlib.sha256(raw).hexdigest(), files
