"""Official territorial toponymy: acquisition, geometry, identity and diagnostics.

The review owns the delivered list. Neither scans nor builds silently add businesses
or infer a settlement's location from economic activity.
"""
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import zipfile

from shapely.geometry import box, shape
from shapely import make_valid
from shapely.errors import ShapelyError

from .place_identity import merge_places
from .source_downloads import atomic_json, sha256

ENGINE = 'official_v1'
KINDS = ('localities', 'dcah', 'settlement_catalog')
MAX_BYTES = 768 * 1024**2


def _download_message(message):
    # The acquisition client reports integer MB; a small positive response is
    # less than one MB, rather than a zero-byte transfer with a zero-byte total.
    return re.sub(r'(?<!\d)0 MB', '<1 MB', message)


def options(config):
    value = config.get('toponymy', {})
    if not isinstance(value, dict) or value.get('engine', ENGINE) != ENGINE:
        raise ValueError('El pipeline de nombres debe ser official_v1.')
    if not isinstance(value.get('osm_fallback', False), bool):
        raise ValueError('osm_fallback debe ser verdadero o falso.')
    if not isinstance(value.get('sources', []), list):
        raise ValueError('toponymy.sources debe ser una lista.')
    if 'layout' in value:
        from .toponymy_layout import validate_options
        validate_options(value)
    return value


def validate_bbox(bbox):
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox) or
            not -180 <= bbox[0] < bbox[2] <= 180 or not -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError('Define un BBOX geográfico válido antes de escanear nombres.')
    return box(*bbox)


def _code(value, width):
    text = str(value or '').strip()
    return text.zfill(width) if text.isdigit() and len(text) <= width else ''


def _identity(props, kind):
    raw = str(props.get('cvegeo') or props.get('cve_geo') or '').strip()
    ent = _code(props.get('cve_ent'), 2) or raw[:2]
    mun = _code(props.get('cve_mun'), 3) or raw[2:5]
    loc = _code(props.get('cve_loc'), 4) or raw[5:9]
    code = ent + mun + loc
    if len(code) != 9 or not code.isdigit():
        raise ValueError('Falta la clave territorial completa de la localidad.')
    if not 1 <= int(ent) <= 32 or mun == '000' or loc == '0000':
        raise ValueError('Clave territorial fuera de dominio.')
    if kind != 'localities':
        asen = _code(props.get('cve_asen'), 4) or (raw[9:13] if len(raw) == 13 else '')
        if len(asen) != 4 or not asen.isdigit():
            raise ValueError('Falta CVE_ASEN; no se puede identificar el asentamiento.')
        code += asen
    if raw and (len(raw) not in (9, 13, 16) or raw[:9] != code[:9] or
                (kind != 'localities' and len(raw) == 13 and raw != code)):
        raise ValueError('CVEGEO y las claves territoriales no coinciden.')
    return code, dict(cve_ent=ent, cve_mun=ent + mun, cve_loc=ent + mun + loc)


def _name(props, kind):
    raw = (props.get('nomgeo') or props.get('nom_loc') or '') if kind == 'localities' else (props.get('nom_asen') or '')
    name = str(raw).strip()
    if not name or name.upper() in ('NAN', 'NULL', 'NINGUNO', 'SIN NOMBRE'):
        raise ValueError('Nombre vacío o sin identificar.')
    name = unicodedata.normalize('NFC', name)
    typ = str(props.get('tipo') or props.get('tipo_asen') or '').strip().upper()
    # Preserve official spelling; only contextualize numbered territorial units.
    for token, prefix in (('SUPERMANZANA', 'Supermanzana'), ('REGIÓN', 'Región'), ('REGION', 'Región')):
        if typ == token and re.fullmatch(r'\d+[A-Za-z]?', name):
            name = prefix + ' ' + name
    return name, typ


