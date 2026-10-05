"""Reproduce legacy/candidate demand without overwriting installed map outputs.

python3 tools/compare_residential_placement.py cities/cancun_riviera_maya.yaml
Optional --baseline-inegi points to a captured pre-change ingestion module.
OSRM is deliberately not started: both runs use the same documented fallback.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geopandas as gpd
import numpy as np
import pandas as pd
from sb_mexico import inegi
from sb_mexico.pipeline import load_city_config, validate_cohort_spatial_integrity
from sb_mexico.gravity import (build_demand_grid, simulate_gravity_demand, cluster_demand_points,
                               consolidate_small_pops, merge_identical_commutes,
                               sync_demand_points_and_pops, sanitize_demand_points,
                               is_point_in_exclusion_zone, assign_zones, recommend_gravity_beta,
                               sanitize_max_distance_km)
from sb_mexico.osrm import calculate_canonical_driving_fallback
from sb_mexico.residential import discover_marco_layers, grid_accounting


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def independent_geometry_check(census, paths):
    """Inspect raw polygons independently of the implementation under test."""
    import shapely
    polygons = []
    for path in paths:
        frame = gpd.read_file(path)
        if not all(c in frame for c in ('CVE_ENT','CVE_MUN','CVE_LOC','CVE_AGEB','CVE_MZA')):
            continue
        frame = frame.to_crs(4326)
        frame['cve_mun_clean'] = frame.CVE_ENT.astype(str).str.zfill(2) + frame.CVE_MUN.astype(str).str.zfill(3)
        frame['loc_clean'] = frame.CVE_LOC.astype(str).str.zfill(4)
        frame['ageb_clean'] = frame.CVE_AGEB.astype(str).str.zfill(4)
        frame['mza_clean'] = pd.to_numeric(frame.CVE_MZA).astype(int).astype(str).str.zfill(3)
        polygons.append(frame[['cve_mun_clean','loc_clean','ageb_clean','mza_clean','geometry']])
    if not polygons:
        return {'compared': 0}
    geo = pd.concat(polygons, ignore_index=True)
    keys = ['cve_mun_clean','ageb_clean','mza_clean']
    source = census.copy()
    source['mza_clean'] = source.mza_clean.astype(str).str.zfill(3)
    if 'loc_clean' in source:
        keys.insert(1, 'loc_clean')
    geo = geo.loc[~geo.duplicated(keys, keep=False)]
    compare = source.merge(geo, on=keys, how='inner', validate='many_to_one')
    points = shapely.points(compare.lon.to_numpy(), compare.lat.to_numpy())
    contained = shapely.covers(compare.geometry.to_numpy(), points)
    centres = shapely.centroid(compare.geometry.to_numpy())
    metres = np.hypot((compare.lon.to_numpy()-shapely.get_x(centres))*111320*np.cos(np.deg2rad(compare.lat)),
                       (compare.lat.to_numpy()-shapely.get_y(centres))*110574)
    result = dict(compared=len(compare), inside_blocks=int(contained.sum()),
                  outside_blocks=int((~contained).sum()),
                  displacement_metres=dict(zip(['p50','p95','p99','max'], map(float,np.percentile(metres,[50,95,99,100])))))
    if 'placement_source' in compare:
        official = compare.placement_source.eq('official_block').to_numpy()
        result['official_block_outside'] = int((official & ~contained).sum())
    return result


def run(args):
    started = time.perf_counter()
    cfg = load_city_config(str(args.config)); city = cfg['city']; macro = cfg['macroeconomics']
    base = ROOT / cfg.get('data_dir', f'data/{args.config.stem}')
    dirs = [base, ROOT/'data']
    def find(patterns):
        return list(dict.fromkeys(p for directory in dirs for pattern in patterns
                                  for p in sorted(directory.glob(pattern)) if p.is_file()))
    den_paths = find(['*denue*.csv','*DENUE*.csv'])
    cpv_paths = find(['*RESAGEBURB*.csv','*resageburb*.csv','*censo*.csv','*censo*.xlsx','*cpv*.csv'])
    ce_paths = find(['*SAIC*.csv','*saic*.csv','*exporta*.csv','*cenu24*.csv','*tr_ce*.csv','*ce_*.csv','*ce2024*.csv'])
    conapo_paths = find(['*pobproy*.csv','*quinq*.csv','*pob_proy*.csv','*conapo*.csv','data-*.csv','*proyeccion*.csv'])
    geometry = discover_marco_layers(base)
    legacy_geometry = find(['*mza*.shp','*mza*.geojson','*mza*.gpkg','*ageb*.shp','*ageb*.geojson','*ageb*.gpkg','*manzana*.shp','*manzana*.geojson'])
    module = inegi
    if args.mode == 'legacy' and args.baseline_inegi:
        spec = importlib.util.spec_from_file_location('baseline_inegi', args.baseline_inegi)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    bbox = dict(zip(['min_lon','min_lat','max_lon','max_lat'], city['bbox']))
    tasa = macro.get('tasa_pea') or .62; til = macro.get('til_1_state') or .45
    # Existing fixture cities specify macro rates explicitly; do not silently use
    # this comparison as an ENOE-selection implementation for other cities.
    if macro.get('tasa_pea') is None or macro.get('til_1_state') is None:
        raise ValueError('Comparison requires explicit macro rates to match production inputs')
    benchmarks = macro.get('ce_2024_benchmarks', {})
    if not benchmarks:
        for path in ce_paths:
            benchmarks = module.parse_ce2024_municipal(str(path))
            if benchmarks: break
    raw = module.load_denue(list(map(str,den_paths)), bbox)
    den, employment = module.calibrate_denue_employment(raw,benchmarks,til,macro.get('sample_threshold',500))
    projection_year = inegi.resolve_projection_year(macro)
    projections = module.parse_conapo_projections(str(conapo_paths[0]),target_year=projection_year,as_growth_factors=True) if conapo_paths else None
    kw = dict(cpv_paths=list(map(str,cpv_paths)),df_denue=den,bbox=bbox,tasa_pea=tasa,
              growth_factors=macro.get('growth_factors',{}),conapo_projections=projections,
              default_growth=macro.get('default_growth_factor',1.),
              marco_paths=list(map(str,legacy_geometry)) if args.mode=='legacy' else geometry)
    if module is inegi: kw['placement_mode'] = args.mode
    census = module.load_cpv_demography(**kw)
    ingestion_seconds = time.perf_counter()-started
    roads_path = ROOT/'dist'/args.config.stem/'roads.geojson'
    roads = gpd.read_file(roads_path) if roads_path.exists() else gpd.GeoDataFrame(geometry=[],crs=4326)
    seed = int(city.get('seed',macro.get('seed',42)))
    grid_merges, cohort_merges = {}, {}
    points, poi = build_demand_grid(den,census,cfg.get('pois',[]),roads,
        grid_size=city.get('grid_size',.0025),min_residents=city.get('min_residents',10),
        min_jobs=city.get('min_jobs',3),seed=seed,affluence_zones=cfg.get('affluence_zones'),
        exclusion_zones=cfg.get('exclusion_zones'),urban_core_polygon=city.get('urban_core_polygon'),
        restrict_demand_to_urban_core=city.get('restrict_demand_to_urban_core',True),
        isolated_zones=cfg.get('isolated_zones'),merge_diagnostics=grid_merges)
    ledger = grid_accounting(census,points,cfg.get('exclusion_zones'),city.get('urban_core_polygon'),city.get('restrict_demand_to_urban_core',True))
    point_budget = sum(p['pea_15ymas'] for p in points)
    pregrid = [dict(p) for p in points]
    beta = macro.get('gravity_beta'); max_dist = sanitize_max_distance_km(macro.get('max_distance_km'),55.)
    if beta is None or str(beta).lower() in ('auto','none',''):
        beta = recommend_gravity_beta(city['bbox'],demand_points=points,isolated_zones=cfg.get('isolated_zones'),max_distance_km=max_dist)['recommended_beta']
    balancing_reports = []
    pops = simulate_gravity_demand(points,beta=float(beta),max_distance_km=max_dist,
        min_pop_size=macro.get('min_pop_size',25),max_pop_size=macro.get('max_pop_size',200),
        target_pop_size=macro.get('target_pop_size',180),seed=seed,isolated_zones=cfg.get('isolated_zones'),
        affluence_zones=cfg.get('affluence_zones'),furness_iterations=macro.get('furness_iterations',15),
        furness_tol=macro.get('furness_tol',.02),road_index=None,balancing_diagnostics=balancing_reports)
    points,pops = cluster_demand_points(points,pops,isolated_zones=cfg.get('isolated_zones'),
        exclusion_zones=cfg.get('exclusion_zones'),
        urban_core_polygon=city.get('urban_core_polygon') if city.get('restrict_demand_to_urban_core',True) else None)
    points,pops = consolidate_small_pops(points,pops,min_pop_size=macro.get('min_pop_size',25),
        max_pop_size=macro.get('max_pop_size',200),isolated_zones=cfg.get('isolated_zones'),
        merge_diagnostics=cohort_merges)
    pops = merge_identical_commutes(pops,min_pop_size=macro.get('min_pop_size',25),max_pop_size=macro.get('max_pop_size',200),include_driving_path=False)
    points,pops = sync_demand_points_and_pops(points,pops,include_driving_path=False)
    locations = {p['id']:p['location'] for p in points}
    for pop in pops:
        a,b = locations[pop['residenceId']],locations[pop['jobId']]
        metres = np.hypot((b[0]-a[0])*111320*np.cos(np.deg2rad((a[1]+b[1])/2)),(b[1]-a[1])*110574)
        pop['drivingDistance'],pop['drivingSeconds'] = calculate_canonical_driving_fallback(metres)
    validate_cohort_spatial_integrity(pops,points)
    if sum(p['size'] for p in pops) != point_budget: raise ValueError('Commuter budget not conserved')
    if len(locations) != len(points): raise ValueError('Duplicate point IDs')
    zone_ids = assign_zones(np.array([p['location'] for p in points]),cfg.get('isolated_zones'))
    zones = dict(zip(locations, map(int,zone_ids)))
    demand = dict(points=sanitize_demand_points(points),pops=pops)
    encoded = json.dumps(demand,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/f'{args.mode}_demand.json').write_bytes(encoded)
    census.to_csv(args.output/f'{args.mode}_census.csv',index=False)
    pd.DataFrame([dict(id=p['id'],lon=p['location'][0],lat=p['location'][1],pea=p['pea_15ymas']) for p in pregrid]).to_csv(args.output/f'{args.mode}_grid.csv',index=False)
    import resource
    sources = list(dict.fromkeys([args.config,*den_paths,*cpv_paths,*ce_paths,*conapo_paths,*geometry,roads_path]))
    # Shapefile attributes/projection/index are inputs too.
    sources += [p.with_suffix(s) for p in map(Path,geometry) if p.suffix.lower()=='.shp' for s in ['.dbf','.prj','.shx']]
    report = dict(mode=args.mode,config=cfg,inputs={str(p):sha(p) for p in sources if Path(p).exists()},
        code_inputs={str(p):sha(p) for p in [Path(module.__file__),ROOT/'sb_mexico/gravity.py',
                     ROOT/'sb_mexico/pipeline.py',ROOT/'sb_mexico/residential.py',
                     ROOT/'sb_mexico/source_identity.py',Path(__file__)]},
        geometry_check=independent_geometry_check(census,geometry),placement=census.attrs.get('residential_placement'),
        grid=ledger,grid_merges=grid_merges,cohort_merges=cohort_merges,
        employment_balancing=balancing_reports,
        denue_identity=den.attrs.get('source_identity'),census_identity=census.attrs.get('source_identity'),
        denue_establishments=len(den),estimated_jobs=float(den.calibrated_jobs.sum()),
        poi=poi,points=len(points),cohorts=len(pops),commuters=point_budget,bytes=len(encoded),
        ingestion_seconds=ingestion_seconds,total_seconds=time.perf_counter()-started,
        peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        final_excluded_points=sum(is_point_in_exclusion_zone(*p['location'],cfg.get('exclusion_zones')) for p in points),
        cross_zone_commuters=sum(p['size'] for p in pops if zones[p['residenceId']]!=zones[p['jobId']]),
        special_ids=sorted(p['id'] for p in points if p['id'] in {q['id'] for q in cfg.get('pois',[])}),
        runtime_note='Static full-demand comparison with canonical fallback; game load/save/reload not verified')
    (args.output/f'{args.mode}_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:report[key] for key in ['mode','points','cohorts','commuters','bytes','total_seconds','peak_rss_mb','geometry_check','cross_zone_commuters','final_excluded_points']}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--mode',choices=['both','legacy','official_blocks'],default='both')
    parser.add_argument('--baseline-inegi',type=Path)
    args = parser.parse_args()
    args.config = args.config.resolve()
    args.output = (args.output or ROOT/'reports'/'residential-placement'/args.config.stem).resolve()
    if args.output.is_relative_to(ROOT/'dist') or args.output == ROOT:
        parser.error('Comparison output must not overwrite dist or the repository root')
    if args.mode == 'both':
        args.output.mkdir(parents=True,exist_ok=True)
        for mode in ['legacy','official_blocks']:
            command = [sys.executable,__file__,str(args.config),'--output',str(args.output),'--mode',mode]
            if args.baseline_inegi: command += ['--baseline-inegi',str(args.baseline_inegi.resolve())]
            with (args.output/f'{mode}.log').open('w',encoding='utf-8') as stream:
                subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT,check=True,cwd=ROOT)
            report = json.loads((args.output/f'{mode}_report.json').read_text())
            print(mode,{k:report[k] for k in ['points','cohorts','commuters','bytes','total_seconds','peak_rss_mb']},flush=True)
    else:
        run(args)
