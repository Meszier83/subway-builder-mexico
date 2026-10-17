"""Explicit label policy shared by the cartographic export and depot patch."""
import math

from sb_mexico.place_identity import place_id, same_place, serialize_places

LAYER_TYPES = {
    'cities': {'city', 'town', 'borough'},
    'suburbs': {'suburb', 'quarter', 'village', 'town', 'city'},
    'neighborhoods': {'neighbourhood', 'neighborhood', 'subdivision', 'hamlet', 'locality', 'suburb', 'quarter'},
}


def curated_collection(places, deleted=(), mode='replace', pipeline=None, bbox=None, core=None):
    if mode not in ('merge', 'replace'):
        raise ValueError('toponymy_mode must be merge or replace')
    features = []
    for place in serialize_places(places or []):
        loc = place.get('loc')
        if not place.get('name') or not isinstance(loc, (list, tuple)) or len(loc) != 2:
            continue
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in loc):
            continue
        # Retain boundaries in the review catalog/YAML, not in point-tile properties.
        props = {k: v for k, v in place.items() if k != 'boundary'}
        scale = (place.get('label_overrides') or {}).get('scale')
        if scale:
            props['territorial_type'] = props.get('type')
            props['type'] = {'large': 'city', 'medium': 'suburb', 'small': 'neighbourhood'}[scale]
        features.append(dict(type='Feature', properties=dict(props, place=place.get('type', 'suburb'), curated=True),
                             geometry=dict(type='Point', coordinates=list(loc))))
    result = dict(type='FeatureCollection', features=features, mode=mode, deleted_places=list(deleted or []))
    if pipeline:
        from .toponymy_official import options
        options({'toponymy': pipeline})
        result.update(pipeline=pipeline.get('engine', 'official_v1'), authoritative=True)
    from .toponymy_layout import enabled, plan_labels
    if enabled(pipeline):
        # Automatic presentation always materializes the complete saved catalog.
        # Complementing native labels would bypass identity and density decisions.
        if mode != 'replace':
            raise ValueError('label_v1 requiere usar la lista guardada (replace).')
        filtered = [p for p in serialize_places(places or []) if not any(
            str(p.get('name', '')).strip().casefold() == d.strip().casefold() if isinstance(d, str)
            else same_place(p, d) for d in deleted)]
        plan = plan_labels(filtered, bbox, core, pipeline)
        result.update(layout=plan, mode='replace')
        result['features'] = []
        for place in filtered:
            decision = plan['decisions'][place['id']]
            if not decision['visible']:
                continue
            result['features'].append(dict(type='Feature',
                tippecanoe=dict(minzoom=decision['min_zoom'], maxzoom=plan['maxzoom'], layer=decision['layer']),
                properties=dict(name=place['name'], id=place['id'], priority=decision['priority']),
                geometry=dict(type='Point', coordinates=list(place['loc']))))
    return result


def _place(feature):
    props = feature.get('properties') or {}
    result = dict(props, type=props.get('type') or props.get('place', 'suburb'),
                  loc=(feature.get('geometry') or {}).get('coordinates'))
    if not result.get('id') and result.get('loc'):
        result['id'] = place_id(result)
    return result


def _matches(native, curated):
    return same_place(native, curated) or bool(curated.get('original_name') and same_place(
        native, dict(curated, id=None, name=curated['original_name'])))


def synchronize_labels(native, collection, layer):
    """Complement incomplete lists; strict replacement remains the legacy default.

    Tombstones are spatial identities. Legacy string tombstones retain their historical
    global-name meaning, but new removals never hide a remote namesake.
    """
    mode = collection.get('mode', 'replace')
    if mode not in ('merge', 'replace'):
        raise ValueError('Unknown toponymy mode')
    allowed = LAYER_TYPES.get(layer, set())
    deleted = collection.get('deleted_places', [])
    legacy = {str(n).strip().casefold() for n in collection.get('deleted_names', [])}
    legacy.update(str(n).strip().casefold() for n in deleted if isinstance(n, str))

    def discarded(place):
        return str(place.get('name', '')).strip().casefold() in legacy or any(
            _matches(place, item) for item in deleted if isinstance(item, dict))

    curated = [f for f in collection.get('features', [])
               if (f.get('geometry') or {}).get('type') == 'Point'
               and _place(f).get('name') and _place(f)['type'] in allowed and not discarded(_place(f))]
    retained = [f for f in native if not discarded(_place(f))]
    if mode == 'replace' and (curated or collection.get('authoritative')):
        return curated
    # Curated IDs/names/coordinates win locally, without suppressing distant homonyms.
    return curated + [f for f in retained if not any(_matches(_place(f), _place(c)) for c in curated)]
