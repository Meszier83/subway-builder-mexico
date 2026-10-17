"""Deterministic territorial label layout, shared by preview and tile delivery."""
import hashlib
import json
import math
from collections import Counter, defaultdict

from shapely.geometry import Point, Polygon, box, shape
from shapely.ops import unary_union

from .place_identity import serialize_places

ENGINE = 'label_v1'
VERSION = 1
FONTS = {'large': 17, 'medium': 13, 'small': 10.5}
LAYERS = {'large': 'city_labels', 'medium': 'suburb_labels', 'small': 'neighborhood_labels'}


def enabled(pipeline):
    return bool(pipeline and (pipeline.get('layout') or {}).get('engine') == ENGINE)


def validate_options(pipeline):
    layout = pipeline.get('layout') or {}
    if not isinstance(layout, dict) or layout.get('engine', ENGINE) != ENGINE:
        raise ValueError('El motor de etiquetas debe ser label_v1.')
    if layout.get('profile', 'balanced') != 'balanced':
        raise ValueError('El perfil de etiquetas debe ser balanced.')
    if layout.get('periphery', 'core_only') != 'core_only':
        raise ValueError('Las etiquetas automáticas deben respetar el núcleo urbano.')


def polygon(value):
    """Accept the Wizard contour and GeoJSON, including holes and multipolygons."""
    if not value:
        return None
    if isinstance(value, list):
        geometry = Polygon(value)
    elif value.get('type') == 'FeatureCollection':
        geometry = unary_union([shape(f['geometry']) for f in value.get('features', [])])
    else:
        geometry = shape(value['geometry'] if value.get('type') == 'Feature' else value)
    if geometry.is_empty or geometry.geom_type not in ('Polygon', 'MultiPolygon') or not geometry.is_valid:
        raise ValueError('El núcleo urbano debe ser un polígono válido; no se sustituirá por una caja.')
    return geometry


def population(place):
    value = place.get('population')
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def presentation(place):
    ptype = place.get('official_type') or place.get('type') or ''
    kind = place.get('feature_kind')
    local = kind == 'locality' or (not kind and ptype in ('city', 'town', 'borough', 'village', 'hamlet', 'locality'))
    urban = place.get('is_urban', ptype in ('city', 'town', 'borough'))
    pop = population(place)
    category = str(place.get('category') or place.get('tipo_asent') or '').upper()
    if local and pop == 0 or place.get('is_micro') or category in ('PRIVADA', 'COTO'):
        return 'small', 15, 1, 'Detalle local'
    if local and urban and pop is not None and pop >= 100000:
        return 'large', 6, 6, 'Localidad urbana principal'
    if local and urban:
        return 'medium', 10, 5, 'Localidad urbana'
    if local and pop is not None and pop >= 2500:
        return 'medium', 11, 4, 'Localidad rural habitada'
    if not local and (category in ('REGIÓN', 'REGION', 'SECTOR', 'SUPERMANZANA') or
                      ptype in ('suburb', 'quarter')):
        return 'medium', 12, 3, 'Referencia urbana'
    return 'small', 14, 2, 'Localidad o colonia' if pop is not None else 'Jerarquía territorial; población no disponible'


def mercator(loc, zoom):
    lon, lat = loc
    lat = max(-85.05112878, min(85.05112878, lat))
    size = 256 * 2 ** zoom
    return ((lon + 180) / 360 * size,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size)


def dimensions(name, scale):
    font = FONTS[scale]
    return [round(max(font, len(name) * font * .65) + 8, 2), round(font * 1.3 + 8, 2)]


def _bounds(loc, zoom, size):
    x, y = mercator(loc, zoom)
    return [x - size[0]/2, y - size[1]/2, x + size[0]/2, y + size[1]/2]


def _cells(rect):
    return [(x, y) for x in range(math.floor(rect[0]/128), math.floor(rect[2]/128)+1)
            for y in range(math.floor(rect[1]/128), math.floor(rect[3]/128)+1)]


