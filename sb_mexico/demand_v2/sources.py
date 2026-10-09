"""Explicit source ingestion and weighted municipal commuting evidence."""
from pathlib import Path
import hashlib
import sqlite3
import tempfile
import pandas as pd


def read_mobility(paths, municipalities):
    columns = ['CVEGEO', 'ID_PERSONA', 'EDAD', 'CONACT', 'TIE_TRASLADO_TRAB',
               'FACTOR', 'MUN_TRAB', 'ENT_PAIS_TRAB']
    flows, histograms = {}, {}
    with tempfile.TemporaryDirectory(prefix='demand-v2-mobility-') as folder:
        db = sqlite3.connect(str(Path(folder)/'identities.db'))
        try:
            db.execute('CREATE TABLE persons (state TEXT, person TEXT, digest TEXT, PRIMARY KEY(state,person))')
            for path in sorted(set(paths)):
                with pd.read_csv(path, dtype=str, keep_default_na=False, encoding='utf-8-sig',
                                 usecols=columns, chunksize=100000) as chunks:
                    for chunk in chunks:
                        chunk = chunk[columns]
                        for origin, person, age, activity, travel, weight, mun, state in chunk.itertuples(index=False, name=None):
                            origin = origin.strip().zfill(5)
                            if origin not in municipalities:
                                continue
                            payload = (origin, person, age, activity, travel, weight, mun, state)
                            digest = hashlib.sha256(repr(payload).encode()).hexdigest()
                            key = (origin[:2], person)
                            previous = db.execute('SELECT digest FROM persons WHERE state=? AND person=?', key).fetchone()
                            if previous:
                                if previous[0] != digest:
                                    raise ValueError('Conflicting EIC mobility identity: '+str(key))
                                continue
                            db.execute('INSERT INTO persons VALUES (?,?,?)', (*key, digest))
                            if not activity or not travel or not (12 <= int(age) <= 130 and 10 <= int(activity) <= 20 and 1 <= int(travel) <= 6):
                                continue
                            w = int(weight)
                            try:
                                ent, municipality = int(state), int(mun)
                                destination = f'{ent:02d}{municipality:03d}' if 1 <= ent <= 32 and 1 <= municipality <= 998 else 'unknown'
                            except ValueError:
                                destination = 'unknown'
                            k = (origin, destination)
                            row = flows.setdefault(k, dict(weight=0., weight_squared=0., records=0,
                                                           training_weight=0., validation_weight=0.,
                                                           training_weight_squared=0., training_records=0))
                            row['weight'] += w
                            row['weight_squared'] += w*w
                            row['records'] += 1
                            split = hashlib.sha256(repr(key).encode()).hexdigest()
                            validation = int(split[:8], 16) % 5 == 0
                            row['validation_weight' if validation else 'training_weight'] += w
                            if not validation:
                                row['training_weight_squared'] += w*w
                                row['training_records'] += 1
                            h = histograms.setdefault(origin, {str(i): 0 for i in range(1, 7)})
                            h[travel.strip()] += w
                        db.commit()
        finally:
            db.close()
    records = [dict(origin=o, destination=d, **v,
                    effective_sample_size=v['training_weight']**2/v['training_weight_squared'] if v['training_weight_squared'] else 0.)
               for (o, d), v in sorted(flows.items())]
    return dict(flows=records, travel_time_categories=histograms,
                precision_basis='Kish effective sample size; not a survey-design confidence interval',
                time_semantics='Reported multimodal category; not OSRM driving time')