def _records(path, kind, bbox=None, depth=0):
    """Read tables separately from geometry; transform declared vector CRS to WGS84."""
    if path.suffix.lower() in ('.json', '.geojson'):
        doc = json.loads(path.read_text(encoding='utf-8-sig'))
        if isinstance(doc, dict) and doc.get('type') == 'FeatureCollection':
            crs = doc.get('crs', {}).get('properties', {}).get('name', 'EPSG:4326')
            if crs not in ('EPSG:4326', 'urn:ogc:def:crs:OGC:1.3:CRS84', 'urn:ogc:def:crs:EPSG::4326'):
                import geopandas as gpd
                frame = _read_vector(path, bbox)
                yield from _frame_records(frame)
                return
            for f in doc.get('features', []):
                yield f.get('properties') or {}, f.get('geometry')
            return
        rows = doc.get('datos') if isinstance(doc, dict) else doc
        if not isinstance(rows, list):
            raise ValueError('Se esperaba una FeatureCollection o el catálogo tabular INEGI.')
        for row in rows:
            yield row, None
        return
    if path.suffix.lower() == '.csv':
        import pandas as pd
        for encoding in ('utf-8-sig', 'cp1252'):
            try:
                frame = pd.read_csv(path, dtype=str, encoding=encoding, sep=None, engine='python').fillna('')
                break
            except UnicodeDecodeError:
                continue
        for row in frame.to_dict('records'):
            yield row, None
        return
    import geopandas as gpd
    if path.suffix.lower() == '.zip' or (path.suffix.lower() == '.bin' and zipfile.is_zipfile(path)):
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (len(members) > 10000 or sum(m.file_size for m in members) > 4 * 1024**3 or
                    any('..' in Path(m.filename.replace('\\', '/')).parts or
                        m.filename.startswith(('/', '\\')) or ':' in m.filename for m in members)):
                raise ValueError('Paquete vectorial fuera de los límites de importación.')
            layers = sorted(m.filename for m in members if m.filename.lower().endswith('.shp'))
            if not layers:
                # INEGI publishes a national wrapper containing an integrated
                # shapefile ZIP plus state copies. Read the integrated copy once.
                nested = [m.filename for m in members if Path(m.filename).name.lower() == '00_integrado.zip']
                if depth >= 1 or not nested:
                    raise ValueError('El ZIP debe contener shapefiles con sus archivos auxiliares.')
                with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.zip', delete=False) as stream:
                    inner = Path(stream.name)
                    with archive.open(nested[0]) as source:
                        import shutil
                        shutil.copyfileobj(source, stream)
                try:
                    yield from _records(inner, kind, bbox, depth=depth+1)
                finally:
                    inner.unlink(missing_ok=True)
                return
        for layer in layers:
            yield from _frame_records(_read_vector('zip://' + str(path.resolve()).replace('\\', '/') + '!' + layer, bbox))
    elif path.suffix.lower() == '.shp':
        yield from _frame_records(_read_vector(path, bbox))
    else:
        raise ValueError('Usa JSON, GeoJSON, CSV, SHP o un ZIP de shapefiles.')


def _read_vector(path, bbox):
    import geopandas as gpd
    import pyogrio
    from pyproj import Transformer
    info = pyogrio.read_info(path, force_feature_count=False, force_total_bounds=False)
    if info['crs'] is None:
        raise ValueError('La capa no declara CRS; incluye el archivo .prj.')
    source_bbox = Transformer.from_crs(4326, info['crs'], always_xy=True).transform_bounds(*bbox, densify_pts=21) if bbox else None
    return gpd.read_file(path, bbox=source_bbox, engine='pyogrio')


def _frame_records(frame):
    if frame.crs is None:
        raise ValueError('La capa no declara CRS; incluye el archivo .prj.')
    frame = frame.to_crs(4326)
    for _, row in frame.iterrows():
        geom = row.geometry
        yield {k: ('' if v is None else str(v)) for k, v in row.items() if k != frame.geometry.name}, (
            geom.__geo_interface__ if geom is not None else None)