def plan_labels(places, bbox, core=None, pipeline=None, maxzoom=15):
    validate_options(pipeline or {})
    if not isinstance(maxzoom, int) or not 6 <= maxzoom <= 24:
        raise ValueError('Zoom máximo de etiquetas inválido.')
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4 or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in bbox) or not (
            -180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
        raise ValueError('Área del mapa inválida.')
    catalog = sorted(serialize_places(places), key=lambda p: p['id'])
    if len({p['id'] for p in catalog}) != len(catalog):
        raise ValueError('IDs repetidos en el catálogo de etiquetas.')
    area = box(*bbox)
    nucleus = polygon(core)
    decisions = {}
    for place in catalog:
        scale, initial, tier, reason = presentation(place)
        overrides = place.get('label_overrides') or {}
        if not isinstance(overrides, dict) or set(overrides) - {'scale', 'visible', 'priority'}:
            raise ValueError('Excepción de etiqueta inválida: ' + place['id'])
        scale = overrides.get('scale', scale)
        if scale not in FONTS or ('visible' in overrides and not isinstance(overrides['visible'], bool)):
            raise ValueError('Tamaño o visibilidad de etiqueta inválidos.')
        manual = overrides.get('priority', 0)
        if isinstance(manual, bool) or not isinstance(manual, (float, int)) or not math.isfinite(manual) or not -100 <= manual <= 100:
            raise ValueError('Prioridad manual fuera de rango (-100 a 100).')
        loc = place.get('loc')
        valid = bool(isinstance(loc, (list, tuple)) and len(loc) == 2 and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in loc) and
            -180 <= loc[0] <= 180 and -90 <= loc[1] <= 90 and str(place.get('name', '')).strip())
        visible = valid
        if not valid:
            reason = 'Nombre o coordenadas inválidos'
        elif not area.covers(Point(loc)):
            visible, reason = False, 'Fuera del área del mapa'
        elif nucleus is not None and not nucleus.covers(Point(loc)):
            visible, reason = False, 'Fuera del núcleo'
        elif overrides.get('visible') is False:
            visible, reason = False, 'Oculta manualmente'
        surface = 0
        if place.get('boundary') and place.get('feature_kind') == 'settlement':
            geometry = shape(place['boundary'])
            # Only a relative within-kind tie breaker; never infer inhabitants.
            surface = geometry.area if geometry.is_valid else 0
        priority = round(manual * 1000000000 + tier * 10000000 +
            min(population(place) or 0, 9999999) + min(surface * 1000000, 999), 6)
        decisions[place['id']] = dict(id=place['id'], scale=scale, priority=priority,
            base_zoom=min(initial, maxzoom), min_zoom=None, visible=visible, reason=reason,
            size=dimensions(str(place.get('name', '')), scale), layer=LAYERS[scale])
    ordered = sorted([p for p in catalog if decisions[p['id']]['visible']],
                     key=lambda p: (-decisions[p['id']]['priority'], p['id']))
    for zoom in range(6, maxzoom+1):
        grid = defaultdict(list)
        for place in ordered:
            decision = decisions[place['id']]
            if decision['base_zoom'] > zoom:
                continue
            rect = _bounds(place['loc'], zoom, decision['size'])
            cells = _cells(rect)
            collision = any(rect[0] < r[2] and rect[2] > r[0] and rect[1] < r[3] and rect[3] > r[1]
                            for cell in cells for r in grid[cell])
            if decision['min_zoom'] is not None or not collision or zoom == maxzoom:
                if decision['min_zoom'] is None:
                    decision['min_zoom'] = zoom
                    if zoom > decision['base_zoom']:
                        decision['reason'] += ' · Diferida por densidad'
                for cell in cells:
                    grid[cell].append(rect)
    summary = dict(total=len(catalog), eligible=len(ordered), hidden=len(catalog)-len(ordered),
        deferred=sum(d['visible'] and d['min_zoom'] > d['base_zoom'] for d in decisions.values()),
        by_scale=dict(Counter(d['scale'] for d in decisions.values() if d['visible'])))
    signature = dict(engine=ENGINE, version=VERSION, places=catalog, bbox=bbox, core=core,
                     pipeline=pipeline, maxzoom=maxzoom)
    return dict(engine=ENGINE, version=VERSION, maxzoom=maxzoom, decisions=decisions, summary=summary,
        fingerprint=hashlib.sha256(json.dumps(signature, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
