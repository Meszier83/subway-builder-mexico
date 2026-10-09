"""Explicit municipal EIC controls, shared by compilation and density preview.

CPV full municipal counts are allocation denominators, not urban/BBOX sums.
CPV block counts remain immutable spatial weights. EIC estimates apply only to
retained urban weights; they do not locate growth or observe jobs at destinations.
"""
from pathlib import Path
import copy
import hashlib
import sqlite3
import tempfile
import numpy as np
import pandas as pd
from sb_mexico.residential_employment import file_sha256, employment_summary


def validate_reference(macro, allow_pending=False):
    ref = macro.get('demographic_reference')
    if ref is None:
        return None
    if not isinstance(ref, dict) or ref.get('mode') != 'eic2025':
        raise ValueError('demographic_reference.mode must be eic2025')
    if macro.get('residential_employment', 'census_employed') != 'census_employed':
        raise ValueError('EIC 2025 requires census_employed spatial weights')
    for field in ('projection_year', 'target_year'):
        if field in macro and (isinstance(macro[field], bool) or str(macro[field]) != '2025'):
            raise ValueError('EIC reference requires model year 2025; remove other projection years')
    if ref.get('pending_download'):
        if allow_pending:
            return ref
        raise ValueError('EIC download/configuration pending; download the sources and save their paths before compiling')
    if not isinstance(ref.get('indicators'), str) or not ref['indicators'].strip():
        raise ValueError('EIC reference requires the official indicators CSV path')
    persons = ref.get('persons')
    if not isinstance(persons, list) or not persons or any(not isinstance(p, str) or not p.strip() for p in persons):
        raise ValueError('EIC reference requires a list of official personas CSV paths')
    return ref


def reference_sources(macro, root):
    ref = validate_reference(macro)
    if ref is None:
        return []
    paths = []
    for name in [ref['indicators']] + ref['persons']:
        path = (Path(root) / name).resolve()
        if not path.is_file():
            raise ValueError(f'EIC source missing: {path}')
        if path not in paths:
            paths.append(path)
    return paths


def reference_identity(macro, root):
    return [dict(path=str(p), sha256=file_sha256(p)) for p in reference_sources(macro, root)]


def _csv(path, usecols=None):
    # Official indicator CSV uses Windows-1252; personas files use UTF-8.
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc, usecols=usecols, low_memory=False)
        except UnicodeDecodeError:
            pass
    raise ValueError(f'Unsupported EIC CSV encoding: {path}')


def _number(value, name, signed=False):
    try:
        result = float(value)
        if not np.isfinite(result) or (result < 0 and not signed):
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise ValueError(f'Invalid {name}: {value!r}')


def _bases(paths, municipalities):
    from sb_mexico.inegi import _detect_cpv_format
    bases = {}
    for path in paths:
        enc, sep = _detect_cpv_format(str(path))
        required = {'ENTIDAD', 'MUN', 'LOC', 'AGEB', 'MZA', 'POBTOT', 'POCUPADA'}
        df = pd.read_csv(path, dtype=str, encoding=enc, sep=sep, low_memory=False,
                         usecols=lambda c:str(c).strip().upper().lstrip('\ufeff') in required)
        df.columns = [str(c).strip().upper().lstrip('\ufeff') for c in df]
        if not required <= set(df):
            raise ValueError('EIC allocation requires full municipal CPV POBTOT/POCUPADA controls')
        for r in df.to_dict('records'):
            code = str(r['ENTIDAD']).zfill(2) + str(r['MUN']).zfill(3)
            if code not in municipalities or any(str(r[k]).strip().strip('0') for k in ('LOC','AGEB','MZA')):
                continue
            values = tuple(_number(r[k], 'CPV '+k) for k in ('POBTOT','POCUPADA'))
            if code in bases and bases[code] != values:
                raise ValueError(f'Conflicting full municipal CPV controls: {code}')
            bases[code] = values
    missing = municipalities - set(bases)
    if missing:
        raise ValueError('Full municipal CPV controls missing (no urban-sum fallback): '+', '.join(sorted(missing)))
    return bases


