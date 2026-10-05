"""Regression fixtures for the bounded map-demand roadmap."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import numpy as np
import pandas as pd

from sb_mexico.gravity import (
    build_demand_grid, cluster_demand_points, consolidate_small_pops, furness_ipfp_balance,
    simulate_gravity_demand,
)
from sb_mexico.inegi import load_cpv_demography, load_denue, parse_conapo_projections, resolve_projection_year


class TestDemandRoadmap(unittest.TestCase):
    def test_conapo_selected_year_ratios_and_visible_nearest_year(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'conapo.csv'
            pd.DataFrame([dict(CLAVE=23005, ANO=y, POB_TOTAL=p)
                          for y, p in [(2020, 100), (2024, 115), (2026, 150)]]).to_csv(path, index=False)
            report = {}
            self.assertEqual(resolve_projection_year(dict(target_year=2024)), 2024)
            self.assertEqual(resolve_projection_year(dict(projection_year=2024, target_year=2026)), 2024)
            self.assertEqual(resolve_projection_year({}), 2026)
            for invalid in (True, 0, 'invalid', 2024.5):
                with self.assertRaises(ValueError):
                    resolve_projection_year(dict(projection_year=invalid))
            factors = parse_conapo_projections(str(path), 2024, True, report)
            self.assertEqual(factors, {'23005': 1.15})
            self.assertEqual(report['effective_year'], 2024)
            factors = parse_conapo_projections(str(path), 2025, True, report)
            self.assertEqual(factors, {'23005': 1.15})
            self.assertEqual(report['year_basis'], 'nearest_available')
            self.assertEqual(report['effective_year'], 2024)
            factors = parse_conapo_projections(str(path), 2026, True, report)
            self.assertEqual(factors, {'23005': 1.5})
            self.assertEqual(report['year_basis'], 'exact')

    def test_conapo_no_year_is_reported_and_manual_factors_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'conapo.csv'
            path.write_text('CLAVE,POB_TOTAL\n23005,150\n')
            report = {}
            self.assertEqual(parse_conapo_projections(str(path), diagnostics=report), {'23005': 150.})
            self.assertIsNone(report['effective_year'])
            self.assertEqual(report['year_basis'], 'unverified')
            cpv = Path(tmp)/'cpv.csv'
            pd.DataFrame([dict(ENTIDAD=23, MUN=5, LOC='0001', AGEB='001A', MZA=1,
                               POBTOT=100, P_15YMAS=80)]).to_csv(cpv, index=False)
            denue = pd.DataFrame([dict(cve_mun_clean='23005', ageb_clean='001A',
                                      mza_clean='1', lon=-86.8, lat=21.1)])
            result = load_cpv_demography(str(cpv), denue,
                dict(min_lon=-87, max_lon=-86, min_lat=21, max_lat=22), .65,
                growth_factors={'23005':1.1}, conapo_projections={'23005':1.5})
            self.assertAlmostEqual(result.pobtot_adj.sum(), 110.)

    def test_pipeline_forwards_non_default_projection_year(self):
        import json
        import yaml
        from sb_mexico.pipeline import execute_pipeline
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Keep total population 100, but give both workplace nodes residents
            # so this year-forwarding fixture has valid non-self OD support.
            pd.DataFrame([dict(ENTIDAD=23, MUN=5, LOC='0001', AGEB='001A', MZA=mza,
                               POBTOT=50, P_15YMAS=40) for mza in (1,2)]).to_csv(root/'cpv.csv', index=False)
            pd.DataFrame([dict(id=100, cve_ent=23, cve_mun=5, ageb='001A', manzana=1,
                               longitud=-86.8, latitud=21.1, per_ocu='11 a 30 personas'),
                          dict(id=101, cve_ent=23, cve_mun=5, ageb='001A', manzana=2,
                               longitud=-86.78, latitud=21.1, per_ocu='11 a 30 personas')]).to_csv(root/'denue.csv', index=False)
            pd.DataFrame([dict(CLAVE=23005, ANO=y, POB_TOTAL=p)
                          for y, p in [(2020,100), (2024,115), (2026,150)]]).to_csv(root/'conapo.csv', index=False)
            cfg = dict(city=dict(code='TST',name='Test',description='Test',bbox=[-87,20,-86,22],
                                 residential_placement='legacy'),
                       data_dir=str(root), macroeconomics=dict(tasa_pea=.65, til_1_state=.45,projection_year=2024,
                                 residential_employment='legacy', workplace_employment='legacy'))
            path = root/'city.yaml'
            path.write_text(yaml.safe_dump(cfg))
            with patch('sb_mexico.pipeline.ROOT_DIR',str(root)), patch('sb_mexico.pipeline.console'), \
                 patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
                execute_pipeline(str(path),skip_map=True,output_dir=str(root/'out'))
            report = json.loads((root/'out/population_projection_report.json').read_text())
            self.assertEqual(report['requested_year'], 2024)
            self.assertEqual(report['effective_year'], 2024)
            self.assertEqual(report['effective_factors'], {'23005':1.15})
            (root/'conapo.csv').write_text('CLAVE,POB_TOTAL\n23005,115\n')
            with patch('sb_mexico.pipeline.ROOT_DIR',str(root)), patch('sb_mexico.pipeline.console'), \
                 patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
                with self.assertRaisesRegex(ValueError, 'source has no year column'):
                    execute_pipeline(str(path),skip_map=True,output_dir=str(root/'unverified'))
                cfg['macroeconomics']['conapo_source_year'] = 2024
                path.write_text(yaml.safe_dump(cfg))
                execute_pipeline(str(path),skip_map=True,output_dir=str(root/'confirmed'))
            confirmed = json.loads((root/'confirmed/population_projection_report.json').read_text())
            self.assertEqual(confirmed['effective_year'], 2024)
            self.assertEqual(confirmed['year_basis'], 'confirmed_source_year')

    def test_balancing_exhaustion_and_zero_targets(self):
        diagnostics = {}
        origins = np.array([900., 100., 0.])
        jobs = np.array([100., 900., 0.])
        probabilities = furness_ipfp_balance(
            origins, jobs, np.array([[1., 50., 5.], [50., 1., 5.], [5., 5., 1.]]),
            max_iter=1, tol=1e-5, diagnostics=diagnostics,
        )
        self.assertEqual(diagnostics['status'], 'iteration_limit')
        self.assertGreater(diagnostics['column_relative_error'], 1e-5)
        self.assertTrue(np.isfinite(probabilities).all())
        np.testing.assert_allclose(probabilities[:2].sum(axis=1), 1.)
        np.testing.assert_array_equal(probabilities[:, 2], 0.)

    def test_balancing_disconnected_support_reports_residual(self):
        diagnostics = {}
        probabilities = furness_ipfp_balance(
            np.array([900., 100.]), np.array([100., 900.]),
            np.array([[1., 100.], [100., 1.]]), max_distance_km=55,
            diagnostics=diagnostics, correct_destination_targets=False,
        )
        self.assertEqual(diagnostics['status'], 'iteration_limit')
        np.testing.assert_allclose(probabilities.sum(axis=1), 1.)

    def test_balancing_checks_final_marginals(self):
        origins = np.array([900., 100.])
        jobs = np.array([100., 900.])
        probabilities = furness_ipfp_balance(
            origins, jobs, np.array([[1., 50.], [50., 1.]]),
            max_iter=500, tol=1e-5,
        )
        flows = origins[:, None] * probabilities
        np.testing.assert_allclose(flows.sum(axis=1), origins, rtol=1e-6)
        np.testing.assert_allclose(flows.sum(axis=0), jobs, rtol=1e-5)

    def test_remote_small_cell_is_retained(self):
        census = pd.DataFrame([
            dict(lon=-86.9, lat=21.1, pobtot_adj=100., pea_real=50.),
            dict(lon=-86.7, lat=21.1, pobtot_adj=5., pea_real=2.),
        ])
        points, _ = build_demand_grid(
            pd.DataFrame(), census, [], gpd.GeoDataFrame(geometry=[], crs=4326),
        )
        self.assertEqual(len(points), 2)
        self.assertEqual(sum(p['residents'] for p in points), 105)
        self.assertEqual(sum(p['pea_15ymas'] for p in points), 52)

    def test_small_cell_keeps_zone_and_avoids_exclusion_centroid(self):
        census = pd.DataFrame([
            dict(lon=-86.8, lat=21.1, pobtot_adj=100., pea_real=50.),
            dict(lon=-86.797, lat=21.1, pobtot_adj=5., pea_real=2.),
        ])
        points, _ = build_demand_grid(
            pd.DataFrame(), census, [], gpd.GeoDataFrame(geometry=[], crs=4326),
            isolated_zones=[dict(bbox=[-86.798, 21.09, -86.796, 21.11])],
        )
        self.assertEqual(len(points), 2)

    def test_small_pop_merge_cannot_chain_beyond_original_cap(self):
        points = [dict(id=str(i), location=[-86.8+i*.004, 21.1]) for i in range(3)]
        points.append(dict(id='job', location=[-86.8, 21.12]))
        pops = [dict(id=str(i), residenceId=str(i), jobId='job', size=s)
                for i, s in enumerate([1, 2, 3])]
        _, result = consolidate_small_pops(
            points, pops, consolidate_max_sizes=[10, 10],
            consolidate_distances=[500, 500], max_merge_distance_m=500,
        )
        self.assertEqual(sum(p['size'] for p in result), 6)
        self.assertGreaterEqual(len(result), 2)

    def test_cluster_does_not_cross_zone(self):
        points = [dict(id='a', location=[-86.8, 21.1]),
                  dict(id='b', location=[-86.799, 21.1])]
        pops = [dict(id='p', residenceId='a', jobId='b', size=20)]
        result, _ = cluster_demand_points(points, pops, isolated_zones=[
            dict(bbox=[-86.7995, 21.09, -86.7985, 21.11]),
        ])
        self.assertEqual(len(result), 2)

    def test_overlapping_denue_files_count_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/'a.csv', Path(tmp)/'b.csv'
            row = dict(id='100', clee='BUSINESS100', latitud=21.1, longitud=-86.8,
                       cve_ent='23', cve_mun='005', per_ocu='6 a 10 personas')
            pd.DataFrame([row]).to_csv(first, index=False)
            pd.DataFrame([row]).to_csv(second, index=False)
            bbox = dict(min_lon=-87, max_lon=-86, min_lat=21, max_lat=22)
            result = load_denue([str(first), str(second)], bbox)
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result.attrs['mun_totals_global']['23005'], 7.75)

    def test_census_duplicates_use_full_locality_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp)/'a.csv', Path(tmp)/'b.csv']
            row = dict(ENTIDAD='23', MUN='005', LOC='0001', AGEB='001A',
                       MZA='001', POBTOT='100', P_15YMAS='80')
            pd.DataFrame([row]).to_csv(paths[0], index=False)
            pd.DataFrame([row, dict(row, LOC='0002')]).to_csv(paths[1], index=False)
            denue = pd.DataFrame([dict(cve_mun_clean='23005', ageb_clean='001A',
                                      mza_clean='1', lon=-86.8, lat=21.1)])
            result = load_cpv_demography(
                list(map(str, paths)), denue,
                dict(min_lon=-87, max_lon=-86, min_lat=21, max_lat=22), .65,
            )
            self.assertEqual(len(result), 2)
            self.assertEqual(result.pobtot_adj.sum(), 200)

    def test_denue_conflicts_fail_but_colocated_businesses_remain(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp)/'a.csv', Path(tmp)/'b.csv'
            row = dict(id='100', latitud=21.1, longitud=-86.8,
                       cve_ent='23', cve_mun='005', per_ocu='6 a 10 personas')
            pd.DataFrame([row, dict(row, id='101')]).to_csv(first, index=False)
            bbox = dict(min_lon=-87, max_lon=-86, min_lat=21, max_lat=22)
            self.assertEqual(len(load_denue(str(first), bbox)), 2)
            pd.DataFrame([dict(row, per_ocu='11 a 30 personas')]).to_csv(second, index=False)
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                load_denue([str(first), str(second)], bbox)

    def test_denue_dedup_precedes_bbox_and_survives_chunk_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'a.csv'
            row = dict(id='100', latitud=23.1, longitud=-86.8,
                       cve_ent='23', cve_mun='005', per_ocu='6 a 10 personas')
            pd.DataFrame([row, row, dict(row, id='101', latitud=21.1)]).to_csv(path, index=False)
            actual_reader = pd.read_csv
            def one_row_chunks(*args, **kwargs):
                kwargs['chunksize'] = 1
                return actual_reader(*args, **kwargs)
            with patch('sb_mexico.inegi.pd.read_csv', side_effect=one_row_chunks):
                result = load_denue(str(path), dict(min_lon=-87, max_lon=-86, min_lat=21, max_lat=22))
            self.assertEqual(len(result), 1)
            self.assertAlmostEqual(result.attrs['mun_totals_global']['23005'], 15.5)
            self.assertEqual(result.attrs['source_identity']['duplicates_removed'], 1)

    def test_exclusion_between_cells_prevents_centroid_merge(self):
        census = pd.DataFrame([
            dict(lon=-86.8, lat=21.1, pobtot_adj=100., pea_real=50.),
            dict(lon=-86.797, lat=21.1, pobtot_adj=5., pea_real=2.),
        ])
        result, _ = build_demand_grid(
            pd.DataFrame(), census, [], gpd.GeoDataFrame(geometry=[], crs=4326),
            exclusion_zones=[dict(bbox=[-86.79995, 21.09, -86.7998, 21.11])],
        )
        self.assertEqual(len(result), 2)

    def test_island_without_local_jobs_does_not_use_mainland(self):
        points = [
            dict(id='home', location=[-86.7, 21.2], residents=100, pea_15ymas=100,
                 jobs=0, popIds=[]),
            dict(id='mainland', location=[-86.8, 21.1], residents=0,
                 pea_15ymas=0, jobs=100, popIds=[]),
        ]
        with self.assertRaisesRegex(ValueError, 'eligible|local|zone'):
            simulate_gravity_demand(points, isolated_zones=[
                dict(bbox=[-86.72, 21.18, -86.68, 21.22]),
            ])


if __name__ == '__main__':
    unittest.main()
