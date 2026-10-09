"""Bounded source reads and territorial DENUE grouping for the Wizard."""
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import math
import numpy as np
import pandas as pd
from .place_identity import place_name_key, place_id

INVALID_NAMES = {'', 'NAN', 'NINGUNO', 'OTRO', 'SIN NOMBRE', 'DESCONOCIDO', 'NULL', 'NO APLICA'}


def read_denue_sources(paths, bbox, diagnostics=None):
    frames, seen = [], set()
    columns = {'id', 'clee', 'nom_estab', 'latitud', 'longitud', 'localidad', 'municipio',
               'cve_ent', 'cve_mun', 'cve_loc', 'nomb_asent', 'asentamiento', 'tipo_asent', 'tipo_asentamiento'}
    for path in paths:
        item = dict(kind='DENUE', path=str(path), status='ok', rows_in_bbox=0, duplicates=0)
        try:
            for encoding in ('utf-8-sig', 'cp1252', 'latin1'):
                try:
                    frame = pd.read_csv(path, encoding=encoding, dtype=str, low_memory=False,
                                        usecols=lambda c: c.lower() in columns)
                    break
                except UnicodeDecodeError:
                    continue
            frame.columns = frame.columns.str.lower()
            if not {'latitud', 'longitud'} <= set(frame.columns):
                raise ValueError('Faltan las columnas latitud y longitud')
            if not {'nomb_asent', 'asentamiento', 'localidad'} & set(frame.columns):
                raise ValueError('Faltan nombres de asentamiento o localidad')
            frame['lon'] = pd.to_numeric(frame['longitud'], errors='coerce')
            frame['lat'] = pd.to_numeric(frame['latitud'], errors='coerce')
            frame = frame[frame.lon.between(bbox[0], bbox[2]) & frame.lat.between(bbox[1], bbox[3])].copy()
            item['rows_in_bbox'] = len(frame)
            frame = frame.fillna('')
            for column in columns:
                if column not in frame:
                    frame[column] = ''
            keep = []
            for row in frame[['clee', 'id', 'cve_ent']].itertuples(index=False, name=None):
                key = ('clee', row[0]) if row[0] else (('id', row[2], row[1]) if row[1] else None)
                keep.append(key is None or key not in seen)
                if key:
                    seen.add(key)
            item['duplicates'] = len(keep) - sum(keep)
            frame = frame.loc[keep].copy()
            frame['source_file'] = str(Path(path).resolve())
            frame['nomb_asent'] = frame['nomb_asent'].where(frame['nomb_asent'] != '', frame['asentamiento'])
            frame['tipo_asent'] = frame['tipo_asent'].where(frame['tipo_asent'] != '', frame['tipo_asentamiento'])
            frames.append(frame)
        except (OSError, ValueError, pd.errors.ParserError) as error:
            item.update(status='error', message=str(error))
        if diagnostics is not None:
            diagnostics.append(item)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _code(value, width):
    value = str(value).strip()
    return value.zfill(width) if value.isdigit() else ''


@lru_cache(maxsize=40000)
def _name(name, kind, cities):
    from .toponymy import format_clean_place_name
    from .toponymy_homogenizer import smart_title_case
    return (smart_title_case(name), 'city') if cities else format_clean_place_name(name, kind.upper().replace('Ó', 'O'))


def _parts(rows, radius=5000):
    """Keep disconnected names apart without quadratic point comparisons."""
    groups, grid, anchors = [], defaultdict(list), []
    lat0 = float(rows[0][1])
    scale = 111320 * math.cos(math.radians(lat0))
    for row in sorted(rows, key=lambda r: (r[0], r[1])):
        x, y = row[0] * scale, row[1] * 111320
        cell = (math.floor(x / radius), math.floor(y / radius))
        choices = [(math.hypot(x - anchors[i][0], y - anchors[i][1]), i)
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for i in grid.get((cell[0] + dx, cell[1] + dy), ())]
        closest = min(choices, default=(math.inf, -1))
        if closest[0] > radius:
            closest = (0, len(groups))
            groups.append([]); anchors.append((x, y)); grid[cell].append(closest[1])
        groups[closest[1]].append(row)
    return groups


def denue_places(frame, min_count=8, cities=False):
    from .toponymy_homogenizer import ToponymyAnalyzer
    if frame.empty:
        return []
    fields = ['lon', 'lat', 'localidad' if cities else 'nomb_asent', 'tipo_asent',
              'cve_ent', 'cve_mun', 'cve_loc', 'municipio', 'localidad', 'source_file']
    groups = defaultdict(list)
    for row in frame[fields].itertuples(index=False, name=None):
        raw = row[2].strip()
        if raw.upper() in INVALID_NAMES or (not cities and raw.upper() == 'CENTRO'):
            continue
        name, typ = _name(raw, row[3], cities)
        ent = _code(row[4], 2)
        mun = _code(row[5], 3)
        mun = ent + mun if ent and len(mun) == 3 else mun
        loc = _code(row[6], 4)
        loc = mun + loc if len(mun) == 5 and len(loc) == 4 else loc
        territory = (ent, mun or place_name_key(row[7]), loc or place_name_key(row[8]))
        groups[(place_name_key(name), territory)].append((float(row[0]), float(row[1]), name, typ,
                                                          ent, mun, loc, row[7], row[8], row[9], row[3], raw))
    result = []
    for records in (value for _, value in sorted(groups.items())):
        for part in ([records] if cities else _parts(records)):
            if len(part) < min_count:
                continue
            record = part[0]
            cls = ToponymyAnalyzer.classify_name(record[11])
            if cls['category'] == 'GENERIC':
                cls = ToponymyAnalyzer.classify_name(record[10] + ' ' + record[2])
            item = dict(name=record[2], type=record[3],
                        loc=[round(float(np.median([r[0] for r in part])), 5),
                             round(float(np.median([r[1] for r in part])), 5)],
                        source='INEGI_DENUE_LOCALIDAD' if cities else 'INEGI_DENUE',
                        source_files=sorted({r[9] for r in part}), establishments=len(part),
                        category='CIUDAD' if cities else cls['category'],
                        is_micro=cls['category'] == 'PRIVADA_CERRADA',
                        cve_ent=record[4], cve_mun=record[5], cve_loc=record[6],
                        municipality=record[7].strip(), locality=record[8].strip())
            item.update(tipo_asent=record[10], aliases=sorted({r[11] for r in part}))
            if cities:
                item.update(hierarchy_inferred=True,
                            hierarchy_note="Localidad sugerida por direcciones DENUE; el comercio no certifica rango de ciudad.")
            item['id'] = place_id(item)
            result.append(item)
    return sorted(result, key=lambda p: (-p['establishments'], p['id']))
