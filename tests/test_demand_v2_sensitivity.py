import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sb_mexico.demand_v2.sensitivity import screen, cached_screen, compare
from sb_mexico.demand_v2.beta_policy import select_beta
from sb_mexico.demand_v2.request import validate_config


def point(id, lon, commuters=0, attraction=0, municipality='A', **extras):
    return dict(id=id,location=[lon,21.],commuters=commuters,attraction=attraction,
                municipality=municipality,zone=0,**extras)


def model():
    return [point('o',-87,1000),point('near',-86.99,attraction=10),
            point('far',-86.7,attraction=100)]


class SensitivityTests(unittest.TestCase):
    def test_paired_reproducible_mass_conserving_and_no_mutation(self):
        points = model(); original = copy.deepcopy(points)
        a,b = screen(points,{}),screen(list(reversed(points)),{})
        self.assertEqual(a['status'],'sensitive')
        self.assertEqual(a['summaries'],b['summaries'])
        self.assertEqual(a['comparisons'],b['comparisons'])
        self.assertEqual(points,original)
        for row in a['summaries']:
            self.assertAlmostEqual(row['estimated_commuters'],1000)
        self.assertGreater(a['summaries'][0]['over_20_km_share'],a['summaries'][2]['over_20_km_share'])
        self.assertNotIn('cells',a);self.assertNotIn('pops',a)
        json.dumps(a)

    def test_hard_support_shared_with_full_assignment(self):
        from sb_mexico.demand_v2.allocation import support
        points = model()
        points[0]['road_component']=1
        points[1]['road_component']=1
        points[2]['road_component']=2
        points += [dict(point('other-zone',-86.98,attraction=100),zone=1),
                   point('coincident',-87,attraction=100,road_component=1)]
        cfg = dict(max_distance_km=5)
        _,destinations,_,cols,_ = support(points,cfg)
        self.assertEqual([destinations[j]['id'] for j in cols],['near'])
        report=screen(points,cfg)
        self.assertEqual(report['status'],'low_variation')
        self.assertTrue(all(r['over_20_km_share']==0 for r in report['summaries']))
        blocked=screen(points,dict(cfg,_forbidden_pairs={('o','near')}))
        self.assertEqual(blocked['reason'],'invalid_support');self.assertEqual(blocked['comparisons'],[])

    def test_missing_municipality_is_inconclusive_and_not_cached(self):
        points=model()+[point('tiny',-86.9,1e-8,municipality='B')]
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.json'
            report=cached_screen(points,{},'fixture',path)
            self.assertEqual(report['reason'],'incomplete_coverage')
            self.assertEqual(report['coverage']['omitted_municipalities'],['B'])
            self.assertEqual(report['comparisons'],[]);self.assertNotIn('summaries',report)
            self.assertFalse(path.exists())

    def test_timeout_discards_partial_work(self):
        # First origin completes, then time expires before the next origin.
        points=model()+[point('second',-86.8,1000)]
        report=screen(points,{},clock=Mock(side_effect=[0,0,16,16]))
        self.assertEqual(report['reason'],'timeout')
        self.assertEqual(report['comparisons'],[]);self.assertNotIn('summaries',report)
        report=screen(model(),{},clock=Mock(side_effect=[0,0,0,16,16]))
        self.assertEqual(report['reason'],'timeout');self.assertNotIn('summaries',report)

    def test_thresholds(self):
        base=dict(median_km=10.,p90_km=20.,over_20_km_share=.1,destination_shares=[.5,.5])
        self.assertFalse(compare(base,base)['potentially_sensitive'])
        for changes in [dict(median_km=11.),dict(p90_km=22.),dict(over_20_km_share=.2),
                        dict(destination_shares=[.6,.4])]:
            self.assertTrue(compare(base,dict(base,**changes))['potentially_sensitive'])

    def test_cache_identity_checksum_and_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cache.json'; points=model()
            original=cached_screen(points,{},'v1',path)
            with patch('sb_mexico.demand_v2.sensitivity.screen',side_effect=AssertionError('cache miss')):
                cached=cached_screen(points,{},'v1',path)
            self.assertTrue(cached['cache_hit']);self.assertEqual(original['summaries'],cached['summaries'])
            for changed_points,cfg,identity in [(points,{},'v2'),(points,{'max_distance_km':40},'v1'),
                    ([dict(p,location=[p['location'][0]+.001,21]) for p in points],{},'v1'),
                    (points,{'_forbidden_pairs':{('o','far')}},'v1')]:
                with patch('sb_mexico.demand_v2.sensitivity.screen',return_value=original) as compute:
                    cached_screen(changed_points,cfg,identity,path);compute.assert_called_once()
            cached_screen(points,{},'v1',path)
            envelope=json.loads(path.read_text());envelope['payload']['status']='low_variation'
            path.write_text(json.dumps(envelope),encoding='utf-8')
            with patch('sb_mexico.demand_v2.sensitivity.screen',return_value=original) as compute:
                cached_screen(points,{},'v1',path);compute.assert_called_once()
            path.write_text('{broken',encoding='utf-8')
            with patch('sb_mexico.demand_v2.sensitivity.screen',return_value=original) as compute:
                cached_screen(points,{},'v1',path);compute.assert_called_once()
            blocker=Path(folder)/'file';blocker.write_text('keep',encoding='utf-8')
            report=cached_screen(points,{},'v1',blocker/'cache.json')
            self.assertIn('cache_warning',report);self.assertEqual(blocker.read_text(),'keep')

    def test_reference_default_and_manual_precedence(self):
        for cfg,beta,mode in [({},.12,'reference'),({'beta_selection':'reference'},.12,'reference'),
                              ({'beta_selection':'automatic','beta':.09},.09,'manual')]:
            solve=Mock(return_value=([],{}))
            _,report=select_beta([],cfg,{},solve)
            self.assertEqual(solve.call_count,1);self.assertEqual(solve.call_args.args[1]['beta'],beta)
            self.assertEqual(report['beta_policy']['mode'],mode)
            self.assertFalse(report['beta_calibrated'])
        with self.assertRaisesRegex(ValueError,'beta_selection'):
            validate_config({'demand':{'beta_selection':'typo'}})

        try:
            from test_demand_v2 import EndToEndTests
        except ImportError:
            from tests.test_demand_v2 import EndToEndTests
        from sb_mexico.demand_v2.engine import build_demand
        fixture=EndToEndTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.config['demand']['fixed_cohort_size']=50
        request=fixture.request(); request.sensitivity_cache=fixture.root/'screen.json'
        with patch('sb_mexico.demand_v2.engine.allocate',side_effect=AssertionError('allocation forbidden')), \
             patch('sb_mexico.demand_v2.fixed_groups.resolve',side_effect=AssertionError('cohorts forbidden')), \
             patch('sb_mexico.demand_v2.cohorts.integerize',side_effect=AssertionError('integerization forbidden')), \
             patch('sb_mexico.demand_v2.engine.route_commutes',side_effect=AssertionError('routing forbidden')), \
             patch('sb_mexico.demand_v2.engine.validate_export',side_effect=AssertionError('export forbidden')):
            result=build_demand(request,'sensitivity')
        self.assertNotIn('cells',result);self.assertNotIn('game',result)
        report=result['report']['sensitivity']
        self.assertGreaterEqual(report['source_preparation_seconds'],0)
        self.assertLess(report['comparison_seconds'],15)
        from sb_mexico.demand_v2.integration import preview_candidate
        fixture.config['routing']={'osrm_url':'http://invalid-not-needed'}
        with patch('sb_mexico.demand_v2.integration.osrm_provider',side_effect=AssertionError('OSRM forbidden')), \
             patch('sb_mexico.demand_v2.engine.allocate',side_effect=AssertionError('allocation forbidden')):
            preview=preview_candidate(fixture.config,fixture.root,stage='sensitivity')
        self.assertEqual(preview['stage'],'sensitivity');self.assertEqual(preview['points'],[])
        self.assertIn('sensitivity',preview['report'])
        with patch('sb_mexico.demand_v2.engine.identity',side_effect=[('old',[]),('changed',[])]):
            with self.assertRaisesRegex(ValueError,'changed during sensitivity'):
                build_demand(request,'sensitivity')


if __name__=='__main__':
    unittest.main()
