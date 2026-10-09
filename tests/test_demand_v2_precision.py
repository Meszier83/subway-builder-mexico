import unittest
from collections import Counter
import numpy as np
from sb_mexico.demand_v2.allocation import allocate
from sb_mexico.demand_v2.cohorts import integerize,settings
from sb_mexico.demand_v2.engine import validate_export
from sb_mexico.demand_v2.export_sites import collapse
from sb_mexico.demand_v2.destination_cohorts import feedback_targets
from sb_mexico.demand_v2.workplace_profile import targets_for_support,check_feasibility


def point(id,lon,budget=0,attraction=0,zone=0,special=False):
    return dict(id=id,location=[lon,21.],commuters=budget,attraction=attraction,
                zone=zone,municipality='M',is_special=special)


class PrecisionTests(unittest.TestCase):
    def test_remote_jobs_survive_nearby_small_destinations(self):
        points=[point('origin',-86.95,280)]
        points.extend(point(str(i),-86.94+i*.00001,attraction=1) for i in range(140))
        points.append(point('remote',-86.75,attraction=140))
        cells,report=allocate(points,{'max_pop_size':1,'target_pop_size':1,'beta':.12},{})
        arrivals=Counter()
        for c in cells:arrivals[c['destination']]+=c['size']
        self.assertEqual(arrivals['remote'],140)
        self.assertEqual(report['support_edges'],141)
        self.assertLess(report['destination_profile']['balance']['max_relative_error'],1e-8)

    def test_complete_support_still_rejects_infeasible_job_shape(self):
        # Both origins/destinations share a connected territory, but the
        # second destination's weight exceeds its only permissible supply.
        points=[point('small',-86.9,10),point('large',-86.8,90),
                point('a',-86.7,attraction=1),point('b',-86.6,attraction=9)]
        with self.assertRaisesRegex(ValueError,'infeasible on complete permitted support'):
            allocate(points,{'_forbidden_pairs':{('large','b')}},{})

    def test_disconnected_territory_check_rounds_locally(self):
        origins=[point('a',-86.9,7,zone=0),point('b',-86.8,3,zone=1)]
        destinations=[point('x',-86.7,attraction=1),point('y',-86.6,attraction=2),
                      point('z',-86.5,attraction=1,zone=1),point('w',-86.4,attraction=6,zone=1)]
        rows=np.array([0,0,1,1]);cols=np.array([0,1,2,3])
        targets,report=targets_for_support(origins,destinations,rows,cols)
        check_feasibility(np.array([7,3]),targets,rows,cols)
        self.assertAlmostEqual(targets[:2].sum(),7)
        self.assertAlmostEqual(targets[2:].sum(),3)

    def test_shared_cohort_rounding_preserves_small_job_weights(self):
        origins=[dict(id=str(i),commuters=50,municipality='M') for i in range(200)]
        destinations=[dict(id='large',municipality='M'),dict(id='small',municipality='M')]
        rows=np.repeat(np.arange(200),2);cols=np.tile([0,1],200)
        cells,_,report=integerize(np.tile([49.5,.5],200),rows,cols,origins,destinations,np.ones(400),
                                  settings({'demand':{'fixed_cohort_size':50}}))
        self.assertEqual(sum(c['size'] for c in cells if c['destination']=='small'),100)
        self.assertEqual(report['actual_count'],200)
        self.assertLessEqual(report['max_destination_rounding_error_people'],50)

    def test_colocated_game_sites_preserve_statistical_cells_and_routes(self):
        model=[point('home1',-86.9,7),point('home2',-86.9,3),point('work',-86.9,attraction=10),
               point('other',-86.8,5,10)]
        cells=[dict(origin='home1',destination='other',size=7),dict(origin='home2',destination='other',size=3),
               dict(origin='other',destination='work',size=5)]
        pops=[dict(id=str(i),residenceId=c['origin'],jobId=c['destination'],size=c['size'],
                   drivingDistance=250.,drivingSeconds=50.) for i,c in enumerate(cells)]
        points=[dict(p,residents=p['commuters'],jobs=5 if p['id']=='work' else 10 if p['id']=='other' else 0,
                     popIds=[]) for p in model]
        sites,export,mapping,report=collapse(points,pops,model)
        validate_export(model,cells,sites,export,50,mapping)
        self.assertEqual(len(sites),2)
        self.assertEqual(mapping['home1'],'work')
        self.assertEqual(report['remaining_colocated_locations'],0)
        self.assertEqual(pops[0]['residenceId'],'home1')
        self.assertEqual([(p['size'],p['drivingDistance'],p['drivingSeconds']) for p in export],
                         [(p['size'],p['drivingDistance'],p['drivingSeconds']) for p in pops])
        self.assertTrue(all(set(p['popIds'])=={q['id'] for q in export if p['id'] in (q['residenceId'],q['jobId'])}
                            for p in sites))
        bad=dict(mapping,home1='other')
        with self.assertRaisesRegex(ValueError,'moved a statistical point'):
            validate_export(model,cells,sites,export,50,bad)

    def test_rounding_errors_do_not_accumulate_across_sizes(self):
        origins=[dict(id=str(s),commuters=50+s,municipality='M') for s in range(1,50)]
        destinations=[dict(id='a_small',municipality='M'),dict(id='z_large',municipality='M')]
        rows=np.repeat(np.arange(49),2);cols=np.tile([0,1],49)
        flow=np.array([v for o in origins for v in (o['commuters']*.009,o['commuters']*.991)])
        cells,_,report=integerize(flow,rows,cols,origins,destinations,np.ones(len(flow)),
                                  settings({'demand':{'fixed_cohort_size':50}}))
        actual=sum(c['size'] for c in cells if c['destination']=='a_small')
        self.assertLessEqual(abs(actual-flow[cols==0].sum()),50)
        self.assertEqual(report['actual_count'],98)

    def test_feedback_can_zero_overfilled_jobs_and_retains_forced_rows(self):
        supplies=np.ones(2,dtype=int)
        kernel=np.array([[.4,.3,.3],[.8,.1,.1]])
        adjusted,_=feedback_targets(kernel.sum(axis=0),np.array([-2.,1.,1.]),1,
                                    kernel>0,supplies,kernel.copy())
        self.assertEqual(adjusted[0],0.)
        np.testing.assert_allclose(adjusted[1:],[1.,1.],atol=1e-6)
        kernel=np.array([[1.,0.,0.],[.2,.3,.5]])
        adjusted,_=feedback_targets(kernel.sum(axis=0),np.array([-2.,1.,1.]),1,
                                    kernel>0,supplies,kernel.copy())
        self.assertEqual(adjusted[0],1.)
        self.assertAlmostEqual(adjusted.sum(),2.)

    def test_colocated_special_ids_and_different_territories_stay_separate(self):
        model=[point('ordinary',-86.9,attraction=10),point('UNI_test',-86.9,attraction=10,special=True),
               point('other_zone',-86.9,attraction=10,zone=1)]
        points=[dict(p,residents=0,jobs=10,popIds=[]) for p in model]
        sites,_,mapping,_=collapse(points,[],model)
        self.assertEqual(len(sites),3)
        self.assertEqual(mapping['UNI_test'],'UNI_test')

    def test_structurally_unreachable_attraction_is_disclosed(self):
        points=[point('o',-86.9,7),point('same',-86.9,attraction=100),point('work',-86.8,attraction=1)]
        cells,report=allocate(points,{}, {})
        self.assertEqual([(c['destination'],c['size']) for c in cells],[('work',7)])
        self.assertEqual(report['destination_profile']['unreachable_attraction'][0]['id'],'same')
