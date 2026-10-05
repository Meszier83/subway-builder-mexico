import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yaml

from sb_mexico.ce_controls import read_saic_controls
from sb_mexico.historical_benchmark import inspect_historical_benchmark, validate_historical_contract
from sb_mexico.inegi import parse_ce2024_municipal
from sb_mexico.workplace_employment import load_workplaces, read_ce_controls
from tools import wizard


class HistoricalBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.denue = self.root / 'denue.csv'
        self.ce = self.root / 'SAIC.csv'
        pd.DataFrame([dict(id=str(i), cve_ent='23', cve_mun='005', codigo_act=code,
                           nombre_act=name, per_ocu='0 a 5 personas', latitud=21,
                           longitud=-86.5 if i == 1 else -88)
                      for i, code, name in [(1,'311110','Manufactura'), (2,'322110','Manufactura'),
                                            (3,'611112','Escuelas del sector público'),
                                            (4,'813210','Asociaciones y organizaciones religiosas')]]).to_csv(self.denue,index=False)
        self.rows = [self.row('Total municipal', 100, 4), self.row('Sector 31-33 Industrias', 8, 2),
                     self.row('Sector 72 Servicios', None, 1), self.row('Sector 81 Otros', 11, 1)]
        self.write()
        self.bbox = dict(min_lon=-87,max_lon=-86,min_lat=20,max_lat=22)

    def row(self, activity, jobs, units, **extra):
        return {'Año Censal':2023, 'Entidad':'23 Quintana Roo','Municipio':'005 Benito Juarez',
                'Actividad económica':activity,'H001A Personal ocupado total':jobs,
                'UE Unidades económicas':units, **extra}

    def write(self):
        pd.DataFrame(self.rows).to_csv(self.ce,index=False)
        # Force official integer text and actual export NUL padding.
        text=self.ce.read_text().replace('.0,',',')
        self.ce.write_bytes(text.encode()+b'\x00'*16)

    def contract(self):
        return dict(schema_version=1,role='historical_benchmark',enabled=False,reference_year=2023,
                    denue_edition=dict(label='unknown',reference_year=None,evidence='Fixture edition not established'),
                    scope_rule_version='saic_private_paraestatal_v1', coverage_evidence='Activity-class triage only',
                    transfer_assumption='Historical diagnostics, no current employment claim',
                    ce_sources=['SAIC.csv'],groups=[dict(municipality='23005',scian_prefixes=['31','32','33'],reporting_unit='establishment')])

    def test_municipal_total_cannot_be_overwritten_by_sector(self):
        self.assertEqual(parse_ce2024_municipal(str(self.ce))['23005']['empleos_ce'],100)
        self.rows.reverse(); self.write()
        self.assertEqual(parse_ce2024_municipal(str(self.ce))['23005']['empleos_ce'],100)
        self.rows=[r for r in self.rows if r['Actividad económica']!='Total municipal']; self.write()
        self.assertEqual(parse_ce2024_municipal(str(self.ce)),{})

    def test_explicit_year_and_conflicting_totals(self):
        self.rows.append(self.row('Total municipal',200,4,**{'Año Censal':2018})); self.write()
        self.assertEqual(parse_ce2024_municipal(str(self.ce),2018)['23005']['empleos_ce'],200)
        self.rows.append(self.row('Total municipal',101,4)); self.write()
        with self.assertRaisesRegex(ValueError,'Contradictory'): parse_ce2024_municipal(str(self.ce),2023)

    def test_suppression_grouped_sectors_and_strata_are_distinct(self):
        self.rows.extend([self.row('Sector 31-33 Industrias',3,1,Estrato='0 a 10'),
                          self.row('Sector 31-33 Industrias',None,1,Estrato='Agrupados por confidencialidad'),
                          self.row('Sector 48-49 Transporte',0,0)])
        self.write()
        totals=read_ce_controls([self.ce],2023)
        self.assertEqual(totals['23005','31-33']['scian_prefixes'],['31','32','33'])
        self.assertEqual(totals['23005','48-49']['scian_prefixes'],['48','49'])
        self.assertIsNone(totals['23005','72']['employment'])
        self.assertEqual(totals['23005','48-49']['employment'],0)
        self.assertEqual(len(read_saic_controls([self.ce],2023)),7)

    def test_blank_cannot_erase_published_duplicate(self):
        self.rows.append(self.row('Sector 31-33 Industrias',None,2)); self.write()
        with self.assertRaisesRegex(ValueError,'Contradictory'): read_ce_controls([self.ce],2023)

    def test_contract_has_no_year_equality_or_count_equality_claim(self):
        c=self.contract(); c['denue_edition']['reference_year']=2026
        self.assertFalse(validate_historical_contract(c)['enabled'])
        report=inspect_historical_benchmark([self.denue],[self.ce],c)
        self.assertEqual(report['full_scope_records'],4)
        self.assertEqual(report['scope_counts']['excluded'],2)
        self.assertFalse(report['weights_changed'])
        self.assertEqual(report['groups'][0]['remaining_records'],2)
        self.assertEqual(report['groups'][0]['reporting_unit'],'establishment')
        c['source_sha256']=report['source_sha256']
        self.assertEqual(inspect_historical_benchmark([self.denue],[self.ce],c)['source_binding'],'match')
        self.rows[1]['UE Unidades económicas']=3; self.write()
        stale=inspect_historical_benchmark([self.denue],[self.ce],c)
        self.assertEqual(stale['source_binding'],'mismatch')
        self.assertEqual(stale['groups'][0]['records_minus_ce_units'],-1)

    def test_activation_and_overlap_rejected(self):
        for value in (True,'false',None):
            c=self.contract(); c['enabled']=value
            with self.assertRaisesRegex(ValueError,'activation'): validate_historical_contract(c)
        c=self.contract(); c['groups'].append(dict(municipality='23005',scian_prefixes=['32'],reporting_unit='establishment'))
        with self.assertRaisesRegex(ValueError,'Overlapping'): validate_historical_contract(c)

    def test_source_edit_during_inspection_is_rejected(self):
        from sb_mexico import workplace_employment
        real=workplace_employment.source_hashes
        calls=0
        def edited(paths):
            nonlocal calls
            calls+=1
            return ['0'*64] if calls==4 else real(paths)
        with patch.object(workplace_employment,'source_hashes',side_effect=edited):
            with self.assertRaisesRegex(ValueError,'changed during'):
                inspect_historical_benchmark([self.denue],[self.ce],self.contract())

    def test_saved_inactive_reference_preserves_legacy_and_existing_fit(self):
        c=self.contract()
        baseline=load_workplaces([self.denue],self.bbox,{})[0]
        actual,_,report=load_workplaces([self.denue],self.bbox,dict(historical_workplace_benchmark=c))
        pd.testing.assert_frame_equal(baseline,actual)
        self.assertFalse(report['historical_benchmark']['weights_changed'])
        macro=dict(workplace_employment='ce_bounded',workplace_control_contract=dict(
            reference_year=2023,denue_reference_year=2023,coverage_evidence='Fixture',groups=[]))
        prior=load_workplaces([self.denue],self.bbox,macro)[0]
        macro['historical_workplace_benchmark']=c
        pd.testing.assert_frame_equal(prior,load_workplaces([self.denue],self.bbox,macro)[0])
        config=dict(city=dict(name='Fixture',code='fixture',bbox=[-87,20,-86,22]),macroeconomics=macro,data_dir='.')
        path=self.root/'fixture.yaml'
        with patch.object(wizard,'ROOT_DIR',str(self.root)):
            wizard.save_full_city_data(str(path),config)
            saved=yaml.safe_load(path.read_text())
            self.assertEqual(saved['macroeconomics']['historical_workplace_benchmark'],c)
            self.assertEqual(saved['macroeconomics']['workplace_control_contract'],macro['workplace_control_contract'])
            before=path.read_bytes()
            result=wizard.inspect_workplace_benchmark(str(path),c)
            self.assertEqual(result['report']['full_scope_records'],4)
            self.assertEqual(path.read_bytes(),before)
            bad=copy.deepcopy(c); bad['ce_sources']=['../outside.csv']
            with self.assertRaises(ValueError): wizard.inspect_workplace_benchmark(str(path),bad)
            config['macroeconomics']['historical_workplace_benchmark']['enabled']=True
            with self.assertRaisesRegex(ValueError,'activation'): wizard.save_full_city_data(str(path),config)
            self.assertEqual(path.read_bytes(),before)

    def test_empty_sources_no_false_publication(self):
        pd.DataFrame(columns=pd.read_csv(self.denue).columns).to_csv(self.denue,index=False)
        report=inspect_historical_benchmark([self.denue],[self.ce],self.contract())
        self.assertEqual(report['full_scope_records'],0)
        self.assertTrue(all(r['status']=='UNAVAILABLE' for r in report['groups']))

    def test_http_inspection_dispatch_and_invalid_contract(self):
        handler=object.__new__(wizard.WizardRequestHandler)
        handler.path='/api/workplace/inspect'
        body=json.dumps(dict(file='fixture.yaml',contract=self.contract())).encode()
        handler.headers={'Content-Length':str(len(body))}; handler.rfile=io.BytesIO(body)
        handler.serve_json=lambda result: setattr(handler,'result',result)
        handler.serve_error=lambda message, status: setattr(handler,'error',(status,message))
        with patch.object(wizard,'inspect_workplace_benchmark',return_value={'status':'ok'}) as inspect:
            handler.do_POST()
            self.assertEqual(handler.result,{'status':'ok'})
            self.assertEqual(inspect.call_args.args[1]['enabled'],False)
        handler.rfile=io.BytesIO(body)
        with patch.object(wizard,'inspect_workplace_benchmark',side_effect=ValueError('Invalid contract')):
            handler.do_POST()
            self.assertEqual(handler.error,(400,'Invalid contract'))


if __name__=='__main__': unittest.main()
