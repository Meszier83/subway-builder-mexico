import unittest
from collections import Counter
import numpy as np
from sb_mexico.demand_v2.allocation import allocate
from sb_mexico.demand_v2.cohorts import integerize,settings
from sb_mexico.demand_v2.engine import validate_export


def point(id,lon,budget=0,attraction=0,zone=0,special=False):
    return dict(id=id,location=[lon,21.],commuters=budget,attraction=attraction,
                zone=zone,municipality='M',is_special=special)


class Stage4Tests(unittest.TestCase):
    def test_rare_municipal_share_survives_large_cohorts(self):
        origins=[dict(id=str(i),commuters=50,municipality='A') for i in range(100)]
        destinations=[dict(id='a',municipality='A'),dict(id='b',municipality='B')]
        rows=np.repeat(np.arange(100),2); cols=np.tile([0,1],100)
        flow=np.tile([49.5,.5],100)
        options=settings({'demand':{'fixed_cohort_size':50}})
        cells,integer,report=integerize(flow,rows,cols,origins,destinations,np.ones(200),options)
        self.assertEqual(sum(c['size'] for c in cells if c['destination']=='b'),50)
        realized=Counter()
        for cell in cells: realized[cell['origin']]+=cell['size']
        self.assertEqual(Counter({str(i):50 for i in range(100)}),realized)
        self.assertEqual(report['actual_count'],150)
        self.assertEqual(report['municipal_rounding_unit'],'person')
        self.assertEqual(integer.sum(),5000)

    def test_structural_zero_and_feasible_rounding(self):
        origins=[dict(id='a',commuters=50,municipality='A'),dict(id='b',commuters=50,municipality='A')]
        destinations=[dict(id='x',municipality='X'),dict(id='y',municipality='Y')]
        cells,_,_=integerize(np.array([10.,40.,50.]),np.array([0,0,1]),np.array([0,1,0]),origins,destinations,
                            np.ones(3),settings({'demand':{'fixed_cohort_size':50}}))
        self.assertTrue(all(c['destination']=='x' for c in cells if c['origin']=='b'))
        self.assertEqual(sum(c['size'] for c in cells),100)

    def test_exact_poi_quota_and_required_small_pieces(self):
        points=[point('o',-86.9,200),point('UNI_test',-86.85,attraction=57,special=True),point('ordinary',-86.8,attraction=10)]
        cells,r=allocate(points,{'fixed_cohort_size':50},{})
        self.assertEqual(sum(c['size'] for c in cells),200)
        self.assertEqual(sum(c['size'] for c in cells if c['destination']=='UNI_test'),57)
        self.assertEqual(r['poi_quotas'][0]['shortfall'],0)
        self.assertEqual(r['cohorts']['actual_count'],5)
        self.assertTrue(all(s<=50 for c in cells for s in c['cohort_sizes']))
        self.assertEqual(cells,allocate(points,{'fixed_cohort_size':50},{})[0])

    def test_poi_budget_shortfall_is_reported(self):
        cells,r=allocate([point('o',-86.9,80),point('AIR_test',-86.8,attraction=100,special=True)],{}, {})
        self.assertEqual(r['poi_quotas'][0]['realized'],80)
        self.assertEqual(r['poi_quotas'][0]['shortfall'],20)
        self.assertEqual(sum(c['size'] for c in cells),80)

    def test_quota_does_not_override_island_or_coincident_support(self):
        points=[point('a',-86.9,37,zone=0),point('b',-86.8,63,zone=1),
                point('UNI_test',-86.7,attraction=100,zone=1,special=True),point('ordinary',-86.6,attraction=10)]
        cells,r=allocate(points,{}, {})
        self.assertEqual(r['poi_quotas'][0]['realized'],63)
        self.assertTrue(all(c['destination']=='ordinary' for c in cells if c['origin']=='a'))

    def test_explicit_count_includes_reserved_segments(self):
        points=[point('o',-86.9,200),point('UNI_test',-86.85,attraction=57,special=True),point('ordinary',-86.8,attraction=10)]
        params=dict(min_pop_size=1,target_pop_size=40,max_pop_size=50,cohort_count=6)
        cells,r=allocate(points,params,{})
        self.assertEqual(r['cohorts']['actual_count'],6)
        self.assertEqual(sum(c['size'] for c in cells if c['destination']=='UNI_test'),57)
        with self.assertRaisesRegex(ValueError,'minimum 5'):
            allocate(points,dict(params,cohort_count=4),{})

    def test_export_rejects_coincident_or_prohibited_endpoints(self):
        model=[point('a',-86.9,7),point('b',-86.9,zone=1)]
        cells=[dict(origin='a',destination='b',size=7)]
        pops=[dict(id='p',residenceId='a',jobId='b',size=7,drivingDistance=1.,drivingSeconds=1.)]
        with self.assertRaisesRegex(ValueError,'coincident'):
            validate_export(model,cells,model,pops,50)
        model[1]['location']=[-86.8,21.]
        with self.assertRaisesRegex(ValueError,'hard territory'):
            validate_export(model,cells,model,pops,50)