def read_source(path, kind, bbox):
    if kind not in KINDS:
        raise ValueError('Fuente de nombres desconocida.')
    area = validate_bbox(bbox)
    path = Path(path)
    places, unlocated = [], []
    stats = Counter(rows=0, invalid=0, outside=0, inactive=0, repaired=0)
    errors = []
    digest = sha256(path)
    source_path = str(path.resolve())
    for raw, geometry in _records(path, kind, bbox):
        stats['rows'] += 1
        try:
            if not isinstance(raw, dict):
                raise ValueError('El registro no es un objeto de atributos.')
            props = {str(k).lower(): v for k, v in raw.items()}
            if str(props.get('estatus', '1')) in ('0', '0.0'):
                stats['inactive'] += 1
                continue
            code, territory = _identity(props, kind)
            name, typ = _name(props, kind)
            if kind == 'settlement_catalog':
                unlocated.append(dict(code=code, name=name, type=typ, **territory))
                continue
            original = None
            if geometry:
                geom = shape(geometry)
                if geom.is_empty or geom.geom_type not in ('Point', 'Polygon', 'MultiPolygon'):
                    raise ValueError('Geometría no válida para una etiqueta territorial.')
                if kind == 'dcah' and geom.geom_type == 'Point':
                    raise ValueError('DCAH requiere el polígono, no un punto inferido.')
                if not geom.is_valid:
                    geom = make_valid(geom)
                    stats['repaired'] += 1
                if geom.geom_type not in ('Point', 'Polygon', 'MultiPolygon') or geom.is_empty:
                    raise ValueError('La reparación no produjo una geometría territorial válida.')
                if (not all(math.isfinite(x) for x in geom.bounds) or
                        not -180 <= geom.bounds[0] <= geom.bounds[2] <= 180 or
                        not -90 <= geom.bounds[1] <= geom.bounds[3] <= 90):
                    raise ValueError('Coordenadas fuera de WGS84; revisa el CRS.')
                if not geom.intersects(area):
                    stats['outside'] += 1
                    continue
                clipped = geom.intersection(area)
                if clipped.geom_type not in ('Point', 'Polygon', 'MultiPolygon') or clipped.is_empty or (
                        geom.geom_type != 'Point' and clipped.area == 0):
                    stats['outside'] += 1
                    continue
                if geom.geom_type != 'Point':
                    original = geom.__geo_interface__
                    # Do not round the point back across a narrow boundary.
                    point = clipped.representative_point()
                    loc, position = [point.x, point.y], 'polygon_interior'
                else:
                    loc, position = [geom.x, geom.y], 'official_point'
            else:
                if kind == 'dcah':
                    raise ValueError('DCAH sin polígono; no se inferirán coordenadas.')
                loc = [float(props['longitud']), float(props['latitud'])]
                if not all(math.isfinite(v) for v in loc) or not -180 <= loc[0] <= 180 or not -90 <= loc[1] <= 90:
                    raise ValueError('Coordenadas oficiales inválidas.')
                if not area.covers(shape(dict(type='Point', coordinates=loc))):
                    stats['outside'] += 1
                    continue
                position = 'official_point'
            population = props.get('pob_total', '')
            population = int(str(population)) if str(population).isdigit() else None
            urban = str(props.get('ambito', '')).upper() in ('U', 'URBANO', 'URBANA')
            # Cartographic scale is explicit, not an assertion of political rank.
            ptype = ('city' if urban and population is not None and population >= 100000 else
                     'town' if urban else 'village') if kind == 'localities' else (
                     'suburb' if typ in ('SUPERMANZANA', 'REGION', 'REGIÓN', 'SECTOR') else 'neighbourhood')
            place = dict(id='inegi_' + ('locality_' if kind == 'localities' else 'settlement_') + code,
                         official_code=code, feature_kind='locality' if kind == 'localities' else 'settlement',
                         name=name, original_name=name, loc=loc, type=ptype, official_type=ptype,
                         source='INEGI_LOCALITIES' if kind == 'localities' else 'INEGI_DCAH',
                         source_files=[source_path], source_hash=digest,
                         position_method=position, population=population,
                         is_urban=urban if kind == 'localities' else False,
                         source_vintage=str(props.get('fecha_act') or props.get('periodo') or ''),
                         municipality=str(props.get('nom_mun') or ''),
                         locality=str(props.get('nom_loc') or (name if kind == 'localities' else '')),
                         category='LOCALIDAD' if kind == 'localities' else typ or 'COLONIA',
                         tipo_asent=typ, is_micro=typ in ('PRIVADA', 'COTO'), **territory)
            if original:
                place['boundary'] = original
            places.append(place)
        except (ValueError, TypeError, KeyError, OverflowError, ShapelyError) as error:
            stats['invalid'] += 1
            if len(errors) < 10:
                errors.append(dict(row=stats['rows'], message=str(error)))
    return places, unlocated, dict(stats), errors


