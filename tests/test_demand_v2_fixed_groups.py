import copy
import unittest
from collections import Counter
from sb_mexico.demand_v2.fixed_groups import resolve
from sb_mexico.demand_v2.cohorts import settings
from sb_mexico.demand_v2.allocation import allocate


def point(id,budget,lon=0,zone=0,component=None,special=False):
    return dict(id=id,location=[lon,20],zone=zone,road_component=component,municipality='M',
                commuters=budget,pea_15ymas=budget,population=budget*2,residents=budget*2,
                jobs=10,attraction=10,is_special=special)


class FixedGroupsTests(unittest.TestCase):
    def test_global_fixed_sizes_and_single_remainder(self):
        original=[point(str(i),b,i*.001) for i,b in enumerate([101,302,530,200,700])]
        snapshot=copy.deepcopy(original)
        for size in [50,100,200]:
            resolved,report=resolve(original,size)
            options=settings({'demand':{'fixed_cohort_size':size}})
            cells,allocation=allocate(resolved,{'cohort_settings':options}, {})
            pieces=[s for c in cells for s in c['cohort_sizes']]
            self.assertEqual(sum(pieces),1833)
            self.assertEqual(Counter(pieces),Counter({size:1833//size,1833%size:1}))
            self.assertEqual(len(pieces),report['expected_cohorts'])
            self.assertEqual(report['remainder_groups'],1)
            self.assertEqual(sum(p['population'] for p in resolved),3666)
            self.assertEqual(original,snapshot)
            self.assertEqual(resolve(original,size),(resolved,report))

    def test_islands_and_road_components_cannot_share_remainders(self):
        points=[point('a',60,zone=1,component=1),point('b',70,zone=2,component=1),point('c',80,zone=1,component=2)]
        result,report=resolve(points,200)
        self.assertEqual(report['remainder_groups'],3)
        self.assertEqual(sum(p['commuters'] for p in result),210)
        for group in report['groups']:
            self.assertEqual(len(group['contributions']),1)

    def test_poi_is_unchanged_and_exception_is_visible(self):
        poi=point('UNI_keep',37,special=True)
        result,report=resolve([poi,point('a',60),point('b',70)],100)
        self.assertEqual(next(p for p in result if p['id']=='UNI_keep'),poi)
        self.assertEqual(report['remainder_groups'],2)

    def test_zero_and_conflicting_total(self):
        result,report=resolve([point('empty',0)],200)
        self.assertEqual(report['expected_cohorts'],0)
        with self.assertRaisesRegex(ValueError,'clear the total count'):
            settings({'demand':{'fixed_cohort_size':200,'cohort_count':12}})

    def test_distant_remainders_stay_local_and_order_is_deterministic(self):
        points=[point('a',23),point('b',24,.01),point('c',29,.3)]
        result,report=resolve(points,50)
        self.assertEqual(report['expected_cohorts'],3)
        self.assertEqual(report['max_displacement_km'],0)
        self.assertEqual(resolve(list(reversed(points)),50),(result,report))
        self.assertEqual(sum(p['commuters'] for p in result),76)

    def test_municipal_boundary_splits_colocated_sources(self):
        a,b=point('a',23),point('b',24)
        b['municipality']='N'
        _,report=resolve([a,b],50)
        self.assertEqual(report['expected_cohorts'],2)
        self.assertEqual(set(report['municipal_representation_delta'].values()),{0})
        self.assertTrue(all(len(g['contributions'])==1 for g in report['groups']))

    def test_radius_boundary_and_chain_do_not_hide_far_contributors(self):
        from sb_mexico.demand_v2.fixed_groups import GEOD
        def at_distance(id,d):
            lon,lat,_=GEOD.fwd(0,20,90,d)
            p=point(id,10,lon); p['location']=[lon,lat]; return p
        a=point('a',10)
        _,r=resolve([a,at_distance('b',499.99),at_distance('c',500.01)],50)
        self.assertEqual(r['expected_cohorts'],2)
        self.assertLessEqual(r['max_displacement_km'],.5)
        _,r=resolve([a,at_distance('b',400),at_distance('c',800)],50)
        self.assertEqual(r['expected_cohorts'],2)
        self.assertLessEqual(r['max_displacement_km'],.5)
        self.assertEqual(sum(g['commuters'] for g in r['groups']),30)


if __name__=='__main__': unittest.main()
