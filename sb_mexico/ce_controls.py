"""SAIC controls with explicit year, geography, activity and size identity."""
import csv
import io
import re
import unicodedata
from pathlib import Path


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode().upper().split())


def activity_codes(activity):
    text = normalize(activity)
    if text in ('TOTAL MUNICIPAL', 'TOTAL ESTATAL', 'TOTAL NACIONAL'):
        return '', ()
    text = re.sub(r'^SECTOR\s+', '', text)
    match = re.match(r'^(31-33|48-49|\d{2,6})(?:\s|$)', text)
    if not match:
        return None, ()
    code = match[1]
    return code, {'31-33': ('31', '32', '33'), '48-49': ('48', '49')}.get(code, (code,))


def read_saic_controls(paths, reference_year=None):
    """Keep suppressed values as None and split/confidentiality strata distinct.

    Duplicate numeric controls must agree; a blank never erases or fills a
    published value. Only explicitly identified SAIC rows are accepted.
    """
    controls = {}
    if reference_year is not None and type(reference_year) is not int:
        raise ValueError('CE reference_year must be an integer')
    for path in paths:
        raw = Path(path).read_bytes().rstrip(b'\x00')
        for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        lines = text.splitlines(keepends=True)
        start = next((i for i, line in enumerate(lines[:12]) if 'H001A' in line and 'Municipio' in line), None)
        if start is None:
            continue
        for rawrow in csv.DictReader(io.StringIO(''.join(lines[start:]))):
            row = {normalize(k): str(v or '').strip() for k, v in rawrow.items() if k}
            def field(prefix):
                return next((v for k, v in row.items() if k.startswith(prefix)), '')
            year = field('ANO CENSAL')
            if not re.fullmatch(r'\d{4}', year) or (reference_year is not None and int(year) != reference_year):
                continue
            ent = re.match(r'^(\d{2})\b', field('ENTIDAD'))
            mun_text = field('MUNICIPIO')
            mun = re.match(r'^(\d{3})\b', mun_text)
            if not ent or not 1 <= int(ent[1]) <= 32:
                continue
            activity = normalize(field('ACTIVIDAD ECONOMICA'))
            code, prefixes = activity_codes(activity)
            if code is None:
                continue
            if mun and mun[1] != '000':
                level, geography = 'municipality', ent[1] + mun[1]
                if activity in ('TOTAL ESTATAL', 'TOTAL NACIONAL'):
                    continue
            elif not mun_text and activity != 'TOTAL MUNICIPAL':
                level, geography = 'state', ent[1]
            else:
                continue
            strata = normalize(field('ESTRATO'))
            if strata in ('', 'TOTAL', 'SUMA', 'SUMA DE ESTRATOS', 'TOTAL DE ESTRATOS'):
                strata = 'TOTAL'
            if strata not in ('TOTAL', '0 A 10', '11 A 50', '51 A 250', '251 Y MAS', 'AGRUPADOS POR CONFIDENCIALIDAD'):
                continue
            def count(prefix):
                value = field(prefix)
                return int(value) if re.fullmatch(r'\d+', value) else None
            key = (int(year), level, geography, code, strata)
            value = dict(reference_year=int(year), geography_level=level, geography=geography,
                         activity_code=code, scian_prefixes=list(prefixes), stratum=strata,
                         employment=count('H001A'), establishments=count('UE '), name=mun_text)
            if key in controls:
                previous = controls[key]
                if (previous['employment'], previous['establishments']) != (value['employment'], value['establishments']):
                    raise ValueError('Contradictory CE controls: ' + str(key))
            else:
                controls[key] = value
    return controls