def source_files(project, opts):
    """Only this project's dedicated source folder; no recursive city fallbacks."""
    entries = list(opts.get('sources', []))
    folder = Path(project) / 'toponymy' / 'sources'
    for kind in KINDS:
        entries.extend(dict(kind=kind, path=str(p)) for p in sorted(folder.glob(kind + '_*'))
                       if p.suffix.lower() in ('.json', '.geojson', '.csv', '.shp', '.zip')
                       and not p.name.endswith('.source.json'))
    seen = set()
    for item in entries:
        kind, path = item.get('kind'), Path(item.get('path', ''))
        if kind not in KINDS or not item.get('path'):
            raise ValueError('Cada fuente debe declarar kind y path.')
        path = path if path.is_absolute() else Path(project) / path
        key = (kind, str(path.resolve()))
        if key not in seen:
            seen.add(key)
            yield kind, path


def scan_official_catalog(city_file, progress=None):
    from .toponymy import _toponymy_context, _load_osm_places
    from .toponymy_homogenizer import ToponymyAnalyzer
    context = _toponymy_context(city_file)
    root, project, config, bbox = context
    opts = options(config)
    validate_bbox(bbox)
    progress = progress or (lambda message: None)
    sources, candidates, unlocated, conflicts = [], [], [], []
    indexed = {}
    for kind, path in source_files(project, opts):
        progress('Leyendo ' + kind + ' · ' + path.name)
        item = dict(kind=kind, path=str(path), status='ok')
        try:
            found, missing, counts, errors = read_source(path, kind, bbox)
            item.update(counts=counts, errors=errors, sha256=sha256(path), labels=len(found))
            provenance = path.with_name(path.name + '.source.json')
            if provenance.exists():
                record = json.loads(provenance.read_text(encoding='utf-8'))
                if record.get('sha256') != item['sha256']:
                    raise ValueError('La fuente cambió desde su adquisición; vuelve a importarla.')
                item['provenance'] = record
                rejected = record.get('counts', {}).get('invalid', 0)
                if rejected:
                    item.update(status='partial', message=f'{rejected} registros originales rechazados; revisa el diagnóstico.')
                if record.get('bbox') and not validate_bbox(record['bbox']).covers(validate_bbox(bbox)):
                    item.update(status='partial', message='El área del mapa creció desde la adquisición; vuelve a descargar esta fuente.')
            if counts['invalid']:
                item.update(status='partial', message=f"{counts['invalid']} registros inválidos; revisa el diagnóstico.")
            for place in found:
                previous = indexed.get(place['id'])
                if previous:
                    if previous['name'] != place['name'] or previous['loc'] != place['loc']:
                        conflicts.append(dict(id=place['id'], files=previous['source_files'] + place['source_files']))
                    continue
                indexed[place['id']] = place
            unlocated.extend(missing)
        except (OSError, ValueError, RuntimeError, TypeError, ShapelyError, zipfile.BadZipFile) as error:
            item.update(status='error', message=str(error))
        sources.append(item)
    candidates = sorted(indexed.values(), key=lambda p: p['id'])
    # Conflicting editions do not win by filename order.
    conflicting = {p['id'] for p in conflicts}
    candidates = [p for p in candidates if p['id'] not in conflicting]
    locality_names = {p['cve_loc']: p['name'] for p in candidates if p['feature_kind'] == 'locality'}
    municipality_names = {p['cve_mun']: p['municipality'] for p in candidates if p.get('municipality')}
    for p in candidates:
        if p['feature_kind'] == 'settlement':
            p['locality'] = locality_names.get(p['cve_loc'], p['locality'])
            p['municipality'] = p['municipality'] or municipality_names.get(p['cve_mun'], '')
    codes = {p['official_code'] for p in candidates}
    covered_localities = set(locality_names)
    unlocated = sorted({p['code']: p for p in unlocated if p['code'] not in codes and
                        p['cve_loc'] in covered_localities}.values(), key=lambda p: p['code'])
    for kind in ('localities', 'dcah'):
        if not any(s['kind'] == kind for s in sources):
            sources.append(dict(kind=kind, status='missing', path='',
                                message='Descarga localidades oficiales.' if kind == 'localities' else
                                'Importa polígonos DCAH. El catálogo tabular no contiene su ubicación.'))
    if opts.get('osm_fallback', False):
        progress('Extrayendo etiquetas complementarias OSM')
        candidates = merge_places(candidates, _load_osm_places(city_file, context, sources))
    from .place_identity import merge_official_places
    discovered, identity_exceptions = merge_official_places(config.get('places', []), candidates,
                                                           config.get('deleted_places', []))
    warnings = [s.get('message', s['status']) for s in sources if s['status'] != 'ok']
    if conflicts:
        warnings.append(f'{len(conflicting)} claves con ediciones contradictorias; no se proponen.')
    if unlocated:
        warnings.append(f'{len(unlocated)} asentamientos del catálogo sin polígono disponible.')
    coverage = [dict(cve_mun=mun, labels=sum(p.get('cve_mun') == mun for p in candidates))
                for mun in sorted({p['cve_mun'] for p in candidates if p.get('cve_mun')})]
    signature = dict(engine=ENGINE, code_hash=sha256(__file__), bbox=bbox, options=opts, sources=sources,
                     deleted=config.get('deleted_places', []), places=config.get('places', []))
    fingerprint = hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result = dict(engine=ENGINE, schema_version=3, total=len(discovered), places=discovered,
                  candidates=candidates, new_candidates=len(candidates), preserved=len(config.get('places', [])),
                  sources=sources, coverage=coverage, coverage_complete=False,
                  coverage_note='Disponibilidad observada; no certifica cobertura exhaustiva de colonias.',
                  unlocated=unlocated, conflicts=conflicts, warnings=warnings, partial=bool(warnings),
                  exceptions=identity_exceptions,
                  fingerprint=fingerprint, diagnosis=ToponymyAnalyzer.analyze_collection(discovered), bbox=bbox)
    folder = project / '.toponymy'
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / 'official-catalog.json', result)
    return result


