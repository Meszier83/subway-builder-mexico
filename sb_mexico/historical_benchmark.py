"""Inspection-only historical CE contracts. Never changes attraction weights."""
import csv
import io
import re
from pathlib import Path

from sb_mexico.ce_controls import normalize, read_saic_controls


def validate_historical_contract(contract):
    if not isinstance(contract, dict) or contract.get('role') != 'historical_benchmark':
        return None
    if type(contract.get('schema_version')) is not int or contract['schema_version'] != 1:
        raise ValueError('Historical benchmark schema_version must be 1')
    if contract.get('enabled') is not False:
        raise ValueError('Historical benchmark activation is disabled during validation')
    if type(contract.get('reference_year')) is not int or not 1900 <= contract['reference_year'] <= 2100:
        raise ValueError('Historical benchmark requires an explicit CE reference_year')
    edition = contract.get('denue_edition')
    if not isinstance(edition, dict) or not str(edition.get('evidence', '')).strip():
        raise ValueError('DENUE edition requires evidence, including when unknown')
    if edition.get('reference_year') is not None and type(edition['reference_year']) is not int:
        raise ValueError('DENUE reference_year must be an integer or null for unknown')
    if contract.get('scope_rule_version') != 'saic_private_paraestatal_v1':
        raise ValueError('Unsupported historical benchmark scope rules')
    for field in ('coverage_evidence', 'transfer_assumption'):
        if not isinstance(contract.get(field), str) or not contract[field].strip():
            raise ValueError('Historical benchmark requires ' + field)
    files = contract.get('ce_sources')
    if not isinstance(files, list) or not files or any(not isinstance(p, str) or not p.strip() for p in files):
        raise ValueError('Historical benchmark requires selected ce_sources')
    assigned = []
    groups = contract.get('groups', [])
    if not isinstance(groups, list):
        raise ValueError('Historical benchmark groups must be a list')
    for group in groups:
        if not isinstance(group, dict) or not re.fullmatch(r'\d{5}', str(group.get('municipality', ''))):
            raise ValueError('Invalid historical benchmark municipality')
        prefixes = group.get('scian_prefixes')
        if not isinstance(prefixes, list) or not prefixes or any(not isinstance(p, str) or not re.fullmatch(r'\d{2,6}', p) for p in prefixes):
            raise ValueError('Historical group requires explicit SCIAN code sets')
        if len(prefixes) != len(set(prefixes)) or any(a != b and (a.startswith(b) or b.startswith(a)) for a in prefixes for b in prefixes):
            raise ValueError('Overlapping SCIAN codes within historical group')
        for mun, previous in assigned:
            if mun == group['municipality'] and any(a.startswith(b) or b.startswith(a) for a in prefixes for b in previous):
                raise ValueError('Overlapping historical benchmark groups')
        assigned.append((group['municipality'], prefixes))
        if group.get('reporting_unit') not in ('establishment', 'unresolved', 'enterprise_or_special'):
            raise ValueError('Historical group needs reporting_unit decision')
    hashes = contract.get('source_sha256')
    if hashes is not None and (not isinstance(hashes, dict) or any(
            not isinstance(hashes.get(kind), list) or not hashes[kind] or any(
                not isinstance(h, str) or not re.fullmatch(r'[0-9a-f]{64}', h) for h in hashes[kind])
            for kind in ('denue', 'ce'))):
        raise ValueError('Invalid historical source hashes')
    return dict(role='historical_benchmark', enabled=False, reference_year=contract['reference_year'],
                denue_edition=edition, status='INSPECTION_ONLY', weights_changed=False)


def scope_disposition(code, activity):
    """Activity-class exclusions; never infer ownership from business names."""
    sector = code[:2]
    name = normalize(activity)
    if sector == '93': return 'excluded', 'public_administration'
    if sector == '11' and not code.startswith(('1125', '1141')):
        return 'excluded', 'agricultural_support'
    if code.startswith('81321') or ('ORGANIZACIONES' in name and ('RELIGIOSAS' in name or 'POLITICAS' in name)):
        return 'excluded', 'religious_or_political'
    if 'SECTOR PUBLICO' in name:
        return ('review', 'public_utility') if sector == '22' else ('excluded', 'public_service')
    if sector in ('21', '22', '23', '48', '49', '51', '52'):
        return 'review', 'reporting_unit'
    return 'candidate', 'ownership_and_vintage_unverified'


def classify_scope(frame, denue_paths):
    """Shared class-label triage; this does not certify institutional ownership."""
    labels = {}
    for path in denue_paths:
        raw = Path(path).read_bytes()
        for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        for row in csv.DictReader(io.StringIO(text)):
            labels.setdefault(str(row.get('codigo_act', '')).strip(), set()).add(row.get('nombre_act', ''))
    dispositions = []
    for code in frame.workplace_industry:
        names = labels.get(code, set())
        decisions = {scope_disposition(code, name)[0] for name in names}
        dispositions.append(next(iter(decisions)) if len(decisions) == 1 else 'review')
    frame = frame.copy()
    frame['_scope'] = dispositions
    return frame


