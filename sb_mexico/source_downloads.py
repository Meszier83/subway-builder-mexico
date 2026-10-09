"""Official source acquisition, independent of demand/model configuration.

Downloads are staged, validated and published without overwriting manual inputs.
The cache stores original responses and their SHA-256 provenance.
"""
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import struct
import tempfile
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
import threading
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse

INEGI = 'https://www.inegi.org.mx'
SAIC = INEGI + '/app/api/saic/'
GEO = 'https://gaia.inegi.org.mx/wscatgeo/v2/geo/'
MARCO_CATALOG = INEGI + '/app/api/productos/interna_v2/ficha/datos?upc=889463807469&lang=false'
CONAPO = ('https://www.datos.gob.mx/dataset/f2b9b220-3ef7-4e3a-bde6-87e1dac78c6a/'
          'resource/3c3092be-583e-4490-8c23-67ef9a64b198/download/pobproy_quinq1.csv')
OSM = 'https://download.geofabrik.de/north-america/mexico-latest.osm.pbf'
EIC_INDICATORS = INEGI + '/contenidos/programas/eic/2025/datosabiertos/conjunto_de_datos_eic2025_105_csv.zip'
EIC_INDICATOR_NAME = 'conjunto_datos_eic2025_105.csv'
KINDS = ('denue', 'cpv', 'marco', 'eic', 'ce', 'enoe', 'conapo', 'osm')
LABELS = dict(denue='DENUE · descarga estatal', cpv='CPV 2020', marco='Marco 2020',
              eic='EIC 2025 · indicadores y personas de todos los estados',
              ce='SAIC 2023 · actividades y tamaños', enoe='ENOE', conapo='CONAPO municipal', osm='OSM México')
MANIFEST = '.source-downloads.json'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_manifest(folder):
    path = Path(folder) / MANIFEST
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'files': {}}