def acquire_catalogs(city_file, progress=None, refresh=False):
    """Acquire the documented tabular APIs by intersecting municipality, not name."""
    from .toponymy import _toponymy_context
    from .source_downloads import Client, geography
    _, project, config, bbox = _toponymy_context(city_file)
    options(config)
    progress = progress or (lambda message: None)
    active = 'Ámbito geográfico INEGI'
    client = Client(project / '.toponymy' / 'http', progress=lambda message: progress(active + ' · ' + _download_message(message)), refresh=refresh)
    geo = geography(client, bbox)
    folder = project / 'toponymy' / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    errors, files = [], []
    for municipal in geo['municipalities']:
        code = municipal['code']
        for kind, service in (('localities', 'localidades'), ('settlement_catalog', 'asentamientos')):
            url = f'https://gaia.inegi.org.mx/wscatgeo/v2/{service}/{code[:2]}/{code[2:]}'
            active = service + ' · ' + municipal['name']
            progress(active + ' · ' + url)
            try:
                doc, record = client.json(url, ttl=7 * 86400)
                if not isinstance(doc, dict) or not isinstance(doc.get('datos'), list) or doc.get('numReg') != len(doc['datos']):
                    raise ValueError('Respuesta INEGI incompleta o no tabular.')
                for row in doc['datos']:
                    row['nom_mun'] = municipal['name']
                path = folder / f'{kind}_{code}.json'
                atomic_json(path, doc)
                record = dict(record, raw_sha256=record['sha256'], sha256=sha256(path),
                              kind=kind, metadata=doc.get('metadatos', {}))
                atomic_json(path.with_name(path.name + '.source.json'), record)
                files.append(str(path))
            except (OSError, ValueError) as error:
                errors.append(dict(url=url, message=str(error)))
    return dict(files=files, errors=errors, partial=bool(errors), geography=geo)


