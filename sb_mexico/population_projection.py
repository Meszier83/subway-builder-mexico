"""Shared automatic growth policy for builds and previews.

Use full municipal CPV totals; never substitute urban block sums for a full
municipality. Fall back explicitly to homogeneous CONAPO-2020 ratios.
"""
from pathlib import Path
import pandas as pd

from sb_mexico.inegi import _detect_cpv_format, parse_conapo_projections, resolve_projection_year


def municipal_census_totals(paths):
    totals = {}
    for path in paths:
        encoding, separator = _detect_cpv_format(str(path))
        frame = pd.read_csv(path, encoding=encoding, sep=separator, dtype=str, low_memory=False)
        frame.columns = [str(c).strip().upper().lstrip('\ufeff') for c in frame.columns]
        required = {'ENTIDAD', 'MUN', 'LOC', 'AGEB', 'MZA', 'POBTOT'}
        if not required <= set(frame):
            continue
        numeric = {key: pd.to_numeric(frame[key], errors='coerce')
                   for key in ('ENTIDAD', 'MUN', 'LOC', 'MZA', 'POBTOT')}
        mask = ((numeric['LOC'] == 0) & (numeric['MZA'] == 0) &
                (frame['AGEB'].str.strip().str.zfill(4) == '0000') &
                (numeric['MUN'] > 0) & (numeric['ENTIDAD'] > 0) & (numeric['POBTOT'] > 0))
        for index in frame.index[mask]:
            key = f"{int(numeric['ENTIDAD'][index]):02d}{int(numeric['MUN'][index]):03d}"
            value = float(numeric['POBTOT'][index])
            if key in totals and totals[key] != value:
                raise ValueError(f'Conflicting municipal census totals for {key}')
            totals[key] = value
    return totals


def resolve_population_factors(conapo_path, cpv_paths, macro, diagnostics=None):
    report = diagnostics if diagnostics is not None else {}
    target = resolve_projection_year(macro)
    confirmed = (resolve_projection_year({'projection_year': macro['conapo_source_year']})
                 if macro.get('conapo_source_year') is not None else None)
    populations = parse_conapo_projections(str(conapo_path), target_year=target,
                                          diagnostics=report, source_year=confirmed)
    census = municipal_census_totals(cpv_paths)
    base_report = {}
    homogeneous = parse_conapo_projections(str(conapo_path), target_year=target,
                                          as_growth_factors=True, diagnostics=base_report,
                                          source_year=confirmed)
    if base_report.get('value_kind') != 'growth_factor':
        homogeneous = {}
    manual = macro.get('growth_factors', {})
    factors, details = {}, {}
    for key, population in populations.items():
        base = census.get(key)
        if key in manual:
            factor, basis = float(manual[key]), 'manual'
        elif base:
            factor, basis = round(max(.90, min(1.60, population / base)), 4), 'CPV municipal total 2020'
        elif key in homogeneous:
            factor, basis = round(max(.90, min(1.60, homogeneous[key])), 4), 'CONAPO population 2020 fallback'
        else:
            factor, basis = float(macro.get('default_growth_factor', 1.0)), 'configured default: municipal base unavailable'
        factors[key] = factor
        details[key] = dict(factor=factor, denominator=basis, census_population_2020=base,
                            projected_population=population)
    report.update(source_path=str(Path(conapo_path)), manual_factors=dict(manual),
                  value_kind='growth_factor', input_value_kind='population',
                  factor_policy='municipal_census_else_conapo_2020_else_default',
                  municipalities=details)
    return factors