def inspect_historical_benchmark(denue_paths, ce_paths, contract):
    from sb_mexico.inegi import load_denue
    from sb_mexico.workplace_employment import band, source_hashes
    header = validate_historical_contract(contract)
    if header is None:
        raise ValueError('Expected historical_benchmark contract')
    hashes = dict(denue=source_hashes(denue_paths), ce=source_hashes(ce_paths))
    if not hashes['denue'] or not hashes['ce']:
        raise ValueError('CE and DENUE sources are both required')
    frame = load_denue(denue_paths, dict(min_lon=-180, max_lon=180, min_lat=-90, max_lat=90), full_scope=True)
    if 'workplace_industry' not in frame:
        frame['workplace_industry'] = ''
    if 'workplace_band' not in frame:
        frame['workplace_band'] = ''
    frame = classify_scope(frame, denue_paths)
    estimates = [band(v) for v in frame.workplace_band]
    controls = read_saic_controls(ce_paths, contract['reference_year'])
    all_totals = [r for r in controls.values() if r['geography_level'] == 'municipality' and r['stratum'] == 'TOTAL' and r['activity_code']]
    declarations = contract.get('groups', [])
    outcomes = []
    for control in all_totals:
        mun, prefixes = control['geography'], control['scian_prefixes']
        declared = next((g for g in declarations if g['municipality'] == mun and sorted(g['scian_prefixes']) == sorted(prefixes)), None)
        mask = frame.cve_mun_clean.eq(mun) & frame.workplace_industry.str.startswith(tuple(prefixes))
        retained = mask & frame['_scope'].ne('excluded')
        indices = frame.index[retained].tolist()
        bounds = [estimates[i] for i in indices]
        unknown = sum(b is None for b in bounds)
        known = [b for b in bounds if b is not None]
        low = sum(b[0] for b in known)
        high = sum(b[1] for b in known)
        published = control['employment'] is not None and control['establishments'] is not None
        feasible = None if not published or unknown else low <= control['employment'] <= high
        outcomes.append(dict(municipality=mun, activity_code=control['activity_code'], scian_prefixes=prefixes,
                             reporting_unit=declared['reporting_unit'] if declared else 'unresolved',
                             raw_records=int(mask.sum()), excluded_records=int((mask & ~retained).sum()),
                             review_records=int((retained & frame['_scope'].eq('review')).sum()),
                             remaining_records=len(indices), unknown_bands=unknown,
                             ce_personnel=control['employment'], ce_units=control['establishments'],
                             records_minus_ce_units=None if control['establishments'] is None else len(indices)-control['establishments'],
                             lower_bound=low, upper_bound=None if high == float('inf') else high,
                             hard_fit_within_bounds=feasible,
                             historical_personnel_per_unit=control['employment']/control['establishments'] if published and control['establishments'] else None,
                             status='CONDITIONAL_HISTORICAL_BENCHMARK' if published and indices else 'UNAVAILABLE'))
    unmatched = [g for g in declarations if not any(o['municipality'] == g['municipality'] and sorted(o['scian_prefixes']) == sorted(g['scian_prefixes']) for o in outcomes)]
    if hashes != dict(denue=source_hashes(denue_paths), ce=source_hashes(ce_paths)):
        raise ValueError('Sources changed during historical inspection; inspect again')
    return dict(**header, source_sha256=hashes,
                source_binding='unbound' if contract.get('source_sha256') is None else 'match' if contract['source_sha256'] == hashes else 'mismatch',
                scope_rule_version=contract['scope_rule_version'], coverage_evidence=contract['coverage_evidence'],
                transfer_assumption=contract['transfer_assumption'], full_scope_records=len(frame),
                scope_counts={k: int(frame['_scope'].eq(k).sum()) for k in ('candidate', 'excluded', 'review')},
                groups=outcomes, unmatched_declarations=unmatched,
                publication_counts=dict(published=sum(r['employment'] is not None for r in all_totals),
                                        suppressed=sum(r['employment'] is None for r in all_totals)),
                confidentiality_rows=sum(r['stratum'] == 'AGRUPADOS POR CONFIDENCIALIDAD' for r in controls.values()),
                limitations=['Historical diagnostics only; no employment weights were changed.',
                             'Record count differences and feasible bounds do not prove comparable coverage.',
                             'Missing sector rows remain absent; no zero or state fallback is imputed.',
                             'Activity classes do not establish ownership of every retained workplace.'])