def atomic_json(path, data):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix='.part') as stream:
        temp = Path(stream.name)
        stream.write((json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def official_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or parsed.hostname not in
            ('www.inegi.org.mx', 'gaia.inegi.org.mx', 'www.datos.gob.mx', 'download.geofabrik.de')
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError('Enlace fuera de los proveedores oficiales permitidos')
    return url


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, cache, progress=None, refresh=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.progress = progress or (lambda message: None)
        self.refresh = refresh
        self.opener = urllib.request.build_opener(OfficialRedirect())

    def fetch(self, url, payload=None, limit=3 * 1024**3, ttl=None):
        official_url(url)
        body = json.dumps(payload, sort_keys=True).encode() if payload is not None else None
        key = hashlib.sha256(url.encode() + (body or b'')).hexdigest()
        target, metadata = self.cache / (key + '.bin'), self.cache / (key + '.json')
        if target.exists() and metadata.exists() and not self.refresh:
            record = json.loads(metadata.read_text(encoding='utf-8'))
            if ((ttl is None or time.time() - record['timestamp'] < ttl)
                    and record['bytes'] == target.stat().st_size and sha256(target) == record['sha256']):
                self.progress('Caché verificada: ' + url.rsplit('/', 1)[-1])
                return target, record
        for attempt in range(3):
            temp = None
            try:
                headers = {'User-Agent': 'SubwayBuilderMexico-source-downloader/1', 'Accept-Encoding': 'identity'}
                if body is not None:
                    headers['Content-Type'] = 'application/json'
                request = urllib.request.Request(url, data=body, headers=headers)
                with self.opener.open(request, timeout=45) as response:
                    official_url(response.url)
                    expected = int(response.headers.get('Content-Length', 0))
                    if expected > limit:
                        raise ValueError('Descarga demasiado grande')
                    with tempfile.NamedTemporaryFile(dir=self.cache, delete=False, suffix='.part') as stream:
                        temp = Path(stream.name)
                        size, last_update = 0, 0
                        while True:
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            size += len(block)
                            if size > limit:
                                raise ValueError('Descarga demasiado grande')
                            stream.write(block)
                            if time.monotonic() - last_update > 1:
                                self.progress(f'Descargando: {size // 1048576} MB' +
                                              (f' / {expected // 1048576} MB' if expected else ''))
                                last_update = time.monotonic()
                    if not size or (expected and size != expected):
                        raise ValueError('Descarga vacía o incompleta')
                    with temp.open('rb') as stream:
                        prefix = stream.read(1024).lstrip().lower()
                    if prefix.startswith((b'<!doctype html', b'<html')):
                        raise ValueError('El proveedor devolvió una página HTML, no el archivo solicitado')
                    record = dict(url=url, final_url=response.url, query=payload, timestamp=time.time(),
                                  bytes=size, sha256=sha256(temp),
                                  last_modified=response.headers.get('Last-Modified'),
                                  etag=response.headers.get('ETag'))
                os.replace(temp, target)
                atomic_json(metadata, record)
                return target, record
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt == 2 or (isinstance(error, urllib.error.HTTPError) and error.code < 500 and error.code != 429):
                    raise
                self.progress('Reintentando descarga…')
                time.sleep(attempt + 1)
            finally:
                if temp is not None:
                    temp.unlink(missing_ok=True)

    def json(self, url, payload=None, ttl=None):
        path, record = self.fetch(url, payload, limit=128 * 1024**2, ttl=ttl)
        return json.loads(path.read_bytes()), record


def geography(client, bbox):
    from shapely.geometry import shape, box
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in bbox)
            or not -180 <= bbox[0] < bbox[2] <= 180 or not -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError('Define un BBOX válido antes de preparar descargas')
    area = box(*bbox)
    states, municipalities = [], []
    document, _ = client.json(GEO + 'mgee/', ttl=7 * 86400)
    for feature in document['features']:
        geometry = shape(feature['geometry'])
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if not geometry.intersects(area):
            continue
        props = feature['properties']
        state = dict(code=props['cve_ent'], name=props['nomgeo'])
        states.append(state)
        document_mun, _ = client.json(GEO + 'mgem/' + state['code'], ttl=7 * 86400)
        for municipal in document_mun['features']:
            geometry = shape(municipal['geometry'])
            if not geometry.is_valid:
                geometry = geometry.buffer(0)
            if geometry.intersects(area):
                p = municipal['properties']
                municipalities.append(dict(code=p['cvegeo'], name=p['nomgeo'], state=state['code']))
    if not states or not municipalities:
        raise ValueError('El BBOX no intersecta municipios de México en el catálogo INEGI')
    return dict(states=sorted(states, key=lambda s: s['code']),
                municipalities=sorted(municipalities, key=lambda m: m['code']), bbox=list(bbox))


def marco_links(client):
    document, _ = client.json(MARCO_CATALOG, ttl=7 * 86400)
    if not document.get('success'):
        raise ValueError('Catálogo del Marco 2020 no disponible')
    links = {}
    def visit(nodes):
        for node in nodes or []:
            match = re.search(r'/([0-3]\d)_[^/]+\.zip$', node.get('url', ''))
            if match:
                links[match[1]] = official_url(urljoin(INEGI, node.get('absoluto', '') + node['url']))
            visit(node.get('hijos'))
    visit(document['info']['multiarchivos'])
    if set(links) != {f'{i:02}' for i in range(1, 33)}:
        raise ValueError('El catálogo del Marco 2020 no contiene las 32 entidades')
    return links


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text).lower() if not unicodedata.combining(c))


