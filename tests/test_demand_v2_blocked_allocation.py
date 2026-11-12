import unittest
from collections import Counter
from unittest.mock import patch

import numpy as np

from sb_mexico.demand_v2.allocation import allocate, support, validate_origin_support
from sb_mexico.demand_v2.blocked_cohorts import residual_transport
from sb_mexico.demand_v2.range_transport import RangeGraph


def point(name, longitude, commuters=0, attraction=0, municipality='A', zone=0, component=1):
    return dict(id=name, location=[longitude, 19.4], commuters=commuters, attraction=attraction,
                municipality=municipality, zone=zone, road_component=component)


class BlockedAllocationTests(unittest.TestCase):
    def test_exact_allocation_matches_dense_continuous_contract(self):
        points = [
            point('a', -99.2, 200), point('b', -99.15, 400), point('c', -99.1, 200, municipality='B'),
            point('island', -99.17, 200, zone=2),
            point('x', -99.2, attraction=15), point('y', -99.13, attraction=40, municipality='B'),
            point('z', -99.05, attraction=45, municipality='B'),
            point('island_job', -99.16, attraction=10, zone=2),
            point('unreachable', -99.15, attraction=100, component=3),
        ]
        params = dict(fixed_cohort_size=200, exact_cohort_size=True, max_distance_km=20.,
                      _forbidden_pairs={('c', 'y')})
        mobility = dict(flows=[
            dict(origin='A', destination='A', training_weight=20., validation_weight=6., weight=26.,
                 effective_sample_size=75.),
            dict(origin='A', destination='B', training_weight=80., validation_weight=24., weight=104.,
                 effective_sample_size=80.),
        ])
        _, dense = allocate(points, params, mobility)
        with patch('sb_mexico.demand_v2.blocked_allocation.MAX_DENSE_PAIRS', 1), \
                patch('sb_mexico.demand_v2.blocked_allocation.BLOCK_PAIRS', 5):
            cells, blocked = allocate(points, params, mobility)
            self.assertEqual(cells, allocate(points, params, mobility)[0])
        self.assertEqual(blocked['support_edges'], dense['support_edges'])
        self.assertEqual(blocked['block_max_pairs'], 5)
        self.assertAlmostEqual(blocked['objective'], dense['objective'], places=8)
        self.assertAlmostEqual(blocked['validation']['conditional_kl'], dense['validation']['conditional_kl'], places=7)
        self.assertEqual(blocked['destination_profile']['targets'], dense['destination_profile']['targets'])
        self.assertLess(blocked['destination_profile']['balance']['max_relative_error'], 1e-8)
        self.assertEqual(blocked['cohorts']['actual_count'], 5)
        budgets = Counter()
        origins, destinations, rows, cols, _ = support(points, params)
        permitted = {(origins[i]['id'], destinations[j]['id']) for i, j in zip(rows, cols)}
        for cell in cells:
            self.assertIn((cell['origin'], cell['destination']), permitted)
            self.assertEqual(set(cell['cohort_sizes']), {200})
            budgets[cell['origin']] += cell['size']
        self.assertEqual(budgets, Counter({p['id']: p['commuters'] for p in origins}))
        self.assertLessEqual(blocked['cohorts']['destination_rounding'][0]['max_destination_count_error'], 1.)

    def test_disconnected_precheck_rejects_the_same_origins(self):
        points = [point('a', -99.2, 200, component=None),
                  point('coincident', -99.2, attraction=100),
                  point('blocked', -99.19, attraction=100),
                  point('other_zone', -99.2, attraction=100, zone=1),
                  point('far', -98.9, attraction=100)]
        params = dict(max_distance_km=2., _forbidden_pairs={('a', 'blocked')})
        for check in (support, validate_origin_support):
            with self.assertRaisesRegex(ValueError, 'Origin without support: a'):
                check(points, params)
            check(points+[point('near', -99.195, attraction=1, component=2)], params)

    def test_compressed_feasibility_rejects_connected_capacity_deficit(self):
        points = [point('a', -99.2, 200), point('b', -99.1, 200),
                  point('low', -99.21, attraction=1), point('high', -99.11, attraction=99)]
        params = dict(fixed_cohort_size=200, exact_cohort_size=True, _forbidden_pairs={('a', 'high')})
        with patch('sb_mexico.demand_v2.blocked_allocation.MAX_DENSE_PAIRS', 1):
            with self.assertRaisesRegex(ValueError, 'infeasible on complete permitted support'):
                allocate(points, params, {})

    def test_range_transport_repairs_seed_through_shared_subtree(self):
        graph = RangeGraph(3, 4)
        graph.add_row(0, np.array([0, 1, 2, 3]))
        graph.add_row(1, np.array([0, 1]))
        graph.add_row(2, np.array([0]))
        proposal = {(0, 0): 1, (1, 0): 1, (2, 0): 1}
        (assigned, arrivals), deficit = residual_transport(
            graph, np.ones(3, dtype=int), np.array([1., 1., .5, .5]), proposal)
        self.assertEqual(deficit, 0)
        self.assertEqual(assigned[2, 0], 1)
        self.assertEqual(assigned[1, 1], 1)
        self.assertEqual(assigned[0, 2]+assigned[0, 3], 1)
        self.assertEqual(int(arrivals.sum()), 3)
