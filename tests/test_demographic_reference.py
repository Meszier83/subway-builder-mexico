"""Geographic, coverage, identity and preview/build parity falsifiers for EIC."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
from sb_mexico.demographic_reference import apply_reference, validate_reference
from sb_mexico.inegi import resolve_projection_year
from tests import test_residential_employment as census_tests
from tools import poi_studio, wizard
from sb_mexico.pipeline import execute_pipeline


def sources(root, codes, population=120, occupied=60):
    indicators, persons = [], []
    for c in codes:
        for label, values in [('Valor',(population,occupied)),('Error estándar',(1,1)),
            ('Límite inferior de confianza',(population-10,occupied-10)),
            ('Límite superior de confianza',(population+10,occupied+10)),('Coeficiente de variación',(10,10))]:
            indicators.append(dict(CVE_ENT=c[:2],CVE_MUN=c[2:],CVE_LOC='0000',ESTIMADOR=label,POBTOT=values[0],POCUPADA=values[1]))
        weight=population/4
        assert occupied==population/2
        for i in range(4):
            persons.append(dict(CVEGEO=c,ID_PERSONA=str(i),EDAD=30,CONACT=10 if i<2 else 30,
                TIE_TRASLADO_TRAB=1 if i==0 else (7 if i==1 else ''),FACTOR=weight,
                MUN_TRAB=c[2:],ENT_PAIS_TRAB=c[:2]))
    pd.DataFrame(indicators).to_csv(root/'indicators.csv',index=False,encoding='cp1252')
    pd.DataFrame(persons).to_csv(root/'persons.csv',index=False)
    return dict(residential_employment='census_employed',projection_year=2025,
        demographic_reference=dict(mode='eic2025',indicators=str(root/'indicators.csv'),persons=[str(root/'persons.csv')]))


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.codes=['01001','02002']
        self.macro=sources(self.root,self.codes)
        bases=[dict(ENTIDAD=c[:2],MUN=c[2:],LOC='0000',AGEB='0000',MZA='000',POBTOT=100,POCUPADA=40) for c in self.codes]
        self.cpv=self.root/'cpv.csv'; pd.DataFrame(bases).to_csv(self.cpv,index=False)
        self.frame=pd.DataFrame([dict(cve_mun_clean=c,pobtot_num=50,pob15_num=40,employed_2020=20,
            published_employed_2020=20,employment_source='published_block',employment_capacity_basis='P_12YMAS',
            pea_real=20,growth=1,pobtot_adj=50) for c in self.codes])
        self.frame.attrs={'residential_employment':{'input':{},'placement':{},'projection':{}},'residential_placement':{}}

    def apply(self): return apply_reference(self.frame,[self.cpv],self.macro,self.root)

    def test_multistate_partial_map_and_separate_budgets(self):
        people=pd.read_csv(self.root/'persons.csv',dtype=str)
        people[sorted(people.columns)].to_csv(self.root/'persons.csv',index=False)
        result=self.apply()
        self.assertEqual(result.pobtot_adj.tolist(),[60,60])
        self.assertEqual(result.occupied_residents.tolist(),[30,30])
        self.assertEqual(result.pea_real.tolist(),[15,15])
        self.assertEqual(result.employed_2020.tolist(),[20,20])
        self.assertEqual(result.attrs['residential_employment']['input']['projected_employed'],60)
        self.assertEqual(result.attrs['residential_employment']['input']['projected_commuters'],30)
        self.assertEqual(resolve_projection_year(self.macro),2025)
        self.assertIs(apply_reference(self.frame,[self.cpv],{},self.root),self.frame)

    def test_no_clipped_denominator_or_cross_state_fallback(self):
        pd.DataFrame([dict(ENTIDAD='01',MUN='001',LOC='0001',AGEB='001A',MZA='001',POBTOT=50,POCUPADA=20)]).to_csv(self.cpv,index=False)
        with self.assertRaisesRegex(ValueError,'Full municipal CPV'): self.apply()

    def test_reject_incomplete_microdata_and_conflicting_overlap(self):
        persons=pd.read_csv(self.root/'persons.csv',dtype=str)
        persons.iloc[:-1].to_csv(self.root/'persons.csv',index=False)
        with self.assertRaisesRegex(ValueError,'Incomplete/incompatible'): self.apply()
        sources(self.root,self.codes)
        persons=pd.read_csv(self.root/'persons.csv',dtype=str)
        persons.iloc[[0]].assign(FACTOR='20').to_csv(self.root/'extra.csv',index=False)
        self.macro['demographic_reference']['persons'].append(str(self.root/'extra.csv'))
        with self.assertRaisesRegex(ValueError,'Conflicting overlapping'): self.apply()

    def test_same_person_identity_in_two_states_and_exact_duplicate(self):
        self.macro['demographic_reference']['persons'] += [str(self.root/'persons.csv')]
        pd.read_csv(self.root/'persons.csv').to_csv(self.root/'duplicate.csv',index=False)
        self.macro['demographic_reference']['persons'].append(str(self.root/'duplicate.csv'))
        self.assertEqual(self.apply().pea_real.sum(),30)

    def test_missing_controls_wrong_year_and_excess_spatial_mass(self):
        self.macro['projection_year']=2026
        with self.assertRaisesRegex(ValueError,'year 2025'): validate_reference(self.macro)
        self.macro['projection_year']=2025
        self.frame.loc[0,'pobtot_num']=200
        with self.assertRaisesRegex(ValueError,'exceed full municipal'): self.apply()
        self.frame.loc[0,'pobtot_num']=50
        df=pd.read_csv(self.root/'indicators.csv',encoding='cp1252',dtype=str)
        df[df.CVE_ENT!='02'].to_csv(self.root/'indicators.csv',encoding='cp1252',index=False)
        with self.assertRaisesRegex(ValueError,'missing: 02002'): self.apply()

    def test_zero_occupied_controls_do_not_invent_travelers(self):
        df=pd.read_csv(self.root/'indicators.csv',encoding='cp1252',dtype=str)
        df.loc[df.ESTIMADOR=='Valor','POCUPADA']='0'
        df.loc[df.ESTIMADOR=='Límite inferior de confianza','POCUPADA']='0'
        df.loc[df.ESTIMADOR=='Coeficiente de variación','POCUPADA']='*'
        df.to_csv(self.root/'indicators.csv',encoding='cp1252',index=False)
        people=pd.read_csv(self.root/'persons.csv',dtype=str)
        people['CONACT']='30'
        people.to_csv(self.root/'persons.csv',index=False)
        base=pd.read_csv(self.cpv,dtype=str);base['POCUPADA']='0';base.to_csv(self.cpv,index=False)
        self.frame['employed_2020']=0
        result=self.apply()
        self.assertEqual(result.occupied_residents.sum(),0)
        self.assertEqual(result.pea_real.sum(),0)


class EicIntegrationTests(unittest.TestCase):
    def test_wizard_preview_cache_and_export_share_reference(self):
        fixture=census_tests.CensusEmploymentTests(methodName='test_wizard_persistence_preview_cache_and_production_export')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        root=fixture.root
        path,cfg,data=fixture.fixture_config()
        cpv=pd.read_csv(data/'cpv.csv',dtype=str)
        parent={k:'0' for k in cpv.columns}
        parent.update(ENTIDAD='23',MUN='005',LOC='0000',AGEB='0000',MZA='000',POBTOT='400',P_12YMAS='300',P_15YMAS='300',POCUPADA='160')
        pd.concat([cpv,pd.DataFrame([parent])],ignore_index=True).to_csv(data/'cpv.csv',index=False)
        macro=sources(root,['23005'],population=480,occupied=240)
        cfg['macroeconomics'].update(macro)
        wizard.save_full_city_data(str(path),cfg)
        diagnostics={}
        with patch.object(poi_studio,'ROOT_DIR',str(root)):
            points=poi_studio.load_demand_sample(city_file=str(path),diagnostics=diagnostics)
            self.assertAlmostEqual(sum(p.get('employed_residents',0) for p in points),120)
            self.assertAlmostEqual(sum(p.get('labor_commuters',0) for p in points),60)
            self.assertEqual(points,poi_studio.load_demand_sample(city_file=str(path)))
            # External source content changes must invalidate cached totals.
            original_people=(root/'persons.csv').read_bytes()
            people=pd.read_csv(root/'persons.csv')
            people.loc[1,'TIE_TRASLADO_TRAB']=1
            people.to_csv(root/'persons.csv',index=False)
            changed=poi_studio.load_demand_sample(city_file=str(path))
            self.assertAlmostEqual(sum(p.get('labor_commuters',0) for p in changed),120)
            (root/'persons.csv').write_bytes(original_people)
        with patch.object(__import__('sb_mexico.pipeline',fromlist=['ROOT_DIR']),'ROOT_DIR',str(root)), \
             patch('sb_mexico.pipeline.console'),patch('sb_mexico.pipeline.is_docker_available',return_value=(False,None)):
            execute_pipeline(str(path),skip_map=True,output_dir=str(root/'out'))
        report=json.loads((root/'out/residential_employment_report.json').read_text())
        self.assertEqual(report['input'],diagnostics['residential_employment']['input'])
        self.assertEqual(report['simulation']['exported_commuters'],60)
        self.assertEqual(report['projection']['model_year'],2025)
        self.assertEqual(report['demographic_reference']['sources'],diagnostics['residential_employment']['demographic_reference']['sources'])


if __name__=='__main__': unittest.main()
