"""Consolidation must preserve positive trips without collapsing their endpoints."""
import random
import unittest
from unittest.mock import patch

from sb_mexico.gravity import (cluster_demand_points, consolidate_small_pops,
                               merge_identical_commutes, sync_demand_points_and_pops)
from sb_mexico.pipeline import validate_exported_commutes, execute_pipeline
import test_residential_employment as census_fixtures


def point(key, offset=0, **extra):
    return dict(id=key, location=[-86.85+offset,21.15], residents=0, jobs=0, popIds=[], **extra)


def trip(residence, job, size=100):
    return dict(id=residence+'_'+job,residenceId=residence,jobId=job,size=size,
                drivingSeconds=180,drivingDistance=2000)


class SelfCommuteTests(unittest.TestCase):
    def test_direct_and_reciprocal_trips_keep_distinct_endpoints(self):
        for pops in ([trip('A','B')], [trip('A','B'),trip('B','A',40)]):
            points = [point('A'),point('B',.0005)]
            merged, flows = cluster_demand_points(points,pops)
            self.assertEqual({p['id'] for p in merged},{'A','B'})
            self.assertTrue(all(p['residenceId'] != p['jobId'] for p in flows))
            self.assertEqual(sum(p['size'] for p in flows),sum(p['size'] for p in pops))
            self.assertEqual(points[0]['location'],[-86.85,21.15])
            self.assertEqual(pops[0]['residenceId'],'A')

    def test_connection_to_nonrepresentative_member_blocks_merge(self):
        points = [point('A'),point('B',.0003),point('C',.0006),point('X',.02)]
        pops = [trip('A','X',1000),trip('B','C',10),trip('B','X',20)]
        merged, flows = cluster_demand_points(points,pops)
        # B can join A, but C cannot: B->C must survive even though A->C is absent.
        bc = next(p for p in flows if p['id']=='B_C')
        self.assertEqual(bc['residenceId'],'A')
        self.assertEqual(bc['jobId'],'C')
        self.assertEqual(len(merged),3)
        self.assertEqual(sum(p['size'] for p in flows),1030)

    def test_safe_merge_and_zero_size_connection_do_not_block(self):
        points = [point('A'),point('B',.0005),point('X',.02)]
        pops = [trip('A','X'),trip('B','X',50),trip('A','B',0)]
        merged, flows = cluster_demand_points(points,pops)
        self.assertEqual(len(merged),2)
        positive = [p for p in flows if p['size']>0]
        self.assertEqual(len({p['residenceId'] for p in positive}),1)
        self.assertEqual(sum(p['size'] for p in flows),150)

    def test_special_poi_and_isolated_zone_protection_remain(self):
        points = [point('A'),point('B',.0005),point('AIR_Test',.0002,is_special=True)]
        merged, flows = cluster_demand_points(points,[trip('A','AIR_Test'),trip('B','AIR_Test')])
        special = next(p for p in merged if p['id']=='AIR_Test')
        self.assertEqual(special['location'],points[-1]['location'])
        self.assertTrue(special['is_special'])
        points = [point('A'),point('B',.0005),point('X',.02)]
        zone = dict(id='west',bbox=[-86.851,21.149,-86.8498,21.151])
        merged, _ = cluster_demand_points(points,[trip('A','X'),trip('B','X')],isolated_zones=[zone])
        self.assertEqual(len(merged),3)

    def test_varied_graphs_never_internalize_positive_commutes(self):
        rng = random.Random(42)
        points = [point(str(i),i*.00015) for i in range(6)]
        for _ in range(25):
            pops = [trip(str(i),str(j),rng.randint(1,200)) for i in range(6) for j in range(6)
                    if i!=j and rng.random()<.25]
            _, flows = cluster_demand_points(points,pops)
            self.assertEqual(sum(p['size'] for p in flows),sum(p['size'] for p in pops))
            self.assertTrue(all(p['residenceId']!=p['jobId'] for p in flows))

    def test_complete_consolidation_sequence_preserves_mass_and_valid_pairs(self):
        points = [point('A'),point('B',.0005),point('X',.02)]
        original = [trip('A','B',100),trip('A','X',20),trip('B','X',30)]
        points, pops = cluster_demand_points(points,original)
        points, pops = consolidate_small_pops(points,pops,max_pop_size=200)
        pops = merge_identical_commutes(pops,max_pop_size=200)
        points, pops = sync_demand_points_and_pops(points,pops)
        validate_exported_commutes(pops,points,150)
        self.assertEqual(sum(p['size'] for p in pops),150)

    def test_export_guard_rejects_loops_missing_endpoints_budget_and_display(self):
        points, pops = sync_demand_points_and_pops([point('A'),point('B')],[trip('A','B')])
        validate_exported_commutes(pops,points,100)
        loop_points, loop_pops = sync_demand_points_and_pops([point('A')],[trip('A','A')])
        with self.assertRaisesRegex(ValueError,'self-commute A->A'): validate_exported_commutes(loop_pops,loop_points,100)
        with self.assertRaisesRegex(ValueError,'missing endpoint'): validate_exported_commutes(pops,points[:1],100)
        with self.assertRaisesRegex(ValueError,'Commuter budget'): validate_exported_commutes(pops,points,99)
        points[0]['residents'] = 99
        with self.assertRaisesRegex(ValueError,'displayed residents'): validate_exported_commutes(pops,points,100)

    def test_unserved_special_display_policy_is_preserved(self):
        points, pops = sync_demand_points_and_pops([point('A'),point('B')],[trip('A','B')])
        special = point('AIR_Test',is_special=True); special['jobs']=50
        validate_exported_commutes(pops,points+[special],100)

    def test_preexisting_bad_trip_is_rejected_not_dropped_or_reassigned(self):
        points, pops = cluster_demand_points([point('A')],[trip('A','A',40)])
        points, pops = sync_demand_points_and_pops(points,pops)
        self.assertEqual(sum(p['size'] for p in pops),40)
        with self.assertRaisesRegex(ValueError,'self-commute'): validate_exported_commutes(pops,points,40)

    def test_production_blocks_a_self_commute_before_routing_or_export(self):
        f = census_fixtures.CensusEmploymentTests(); f.setUp(); self.addCleanup(f.doCleanups)
        path, _, _ = f.fixture_config()
        def corrupt_cluster(points,pops,**kwargs):
            broken = [dict(p,jobId=p['residenceId']) for p in pops]
            return points,broken
        output = f.root/'bad_output'
        with patch('sb_mexico.pipeline.ROOT_DIR',str(f.root)), patch('sb_mexico.pipeline.console'), \
             patch('sb_mexico.pipeline.cluster_demand_points',side_effect=corrupt_cluster), \
             patch('sb_mexico.pipeline.is_docker_available') as routing:
            with self.assertRaisesRegex(ValueError,'self-commute'):
                execute_pipeline(str(path),skip_map=True,output_dir=str(output))
            routing.assert_not_called()
        self.assertFalse((output/'demand_data.json').exists())


if __name__=='__main__': unittest.main()
