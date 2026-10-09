import unittest
import numpy as np
from sb_mexico.demand_v2.cohorts import settings, integerize


class CohortResolutionTests(unittest.TestCase):
    def assign(self,target=150,count=None,budgets=(300,600),maximum=200,minimum=25):
        origins=[dict(id=str(i),commuters=b) for i,b in enumerate(budgets)]
        destinations=[dict(id='d'+str(i)) for i in range(100)]
        rows=np.repeat(np.arange(len(origins)),100); cols=np.tile(np.arange(100),len(origins))
        flow=np.repeat(np.asarray(budgets)/100,100)
        options=settings(dict(macroeconomics=dict(min_pop_size=minimum,target_pop_size=target,max_pop_size=maximum),
                              demand=dict(cohort_count=count)))
        return integerize(flow,rows,cols,origins,destinations,np.ones(len(rows)),options)

    def test_wizard_target_controls_count_before_od(self):
        small=self.assign(target=75); large=self.assign(target=150)
        self.assertEqual(small[2]['actual_count'],12)
        self.assertEqual(large[2]['actual_count'],6)
        self.assertEqual(sum(c['size'] for c in large[0]),900)
        self.assertLessEqual(len(large[0]),6)
        for cells,flow,report in (small,large):
            self.assertEqual(sum(len(c['cohort_sizes']) for c in cells),report['actual_count'])
            self.assertEqual(flow.sum(),900)
            for c in cells: self.assertEqual(sum(c['cohort_sizes']),c['size'])

    def test_explicit_total_is_exact_and_deterministic(self):
        a=self.assign(count=11); b=self.assign(count=11)
        self.assertEqual(a[0],b[0]); self.assertEqual(a[2]['actual_count'],11)
        self.assertEqual(sum(c['size'] for c in a[0] if c['origin']=='0'),300)
        self.assertEqual(sum(c['size'] for c in a[0] if c['origin']=='1'),600)
        self.assertTrue(all(0<s<=200 for c in a[0] for s in c['cohort_sizes']))

    def test_explicit_total_above_preferred_minimum_preserves_people(self):
        a=self.assign(count=900)
        self.assertEqual(a[2]['actual_count'],900)
        self.assertEqual(a[2]['below_preferred_minimum'],900)

    def test_impossible_counts_report_bounds_without_dropping_origins(self):
        for count in (1,901):
            with self.assertRaisesRegex(ValueError,'minimum 5.*maximum 900'):
                self.assign(count=count)

    def test_small_origins_and_rigid_sizes_keep_remainders(self):
        cells,_,report=self.assign(target=200,budgets=(1,201),minimum=200)
        self.assertEqual(report['actual_count'],3)
        self.assertEqual(sum(c['size'] for c in cells),202)
        self.assertTrue(any(c['size']==1 for c in cells))
        self.assertEqual(sorted(s for c in cells for s in c['cohort_sizes']),[1,1,200])

    def test_invalid_configuration_is_not_silently_ignored(self):
        for count in (0,-1,True,2.5):
            with self.assertRaisesRegex(ValueError,'cohort_count'):
                settings({'demand':{'cohort_count':count}})
        with self.assertRaisesRegex(ValueError,'min_pop_size <= target_pop_size'):
            settings({'macroeconomics':{'min_pop_size':25,'target_pop_size':200,'max_pop_size':60}})


if __name__=='__main__': unittest.main()
