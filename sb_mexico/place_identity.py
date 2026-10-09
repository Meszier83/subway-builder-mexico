"""Territorial identities shared by toponymy scans, imports and duplicate review."""
import hashlib
import math
import re
import unicodedata
from collections import defaultdict
from functools import lru_cache


@lru_cache(maxsize=40000)
def place_name_key(name):
    # Fold vowel accents but preserve ñ: Peña and Pea are different names.
    text = unicodedata.normalize('NFC', str(name)).casefold().replace('ñ', '\u0001')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = text.replace('\u0001', 'ñ')
    text = re.sub(r'^(?:super\s*manzana|s\.?\s*m\.?)(?=\s|\d|$)\s*', 'supermanzana ', text)
    text = re.sub(r'^(?:region|reg\.?)(?=\s|\d|$)\s*', 'region ', text)
    text = re.sub(r'^(?:colonia|col\.?|fraccionamiento|fracc\.?)\s+', '', text)
    if re.fullmatch(r'\d+[a-z]?', text.strip()):
        text = 'supermanzana ' + text
    return ''.join(c for c in text if c.isalnum())


def distance_m(a, b):
    try:
        x1, y1 = map(float, a['loc'])
        x2, y2 = map(float, b['loc'])
        values = (x1, y1, x2, y2)
        if not all(math.isfinite(v) for v in values):
            return math.inf
        dy = math.radians(y2 - y1)
        dx = math.radians(x2 - x1)
        h = math.sin(dy / 2) ** 2 + math.cos(math.radians(y1)) * math.cos(math.radians(y2)) * math.sin(dx / 2) ** 2
        return 12742000 * math.asin(min(1, math.sqrt(h)))
    except (KeyError, TypeError, ValueError):
        return math.inf


def same_territory(a, b):
    return all(not a.get(k) or not b.get(k) or str(a[k]) == str(b[k])
               for k in ('cve_ent', 'cve_mun', 'cve_loc'))


def same_place(a, b, radius_m=None):
    if not same_territory(a, b):
        return False
    if a.get('id') and a.get('id') == b.get('id'):
        return True
    if radius_m is None:
        radius_m = 7500 if a.get('type') in ('city', 'town') and b.get('type') in ('city', 'town') else 500
    a_keys = {place_name_key(a.get(k, '')) for k in ('name', 'original_name')} - {''}
    b_keys = {place_name_key(b.get(k, '')) for k in ('name', 'original_name')} - {''}
    return (bool(a_keys & b_keys) and
            distance_m(a, b) <= radius_m)


def place_id(place):
    if place.get('id'):
        return place['id']
    payload = '|'.join([place_name_key(place.get('name', ''))] +
                       [str(place.get(k, '')) for k in ('cve_ent', 'cve_mun', 'cve_loc')] +
                       [f'{float(x):.4f}' for x in place['loc']])
    return 'place_' + hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]


def serialize_places(places):
    """Retain provenance and manual edits; never upgrade unknown origin to curated."""
    result = []
    for item in places or []:
        place = dict(item)
        place.setdefault('source', 'UNKNOWN')
        place['id'] = place_id(place)
        result.append(place)
    return result


def merge_places(existing, candidates, deleted=()):
    result = serialize_places(existing)
    buckets, identities = defaultdict(list), {}
    for place in result:
        buckets[place_name_key(place.get('name', ''))].append(place)
        if place.get('original_name'):
            buckets[place_name_key(place['original_name'])].append(place)
        identities[place['id']] = place
    deleted_ids = {p['id'] for p in deleted if isinstance(p, dict) and p.get('id')}
    deleted_names = {place_name_key(p) for p in deleted if isinstance(p, str)}
    for candidate in candidates:
        if candidate.get('id') in deleted_ids or place_name_key(candidate.get('name', '')) in deleted_names:
            continue
        if any(isinstance(p, dict) and same_place(p, candidate) for p in deleted):
            continue
        choices = list(buckets[place_name_key(candidate.get('name', ''))])
        if candidate.get('id') in identities:
            choices.insert(0, identities[candidate['id']])
        old = next((p for p in choices if same_place(p, candidate)), None)
        if old is None:
            place = dict(candidate, id=place_id(candidate))
            result.append(place)
            buckets[place_name_key(place.get('name', ''))].append(place)
            identities[place['id']] = place
        else:
            # Geometry, spelling, hierarchy and IDs belong to the existing edit.
            for key in candidate:
                if key not in ('id', 'name', 'loc', 'type', 'source') and key not in old:
                    old[key] = candidate[key]
            old['establishments'] = max(old.get('establishments', 0) or 0, candidate.get('establishments', 0) or 0)
            sources = set(old.get('source_files', [])) | set(candidate.get('source_files', []))
            if sources:
                old['source_files'] = sorted(sources)
            if old.get('source') == 'UNKNOWN' and candidate.get('source'):
                old['matched_source'] = candidate['source']
    return result
