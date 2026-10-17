"""Explicit MapGen adapter: planned labels never pass through legacy heuristics."""
import json
import os
from pathlib import Path
import shutil
from functools import partial

try:
    from depot.maps import MapGen as _DepotMapGen
except ImportError:
    _DepotMapGen = None

LAYERS = ('city_labels', 'suburb_labels', 'neighborhood_labels')


def compile_labels(collection, output, run):
    layout = collection.get('layout') or {}
    if layout.get('engine') != 'label_v1' or layout.get('version') != 1:
        raise ValueError('Plan de etiquetas ausente o incompatible.')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    source = output.with_suffix('.geojson')
    features = collection.get('features', [])
    if not features:
        return None
    for feature in features:
        options = feature.get('tippecanoe') or {}
        if options.get('layer') not in LAYERS or not 6 <= options.get('minzoom', 0) <= layout['maxzoom']:
            raise ValueError('Etiqueta sin capa o zoom válido.')
    source.write_text(json.dumps(dict(type='FeatureCollection', features=features), ensure_ascii=False), encoding='utf-8')
    run(['tippecanoe', '-Z', '6', '-z', str(layout['maxzoom']), '-r', '1',
         '-y', 'name', '-y', 'id', '-y', 'priority', '--no-tile-size-limit', '--no-feature-limit',
         '--force', '-o', str(output), str(source)])
    if not output.is_file():
        raise RuntimeError('Tippecanoe no produjo las teselas de etiquetas.')
    return output


def planned_mapgen(base, collection):
    """Return the legacy class unchanged unless the new layout was activated."""
    if not collection or not collection.get('layout'):
        return base
    required = ('add_labels', '_run_command', '_update_mbtiles_metadata', 'run_all')
    missing = [name for name in required if not callable(getattr(base, name, None))]
    if missing:
        raise RuntimeError('Compilador incompatible con label_v1: ' + ', '.join(missing))
    for name in ('tippecanoe', 'tile-join', 'pmtiles'):
        if not shutil.which(name):
            raise RuntimeError('Compilador de etiquetas no disponible: ' + name)

    # The installed engine submits bound methods to ProcessPoolExecutor. Its
    # subclass must have a module-level identity, including in fresh workers.
    cls = PlannedMapGen if base is _DepotMapGen else type('FixturePlannedMapGen', (PlannedLabelStage, base), {})
    return partial(cls, label_collection=collection)


class PlannedLabelStage:
    def __init__(self, *args, label_collection, **kwargs):
        self._label_collection = label_collection
        super().__init__(*args, **kwargs)

    def add_labels(self):
        collection = self._label_collection
        if self.maxzoom != collection['layout']['maxzoom']:
            raise RuntimeError('El zoom del compilador difiere del plan de etiquetas.')
        prefix = Path(self.city_dir) / self.city
        base_tiles = Path(str(prefix) + '-nolabels.pmtiles')
        if not base_tiles.is_file():
            raise RuntimeError('Falta la cartografía base de esta compilación.')
        final = prefix.with_suffix('.pmtiles')
        labels = compile_labels(collection, str(prefix) + '-onlylabels.pmtiles', self._run_command)
        temporary = Path(str(prefix) + '-planned.pmtiles')
        if labels:
            mbtiles = Path(str(prefix) + '-planned.mbtiles')
            self._run_command(['tile-join', '-o', str(mbtiles), str(base_tiles), str(labels),
                               '--no-tile-size-limit', '--force'])
            self._update_mbtiles_metadata(str(mbtiles))
            self._run_command(['pmtiles', 'convert', str(mbtiles), str(temporary)])
        else:
            shutil.copyfile(base_tiles, temporary)
        if not temporary.is_file():
            raise RuntimeError('No se produjo el mapa con etiquetas preparadas.')
        os.replace(temporary, final)
        (Path(self.city_dir) / 'toponymy-layout.json').write_text(
            json.dumps(collection['layout'], ensure_ascii=False, indent=2), encoding='utf-8')
        if getattr(self, 'verb', False):
            print(f"[label_v1] {len(collection['features'])} etiquetas; {final}")


if _DepotMapGen is not None:
    class PlannedMapGen(PlannedLabelStage, _DepotMapGen):
        """Importable by the installed MapGen's multiprocessing workers."""
else:
    PlannedMapGen = None
