import unittest
import pandas as pd
import test_historical_benchmark as fixtures
from sb_mexico.ce_controls import activity_codes, read_saic_controls
from sb_mexico.fine_workplace import load_fine_workplaces
from sb_mexico.workplace_employment import load_workplaces

class FineWorkplaceTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.HistoricalBenchmarkTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)

    def load(self):
        f=self.f
        return load_fine_workplaces([f.denue],f.bbox,{},ce_paths=[f.ce],source_root=f.root)

    def test_all_scian_labels_and_malformed_labels(self):
        for label,code in [('Subsector','311'),('Rama','3111'),('Subrama','31111'),('Clase','311110')]:
            self.assertEqual(activity_codes(label+' '+code+' Industria'),(code,(code,)))
        self.assertEqual(activity_codes('Clase 72 Servicios'),(None,()))

    def test_leaf_partition_excludes_parent_and_missing_siblings(self):
        f=self.f
        f.rows += [f.row('Sector 31-33 Industrias',20,2,Estrato='0 a 10'),
                   f.row('Subsector 311 Alimentos',8,1,Estrato='0 a 10'),
                   f.row('Clase 311110 Fabricación',4,1,Estrato='0 a 10')]
        f.write()
        frame,_,r=self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(),4)
        self.assertEqual([g['activity_code'] for g in r['controls']],['311110'])
        self.assertEqual(r['transferred_establishments'],1)
        self.assertEqual(r['fallback_reasons']['NO_SUPPORTED_CE_ACTIVITY'],1)
        self.assertAlmostEqual(r['coverage']['bbox']['transferred_attraction'],4)

    def test_reserved_leaf_does_not_fall_back_to_parent(self):
        f=self.f
        f.rows += [f.row('Sector 31-33 Industria',20,2,Estrato='0 a 10'),
                   f.row('Clase 311110 Industria',None,1,Estrato='0 a 10')]
        f.write()
        frame,_,r=self.load()
        self.assertEqual(frame.calibrated_jobs.sum(),2.24)
        self.assertEqual(r['controls'][0]['status'],'SUPPRESSED_CE_CONTROL')
        self.assertIsNone(r['controls'][0]['ce_personnel'])

    def test_totals_only_leaf_does_not_invent_size_detail(self):
        f=self.f; f.rows += [f.row('Sector 31-33 Industria',20,2,Estrato='0 a 10'),f.row('Clase 311110 Industria',4,1)]
        f.write()
        self.assertEqual(self.load()[2]['controls'][0]['status'],'MISSING_SIZE_CONTROL')

    def test_infeasible_target_retains_prior(self):
        f=self.f
        df=pd.read_csv(f.denue); df.loc[0,'per_ocu']='6 a 10 personas'; df.to_csv(f.denue,index=False)
        f.rows += [f.row('Clase 311110 Industria',4,1,Estrato='0 a 10')]; f.write()
        frame,_,r=self.load()
        self.assertEqual(frame.calibrated_jobs.sum(),7.75)
        self.assertEqual(r['controls'][0]['status'],'INFEASIBLE_HISTORICAL_TARGET')

    def test_open_band_has_no_450_upper_limit(self):
        f=self.f
        df=pd.read_csv(f.denue); df.loc[0,'per_ocu']='251 y más personas'; df.to_csv(f.denue,index=False)
        f.rows += [f.row('Clase 311110 Industria',10000,1,Estrato='251 y más')]; f.write()
        frame,_,r=self.load()
        self.assertAlmostEqual(frame.calibrated_jobs.sum(),10000,places=5)
        self.assertTrue(r['controls'][0]['open_upper_bound'])
        self.assertIsNone(r['controls'][0]['upper_capacity'])

    def test_legacy_ignores_added_fine_detail(self):
        f=self.f
        f.rows += [f.row('Sector 31-33 Industria',12,3,Estrato='0 a 10')]; f.write()
        old=load_workplaces([f.denue],f.bbox,{'workplace_employment':'auto'},ce_paths=[f.ce],source_root=f.root)[0]
        f.rows += [f.row('Clase 311110 Industria',5,1,Estrato='0 a 10')]; f.write()
        new=load_workplaces([f.denue],f.bbox,{'workplace_employment':'auto'},ce_paths=[f.ce],source_root=f.root)[0]
        self.assertEqual(old.calibrated_jobs.tolist(),new.calibrated_jobs.tolist())

    def test_conflicts_and_mixed_years_fail(self):
        f=self.f
        f.rows += [f.row('Clase 311110 Industria',4,1,Estrato='0 a 10'),
                   f.row('Clase 311110 Industria',5,1,Estrato='0 a 10')]; f.write()
        with self.assertRaisesRegex(ValueError,'Contradictory'): self.load()
        f.rows[-1]=f.row('Clase 311110 Industria',5,1,Estrato='0 a 10',**{'Año Censal':2018}); f.write()
        with self.assertRaisesRegex(ValueError,'multiple activity years'): self.load()
