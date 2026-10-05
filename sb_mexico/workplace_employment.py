"""Opt-in bounded workplace estimates, with explicit CE/DENUE comparability.

DENUE bands describe occupied personnel, not formal employment. A source-bound
coverage declaration is an assumption supplied by the analyst, not independently
verified source truth. No TIL1 expansion is applied here.
"""
import csv
import hashlib
import io
import json
import math
import re
import unicodedata
from pathlib import Path

import numpy as np

from sb_mexico.residential_employment import file_sha256

MODES = ('auto', 'legacy', 'ce_bounded', 'historical_transfer')
BANDS = ((0, 5, 2.24), (6, 10, 7.75), (11, 30, 18.17),
         (31, 50, 39.37), (51, 100, 71.41), (101, 250, 158.90),
         (251, math.inf, 450.0))

def validate_workplace_mode(value):
    if value not in MODES:
        raise ValueError('macroeconomics.workplace_employment must be ' + ', '.join(MODES))
    return value

def normalize(value):
    return ' '.join(unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().upper().split())

def band(value):
    text = normalize(value)
    for lower, upper, prior in BANDS:
        expected = f'{lower} A {int(upper)} PERSONAS' if math.isfinite(upper) else '251 Y MAS PERSONAS'
        if text == expected:
            return lower, upper, prior
    return None

def bounded_fit(priors, lower, upper, target):
    """Proportional fit with per-establishment bounds, including open 251+.

    Returns estimates and multiplier; infeasible controls fail instead of
    silently claiming closure. No destination marginal is implied downstream.
    """
    prior, low, high = (np.asarray(v, dtype=float) for v in (priors, lower, upper))
    if not (prior.shape == low.shape == high.shape) or prior.ndim != 1:
        raise ValueError('Inconsistent workplace bounds')
    if not math.isfinite(target) or target < 0 or not np.isfinite(prior).all() or not np.isfinite(low).all():
        raise ValueError('Nonfinite or negative workplace control')
    if (prior <= 0).any() or (low < 0).any() or np.isnan(high).any() or (low > high).any():
        raise ValueError('Invalid workplace bounds/priors')
    tolerance = max(1e-8, target * 1e-10)
    if target < low.sum() or target > high.sum():
        raise ValueError('Infeasible workplace control for observed size bands')
    if target == low.sum():
        return low.copy(), 0.0
    if target == high.sum():
        return high.copy(), None
    left, right = 0.0, 1.0
    while np.clip(prior * right, low, high).sum() < target:
        right *= 2
        if not math.isfinite(right):
            raise ValueError('Unbounded workplace multiplier')
    for _ in range(200):
        factor = (left + right) / 2
        fitted = np.clip(prior * factor, low, high)
        residual = float(fitted.sum() - target)
        if abs(residual) <= tolerance:
            return fitted, factor
        if residual < 0:
            left = factor
        else:
            right = factor
    raise ValueError('Workplace fitting did not converge')

def read_ce_controls(paths, reference_year):
    """Municipal all-size controls; split strata and state controls stay separate."""
    from sb_mexico.ce_controls import read_saic_controls
    return {(r['geography'], r['activity_code']):
            dict(employment=r['employment'], establishments=r['establishments'],
                 name=r['name'], scian_prefixes=r['scian_prefixes'])
            for r in read_saic_controls(paths, reference_year).values()
            if r['geography_level'] == 'municipality' and r['stratum'] == 'TOTAL'}


def source_hashes(paths):
    return sorted(file_sha256(p) for p in set(map(str, paths)))

def available_ce_controls(paths):
    from sb_mexico.ce_controls import read_saic_controls
    return [dict(scian_prefix=r['activity_code'], municipality=r['geography'], **r)
            for r in read_saic_controls(paths).values()]


