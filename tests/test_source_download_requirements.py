"""Necessary source columns, engine requirements, and complementary manual CE."""
import base64,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from sb_mexico import source_downloads as d
from tests.test_source_downloads import FixtureClient

class RequirementsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'project';self.folder.mkdir()
        self.config=self.root/'city.yaml';self.config.write_text('saved config')
        self.geo=dict(states=[dict(code='23',name='Quintana Roo')],
            municipalities=[dict(code='23005',state='23',name='Benito Juárez')])
    def tearDown(self):self.temp.cleanup()

    def test_prepare_downloads_full_ce_for_both_engines_and_requires_candidate_eic(self):
        for engine in ('legacy','v2'):
            cfg=dict(city={'bbox':[0,0,1,1]},data_dir=str(self.folder),demand={'engine':engine})
            manager=d.DownloadManager(self.root,self.root/'data',lambda _:cfg,str)
            with patch.object(d,'geography',return_value=self.geo):plan=manager.prepare(str(self.config))
            self.assertTrue(plan['ce_detail'])
            rows={s['kind']:s for s in plan['sources']}
            self.assertEqual(rows['eic']['recommended'],engine=='v2')
            self.assertEqual(rows['conapo']['recommended'],engine!='v2')
            self.assertEqual(rows['enoe']['recommended'],engine!='v2')

    def test_eic_missing_workplace_geography_rejected_before_publication(self):
        path=self.folder/'personas23.csv'
        path.write_text('CVEGEO,ID_PERSONA,EDAD,CONACT,TIE_TRASLADO_TRAB,FACTOR\n23005,x,30,10,2,1\n')
        with self.assertRaisesRegex(ValueError,'ent_pais_trab'):d.validate_csv(path,'eic_persons')
        path.write_text('CVEGEO,ID_PERSONA,EDAD,CONACT,TIE_TRASLADO_TRAB,FACTOR,MUN_TRAB,ENT_PAIS_TRAB\n23005,x,30,10,2,1,005,23\n')
        d.validate_csv(path,'eic_persons')

    def test_denue_missing_municipality_rejected(self):
        path=self.folder/'denue.csv';path.write_text('latitud,longitud,codigo_act,per_ocu\n21,-86,721111,1\n')
        with self.assertRaisesRegex(ValueError,'cve_mun'):d.validate_csv(path,'denue')

    def test_source_manifest_cannot_be_published_as_data_or_change_existing_files(self):
        stage=self.root/'stage';stage.mkdir()
        (stage/'SAIC_new.csv').write_text('new data')
        (stage/d.MANIFEST).write_text('{"files":{}}')
        manual=self.folder/'SAIC_manual.csv';manual.write_text('preserve')
        with self.assertRaisesRegex(ValueError,'manifiesto'):d.publish(stage,self.folder,'ce',[])
        self.assertEqual(manual.read_text(),'preserve')
        self.assertEqual(list(self.folder.iterdir()),[manual])

    def ce_job(self, conflict=False):
        header='"Año Censal","Entidad","Municipio","Estrato","Actividad económica","UE Unidades económicas","H001A Personal ocupado total"\n'
        manual=self.folder/'SAIC_manual.csv'
        manual.write_text(header+('2023,"23 Quintana Roo","005 Benito Juárez","Total","Sector 72 Servicios",2,99\n' if conflict else
                                 '2023,"23 Quintana Roo","005 Benito Juárez","Total","Total municipal",20,80\n'),encoding='utf-8')
        original=manual.read_bytes()
        def exported(q):
            activity='Clase 721111 Hoteles' if '721111' in q['actecos'] else 'Sector 72 Servicios'
            size='Total' if q['stratums']==[0] else '0 a 10'
            return base64.b64encode((header+f'2023,"23 Quintana Roo","005 Benito Juárez","{size}","{activity}",2,8\n').encode()).decode()
        docs={d.SAIC+'anios/seg/0/0/6/':dict(success=True,list=[dict(key='2023')]),
              d.SAIC+'acteco/seg/0/2/6/':dict(success=True,list=[dict(key='72')]),
              d.SAIC+'ageos/seg/23/1/6/':dict(success=True,list=[dict(key='23005')]),
              d.SAIC+'consulta/total/6/':dict(success=True,info=[dict(total=1)]),
              d.SAIC+'exporta/files/2/6/':exported}
        manager=d.DownloadManager(self.root,self.root/'data',lambda _: {},str)
        plan=dict(id='plan',file=str(self.config),config_hash=d.sha256(self.config),folder=str(self.folder),
                  geography=self.geo,exclusions=[],ce_detail=True,created_at=time.time())
        manager.plans['plan']=plan
        self.assertEqual(manager.conflicts(plan,'ce'),[])
        with patch.object(d,'Client',return_value=FixtureClient(docs)),patch.object(d,'fine_ce_catalog',return_value=['72','721111']):
            job=manager.start('plan',['ce'],None,2026,2)
            for _ in range(300):
                job=manager.status(job['id'])
                if job['status']!='running':break
                time.sleep(.01)
        self.assertEqual(manual.read_bytes(),original)
        return job

    def test_manual_total_can_receive_complementary_ce_detail(self):
        job=self.ce_job()
        self.assertEqual(job['status'],'complete',job)
        self.assertEqual(len(job['results'][0]['files']),4)

    def test_conflicting_manual_ce_does_not_publish_or_claim_ready(self):
        job=self.ce_job(conflict=True)
        self.assertEqual(job['status'],'partial',job)
        self.assertEqual(job['results'][0]['status'],'error')
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),['SAIC_manual.csv'])

if __name__=='__main__':unittest.main()
