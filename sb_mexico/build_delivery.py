"""Build-specific Wizard artifacts; CLI's path-returning API stays compatible."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid
import zipfile

import yaml


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def config_hash(path):
    config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_manifest(root, result):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / 'wizard-build.json.tmp'
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, root / 'wizard-build.json')


def validate_package(path, code):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        required = {'config.json', 'demand_data.json', f'{code}.pmtiles', 'roads.geojson'}
        if len(names) != len(set(names)) or not required <= set(names) or archive.testzip():
            raise ValueError('Invalid or incomplete map package')
        for name in names:
            if Path(name).name != name:
                raise ValueError('Package members must be at the archive root')
            artifact = Path(path).parent / name
            digest = hashlib.sha256()
            with archive.open(name) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if not artifact.is_file() or digest.hexdigest() != file_hash(artifact):
                raise ValueError('Package does not match this build output')
        demand = json.loads(archive.read('demand_data.json'))
        points = {point['id']: point for point in demand['points']}
        pops = {pop['id']: pop for pop in demand['pops']}
        if len(points) != len(demand['points']) or len(pops) != len(demand['pops']):
            raise ValueError('Duplicate demand IDs')
        membership = {key: set() for key in points}
        for pop in pops.values():
            if not isinstance(pop['size'], int) or pop['size'] <= 0:
                raise ValueError('Invalid cohort size')
            for field in ('residenceId', 'jobId'):
                if pop[field] not in points:
                    raise ValueError('Broken cohort reference')
                membership[pop[field]].add(pop['id'])
        for key, point in points.items():
            if set(point['popIds']) != membership[key]:
                raise ValueError('Broken point membership')
        commuters = sum(pop['size'] for pop in pops.values())
        allocation_path = Path(path).parent / 'od_allocation_report.json'
        if allocation_path.exists():
            allocation = json.loads(allocation_path.read_text(encoding='utf-8'))
            if allocation.get('mode') == 'balanced_integer_v1':
                from .od_allocation import validate_integer_margins
                validate_integer_margins(demand['pops'], allocation)
        report = Path(path).parent / 'demand_pipeline_report.json'
        if report.exists() and json.loads(report.read_text(encoding='utf-8'))['commuters'] != commuters:
            raise ValueError('Package commuter total differs from the build report')
        return commuters


def execute_wizard_build(config_path, output_dir, data_dir, skip_map=False):
    from sb_mexico.pipeline import execute_pipeline
    config_path = Path(config_path).resolve()
    root = Path(output_dir).resolve()
    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    code = config['city']['code']
    build_id = uuid.uuid4().hex
    stage = root / 'wizard-builds' / build_id
    stage.mkdir(parents=True)
    result = dict(build_id=build_id, config_path=str(config_path),
                  config_hash=config_hash(config_path), output_dir=str(stage),
                  status='running', package_path=None, package_hash=None,
                  missing_map_files=[])
    map_keys = {'code', 'bbox', 'building_filter_size', 'building_simplification',
                'include_ocean', 'urban_parks_only', 'urban_core_polygon',
                'lod_peripheral_roads', 'include_pedestrian_paths',
                'lod_peripheral_labels', 'lod_peripheral_buildings'}
    result['cartography_config'] = {key: value for key, value in config['city'].items()
                                    if key in map_keys}
    result['cartography_config']['places'] = config.get('places', [])
    result['cartography_config']['data_dir'] = str(Path(data_dir).resolve())
    previous = None
    manifest = root / 'wizard-build.json'
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    write_manifest(root, result)
    try:
        if skip_map:
            # Reuse only this project's cartography. Record provenance separately
            # from demand/package identity; asset presence alone does not prove age.
            asset_root = Path(previous['output_dir']) if previous and previous.get('status') == 'success' else root
            if previous and (previous.get('cartography_config') != result['cartography_config']
                             or previous.get('config_path') != str(config_path)):
                asset_root = stage  # Changed BBOX/map settings require fresh cartography.
            assets = [f'{code}.pmtiles', 'roads.geojson', 'buildings_index.bin.gz',
                      'runways_taxiways.geojson', 'ocean_depth_index.json.gz']
            result['reused_cartography'] = {}
            for name in assets:
                source = asset_root / name
                if source.exists():
                    shutil.copy2(source, stage / name)
                    result['reused_cartography'][name] = file_hash(source)
            if config.get('demand', {}).get('engine') == 'v2':
                cache = asset_root / '.demand-v2-routes.json'
                if cache.is_file():
                    # Each entry is keyed by coordinates, network and geometry mode.
                    shutil.copy2(cache, stage / cache.name)
            result['cartography_provenance'] = ('previous Wizard build' if asset_root != root
                                                else 'existing project assets; original vintage unverified')
        snapshot = stage / 'effective-city.yaml'
        config['data_dir'] = str(Path(data_dir).resolve())
        snapshot.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        produced = Path(execute_pipeline(str(snapshot), output_dir=str(stage),
                                        data_dir=str(data_dir), skip_map=skip_map)).resolve()
        if produced.parent != stage:
            raise ValueError('Build returned an artifact outside its staging directory')
        result['demand_path'] = str(stage / 'demand_data.json')
        if produced.suffix.lower() == '.zip':
            result['commuters'] = validate_package(produced, code)
            result.update(status='success', package_path=str(produced), package_hash=file_hash(produced))
        else:
            result['status'] = 'demand_only'
            result['missing_map_files'] = [name for name in (f'{code}.pmtiles', 'roads.geojson')
                                           if not (stage / name).exists()]
        write_manifest(root, result)
        return result
    except Exception as error:
        result.update(status='error', error=str(error), package_path=None, package_hash=None)
        write_manifest(root, result)
        raise


def resolve_download(config_path, output_dir):
    root = Path(output_dir).resolve()
    result = json.loads((root / 'wizard-build.json').read_text(encoding='utf-8'))
    if result['status'] != 'success' or result['config_hash'] != config_hash(config_path):
        raise ValueError('Compile this configuration to produce a current ZIP')
    if Path(result['config_path']).resolve() != Path(config_path).resolve():
        raise ValueError('Package belongs to another project')
    package = Path(result['package_path']).resolve()
    if not package.is_relative_to(root / 'wizard-builds' / result['build_id']):
        raise ValueError('Invalid package location')
    if file_hash(package) != result['package_hash']:
        raise ValueError('Package changed since validation; rebuild required')
    return package, result


def resolve_preview_roads(config_path, output_dir):
    """Use the last matching build's roads rather than stale root assets."""
    root = Path(output_dir)
    manifest = root / 'wizard-build.json'
    if manifest.is_file():
        result = json.loads(manifest.read_text(encoding='utf-8'))
        if (result.get('status') == 'success'
                and result.get('config_hash') == config_hash(config_path)
                and Path(result['config_path']).resolve() == Path(config_path).resolve()):
            stage = Path(result['output_dir']).resolve()
            if not stage.is_relative_to(root.resolve() / 'wizard-builds' / result['build_id']):
                raise ValueError('Invalid preview artifact location')
            return stage / 'roads.geojson'
    return root / 'roads.geojson'