def load_workplaces(paths, bbox, macro, ce_benchmarks=None, ce_paths=(), source_root=None):
    """Shared production/preview entrypoint. Fit full source scope before BBOX.

    Contract: reference_year == denue_reference_year, exact source hashes,
    coverage_evidence, and disjoint groups {municipality, scian_prefix}. Counts
    must also match CE UE. Matching counts are necessary, not proof of coverage.
    """
    from sb_mexico.inegi import load_denue, calibrate_denue_employment, resolve_projection_year
    paths = [str(paths)] if isinstance(paths, (str, Path)) else list(paths)
    ce_paths = [str(ce_paths)] if isinstance(ce_paths, (str, Path)) else list(ce_paths)
    if macro.get('workplace_employment') == 'auto':
        from sb_mexico.automatic_workplace import resolve_automatic_workplace
        effective, notice = resolve_automatic_workplace(paths, ce_paths, macro, source_root)
        frame, audit, report = load_workplaces(paths, bbox, effective, ce_benchmarks, ce_paths, source_root)
        notice['implementation_sha256'] = file_sha256(Path(__file__).with_name('automatic_workplace.py'))
        report['automatic_selection'] = notice
        return frame, audit, report
    from sb_mexico.historical_benchmark import validate_historical_contract
    control_historical = validate_historical_contract(macro.get('workplace_control_contract'))
    historical = validate_historical_contract(macro.get('historical_workplace_benchmark')) or control_historical
    if 'historical_workplace_benchmark' in macro and validate_historical_contract(macro['historical_workplace_benchmark']) is None:
        raise ValueError('Expected historical_benchmark role')
    mode = validate_workplace_mode(macro.get('workplace_employment', 'legacy'))
    if mode == 'historical_transfer':
        from sb_mexico.historical_transfer import load_historical_workplaces
        return load_historical_workplaces(paths, bbox, macro, source_root)
    if mode == 'legacy':
        frame = load_denue(paths, bbox)
        frame, audit = calibrate_denue_employment(frame, ce_benchmarks or {},
            float(macro.get('til_1_state', .45)), int(macro.get('sample_threshold', 500)))
        return frame, audit, dict(schema_version=1, mode=mode, **({'historical_benchmark': historical} if historical else {}))
    frame = load_denue(paths, bbox, full_scope=True)
    if 'workplace_industry' not in frame:
        frame['workplace_industry'] = ''
    estimates = [band(v) for v in frame.get('workplace_band', [])]
    frame['calibrated_jobs'] = [v[2] if v else 2.24 for v in estimates]
    prior_total = float(frame.calibrated_jobs.sum())
    contract = macro.get('workplace_control_contract') or {}
    if not isinstance(contract, dict):
        raise ValueError('workplace_control_contract must be a mapping')
    hashes = dict(denue=source_hashes(paths), ce=source_hashes(ce_paths))
    year = contract.get('reference_year')
    eligible = (not control_historical and type(year) is int and year == contract.get('denue_reference_year')
                and bool(str(contract.get('coverage_evidence', '')).strip())
                and bool(hashes['ce']) and contract.get('source_sha256') == hashes)
    gate_reasons = []
    if type(year) is not int or year != contract.get('denue_reference_year'):
        gate_reasons.append('Missing or mismatched declared CE/DENUE reference years')
    if not str(contract.get('coverage_evidence', '')).strip():
        gate_reasons.append('Missing analyst coverage evidence')
    if not hashes['ce'] or contract.get('source_sha256') != hashes:
        gate_reasons.append('Missing CE sources or source hashes do not match the declaration')
    controls = read_ce_controls(ce_paths, year) if eligible else {}
    groups = contract.get('groups', [])
    if not isinstance(groups, list):
        raise ValueError('Workplace control groups must be a list')
    assigned = set()
    outcomes = []
    for group in groups if eligible else []:
        if not isinstance(group, dict):
            raise ValueError('Workplace control group must be a mapping')
        mun, prefix = str(group.get('municipality', '')), str(group.get('scian_prefix', ''))
        if not re.fullmatch(r'\d{5}', mun) or (prefix and not re.fullmatch(r'\d{2,6}', prefix)):
            raise ValueError('Invalid workplace control geography/SCIAN')
        # Reject overlapping declarations even if no source row happens to overlap.
        if any(mun == m and (prefix.startswith(p) or p.startswith(prefix)) for m,p in assigned):
            raise ValueError('Overlapping workplace control groups')
        assigned.add((mun, prefix))
        mask = frame.cve_mun_clean.eq(mun)
        if prefix:
            mask &= frame.workplace_industry.str.fullmatch(r'\d{6}').fillna(False) & frame.workplace_industry.str.startswith(prefix)
        indices = np.flatnonzero(mask.to_numpy())
        control = controls.get((mun, prefix))
        outcome = dict(municipality=mun, scian_prefix=prefix, establishments=len(indices),
                       status='MISSING_CE_CONTROL', control=control)
        if control:
            if control['employment'] is None or control['establishments'] is None:
                outcome['status'] = 'SUPPRESSED_CE_CONTROL'
            elif len(indices) != control['establishments']:
                outcome['status'] = 'COVERAGE_COUNT_MISMATCH'
            elif any(estimates[i] is None for i in indices):
                outcome['status'] = 'UNKNOWN_SIZE_BAND'
            else:
                bounds = [estimates[i] for i in indices]
                try:
                    fitted, multiplier = bounded_fit([v[2] for v in bounds], [v[0] for v in bounds],
                                                    [v[1] for v in bounds], control['employment'])
                    frame.loc[mask, 'calibrated_jobs'] = fitted
                    outcome.update(status='FITTED_DECLARED_COMPARABLE', multiplier=multiplier,
                                   fitted_full_scope=float(fitted.sum()), residual=float(fitted.sum()-control['employment']))
                except ValueError as error:
                    outcome.update(status='INFEASIBLE_CONTROL', reason=str(error))
        outcomes.append(outcome)
    located = np.isfinite(frame.lat.astype(float)) & np.isfinite(frame.lon.astype(float))
    inside = located & frame.lon.between(bbox['min_lon'], bbox['max_lon']) & frame.lat.between(bbox['min_lat'], bbox['max_lat'])
    report = dict(schema_version=1, mode=mode, source_sha256=hashes,
                  effective_macro_sha256=hashlib.sha256(json.dumps(macro,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                  implementation_sha256={name:file_sha256(Path(__file__).with_name(name)) for name in ('workplace_employment.py','inegi.py','source_identity.py','ce_controls.py','historical_benchmark.py')},
                  reference_year=year if eligible else None, model_year=resolve_projection_year(macro),
                  comparability='analyst_declared_source_bound' if eligible else 'UNVERIFIED_SOURCE_COMPARABILITY',
                  coverage_evidence=contract.get('coverage_evidence') if eligible else None,
                  historical_benchmark=historical,
                  control_gate_reasons=gate_reasons + (['Historical benchmark is inspection-only; activation is disabled'] if historical else []),
                  available_ce_controls=available_ce_controls(ce_paths),
                  assumptions=['Coverage declaration is not independent verification; equal establishment counts alone do not prove comparable universes.',
                               'Size bands are occupied personnel estimates, not formal jobs. Unknown bands retain an explicitly uncalibrated 2.24 prior.',
                               'No TIL1 expansion. No workplace growth projection. Reference counts are attraction weights in the model year.',
                               'The 251+ upper bound is open; its allocation remains uncertain.',
                               'Full source scope is fitted before clipping. Exported jobs are realized model commuters, not CE destination marginals.'],
                  unknown_band_establishments=sum(v is None for v in estimates), controls=outcomes,
                  prior_full_scope_attraction=prior_total,
                  full_scope_establishments=len(frame), full_scope_attraction=float(frame.calibrated_jobs.sum()),
                  bbox_attraction=float(frame.loc[inside,'calibrated_jobs'].sum()),
                  outside_bbox_attraction=float(frame.loc[located & ~inside,'calibrated_jobs'].sum()),
                  unlocated_attraction=float(frame.loc[~located,'calibrated_jobs'].sum()))
    for outcome in outcomes:
        mask = frame.cve_mun_clean.eq(outcome['municipality'])
        if outcome['scian_prefix']:
            mask &= frame.workplace_industry.str.fullmatch(r'\d{6}').fillna(False) & frame.workplace_industry.str.startswith(outcome['scian_prefix'])
        outcome.update(bbox_attraction=float(frame.loc[mask & inside,'calibrated_jobs'].sum()),
                       outside_bbox_attraction=float(frame.loc[mask & located & ~inside,'calibrated_jobs'].sum()),
                       unlocated_attraction=float(frame.loc[mask & ~located,'calibrated_jobs'].sum()))
    clipped = frame.loc[inside].copy()
    audit = {}
    for mun, subset in clipped.groupby('cve_mun_clean'):
        audit[mun] = dict(nombre=mun, jobs_formal=float(subset.jobs_formal.sum()),
                         share_bbox=len(subset)/max(1, int(frame.cve_mun_clean.eq(mun).sum())),
                         h001a=None, factor=float(subset.calibrated_jobs.sum()/max(1e-12,subset.jobs_formal.sum())),
                         status='BOUNDED_ESTIMATES', notes='See workplace_employment_report.json for full-scope CE outcomes')
    clipped.attrs['workplace_employment'] = report
    return clipped, audit, report