def csv_header(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(65536)
    if b'<html' in raw.lower() or b'<!doctype' in raw.lower():
        raise ValueError('El proveedor devolvió HTML en lugar del archivo')
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError('CSV con codificación desconocida')
    header = next(csv.reader(io.StringIO(text)), [])
    return {normalize(c.strip()) for c in header}


def validate_csv(path, kind):
    header = csv_header(path)
    required = dict(cpv={'entidad', 'mun', 'loc', 'ageb', 'mza', 'pobtot', 'p_15ymas', 'pocupada'},
                    denue={'latitud', 'longitud', 'codigo_act', 'per_ocu', 'cve_ent', 'cve_mun'},
                    conapo={'clave', 'ano', 'pob_total'},
                    eic_indicators={'cve_ent', 'cve_mun', 'cve_loc', 'estimador', 'pobtot', 'pocupada'},
                    eic_persons={'cvegeo', 'id_persona', 'edad', 'conact', 'tie_traslado_trab', 'factor',
                                 'mun_trab', 'ent_pais_trab'})[kind]
    if not required <= header:
        raise ValueError(f'{kind}: faltan columnas {sorted(required - header)}')


def safe_members(archive):
    members, size = [], 0
    for member in archive.infolist():
        name = PurePosixPath(member.filename.replace('\\', '/'))
        if name.is_absolute() or '..' in name.parts or ':' in member.filename or (member.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('Ruta insegura en el ZIP')
        size += member.file_size
        if size > 6 * 1024**3 or len(members) > 20000:
            raise ValueError('ZIP excede los límites de extracción')
        if not member.is_dir():
            members.append(member)
    return members


def extract(archive_path, stage, kind, state=None, reference=None):
    stage = Path(stage)
    with zipfile.ZipFile(archive_path) as archive:
        members = safe_members(archive)
        if kind in ('denue', 'cpv'):
            pattern = (rf'denue_inegi_{state}_.*\.csv$' if kind == 'denue' else
                       rf'conjunto_de_datos_ageb_urbana_{state}_cpv2020\.csv$')
            chosen = [m for m in members if re.fullmatch(pattern, PurePosixPath(m.filename).name, re.I)]
            if len(chosen) != 1:
                raise ValueError(f'{kind}: no se encontró un único CSV de datos para {state}')
            outputs = [(chosen[0], stage / PurePosixPath(chosen[0].filename).name)]
        elif kind in ('eic_indicators', 'eic_persons'):
            expected = EIC_INDICATOR_NAME if kind == 'eic_indicators' else f'personas{state}.csv'
            chosen = [m for m in members if PurePosixPath(m.filename).name.lower() == expected]
            if len(chosen) != 1:
                raise ValueError(f'EIC: no se encontró un único {expected}')
            outputs = [(chosen[0], stage / expected)]
        elif kind == 'enoe':
            expected = normalize(reference).replace('_', ' ')
            chosen = [m for m in members if re.search(r'(^|/)Entidades/', m.filename, re.I)
                      and normalize(PurePosixPath(m.filename).stem).split('_entidad_')[-1].replace('_', ' ') == expected
                      and m.filename.lower().endswith('.xls')]
            if len(chosen) != 1:
                raise ValueError('ENOE: no se encontró un único tabulado estatal de la entidad elegida')
            outputs = [(chosen[0], stage / PurePosixPath(chosen[0].filename).name)]
        else:
            # Preserve all components of the two required layers, in one directory.
            chosen = [m for m in members if re.fullmatch(rf'{state}[ma]\.(shp|shx|dbf|prj|cpg)', PurePosixPath(m.filename).name, re.I)]
            names = {PurePosixPath(m.filename).name.lower() for m in chosen}
            if any(f'{state}{layer}.{ext}' not in names for layer in ('m', 'a') for ext in ('shp', 'shx', 'dbf', 'prj')):
                raise ValueError('Marco: componentes incompletos de manzanas/AGEB')
            outputs = [(m, stage / f'marco_2020_{state}' / PurePosixPath(m.filename).name) for m in chosen]
        if len({str(p).lower() for _, p in outputs}) != len(outputs):
            raise ValueError('Nombres de archivo duplicados en el ZIP')
        for member, output in outputs:
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, output.open('wb') as dest:
                shutil.copyfileobj(source, dest)
            if kind in ('denue', 'cpv', 'eic_indicators', 'eic_persons'):
                validate_csv(output, kind)
            elif kind == 'enoe' and output.read_bytes()[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                raise ValueError('ENOE: el archivo no es un libro XLS')
            elif kind == 'marco' and output.suffix.lower() == '.shp':
                with output.open('rb') as source:
                    header = source.read(100)
                if len(header) != 100 or struct.unpack('>i', header[:4])[0] != 9994 or struct.unpack('>i', header[24:28])[0] * 2 != output.stat().st_size:
                    raise ValueError('Marco: shapefile incompleto')
    return [p for _, p in outputs]


def validate_pbf(path):
    def varint(data, pos):
        value, shift = 0, 0
        while pos < len(data) and shift < 64:
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value, pos
            shift += 7
        raise ValueError('Cabecera PBF inválida')
    with Path(path).open('rb') as stream:
        total, blocks = Path(path).stat().st_size, 0
        while stream.tell() < total:
            header_size = stream.read(4)
            if len(header_size) != 4:
                raise ValueError('PBF incompleto')
            length = struct.unpack('>I', header_size)[0]
            if not 0 < length <= 65536:
                raise ValueError('Cabecera PBF inválida')
            header = stream.read(length)
            if len(header) != length or (blocks == 0 and b'OSMHeader' not in header):
                raise ValueError('El proveedor no devolvió un PBF de OpenStreetMap')
            pos, size = 0, None
            while pos < len(header):
                tag, pos = varint(header, pos)
                field, wire = tag >> 3, tag & 7
                if wire == 0:
                    value, pos = varint(header, pos)
                    if field == 3:
                        size = value
                elif wire == 2:
                    value, pos = varint(header, pos)
                    pos += value
                else:
                    raise ValueError('Cabecera PBF desconocida')
            if size is None or not 0 < size <= 32 * 1024**2 or stream.tell() + size > total or pos > len(header):
                raise ValueError('Bloque PBF incompleto')
            stream.seek(size, 1)
            blocks += 1
        if not blocks:
            raise ValueError('PBF vacío')


def publish(stage, folder, kind, records):
    """Preflight the whole source before publishing; never overwrite manual files."""
    folder, stage = Path(folder), Path(stage)
    manifest = read_manifest(folder)
    paths = sorted(p for p in stage.rglob('*') if p.is_file())
    relative_paths = {p.relative_to(stage).as_posix() for p in paths}
    if MANIFEST in relative_paths:
        raise ValueError('La carpeta de publicación contiene un manifiesto; prepara solo los archivos de datos')
    stale = {name: entry for name, entry in manifest['files'].items()
             if entry['kind'] == kind and name not in relative_paths}
    for name, entry in stale.items():
        dest = folder / name
        if dest.exists() and sha256(dest) != entry['sha256']:
            raise ValueError('Fuente gestionada modificada; no se reemplaza: ' + name)
    for source in paths:
        relative = source.relative_to(stage).as_posix()
        dest = folder / relative
        old = manifest['files'].get(relative)
        if dest.exists() and (not old or sha256(dest) != old['sha256']):
            raise ValueError('Archivo manual o modificado; no se sobrescribe: ' + relative)
    for source in paths:
        relative = source.relative_to(stage).as_posix()
        dest = folder / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, dest)
        manifest['files'][relative] = dict(kind=kind, sha256=sha256(dest), sources=records)
    for name in stale:
        (folder / name).unlink(missing_ok=True)
        del manifest['files'][name]
    manifest['updated_at'] = time.time()
    atomic_json(folder / MANIFEST, manifest)
    return [str(folder / p.relative_to(stage)) for p in paths]


def eic_persons_url(state):
    if not isinstance(state, str) or not re.fullmatch(r'(0[1-9]|[12][0-9]|3[0-2])', state):
        raise ValueError('Entidad EIC inválida')
    return INEGI + f'/contenidos/programas/eic/2025/microdatos/eic2025_micro_{state}_csv.zip'


def fine_ce_catalog(client):
    """Walk the official hierarchy only for establishment-compatible sectors."""
    from sb_mexico.historical_transfer import SUPPORTED
    from sb_mexico.ce_controls import activity_codes
    queue = list(SUPPORTED)
    selected, seen = [], set()
    while queue:
        code = queue.pop(0)
        if code in seen:
            continue
        seen.add(code)
        if len(seen)>4096:
            raise ValueError('SAIC: catálogo SCIAN excede el límite de consulta')
        selected.append(code)
        depth = len(code.split('-')[0])
        if depth==6:
            continue
        result,_ = client.json(SAIC+f'acteco/seg/{code}/{depth+1}/6/',ttl=86400)
        if not result.get('success'):
            raise ValueError('SAIC: catálogo de detalle no disponible: '+code)
        _,prefixes = activity_codes(code)
        for row in result.get('list',[]):
            child = str(row['key'])
            parsed,child_prefixes = activity_codes(child)
            if (parsed!=child or any(len(p)<=depth or not p.startswith(prefixes) for p in child_prefixes)):
                raise ValueError('SAIC: jerarquía SCIAN incompatible: '+child)
            queue.append(child)
    return selected


def acquire_source(client, geo, folder, kind, enoe_state=None, enoe_year=2026, enoe_quarter=2, before_publish=None, ce_detail=False):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix='.download-', dir=folder) as temp:
        stage = Path(temp)
        if kind == 'osm':
            path, record = client.fetch(OSM)
            validate_pbf(path)
            shutil.copyfile(path, stage / 'mexico-latest.osm.pbf')
            records.append(record)
        elif kind == 'eic':
            path, record = client.fetch(EIC_INDICATORS)
            extract(path, stage, 'eic_indicators')
            records.append(dict(record, year=2025, role='municipal_indicators'))
            persons = []
            for state in geo['states']:
                path, record = client.fetch(eic_persons_url(state['code']))
                persons.extend(extract(path, stage, 'eic_persons', state=state['code']))
                records.append(dict(record, year=2025, state=state['code'], role='persons'))
            from sb_mexico.demographic_reference import _controls, _people
            municipalities = {m['code'] for m in geo['municipalities']}
            controls = _controls(stage / EIC_INDICATOR_NAME, municipalities)
            people = _people(persons, municipalities)
            for code in sorted(municipalities):
                if (people[code]['population'], people[code]['occupied']) != controls[code]['Valor']:
                    raise ValueError(f'EIC: microdatos incompletos o incompatibles con los controles publicados de {code}')
        elif kind == 'conapo':
            path, record = client.fetch(CONAPO)
            validate_csv(path, kind)
            shutil.copyfile(path, stage / 'pobproy_quinq1.csv')
            records.append(record)
        elif kind == 'enoe':
            reference = next((s for s in geo['states'] if s['code'] == enoe_state), None)
            if reference is None:
                raise ValueError('Elige la entidad de referencia ENOE entre las entidades del BBOX')
            if type(enoe_year) is not int or not 2005 <= enoe_year <= 2100 or type(enoe_quarter) is not int or enoe_quarter not in (1, 2, 3, 4):
                raise ValueError('Periodo ENOE inválido')
            url = INEGI + f'/contenidos/programas/enoe/15ymas/tabulados/enoe_indicadores_estrategicos_{enoe_year}_trim{enoe_quarter}_xls.zip'
            path, record = client.fetch(url)
            extract(path, stage, kind, reference=reference['name'])
            records.append(dict(record, reference_state=enoe_state, year=enoe_year, quarter=enoe_quarter))
        elif kind == 'ce':
            years, _ = client.json(SAIC + 'anios/seg/0/0/6/', ttl=86400)
            sectors, _ = client.json(SAIC + 'acteco/seg/0/2/6/', ttl=86400)
            if not years.get('success') or '2023' not in {r['key'] for r in years['list']} or not sectors.get('success'):
                raise ValueError('SAIC: catálogo de año/sectores no disponible')
            from sb_mexico.ce_controls import read_saic_controls
            fine_codes = fine_ce_catalog(client) if ce_detail else []
            for state in geo['states']:
                municipal, _ = client.json(SAIC + f"ageos/seg/{state['code']}/1/6/", ttl=86400)
                wanted = [m['code'] for m in geo['municipalities'] if m['state'] == state['code']]
                available = {r['key'] for r in municipal.get('list', [])}
                if not municipal.get('success') or not set(wanted) <= available:
                    raise ValueError('SAIC: municipios sin correspondencia en el catálogo censal: ' + ', '.join(sorted(set(wanted) - available)))
                # Small municipal batches avoid unbounded exports even for large maps.
                for start in range(0, len(wanted), 20):
                    geos = wanted[start:start + 20]
                    selections = [('sectores_tamanos',[1,2,3,4,99],[r['key'] for r in sectors['list']]),
                                  ('totales',[0],[r['key'] for r in sectors['list']])]
                    if fine_codes:
                        selections += [('detalle_tamanos',[1,2,3,4,99],fine_codes),('detalle_totales',[0],fine_codes)]
                    for label, strata, activities in selections:
                        query = dict(anios=[2023], ageos=geos, actecos=activities,
                                     varcens=[dict(nom='UE', pos=0), dict(nom='H001A', pos=1)], indicators=[],
                                     stratums=strata, calcs=[], total=True, orden='1', desc=True, page=0, reg=0)
                        count, _ = client.json(SAIC + 'consulta/total/6/', dict(query, varcens=None, calcs=None, orden=None, desc=False))
                        result, record = client.json(SAIC + 'exporta/files/2/6/', query)
                        if not isinstance(result, str) or not count.get('success'):
                            raise ValueError('SAIC: consulta/exportación fallida')
                        output = stage / f"SAIC_{state['code']}_2023_{start // 20:03}_{label}.csv"
                        output.write_bytes(base64.b64decode(result, validate=True).rstrip(b'\x00'))
                        controls = read_saic_controls([output], 2023)
                        if (not controls or len(controls) != count['info'][0]['total']
                                or {v['geography'] for v in controls.values()} - set(geos)
                                or {v['activity_code'] for v in controls.values() if v['activity_code']} - set(activities)):
                            raise ValueError('SAIC: filas, año o cobertura no coinciden con la consulta')
                        records.append(dict(record, reported_rows=len(controls)))
        else:
            links = marco_links(client) if kind == 'marco' else {}
            for state in geo['states']:
                code = state['code']
                if kind == 'denue':
                    url = INEGI + f'/contenidos/masiva/denue/denue_{code}_csv.zip'
                elif kind == 'cpv':
                    url = INEGI + f'/contenidos/programas/ccpv/2020/datosabiertos/ageb_manzana/ageb_mza_urbana_{code}_cpv2020_csv.zip'
                elif kind == 'marco':
                    url = links[code]
                else:
                    raise ValueError('Fuente desconocida')
                path, record = client.fetch(url)
                extract(path, stage, kind, state=code)
                records.append(record)
        if before_publish:
            before_publish(stage)
        return publish(stage, folder, kind, records)


class DownloadManager:
    """One background acquisition at a time, bound to a saved configuration."""
    def __init__(self, root, national, load_config, resolve_config):
        self.root, self.national = Path(root), Path(national)
        self.load_config, self.resolve_config = load_config, resolve_config
        self.cache = self.national / '.source-cache'
        self.lock = threading.Lock()
        self.plans, self.jobs = {}, {}
        self.preparations = {}

    def prepare_async(self, file):
        resolved = self.resolve_config(file)
        task = dict(id=uuid.uuid4().hex, file=resolved, status='running',
                    message='Consultando entidades y municipios del BBOX', created_at=time.time())
        with self.lock:
            for running in self.preparations.values():
                if running['status'] == 'running':
                    if running['file'] == resolved:
                        return json.loads(json.dumps(running))
                    raise ValueError('Hay una preparación de descargas en curso para otro proyecto')
            self.preparations = {key: value for key, value in self.preparations.items()
                                 if time.time() - value['created_at'] < 3600}
            self.preparations[task['id']] = task
        def run():
            try:
                plan = self.prepare(resolved)
                with self.lock:
                    task.update(status='complete', plan=plan)
            except Exception as error:
                with self.lock:
                    task.update(status='error', message=str(error))
        threading.Thread(target=run, daemon=True).start()
        return self.preparation_status(task['id'])

    def preparation_status(self, task_id):
        with self.lock:
            if task_id not in self.preparations:
                raise ValueError('Preparación no encontrada; prepara de nuevo')
            return json.loads(json.dumps(self.preparations[task_id]))

    def conflicts(self, plan, kind):
        from sb_mexico.demand_sources import select_sources
        folder = Path(plan['folder'])
        if kind == 'marco':
            candidates = [p for p in folder.rglob('*') if p.suffix.lower() in ('.shp', '.geojson', '.gpkg')
                          and '.download-' not in str(p)]
        elif kind == 'eic':
            candidates = list(folder.glob('personas[0-9][0-9].csv')) + list(folder.glob(EIC_INDICATOR_NAME))
            ref = self.load_config(plan['file']).get('macroeconomics', {}).get('demographic_reference') or {}
            for name in [ref.get('indicators')] + ref.get('persons', []):
                if name:
                    path = (self.root / name).resolve()
                    if path.is_file():
                        candidates.append(path)
        elif kind == 'osm':
            candidates = list(folder.glob('*.osm.pbf')) + list(self.national.glob('*.osm.pbf'))
        else:
            candidates = [Path(p) for p in select_sources(folder, self.national, kind, plan['exclusions'])]
        conflicts = []
        destination = self.national if kind == 'osm' else folder
        manifest = read_manifest(destination)
        for candidate in candidates:
            if kind != 'eic' and candidate.name.lower() in {n.lower() for n in plan['exclusions']}:
                continue
            try:
                relative = candidate.relative_to(destination).as_posix()
            except ValueError:
                relative = ''
            entry = manifest['files'].get(relative)
            if not entry or entry['kind'] != kind or sha256(candidate) != entry['sha256']:
                conflicts.append(str(candidate))
        # CE exports are complementary tables, unlike overlapping establishment
        # or census microdata. Publication still protects manual name collisions;
        # the combined controls are checked before installing the new bundle.
        return [] if kind == 'ce' else sorted(set(conflicts))

    def prepare(self, file):
        resolved = self.resolve_config(file)
        before = sha256(resolved)
        config = self.load_config(resolved)
        folder = Path(config.get('data_dir') or self.national / Path(resolved).stem.lower())
        if not folder.is_absolute():
            folder = self.root / folder
        folder = folder.resolve()
        if folder == self.national.resolve():
            raise ValueError('Usa una carpeta propia del proyecto; data/ se reserva para fuentes compartidas')
        if folder == self.root.resolve() or folder in self.root.resolve().parents:
            raise ValueError('Usa una carpeta de datos del proyecto, no la raíz del workspace')
        client = Client(self.cache)
        geo = geography(client, config.get('city', {}).get('bbox'))
        plan = dict(id=uuid.uuid4().hex, file=resolved, config_hash=before, folder=str(folder), geography=geo,
                    exclusions=config.get('data_exclusions', []), created_at=time.time(),
                    ce_detail=True)
        if sha256(resolved) != before:
            raise ValueError('El proyecto cambió mientras se preparaban las descargas; prepara de nuevo')
        demand = config.get('demand', {})
        eic_active = ((demand.get('target_year', 2025) == 2025) if demand.get('engine') == 'v2' else
                      (config.get('macroeconomics', {}).get('demographic_reference') or {}).get('mode') == 'eic2025')
        plan['sources'] = [dict(kind=k, label=LABELS[k], conflicts=self.conflicts(plan, k),
                               recommended=(eic_active if k == 'eic' else
                                            demand.get('engine') != 'v2' if k == 'enoe' else
                                            not (eic_active and k == 'conapo')))
                           for k in KINDS]
        with self.lock:
            self.plans = {key: value for key, value in self.plans.items() if time.time() - value['created_at'] < 3600}
            self.plans[plan['id']] = plan
        return plan

    def start(self, plan_id, kinds, enoe_state, enoe_year, enoe_quarter, refresh=False):
        with self.lock:
            plan = self.plans.get(plan_id)
            if not plan or time.time() - plan['created_at'] > 3600:
                raise ValueError('Prepara de nuevo las descargas de este proyecto')
            if any(j['status'] == 'running' for j in self.jobs.values()):
                raise ValueError('Ya hay una descarga en curso; espera a que termine')
            if not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds) or any(k not in KINDS for k in kinds):
                raise ValueError('Selecciona fuentes válidas para descargar')
            if sha256(plan['file']) != plan['config_hash']:
                raise ValueError('El proyecto cambió; prepara las descargas de nuevo')
            for kind in kinds:
                conflicts = self.conflicts(plan, kind)
                if conflicts:
                    raise ValueError('Excluye las fuentes anteriores o conserva su descarga manual antes de descargar ' + kind + ': ' + ', '.join(conflicts))
            if 'enoe' in kinds and enoe_state not in {s['code'] for s in plan['geography']['states']}:
                raise ValueError('Elige la entidad de referencia ENOE')
            if 'enoe' in kinds and (type(enoe_year) is not int or not 2005 <= enoe_year <= 2100
                                   or type(enoe_quarter) is not int or enoe_quarter not in (1, 2, 3, 4)):
                raise ValueError('Periodo ENOE inválido')
            job = dict(id=uuid.uuid4().hex, file=plan['file'], folder=plan['folder'], status='running',
                       message='Preparando descargas', results=[], started_at=time.time())
            self.jobs = {key: value for key, value in self.jobs.items() if time.time() - value['started_at'] < 86400}
            self.jobs[job['id']] = job
        def update(message):
            with self.lock:
                job['message'] = message
        def run():
            try:
                client = Client(self.cache, update, refresh)
                for kind in kinds:
                    update('Preparando ' + LABELS[kind])
                    try:
                        if sha256(plan['file']) != plan['config_hash']:
                            raise ValueError('El proyecto cambió durante la descarga; se detuvo la adquisición')
                        destination = self.national if kind == 'osm' else Path(plan['folder'])
                        # Reject manually excluded managed names rather than claiming ready inputs.
                        manifest = read_manifest(destination)
                        if any(Path(n).name.lower() in {e.lower() for e in plan['exclusions']}
                               for n, entry in manifest['files'].items() if entry['kind'] == kind):
                            raise ValueError('Hay archivos gestionados excluidos; restáuralos antes de actualizar esta fuente')
                        def before_publish(stage):
                            if sha256(plan['file']) != plan['config_hash']:
                                raise ValueError('El proyecto cambió durante la descarga; prepara de nuevo')
                            if self.conflicts(plan, kind):
                                raise ValueError('Las fuentes locales cambiaron durante la descarga; prepara de nuevo')
                            excluded = {e.lower() for e in plan['exclusions']}
                            if any(p.name.lower() in excluded for p in stage.rglob('*') if p.is_file()):
                                raise ValueError('Un archivo descargado está excluido; revisa las exclusiones')
                            if kind == 'ce':
                                from sb_mexico.demand_sources import select_sources
                                from sb_mexico.ce_controls import read_saic_controls
                                # Managed tables are replaced together. Other CE
                                # files remain on disk and may coexist only when
                                # their overlapping controls agree with the export.
                                managed = read_manifest(destination)['files']
                                replaced = {(destination/n).resolve() for n,entry in managed.items()
                                            if entry['kind']=='ce' and (destination/n).is_file()
                                            and sha256(destination/n)==entry['sha256']}
                                retained = [p for p in select_sources(destination, self.national, 'ce', plan['exclusions'])
                                            if Path(p).resolve() not in replaced]
                                controls = read_saic_controls(retained + [str(p) for p in stage.glob('*.csv')])
                                from sb_mexico.fine_workplace import supported
                                if {r['reference_year'] for r in controls.values()
                                    if r['geography_level']=='municipality' and supported(r)} - {2023}:
                                    raise ValueError('CE: los archivos existentes mezclan años incompatibles con la referencia 2023')
                        options = dict(ce_detail=True) if kind=='ce' and plan.get('ce_detail') else {}
                        paths = acquire_source(client, plan['geography'], destination, kind,
                                               enoe_state, enoe_year, enoe_quarter, before_publish, **options)
                        result = dict(kind=kind, status='ok', files=paths)
                        if kind == 'eic':
                            def config_path(path):
                                path = Path(path).resolve()
                                return path.relative_to(self.root.resolve()).as_posix() if path.is_relative_to(self.root.resolve()) else str(path)
                            result['demographic_reference'] = dict(mode='eic2025',
                                indicators=config_path(destination / EIC_INDICATOR_NAME),
                                persons=[config_path(destination / f"personas{s['code']}.csv") for s in plan['geography']['states']])
                        if kind == 'enoe':
                            try:
                                from sb_mexico.inegi import parse_enoe_indicators
                                result['indicators'] = parse_enoe_indicators(paths[0])
                            except Exception as error:
                                result['warnings'] = ['XLS descargado; no se pudieron leer sus tasas: ' + str(error)]
                    except Exception as error:
                        result = dict(kind=kind, status='error', message=str(error))
                    with self.lock:
                        job['results'].append(result)
                with self.lock:
                    job['status'] = 'complete' if all(r['status'] == 'ok' for r in job['results']) else 'partial'
                    job['message'] = 'Descargas terminadas; revisa los resultados y la vista previa'
            except Exception as error:
                with self.lock:
                    job.update(status='error', message=str(error))
        threading.Thread(target=run, daemon=True).start()
        return self.status(job['id'])

    def status(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError('Descarga no encontrada; el servidor pudo reiniciarse')
            return json.loads(json.dumps(self.jobs[job_id]))