def acquire_dcah(city_file, progress=None, refresh=False):
    """Resolve the published national SHP product, then materialize only this map."""
    from .toponymy import _toponymy_context
    from .source_downloads import Client, INEGI, official_url
    from urllib.parse import urljoin
    _, project, config, bbox = _toponymy_context(city_file)
    options(config)
    validate_bbox(bbox)
    progress = progress or (lambda message: None)
    client = Client(project / '.toponymy' / 'http', progress=progress, refresh=refresh)
    query = {key: '' for key in ('enti','muni','loca','tema','titg','esca','form','edic','seri',
                                'clave','rango','busc','wordag','mkeys','mageo','formIncl','formExcl')}
    query.update(prog='3374',tipoB=1,adv=False,orden=4,desc=True,pag=0,tam=50)
    catalog_url = INEGI + '/app/api/productos/interna_v2/componente/mapas/lista/resultados/'
    progress('Consultando productos oficiales DCAH · ' + catalog_url)
    catalog, _ = client.json(catalog_url, payload=query, ttl=7 * 86400)
    products = [p for p in catalog.get('list', {}).get('mapas', [])
                if 'delimitaci' in p.get('titulo', '').casefold() and
                isinstance(p.get('edicion'), int)] if catalog.get('success') else []
    products.sort(key=lambda p: (p['edicion'], p.get('key','')), reverse=True)
    product, fmt = next(((p,f) for p in products for f in p.get('formatos', [])
                         if f.get('extension') == 'SHP' and f.get('url', {}).get('valor')), (None,None))
    if not product:
        raise ValueError('El catálogo oficial no publicó un enlace SHP de DCAH.')
    url = official_url(urljoin(INEGI,fmt['url']['valor']))
    title = product['titulo']
    client.progress = lambda message: progress(title + ' · ' + _download_message(message))
    progress(title + ' · Paquete nacional: ' + str(fmt.get('peso','?')) + ' ' + str(fmt.get('siglas','MB')))
    path, record = client.fetch(url, limit=MAX_BYTES)
    progress(title + ' · Validando geometrías y recortando al área del proyecto')
    found, _, counts, errors = read_source(path, 'dcah', bbox)
    if not found:
        raise ValueError('DCAH sin polígonos válidos para el área: ' + json.dumps(dict(counts=counts,errors=errors),ensure_ascii=False))
    features = [dict(type='Feature', properties=dict(CVEGEO=p['cve_loc'], CVE_ASEN=p['official_code'][9:],
                    NOM_ASEN=p['original_name'], TIPO=p['tipo_asent'], FECHA_ACT=p['source_vintage']),
                    geometry=p['boundary']) for p in found]
    # Publication replaces only the owned automatic materialization, not imports.
    folder = project / 'toponymy' / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'dcah_automatic.geojson'
    atomic_json(target,dict(type='FeatureCollection',features=features))
    atomic_json(target.with_name(target.name+'.source.json'),dict(record,
                raw_sha256=record['sha256'],sha256=sha256(target),kind='dcah',product_key=product['key'],
                product_title=title,edition=product['edicion'],bbox=bbox,counts=counts,errors=errors))
    return dict(files=[str(target)], labels=len(found), errors=errors, partial=bool(counts['invalid']),
                coverage_complete=False, product=title, url=url)


def import_source(city_file, kind, filename, content):
    """Validate a staged input before publishing an immutable, project-owned source."""
    from .toponymy import _toponymy_context
    _, project, config, bbox = _toponymy_context(city_file)
    options(config)
    suffix = Path(filename).suffix.lower()
    if kind not in ('localities', 'dcah') or suffix not in ('.json', '.geojson', '.csv', '.zip'):
        raise ValueError('Importa localidades o DCAH en JSON, GeoJSON, CSV o ZIP de shapefiles.')
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Fuente vacía o mayor de 768 MB.')
    folder = project / 'toponymy' / 'sources'
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=folder, suffix=suffix, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        found, _, counts, errors = read_source(temporary, kind, bbox)
        if not counts['rows'] or counts['invalid'] or not found:
            raise ValueError('Fuente sin etiquetas válidas dentro del mapa: ' + json.dumps(dict(counts=counts, errors=errors), ensure_ascii=False))
        digest = sha256(temporary)
        path = folder / f'{kind}_{digest[:20]}{suffix}'
        os.replace(temporary, path)
        atomic_json(path.with_name(path.name + '.source.json'), dict(kind=kind, sha256=digest,
                    original_filename=Path(filename).name, acquisition='user_import',
                    coverage='partial_or_unknown', counts=counts))
        return dict(path=str(path), labels=len(found), sha256=digest)
    finally:
        temporary.unlink(missing_ok=True)
