# -*- coding: utf-8 -*-
"""
Tests para Deteccion y Resolucion de Duplicados de Toponimia Urbana
y Normalizacion de Escalas Cartograficas (Subway Builder Mexico).
"""

import unittest
from sb_mexico.toponymy_homogenizer import (
    normalize_place_scale,
    find_exact_and_fuzzy_duplicates,
    resolve_duplicate_groups
)


class TestToponymyDuplicates(unittest.TestCase):

    def test_normalize_place_scale(self):
        self.assertEqual(normalize_place_scale('city'), 'large')
        self.assertEqual(normalize_place_scale('town'), 'large')
        self.assertEqual(normalize_place_scale('borough'), 'large')
        self.assertEqual(normalize_place_scale('large'), 'large')

        self.assertEqual(normalize_place_scale('suburb'), 'medium')
        self.assertEqual(normalize_place_scale('quarter'), 'medium')
        self.assertEqual(normalize_place_scale(''), 'medium')
        self.assertEqual(normalize_place_scale('unknown'), 'medium')

        self.assertEqual(normalize_place_scale('neighbourhood'), 'small')
        self.assertEqual(normalize_place_scale('neighborhood'), 'small')
        self.assertEqual(normalize_place_scale('village'), 'small')
        self.assertEqual(normalize_place_scale('isolated_dwelling'), 'small')
        self.assertEqual(normalize_place_scale('small'), 'small')

    def test_find_exact_duplicates(self):
        places = [
            {
                'name': 'La Toscana',
                'loc': [-86.8500, 21.1600],
                'type': 'suburb',
                'source': 'OSM_NODE'
            },
            {
                'name': 'Supermanzana 15',
                'loc': [-86.8300, 21.1500],
                'type': 'suburb',
                'source': 'OSM_NODE'
            },
            {
                'name': 'La Toscana',
                'loc': [-86.8510, 21.1605],
                'type': 'suburb',
                'source': 'DENUE_ESTABLISHMENT_CLUSTER'
            }
        ]

        result = find_exact_and_fuzzy_duplicates(places, distance_threshold_m=2000.0)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['total_duplicate_groups'], 1)
        self.assertEqual(result['total_redundant'], 1)

        group = result['groups'][0]
        self.assertEqual(group['title'], 'La Toscana')
        self.assertEqual(group['match_type'], 'EXACT')
        self.assertEqual(group['candidates_count'], 2)

        winner_cand = [c for c in group['candidates'] if c['is_recommended']][0]
        self.assertEqual(winner_cand['source'], 'OSM_NODE')
        self.assertEqual(winner_cand['original_index'], 0)

    def test_find_fuzzy_proximity_duplicates(self):
        places = [
            {
                'name': 'Cancun Centro',
                'loc': [-86.8475, 21.1619],
                'type': 'city',
                'source': 'OSM_NODE'
            },
            {
                'name': 'Cancun',
                'loc': [-86.8490, 21.1630],
                'type': 'city',
                'source': 'OSM_POLYGON'
            },
            {
                'name': 'Playa del Carmen',
                'loc': [-87.0739, 20.6296],
                'type': 'town',
                'source': 'OSM_NODE'
            }
        ]

        result = find_exact_and_fuzzy_duplicates(places, distance_threshold_m=2500.0)
        self.assertEqual(result['total_duplicate_groups'], 1)
        group = result['groups'][0]
        self.assertEqual(group['candidates_count'], 2)
        self.assertEqual(group['recommended_index'], 0)

    def test_resolve_duplicate_groups(self):
        places = [
            {'name': 'La Toscana', 'loc': [-86.8500, 21.1600], 'source': 'OSM_NODE'},
            {'name': 'Colonia Centro', 'loc': [-86.8300, 21.1500], 'source': 'OSM_NODE'},
            {'name': 'La Toscana', 'loc': [-86.8510, 21.1605], 'source': 'DENUE'},
            {'name': 'Bonfil', 'loc': [-86.8600, 21.0900], 'source': 'OSM_NODE'}
        ]

        resolutions = [
            {
                'group_id': 'dup_1',
                'selected_index': 0,
                'candidates': [
                    {'original_index': 0},
                    {'original_index': 2}
                ]
            }
        ]

        resolved = resolve_duplicate_groups(places, resolutions)
        self.assertEqual(resolved['status'], 'ok')
        self.assertEqual(resolved['total_initial'], 4)
        self.assertEqual(resolved['total_kept'], 3)
        self.assertEqual(resolved['total_pruned'], 1)
        self.assertEqual(resolved['pruned_indices'], [2])

        kept_names = [p['name'] for p in resolved['places']]
        self.assertEqual(kept_names, ['La Toscana', 'Colonia Centro', 'Bonfil'])
        self.assertEqual(resolved['places'][0]['source'], 'OSM_NODE')


if __name__ == '__main__':
    unittest.main()
