"""One-click acquisition, migration and layout; computation never edits the city."""
import copy
import time
from pathlib import Path

from .toponymy_layout import ENGINE, plan_labels


def prepare_labels(city_file, progress=None, refresh=False, download=True):
    from .toponymy import _toponymy_context
    from .toponymy_official import acquire_catalogs, acquire_dcah, scan_official_catalog
    from .place_identity import merge_official_places
    progress = progress or (lambda message: None)
    _, project, config, bbox = _toponymy_context(city_file)
    progress('Comprobando fuentes locales de nombres')
    catalog = scan_official_catalog(city_file, progress)
    acquisition_errors = []
    acquired_any = False
    for kind, acquire in [('localities', acquire_catalogs), ('dcah', acquire_dcah)]:
        sources = [s for s in catalog['sources'] if s['kind'] == kind]
        usable = [s for s in sources if s['status'] in ('ok', 'partial') and s.get('labels') and
                  not ('creció' in s.get('message', ''))]
        stale = usable and any(time.time() - Path(s['path']).stat().st_mtime > 7 * 86400 for s in usable)
        # Explicit user imports have no acquisition sidecar and are not replaced.
        automatic_stale = stale and any('provenance' in s for s in usable)
        if refresh or not usable or automatic_stale:
            if not download:
                acquisition_errors.append(dict(kind=kind, message='Verificación local: no se actualizaron fuentes ausentes o antiguas.'))
                continue
            try:
                progress('Preparando ' + kind)
                acquired = acquire(city_file, progress, refresh=refresh)
                acquired_any = acquired_any or bool(acquired.get('files'))
                acquisition_errors.extend(acquired.get('errors', []))
            except (OSError, ValueError, RuntimeError) as error:
                acquisition_errors.append(dict(kind=kind, message=str(error)))
    if acquired_any:
        catalog = scan_official_catalog(city_file, progress)
    if not catalog['candidates']:
        raise ValueError('No hay fuentes oficiales utilizables. Se conserva la toponimia anterior. ' +
                         '; '.join(catalog['warnings']))
    places, exceptions = merge_official_places(config.get('places', []), catalog['candidates'],
                                              config.get('deleted_places', []))
    pipeline = copy.deepcopy(config.get('toponymy') or {})
    pipeline.update(engine='official_v1', osm_fallback=False)
    pipeline['layout'] = dict(engine=ENGINE, profile='balanced', periphery='core_only')
    progress('Calculando importancia, tamaños y visibilidad por zoom')
    layout = plan_labels(places, bbox, config['city'].get('urban_core_polygon'), pipeline)
    for conflict in catalog['conflicts']:
        exceptions.append(dict(conflict, kind='source_conflict',
            message='Ediciones oficiales contradictorias; no se incorpora esta clave.'))
    for missing in catalog['unlocated']:
        exceptions.append(dict(missing, id='unlocated_' + missing['code'], kind='unlocated',
            message='Nombre sin polígono disponible; no se inventa una ubicación.'))
    warnings = catalog['warnings'] + [str(e.get('message', e)) for e in acquisition_errors]
    from .place_identity import serialize_places
    old_ids = {p['id'] for p in serialize_places(config.get('places', []))}
    summary = dict(layout['summary'], added=sum(p['id'] not in old_ids for p in places),
                   preserved=len(config.get('places', [])), exceptions=len(exceptions))
    previous_review = (config.get('toponymy') or {}).get('review') or {}
    ids = {p['id'] for p in places}
    for exception in previous_review.get('exceptions', []):
        if (exception.get('kind') == 'ambiguous_identity' and exception.get('candidate_id') in ids and
                all(i in ids for i in exception.get('existing_ids', [])) and
                exception['id'] not in {e['id'] for e in exceptions}):
            exceptions.append(exception)
    resolved = previous_review.get('resolved', [])
    summary['exceptions'] = sum(e['id'] not in resolved for e in exceptions)
    pipeline['review'] = dict(exceptions=exceptions, resolved=resolved, warnings=warnings, sources=catalog['sources'])
    return dict(places=places, toponymy=pipeline, toponymy_mode='replace',
                deleted_places=config.get('deleted_places', []), layout=layout, summary=summary,
                exceptions=exceptions, warnings=warnings, partial=bool(warnings), project=str(project))
