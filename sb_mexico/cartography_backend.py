"""Importable building/label adapters for depot multiprocessing workers."""
import os
from importlib.metadata import version
from functools import partial
from .building_pipeline import BoundedBuildingStage
from .toponymy_tiles import PlannedLabelStage, _DepotMapGen, planned_mapgen

if _DepotMapGen is not None:
    class BoundedMapGen(BoundedBuildingStage, _DepotMapGen):
        pass

    class BoundedPlannedMapGen(PlannedLabelStage, BoundedBuildingStage, _DepotMapGen):
        pass


def mapgen_for_build(base, collection):
    backend = os.environ.get('SB_BUILDINGS_BACKEND', 'bounded')
    if backend == 'legacy':
        return planned_mapgen(base, collection)
    if backend != 'bounded':
        raise ValueError('SB_BUILDINGS_BACKEND must be bounded or legacy')
    if base is not _DepotMapGen:
        raise RuntimeError('Bounded buildings require the installed depot MapGen class')
    if version('depot') != '1.2.7':
        raise RuntimeError('Bounded buildings support depot 1.2.7; select legacy explicitly for another version')
    for name in ('_get_latest_overture_release', '_calculate_building_foundation', '_generate_building_tiles'):
        if not callable(getattr(base, name, None)):
            raise RuntimeError(f'Unsupported depot backend: missing {name}')
    if collection and collection.get('layout'):
        planned_mapgen(base, collection)  # Retain label-tool compatibility checks.
        return partial(BoundedPlannedMapGen, label_collection=collection)
    return BoundedMapGen
