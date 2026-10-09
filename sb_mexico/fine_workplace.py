"""Candidate-only disjoint historical SCIAN-size intensities, before clipping.

The deepest source node owns each record, even when suppressed or missing its
size cell. Never subtract children to recover confidential values, or fill a
suppressed class with its parent. Open bands have no invented upper limit.
"""
from collections import Counter
import math
from pathlib import Path
import numpy as np

from sb_mexico.ce_controls import read_saic_controls
from sb_mexico.historical_transfer import SUPPORTED, coarse_stratum

FORMULA_VERSION = 'municipal_scian_size_partition_v1'

def supported(row):
    return any(p.startswith(tuple(prefixes)) for p in row['scian_prefixes'] for prefixes in SUPPORTED.values())

def level(code):
    return {2:'sector',3:'subsector',4:'rama',5:'subrama',6:'class'}[len(code.split('-')[0])]

def load_fine_workplaces(paths, bbox, macro, ce_paths=(), source_root=None, retain_full_scope=False):
    from sb_mexico.inegi import load_denue, resolve_projection_year
    from sb_mexico.historical_benchmark import classify_scope
    from sb_mexico.workplace_employment import band, bounded_fit, source_hashes
    from sb_mexico.residential_employment import file_sha256
    hashes = dict(denue=source_hashes(paths), ce=source_hashes(ce_paths))
    controls = read_saic_controls(ce_paths)
    frame = classify_scope(load_denue(paths, bbox, full_scope=True).reset_index(drop=True), paths)
    for column in ('workplace_industry', 'workplace_band'):
        if column not in frame:
            frame[column] = ''
    bounds = [band(v) for v in frame.workplace_band]
    strata = [coarse_stratum(v) for v in bounds]
    frame['calibrated_jobs'] = [v[2] if v else 2.24 for v in bounds]
    priors = frame.calibrated_jobs.copy()
    municipalities = set(frame.cve_mun_clean)
    relevant = [r for r in controls.values() if r['geography_level']=='municipality'
                and r['geography'] in municipalities and supported(r)]
    years = {r['reference_year'] for r in relevant}
    if len(years)>1:
        raise ValueError('Fine CE transfer found multiple activity years; select one year of CE sources')
    year = next(iter(years), None)
    catalog = {}
    for r in relevant:
        catalog.setdefault(r['geography'], {})[r['activity_code']] = tuple(r['scian_prefixes'])
    # Source controls form a partition too: never use an ancestor's total next
    # to a descendant. Missing/unpublished leaves retain priors, not parent means.
    ordered = {}
    for mun,nodes in catalog.items():
        leaves = [(code,prefixes) for code,prefixes in nodes.items() if not any(
            len(child)>len(parent) and child.startswith(parent)
            for other,children in nodes.items() if other!=code for child in children for parent in prefixes)]
        ordered[mun]=sorted(leaves,key=lambda item:(-max(map(len,item[1])),item[0]))
    groups, reasons = {}, Counter()
    for i, r in enumerate(frame.itertuples()):
        # itertuples renames private columns; use the actual scope column here.
        if frame.at[i,'_scope']!='candidate':
            reasons['SCOPE_'+frame.at[i,'_scope'].upper()] += 1
            continue
        if bounds[i] is None:
            reasons['UNKNOWN_BAND'] += 1
            continue
        industry = str(r.workplace_industry)
        if not (len(industry)==6 and industry.isdigit()):
            reasons['UNKNOWN_INDUSTRY'] += 1
            continue
        match = next((code for code,prefixes in ordered.get(r.cve_mun_clean, []) if industry.startswith(prefixes)), None)
        if match is None:
            reasons['NO_SUPPORTED_CE_ACTIVITY'] += 1
            continue
        groups.setdefault((r.cve_mun_clean,match,strata[i]), []).append(i)
    outcomes, transferred = [], set()
    for (mun,code,stratum), indices in sorted(groups.items()):
        control = controls.get((year,'municipality',mun,code,stratum))
        base = float(priors.iloc[indices].sum())
        outcome = dict(municipality=mun,activity_code=code,activity_level=level(code),stratum=stratum,
            establishments=len(indices),prior_attraction=base,status='MISSING_SIZE_CONTROL',
            open_upper_bound=stratum=='251 Y MAS')
        if control is not None:
            jobs, units = control['employment'], control['establishments']
            outcome.update(ce_personnel=jobs,ce_units=units)
            if jobs is None or units is None:
                outcome['status']='SUPPRESSED_CE_CONTROL'
            elif units<=0:
                outcome['status']='NO_CE_ESTABLISHMENTS'
            else:
                mean = jobs/units
                target = len(indices)*mean
                lower,upper = {'0 A 10':(0,10),'11 A 50':(11,50),'51 A 250':(51,250),'251 Y MAS':(251,math.inf)}[stratum]
                selected = [bounds[i] for i in indices]
                outcome.update(historical_mean=mean,historical_target=target,
                    lower_capacity=sum(b[0] for b in selected), upper_capacity=(None if math.isinf(sum(b[1] for b in selected)) else sum(b[1] for b in selected)))
                if not lower<=mean<=upper:
                    outcome['status']='INVALID_CE_SIZE_MEAN'
                elif not sum(b[0] for b in selected)<=target<=sum(b[1] for b in selected):
                    outcome['status']='INFEASIBLE_HISTORICAL_TARGET'
                else:
                    fitted,multiplier = bounded_fit([b[2] for b in selected],[b[0] for b in selected],[b[1] for b in selected],target)
                    frame.loc[indices,'calibrated_jobs']=fitted
                    transferred.update(indices)
                    outcome.update(status='TRANSFERRED_HISTORICAL_INTENSITY',multiplier=multiplier,residual=float(fitted.sum()-target))
        outcome['full_scope_attraction']=float(frame.loc[indices,'calibrated_jobs'].sum())
        outcomes.append(outcome)
        if outcome['status']!='TRANSFERRED_HISTORICAL_INTENSITY':
            reasons[outcome['status']]+=len(indices)
    located = np.isfinite(frame.lon.astype(float)) & np.isfinite(frame.lat.astype(float))
    inside = located & frame.lon.between(bbox['min_lon'],bbox['max_lon']) & frame.lat.between(bbox['min_lat'],bbox['max_lat'])
    changed = np.asarray([i in transferred for i in range(len(frame))])
    coverage = {}
    for name,mask in [('full_scope',np.ones(len(frame),dtype=bool)),('bbox',inside.to_numpy())]:
        total = float(frame.loc[mask,'calibrated_jobs'].sum())
        mass = float(frame.loc[mask & changed,'calibrated_jobs'].sum())
        by_level = {}
        for node_level in ('sector','subsector','rama','subrama','class'):
            idx=[i for key,entries in groups.items() if level(key[1])==node_level for i in entries if i in transferred and mask[i]]
            by_level[node_level]=dict(establishments=len(idx),attraction=float(frame.loc[idx,'calibrated_jobs'].sum()))
        coverage[name]=dict(establishments=int(mask.sum()),transferred_establishments=int((mask & changed).sum()),
            attraction=total,transferred_attraction=mass,attraction_share=mass/total if total else 0.,by_level=by_level)
    if hashes!=dict(denue=source_hashes(paths),ce=source_hashes(ce_paths)):
        raise ValueError('Sources changed during fine CE transfer')
    effective_mode = 'historical_fine_transfer' if transferred else 'ce_bounded'
    report = dict(schema_version=1,mode=effective_mode,formula_version=FORMULA_VERSION,
        reference_year=year,model_year=resolve_projection_year(macro),source_sha256=hashes,
        implementation_sha256={n:file_sha256(Path(__file__).with_name(n)) for n in
            ('fine_workplace.py','ce_controls.py','historical_benchmark.py','workplace_employment.py','inegi.py')},
        comparability='CONDITIONAL_HISTORICAL_MODEL',controls=outcomes,coverage=coverage,
        full_scope_establishments=len(frame),transferred_establishments=len(transferred),fallback_establishments=len(frame)-len(transferred),
        fallback_reasons=dict(reasons),prior_full_scope_attraction=float(priors.sum()),full_scope_attraction=float(frame.calibrated_jobs.sum()),
        bbox_attraction=float(frame.loc[inside,'calibrated_jobs'].sum()),
        automatic_selection=dict(requested_mode='auto',effective_mode=effective_mode,reason='Deepest available municipal SCIAN node owns each record; reserved/missing/infeasible cells retain DENUE priors'),
        assumptions=['Historical group totals scaled by current eligible unit count; not a fit to contemporary CE totals.',
                     'Headcounts within each group remain estimates, including the open 251+ band.',
                     'No parent fallback over suppressed children; no confidential values reconstructed.',
                     'Established sector/unit policy retained; ownership, vintage and rural completeness conditional.',
                     'No TIL1 expansion or synthetic job locations.'])
    clipped = frame.loc[np.ones(len(frame),dtype=bool) if retain_full_scope else inside].drop(columns='_scope').copy()
    clipped.attrs['workplace_employment']=report
    return clipped, {}, report
