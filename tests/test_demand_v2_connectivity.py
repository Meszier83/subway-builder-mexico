"""False geometry isolation may be removed only with accepted bilateral routes."""
import copy,unittest
from types import SimpleNamespace
from sb_mexico.demand_v2.connectivity import reconcile
from sb_mexico.demand_v2.allocation import support

class ConnectivityTests(unittest.TestCase):
    def points(self):
        return [dict(id='home',location=[0.,0.],zone=0,road_component=2,commuters=27,attraction=0),
                dict(id='work',location=[.01,0.],zone=0,road_component=7,commuters=0,attraction=100)]
    def request(self,provider):return SimpleNamespace(route_provider=provider,config={'macroeconomics':{'max_distance_km':50}})
    def test_bilateral_accepted_routes_restore_support_without_moving_or_adding_people(self):
        points=self.points();before=copy.deepcopy(points);calls=[]
        def provider(pops,all_points,include):
            calls.append(pops);self.assertFalse(include)
            return {(p['residenceId'],p['jobId']):('osrm','accepted') for p in pops}
        report=reconcile(self.request(provider),points)
        self.assertEqual(len(report['unions']),1);self.assertEqual(report['queried_pairs'],2)
        self.assertEqual(points[0]['road_component'],points[1]['road_component'])
        self.assertEqual([p['commuters'] for p in points],[p['commuters'] for p in before])
        self.assertEqual([p['location'] for p in points],[p['location'] for p in before])
        self.assertEqual(len(support(points,{})[2]),1)
    def test_fallback_or_one_direction_cannot_merge_components(self):
        for reverse in (None,('canonical','no_route'),('canonical','snapping_excess')):
            points=self.points()
            records={('home','work'):('osrm','accepted')}
            if reverse:records['work','home']=reverse
            report=reconcile(self.request(lambda *args:records),points)
            self.assertEqual(report['unions'],[])
            with self.assertRaisesRegex(ValueError,'without support'):support(points,{})
    def test_isolated_zone_remains_hard_and_is_not_probed(self):
        points=self.points();points[1]['zone']=1
        def forbidden(*args):self.fail('A cross-zone probe was attempted')
        report=reconcile(self.request(forbidden),points)
        self.assertEqual(report['queried_pairs'],0);self.assertEqual(report['unions'],[])
    def test_already_supported_origin_needs_no_probe(self):
        points=self.points();points[1]['road_component']=2
        def forbidden(*args):self.fail('Supported origin was probed')
        self.assertEqual(reconcile(self.request(forbidden),points)['queried_pairs'],0)
    def test_no_provider_preserves_geometry_components(self):
        points=self.points();before=copy.deepcopy(points)
        self.assertEqual(reconcile(self.request(None),points)['status'],'not_probed')
        self.assertEqual(points,before)

if __name__=='__main__':unittest.main()