def _controls(path, municipalities):
    columns = ['CVE_ENT','CVE_MUN','CVE_LOC','ESTIMADOR','POBTOT','POCUPADA']
    df = _csv(path, columns)
    controls = {}
    for r in df.to_dict('records'):
        code = str(r['CVE_ENT']).zfill(2) + str(r['CVE_MUN']).zfill(3)
        if code not in municipalities or str(r['CVE_LOC']).zfill(4) != '0000':
            continue
        estimator = r['ESTIMADOR']
        values = tuple(None if estimator=='Coeficiente de variación' and str(r[k]).strip().upper() in ('*','N/D','N/A','NAN','')
                       else _number(r[k], 'EIC '+k+' '+code, signed=estimator=='Límite inferior de confianza') for k in ('POBTOT','POCUPADA'))
        prior = controls.setdefault(code, {})
        if estimator in prior and prior[estimator] != values:
            raise ValueError(f'Conflicting EIC municipal control: {code}/{estimator}')
        prior[estimator] = values
    required = {'Valor','Error estándar','Límite inferior de confianza','Límite superior de confianza','Coeficiente de variación'}
    for code in sorted(municipalities):
        if not required <= set(controls.get(code, {})):
            raise ValueError(f'EIC municipal controls/precision missing: {code}')
        row = controls[code]
        if row['Valor'][1] > row['Valor'][0]:
            raise ValueError(f'EIC occupied exceeds population: {code}')
        for i in (0,1):
            if row['Valor'][i] > 0 and row['Coeficiente de variación'][i] is None:
                raise ValueError(f'EIC precision missing for positive control: {code}')
            if not row['Límite inferior de confianza'][i] <= row['Valor'][i] <= row['Límite superior de confianza'][i]:
                raise ValueError(f'Invalid EIC confidence interval: {code}')
    return controls


def _people(paths, municipalities):
    cols = ['CVEGEO','ID_PERSONA','EDAD','CONACT','TIE_TRASLADO_TRAB','FACTOR']
    totals = {c:dict(population=0., occupied=0., commuters=0., no_travel=0., unspecified=0.) for c in municipalities}
    # Disk-backed identities keep multi-state/national microdata memory bounded.
    with tempfile.TemporaryDirectory(prefix='eic-identities-') as temporary:
        connection = sqlite3.connect(str(Path(temporary)/'people.sqlite'))
        try:
            connection.execute('CREATE TABLE people (state TEXT, id TEXT, digest BLOB, PRIMARY KEY(state,id))')
            for path in dict.fromkeys(paths):
                with pd.read_csv(path, dtype=str, encoding='utf-8-sig', usecols=cols, chunksize=100000) as reader:
                    for df in reader:
                        df = df[cols].fillna('')
                        df = df[df.CVEGEO.str.zfill(5).isin(municipalities)]
                        for r in df.itertuples(index=False, name=None):
                            code, person, age, activity, travel, weight = [v.strip() for v in r]
                            code = code.zfill(5)
                            if not person:
                                raise ValueError('Missing EIC person identity')
                            w = _number(weight,'EIC FACTOR')
                            a = _number(age,'EIC EDAD')
                            act = _number(activity,'EIC CONACT') if activity else None
                            time = _number(travel,'EIC travel code') if travel else None
                            if w <= 0 or w != int(w) or a != int(a):
                                raise ValueError('EIC FACTOR must be a positive integer and EDAD an integer')
                            key = (code[:2],person)
                            digest = hashlib.sha256(repr((code,person,a,act,time,w)).encode()).digest()
                            try:
                                connection.execute('INSERT INTO people VALUES (?,?,?)',(*key,digest))
                            except sqlite3.IntegrityError:
                                previous = connection.execute('SELECT digest FROM people WHERE state=? AND id=?',key).fetchone()[0]
                                if previous != digest:
                                    raise ValueError('Conflicting overlapping EIC person: '+str(key))
                                continue
                            t = totals[code]
                            t['population'] += w
                            if 12 <= a <= 130 and act is not None and 10 <= act <= 20:
                                if time not in (1,2,3,4,5,6,7,9):
                                    raise ValueError('Invalid EIC employed travel code')
                                t['occupied'] += w
                                t['commuters' if time <= 6 else ('no_travel' if time==7 else 'unspecified')] += w
                        connection.commit()
        finally:
            connection.close()
    return totals


