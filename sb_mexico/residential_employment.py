"""Opt-in CPV employed residents with controlled, explicit reconstruction.

Published block counts are immutable. Parent controls apply only to complete
population coverage, before any spatial filtering. Allocated block counts and
population-based projections remain estimates, not observed commuters.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from sb_mexico.residential import geo_code
from sb_mexico.source_identity import RecordIdentityLedger


EMPLOYMENT_MODES = ('legacy', 'census_employed')
KEYS = ['cve_mun_clean', 'loc_clean', 'ageb_clean', 'mza_clean']
MISSING = {'', '*', 'N/D', 'N/A', 'NA', 'NAN', 'NONE', 'NULL'}


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def attach_source_context(report, config_path, projection_report, projection_paths):
    """Bind both preview and build provenance to the same saved configuration."""
    if report is None:
        return
    root = Path(__file__).resolve().parents[1]
    candidates = (Path(config_path), root / config_path, root / 'cities' / Path(config_path).name)
    actual = next((path for path in candidates if path.is_file()), None)
    if actual is None:
        raise ValueError('Cannot identify the saved employment configuration')
    report['source_config_sha256'] = file_sha256(actual)
    report['population_projection_sources'] = [dict(
        path=str(Path(path).resolve()), sha256=file_sha256(path),
        requested_year=projection_report.get('requested_year'),
        effective_year=projection_report.get('effective_year'), year_basis=projection_report.get('year_basis'),
        role='consulted population projection; applied factors are reported separately')
        for path in projection_paths]


def validate_employment_mode(value):
    if value not in EMPLOYMENT_MODES:
        raise ValueError('macroeconomics.residential_employment must be legacy or census_employed')
    return value


def _counts(series, field):
    text = series.astype(str).str.strip().str.upper()
    values = pd.to_numeric(text.where(~text.isin(MISSING)), errors='coerce')
    invalid = (~text.isin(MISSING) & values.isna()) | (
        values.notna() & (~np.isfinite(values) | (values < 0) | (values != np.floor(values))))
    if invalid.any():
        raise ValueError(f'Invalid CPV {field}: {text.loc[invalid].iloc[0]}')
    return values.astype(float), text


def _normalize(chunk):
    chunk.columns = [str(c).strip().upper() for c in chunk.columns]
    aliases = dict(ent=('ENTIDAD', 'CVE_ENT'), mun=('MUN', 'CVE_MUN'),
                   loc=('LOC', 'CVE_LOC'), ageb=('AGEB', 'CVE_AGEB'),
                   mza=('MZA', 'CVE_MZA', 'MANZANA'),
                   pop=('POBTOT', 'POB_TOTAL'), p12=('P_12YMAS', 'P12YMAS'),
                   p15=('P_15YMAS', 'P15YMAS', 'POB15'), occupied=('POCUPADA',))
    cols = {name: next((c for c in choices if c in chunk), None) for name, choices in aliases.items()}
    for name in ('pop', 'occupied'):
        if cols[name] is None:
            raise ValueError(f'census_employed requires CPV column {aliases[name][0]}')
    result = pd.DataFrame(index=chunk.index)
    for name, width in [('ent', 2), ('mun', 3), ('loc', 4), ('ageb', 4), ('mza', 3)]:
        result[name] = (chunk[cols[name]].map(lambda v: geo_code(v, width)) if cols[name]
                        else pd.Series(None, index=chunk.index, dtype=object))
    # A full official identity takes precedence over abbreviated columns.
    if 'CVEGEO' in chunk:
        full = chunk.CVEGEO.astype(str).str.strip().str.upper()
        valid = full.str.fullmatch(r'\d{9}[0-9A-Z]{4}\d{3}')
        for name, start, end in [('ent', 0, 2), ('mun', 2, 5), ('loc', 5, 9),
                                 ('ageb', 9, 13), ('mza', 13, 16)]:
            result.loc[valid, name] = full.loc[valid].str.slice(start, end)
    if result[['ent', 'mun', 'loc', 'ageb', 'mza']].isna().any().any():
        raise ValueError('census_employed requires complete CPV locality/block identities')
    if not result[['ent', 'mun', 'loc', 'mza']].apply(lambda col: col.str.fullmatch(r'\d+')).all().all():
        raise ValueError('Invalid numeric CPV geography code')
    if not result.ageb.str.fullmatch(r'\d{3}[0-9A-Z]').all():
        raise ValueError('Invalid CPV AGEB identity')
    invalid_hierarchy = (((result['loc'] != '0000') & (result.mun == '000')) |
                         ((result.ageb != '0000') & (result['loc'] == '0000')) |
                         ((result.mza != '000') & (result.ageb == '0000')))
    if invalid_hierarchy.any():
        raise ValueError('Incomplete CPV locality/block identity hierarchy')
    result['cve_mun_clean'] = result.ent + result.mun
    result['loc_clean'], result['ageb_clean'], result['mza_clean'] = result['loc'], result.ageb, result.mza
    for name, output in [('pop', 'pobtot_num'), ('p12', 'pob12_num'), ('p15', 'pob15_num'),
                          ('occupied', 'published_employed_2020')]:
        values, tokens = _counts(chunk[cols[name]] if cols[name] else pd.Series('', index=chunk.index),
                                  aliases[name][0])
        result[output] = values
        result[output + '_status'] = tokens.where(values.isna(), 'numeric')
    for field in ('pob12_num', 'pob15_num'):
        if (result[field] > result.pobtot_num).any():
            raise ValueError(f'CPV {field} exceeds POBTOT')
    result['employment_capacity'] = result.pob12_num.fillna(result.pobtot_num)
    if (result.published_employed_2020 > result.employment_capacity).any():
        raise ValueError('CPV POCUPADA exceeds age-eligible population bound')
    return result


def employment_summary(frame):
    return dict(blocks=len(frame), population_2020=float(frame.pobtot_num.sum()),
                published_employed_2020=float(frame.published_employed_2020.sum()),
                modeled_employed_2020=float(frame.employed_2020.sum()),
                projected_employed=float(frame.pea_real.sum()) if 'pea_real' in frame else None,
                capacity_basis={str(basis): len(group)
                                for basis, group in frame.groupby('employment_capacity_basis')},
                by_source={str(source): dict(blocks=len(group),
                           employed_2020=float(group.employed_2020.sum()),
                           projected_employed=float(group.pea_real.sum()) if 'pea_real' in group else None)
                           for source, group in frame.groupby('employment_source')})


def _apply_controls(blocks, parents, keys, level, outcomes):
    controls = parents.set_index(keys)
    for key, children in blocks.groupby(keys, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        lookup = key if len(keys) > 1 else key[0]
        if lookup not in controls.index:
            continue
        parent = controls.loc[lookup]
        outcome = dict(level=level, identity=list(key), blocks=len(children))
        target = parent.published_employed_2020
        if pd.isna(target):
            outcome['status'] = 'unavailable_employment_control'
        elif pd.isna(parent.pobtot_num) or parent.pobtot_num != children.pobtot_num.sum():
            outcome['status'] = 'incomplete_population_coverage'
            outcome['parent_population'] = None if pd.isna(parent.pobtot_num) else float(parent.pobtot_num)
            outcome['child_population'] = float(children.pobtot_num.sum())
        else:
            unknown = children.employed_2020.isna()
            residual = float(target - children.employed_2020.sum())
            capacity = float(children.loc[unknown, 'employment_capacity'].sum())
            outcome.update(target=float(target), residual=residual, capacity=capacity)
            if residual < -1e-8 or residual > capacity + 1e-8:
                raise ValueError(f'Incompatible CPV {level} employment control {key}: '
                                 f'residual {residual}, capacity {capacity}')
            if unknown.any():
                ids = children.index[unknown]
                allocation = (children.loc[ids, 'employment_capacity'] * (min(capacity, max(0., residual)) / capacity)
                              if capacity > 0 else pd.Series(0., index=ids))
                # Remove only floating-point summation error; no measured count changes.
                if capacity > 0:
                    last = children.loc[ids, 'employment_capacity'].loc[lambda col: col > 0].index[-1]
                    allocation.loc[last] += min(capacity, max(0., residual)) - float(allocation.sum())
                blocks.loc[ids, 'employed_2020'] = allocation
                blocks.loc[ids, 'employment_source'] = level + '_residual'
                outcome.update(status='allocated', allocated_blocks=len(ids))
            else:
                outcome['status'] = 'matched'
        outcomes.append(outcome)


def load_employed_census(paths, target_muns=None):
    """Load/deduplicate blocks and controls before clipping or projection."""
    from sb_mexico.inegi import _detect_cpv_format
    frames, sources = [], []
    ledger = RecordIdentityLedger('CPV employment')
    fields = ['pobtot_num', 'pob12_num', 'pob15_num', 'published_employed_2020']
    for path in paths:
        path = Path(path)
        source_hash = file_sha256(path)
        if path.suffix.lower() in ('.xlsx', '.xls'):
            chunks = [pd.read_excel(path, dtype=str, keep_default_na=False)]
        else:
            encoding, sep = _detect_cpv_format(str(path))
            chunks = pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding,
                                 sep=sep, chunksize=100_000, low_memory=False)
        try:
            for chunk in chunks:
                normalized = _normalize(chunk)
                if target_muns:
                    normalized = normalized[normalized.cve_mun_clean.isin(target_muns)]
                keep = []
                for row in normalized.to_dict('records'):
                    payload = tuple((None if pd.isna(row[field]) else row[field], row[field + '_status'])
                                    for field in fields)
                    keep.append(ledger.keep([tuple(row[key] for key in KEYS)], payload))
                frames.append(normalized.loc[keep])
        finally:
            if hasattr(chunks, 'close'):
                chunks.close()
        if file_sha256(path) != source_hash:
            raise ValueError(f'CPV source changed during employment ingestion: {path}')
        sources.append(dict(path=str(path.resolve()), sha256=source_hash, reference_year=2020,
                            program='INEGI CPV 2020, urban AGEB/block results'))
    if not frames:
        raise ValueError('No CPV employment records found')
    frame = pd.concat(frames, ignore_index=True)
    block_rows = frame.mza_clean != '000'
    if frame.loc[block_rows, 'pobtot_num'].isna().any():
        raise ValueError('census_employed requires numeric block POBTOT for coverage accounting')
    blocks = frame.loc[block_rows & (frame.pobtot_num > 0)].copy()
    if blocks.empty:
        raise ValueError('No inhabited CPV employment blocks found')
    blocks['employed_2020'] = blocks.published_employed_2020
    blocks['employment_source'] = np.where(blocks.employed_2020.notna(), 'published_block', 'unresolved')
    blocks['employment_capacity_basis'] = np.where(blocks.pob12_num.notna(), 'P_12YMAS', 'POBTOT_upper_bound')
    outcomes = []
    parents = frame.loc[~block_rows & (frame.loc_clean != '0000')]
    _apply_controls(blocks, parents.loc[parents.ageb_clean != '0000'], KEYS[:3], 'ageb', outcomes)
    _apply_controls(blocks, parents.loc[parents.ageb_clean == '0000'], KEYS[:2], 'locality', outcomes)
    # An uncontrolled block uses a rate from published employed residents in
    # its own urban geography, never the ENOE active-population rate (15+).
    published = blocks.published_employed_2020.notna()
    rates = []
    for keys, level in [(KEYS[:3], 'ageb'), (KEYS[:2], 'locality'), (KEYS[:1], 'municipality')]:
        for key, children in blocks.groupby(keys, sort=True):
            ids = children.index[children.employed_2020.isna()]
            observed = children.loc[published.loc[children.index]]
            denominator = float(observed.employment_capacity.sum())
            if len(ids) and denominator > 0:
                rate = float(observed.published_employed_2020.sum()) / denominator
                blocks.loc[ids, 'employed_2020'] = blocks.loc[ids, 'employment_capacity'] * rate
                blocks.loc[ids, 'employment_source'] = level + '_observed_rate'
                rates.append(dict(level=level, identity=list(key) if isinstance(key, tuple) else [key],
                                  rate=rate, published_employment=float(observed.published_employed_2020.sum()),
                                  capacity_denominator=denominator, estimated_blocks=len(ids),
                                  population_bound_blocks=int(observed.pob12_num.isna().sum())))
    zero_capacity = blocks.employed_2020.isna() & (blocks.employment_capacity == 0)
    blocks.loc[zero_capacity, 'employed_2020'] = 0.
    blocks.loc[zero_capacity, 'employment_source'] = 'zero_capacity'
    if blocks.employed_2020.isna().any():
        raise ValueError('No published employment control/rate for unresolved CPV blocks')
    blocks['mza_num'] = blocks.mza_clean.astype(int)
    # Preserve the legacy display column; it does not determine census employment.
    blocks['pob15_num'] = blocks.pob15_num.fillna(0.)
    blocks['_full_identity'] = blocks.cve_mun_clean + blocks.loc_clean + blocks.ageb_clean + blocks.mza_clean
    blocks['_employment_row_id'] = np.arange(len(blocks))
    report = dict(schema_version=1, mode='census_employed', source_reference_year=2020,
                  age_universe='12+', semantics='employed residents; commuting is modeled',
                  sources=sources, source_identity=ledger.report,
                  published_blocks=int(published.sum()),
                  published_zero_blocks=int((blocks.published_employed_2020 == 0).sum()),
                  suppressed_blocks=int((blocks.published_employed_2020_status == '*').sum()),
                  unavailable_blocks=int((~published & (blocks.published_employed_2020_status != '*')).sum()),
                  capacity_upper_bound_blocks=int(blocks.pob12_num.isna().sum()),
                  controls=outcomes, fallback_rates=rates,
                  scope=dict(urban_only=True, target_municipalities=sorted(target_muns) if target_muns else None,
                             reconstruction_before_bbox=True),
                  assumptions=['Residuals allocated in proportion to age-eligible capacity.',
                               'Missing P_12YMAS uses POBTOT as an upper bound, not a measured age count.',
                               'Uncontrolled blocks use a rate from published blocks in their urban geography.'])
    blocks.attrs['source_identity'] = ledger.report
    return blocks, report


def record_projection(report, census, projection_year):
    growth = census.growth.to_numpy(float)
    if not np.isfinite(growth).all() or (growth < 0).any():
        raise ValueError('Invalid employed-resident population projection factor')
    report['projection'] = dict(model_year=projection_year,
        policy='existing municipal population growth factors',
        assumption='Employment grows proportionally to population; not observed target-year employment.',
        factors={str(k): float(v) for k, v in census.groupby('cve_mun_clean').growth.first().items()})
    report['input'] = employment_summary(census)
    report['implementation_sha256'] = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in ('inegi.py', 'residential.py', 'residential_employment.py', 'source_identity.py')}


def record_placement(report, frame, retained, unlocated, outside):
    if frame._employment_row_id.duplicated().any():
        raise ValueError('Census placement multiplied employment records; use official_blocks placement')
    report['placement'] = dict(unlocated=employment_summary(frame.loc[unlocated]),
                              outside_bbox=employment_summary(frame.loc[outside]),
                              retained=employment_summary(retained))
    retained.attrs['residential_employment'] = report
