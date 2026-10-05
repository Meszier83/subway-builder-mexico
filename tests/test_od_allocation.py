import unittest
from collections import Counter
import numpy as np
from sb_mexico.od_allocation import (
    ODAllocationError, integer_transport, finalize_integer_od, validate_integer_margins)


class IntegerODTests(unittest.TestCase):
    def test_joint_rounding_respects_tight_support_cut(self):
        # Largest-remainder rounding would give d0=0, d1=1, d2=1.
        # o0 can serve only d0; support-aware rounding must give d0 its ceiling.
        allowed = np.array([[True, False, False], [False, True, True]])
        pairs, targets, _ = integer_transport(['o0', 'o1'], [1, 1],
            ['d0', 'd1', 'd2'], [.4, .8, .8], Counter(), allowed,
            np.ones((2, 3)))
        self.assertEqual(pairs['o0', 'd0'], 1)
        self.assertEqual(targets['d0'], 1)
        self.assertEqual(sum(targets.values()), 2)

    def test_repair_keeps_exact_rows_and_feasible_proposal_mass(self):
        proposal = Counter({('a', 'c'): 8, ('b', 'd'): 2})
        args = (['a', 'b'], [8, 2], ['c', 'd'], [5., 5.], proposal,
                np.ones((2, 2), dtype=bool), np.array([[1., 2.], [2., 1.]]))
        pairs, targets, _ = integer_transport(*args)
        self.assertEqual(pairs, {('a', 'c'): 5, ('a', 'd'): 3, ('b', 'd'): 2})
        self.assertEqual(targets, {'c': 5, 'd': 5})
        self.assertEqual(integer_transport(*args)[0], pairs)

    def test_true_infeasibility_reports_complete_support_deficit(self):
        with self.assertRaisesRegex(ODAllocationError, 'Complete support.*deficit=1'):
            integer_transport(['a'], [2], ['c', 'd'], [1., 1.], Counter(),
                np.array([[True, False]]), np.ones((1, 2)))

    def test_lower_target_cut_can_fail_even_with_sufficient_upper_capacity(self):
        with self.assertRaisesRegex(ODAllocationError, 'Complete support'):
            integer_transport(['a', 'b'], [3, 1], ['c', 'd'], [1., 3.], Counter(),
                np.array([[True, False], [True, True]]), np.ones((2, 2)))

    def test_one_person_pair_survives_packing_and_detects_later_drift(self):
        initial = [dict(id='a', location=[0., 0.], pea_15ymas=1),
                   dict(id='c', location=[.01, 0.], jobs=1)]
        proposal = [dict(id='p', residenceId='a', jobId='c', size=1)]
        reports = [dict(destination_ids=['c'], effective_destination_targets=[1.])]
        pops, authority = finalize_integer_od(initial, initial, proposal,
            {'a': 'a', 'c': 'c'}, reports, 50, max_pop_size=60, min_pop_size=10)
        self.assertEqual(pops[0]['size'], 1)
        self.assertEqual(authority['small_cohorts'], 1)
        validate_integer_margins(pops, authority)
        pops[0]['size'] = 2
        with self.assertRaisesRegex(ODAllocationError, 'margins changed'):
            validate_integer_margins(pops, authority)

    def test_self_only_support_fails_instead_of_inventing_trip(self):
        point = dict(id='a', location=[0., 0.], pea_15ymas=1, jobs=1)
        with self.assertRaises(ODAllocationError):
            finalize_integer_od([point], [point], [], {'a': 'a'},
                [dict(destination_ids=['a'], effective_destination_targets=[1.])], 50)

    def test_large_pair_packing_preserves_mass_and_hard_maximum(self):
        initial = [dict(id='a', location=[0., 0.], pea_15ymas=80),
                   dict(id='c', location=[.01, 0.], jobs=1)]
        pops, authority = finalize_integer_od(initial, initial,
            [dict(residenceId='a', jobId='c', size=80)], {'a': 'a', 'c': 'c'},
            [dict(destination_ids=['c'], effective_destination_targets=[80.])], 50,
            max_pop_size=30, min_pop_size=10)
        self.assertEqual([pop['size'] for pop in pops], [27, 27, 26])
        with self.assertRaisesRegex(ODAllocationError, 'max_pop_size'):
            validate_integer_margins([dict(residenceId='a', jobId='c', size=80)], authority)

    def test_final_id_self_edge_is_removed_while_budget_is_preserved(self):
        initial = [dict(id='a', location=[0., 0.], pea_15ymas=2),
                   dict(id='b', location=[.001, 0.], jobs=1),
                   dict(id='c', location=[.01, 0.], jobs=1)]
        final = [initial[0], initial[2]]
        proposal = [dict(id='p', residenceId='a', jobId='a', size=2)]
        reports = [dict(destination_ids=['c'], effective_destination_targets=[2.])]
        pops, authority = finalize_integer_od(initial, final, proposal,
            {'a': 'a', 'b': 'a', 'c': 'c'}, reports, 50)
        self.assertEqual([(p['residenceId'], p['jobId'], p['size']) for p in pops], [('a', 'c', 2)])
        self.assertEqual(authority['origin_budgets']['a'], 2)

    def test_limited_special_quota_and_pair_are_preserved(self):
        initial = [dict(id='a', location=[0., 0.], pea_15ymas=2),
                   dict(id='special', location=[.01, 0.], jobs=3, is_special=True)]
        proposal = [dict(id='p', residenceId='a', jobId='special', size=2)]
        pops, authority = finalize_integer_od(initial, initial, proposal,
            {'a': 'a', 'special': 'special'}, [], 50)
        self.assertEqual(authority['special_quotas']['special'], {'requested': 3, 'realized': 2})
        self.assertEqual(authority['pair_targets'], [['a', 'special', 2]])
        validate_integer_margins(pops, authority)

    def test_pair_swaps_fail_even_when_both_margins_match(self):
        authority = dict(origin_budgets={'a': 1, 'b': 1}, destination_targets={'c': 1, 'd': 1},
                         pair_targets=[['a', 'c', 1], ['b', 'd', 1]])
        with self.assertRaisesRegex(ODAllocationError, 'pair masses changed'):
            validate_integer_margins([dict(residenceId='a', jobId='d', size=1),
                                      dict(residenceId='b', jobId='c', size=1)], authority)


if __name__ == '__main__':
    unittest.main()
