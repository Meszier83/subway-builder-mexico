"""Opt-in historical CE intensities on current DENUE locations, before clipping."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from sb_mexico.ce_controls import read_saic_controls
from sb_mexico.historical_benchmark import classify_scope, validate_historical_contract

FORMULA_VERSION = 'municipal_sector_size_mean_v1'
SUPPORTED = {'31-33': ['31', '32', '33'], '43': ['43'], '46': ['46'],
             '53': ['53'], '72': ['72']}
STRATA = ('0 A 10', '11 A 50', '51 A 250', '251 Y MAS')


def validate_transfer_contract(contract):
    if not isinstance(contract, dict) or contract.get('role') != 'historical_transfer' or contract.get('enabled') is not True:
        raise ValueError('Historical transfer requires a separate explicitly enabled contract')
    validate_historical_contract({**contract, 'role': 'historical_benchmark', 'enabled': False})
    if contract.get('formula_version') != FORMULA_VERSION:
        raise ValueError('Unsupported historical transfer formula')
    strength = contract.get('strength')
    if type(strength) not in (int, float) or not math.isfinite(strength) or not 0 <= strength <= 1:
        raise ValueError('Historical transfer strength must be between 0 and 1')
    if not contract.get('source_sha256') or not contract.get('groups'):
        raise ValueError('Inspect sources and select historical transfer groups first')
    for group in contract['groups']:
        if sorted(group['scian_prefixes']) not in [sorted(p) for p in SUPPORTED.values()]:
            raise ValueError('Unsupported historical transfer sector')
        if group['reporting_unit'] != 'establishment' or not isinstance(group.get('reporting_unit_evidence'), str) or not group['reporting_unit_evidence'].strip():
            raise ValueError('Historical transfer needs establishment reporting-unit evidence')
    return contract


def selected_ce_sources(macro, source_root=None):
    contract = validate_transfer_contract(macro.get('historical_workplace_transfer'))
    root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    paths = []
    for value in contract['ce_sources']:
        path = (root / value).resolve()
        if not path.is_relative_to(root) or path.suffix.lower() != '.csv' or not path.is_file():
            raise ValueError('Historical CE source must be a CSV inside the project')
        paths.append(str(path))
    return paths


def coarse_stratum(bounds):
    if bounds is None:
        return None
    lower = bounds[0]
    return STRATA[0 if lower <= 10 else 1 if lower <= 50 else 2 if lower <= 250 else 3]


def checked_sources(paths, macro, source_root=None):
    from sb_mexico.workplace_employment import source_hashes
    ce_paths = selected_ce_sources(macro, source_root)
    hashes = dict(denue=source_hashes(paths), ce=source_hashes(ce_paths))
    if not hashes['denue'] or hashes != macro['historical_workplace_transfer']['source_sha256']:
        raise ValueError('Historical transfer sources changed; inspect and explicitly rebind them')
    return ce_paths, hashes


def load_historical_workplaces(paths, bbox, macro, source_root=None):
    from sb_mexico.inegi import load_denue, resolve_projection_year
    from sb_mexico.residential_employment import file_sha256
    from sb_mexico.workplace_employment import band, bounded_fit, source_hashes
    contract = validate_transfer_contract(macro.get('historical_workplace_transfer'))
    ce_paths, hashes = checked_sources(paths, macro, source_root)
    frame = load_denue(paths, bbox, full_scope=True).reset_index(drop=True)
    for column in ('workplace_industry', 'workplace_band'):
        if column not in frame:
            frame[column] = ''
    frame = classify_scope(frame, paths)
    estimates = [band(v) for v in frame.workplace_band]
    strata = np.array([coarse_stratum(v) for v in estimates], dtype=object)
    frame['calibrated_jobs'] = [v[2] if v else 2.24 for v in estimates]
    priors = frame.calibrated_jobs.copy()
    controls = read_saic_controls(ce_paths, contract['reference_year'])
    located = np.isfinite(frame.lat.astype(float)) & np.isfinite(frame.lon.astype(float))
    inside = located & frame.lon.between(bbox['min_lon'], bbox['max_lon']) & frame.lat.between(bbox['min_lat'], bbox['max_lat'])
    outcomes, transferred = [], set()
    for group in contract['groups']:
        mun, prefixes = group['municipality'], group['scian_prefixes']
        code = next(c for c, p in SUPPORTED.items() if sorted(p) == sorted(prefixes))
        sector = frame.cve_mun_clean.eq(mun) & frame.workplace_industry.str.fullmatch(r'\d{6}').fillna(False) & frame.workplace_industry.str.startswith(tuple(prefixes))
        eligible = sector & frame['_scope'].eq('candidate')
        for stratum in STRATA:
            mask = eligible & (strata == stratum)
            indices = np.flatnonzero(mask.to_numpy())
            control = controls.get((contract['reference_year'], 'municipality', mun, code, stratum))
            baseline = float(priors.loc[mask].sum())
            outcome = dict(municipality=mun, activity_code=code, scian_prefixes=prefixes,
                           stratum=stratum, establishments=len(indices), prior_attraction=baseline,
                           excluded_records=int((sector & frame['_scope'].eq('excluded') & (strata == stratum)).sum()),
                           review_records=int((sector & frame['_scope'].eq('review') & (strata == stratum)).sum()),
                           reporting_unit_evidence=group['reporting_unit_evidence'], status='MISSING_CE_CONTROL')
            if not len(indices):
                outcome['status'] = 'NO_ELIGIBLE_ESTABLISHMENTS'
            elif control is not None:
                jobs, units = control['employment'], control['establishments']
                outcome.update(ce_personnel=jobs, ce_units=units)
                if jobs is None or units is None:
                    outcome['status'] = 'SUPPRESSED_CE_CONTROL'
                elif units <= 0:
                    outcome['status'] = 'NO_CE_ESTABLISHMENTS'
                else:
                    average = jobs / units
                    historical_target = len(indices) * average
                    low, high = {'0 A 10': (0, 10), '11 A 50': (11, 50),
                                 '51 A 250': (51, 250), '251 Y MAS': (251, math.inf)}[stratum]
                    outcome.update(historical_mean=average, historical_target=historical_target,
                                   open_upper_bound=stratum == '251 Y MAS')
                    target = baseline * (1 - contract['strength']) + historical_target * contract['strength']
                    outcome['target'] = target
                    bounds = [estimates[i] for i in indices]
                    if not low <= average <= high:
                        outcome['status'] = 'INVALID_CE_SIZE_MEAN'
                    elif not sum(v[0] for v in bounds) <= historical_target <= sum(v[1] for v in bounds):
                        # Even a weakened transfer must not conceal an infeasible full target.
                        outcome['status'] = 'INFEASIBLE_HISTORICAL_TARGET'
                    elif contract['strength'] == 0:
                        outcome['status'] = 'ZERO_STRENGTH_PRIOR'
                    else:
                        fitted, multiplier = bounded_fit([v[2] for v in bounds], [v[0] for v in bounds],
                                                        [v[1] for v in bounds], target)
                        frame.loc[mask, 'calibrated_jobs'] = fitted
                        transferred.update(indices.tolist())
                        outcome.update(status='TRANSFERRED_HISTORICAL_MEAN', multiplier=multiplier,
                                       residual=float(fitted.sum() - target),
                                       at_lower_bound=int(np.isclose(fitted, [v[0] for v in bounds]).sum()),
                                       at_upper_bound=int(np.isclose(fitted, [v[1] for v in bounds]).sum()))
            outcome.update(full_scope_attraction=float(frame.loc[mask, 'calibrated_jobs'].sum()),
                           bbox_attraction=float(frame.loc[mask & inside, 'calibrated_jobs'].sum()),
                           outside_bbox_attraction=float(frame.loc[mask & located & ~inside, 'calibrated_jobs'].sum()),
                           unlocated_attraction=float(frame.loc[mask & ~located, 'calibrated_jobs'].sum()))
            outcomes.append(outcome)
    if hashes != dict(denue=source_hashes(paths), ce=source_hashes(ce_paths)):
        raise ValueError('Sources changed during historical transfer; inspect again')
    report = dict(schema_version=1, mode='historical_transfer', formula_version=FORMULA_VERSION,
                  strength=contract['strength'], reference_year=contract['reference_year'],
                  model_year=resolve_projection_year(macro), denue_edition=contract['denue_edition'],
                  source_sha256=hashes, comparability='CONDITIONAL_HISTORICAL_MODEL',
                  effective_macro_sha256=hashlib.sha256(json.dumps(macro, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                  implementation_sha256={n: file_sha256(Path(__file__).with_name(n)) for n in
                                         ('historical_transfer.py', 'historical_benchmark.py', 'workplace_employment.py', 'ce_controls.py', 'inegi.py', 'source_identity.py')},
                  scope_rule_version=contract['scope_rule_version'], coverage_evidence=contract['coverage_evidence'],
                  transfer_assumption=contract['transfer_assumption'], controls=outcomes,
                  full_scope_establishments=len(frame), prior_full_scope_attraction=float(priors.sum()),
                  full_scope_attraction=float(frame.calibrated_jobs.sum()),
                  bbox_attraction=float(frame.loc[inside, 'calibrated_jobs'].sum()),
                  outside_bbox_attraction=float(frame.loc[located & ~inside, 'calibrated_jobs'].sum()),
                  unlocated_attraction=float(frame.loc[~located, 'calibrated_jobs'].sum()),
                  transferred_establishments=len(transferred),
                  transferred_attraction=float(frame.loc[sorted(transferred), 'calibrated_jobs'].sum()),
                  fallback_establishments=len(frame)-len(transferred),
                  unknown_band_establishments=sum(v is None for v in estimates),
                  scope_components={k: dict(establishments=int(frame['_scope'].eq(k).sum()),
                                           attraction=float(frame.loc[frame['_scope'].eq(k), 'calibrated_jobs'].sum()))
                                    for k in ('candidate', 'review', 'excluded')},
                  assumptions=['Historical sector/size means transfer to current locations; vintage and ownership remain conditional.',
                               'Full selected source municipalities are processed before BBOX; completeness is not certified.',
                               'No CE-total fit, TIL1 expansion, workplace growth, state or confidentiality fallback.',
                               'Open 251+ band remains open. Excluded/review/unknown records retain priors.',
                               'Attraction weights are not observed employment or realized destination commuter marginals.'])
    clipped = frame.loc[inside].drop(columns=['_scope']).copy()
    audit = {mun: dict(nombre=mun, jobs_formal=float(sub.jobs_formal.sum()), h001a=None,
                      share_bbox=len(sub)/max(1, int(frame.cve_mun_clean.eq(mun).sum())),
                      factor=float(sub.calibrated_jobs.sum()/max(1e-12, sub.jobs_formal.sum())),
                      status='HISTORICAL_TRANSFER', notes='Conditional historical intensity; see workplace_employment_report.json')
             for mun, sub in clipped.groupby('cve_mun_clean')}
    clipped.attrs['workplace_employment'] = report
    return clipped, audit, report
