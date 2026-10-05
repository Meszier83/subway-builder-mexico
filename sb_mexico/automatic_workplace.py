"""Apply one source-driven employment policy to every project."""
from pathlib import Path
import pandas as pd

from sb_mexico.ce_controls import read_saic_controls
from sb_mexico.historical_transfer import SUPPORTED, FORMULA_VERSION, validate_transfer_contract, checked_sources

UNIT_EVIDENCE = ('INEGI CE2024 Metodología, Table 3 PDF pages 20-21: ordinary establishment '
                 'reporting for manufacturing, commerce and these services. '
                 'https://www.inegi.org.mx/contenidos/programas/ce/2024/doc/889463925644.pdf. '
                 'Ownership, vintage and completeness remain conditional.')


def selected_municipalities(paths):
    from sb_mexico.inegi import format_cve_mun
    municipalities = set()
    for path in paths:
        for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
            try:
                frame = pd.read_csv(path, encoding=encoding, dtype=str,
                    usecols=lambda col: col.strip().lower() in ('cve_ent', 'cve_mun'))
                break
            except UnicodeDecodeError:
                continue
        frame.columns = [col.strip().lower() for col in frame]
        if 'cve_mun' not in frame:
            raise ValueError('Automatic CE selection requires DENUE cve_mun geography')
        pairs = frame[['cve_ent', 'cve_mun']].drop_duplicates() if 'cve_ent' in frame else frame[['cve_mun']].drop_duplicates().assign(cve_ent=None)
        municipalities.update(format_cve_mun(row.cve_mun, row.cve_ent) for row in pairs.itertuples())
    return municipalities


def resolve_automatic_workplace(denue_paths, ce_paths, macro, source_root=None):
    """Inspect data content, never project names. Return effective macro and notice.

    Same-year CE fitting is not inferred. Published historical size means may
    transfer under the existing restricted sector policy. Missing detail retains
    bounded priors with an explicit reason. Conflicting cells/years fail.
    """
    from sb_mexico.workplace_employment import source_hashes
    effective = dict(macro)
    if macro.get('workplace_employment') != 'auto':
        return effective, None
    root = Path(source_root or Path(__file__).resolve().parents[1]).resolve()
    initial_hashes = dict(denue=source_hashes(denue_paths), ce=source_hashes(ce_paths))
    municipalities = selected_municipalities(denue_paths)
    controls = read_saic_controls(ce_paths)
    relevant = [row for row in controls.values() if row['geography_level'] == 'municipality'
                and row['geography'] in municipalities
                and row['activity_code'] in SUPPORTED
                and row['stratum'] in ('0 A 10', '11 A 50', '51 A 250', '251 Y MAS')]
    years = {row['reference_year'] for row in relevant}
    if len(years) > 1:
        raise ValueError('Automatic CE transfer found multiple activity years; select one year of CE sources')
    published = [row for row in relevant if row['employment'] is not None
                 and row['establishments'] is not None and row['establishments'] > 0]
    if not published:
        reason = ('No selected CE files' if not ce_paths else
                  'CE files contain no usable published municipal sector-and-size employment/establishment cells')
        effective['workplace_employment'] = 'ce_bounded'
        if initial_hashes != dict(denue=source_hashes(denue_paths), ce=source_hashes(ce_paths)):
            raise ValueError('Sources changed during automatic CE inspection')
        return effective, dict(requested_mode='auto', effective_mode='ce_bounded', reason=reason)
    if not denue_paths:
        raise ValueError('Automatic employment requires selected DENUE sources')
    sources = []
    for value in ce_paths:
        path = Path(value).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Automatic historical CE sources must be inside the project workspace')
        sources.append(path.relative_to(root).as_posix())
    groups = {(row['geography'], row['activity_code']) for row in published}
    contract = dict(schema_version=1, role='historical_transfer', enabled=True,
        reference_year=next(iter(years)),
        denue_edition=dict(label='unknown', reference_year=None,
            evidence='Automatic selection has no independently confirmed DENUE edition manifest'),
        scope_rule_version='saic_private_paraestatal_v1',
        coverage_evidence='Selected source municipalities before clipping; ownership and completeness conditional, not independently certified',
        transfer_assumption='Historical municipal sector-size means transferred to current selected DENUE locations',
        ce_sources=sources, source_sha256=initial_hashes,
        formula_version=FORMULA_VERSION, strength=1,
        groups=[dict(municipality=municipality, scian_prefixes=SUPPORTED[sector],
                     reporting_unit='establishment', reporting_unit_evidence=UNIT_EVIDENCE)
                for municipality, sector in sorted(groups)])
    validate_transfer_contract(contract)
    effective.update(workplace_employment='historical_transfer', historical_workplace_transfer=contract)
    checked_sources(denue_paths, effective, root)
    return effective, dict(requested_mode='auto', effective_mode='historical_transfer',
                          reference_year=contract['reference_year'], selected_groups=len(groups),
                          reason='Published municipal sector-and-size cells found; conditional historical transfer enabled')
