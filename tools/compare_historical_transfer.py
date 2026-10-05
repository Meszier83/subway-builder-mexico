"""Reproducible employment comparison; isolated demand builds, no game certification."""
import argparse
import copy
import hashlib
import json
import shutil
import sys
from collections import Counter
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sb_mexico.demand_sources import select_sources
from sb_mexico.historical_transfer import SUPPORTED, FORMULA_VERSION
from sb_mexico.workplace_employment import load_workplaces, source_hashes
from sb_mexico.inegi import parse_ce2024_municipal
from sb_mexico.pipeline import load_city_config, execute_pipeline
from sb_mexico import pipeline as production_pipeline
from sb_mexico.gravity import calculate_commute_distance_distribution
from tools.wizard import inspect_workplace_benchmark
from tools.trace_self_commutes import stage_summary


UNIT_EVIDENCE = ('INEGI CE2024 Metodología, Table 3 PDF pages 20-21: ordinary establishment '
                 'reporting for manufacturing, commerce and these services. '
                 'https://www.inegi.org.mx/contenidos/programas/ce/2024/doc/889463925644.pdf; '
                 'see reports/source-audit/ce-denue-match/README.md. Ownership/edition conditional.')


def digest(frame):
    return hashlib.sha256(frame[['cve_mun_clean','lat','lon','calibrated_jobs']].to_csv(index=False,float_format='%.17g').encode()).hexdigest()