def apply_reference(frame, cpv_paths, macro, root):
    """Calibrate retained CPV weights using full controls; never inflate a clipped map to a full municipality."""
    ref = validate_reference(macro)
    if ref is None:
        return frame
    reference_sources(macro,root)
    indicator = (Path(root)/ref['indicators']).resolve()
    persons = [(Path(root)/p).resolve() for p in ref['persons']]
    municipalities = set(frame.cve_mun_clean)
    bases = _bases(cpv_paths,municipalities)
    controls = _controls(indicator,municipalities)
    people = _people(persons,municipalities)
    details = {}
    for code in sorted(municipalities):
        pop, occupied = controls[code]['Valor']
        observed = people[code]
        if observed['population'] != pop or observed['occupied'] != occupied:
            raise ValueError(f'Incomplete/incompatible EIC personas for {code}: weighted totals do not match published controls')
        bp, be = bases[code]
        if (pop > 0 and bp <= 0) or (occupied > 0 and be <= 0):
            raise ValueError(f'Cannot allocate positive EIC control from zero CPV weights: {code}')
        details[code] = dict(population=pop,occupied=occupied,**{k:v for k,v in observed.items() if k not in ('population','occupied')},
            population_weight_factor=pop/bp if bp else 0.,occupied_weight_factor=occupied/be if be else 0.,
            commuting_fraction=observed['commuters']/occupied if occupied else 0.,
            cpv_full_population_weight=bp,cpv_full_occupied_weight=be,
            precision={k:list(v) for k,v in controls[code].items() if k!='Valor'})
    result = frame.copy()
    pop_f = result.cve_mun_clean.map({k:v['population_weight_factor'] for k,v in details.items()})
    occupied_f = result.cve_mun_clean.map({k:v['occupied_weight_factor'] for k,v in details.items()})
    fraction = result.cve_mun_clean.map({k:v['commuting_fraction'] for k,v in details.items()})
    result['growth'] = pop_f
    result['pobtot_adj'] = result.pobtot_num * pop_f
    result['pob15_adj'] = result.pob15_num * pop_f
    result['occupied_residents'] = result.employed_2020 * occupied_f
    result['pea_real'] = result.occupied_residents * fraction
    result['labor_commuters'] = result.pea_real
    if not np.isfinite(result[['pobtot_adj','occupied_residents','pea_real']].to_numpy()).all():
        raise ValueError('Invalid calibrated EIC weights')
    for code, group in result.groupby('cve_mun_clean'):
        bp, be = bases[code]
        if group.pobtot_num.sum() > bp+1e-6 or group.employed_2020.sum() > be+1e-6:
            raise ValueError(f'CPV spatial weights exceed full municipal control: {code}')
    report = copy.deepcopy(result.attrs['residential_employment'])
    report['cpv_stage_before_reference'] = {k:report[k] for k in ('input','placement','projection') if k in report}
    report['input'] = employment_summary(result)
    report['input_scope'] = 'retained BBOX weights after EIC calibration; full CPV stage preserved separately'
    report['placement'] = {'retained':employment_summary(result)}
    report['projection'] = dict(model_year=2025,policy='EIC 2025 municipal controls allocated by full CPV 2020 spatial weights',
        assumption='Uniform municipal allocation to existing urban weights; rural/unlocated/outside-map shares not absorbed',
        factors={k:v['population_weight_factor'] for k,v in details.items()})
    report['demographic_reference'] = dict(mode='eic2025',reference_year=2025,confidence_level=.90,
        universe='residents of inhabited private dwellings',municipalities=details,sources=reference_identity(macro,root),
        spatial_reference_year=2020,ignored_growth_factors=macro.get('growth_factors',{}),
        occupied_semantics='resident occupied persons, not workplace jobs',
        commuter_semantics='occupied residents reporting work travel, time codes 1..6; unspecified excluded',
        allocation_basis='CPV full municipal POBTOT/POCUPADA as spatial weights, not a claim of identical historical universes')
    report.setdefault('implementation_sha256', {})['demographic_reference.py'] = file_sha256(__file__)
    result.attrs['residential_employment'] = report
    result.attrs['residential_placement']['reference_calibration'] = report['demographic_reference']
    # Placement source diagnostics describe pre-calibration CPV weights.
    result.attrs['residential_placement']['pre_reference_weights'] = True
    return result
