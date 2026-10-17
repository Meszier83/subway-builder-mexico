import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd

from sb_mexico.demand_v2.allocation import allocate as production_allocate, _allocate_at_beta
from sb_mexico.demand_v2.beta_policy import select_beta as production_select_beta, improvement, probabilities, sample_rows
from sb_mexico.demand_v2.sources import read_mobility


def allocate(points, config, mobility):
    # These fixtures exercise the explicitly opted-in automatic policy.
    return production_allocate(points, dict(config, beta_selection='automatic'), mobility)


def select_beta(points, config, mobility, solve):
    return production_select_beta(points, dict(config, beta_selection='automatic'), mobility, solve)


def points():
    return [dict(id=id, location=[lon, 21.], commuters=budget, attraction=jobs,
                 municipality=municipality, zone=0) for id, lon, budget, jobs, municipality in
            [('a', -87., 10000, 0, 'A'), ('b', -86.8, 10000, 0, 'B'),
             ('x', -86.99, 0, 10000, 'A'), ('y', -86.79, 0, 10000, 'B')]]


def observations(cells, model, reverse_guard=False):
    p = probabilities(model, cells)
    flows = []
    for (origin, destination), probability in p.items():
        count = round(5000*probability)
        guard = 5000-count if reverse_guard else count
        flows.append(dict(origin=origin, destination=destination, weight=count+guard,
                          training_weight=0., training_records=0, effective_sample_size=0.,
                          validation_weight=count+guard, selection_weight=count, guard_weight=guard,
                          selection_weights={'1': count}, guard_weights={'1': guard}))
    return dict(flows=flows)