def compare(city, ce_sources, output, build=False):
    before = city.read_bytes()
    cfg = load_city_config(str(city))
    output.mkdir(parents=True, exist_ok=True)
    project = ROOT / cfg.get('data_dir', 'data/' + city.stem)
    sources = {k: select_sources(project, ROOT/'data', k, cfg.get('data_exclusions', []))
               for k in ('denue','ce')}
    benchmark = dict(schema_version=1,role='historical_benchmark',enabled=False,reference_year=2023,
        denue_edition=dict(label='unknown',reference_year=None,evidence='No local edition archive/manifest; fecha_alta is not edition provenance'),
        scope_rule_version='saic_private_paraestatal_v1',coverage_evidence='Full selected municipality source before clipping; ownership and completeness conditional, not independently certified',
        transfer_assumption='Historical municipal sector-size averages on current DENUE locations',
        ce_sources=ce_sources,groups=[])
    inspected = inspect_workplace_benchmark(str(city),benchmark)
    contract = {**inspected['contract'], 'role':'historical_transfer','enabled':True,
                'formula_version':FORMULA_VERSION,'strength':1,
                'groups':[dict(municipality=g['municipality'],scian_prefixes=g['scian_prefixes'],
                               reporting_unit='establishment',reporting_unit_evidence=UNIT_EVIDENCE)
                          for g in inspected['report']['groups'] if g['activity_code'] in SUPPORTED]}
    configs = {}
    for name, mode, strength in [('legacy','legacy',None),('band_prior','ce_bounded',None),
                                 ('transfer_zero','historical_transfer',0),('transfer_half','historical_transfer',.5),
                                 ('transfer_full','historical_transfer',1)]:
        candidate = copy.deepcopy(cfg)
        macro = candidate['macroeconomics']
        for field in ('workplace_control_contract','historical_workplace_transfer','historical_workplace_benchmark'):
            macro.pop(field, None)
        macro['workplace_employment'] = mode
        if strength is not None:
            macro['historical_workplace_transfer'] = {**contract,'strength':strength}
        configs[name] = candidate
    bbox = dict(zip(('min_lon','min_lat','max_lon','max_lat'),cfg['city']['bbox']))
    ce = cfg['macroeconomics'].get('ce_2024_benchmarks', {})
    if not ce:
        for file in sources['ce']:
            ce = parse_ce2024_municipal(file)
            if ce: break
    result = dict(city=str(city.relative_to(ROOT)),original_config_sha256=hashlib.sha256(before).hexdigest(),
                  source_sha256=contract['source_sha256'], cases={},
                  purpose='Compare modeled attraction and demand, not measured commute accuracy',
                  routing='canonical fallback identical in all comparison cases; no OSRM service launched',
                  game_validation='not_run', package_validation='not_run; demand-only isolated outputs')
    input_paths = {str(ROOT/p) for p in ce_sources}
    for kind in ('denue','ce','cpv','conapo'):
        input_paths.update(select_sources(project,ROOT/'data',kind,cfg.get('data_exclusions',[])))
    roads_source = ROOT/'dist'/city.stem/'roads.geojson'
    if build and roads_source.is_file(): input_paths.add(str(roads_source))
    result['input_file_sha256'] = {str(Path(p).relative_to(ROOT)):hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                   for p in sorted(input_paths)}
    code_names = ('pipeline','gravity','inegi','historical_transfer','historical_benchmark','ce_controls',
                  'workplace_employment','source_identity','population_projection','residential',
                  'residential_employment','demand_sources')
    result['pipeline_code_sha256'] = {'sb_mexico/'+n+'.py':hashlib.sha256((ROOT/'sb_mexico'/(n+'.py')).read_bytes()).hexdigest()
                                      for n in code_names}
    prior_digest = None
    for name, candidate in configs.items():
        print('Comparing',name,flush=True)
        frame, _, report = load_workplaces(sources['denue'],bbox,candidate['macroeconomics'],ce,sources['ce'],source_root=ROOT)
        entry = dict(bbox_attraction=float(frame.calibrated_jobs.sum()),bbox_establishments=len(frame),
                     weight_sha256=digest(frame),workplace_report=report,
                     by_municipality_sector=[dict(municipality=m,sector=s,attraction=float(j))
                         for (m,s),j in frame.assign(sector=frame.get('workplace_industry',frame.get('codigo_act')).fillna('').astype(str).str[:2]).groupby(['cve_mun_clean','sector']).calibrated_jobs.sum().items()])
        if name == 'band_prior': prior_digest = entry['weight_sha256']
        if name == 'transfer_zero':
            assert entry['weight_sha256'] == prior_digest, 'Zero strength changed band priors'
        result['cases'][name] = entry
        case_dir = output/name; case_dir.mkdir(exist_ok=True)
        config_path = case_dir/'effective.yaml'
        config_path.write_text(yaml.safe_dump(candidate,allow_unicode=True,sort_keys=False),encoding='utf-8')
        entry['effective_config_sha256'] = hashlib.sha256(config_path.read_bytes()).hexdigest()
        if build and name != 'transfer_zero':
            roads = ROOT/'dist'/city.stem/'roads.geojson'
            if not roads.is_file():
                raise ValueError('Comparison requires the existing road geometry: ' + str(roads))
            shutil.copyfile(roads,case_dir/'roads.geojson')
            entry['roads_sha256'] = hashlib.sha256(roads.read_bytes()).hexdigest()
            print('Running isolated demand pipeline',name,flush=True)
            grid_function = production_pipeline.build_demand_grid
            stage_points = []
            entry['stages'] = []
            def capture_grid(*args, **kwargs):
                nonlocal stage_points
                points, poi_audit = grid_function(*args, **kwargs)
                stage_points = points
                entry['poi_audit'] = poi_audit
                entry['grid_origin_budget'] = sum(p.get('pea_15ymas',0) for p in points)
                entry['grid_attraction'] = sum(p.get('jobs',0) for p in points)
                entry['grid_origins_sha256'] = hashlib.sha256(json.dumps(
                    [(p['id'],p['location'],p.get('pea_15ymas',0)) for p in points if p.get('pea_15ymas',0)>0],
                    sort_keys=True).encode()).hexdigest()
                return points, poi_audit
            def capture_stage(name, fn, tuple_result):
                def capture(*args, **kwargs):
                    nonlocal stage_points
                    result = fn(*args, **kwargs)
                    if tuple_result:
                        stage_points, flows = result
                    else:
                        flows = result
                    entry['stages'].append(dict(stage=name,**stage_summary(stage_points,flows)))
                    return result
                return capture
            with (case_dir/'build.log').open('w',encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log), \
                 patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)), \
                 patch('sb_mexico.pipeline.build_demand_grid',side_effect=capture_grid), ExitStack() as stages:
                for stage_name, function, tuple_result in [('gravity','simulate_gravity_demand',False),
                    ('clustering','cluster_demand_points',True),('consolidation','consolidate_small_pops',True),
                    ('identical_merge','merge_identical_commutes',False),('synchronization','sync_demand_points_and_pops',True)]:
                    stages.enter_context(patch.object(production_pipeline,function,
                        side_effect=capture_stage(stage_name,getattr(production_pipeline,function),tuple_result)))
                execute_pipeline(str(config_path),skip_map=True,output_dir=str(case_dir))
            demand = json.loads((case_dir/'demand_data.json').read_text(encoding='utf-8'))
            pipeline = json.loads((case_dir/'demand_pipeline_report.json').read_text(encoding='utf-8'))
            points, pops = demand['points'], demand['pops']
            ids = {p['id'] for p in points}
            incoming, outgoing = Counter(), Counter()
            self_edges = self_commuters = 0
            for pop in pops:
                assert pop['residenceId'] in ids and pop['jobId'] in ids
                if pop['residenceId'] == pop['jobId']:
                    self_edges += 1
                    self_commuters += pop['size']
                incoming[pop['jobId']] += pop['size']; outgoing[pop['residenceId']] += pop['size']
            commuters = sum(p['size'] for p in pops)
            assert commuters == sum(p['jobs'] for p in points) == sum(p['residents'] for p in points)
            entry['demand'] = dict(points=len(points),cohorts=len(pops),commuters=commuters,
                jobs=sum(p['jobs'] for p in points),residents=sum(p['residents'] for p in points),
                distance=calculate_commute_distance_distribution(pops,points),
                top_destinations=sorted(incoming.items(),key=lambda pair:(-pair[1],pair[0]))[:20],
                residential_projection=pipeline['population_projection'],
                census_identity=pipeline['census_identity'],
                balancing=pipeline['employment_balancing'], grid_merges=pipeline['grid_merges'],
                demand_sha256=hashlib.sha256((case_dir/'demand_data.json').read_bytes()).hexdigest(),
                self_edges=self_edges,self_commuters=self_commuters,missing_references=0,
                realized_destination_residual=max((abs(incoming[p['id']]-p['jobs']) for p in points),default=0),
                realized_origin_residual=max((abs(outgoing[p['id']]-p['residents']) for p in points),default=0))
            # POI processing is performed by the unchanged build_demand_grid path.
            entry['poi_config_sha256'] = hashlib.sha256(json.dumps(candidate.get('pois',[]),sort_keys=True).encode()).hexdigest()
        (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert city.read_bytes() == before
    result['original_config_unchanged'] = True
    result['zero_strength_matches_band_prior'] = True
    frozen_path = ROOT/'reports/source-audit/historical-benchmark-validation/quintana_roo.json'
    if frozen_path.is_file():
        frozen = json.loads(frozen_path.read_text(encoding='utf-8'))['verification']
        if frozen['city_config_sha256'] == result['original_config_sha256']:
            result['legacy_matches_frozen_preimplementation_weights'] = result['cases']['legacy']['weight_sha256'] == frozen['legacy_weight_sha256']
            assert result['legacy_matches_frozen_preimplementation_weights']
    if build:
        names = [n for n in configs if n != 'transfer_zero']
        result['same_commuter_budget'] = len({result['cases'][n]['demand']['commuters'] for n in names}) == 1
        assert result['same_commuter_budget'], 'Employment change altered exported commuter budget'
        result['same_projection'] = all(result['cases'][n]['demand']['residential_projection'] == result['cases']['legacy']['demand']['residential_projection'] for n in names)
        assert result['same_projection']
        result['same_grid_origin_budget'] = len({result['cases'][n]['grid_origin_budget'] for n in names}) == 1
        assert result['same_grid_origin_budget']
        result['same_grid_origins'] = len({result['cases'][n]['grid_origins_sha256'] for n in names}) == 1
        result['census_identities_unchanged'] = all(result['cases'][n]['demand']['census_identity'] == result['cases']['legacy']['demand']['census_identity'] for n in names)
        assert result['census_identities_unchanged']
        result['self_edge_gate'] = 'FAIL' if any(result['cases'][n]['demand']['self_edges'] for n in names) else 'PASS'
    (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({name:dict(attraction=e['bbox_attraction'],commuters=e.get('demand',{}).get('commuters')) for name,e in result['cases'].items()},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('city',type=Path)
    parser.add_argument('--ce',action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--build',action='store_true')
    args = parser.parse_args()
    compare(args.city.resolve(),args.ce,args.output.resolve(),args.build)