def ingest(request, files):
    from sb_mexico.residential_employment import load_employed_census
    from sb_mexico.residential import load_official_layers
    from sb_mexico.workplace_employment import load_workplaces
    from sb_mexico.demographic_reference import _bases, _controls, _people
    from .request import validate_config
    cfg, paths = request.config, request.sources
    demand = validate_config(cfg)
    for role in ('cpv', 'denue', 'marco'):
        if not paths.get(role):
            raise ValueError('Candidate requires explicit/resolved '+role+' sources')
    blocks_geo, areas_geo, layers = load_official_layers(paths['marco'])
    bbox = cfg['city']['bbox']
    municipalities = set()
    for geo in (blocks_geo, areas_geo):
        if geo is not None and not geo.empty:
            municipalities.update(geo.loc[geo.lon_geo.between(bbox[0], bbox[2]) &
                                          geo.lat_geo.between(bbox[1], bbox[3]), 'cve_mun_clean'])
    if not municipalities:
        raise ValueError('No official geography in map; cannot infer municipal coverage from jobs')
    # All workplaces are loaded before clipping. No TIL1 expansion.
    macro = dict(cfg.get('macroeconomics', {}))
    year = demand.get('target_year', 2025)
    macro['projection_year'] = year
    macro.pop('target_year', None)
    if year != 2025:
        macro.pop('demographic_reference', None)
    macro['workplace_employment'] = 'auto' if macro.get('workplace_employment', 'auto') == 'legacy' else macro.get('workplace_employment', 'auto')
    macro['til_1_state'] = 0.
    world = dict(min_lon=-180, min_lat=-90, max_lon=180, max_lat=90)
    workplace_loader = load_workplaces
    if macro.get('workplace_employment', 'auto') == 'auto':
        from sb_mexico.fine_workplace import load_fine_workplaces
        workplace_loader = load_fine_workplaces
        world = dict(min_lon=bbox[0],min_lat=bbox[1],max_lon=bbox[2],max_lat=bbox[3])
    options = dict(retain_full_scope=True) if workplace_loader is not load_workplaces else {}
    denue, _, workplace_report = workplace_loader(sorted(paths['denue']), world, macro, ce_paths=sorted(paths.get('ce', [])), source_root=request.root, **options)
    census, census_report = load_employed_census(sorted(paths['cpv']), municipalities)
    census = census.sort_values('_full_identity').reset_index(drop=True)
    if 'id' in denue:
        denue = denue.sort_values('id').reset_index(drop=True)
    bases = _bases(paths['cpv'], municipalities)
    crosswalk = demand.get('municipality_crosswalk', {})
    if not isinstance(crosswalk, dict) or any(not __import__('re').fullmatch(r'\d{5}', str(k)) or
            not __import__('re').fullmatch(r'\d{5}', str(v)) for k,v in crosswalk.items()):
        raise ValueError('municipality_crosswalk must map five-digit codes to five-digit codes')
    if len(set(crosswalk.values())) != len(crosswalk):
        raise ValueError('Merged/split municipalities require source-specific allocation evidence; no automatic remap')
    if crosswalk:
        mapped = {c:crosswalk.get(c,c) for c in municipalities}
        if len(set(mapped.values())) != len(mapped):
            raise ValueError('Municipal crosswalk collides with an existing control')
        for frame in (census, denue, blocks_geo, areas_geo):
            if frame is not None:
                frame['cve_mun_clean'] = frame.cve_mun_clean.map(lambda c:crosswalk.get(c,c))
        bases = {mapped[c]:v for c,v in bases.items()}
        municipalities = set(mapped.values())
    if year == 2025:
        if len(paths.get('eic_indicators', [])) != 1 or not paths.get('eic_persons'):
            raise ValueError('2025 candidate requires one EIC indicators file and complete state persons files')
        controls = _controls(paths['eic_indicators'][0], municipalities)
        people = _people(paths['eic_persons'], municipalities)
        mobility = read_mobility(paths['eic_persons'], municipalities)
    else:
        controls = {c: {'Valor': v} for c, v in bases.items()}
        # CPV occupied is not an observed commute pool: this assumption must be visible.
        people = {c: dict(population=p, occupied=e, commuters=e, no_travel=0., unspecified=0.) for c, (p, e) in bases.items()}
        mobility = dict(flows=[], travel_time_categories={}, fallback='2020 occupied assumed commuters')
    catalog = []
    roles = dict(cpv=(2020, 'census population', 'spatial_weights'), marco=(2020, 'official geography', 'placement'),
                 denue=(None, 'covered establishments', 'destination_locations'), ce=(2023, 'CE covered economic units', 'historical_intensity'),
                 eic_indicators=(2025, 'inhabited private dwellings', 'municipal_controls'),
                 eic_persons=(2025, 'inhabited private dwellings', 'resident_mobility'))
    for role, path, sha in files:
        year_ref, universe, function = roles[role]
        if role == 'ce':
            from sb_mexico.ce_controls import read_saic_controls
            years = sorted({r['reference_year'] for r in read_saic_controls([path]).values()})
            year_ref = years[0] if len(years) == 1 else years or None
        declared = request.source_metadata.get(role, {})
        if declared.get('reference_year') is not None and year_ref is not None and declared['reference_year'] != year_ref:
            raise ValueError('Declared source year conflicts with parsed source: '+path)
        catalog.append(dict(role=role, path=path, sha256=sha, reference_year=declared.get('reference_year', year_ref),
                            edition=declared.get('edition', 'unspecified'), universe=universe, function=function,
                            coverage=sorted(municipalities)))
    return dict(census=census, denue=denue, blocks_geo=blocks_geo, areas_geo=areas_geo, layers=layers,
                bases=bases, controls=controls, people=people, mobility=mobility,
                report=dict(sources=catalog, census=census_report, workplaces=workplace_report,
                            municipality_crosswalk=crosswalk,
                            municipalities=sorted(municipalities), target_year=year,
                            ignored_parameters={k:v for k,v in cfg.get('macroeconomics', {}).items()
                              if k in ('tasa_pea', 'til_1_state', 'growth_factors', 'default_growth_factor',
                                       'projection_year', 'gravity_beta', 'furness_iterations', 'furness_tol', 'od_allocation', 'modal_experiment')}))