class BetaPolicyTests(unittest.TestCase):
    def test_actual_integer_allocation_improves_and_is_reproducible(self):
        model = points()
        desired, _ = _allocate_at_beta(model, dict(beta=.24, fixed_cohort_size=50), {})
        mobility = observations(desired, model)
        original = copy.deepcopy((model, mobility))
        cells, report = allocate(model, dict(fixed_cohort_size=50), mobility)
        self.assertNotEqual(report['beta'], .12)
        self.assertEqual(report['beta_policy']['reason'], 'stable_guard_improvement')
        self.assertGreater(report['beta_policy']['guard_check']['lower95'], 0)
        self.assertEqual(sum(c['size'] for c in cells), 20000)
        self.assertTrue(all(s <= 50 for c in cells for s in c['cohort_sizes']))
        self.assertEqual(cells, allocate(model, dict(fixed_cohort_size=50), mobility)[0])
        self.assertEqual((model, mobility), original)
        json.dumps(report)

    def test_guard_disagrees_and_restores_exact_reference_cells(self):
        model = points()
        desired, _ = _allocate_at_beta(model, dict(beta=.24), {})
        mobility = observations(desired, model, reverse_guard=True)
        cells, report = allocate(model, {}, mobility)
        reference, _ = _allocate_at_beta(model, dict(beta=.12), mobility)
        self.assertEqual(cells, reference)
        self.assertEqual(report['beta'], .12)
        self.assertEqual(report['beta_policy']['reason'], 'no_stable_improvement')
        self.assertLess(report['beta_policy']['guard_check']['gain'], 0)

    def test_single_municipality_never_claims_calibration(self):
        model = points()
        for p in model: p['municipality'] = 'A'
        calls = []
        def solve(p, config, mobility):
            calls.append(config['beta'])
            return _allocate_at_beta(p, config, mobility)
        cells, report = select_beta(model, {}, dict(flows=[]), solve)
        self.assertEqual(calls, [.12])
        self.assertFalse(report['beta_calibrated'])
        self.assertEqual(report['beta_policy']['reason'], 'municipal_flows_uninformative')

    def test_low_effective_sample_due_to_dominant_weight_uses_reference(self):
        model = points()
        row = dict(origin='A', destination='B', weight=20018, training_weight=0,
                   selection_weight=10009, guard_weight=10009,
                   selection_weights={'10000':1, '1':9}, guard_weights={'10000':1, '1':9})
        _, report = allocate(model, {}, dict(flows=[row]))
        self.assertEqual(report['beta'], .12)
        self.assertLess(report['beta_policy']['guard_sample']['effective_sample_size'], 2)

    def test_manual_beta_and_poi_quota_are_preserved(self):
        model = points()
        model.append(dict(id='UNI_test', location=[-86.9,21.], commuters=0, attraction=57,
                          municipality='A', zone=0, is_special=True))
        cells, report = allocate(model, dict(beta=.09, fixed_cohort_size=50), {})
        self.assertEqual(report['beta'], .09)
        self.assertEqual(report['beta_policy']['mode'], 'manual')
        self.assertEqual(sum(c['size'] for c in cells if c['destination']=='UNI_test'), 57)
        self.assertEqual(sum(c['size'] for c in cells), 20000)
        self.assertEqual(report['distance_summary']['commuters'], 20000)

    def test_changed_routing_support_reruns_selection_and_removes_pair(self):
        model = points()
        model.append(dict(id='c',location=[-86.9,21.],commuters=2000,attraction=0,municipality='A',zone=0))
        desired, _ = _allocate_at_beta(model, dict(beta=.24), {})
        cells, report = allocate(model, dict(_forbidden_pairs={('a','y')}), observations(desired, model))
        self.assertTrue(report['beta_policy']['routing_support_rechecked'])
        self.assertFalse(any(c['origin']=='a' and c['destination']=='y' for c in cells))
        self.assertEqual(sum(c['size'] for c in cells), 22000)

    def test_external_unknown_and_other_origin_do_not_enter_decision(self):
        model = points()
        flows = [dict(origin=o,destination=d, selection_weight=100,selection_weights={'1':100})
                 for o,d in [('A','unknown'),('A','outside'),('outside','A')]]
        samples, excluded = sample_rows(model, dict(flows=flows), 'selection')
        self.assertEqual(samples, [])
        self.assertEqual(excluded, 300)

    def test_ties_and_candidate_failures_keep_reference(self):
        model = points()
        reference, _ = _allocate_at_beta(model, dict(beta=.12), {})
        mobility = observations(reference, model)
        def solve(p, cfg, m):
            if cfg['beta'] != .12: raise ValueError('fixture non-convergence')
            return copy.deepcopy(reference), {}
        cells, report = select_beta(model, {}, mobility, solve)
        self.assertEqual(cells, reference)
        self.assertEqual(report['beta_policy']['reason'], 'candidate_solver_failures')
        self.assertEqual(len(report['beta_policy']['candidate_failures']), 4)
        with self.assertRaisesRegex(ValueError, 'reference failed'):
            select_beta(model, {}, mobility, lambda *args: (_ for _ in ()).throw(ValueError('reference failed')))

    def test_split_is_disjoint_identity_stable_deduplicated_and_chunk_independent(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'people.csv'
            rows = [dict(CVEGEO='23005',ID_PERSONA=str(i), EDAD=30, CONACT=10,
                         TIE_TRASLADO_TRAB=1,FACTOR=(i%4+1)*10,MUN_TRAB='005',ENT_PAIS_TRAB='23')
                    for i in range(200)]
            pd.DataFrame(rows).to_csv(path,index=False)
            a = read_mobility([path,path], {'23005'})
            pd.DataFrame(list(reversed(rows))).to_csv(path,index=False)
            b = read_mobility([path], {'23005'})
            self.assertEqual(a,b)
            flow = a['flows'][0]
            expected = dict(training=0, selection=0, guard=0)
            for row in rows:
                bucket = int(hashlib.sha256(repr(('23', row['ID_PERSONA'])).encode()).hexdigest()[:8],16)%10
                partition = 'guard' if bucket<2 else 'selection' if bucket<4 else 'training'
                expected[partition] += row['FACTOR']
            for name, weight in expected.items(): self.assertEqual(flow[name+'_weight'],weight)
            self.assertEqual(flow['validation_weight'], expected['selection']+expected['guard'])
            for name in ('selection','guard'):
                self.assertEqual(sum(int(w)*n for w,n in flow[name+'_weights'].items()),expected[name])

    def test_engine_rechecks_policy_after_no_route_and_exports_integer_budgets(self):
        from sb_mexico.demand_v2.engine import build_demand, route_commutes, DisconnectedCommutes
        from sb_mexico.demand_v2.request import DemandRequest
        model = points()
        model.append(dict(id='c',location=[-86.9,21.],commuters=2000,attraction=0,municipality='A',zone=0))
        for p in model:
            p.update(population=p['commuters'],residents=p['commuters'],jobs=p['attraction'],
                     pea_15ymas=p['commuters'],popIds=[],is_special=False)
        desired, _ = _allocate_at_beta(model, dict(beta=.24), {})
        evidence=dict(report={}, mobility=observations(desired,model), census=None,
                      bases={},controls={},people={})
        mapping={p['id']:p['id'] for p in model}
        request=DemandRequest(config=dict(demand=dict(engine='v2',target_year=2025,beta_selection='automatic'),
                              macroeconomics=dict(min_pop_size=1,target_pop_size=35,max_pop_size=50)),
                              sources={},root=Path.cwd())
        attempts=[]
        def route(request,pops,game_points):
            attempts.append([(p['residenceId'],p['jobId']) for p in pops])
            if len(attempts)==1:
                self.assertIn(('a','y'), attempts[-1])
                raise DisconnectedCommutes({('a','y')})
            return route_commutes(request,pops,game_points)
        with patch('sb_mexico.demand_v2.engine.identity',return_value=('fixture',[])), \
             patch('sb_mexico.demand_v2.engine.ingest',return_value=evidence), \
             patch('sb_mexico.demand_v2.engine.allocate_population',return_value=(None,{})), \
             patch('sb_mexico.demand_v2.engine.build_points',return_value=(model,None,dict(point_mapping=mapping))), \
             patch('sb_mexico.demand_v2.engine.consolidate_points',return_value=(model,mapping)), \
             patch('sb_mexico.demand_v2.engine.route_commutes',side_effect=route):
            result=build_demand(request)
        self.assertEqual(len(attempts),2)
        self.assertNotIn(('a','y'),attempts[-1])
        self.assertTrue(result['report']['allocation']['beta_policy']['routing_support_rechecked'])
        self.assertEqual(sum(p['size'] for p in result['game']['pops']),22000)
        self.assertEqual(result['report']['allocation']['distance_summary']['commuters'],22000)

    def test_cache_exact_match_and_source_point_config_support_corruption_invalidation(self):
        with tempfile.TemporaryDirectory() as folder:
            model=points()
            desired,_=_allocate_at_beta(model,dict(beta=.24),{})
            mobility=observations(desired,model)
            path=Path(folder)/'beta.json'
            config=dict(_beta_cache_path=str(path),_beta_cache_identity='source-and-code-v1')
            solve=unittest.mock.Mock(side_effect=_allocate_at_beta)
            cells,report=select_beta(model,config,mobility,solve)
            self.assertFalse(report['beta_policy']['cache_hit'])
            solve.reset_mock()
            cached,report=select_beta(model,config,mobility,solve)
            self.assertEqual(cells,cached);solve.assert_not_called()
            self.assertTrue(report['beta_policy']['cache_hit'])
            for changed_model,changed_config,changed_mobility in [
                (model,dict(config,_beta_cache_identity='source-and-code-v2'),mobility),
                (model,dict(config,fixed_cohort_size=50),mobility),
                ([dict(p,location=[p['location'][0]+.001,21.]) for p in model],config,mobility),
                (model,dict(config,_forbidden_pairs={('unused','unused')}),mobility),
                (model,config,dict(mobility,beta_split='changed'))]:
                # Limit this test to the baseline; identity misses must call the
                # solver, without repeating statistical tests already covered.
                with patch('sb_mexico.demand_v2.beta_policy._select_beta',return_value=(cells,copy.deepcopy(report))) as compute:
                    select_beta(changed_model,changed_config,changed_mobility,solve)
                    compute.assert_called_once()
            path.write_text('{broken',encoding='utf-8')
            with patch('sb_mexico.demand_v2.beta_policy._select_beta',return_value=(cells,copy.deepcopy(report))) as compute:
                select_beta(model,config,mobility,solve);compute.assert_called_once()
            envelope=json.loads(path.read_text());envelope['payload']['cells'][0]['size']+=1
            path.write_text(json.dumps(envelope),encoding='utf-8')
            with patch('sb_mexico.demand_v2.beta_policy._select_beta',return_value=(cells,copy.deepcopy(report))) as compute:
                select_beta(model,config,mobility,solve);compute.assert_called_once()

    def test_cache_write_failure_does_not_destroy_valid_demand(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'not-a-directory';path.write_text('preserve',encoding='utf-8')
            cells,report=select_beta(points(),dict(_beta_cache_path=str(path/'cache.json'),
                                     _beta_cache_identity='fixture'),{},_allocate_at_beta)
            self.assertEqual(sum(c['size'] for c in cells),20000)
            self.assertIn('cache_warning',report['beta_policy'])

    def test_preview_build_routing_flags_reuse_allocation_but_not_full_identity(self):
        from sb_mexico.demand_v2.request import DemandRequest,identity,beta_identity
        a=DemandRequest(config=dict(demand={'engine':'v2'},routing={}),sources={},root=Path.cwd())
        b=copy.deepcopy(a);b.config['routing']['include_driving_path']=False;b.route_identity='network:car'
        self.assertNotEqual(identity(a)[0],identity(b)[0])
        self.assertEqual(beta_identity(a,[]),beta_identity(b,[]))
        self.assertNotEqual(beta_identity(a,[]),beta_identity(a,[('eic','people.csv','changed-source')]))

    def test_real_ingestion_preview_cache_reused_in_export_with_routing_flags(self):
        from test_demand_v2 import EndToEndTests
        from sb_mexico.demand_v2.engine import build_demand
        fixture=EndToEndTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.config['demand'].pop('beta',None)
        fixture.config['demand']['beta_selection']='automatic'
        request=fixture.request();request.beta_cache=fixture.root/'beta-cache.json'
        preview=build_demand(request,'allocation')
        self.assertFalse(preview['report']['allocation']['beta_policy']['cache_hit'])
        fixture.config['routing']={'include_driving_path':False}
        request=fixture.request();request.beta_cache=fixture.root/'beta-cache.json';request.route_identity='network:car'
        with patch('sb_mexico.demand_v2.allocation._allocate_at_beta',side_effect=AssertionError('must reuse final allocation')):
            exported=build_demand(request)
        self.assertTrue(exported['report']['allocation']['beta_policy']['cache_hit'])
        self.assertEqual(preview['cells'],exported['cells'])
        self.assertEqual(sum(p['size'] for p in exported['game']['pops']),80)


if __name__ == '__main__': unittest.main()
