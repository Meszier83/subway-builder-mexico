import os
import tempfile
import unittest
from unittest.mock import patch

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from sb_mexico.inegi import (
    calibrate_denue_employment,
    load_cpv_demography,
    load_denue,
    load_marco_geoestadistico_coords,
    parse_ce2024_municipal,
    parse_conapo_projections,
    parse_enoe_indicators,
)
from sb_mexico.pipeline import build_gravity_input_contract, validate_employment_ledger
from sb_mexico.gravity import build_demand_grid
from sb_mexico.sources import SourceAmbiguityError, manifest_paths, resolve_source_manifest


BBOX = {"min_lon": -87.0, "min_lat": 21.0, "max_lon": -86.7, "max_lat": 21.3}


class TestDataIntegrity(unittest.TestCase):
    @staticmethod
    def _denue_row(clee="", name="Tienda Uno", lon="-86.8", lat="21.1", manzana="001", cve_mun="005"):
        return {
            "clee": clee, "cve_ent": "23", "cve_mun": cve_mun, "ageb": "0001",
            "manzana": manzana, "latitud": lat, "longitud": lon, "nom_estab": name,
            "codigo_act": "461110", "nom_vial": "Avenida Central", "numero_ext": "10",
            "per_ocu": "0 a 5 personas",
        }

    @staticmethod
    def _denominator(cve, employment, municipality=None, vintage=2025):
        return {
            "municipality": municipality or cve,
            "employment": employment,
            "source": f"official complete DENUE {cve}",
            "vintage": vintage,
            "coverage_basis": "complete_municipality",
        }

    @staticmethod
    def _employment_ledger(final_jobs=80):
        return {
            "authoritative_calibrated_employment": 80.0,
            "geographic_bbox_selected_employment": 80.0,
            "removed_by_exclusion_zones": 0.0,
            "removed_by_urban_core": 0.0,
            "after_explicit_removals": 80.0,
            "absorbed_by_special_pois": 0.0,
            "regular_before_affluence": 80.0,
            "affluence_multiplier_delta": 0.0,
            "target_capacity_delta": 0.0,
            "regular_grid_before_rounding": 80.0,
            "grid_rounding_delta": 0.0,
            "regular_grid_employment_expected": 80,
            "poi_capacity_delta": 0.0,
            "special_poi_employment_expected": 0,
            "final_employment_expected": final_jobs,
            "tolerance": 1e-6,
        }

    def test_excluded_project_file_does_not_reenter_from_root(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "data", "demo")
            os.makedirs(project)
            local = os.path.join(project, "cpv_excluded.csv")
            root_copy = os.path.join(root, "data", "cpv_other_city.csv")
            for path in (local, root_copy):
                open(path, "w", encoding="utf-8").close()
            cfg = {"city": {"code": "DEM"}, "data_dir": project, "data_exclusions": [os.path.basename(local)]}
            manifest = resolve_source_manifest(cfg, os.path.join(root, "cities", "demo.yaml"), root)
            self.assertEqual(manifest_paths(manifest, "cpv"), [])

    def test_shared_data_root_cannot_be_a_project_directory(self):
        with tempfile.TemporaryDirectory() as root:
            shared = os.path.join(root, "data")
            os.makedirs(shared)
            cfg = {"city": {"code": "DEM"}, "data_dir": shared}
            with self.assertRaisesRegex(ValueError, "shared national data root"):
                resolve_source_manifest(cfg, os.path.join(root, "cities", "demo.yaml"), root)

    def test_ambiguous_singleton_vintages_fail(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "data", "demo")
            os.makedirs(project)
            for year in (2024, 2025):
                open(os.path.join(project, f"enoe_{year}_trim1.csv"), "w", encoding="utf-8").close()
            cfg = {"city": {"code": "DEM"}, "data_dir": project}
            with self.assertRaises(SourceAmbiguityError):
                resolve_source_manifest(cfg, os.path.join(root, "cities", "demo.yaml"), root)

    def test_multi_file_discovery_is_deterministically_sorted(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "data", "demo")
            os.makedirs(project)
            for name in ("RESAGEBURB_z.csv", "RESAGEBURB_a.csv"):
                open(os.path.join(project, name), "w", encoding="utf-8").close()
            cfg = {"city": {"code": "DEM"}, "data_dir": project}
            manifest = resolve_source_manifest(cfg, os.path.join(root, "cities", "demo.yaml"), root)
            self.assertEqual([os.path.basename(p) for p in manifest_paths(manifest, "cpv")], ["RESAGEBURB_a.csv", "RESAGEBURB_z.csv"])

    def test_duplicate_denue_identity_fails(self):
        frame = pd.DataFrame([
            {"clee": "001", "cve_ent": "23", "cve_mun": "005", "ageb": "0001", "manzana": "001", "latitud": "21.1", "longitud": "-86.8", "per_ocu": "0 a 5 personas"},
            {"clee": "001", "cve_ent": "23", "cve_mun": "005", "ageb": "0001", "manzana": "001", "latitud": "21.1", "longitud": "-86.8", "per_ocu": "0 a 5 personas"},
        ])
        with tempfile.NamedTemporaryFile(suffix="_denue.csv", delete=False) as handle:
            path = handle.name
        try:
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate DENUE"):
                load_denue(path, BBOX)
        finally:
            os.remove(path)

    def test_blank_clee_duplicate_across_files_uses_normalized_composite(self):
        with tempfile.TemporaryDirectory() as directory:
            first = os.path.join(directory, "denue_a.csv")
            second = os.path.join(directory, "denue_b.csv")
            pd.DataFrame([self._denue_row(name="  Tienda   Uno ")]).to_csv(first, index=False)
            pd.DataFrame([self._denue_row(name="tienda uno", lon="-86.800000")]).to_csv(second, index=False)
            with self.assertRaisesRegex(ValueError, "normalized composite identity"):
                load_denue([first, second], BBOX)

    def test_mixed_valid_and_blank_denue_identifiers_cannot_duplicate(self):
        rows = [self._denue_row(clee="ABC-123"), self._denue_row(clee="")]
        with tempfile.NamedTemporaryFile(suffix="_denue.csv", delete=False) as handle:
            path = handle.name
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate DENUE"):
                load_denue(path, BBOX)
        finally:
            os.remove(path)

    def test_distinct_blank_id_establishments_with_different_composite_fields_survive(self):
        rows = [
            self._denue_row(name="Tienda Uno", lon="-86.80", manzana="001"),
            self._denue_row(name="Tienda Uno", lon="-86.81", manzana="002"),
        ]
        with tempfile.NamedTemporaryFile(suffix="_denue.csv", delete=False) as handle:
            path = handle.name
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
            loaded = load_denue(path, BBOX)
            self.assertEqual(len(loaded), 2)
            self.assertTrue(loaded['_logical_identity'].str.startswith('composite:').all())
        finally:
            os.remove(path)

    def test_primary_denue_identifier_normalizes_case_whitespace_and_formatting(self):
        rows = [self._denue_row(clee=" AB- 123 "), self._denue_row(clee="ab123", lon="-86.81")]
        with tempfile.NamedTemporaryFile(suffix="_denue.csv", delete=False) as handle:
            path = handle.name
        try:
            pd.DataFrame(rows).to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate DENUE"):
                load_denue(path, BBOX)
        finally:
            os.remove(path)

    def test_duplicate_cpv_logical_key_fails_and_preserves_zero_padding(self):
        frame = pd.DataFrame([
            {"ENTIDAD": "01", "MUN": "005", "LOC": "0001", "AGEB": "000A", "MZA": "001", "POBTOT": "10", "P_15YMAS": "7"},
            {"ENTIDAD": "01", "MUN": "005", "LOC": "0001", "AGEB": "000A", "MZA": "1", "POBTOT": "10", "P_15YMAS": "7"},
        ])
        denue = pd.DataFrame(columns=['cve_mun_clean', 'ageb_clean', 'mza_clean', 'lon', 'lat'])
        with tempfile.NamedTemporaryFile(suffix="_cpv.csv", delete=False) as handle:
            path = handle.name
        try:
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate CPV"):
                load_cpv_demography(path, denue, BBOX, 0.6)
        finally:
            os.remove(path)

    def test_overlapping_marco_keys_are_collapsed_without_join_multiplication(self):
        layer = gpd.GeoDataFrame({"CVE_ENT": ["01"], "CVE_MUN": ["005"], "CVE_AGEB": ["000A"], "CVE_MZA": ["001"]}, geometry=[Point(-86.8, 21.1)], crs="EPSG:4326")
        with tempfile.TemporaryDirectory() as directory:
            paths = [os.path.join(directory, "mza_a.geojson"), os.path.join(directory, "mza_b.geojson")]
            for path in paths:
                open(path, "w", encoding="utf-8").close()
            with patch("geopandas.read_file", side_effect=[layer.copy(), layer.copy()]):
                mza, _ = load_marco_geoestadistico_coords(paths)
            self.assertEqual(len(mza), 1)
            self.assertEqual(mza.iloc[0]['cve_mun_clean'], "01005")

    def test_unmatched_population_is_reported_and_thresholded(self):
        frame = pd.DataFrame([{"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "1", "POBTOT": "100", "P_15YMAS": "70"}])
        denue = pd.DataFrame(columns=['cve_mun_clean', 'ageb_clean', 'mza_clean', 'lon', 'lat'])
        with tempfile.NamedTemporaryFile(suffix="_cpv.csv", delete=False) as handle:
            path = handle.name
        try:
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Unmatched CPV population"):
                load_cpv_demography(path, denue, BBOX, 0.6, max_unmatched_population_fraction=0.01)
            result = load_cpv_demography(path, denue, BBOX, 0.6, max_unmatched_population_fraction=1.0)
            self.assertEqual(result.attrs['population_ledger']['unmatched_population'], 100.0)
        finally:
            os.remove(path)

    def test_bbox_exclusion_is_reconciled_in_population_ledger(self):
        frame = pd.DataFrame([
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0001", "MZA": "1", "POBTOT": "100", "P_15YMAS": "70"},
            {"ENTIDAD": "23", "MUN": "005", "AGEB": "0002", "MZA": "2", "POBTOT": "50", "P_15YMAS": "35"},
        ])
        denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "ageb_clean": "0001", "mza_clean": "1", "lon": -86.8, "lat": 21.1},
            {"cve_mun_clean": "23005", "ageb_clean": "0002", "mza_clean": "2", "lon": -88.0, "lat": 21.1},
        ])
        with tempfile.NamedTemporaryFile(suffix="_cpv.csv", delete=False) as handle:
            path = handle.name
        try:
            frame.to_csv(path, index=False)
            result = load_cpv_demography(path, denue, BBOX, 0.6)
            ledger = result.attrs['population_ledger']
            self.assertEqual(ledger['projected_population'], 150.0)
            self.assertEqual(ledger['outside_bbox_population'], 50.0)
            self.assertEqual(ledger['population_entering_grid'], 100.0)
        finally:
            os.remove(path)

    def test_enoe_period_ambiguity_requires_explicit_period(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write('Indicador,2024-T1,2024-T2\nTasa de participación,"60,0","65,0"\nTIL1,"40,0","45,0"\n')
            path = handle.name
        try:
            with self.assertRaisesRegex(ValueError, "Ambiguous ENOE"):
                parse_enoe_indicators(path)
            parsed = parse_enoe_indicators(path, "2024-T2")
            self.assertEqual(parsed['period'], "2024-T2")
            self.assertEqual(parsed['tasa_pea'], 0.65)
        finally:
            os.remove(path)

    def test_conapo_requires_exact_base_and_target_years(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("CLAVE,ANO,POB_TOTAL\n23005,2020,100\n23005,2026,120\n")
            path = handle.name
        try:
            meta = parse_conapo_projections(path, target_year=2026, base_year=2020, as_growth_factors=True, return_metadata=True)
            self.assertEqual(meta['target_year'], 2026)
            self.assertAlmostEqual(meta['values']['23005'], 1.2)
            with self.assertRaisesRegex(ValueError, "target year 2025 unavailable"):
                parse_conapo_projections(path, target_year=2025, base_year=2020, as_growth_factors=True)
        finally:
            os.remove(path)

    def test_conapo_single_year_requires_matching_explicit_provenance(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("CLAVE,POB_TOTAL\n23005,120\n")
            path = handle.name
        try:
            matched = parse_conapo_projections(
                path, target_year=2026, source_year=2026, return_metadata=True
            )
            self.assertEqual(matched['target_year'], 2026)
            self.assertEqual(matched['year_provenance'], "explicit source_year configuration")
            with self.assertRaisesRegex(ValueError, "requires explicit trusted source_year"):
                parse_conapo_projections(path, target_year=2026)
            with self.assertRaisesRegex(ValueError, "does not match target year"):
                parse_conapo_projections(path, target_year=2026, source_year=2024)
            with self.assertRaisesRegex(ValueError, "does not match target year"):
                parse_conapo_projections(path, target_year=2027, source_year=2026)
        finally:
            os.remove(path)

    def test_ce_format_does_not_assume_state_code(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as handle:
            handle.write("E04,H001A\n005,1000\n")
            path = handle.name
        try:
            with self.assertRaisesRegex(ValueError, "requires E03 state code"):
                parse_ce2024_municipal(path)
        finally:
            os.remove(path)

    def test_complete_denue_denominator_records_provenance(self):
        denue = pd.DataFrame([{"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0}])
        ce = {"23005": {"nombre": "Benito Juárez", "empleos_ce": 1000.0}}
        _, report = calibrate_denue_employment(
            denue, ce, 0.45, min_sample_threshold=1,
            denominator_contract={"23005": self._denominator("23005", 200.0)}, denue_vintage=2025,
        )
        self.assertEqual(report['23005']['share_bbox'], 0.5)
        self.assertEqual(report['23005']['denominator_source'], "official complete DENUE 23005")

    def test_denue_boolean_attestation_cannot_replace_denominator_contract(self):
        denue = pd.DataFrame([{"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0}])
        ce = {"23005": {"nombre": "Benito Juárez", "empleos_ce": 1000.0}}
        for coverage_complete in (False, True):
            with self.subTest(coverage_complete=coverage_complete):
                with self.assertRaisesRegex(ValueError, "Missing validated DENUE municipal denominator"):
                    calibrate_denue_employment(
                        denue, ce, 0.45, min_sample_threshold=1,
                        coverage_complete=coverage_complete,
                    )

    def test_partial_denue_extract_cannot_claim_complete_coverage(self):
        denue = pd.DataFrame([{"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0}])
        ce = {"23005": {"nombre": "Benito Juárez", "empleos_ce": 1000.0}}
        contract = self._denominator("23005", 200.0)
        contract['coverage_basis'] = 'partial_extract'
        with self.assertRaisesRegex(ValueError, "must be 'complete_municipality'"):
            calibrate_denue_employment(
                denue, ce, 0.45, min_sample_threshold=1,
                denominator_contract={"23005": contract}, denue_vintage=2025,
            )

    def test_denue_denominator_must_cover_every_calibrated_municipality(self):
        denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0},
            {"cve_mun_clean": "23001", "is_micro_small": True, "jobs_formal": 50.0},
        ])
        ce = {
            "23005": {"nombre": "A", "empleos_ce": 1000.0},
            "23001": {"nombre": "B", "empleos_ce": 500.0},
        }
        with self.assertRaisesRegex(ValueError, "23001"):
            calibrate_denue_employment(
                denue, ce, 0.45, min_sample_threshold=1,
                denominator_contract={"23005": self._denominator("23005", 200.0)}, denue_vintage=2025,
            )

    def test_denue_denominator_rejects_mismatch_zero_and_small_total(self):
        denue = pd.DataFrame([{"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0}])
        ce = {"23005": {"nombre": "A", "empleos_ce": 1000.0}}
        invalid = [
            (self._denominator("23005", 200.0, municipality="23001"), "municipality mismatch"),
            (self._denominator("23005", 0.0), "must be positive"),
            (self._denominator("23005", 99.0), "exceeds municipal denominator"),
        ]
        for contract, message in invalid:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    calibrate_denue_employment(
                        denue, ce, 0.45, min_sample_threshold=1,
                        denominator_contract={"23005": contract}, denue_vintage=2025,
                    )

    def test_valid_multi_municipality_denue_denominators(self):
        denue = pd.DataFrame([
            {"cve_mun_clean": "23005", "is_micro_small": True, "jobs_formal": 100.0},
            {"cve_mun_clean": "23001", "is_micro_small": True, "jobs_formal": 50.0},
        ])
        ce = {
            "23005": {"nombre": "A", "empleos_ce": 1000.0},
            "23001": {"nombre": "B", "empleos_ce": 500.0},
        }
        _, report = calibrate_denue_employment(
            denue, ce, 0.45, min_sample_threshold=1,
            denominator_contract={
                "23005": self._denominator("23005", 200.0),
                "23001": self._denominator("23001", 100.0),
            },
            denue_vintage=2025,
        )
        self.assertEqual(set(report), {"23005", "23001"})
        self.assertTrue(all(row['share_bbox'] == 0.5 for row in report.values()))

    def test_final_gravity_marginals_are_reconciled(self):
        cpv = pd.DataFrame([{"lon": -86.8, "lat": 21.1, "pobtot_adj": 100.0, "pea_real": 60.0}])
        cpv.attrs['population_ledger'] = {'population_entering_grid': 100.0}
        denue = pd.DataFrame([{"calibrated_jobs": 80.0}])
        denue.attrs['ingestion_diagnostics'] = {'input_rows': 1}
        points = [{"id": "dp_1", "location": [-86.8, 21.1], "residents": 100, "pea_15ymas": 60, "jobs": 80}]
        contract = build_gravity_input_contract(
            cpv, denue, points, [], employment_ledger=self._employment_ledger()
        )
        self.assertEqual(contract['authoritative_marginals'], {
            'population': 100, 'workers_origins_pea': 60, 'employment_destination_capacity': 80
        })

    def test_grid_employment_ledger_reconciles_affluence_and_poi_absorption(self):
        denue = pd.DataFrame([
            {"lon": -86.84, "lat": 21.14, "calibrated_jobs": 10.0},
            {"lon": -86.90, "lat": 21.20, "calibrated_jobs": 5.0},
        ])
        cpv = pd.DataFrame(columns=['lon', 'lat', 'pobtot_adj', 'pea_real'])
        roads = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        zone = {
            "id": "z", "type": "bbox", "bbox": [-86.85, 21.13, -86.83, 21.15],
            "multiplier": 2.0, "enabled": True,
        }
        poi = {"id": "POI_A", "loc": [-86.90, 21.20], "jobs": 8, "radius_m": 100, "mode": "MAX"}
        points, _, ledger = build_demand_grid(
            denue, cpv, [poi], roads, min_jobs=1, min_residents=1,
            affluence_zones=[zone], return_employment_ledger=True,
        )
        validated = validate_employment_ledger(ledger, points)
        self.assertEqual(validated['authoritative_calibrated_employment'], 15.0)
        self.assertEqual(validated['absorbed_by_special_pois'], 5.0)
        self.assertEqual(validated['affluence_multiplier_delta'], 10.0)
        self.assertEqual(validated['regular_grid_employment_expected'], 20)
        self.assertEqual(validated['special_poi_employment_expected'], 8)
        self.assertEqual(validated['actual_final_employment'], 28)

    def test_employment_ledger_detects_dropped_duplicated_and_positive_wrong_totals(self):
        for jobs, label in [(79, "dropped"), (81, "duplicated"), (70, "positive-but-wrong")]:
            with self.subTest(label=label):
                points = [{"id": "dp", "jobs": jobs}]
                with self.assertRaisesRegex(ValueError, "regular demand points"):
                    validate_employment_ledger(self._employment_ledger(), points)

    def test_employment_ledger_detects_spatial_filter_mutation(self):
        ledger = self._employment_ledger()
        ledger['removed_by_exclusion_zones'] = 10.0
        with self.assertRaisesRegex(ValueError, "explicit removals"):
            validate_employment_ledger(ledger, [{"id": "dp", "jobs": 80}])

    def test_employment_ledger_detects_incorrect_poi_absorption(self):
        ledger = self._employment_ledger()
        ledger['absorbed_by_special_pois'] = 10.0
        with self.assertRaisesRegex(ValueError, "POI absorption split"):
            validate_employment_ledger(ledger, [{"id": "dp", "jobs": 80}])

    def test_employment_ledger_detects_regular_special_double_counting(self):
        ledger = self._employment_ledger()
        points = [{"id": "dp", "jobs": 80}, {"id": "poi", "jobs": 10, "is_special": True}]
        with self.assertRaisesRegex(ValueError, "special demand points"):
            validate_employment_ledger(ledger, points)


if __name__ == "__main__":
    unittest.main()
